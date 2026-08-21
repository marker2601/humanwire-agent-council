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
from humanwire.mission_store import (
    InMemoryMissionRepository,
    MissionUnavailable,
)

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
ORG_A = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
ORG_B = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AB"
WORKSPACE_A = "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
MISSION_A = "mis_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"


class FixedIdentifiers:
    def mission_id(self) -> str:
        return MISSION_A


def context(
    organization_id: str = ORG_A,
    *,
    role: DecisionOSRole = DecisionOSRole.OWNER,
) -> DecisionOSContext:
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
            role=role,
            status=MembershipStatus.ACTIVE,
        ),
    )


def workspace() -> DecisionWorkspace:
    return DecisionWorkspace(
        workspace_id=WORKSPACE_A,
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


def repository() -> InMemoryMissionRepository:
    return InMemoryMissionRepository(
        clock=lambda: NOW,
        identifiers=FixedIdentifiers(),
    )


def test_create_binds_identity_tenant_workspace_and_mode() -> None:
    saved = repository().create(context(), workspace(), request())

    assert saved.mission_id == MISSION_A
    assert saved.organization_id == ORG_A
    assert saved.workspace_id == WORKSPACE_A
    assert saved.mode is MissionMode.DEMO_RUN
    assert saved.state is MissionState.READY
    assert saved.version == 1
    assert saved.events[0].kind == "mission.created"


def test_repository_compare_and_swap_rejects_stale_version() -> None:
    store = repository()
    saved = store.create(context(), workspace(), request())
    changed = saved.model_copy(update={"state": MissionState.RUNNING})

    updated = store.update(context(), changed, expected_version=1)

    assert updated.version == 2
    assert updated.state is MissionState.RUNNING
    with pytest.raises(MissionUnavailable, match="mission_unavailable"):
        store.update(context(), changed, expected_version=1)


def test_repository_never_returns_cross_tenant_snapshot() -> None:
    store = repository()
    saved = store.create(context(), workspace(), request())

    with pytest.raises(MissionUnavailable, match="mission_unavailable"):
        store.load(context(ORG_B), saved.mission_id)


def test_repository_rejects_cross_workspace_creation() -> None:
    foreign_workspace = workspace().model_copy(update={"organization_id": ORG_B})
    with pytest.raises(MissionUnavailable, match="mission_unavailable"):
        repository().create(context(), foreign_workspace, request())


def test_viewer_can_load_but_cannot_create_or_update() -> None:
    store = repository()
    saved = store.create(context(), workspace(), request())
    viewer = context(role=DecisionOSRole.VIEWER)

    assert store.load(viewer, saved.mission_id) == saved
    with pytest.raises(Exception, match="authorization_denied"):
        store.create(viewer, workspace(), request())
    with pytest.raises(Exception, match="authorization_denied"):
        store.update(viewer, saved, expected_version=1)


def test_update_cannot_change_immutable_mission_identity_or_mode() -> None:
    store = repository()
    saved = store.create(context(), workspace(), request())
    changed_mode = saved.model_copy(update={"mode": MissionMode.CONNECTED_ORGANIZATION})

    with pytest.raises(MissionUnavailable, match="mission_unavailable"):
        store.update(context(), changed_mode, expected_version=1)


def test_loaded_snapshot_is_detached_from_hostile_subclass() -> None:
    class PrivateString(str):
        pass

    store = repository()
    saved = store.create(context(), workspace(), request())
    poisoned = saved.model_copy(update={"objective": PrivateString(saved.objective)})
    store._records[(ORG_A, MISSION_A)] = poisoned  # type: ignore[attr-defined]

    with pytest.raises(MissionUnavailable, match="mission_unavailable"):
        store.load(context(), MISSION_A)

