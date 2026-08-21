from __future__ import annotations

from datetime import UTC, datetime

import pytest

from humanwire.decisionos_models import (
    DecisionOSContext,
    DecisionOSPrincipal,
    DecisionOSRole,
    DecisionWorkspace,
    MembershipStatus,
    OrganizationMembership,
    WorkspacePlaybook,
)
from humanwire.mission_models import MissionMode, MissionRequest, MissionState
from humanwire.mission_store import FirestoreMissionRepository, MissionUnavailable

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
ORG_A = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
ORG_B = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AB"
WORKSPACE = "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
MISSION = "mis_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"


class FixedIdentifiers:
    def mission_id(self) -> str:
        return MISSION


class FakeSnapshot:
    def __init__(self, value) -> None:
        self._value = value
        self.exists = value is not None

    def to_dict(self):
        return None if self._value is None else dict(self._value)


class FakeDocument:
    def __init__(self, client, path: tuple[str, ...]) -> None:
        self.client = client
        self.path = path

    def collection(self, name: str):
        return FakeCollection(self.client, (*self.path, name))

    def get(self, *, transaction=None):
        del transaction
        if self.client.fail_reads:
            raise RuntimeError("private-firestore-read-failure")
        return FakeSnapshot(self.client.rows.get(self.path))


class FakeCollection:
    def __init__(self, client, path: tuple[str, ...]) -> None:
        self.client = client
        self.path = path

    def document(self, name: str):
        return FakeDocument(self.client, (*self.path, name))


class FakeTransaction:
    def __init__(self, client) -> None:
        self.client = client

    def create(self, reference: FakeDocument, value) -> None:
        if reference.path in self.client.rows:
            raise RuntimeError("already exists")
        self.client.rows[reference.path] = dict(value)

    def set(self, reference: FakeDocument, value) -> None:
        self.client.rows[reference.path] = dict(value)


class FakeQuery:
    def __init__(self, client) -> None:
        self.client = client
        self.filters: list[tuple[str, object]] = []
        self.maximum = 100

    def where(self, field: str, operator: str, value: object):
        assert operator == "=="
        self.filters.append((field, value))
        return self

    def limit(self, maximum: int):
        self.maximum = maximum
        return self

    def stream(self):
        if self.client.fail_reads:
            raise RuntimeError("private-firestore-read-failure")
        matches = []
        for path, value in self.client.rows.items():
            if len(path) == 6 and path[4] == "missions" and all(
                value.get(field) == expected for field, expected in self.filters
            ):
                matches.append(FakeSnapshot(value))
        return tuple(matches[: self.maximum])


class FakeClient:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, ...], dict] = {}
        self.fail_reads = False
        self.transaction_count = 0

    def collection(self, name: str):
        return FakeCollection(self, (name,))

    def collection_group(self, name: str):
        assert name == "missions"
        return FakeQuery(self)

    def transaction(self):
        self.transaction_count += 1
        return FakeTransaction(self)


@pytest.fixture(autouse=True)
def direct_transaction(monkeypatch) -> None:
    from google.cloud import firestore

    monkeypatch.setattr(firestore, "transactional", lambda function: function)


def context(organization_id: str = ORG_A) -> DecisionOSContext:
    principal = DecisionOSPrincipal(
        uid="firebase-owner-01",
        email_verified=True,
        provider_ids=("google.com",),
    )
    return DecisionOSContext(
        principal=principal,
        membership=OrganizationMembership(
            organization_id=organization_id,
            uid=principal.uid,
            role=DecisionOSRole.OWNER,
            status=MembershipStatus.ACTIVE,
        ),
    )


def workspace() -> DecisionWorkspace:
    return DecisionWorkspace(
        workspace_id=WORKSPACE,
        organization_id=ORG_A,
        name="Launch decisions",
        playbook=WorkspacePlaybook.LAUNCH_DECISION,
        created_by_uid="firebase-owner-01",
    )


def request() -> MissionRequest:
    return MissionRequest(
        mode=MissionMode.DEMO_RUN,
        objective="Approve the launch decision with current evidence.",
        urgency="standard",
        include_conflict=True,
    )


def repository(client: FakeClient) -> FirestoreMissionRepository:
    return FirestoreMissionRepository(
        client,
        clock=lambda: NOW,
        identifiers=FixedIdentifiers(),
    )


def test_firestore_create_is_restart_loadable() -> None:
    client = FakeClient()
    saved = repository(client).create(context(), workspace(), request())

    loaded = repository(client).load(context(), saved.mission_id)

    assert loaded == saved
    assert client.transaction_count == 1


def test_firestore_update_is_compare_and_swap() -> None:
    client = FakeClient()
    store = repository(client)
    saved = store.create(context(), workspace(), request())
    changed = saved.model_copy(update={"state": MissionState.RUNNING})

    updated = store.update(context(), changed, expected_version=1)

    assert updated.version == 2
    assert repository(client).load(context(), MISSION).state is MissionState.RUNNING
    with pytest.raises(MissionUnavailable, match="mission_unavailable"):
        store.update(context(), changed, expected_version=1)


def test_firestore_cross_tenant_read_has_one_fixed_failure() -> None:
    client = FakeClient()
    repository(client).create(context(), workspace(), request())

    with pytest.raises(MissionUnavailable, match="mission_unavailable"):
        repository(client).load(context(ORG_B), MISSION)


def test_firestore_corrupt_snapshot_fails_closed() -> None:
    client = FakeClient()
    repository(client).create(context(), workspace(), request())
    path = (
        "decisionos_organizations",
        ORG_A,
        "workspaces",
        WORKSPACE,
        "missions",
        MISSION,
    )
    client.rows[path]["snapshot"]["objective"] = "private <script> value"

    with pytest.raises(MissionUnavailable, match="mission_unavailable"):
        repository(client).load(context(), MISSION)


def test_firestore_provider_exception_is_fixed_and_content_free() -> None:
    client = FakeClient()
    client.fail_reads = True

    with pytest.raises(MissionUnavailable, match="^mission_unavailable$") as captured:
        repository(client).load(context(), MISSION)

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
