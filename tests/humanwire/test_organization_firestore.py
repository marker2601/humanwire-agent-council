from __future__ import annotations

import os
import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from hashlib import sha256
from uuid import uuid4

import pytest
from google.api_core.datetime_helpers import DatetimeWithNanoseconds
from google.cloud.firestore_v1 import _helpers as firestore_helpers
from google.cloud.firestore_v1.types import Document as FirestoreDocument
from pydantic_core import to_json

from humanwire import decisionos_store, organization_store
from humanwire.decisionos_models import (
    DecisionOrganization,
    DecisionOSContext,
    DecisionOSPrincipal,
    DecisionOSRole,
    MembershipStatus,
    OrganizationMembership,
)
from humanwire.decisionos_store import (
    FirestoreDecisionOSRepository,
    InvitationUnavailable,
    LastOwnerRequired,
    OrganizationUnavailable,
    SubjectInvitationDeliveryState,
)
from humanwire.organization_models import (
    AuthorityAssignment,
    AuthorityFunction,
    ImportDraft,
    ImportReceipt,
    OrganizationEdge,
    OrganizationEdgeKind,
    OrganizationGraph,
    OrganizationGraphCandidate,
    OrganizationSubject,
    OrganizationSubjectKind,
    OrganizationUnit,
    SourceRecord,
    SourceSnapshot,
    SubjectLifecycle,
)
from humanwire.organization_store import (
    FirestoreOrganizationGraphRepository,
    ImportUnavailable,
)

ORG = "org_01ARZ3NDEKTSV4RRFFQ69G5FAV"
SUBJECT = "sub_01ARZ3NDEKTSV4RRFFQ69G5FAV"
SUBJECT_B = "sub_01ARZ3NDEKTSV4RRFFQ69G5FAW"
NOW = datetime(2026, 8, 18, 15, 0, tzinfo=UTC)
DIGEST = "1" * 64
PRIVATE = "private-provider-sentinel@example.invalid"
COLLECTION = "test_organizations"
AUDIT = "test_org_audit"
OWNER = DecisionOSPrincipal(
    uid="firebase-owner-01",
    email_verified=True,
    provider_ids=("google.com",),
)


class HostileString(str):
    def __new__(cls, allowed: str, private: str):
        value = super().__new__(cls, private)
        value.allowed = allowed
        return value

    def __eq__(self, other):
        return other in {self.allowed, str.__str__(self)}

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash(self.allowed)


class ScalarKey(str):
    pass


class ExplodesIfMaterialized:
    def __deepcopy__(self, _memo):
        raise AssertionError("irrelevant future transition was materialized")


def actual_document_bytes(reference, data, *, include_name: bool = True) -> int:
    name = reference._document_path if include_name else ""
    return FirestoreDocument(
        name=name,
        fields=firestore_helpers.encode_dict(data),
    )._pb.ByteSize()


def independent_digest(data) -> str:
    return sha256(to_json(data)).hexdigest()


def deterministic_ulid(*parts: str) -> str:
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    value = int.from_bytes(
        sha256("\0".join(parts).encode()).digest()[:16],
        "big",
    )
    return "".join(alphabet[(value >> (5 * index)) & 31] for index in range(25, -1, -1))


class FakeSnapshot:
    def __init__(self, reference, data, *, create_time=None, update_time=None):
        self.reference = reference
        self.id = reference.id
        self._data = data
        self.exists = data is not None
        self.create_time = create_time
        self.update_time = update_time

    def to_dict(self):
        return deepcopy(self._data)


class FakeQuery:
    def __init__(self, client, paths, filters=(), ordering=None, limit_count=None):
        self.client = client
        self.paths = tuple(paths)
        self.filters = tuple(filters)
        self.ordering = ordering
        self.limit_count = limit_count

    def where(self, *, filter):
        return FakeQuery(
            self.client,
            self.paths,
            (*self.filters, (filter.field_path, filter.op_string, filter.value)),
            self.ordering,
            self.limit_count,
        )

    def order_by(self, field, direction=None):
        return FakeQuery(
            self.client,
            self.paths,
            self.filters,
            (field, direction),
            self.limit_count,
        )

    def limit(self, count):
        self.client.query_limits.append(count)
        return FakeQuery(
            self.client,
            self.paths,
            self.filters,
            self.ordering,
            count,
        )

    def stream(self, transaction=None):
        if self.client.failure is not None:
            raise RuntimeError(self.client.failure)
        rows = []
        for path in sorted(self.paths):
            candidate = (
                transaction.staged.get(path, self.client.data.get(path))
                if transaction is not None
                else self.client.data.get(path)
            )
            if candidate is None:
                continue
            if all(
                self._matches(self._field(candidate, field), operator, value)
                for field, operator, value in self.filters
            ):
                data = (
                    transaction._get(path)
                    if transaction is not None
                    else candidate
                )
                rows.append(
                    FakeSnapshot(
                        FakeDocument(self.client, path),
                        data,
                        create_time=self.client.create_times.get(path),
                        update_time=self.client.update_times.get(path),
                    )
                )
        if self.ordering is not None:
            field, direction = self.ordering
            rows.sort(
                key=lambda row: self._field(row.to_dict(), field),
                reverse=str(direction).casefold().endswith("descending"),
            )
        if self.limit_count is not None:
            rows = rows[: self.limit_count]
        return tuple(rows)

    @staticmethod
    def _matches(actual, operator, expected):
        if operator == "==":
            return actual == expected
        if operator == ">=":
            return actual is not None and actual >= expected
        if operator == "<=":
            return actual is not None and actual <= expected
        raise AssertionError(f"unsupported fake query operator: {operator}")

    @staticmethod
    def _field(data, field):
        value = data
        for part in field.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value


class FakeCollection:
    def __init__(self, client, path):
        self.client = client
        self.path = tuple(path)

    def document(self, document_id):
        return FakeDocument(self.client, (*self.path, document_id))

    def _document_paths(self):
        return (
            path
            for path in self.client.data
            if len(path) == len(self.path) + 1 and path[:-1] == self.path
        )

    def stream(self, transaction=None):
        return FakeQuery(self.client, self._document_paths()).stream(transaction=transaction)

    def where(self, *, filter):
        return FakeQuery(self.client, self._document_paths()).where(filter=filter)

    def order_by(self, field):
        return FakeQuery(self.client, self._document_paths()).order_by(field)

    def limit(self, count):
        return FakeQuery(self.client, self._document_paths()).limit(count)


class FakeDocument:
    def __init__(self, client, path):
        self.client = client
        self.path = tuple(path)
        self.id = self.path[-1]

    @property
    def _document_path(self):
        suffix = "/".join(self.path)
        return f"projects/test-project/databases/(default)/documents/{suffix}"

    def collection(self, name):
        return FakeCollection(self.client, (*self.path, name))

    def get(self, transaction=None):
        if self.client.failure is not None:
            raise RuntimeError(self.client.failure)
        data = (
            transaction._get(self.path)
            if transaction is not None
            else self.client.data.get(self.path)
        )
        return FakeSnapshot(
            self,
            data,
            create_time=self.client.create_times.get(self.path),
            update_time=self.client.update_times.get(self.path),
        )


class FakeTransaction:
    def __init__(self, client):
        self.client = client
        self.write_count = 0
        self.staged = {}

    def _get(self, path):
        if path in self.staged:
            return deepcopy(self.staged[path])
        return deepcopy(self.client.data.get(path))

    def _write(self, reference, data):
        self.client.provider_mutation_attempts += 1
        resolved = self.client.resolve_server_timestamps(data)
        encoded_size = actual_document_bytes(reference, resolved)
        if encoded_size > self.client.max_document_bytes:
            raise AssertionError(f"document exceeds bound: {encoded_size}")
        self.write_count += 1
        if self.write_count > self.client.max_transaction_writes:
            raise AssertionError(f"transaction exceeds write bound: {self.write_count}")
        self.client.max_observed_document_bytes = max(
            self.client.max_observed_document_bytes,
            encoded_size,
        )
        self.staged[reference.path] = deepcopy(resolved)

    def create(self, reference, data):
        if self._get(reference.path) is not None:
            raise RuntimeError("document already exists")
        self._write(reference, data)

    def set(self, reference, data):
        self._write(reference, data)

    def update(self, reference, data):
        current = self._get(reference.path) or {}
        current.update(deepcopy(data))
        self._write(reference, current)

    def delete(self, reference):
        self.client.provider_mutation_attempts += 1
        self.write_count += 1
        if self.write_count > self.client.max_transaction_writes:
            raise AssertionError(f"transaction exceeds write bound: {self.write_count}")
        self.staged[reference.path] = None

    def commit(self):
        if self.client.abort_next_transaction:
            self.client.abort_next_transaction = False
            self.staged.clear()
            raise RuntimeError(PRIVATE)
        commit_time = self.client.next_commit_time()
        for path, data in self.staged.items():
            if data is None:
                self.client.data.pop(path, None)
                self.client.create_times.pop(path, None)
                self.client.update_times.pop(path, None)
            else:
                if path not in self.client.data:
                    self.client.create_times[path] = commit_time
                self.client.data[path] = deepcopy(data)
                self.client.update_times[path] = commit_time
        self.staged.clear()

    def rollback(self):
        self.staged.clear()


class FakeClient:
    def __init__(self, *, failure=None):
        self.data = {}
        self.failure = failure
        self.max_document_bytes = 450_000
        self.max_transaction_writes = 450
        self.max_observed_document_bytes = 0
        self.provider_mutation_attempts = 0
        self.abort_next_transaction = False
        self.transactions = []
        self.query_limits = []
        self.create_times = {}
        self.update_times = {}
        self.commit_sequence = 0

    def next_commit_time(self):
        self.commit_sequence += 1
        value = NOW - timedelta(days=7) + timedelta(microseconds=self.commit_sequence)
        return DatetimeWithNanoseconds(
            value.year,
            value.month,
            value.day,
            value.hour,
            value.minute,
            value.second,
            nanosecond=value.microsecond * 1000,
            tzinfo=UTC,
        )

    def peek_next_commit_time(self):
        value = NOW - timedelta(days=7) + timedelta(
            microseconds=self.commit_sequence + 1
        )
        return DatetimeWithNanoseconds(
            value.year,
            value.month,
            value.day,
            value.hour,
            value.minute,
            value.second,
            nanosecond=value.microsecond * 1000,
            tzinfo=UTC,
        )

    def resolve_server_timestamps(self, value):
        from google.cloud import firestore

        if value is firestore.SERVER_TIMESTAMP:
            return self.peek_next_commit_time()
        if type(value) is dict:
            return {
                key: self.resolve_server_timestamps(item)
                for key, item in value.items()
            }
        if type(value) is list:
            return [self.resolve_server_timestamps(item) for item in value]
        if type(value) is tuple:
            return tuple(self.resolve_server_timestamps(item) for item in value)
        return value

    def collection(self, name):
        if self.failure is not None:
            raise RuntimeError(self.failure)
        return FakeCollection(self, (name,))

    def collection_group(self, name):
        paths = (path for path in self.data if len(path) >= 2 and path[-2] == name)
        return FakeQuery(self, paths)

    def transaction(self):
        transaction = FakeTransaction(self)
        self.transactions.append(transaction)
        return transaction


def recreate_document(client: FakeClient, path: tuple[str, ...]) -> None:
    payload = deepcopy(client.data[path])
    delete = client.transaction()
    delete.delete(FakeDocument(client, path))
    delete.commit()
    create = client.transaction()
    create.create(FakeDocument(client, path), payload)
    create.commit()


@pytest.fixture
def fake_firestore(monkeypatch):
    from google.cloud import firestore

    def transactional(function):
        def invoke(transaction):
            try:
                result = function(transaction)
                transaction.commit()
                return result
            except Exception:
                transaction.rollback()
                raise

        return invoke

    monkeypatch.setattr(firestore, "transactional", transactional)
    client = FakeClient()
    client.data[(COLLECTION, ORG)] = DecisionOrganization(
        organization_id=ORG,
        name="Northstar Labs",
        created_by_uid=OWNER.uid,
    ).model_dump(mode="python")
    membership = OrganizationMembership(
        organization_id=ORG,
        uid=OWNER.uid,
        role=DecisionOSRole.OWNER,
        status=MembershipStatus.ACTIVE,
    )
    client.data[(COLLECTION, ORG, "members", OWNER.uid)] = membership.model_dump(
        mode="python"
    )
    context = DecisionOSContext(principal=OWNER, membership=membership)
    repository = FirestoreOrganizationGraphRepository(
        client,
        clock=lambda: NOW,
        organization_collection=COLLECTION,
        audit_collection=AUDIT,
    )
    return client, repository, context


def exception_graph_text(error: BaseException) -> str:
    pending = [error]
    seen = set()
    rendered = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        rendered.extend((str(current), repr(current), repr(current.args)))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return " ".join(rendered)


class Identifiers:
    def organization_id(self) -> str:
        return ORG

    def workspace_id(self) -> str:
        return "wrk_01ARZ3NDEKTSV4RRFFQ69G5FAV"

    def invitation_id(self) -> str:
        return "inv_01ARZ3NDEKTSV4RRFFQ69G5FAV"

    def invitation_token(self) -> str:
        return "opaque-invitation-token-123456"


class SequenceFirestoreIdentifiers(Identifiers):
    def __init__(self) -> None:
        self.sequence = 0

    def invitation_id(self) -> str:
        self.sequence += 1
        return f"inv_{self.sequence:026d}"

    def invitation_token(self) -> str:
        return f"opaque-invitation-token-{self.sequence:06d}"


def _stored_generic_invitation(expires_at: object) -> dict[str, object]:
    return {
        "invitation_kind": "generic",
        "invitation_id": "inv_00000000000000000000000001",
        "organization_id": ORG,
        "role": "viewer",
        "expires_at": expires_at,
        "status": "active",
    }


def _stored_subject_invitation(expires_at: object) -> dict[str, object]:
    return {
        "schema_version": 1,
        "invitation_kind": "organization_subject",
        "invitation_id": "inv_00000000000000000000000002",
        "organization_id": ORG,
        "subject_id": SUBJECT,
        "role": "viewer",
        "expires_at": expires_at,
        "delivery_status": "not_delivered",
        "delivery_route_id": None,
        "retry_sequence": 0,
        "retry_of_invitation_id": None,
        "status": "active",
    }


def test_firestore_invitation_loaders_normalize_exact_sdk_timestamps() -> None:
    sdk_timestamp = DatetimeWithNanoseconds.from_rfc3339(
        "2026-08-19T13:00:00.123456Z"
    )
    digest = "a" * 64

    current_mapping = _stored_generic_invitation(sdk_timestamp)
    historical_mapping = dict(current_mapping)
    historical_mapping.pop("invitation_kind")
    generic, is_legacy = decisionos_store._firestore_generic_invitation(
        current_mapping,
        token_digest=digest,
    )
    historical, historical_is_legacy = (
        decisionos_store._firestore_generic_invitation(
            historical_mapping,
            token_digest=digest,
        )
    )
    subject = decisionos_store._firestore_subject_invitation(
        _stored_subject_invitation(sdk_timestamp),
        token_digest=digest,
    )
    state = {
        **_stored_subject_invitation(sdk_timestamp),
        "token_digest": digest,
    }

    assert is_legacy is False
    assert historical_is_legacy is True
    assert type(generic.expires_at) is datetime
    assert type(historical.expires_at) is datetime
    assert type(subject.expires_at) is datetime
    assert generic.expires_at == datetime(
        2026,
        8,
        19,
        13,
        0,
        0,
        123456,
        tzinfo=UTC,
    )
    assert historical.expires_at == generic.expires_at
    assert subject.expires_at == generic.expires_at
    assert decisionos_store._exact_stored_mapping(
        state,
        decisionos_store._subject_invitation_state_payload(subject),
    )


def test_firestore_invitation_timestamp_boundary_rejects_loss_and_hooks() -> None:
    class HostileDatetime(datetime):
        hook_called = False

        def utcoffset(self):
            type(self).hook_called = True
            raise AssertionError("attacker datetime hook must not run")

    class HostileTimezone(tzinfo):
        hook_called = False

        def utcoffset(self, _value):
            type(self).hook_called = True
            raise AssertionError("attacker timezone hook must not run")

        def dst(self, _value):
            type(self).hook_called = True
            raise AssertionError("attacker timezone hook must not run")

    nonrepresentable = DatetimeWithNanoseconds.from_rfc3339(
        "2026-08-19T13:00:00.123456789Z"
    )
    hostile = HostileDatetime(2026, 8, 19, 13, 0, tzinfo=UTC)
    hostile_zone = datetime(2026, 8, 19, 13, 0, tzinfo=HostileTimezone())

    for value in (nonrepresentable, hostile, hostile_zone):
        with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
            decisionos_store._firestore_generic_invitation(
                _stored_generic_invitation(value),
                token_digest="b" * 64,
            )

    assert HostileDatetime.hook_called is False
    assert HostileTimezone.hook_called is False


def draft() -> ImportDraft:
    snapshot = SourceSnapshot(
        snapshot_id="snap_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        organization_id=ORG,
        source_kind="csv",
        captured_at=NOW,
        records=(
            SourceRecord(
                record_id="rec_01ARZ3NDEKTSV4RRFFQ69G5FAV",
                source_ordinal=1,
                source_identity="directory/alice",
                fields=(("email", "private-alice@example.invalid"),),
            ),
        ),
        semantic_digest=DIGEST,
    )
    return ImportDraft(
        import_id="imp_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        organization_id=ORG,
        source_snapshot=snapshot,
        candidate=OrganizationGraphCandidate(
            organization_id=ORG,
            source_snapshot_id=snapshot.snapshot_id,
            subjects=(
                OrganizationSubject(
                    subject_id=SUBJECT,
                    organization_id=ORG,
                    kind=OrganizationSubjectKind.HUMAN,
                    lifecycle=SubjectLifecycle.DRAFT_IMPORTED,
                    display_name="Alice Example",
                    source_identity="directory/alice",
                ),
            ),
        ),
        base_graph_version=0,
        semantic_digest=DIGEST,
        created_at=NOW,
    )


def two_subject_draft() -> ImportDraft:
    first = draft()
    return first.model_copy(
        update={
            "source_snapshot": first.source_snapshot.model_copy(
                update={
                    "records": (
                        *first.source_snapshot.records,
                        SourceRecord(
                            record_id="rec_01ARZ3NDEKTSV4RRFFQ69G5FAW",
                            source_ordinal=2,
                            source_identity="directory/bob",
                            fields=(("email", "bob@example.invalid"),),
                        ),
                    )
                }
            ),
            "candidate": first.candidate.model_copy(
                update={
                    "subjects": (
                        *first.candidate.subjects,
                        OrganizationSubject(
                            subject_id=SUBJECT_B,
                            organization_id=ORG,
                            kind=OrganizationSubjectKind.HUMAN,
                            lifecycle=SubjectLifecycle.DRAFT_IMPORTED,
                            display_name="Bob Example",
                            source_identity="directory/bob",
                        ),
                    )
                }
            ),
        }
    )


def next_draft(
    original: ImportDraft,
    *,
    import_id: str = "imp_01ARZ3NDEKTSV4RRFFQ69G5FAW",
    digest: str = "2" * 64,
    created_at: datetime = NOW,
    supersedes_import_id: str | None = None,
) -> ImportDraft:
    return original.model_copy(
        update={
            "import_id": import_id,
            "semantic_digest": digest,
            "created_at": created_at,
            "supersedes_import_id": supersedes_import_id,
            "source_snapshot": original.source_snapshot.model_copy(
                update={"semantic_digest": digest}
            ),
        }
    )


def downgrade_to_legacy_v1(client: FakeClient, import_id: str) -> None:
    import_path = (COLLECTION, ORG, "imports", import_id)
    payload = client.data[import_path]
    payload.pop("supersedes_import_id")
    payload["schema_version"] = 1
    payload["payload_digest"] = independent_digest(
        {key: payload[key] for key in payload if key != "payload_digest"}
    )
    client.data.pop((COLLECTION, ORG, "organization_import_lineage", "csv"), None)


def inject_phantom_committed_receipt(client: FakeClient, saved: ImportDraft) -> ImportReceipt:
    receipt = ImportReceipt(
        receipt_id=f"rcp_{deterministic_ulid(ORG, saved.import_id)}",
        import_id=saved.import_id,
        organization_id=ORG,
        source_snapshot_id=saved.source_snapshot.snapshot_id,
        source_snapshot_digest=saved.source_snapshot.semantic_digest,
        graph_version=1,
        committed_subject_count=len(saved.candidate.subjects),
        committed_at=NOW,
        committed_by_uid=OWNER.uid,
    )
    import_path = (COLLECTION, ORG, "imports", saved.import_id)
    client.data[import_path].update(
        {
            "status": "committed",
            "receipt": receipt.model_dump(mode="python"),
        }
    )
    client.data[import_path]["payload_digest"] = independent_digest(
        {
            key: value
            for key, value in client.data[import_path].items()
            if key != "payload_digest"
        }
    )
    return receipt


def inject_committed_receipt(
    client: FakeClient,
    saved: ImportDraft,
    *,
    graph_version: int,
) -> ImportReceipt:
    receipt = ImportReceipt(
        receipt_id=f"rcp_{deterministic_ulid(ORG, saved.import_id)}",
        import_id=saved.import_id,
        organization_id=ORG,
        source_snapshot_id=saved.source_snapshot.snapshot_id,
        source_snapshot_digest=saved.source_snapshot.semantic_digest,
        graph_version=graph_version,
        committed_subject_count=len(saved.candidate.subjects),
        committed_at=NOW,
        committed_by_uid=OWNER.uid,
    )
    import_path = (COLLECTION, ORG, "imports", saved.import_id)
    client.data[import_path].update(
        {
            "status": "committed",
            "receipt": receipt.model_dump(mode="python"),
        }
    )
    client.data[import_path]["payload_digest"] = independent_digest(
        {
            key: value
            for key, value in client.data[import_path].items()
            if key != "payload_digest"
        }
    )
    return receipt


def replace_stored_graph(
    client: FakeClient,
    repository: FirestoreOrganizationGraphRepository,
    graph: OrganizationGraph,
) -> None:
    storage, chunks = organization_store._chunked_graph(graph)
    version_path = repository._version_ref(ORG, graph.version).path
    chunk_prefix = (*version_path, "chunks")
    for path in tuple(client.data):
        if path[: len(chunk_prefix)] == chunk_prefix:
            client.data.pop(path)
    client.data[version_path] = deepcopy(storage)
    for chunk_id, chunk in chunks.items():
        client.data[(*chunk_prefix, chunk_id)] = deepcopy(chunk)
    state_path = repository._state_ref(ORG).path
    if client.data[state_path]["current_version"] == graph.version:
        client.data[state_path]["payload_digest"] = storage["payload_digest"]


def seed_activation_transition_range(
    client: FakeClient,
    *,
    first_version: int,
    last_version: int,
) -> None:
    for version in range(first_version, last_version + 1):
        transition = organization_store._activation_transition(
            organization_id=ORG,
            kind="invitations_created",
            subject_ids=(SUBJECT,),
            prior_graph_version=version - 1,
            member_uid=None,
            occurred_at=NOW,
        )
        client.data[
            (
                COLLECTION,
                ORG,
                "organization_activation_transitions",
                f"{version:020d}",
            )
        ] = transition.model_dump(mode="python")


@pytest.mark.parametrize(
    ("operation", "expected_error"),
    (
        (lambda repository, context: repository.load_context(OWNER, ORG), OrganizationUnavailable),
        (lambda repository, context: repository.save_import_draft(context, draft()), ImportUnavailable),
        (
            lambda repository, context: repository.load_import_draft(
                context,
                "imp_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            ),
            ImportUnavailable,
        ),
        (lambda repository, context: repository.list_imports(context), ImportUnavailable),
        (
            lambda repository, context: repository.commit_graph(
                context,
                draft_id="imp_01ARZ3NDEKTSV4RRFFQ69G5FAV",
                reviewed_digest=DIGEST,
            ),
            ImportUnavailable,
        ),
        (lambda repository, context: repository.load_graph(context), OrganizationUnavailable),
        (
            lambda repository, context: repository.bind_member(
                context,
                subject_id=SUBJECT,
                member_uid=OWNER.uid,
            ),
            OrganizationUnavailable,
        ),
        (lambda repository, context: repository.list_audit(context), OrganizationUnavailable),
    ),
)
def test_firestore_public_error_barrier_removes_provider_exception_graph(
    monkeypatch,
    operation,
    expected_error,
) -> None:
    from google.cloud import firestore

    monkeypatch.setattr(firestore, "transactional", lambda function: function)
    repository = FirestoreOrganizationGraphRepository(
        FakeClient(failure=PRIVATE),
        clock=lambda: NOW,
        organization_collection=COLLECTION,
        audit_collection=AUDIT,
    )
    context = DecisionOSContext(
        principal=OWNER,
        membership=OrganizationMembership(
            organization_id=ORG,
            uid=OWNER.uid,
            role=DecisionOSRole.OWNER,
            status=MembershipStatus.ACTIVE,
        ),
    )

    with pytest.raises(expected_error) as captured:
        operation(repository, context)

    assert PRIVATE not in exception_graph_text(captured.value)


def test_firestore_commit_keeps_strict_decisionos_root_compatible(fake_firestore) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())

    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )

    assert DecisionOrganization.model_validate(client.data[(COLLECTION, ORG)]).organization_id == ORG
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        organization_collection=COLLECTION,
        invitation_index_collection="test_invites",
    )
    assert decisionos.load_context(OWNER, ORG) == context
    assert decisionos.list_organizations(OWNER)[0].organization_id == ORG


def test_firestore_reads_exact_receipt_and_committed_graph_version(fake_firestore) -> None:
    _client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    assert repository.load_import_receipt(context, saved.import_id) is None
    assert repository.load_committed_import(context, 0) is None
    receipt = repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )

    assert repository.load_import_receipt(context, saved.import_id) == receipt
    assert repository.load_committed_import(context, receipt.graph_version) == (
        saved,
        receipt,
    )
    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository.load_committed_import(context, receipt.graph_version + 1)


def test_firestore_subject_invitation_accepts_membership_and_binding_atomically(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=Identifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    repository.initialize_subject_invitation_schema(decisionos, context)
    grants = repository.create_subject_invitations(
        decisionos,
        context,
        subject_ids=(SUBJECT,),
        role=DecisionOSRole.CONTRIBUTOR,
        expires_in=timedelta(days=1),
        delivery_route_id="consented_test_route",
    )
    issued = repository.load_graph(context)
    assert issued.version == 2
    assert issued.subjects[0].lifecycle is SubjectLifecycle.INVITED
    token = grants[0].token.get_secret_value()
    sending = decisionos.begin_subject_invitation_delivery(context, grants[0])
    decisionos.record_subject_invitation_delivery(
        context,
        sending,
        delivered=True,
    )
    invitee = DecisionOSPrincipal(
        uid="firebase-subject-invitee",
        email_verified=True,
        provider_ids=("google.com",),
    )

    membership, subject = repository.accept_subject_invitation(
        decisionos,
        invitee,
        token,
    )

    assert membership.uid == invitee.uid
    assert subject.member_uid == invitee.uid
    assert repository.load_graph(context).version == 3
    assert repository.load_committed_import(context, 3) == (saved, repository.load_import_receipt(context, saved.import_id))
    assert decisionos.load_context(invitee, ORG).membership.role is DecisionOSRole.CONTRIBUTOR
    assert token not in repr(client.data)
    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        repository.accept_subject_invitation(decisionos, invitee, token)


def test_firestore_subject_acceptance_abort_rolls_back_and_retry_advances_once(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=Identifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    repository.initialize_subject_invitation_schema(decisionos, context)
    grant = repository.create_subject_invitations(
        decisionos,
        context,
        subject_ids=(SUBJECT,),
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
        delivery_route_id="consented_test_route",
    )[0]
    sending = decisionos.begin_subject_invitation_delivery(context, grant)
    decisionos.record_subject_invitation_delivery(context, sending, delivered=True)
    token = grant.token.get_secret_value()
    invitee = DecisionOSPrincipal(
        uid="firebase-transaction-retry",
        email_verified=True,
        provider_ids=("google.com",),
    )
    before = deepcopy(client.data)
    client.abort_next_transaction = True

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        repository.accept_subject_invitation(decisionos, invitee, token)

    assert client.data == before
    membership, subject = repository.accept_subject_invitation(
        decisionos,
        invitee,
        token,
    )
    assert membership.uid == subject.member_uid == invitee.uid
    assert repository.load_graph(context).version == 3


def test_firestore_pending_delivery_reissues_but_sending_survives_restart(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=SequenceFirestoreIdentifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    repository.initialize_subject_invitation_schema(decisionos, context)
    grant = repository.create_subject_invitations(
        decisionos,
        context,
        subject_ids=(SUBJECT,),
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
        delivery_route_id="consented_test_route",
    )[0]
    token = grant.token.get_secret_value()
    pending_retry = repository.create_subject_invitations(
        decisionos,
        context,
        subject_ids=(SUBJECT,),
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
        delivery_route_id="consented_test_route",
    )[0]
    assert pending_retry.invitation_id != grant.invitation_id
    assert pending_retry.token is not None
    assert (
        pending_retry.delivery_status
        is SubjectInvitationDeliveryState.DELIVERY_PENDING
    )
    sending = decisionos.begin_subject_invitation_delivery(context, pending_retry)
    assert sending.delivery_status is SubjectInvitationDeliveryState.DELIVERY_SENDING

    restarted = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=SequenceFirestoreIdentifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    before_attempts = client.provider_mutation_attempts
    retry = repository.create_subject_invitations(
        restarted,
        context,
        subject_ids=(SUBJECT,),
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
        delivery_route_id="consented_test_route",
    )[0]

    assert retry.invitation_id == pending_retry.invitation_id
    assert retry.token is None
    assert retry.delivery_status is SubjectInvitationDeliveryState.DELIVERY_SENDING
    assert repository.load_graph(context).version == 2
    assert client.provider_mutation_attempts == before_attempts
    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        repository.accept_subject_invitation(
            restarted,
            DecisionOSPrincipal(
                uid="firebase-unknown-delivery",
                email_verified=True,
                provider_ids=("google.com",),
            ),
            token,
        )


def test_firestore_sending_survives_restart_and_expiry_without_any_write(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    current_time = [NOW]
    identifiers = SequenceFirestoreIdentifiers()
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: current_time[0],
        identifiers=identifiers,
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    repository.initialize_subject_invitation_schema(decisionos, context)
    pending = repository.create_subject_invitations(
        decisionos,
        context,
        subject_ids=(SUBJECT,),
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(seconds=60),
        delivery_route_id="consented_test_route",
    )[0]
    sending = decisionos.begin_subject_invitation_delivery(context, pending)
    token = pending.token.get_secret_value()
    digest = sha256(token.encode()).hexdigest()
    current_time[0] += timedelta(seconds=61)
    before = deepcopy(client.data)
    attempts_before = client.provider_mutation_attempts
    sequence_before = identifiers.sequence
    issued_version = repository.load_graph(context).version

    restarted = FirestoreDecisionOSRepository(
        client,
        clock=lambda: current_time[0],
        identifiers=identifiers,
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    retried = repository.create_subject_invitations(
        restarted,
        context,
        subject_ids=(SUBJECT,),
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(seconds=60),
        delivery_route_id="consented_test_route",
    )[0]

    assert retried.invitation_id == sending.invitation_id
    assert retried.token is None
    assert retried.delivery_status is SubjectInvitationDeliveryState.DELIVERY_SENDING
    assert identifiers.sequence == sequence_before
    assert client.provider_mutation_attempts == attempts_before
    assert client.data == before
    assert repository.load_graph(context).version == issued_version
    assert token not in repr(retried)
    assert digest not in repr(retried)


@pytest.mark.parametrize("disguise_as_exact_legacy", [False, True])
def test_firestore_generic_acceptance_rejects_subject_invitation_with_removed_kind(
    fake_firestore,
    disguise_as_exact_legacy,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=SequenceFirestoreIdentifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    repository.initialize_subject_invitation_schema(decisionos, context)
    grant = repository.create_subject_invitations(
        decisionos,
        context,
        subject_ids=(SUBJECT,),
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
        delivery_route_id=None,
    )[0]
    token = grant.token.get_secret_value()
    digest = sha256(token.encode()).hexdigest()
    invitation_path = (COLLECTION, ORG, "invitations", grant.invitation_id)
    index_path = ("test_subject_invites", digest)
    client.data[invitation_path].pop("invitation_kind")
    client.data[index_path].pop("invitation_kind")
    if disguise_as_exact_legacy:
        invitation = client.data[invitation_path]
        client.data[invitation_path] = {
            key: invitation[key]
            for key in (
                "invitation_id",
                "organization_id",
                "role",
                "expires_at",
                "status",
            )
        }
        index = client.data[index_path]
        client.data[index_path] = {
            key: index[key]
            for key in ("invitation_id", "organization_id", "status")
        }
    before = deepcopy(client.data)
    invitee = DecisionOSPrincipal(
        uid="firebase-corrupt-cross-kind",
        email_verified=True,
        provider_ids=("google.com",),
    )

    restarted = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        restarted.accept_invitation(invitee, token)

    assert client.data == before
    assert (COLLECTION, ORG, "members", invitee.uid) not in client.data
    graph = repository.load_graph(context)
    assert graph.version == 2
    assert graph.subjects[0].lifecycle is SubjectLifecycle.INVITED
    assert graph.subjects[0].member_uid is None


@pytest.mark.parametrize("state_corruption", ["missing", "different_digest"])
def test_firestore_legacy_generic_requires_positive_provenance_when_subject_state_is_corrupt(
    fake_firestore,
    state_corruption,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=SequenceFirestoreIdentifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    repository.initialize_subject_invitation_schema(decisionos, context)
    grant = repository.create_subject_invitations(
        decisionos,
        context,
        subject_ids=(SUBJECT,),
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
        delivery_route_id=None,
    )[0]
    token = grant.token.get_secret_value()
    digest = sha256(token.encode()).hexdigest()
    invitation_path = (COLLECTION, ORG, "invitations", grant.invitation_id)
    index_path = ("test_subject_invites", digest)
    state_path = (COLLECTION, ORG, "subject_invitation_state", SUBJECT)
    invitation = client.data[invitation_path]
    client.data[invitation_path] = {
        key: invitation[key]
        for key in (
            "invitation_id",
            "organization_id",
            "role",
            "expires_at",
            "status",
        )
    }
    index = client.data[index_path]
    client.data[index_path] = {
        key: index[key]
        for key in ("invitation_id", "organization_id", "status")
    }
    if state_corruption == "missing":
        client.data.pop(state_path)
    else:
        client.data[state_path]["token_digest"] = "c" * 64
    before = deepcopy(client.data)
    invitee = DecisionOSPrincipal(
        uid=f"firebase-corrupt-{state_corruption}",
        email_verified=True,
        provider_ids=("google.com",),
    )

    restarted = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        restarted.accept_invitation(invitee, token)

    assert client.data == before
    assert (COLLECTION, ORG, "members", invitee.uid) not in client.data
    graph = repository.load_graph(context)
    assert graph.version == 2
    assert graph.subjects[0].lifecycle is SubjectLifecycle.INVITED
    assert graph.subjects[0].member_uid is None


def test_firestore_runtime_generic_acceptance_never_initializes_legacy_provenance(
    fake_firestore,
) -> None:
    client, _repository, context = fake_firestore
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=SequenceFirestoreIdentifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    historical = decisionos.create_invitation(
        context,
        role=DecisionOSRole.CONTRIBUTOR,
        expires_in=timedelta(days=1),
    )
    token = historical.token.get_secret_value()
    digest = sha256(token.encode()).hexdigest()
    invitation_path = (
        COLLECTION,
        ORG,
        "invitations",
        historical.invitation_id,
    )
    index_path = ("test_subject_invites", digest)
    marker_path = (
        COLLECTION,
        ORG,
        "invitation_migrations",
        "legacy_generic_cutover",
    )
    provenance_path = (
        COLLECTION,
        ORG,
        "legacy_generic_invitation_provenance",
        digest,
    )
    client.data[invitation_path].pop("invitation_kind")
    client.data[index_path].pop("invitation_kind")
    before = deepcopy(client.data)
    invitee = DecisionOSPrincipal(
        uid="firebase-uninitialized-historical-generic",
        email_verified=True,
        provider_ids=("google.com",),
    )

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        decisionos.accept_invitation(invitee, token)

    assert client.data == before
    assert marker_path not in client.data
    assert provenance_path not in client.data
    assert (COLLECTION, ORG, "members", invitee.uid) not in client.data


def test_firestore_trusted_initialization_migrates_legacy_before_subject_schema(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=SequenceFirestoreIdentifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    historical = decisionos.create_invitation(
        context,
        role=DecisionOSRole.CONTRIBUTOR,
        expires_in=timedelta(days=1),
    )
    token = historical.token.get_secret_value()
    digest = sha256(token.encode()).hexdigest()
    invitation_path = (
        COLLECTION,
        ORG,
        "invitations",
        historical.invitation_id,
    )
    index_path = ("test_subject_invites", digest)
    marker_path = (
        COLLECTION,
        ORG,
        "invitation_migrations",
        "legacy_generic_cutover",
    )
    provenance_path = (
        COLLECTION,
        ORG,
        "legacy_generic_invitation_provenance",
        digest,
    )
    client.data[invitation_path].pop("invitation_kind")
    client.data[index_path].pop("invitation_kind")

    repository.initialize_subject_invitation_schema(decisionos, context)

    assert marker_path in client.data
    assert provenance_path in client.data
    initialized = deepcopy(client.data)
    restarted_graph = FirestoreOrganizationGraphRepository(
        client,
        clock=lambda: NOW,
        organization_collection=COLLECTION,
        audit_collection=AUDIT,
    )
    restarted_decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    restarted_graph.initialize_subject_invitation_schema(
        restarted_decisionos,
        context,
    )
    assert client.data == initialized

    invitee = DecisionOSPrincipal(
        uid="firebase-migrated-historical-generic",
        email_verified=True,
        provider_ids=("google.com",),
    )
    assert restarted_decisionos.accept_invitation(invitee, token).uid == invitee.uid
    assert client.data[marker_path] == initialized[marker_path]
    assert client.data[provenance_path] == initialized[provenance_path]


@pytest.mark.parametrize(
    "evidence_kind",
    ["subject_grant", "subject_index", "subject_state", "activation_transition"],
)
def test_firestore_trusted_initialization_rejects_any_subject_schema_evidence(
    fake_firestore,
    evidence_kind,
) -> None:
    client, repository, context = fake_firestore
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    subject = _stored_subject_invitation(NOW + timedelta(days=1))
    if evidence_kind == "subject_grant":
        path = (COLLECTION, ORG, "invitations", subject["invitation_id"])
        client.data[path] = subject
    elif evidence_kind == "subject_index":
        client.data[("test_subject_invites", "d" * 64)] = subject
    elif evidence_kind == "subject_state":
        client.data[(COLLECTION, ORG, "subject_invitation_state", SUBJECT)] = {
            **subject,
            "token_digest": "d" * 64,
        }
    else:
        client.data[
            (
                COLLECTION,
                ORG,
                "organization_activation_transitions",
                "00000000000000000001",
            )
        ] = {"subject_schema_evidence": True}
    before = deepcopy(client.data)

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        repository.initialize_subject_invitation_schema(decisionos, context)

    assert client.data == before
    assert (
        COLLECTION,
        ORG,
        "invitation_migrations",
        "legacy_generic_cutover",
    ) not in client.data


@pytest.mark.parametrize("lifecycle", [SubjectLifecycle.INVITED, SubjectLifecycle.ACTIVE])
def test_firestore_trusted_initialization_rejects_subject_graph_activation_evidence(
    fake_firestore,
    lifecycle,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    graph = repository.load_graph(context)
    member_uid = "firebase-graph-evidence" if lifecycle is SubjectLifecycle.ACTIVE else None
    forged = graph.model_copy(
        update={
            "subjects": tuple(
                item.model_copy(
                    update={"lifecycle": lifecycle, "member_uid": member_uid}
                )
                if item.subject_id == SUBJECT
                else item
                for item in graph.subjects
            )
        }
    )
    replace_stored_graph(client, repository, forged)
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    before = deepcopy(client.data)

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        repository.initialize_subject_invitation_schema(decisionos, context)

    assert client.data == before


def test_firestore_migration_cannot_run_again_after_subject_features_are_used(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=SequenceFirestoreIdentifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    repository.initialize_subject_invitation_schema(decisionos, context)
    repository.create_subject_invitations(
        decisionos,
        context,
        subject_ids=(SUBJECT,),
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
        delivery_route_id=None,
    )
    before = deepcopy(client.data)

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        repository.initialize_subject_invitation_schema(decisionos, context)

    assert client.data == before


def test_firestore_recreated_cutover_marker_is_not_trusted_after_restart(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=SequenceFirestoreIdentifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    historical = decisionos.create_invitation(
        context,
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
    )
    token = historical.token.get_secret_value()
    digest = sha256(token.encode()).hexdigest()
    invitation_path = (COLLECTION, ORG, "invitations", historical.invitation_id)
    index_path = ("test_subject_invites", digest)
    marker_path = (
        COLLECTION,
        ORG,
        "invitation_migrations",
        "legacy_generic_cutover",
    )
    client.data[invitation_path].pop("invitation_kind")
    client.data[index_path].pop("invitation_kind")
    repository.initialize_subject_invitation_schema(decisionos, context)
    recreate_document(client, marker_path)
    before = deepcopy(client.data)
    restarted = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    invitee = DecisionOSPrincipal(
        uid="firebase-recreated-cutover",
        email_verified=True,
        provider_ids=("google.com",),
    )

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        restarted.accept_invitation(invitee, token)
    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        repository.initialize_subject_invitation_schema(restarted, context)

    assert client.data == before
    assert (COLLECTION, ORG, "members", invitee.uid) not in client.data


@pytest.mark.parametrize("corruption", ["missing", "recreated", "rebound"])
def test_firestore_legacy_acceptance_requires_original_exact_provenance(
    fake_firestore,
    corruption,
) -> None:
    client, repository, context = fake_firestore
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=SequenceFirestoreIdentifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    historical = decisionos.create_invitation(
        context,
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
    )
    token = historical.token.get_secret_value()
    digest = sha256(token.encode()).hexdigest()
    invitation_path = (COLLECTION, ORG, "invitations", historical.invitation_id)
    index_path = ("test_subject_invites", digest)
    provenance_path = (
        COLLECTION,
        ORG,
        "legacy_generic_invitation_provenance",
        digest,
    )
    client.data[invitation_path].pop("invitation_kind")
    client.data[index_path].pop("invitation_kind")
    repository.initialize_subject_invitation_schema(decisionos, context)
    if corruption == "missing":
        client.data.pop(provenance_path)
        client.create_times.pop(provenance_path, None)
        client.update_times.pop(provenance_path, None)
    elif corruption == "recreated":
        recreate_document(client, provenance_path)
    else:
        client.data[provenance_path]["invitation_id"] = (
            "inv_99999999999999999999999999"
        )
    before = deepcopy(client.data)
    restarted = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    invitee = DecisionOSPrincipal(
        uid=f"firebase-corrupt-provenance-{corruption}",
        email_verified=True,
        provider_ids=("google.com",),
    )

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        restarted.accept_invitation(invitee, token)

    assert client.data == before
    assert (COLLECTION, ORG, "members", invitee.uid) not in client.data


def test_firestore_forged_marker_and_provenance_pair_is_rejected(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=SequenceFirestoreIdentifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    historical = decisionos.create_invitation(
        context,
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
    )
    token = historical.token.get_secret_value()
    digest = sha256(token.encode()).hexdigest()
    invitation_path = (COLLECTION, ORG, "invitations", historical.invitation_id)
    index_path = ("test_subject_invites", digest)
    marker_path = (
        COLLECTION,
        ORG,
        "invitation_migrations",
        "legacy_generic_cutover",
    )
    provenance_path = (
        COLLECTION,
        ORG,
        "legacy_generic_invitation_provenance",
        digest,
    )
    client.data[invitation_path].pop("invitation_kind")
    client.data[index_path].pop("invitation_kind")
    repository.initialize_subject_invitation_schema(decisionos, context)
    forged_id = "f" * 64
    transaction = client.transaction()
    marker = deepcopy(client.data[marker_path])
    marker["cutover_id"] = forged_id
    provenance = deepcopy(client.data[provenance_path])
    provenance["cutover_id"] = forged_id
    transaction.set(FakeDocument(client, marker_path), marker)
    transaction.set(FakeDocument(client, provenance_path), provenance)
    transaction.commit()
    before = deepcopy(client.data)
    invitee = DecisionOSPrincipal(
        uid="firebase-forged-cutover-pair",
        email_verified=True,
        provider_ids=("google.com",),
    )

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        decisionos.accept_invitation(invitee, token)

    assert client.data == before
    assert (COLLECTION, ORG, "members", invitee.uid) not in client.data


def test_firestore_initialization_abort_is_atomic_and_restart_retry_is_clean(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=SequenceFirestoreIdentifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    historical = decisionos.create_invitation(
        context,
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
    )
    digest = sha256(historical.token.get_secret_value().encode()).hexdigest()
    invitation_path = (COLLECTION, ORG, "invitations", historical.invitation_id)
    index_path = ("test_subject_invites", digest)
    client.data[invitation_path].pop("invitation_kind")
    client.data[index_path].pop("invitation_kind")
    before = deepcopy(client.data)
    client.abort_next_transaction = True

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        repository.initialize_subject_invitation_schema(decisionos, context)

    assert client.data == before
    restarted = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    repository.initialize_subject_invitation_schema(restarted, context)
    assert (
        COLLECTION,
        ORG,
        "invitation_migrations",
        "legacy_generic_cutover",
    ) in client.data
    assert (
        COLLECTION,
        ORG,
        "legacy_generic_invitation_provenance",
        digest,
    ) in client.data


@pytest.mark.parametrize(
    "relation_corruption",
    [
        "intact",
        "missing_state",
        "rebound_state",
        "recreated_state",
        "missing_index",
        "rebound_index",
        "recreated_index",
        "disguised_grant",
    ],
)
def test_firestore_deleted_cutover_cannot_be_recovered_by_subject_retry_or_accept(
    fake_firestore,
    relation_corruption,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=SequenceFirestoreIdentifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    repository.initialize_subject_invitation_schema(decisionos, context)
    grant = repository.create_subject_invitations(
        decisionos,
        context,
        subject_ids=(SUBJECT,),
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
        delivery_route_id=None,
    )[0]
    token = grant.token.get_secret_value()
    digest = sha256(token.encode()).hexdigest()
    invitation_path = (COLLECTION, ORG, "invitations", grant.invitation_id)
    index_path = ("test_subject_invites", digest)
    marker_path = (
        COLLECTION,
        ORG,
        "invitation_migrations",
        "legacy_generic_cutover",
    )
    state_path = (COLLECTION, ORG, "subject_invitation_state", SUBJECT)
    client.data.pop(marker_path)
    client.create_times.pop(marker_path, None)
    client.update_times.pop(marker_path, None)
    if relation_corruption == "missing_state":
        client.data.pop(state_path)
    elif relation_corruption == "rebound_state":
        client.data[state_path]["token_digest"] = "c" * 64
    elif relation_corruption == "recreated_state":
        recreate_document(client, state_path)
    elif relation_corruption == "missing_index":
        client.data.pop(index_path)
    elif relation_corruption == "rebound_index":
        client.data[index_path]["invitation_id"] = (
            "inv_99999999999999999999999999"
        )
    elif relation_corruption == "recreated_index":
        recreate_document(client, index_path)
    elif relation_corruption == "disguised_grant":
        invitation = client.data[invitation_path]
        client.data[invitation_path] = {
            key: invitation[key]
            for key in (
                "invitation_id",
                "organization_id",
                "role",
                "expires_at",
                "status",
            )
        }
        index = client.data[index_path]
        client.data[index_path] = {
            key: index[key]
            for key in ("invitation_id", "organization_id", "status")
        }
    before_retry = deepcopy(client.data)

    restarted = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=SequenceFirestoreIdentifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        repository.create_subject_invitations(
            restarted,
            context,
            subject_ids=(SUBJECT,),
            role=DecisionOSRole.VIEWER,
            expires_in=timedelta(days=1),
            delivery_route_id=None,
        )
    assert client.data == before_retry
    assert marker_path not in client.data

    if relation_corruption not in {
        "missing_index",
        "rebound_index",
        "recreated_index",
    }:
        invitation = client.data[invitation_path]
        if "invitation_kind" in invitation:
            client.data[invitation_path] = {
                key: invitation[key]
                for key in (
                    "invitation_id",
                    "organization_id",
                    "role",
                    "expires_at",
                    "status",
                )
            }
        index = client.data[index_path]
        if "invitation_kind" in index:
            client.data[index_path] = {
                key: index[key]
                for key in ("invitation_id", "organization_id", "status")
            }
    before_accept = deepcopy(client.data)
    invitee = DecisionOSPrincipal(
        uid=f"firebase-deleted-cutover-{relation_corruption}",
        email_verified=True,
        provider_ids=("google.com",),
    )

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        restarted.accept_invitation(invitee, token)

    assert client.data == before_accept
    assert marker_path not in client.data
    assert (COLLECTION, ORG, "members", invitee.uid) not in client.data
    graph = repository.load_graph(context)
    assert graph.subjects[0].lifecycle is SubjectLifecycle.INVITED
    assert graph.subjects[0].member_uid is None


@pytest.mark.parametrize("marker_corruption", ["missing", "recreated"])
def test_firestore_subject_acceptance_fails_closed_after_cutover_loss(
    fake_firestore,
    marker_corruption,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=SequenceFirestoreIdentifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    repository.initialize_subject_invitation_schema(decisionos, context)
    grant = repository.create_subject_invitations(
        decisionos,
        context,
        subject_ids=(SUBJECT,),
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
        delivery_route_id="consented_test_route",
    )[0]
    token = grant.token.get_secret_value()
    sending = decisionos.begin_subject_invitation_delivery(context, grant)
    decisionos.record_subject_invitation_delivery(context, sending, delivered=True)
    marker_path = (
        COLLECTION,
        ORG,
        "invitation_migrations",
        "legacy_generic_cutover",
    )
    if marker_corruption == "missing":
        client.data.pop(marker_path)
        client.create_times.pop(marker_path, None)
        client.update_times.pop(marker_path, None)
    else:
        recreate_document(client, marker_path)
    before = deepcopy(client.data)
    restarted_decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    restarted_graph = FirestoreOrganizationGraphRepository(
        client,
        clock=lambda: NOW,
        organization_collection=COLLECTION,
        audit_collection=AUDIT,
    )
    invitee = DecisionOSPrincipal(
        uid=f"firebase-subject-cutover-{marker_corruption}",
        email_verified=True,
        provider_ids=("google.com",),
    )

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        restarted_graph.accept_subject_invitation(
            restarted_decisionos,
            invitee,
            token,
        )

    assert client.data == before
    assert (COLLECTION, ORG, "members", invitee.uid) not in client.data
    graph = restarted_graph.load_graph(context)
    assert graph.subjects[0].lifecycle is SubjectLifecycle.INVITED
    assert graph.subjects[0].member_uid is None


def test_firestore_generic_invitation_supports_exact_new_and_historical_schemas(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    identifiers = SequenceFirestoreIdentifiers()
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=identifiers,
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    current = decisionos.create_invitation(
        context,
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
    )
    current_digest = sha256(current.token.get_secret_value().encode()).hexdigest()
    current_invitation_path = (
        COLLECTION,
        ORG,
        "invitations",
        current.invitation_id,
    )
    current_index_path = ("test_subject_invites", current_digest)
    assert client.data[current_invitation_path]["invitation_kind"] == "generic"
    assert client.data[current_index_path]["invitation_kind"] == "generic"
    current_invitee = DecisionOSPrincipal(
        uid="firebase-current-generic",
        email_verified=True,
        provider_ids=("google.com",),
    )
    assert decisionos.accept_invitation(
        current_invitee,
        current.token.get_secret_value(),
    ).uid == current_invitee.uid

    historical = decisionos.create_invitation(
        context,
        role=DecisionOSRole.CONTRIBUTOR,
        expires_in=timedelta(days=1),
    )
    historical_digest = sha256(
        historical.token.get_secret_value().encode()
    ).hexdigest()
    historical_invitation_path = (
        COLLECTION,
        ORG,
        "invitations",
        historical.invitation_id,
    )
    historical_index_path = ("test_subject_invites", historical_digest)
    client.data[historical_invitation_path].pop("invitation_kind")
    client.data[historical_index_path].pop("invitation_kind")
    repository.initialize_subject_invitation_schema(decisionos, context)
    historical_invitee = DecisionOSPrincipal(
        uid="firebase-historical-generic",
        email_verified=True,
        provider_ids=("google.com",),
    )
    restarted = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )

    assert restarted.accept_invitation(
        historical_invitee,
        historical.token.get_secret_value(),
    ).uid == historical_invitee.uid
    provenance_path = (
        COLLECTION,
        ORG,
        "legacy_generic_invitation_provenance",
        historical_digest,
    )
    marker_path = (
        COLLECTION,
        ORG,
        "invitation_migrations",
        "legacy_generic_cutover",
    )
    sdk_created_at = client.create_times[historical_invitation_path]
    expected_created_at = datetime(
        sdk_created_at.year,
        sdk_created_at.month,
        sdk_created_at.day,
        sdk_created_at.hour,
        sdk_created_at.minute,
        sdk_created_at.second,
        sdk_created_at.microsecond,
        tzinfo=UTC,
    )
    marker = client.data[marker_path]
    provenance = client.data[provenance_path]
    assert marker["schema_version"] == 2
    assert marker["migration_kind"] == "legacy_generic_cutover"
    assert marker["organization_id"] == ORG
    assert marker["legacy_relation_count"] == 1
    assert re.fullmatch(r"[0-9a-f]{64}", marker["cutover_id"])
    assert re.fullmatch(r"[0-9a-f]{64}", marker["legacy_manifest_digest"])
    assert provenance["schema_version"] == 2
    assert provenance["provenance_kind"] == "legacy_generic_invitation"
    assert provenance["organization_id"] == ORG
    assert provenance["invitation_id"] == historical.invitation_id
    assert provenance["token_digest"] == historical_digest
    assert provenance["created_at"] == expected_created_at
    assert provenance["role"] == DecisionOSRole.CONTRIBUTOR.value
    assert provenance["expires_at"] == historical.expires_at
    assert provenance["cutover_id"] == marker["cutover_id"]
    assert provenance["cutover_initialized_at"] == marker["initialized_at"]
    assert type(client.data[provenance_path]["created_at"]) is datetime


def test_firestore_historical_generic_provenance_rejects_creation_after_expiry(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=SequenceFirestoreIdentifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    invitation = decisionos.create_invitation(
        context,
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
    )
    token = invitation.token.get_secret_value()
    digest = sha256(token.encode()).hexdigest()
    invitation_path = (
        COLLECTION,
        ORG,
        "invitations",
        invitation.invitation_id,
    )
    index_path = ("test_subject_invites", digest)
    client.data[invitation_path].pop("invitation_kind")
    client.data[index_path].pop("invitation_kind")
    impossible_created_at = DatetimeWithNanoseconds(
        2026,
        8,
        20,
        15,
        0,
        tzinfo=UTC,
    )
    for path in (invitation_path, index_path):
        client.create_times[path] = impossible_created_at
        client.update_times[path] = impossible_created_at
    before = deepcopy(client.data)
    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        repository.initialize_subject_invitation_schema(decisionos, context)

    assert client.data == before
    assert (
        COLLECTION,
        ORG,
        "invitation_migrations",
        "legacy_generic_cutover",
    ) not in client.data


def test_firestore_activation_transitions_survive_restart_and_corruption_fails_closed(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    receipt = repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=Identifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    repository.initialize_subject_invitation_schema(decisionos, context)
    repository.create_subject_invitations(
        decisionos,
        context,
        subject_ids=(SUBJECT,),
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
        delivery_route_id=None,
    )
    restarted = FirestoreOrganizationGraphRepository(
        client,
        clock=lambda: NOW,
        organization_collection=COLLECTION,
        audit_collection=AUDIT,
    )

    assert restarted.load_committed_import(context, 2) == (saved, receipt)
    transition_path = (
        COLLECTION,
        ORG,
        "organization_activation_transitions",
        "00000000000000000002",
    )
    client.data[transition_path]["subject_ids"] = [SUBJECT_B]

    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        restarted.load_committed_import(context, 2)


@pytest.mark.parametrize(
    "corruption",
    [
        "boolean_state_schema",
        "missing_subject_state",
        "missing_global_index",
        "extra_global_index_field",
        "boolean_invitation_schema",
        "hostile_state_key",
        "hostile_index_key",
        "hostile_invitation_key",
        "phantom_delivered_invitation",
    ],
)
def test_firestore_subject_invitation_relation_fails_closed_on_corruption(
    fake_firestore,
    corruption,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=SequenceFirestoreIdentifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    repository.initialize_subject_invitation_schema(decisionos, context)
    grant = repository.create_subject_invitations(
        decisionos,
        context,
        subject_ids=(SUBJECT,),
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
        delivery_route_id=None,
    )[0]
    state_path = (COLLECTION, ORG, "subject_invitation_state", SUBJECT)
    invitation_path = (COLLECTION, ORG, "invitations", grant.invitation_id)
    digest = client.data[state_path]["token_digest"]
    index_path = ("test_subject_invites", digest)
    if corruption == "boolean_state_schema":
        client.data[state_path]["schema_version"] = True
    elif corruption == "missing_subject_state":
        del client.data[state_path]
    elif corruption == "missing_global_index":
        del client.data[index_path]
    elif corruption == "extra_global_index_field":
        client.data[index_path]["private"] = PRIVATE
    elif corruption == "boolean_invitation_schema":
        client.data[invitation_path]["schema_version"] = True
    elif corruption == "hostile_state_key":
        client.data[state_path][ScalarKey("schema_version")] = client.data[
            state_path
        ].pop("schema_version")
    elif corruption == "hostile_index_key":
        client.data[index_path][ScalarKey("schema_version")] = client.data[
            index_path
        ].pop("schema_version")
    elif corruption == "hostile_invitation_key":
        client.data[invitation_path][ScalarKey("schema_version")] = client.data[
            invitation_path
        ].pop("schema_version")
    else:
        client.data[invitation_path]["delivery_status"] = "delivered"

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        repository.create_subject_invitations(
            decisionos,
            context,
            subject_ids=(SUBJECT,),
            role=DecisionOSRole.VIEWER,
            expires_in=timedelta(days=1),
            delivery_route_id=None,
        )


def test_firestore_directory_graph_rejects_inverse_phantom_grant_relation(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=SequenceFirestoreIdentifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    repository.initialize_subject_invitation_schema(decisionos, context)
    repository.create_subject_invitations(
        decisionos,
        context,
        subject_ids=(SUBJECT,),
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
        delivery_route_id=None,
    )
    invited = repository.load_graph(context)
    forged = invited.model_copy(
        update={
            "subjects": tuple(
                item.model_copy(update={"lifecycle": SubjectLifecycle.DIRECTORY_ONLY})
                if item.subject_id == SUBJECT
                else item
                for item in invited.subjects
            )
        }
    )
    replace_stored_graph(client, repository, forged)
    before = deepcopy(client.data)

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        repository.create_subject_invitations(
            decisionos,
            context,
            subject_ids=(SUBJECT,),
            role=DecisionOSRole.VIEWER,
            expires_in=timedelta(days=1),
            delivery_route_id=None,
        )

    assert client.data == before


def test_firestore_transition_replay_never_materializes_irrelevant_future_docs(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    receipt = repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=Identifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    repository.initialize_subject_invitation_schema(decisionos, context)
    repository.create_subject_invitations(
        decisionos,
        context,
        subject_ids=(SUBJECT,),
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
        delivery_route_id=None,
    )
    for version in range(10_000, 11_000):
        client.data[
            (
                COLLECTION,
                ORG,
                "organization_activation_transitions",
                f"{version:020d}",
            )
        ] = ExplodesIfMaterialized()

    assert repository.load_committed_import(context, 2) == (saved, receipt)


def test_firestore_transition_loader_accepts_full_five_thousand_subject_lifecycle(
    fake_firestore,
) -> None:
    client, repository, _context = fake_firestore
    seed_activation_transition_range(
        client,
        first_version=2,
        last_version=5_051,
    )

    transitions = repository._activation_transition_chain(
        None,
        ORG,
        receipt_version=1,
        target_version=5_051,
    )

    assert len(transitions) == 5_050
    assert transitions[0].new_graph_version == 2
    assert transitions[-1].new_graph_version == 5_051


def test_firestore_transition_loader_rejects_more_than_conservative_maximum(
    fake_firestore,
) -> None:
    client, repository, _context = fake_firestore
    before_limits = tuple(client.query_limits)

    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository._activation_transition_chain(
            None,
            ORG,
            receipt_version=1,
            target_version=10_002,
        )

    assert tuple(client.query_limits) == before_limits


def test_firestore_transition_loader_rejects_duplicate_claimed_version(
    fake_firestore,
) -> None:
    client, repository, _context = fake_firestore
    seed_activation_transition_range(
        client,
        first_version=2,
        last_version=2,
    )
    canonical_path = (
        COLLECTION,
        ORG,
        "organization_activation_transitions",
        "00000000000000000002",
    )
    client.data[
        (
            COLLECTION,
            ORG,
            "organization_activation_transitions",
            "duplicate_claim",
        )
    ] = deepcopy(client.data[canonical_path])

    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository._activation_transition_chain(
            None,
            ORG,
            receipt_version=1,
            target_version=2,
        )


def test_firestore_receipt_rejects_unrelated_valid_v1_graph_with_matching_subjects(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    graph = repository.load_graph(context)
    forged = graph.model_copy(
        update={
            "units": (
                OrganizationUnit(
                    unit_id="unit_01ARZ3NDEKTSV4RRFFQ69G5FAV",
                    organization_id=ORG,
                    name="Unrelated Valid Unit",
                ),
            )
        }
    )
    replace_stored_graph(client, repository, forged)
    before = deepcopy(client.data)
    client.provider_mutation_attempts = 0

    with pytest.raises(ImportUnavailable, match="^import_unavailable$"):
        repository.load_import_receipt(context, saved.import_id)

    assert client.data == before
    assert client.provider_mutation_attempts == 0


def test_firestore_receipt_rejects_bind_derived_v2_graph_for_different_draft(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    first = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=first.import_id,
        reviewed_digest=first.semantic_digest,
    )
    second = repository.save_import_draft(
        context,
        next_draft(
            first.model_copy(update={"base_graph_version": 1}),
            supersedes_import_id=first.import_id,
        ),
    )
    repository.bind_member(context, subject_id=SUBJECT, member_uid=OWNER.uid)
    inject_committed_receipt(client, second, graph_version=2)
    before = deepcopy(client.data)
    client.provider_mutation_attempts = 0

    with pytest.raises(ImportUnavailable, match="^import_unavailable$"):
        repository.load_import_receipt(context, second.import_id)

    assert client.data == before
    assert client.provider_mutation_attempts == 0


def test_firestore_receipt_provenance_reads_base_and_commit_in_one_transaction(
    fake_firestore,
    monkeypatch,
) -> None:
    _client, repository, context = fake_firestore
    first = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=first.import_id,
        reviewed_digest=first.semantic_digest,
    )
    second = repository.save_import_draft(
        context,
        next_draft(
            first.model_copy(update={"base_graph_version": 1}),
            supersedes_import_id=first.import_id,
        ),
    )
    receipt = repository.commit_graph(
        context,
        draft_id=second.import_id,
        reviewed_digest=second.semantic_digest,
    )
    original_graph_from_row = repository._graph_from_row
    graph_reads = []

    def recording_graph_from_row(
        organization_id,
        version,
        row,
        *,
        transaction=None,
    ):
        graph_reads.append((version, transaction))
        return original_graph_from_row(
            organization_id,
            version,
            row,
            transaction=transaction,
        )

    monkeypatch.setattr(repository, "_graph_from_row", recording_graph_from_row)

    assert repository.load_import_receipt(context, second.import_id) == receipt
    assert tuple(version for version, _transaction in graph_reads) == (1, 2)
    assert graph_reads[0][1] is not None
    assert graph_reads[0][1] is graph_reads[1][1]


@pytest.mark.parametrize(
    "operation",
    ["load_draft", "load_receipt", "list_imports", "load_committed"],
)
def test_firestore_import_reads_use_one_transactional_snapshot(
    fake_firestore,
    operation,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    if operation == "load_committed":
        repository.commit_graph(
            context,
            draft_id=saved.import_id,
            reviewed_digest=saved.semantic_digest,
        )
    client.transactions.clear()

    if operation == "load_draft":
        repository.load_import_draft(context, saved.import_id)
    elif operation == "load_receipt":
        repository.load_import_receipt(context, saved.import_id)
    elif operation == "list_imports":
        repository.list_imports(context)
    else:
        repository.load_committed_import(context, 1)

    assert len(client.transactions) == 1


def test_firestore_committed_import_carries_across_two_member_binding_versions(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    second_uid = "firebase-viewer-02"
    client.data[(COLLECTION, ORG, "members", second_uid)] = OrganizationMembership(
        organization_id=ORG,
        uid=second_uid,
        role=DecisionOSRole.VIEWER,
        status=MembershipStatus.ACTIVE,
    ).model_dump(mode="python")
    saved = repository.save_import_draft(context, two_subject_draft())
    receipt = repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    repository.bind_member(context, subject_id=SUBJECT, member_uid=OWNER.uid)
    repository.bind_member(context, subject_id=SUBJECT_B, member_uid=second_uid)

    assert repository.load_committed_import(context, 2) == (saved, receipt)
    assert repository.load_committed_import(context, 3) == (saved, receipt)
    assert client.query_limits[-2:] == [2, 3]


def test_firestore_committed_import_carry_rejects_reordered_subject_tuple(
    fake_firestore,
    monkeypatch,
) -> None:
    client, repository, context = fake_firestore
    second_uid = "firebase-viewer-02"
    client.data[(COLLECTION, ORG, "members", second_uid)] = OrganizationMembership(
        organization_id=ORG,
        uid=second_uid,
        role=DecisionOSRole.VIEWER,
        status=MembershipStatus.ACTIVE,
    ).model_dump(mode="python")
    saved = repository.save_import_draft(context, two_subject_draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    repository.bind_member(context, subject_id=SUBJECT, member_uid=OWNER.uid)
    receipt_graph, receipt_storage = repository._graph_from_row(
        ORG,
        1,
        repository._version_ref(ORG, 1).get(),
    )
    target_graph, target_storage = repository._graph_from_row(
        ORG,
        2,
        repository._version_ref(ORG, 2).get(),
    )
    target_graph = target_graph.model_copy(
        update={"subjects": tuple(reversed(target_graph.subjects))}
    )

    def reordered_graph_from_row(
        organization_id,
        version,
        row,
        *,
        transaction=None,
    ):
        del organization_id, row, transaction
        if version == 1:
            return receipt_graph, receipt_storage
        assert version == 2
        return target_graph, target_storage

    monkeypatch.setattr(repository, "_graph_from_row", reordered_graph_from_row)

    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository.load_committed_import(context, 2)


def test_firestore_committed_import_carry_rejects_suspended_subject_reactivation(
    fake_firestore,
    monkeypatch,
) -> None:
    _client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    repository.bind_member(context, subject_id=SUBJECT, member_uid=OWNER.uid)
    receipt_graph, receipt_storage = repository._graph_from_row(
        ORG,
        1,
        repository._version_ref(ORG, 1).get(),
    )
    target_graph, target_storage = repository._graph_from_row(
        ORG,
        2,
        repository._version_ref(ORG, 2).get(),
    )
    receipt_graph = receipt_graph.model_copy(
        update={
            "subjects": (
                receipt_graph.subjects[0].model_copy(
                    update={"lifecycle": SubjectLifecycle.SUSPENDED}
                ),
            )
        }
    )

    def suspended_graph_from_row(
        organization_id,
        version,
        row,
        *,
        transaction=None,
    ):
        del organization_id, row, transaction
        if version == 1:
            return receipt_graph, receipt_storage
        assert version == 2
        return target_graph, target_storage

    monkeypatch.setattr(repository, "_graph_from_row", suspended_graph_from_row)

    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository.load_committed_import(context, 2)


def test_firestore_newer_committed_import_supersedes_prior_receipt_predecessor(
    fake_firestore,
) -> None:
    _client, repository, context = fake_firestore
    first = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=first.import_id,
        reviewed_digest=first.semantic_digest,
    )
    repository.bind_member(context, subject_id=SUBJECT, member_uid=OWNER.uid)
    second = repository.save_import_draft(
        context,
        next_draft(
            first.model_copy(update={"base_graph_version": 2}),
            supersedes_import_id=first.import_id,
        ),
    )
    second_receipt = repository.commit_graph(
        context,
        draft_id=second.import_id,
        reviewed_digest=second.semantic_digest,
    )

    assert second_receipt.graph_version == 3
    assert repository.load_committed_import(context, 3) == (second, second_receipt)


@pytest.mark.parametrize(
    "corruption",
    ["subject", "lifecycle", "structure", "delta", "scalar", "offset", "dangling"],
)
def test_firestore_predecessor_rejects_non_binding_graph_delta(
    fake_firestore,
    monkeypatch,
    corruption,
) -> None:
    _client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    repository.bind_member(context, subject_id=SUBJECT, member_uid=OWNER.uid)
    receipt_graph, receipt_storage = repository._graph_from_row(
        ORG,
        1,
        repository._version_ref(ORG, 1).get(),
    )
    target_graph, target_storage = repository._graph_from_row(
        ORG,
        2,
        repository._version_ref(ORG, 2).get(),
    )
    target_version = 2
    if corruption == "subject":
        target_graph = target_graph.model_copy(
            update={
                "subjects": (
                    target_graph.subjects[0].model_copy(
                        update={"title": "PRIVATE-CHANGED"}
                    ),
                )
            }
        )
    elif corruption == "lifecycle":
        target_graph = target_graph.model_copy(
            update={
                "subjects": (
                    target_graph.subjects[0].model_copy(
                        update={"lifecycle": SubjectLifecycle.SUSPENDED}
                    ),
                )
            }
        )
    elif corruption == "structure":
        target_graph = target_graph.model_copy(
            update={
                "units": (
                    OrganizationUnit(
                        unit_id="unit_01ARZ3NDEKTSV4RRFFQ69G5FAV",
                        organization_id=ORG,
                        name="PRIVATE-UNIT",
                    ),
                )
            }
        )
    elif corruption == "delta":
        target_version = 3
        target_graph = target_graph.model_copy(update={"version": target_version})
    elif corruption == "scalar":
        private_title = HostileString("Engineering Lead", PRIVATE)
        receipt_graph = receipt_graph.model_copy(
            update={
                "subjects": (
                    receipt_graph.subjects[0].model_copy(update={"title": private_title}),
                )
            }
        )
        target_graph = target_graph.model_copy(
            update={
                "subjects": (
                    target_graph.subjects[0].model_copy(update={"title": private_title}),
                )
            }
        )
    elif corruption == "offset":
        receipt_assignment = AuthorityAssignment(
            assignment_id="auth_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            organization_id=ORG,
            subject_id=SUBJECT,
            decision_type="launch_decision",
            function=AuthorityFunction.DECISION_OWNER,
            effective_from=NOW,
        )
        target_assignment = receipt_assignment.model_copy(
            update={
                "effective_from": NOW.astimezone(timezone(timedelta(hours=-5)))
            }
        )
        receipt_graph = receipt_graph.model_copy(
            update={"authority_assignments": (receipt_assignment,)}
        )
        target_graph = target_graph.model_copy(
            update={"authority_assignments": (target_assignment,)}
        )
    else:
        dangling = OrganizationUnit(
            unit_id="unit_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            organization_id=ORG,
            name="Dangling",
            leader_subject_id=SUBJECT_B,
        )
        receipt_graph = receipt_graph.model_copy(update={"units": (dangling,)})
        target_graph = target_graph.model_copy(update={"units": (dangling,)})

    def corrupted_graph_from_row(
        organization_id,
        version,
        row,
        *,
        transaction=None,
    ):
        del organization_id, row, transaction
        if version == 1:
            return receipt_graph, receipt_storage
        assert version == target_version
        return target_graph, target_storage

    monkeypatch.setattr(repository, "_graph_from_row", corrupted_graph_from_row)

    with pytest.raises(ImportUnavailable, match="import_unavailable") as captured:
        repository.load_committed_import(context, target_version)
    assert PRIVATE not in exception_graph_text(captured.value)


def test_firestore_draft_status_rejects_receipt_even_with_valid_outer_digest(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    receipt = ImportReceipt(
        receipt_id=f"rcp_{deterministic_ulid(ORG, saved.import_id)}",
        import_id=saved.import_id,
        organization_id=ORG,
        source_snapshot_id=saved.source_snapshot.snapshot_id,
        source_snapshot_digest=saved.source_snapshot.semantic_digest,
        graph_version=1,
        committed_subject_count=len(saved.candidate.subjects),
        committed_at=NOW,
        committed_by_uid=OWNER.uid,
    )
    import_path = (COLLECTION, ORG, "imports", saved.import_id)
    client.data[import_path]["receipt"] = receipt.model_dump(mode="python")
    client.data[import_path]["payload_digest"] = independent_digest(
        {
            key: value
            for key, value in client.data[import_path].items()
            if key != "payload_digest"
        }
    )

    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository.load_import_receipt(context, saved.import_id)


@pytest.mark.parametrize(
    "operation",
    ["load_draft", "list_imports", "require_latest", "commit"],
)
def test_firestore_every_draft_read_rejects_injected_receipt_without_graph_publish(
    fake_firestore,
    operation,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    receipt = ImportReceipt(
        receipt_id=f"rcp_{deterministic_ulid(ORG, saved.import_id)}",
        import_id=saved.import_id,
        organization_id=ORG,
        source_snapshot_id=saved.source_snapshot.snapshot_id,
        source_snapshot_digest=saved.source_snapshot.semantic_digest,
        graph_version=1,
        committed_subject_count=len(saved.candidate.subjects),
        committed_at=NOW,
        committed_by_uid=OWNER.uid,
    )
    import_path = (COLLECTION, ORG, "imports", saved.import_id)
    client.data[import_path]["receipt"] = receipt.model_dump(mode="python")
    client.data[import_path]["payload_digest"] = independent_digest(
        {
            key: value
            for key, value in client.data[import_path].items()
            if key != "payload_digest"
        }
    )

    def invoke():
        if operation == "load_draft":
            return repository.load_import_draft(context, saved.import_id)
        if operation == "list_imports":
            return repository.list_imports(context)
        if operation == "require_latest":
            return repository.require_latest_import(context, saved.import_id)
        return repository.commit_graph(
            context,
            draft_id=saved.import_id,
            reviewed_digest=saved.semantic_digest,
        )

    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        invoke()
    assert repository.load_graph(context).version == 0


def test_firestore_legacy_lineage_scan_rejects_pending_receipt_corruption(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    first = repository.save_import_draft(context, draft())
    second = repository.save_import_draft(
        context,
        next_draft(first, supersedes_import_id=first.import_id),
    )
    downgrade_to_legacy_v1(client, first.import_id)
    downgrade_to_legacy_v1(client, second.import_id)
    receipt = ImportReceipt(
        receipt_id=f"rcp_{deterministic_ulid(ORG, first.import_id)}",
        import_id=first.import_id,
        organization_id=ORG,
        source_snapshot_id=first.source_snapshot.snapshot_id,
        source_snapshot_digest=first.source_snapshot.semantic_digest,
        graph_version=1,
        committed_subject_count=len(first.candidate.subjects),
        committed_at=NOW,
        committed_by_uid=OWNER.uid,
    )
    first_path = (COLLECTION, ORG, "imports", first.import_id)
    client.data[first_path]["receipt"] = receipt.model_dump(mode="python")
    client.data[first_path]["payload_digest"] = independent_digest(
        {
            key: value
            for key, value in client.data[first_path].items()
            if key != "payload_digest"
        }
    )

    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository.require_latest_import(context, second.import_id)
    assert repository.load_graph(context).version == 0


@pytest.mark.parametrize(
    "operation",
    [
        "load_draft",
        "load_receipt",
        "list_imports",
        "require_latest",
        "legacy_scan",
        "commit_retry",
        "load_committed",
    ],
)
def test_firestore_phantom_committed_receipt_fails_every_import_path(
    fake_firestore,
    operation,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    inject_phantom_committed_receipt(client, saved)
    if operation == "legacy_scan":
        client.data.pop((COLLECTION, ORG, "organization_import_lineage", "csv"), None)
    before = deepcopy(client.data)

    def invoke():
        if operation == "load_draft":
            return repository.load_import_draft(context, saved.import_id)
        if operation == "load_receipt":
            return repository.load_import_receipt(context, saved.import_id)
        if operation == "list_imports":
            return repository.list_imports(context)
        if operation == "require_latest":
            return repository.require_latest_import(context, saved.import_id)
        if operation == "legacy_scan":
            return repository.save_import_draft(
                context,
                next_draft(
                    saved,
                    import_id="imp_01ARZ3NDEKTSV4RRFFQ69G5FAW",
                    digest="2" * 64,
                ),
            )
        if operation == "commit_retry":
            return repository.commit_graph(
                context,
                draft_id=saved.import_id,
                reviewed_digest=saved.semantic_digest,
            )
        return repository.load_committed_import(context, 1)

    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        invoke()
    assert client.data == before


def test_firestore_graph_version_read_ignores_pending_and_rejects_corrupt_shape(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    committed = repository.save_import_draft(context, draft())
    receipt = repository.commit_graph(
        context,
        draft_id=committed.import_id,
        reviewed_digest=committed.semantic_digest,
    )
    repository.save_import_draft(
        context,
        next_draft(committed, supersedes_import_id=committed.import_id),
    )
    assert repository.load_committed_import(context, receipt.graph_version) == (
        committed,
        receipt,
    )

    import_path = (COLLECTION, ORG, "imports", committed.import_id)
    client.data[import_path]["receipt"]["organization_id"] = (
        "org_01ARZ3NDEKTSV4RRFFQ69G5FAW"
    )
    client.data[import_path]["payload_digest"] = independent_digest(
        {
            key: value
            for key, value in client.data[import_path].items()
            if key != "payload_digest"
        }
    )
    with pytest.raises(ImportUnavailable, match="import_unavailable") as captured:
        repository.load_committed_import(context, receipt.graph_version)
    assert PRIVATE not in exception_graph_text(captured.value)


def test_firestore_graph_version_read_rejects_multiple_committed_records(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    first = repository.save_import_draft(context, draft())
    first_receipt = repository.commit_graph(
        context,
        draft_id=first.import_id,
        reviewed_digest=first.semantic_digest,
    )
    second = repository.save_import_draft(
        context,
        next_draft(first, supersedes_import_id=first.import_id),
    )
    second_receipt = ImportReceipt(
        receipt_id=f"rcp_{deterministic_ulid(ORG, second.import_id)}",
        import_id=second.import_id,
        organization_id=ORG,
        source_snapshot_id=second.source_snapshot.snapshot_id,
        source_snapshot_digest=second.source_snapshot.semantic_digest,
        graph_version=first_receipt.graph_version,
        committed_subject_count=len(second.candidate.subjects),
        committed_at=NOW,
        committed_by_uid=OWNER.uid,
    )
    second_path = (COLLECTION, ORG, "imports", second.import_id)
    client.data[second_path].update(
        {
            "status": "committed",
            "receipt": second_receipt.model_dump(mode="python"),
        }
    )
    client.data[second_path]["payload_digest"] = independent_digest(
        {
            key: value
            for key, value in client.data[second_path].items()
            if key != "payload_digest"
        }
    )

    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository.load_committed_import(context, first_receipt.graph_version)


def test_firestore_latest_import_lineage_rejects_superseded_commit(fake_firestore) -> None:
    client, repository, context = fake_firestore
    first = repository.save_import_draft(context, draft())
    second = first.model_copy(
        update={
            "import_id": "imp_01ARZ3NDEKTSV4RRFFQ69G5FAW",
            "semantic_digest": "2" * 64,
            "source_snapshot": first.source_snapshot.model_copy(
                update={"semantic_digest": "2" * 64}
            ),
        }
    )
    repository.save_import_draft(context, second)
    lineage_path = (COLLECTION, ORG, "organization_import_lineage", "csv")

    assert client.data[lineage_path]["latest_import_id"] == second.import_id
    with pytest.raises(ImportUnavailable, match="^import_lineage_conflict$"):
        repository.commit_graph(
            context,
            draft_id=first.import_id,
            reviewed_digest=first.semantic_digest,
        )


def test_firestore_current_draft_schema_is_v2(fake_firestore) -> None:
    client, repository, context = fake_firestore

    saved = repository.save_import_draft(context, draft())

    payload = client.data[(COLLECTION, ORG, "imports", saved.import_id)]
    assert payload["schema_version"] == 2
    assert "supersedes_import_id" in payload


def test_firestore_reads_and_lists_exact_legacy_v1_draft(fake_firestore) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    downgrade_to_legacy_v1(client, saved.import_id)

    assert repository.load_import_draft(context, saved.import_id) == saved
    assert repository.list_imports(context) == (saved,)


def test_firestore_new_source_supersedes_legacy_without_lineage(fake_firestore) -> None:
    client, repository, context = fake_firestore
    first = repository.save_import_draft(context, draft())
    downgrade_to_legacy_v1(client, first.import_id)
    second = next_draft(first, created_at=NOW + timedelta(seconds=1))

    repository.save_import_draft(context, second)

    lineage_path = (COLLECTION, ORG, "organization_import_lineage", "csv")
    assert client.data[lineage_path]["latest_import_id"] == second.import_id
    with pytest.raises(ImportUnavailable, match="^import_lineage_conflict$"):
        repository.commit_graph(
            context,
            draft_id=first.import_id,
            reviewed_digest=first.semantic_digest,
        )


def test_firestore_legacy_correction_uses_newest_import_id_tiebreak(fake_firestore) -> None:
    client, repository, context = fake_firestore
    first = repository.save_import_draft(context, draft())
    second = repository.save_import_draft(context, next_draft(first))
    downgrade_to_legacy_v1(client, first.import_id)
    downgrade_to_legacy_v1(client, second.import_id)
    correction = next_draft(
        second,
        import_id="imp_01ARZ3NDEKTSV4RRFFQ69G5FAX",
        digest="3" * 64,
        supersedes_import_id=second.import_id,
    )

    saved = repository.save_import_draft(context, correction)

    assert saved.supersedes_import_id == second.import_id
    lineage_path = (COLLECTION, ORG, "organization_import_lineage", "csv")
    assert client.data[lineage_path]["latest_import_id"] == correction.import_id


def test_firestore_legacy_commit_initializes_missing_lineage_atomically(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    downgrade_to_legacy_v1(client, saved.import_id)

    receipt = repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )

    lineage_path = (COLLECTION, ORG, "organization_import_lineage", "csv")
    assert receipt.import_id == saved.import_id
    assert client.data[lineage_path]["latest_import_id"] == saved.import_id


def test_firestore_aborted_legacy_commit_does_not_orphan_lineage(fake_firestore) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    downgrade_to_legacy_v1(client, saved.import_id)
    before = deepcopy(client.data)
    client.abort_next_transaction = True

    with pytest.raises(ImportUnavailable, match="^import_unavailable$"):
        repository.commit_graph(
            context,
            draft_id=saved.import_id,
            reviewed_digest=saved.semantic_digest,
        )

    assert client.data == before
    assert (COLLECTION, ORG, "organization_import_lineage", "csv") not in client.data


def test_firestore_exact_legacy_receipt_retry_needs_no_lineage(fake_firestore) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    receipt = repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    downgrade_to_legacy_v1(client, saved.import_id)

    assert repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    ) == receipt
    assert (COLLECTION, ORG, "organization_import_lineage", "csv") not in client.data


@pytest.mark.parametrize(
    ("schema_version", "remove_supersedes"),
    ((1, False), (2, True)),
)
def test_firestore_rejects_mixed_draft_schema_shapes(
    fake_firestore,
    schema_version,
    remove_supersedes,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    import_path = (COLLECTION, ORG, "imports", saved.import_id)
    payload = client.data[import_path]
    payload["schema_version"] = schema_version
    if remove_supersedes:
        payload.pop("supersedes_import_id")
    payload["payload_digest"] = independent_digest(
        {key: payload[key] for key in payload if key != "payload_digest"}
    )

    with pytest.raises(ImportUnavailable, match="^import_unavailable$"):
        repository.load_import_draft(context, saved.import_id)


@pytest.mark.parametrize("invalid_schema", [True, 1.0, "1", 9])
def test_firestore_draft_schema_requires_exact_builtin_integer(
    fake_firestore,
    invalid_schema,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    import_path = (COLLECTION, ORG, "imports", saved.import_id)
    payload = client.data[import_path]
    payload.pop("supersedes_import_id")
    payload["schema_version"] = invalid_schema
    payload["payload_digest"] = independent_digest(
        {key: payload[key] for key in payload if key != "payload_digest"}
    )

    with pytest.raises(ImportUnavailable, match="^import_unavailable$"):
        repository.load_import_draft(context, saved.import_id)


@pytest.mark.parametrize("invalid_schema", [True, 1.0, "1", 9])
def test_firestore_legacy_lineage_scan_rejects_nonexact_schema(
    fake_firestore,
    invalid_schema,
) -> None:
    client, repository, context = fake_firestore
    first = repository.save_import_draft(context, draft())
    downgrade_to_legacy_v1(client, first.import_id)
    import_path = (COLLECTION, ORG, "imports", first.import_id)
    payload = client.data[import_path]
    payload["schema_version"] = invalid_schema
    payload["payload_digest"] = independent_digest(
        {key: payload[key] for key in payload if key != "payload_digest"}
    )
    correction = next_draft(
        first,
        import_id="imp_01ARZ3NDEKTSV4RRFFQ69G5FAX",
        digest="3" * 64,
        supersedes_import_id=first.import_id,
    )
    before = deepcopy(client.data)

    with pytest.raises(ImportUnavailable, match="^import_unavailable$"):
        repository.save_import_draft(context, correction)

    assert client.data == before


def test_firestore_exact_committed_retry_precedes_newer_lineage(fake_firestore) -> None:
    _client, repository, context = fake_firestore
    first = repository.save_import_draft(context, draft())
    receipt = repository.commit_graph(
        context,
        draft_id=first.import_id,
        reviewed_digest=first.semantic_digest,
        acknowledged_codes=("leaderless_team",),
    )
    second = first.model_copy(
        update={
            "import_id": "imp_01ARZ3NDEKTSV4RRFFQ69G5FAW",
            "semantic_digest": "2" * 64,
            "base_graph_version": 1,
            "source_snapshot": first.source_snapshot.model_copy(
                update={"semantic_digest": "2" * 64}
            ),
        }
    )
    repository.save_import_draft(context, second)

    assert repository.commit_graph(
        context,
        draft_id=first.import_id,
        reviewed_digest=first.semantic_digest,
        acknowledged_codes=("leaderless_team",),
    ) == receipt
    with pytest.raises(ImportUnavailable, match="^import_unavailable$"):
        repository.commit_graph(
            context,
            draft_id=first.import_id,
            reviewed_digest=first.semantic_digest,
            acknowledged_codes=(),
        )
    with pytest.raises(ImportUnavailable, match="^import_unavailable$"):
        repository.commit_graph(
            context,
            draft_id=first.import_id,
            reviewed_digest="9" * 64,
            acknowledged_codes=("leaderless_team",),
        )


def test_firestore_correction_requires_exact_latest_lineage(fake_firestore) -> None:
    client, repository, context = fake_firestore
    first = repository.save_import_draft(context, draft())
    second = first.model_copy(
        update={
            "import_id": "imp_01ARZ3NDEKTSV4RRFFQ69G5FAW",
            "semantic_digest": "2" * 64,
            "source_snapshot": first.source_snapshot.model_copy(
                update={"semantic_digest": "2" * 64}
            ),
        }
    )
    repository.save_import_draft(context, second)
    correction = first.model_copy(
        update={
            "import_id": "imp_01ARZ3NDEKTSV4RRFFQ69G5FAX",
            "semantic_digest": "3" * 64,
            "supersedes_import_id": first.import_id,
            "source_snapshot": first.source_snapshot.model_copy(
                update={"semantic_digest": "3" * 64}
            ),
        }
    )
    before = deepcopy(client.data)

    with pytest.raises(ImportUnavailable, match="^import_lineage_conflict$"):
        repository.save_import_draft(context, correction)

    assert client.data == before


def test_firestore_lineage_and_draft_save_roll_back_together(fake_firestore) -> None:
    client, repository, context = fake_firestore
    first = repository.save_import_draft(context, draft())
    second = first.model_copy(
        update={
            "import_id": "imp_01ARZ3NDEKTSV4RRFFQ69G5FAW",
            "semantic_digest": "2" * 64,
            "source_snapshot": first.source_snapshot.model_copy(
                update={"semantic_digest": "2" * 64}
            ),
        }
    )
    before = deepcopy(client.data)
    client.abort_next_transaction = True

    with pytest.raises(ImportUnavailable, match="^import_unavailable$"):
        repository.save_import_draft(context, second)

    assert client.data == before


def test_firestore_corrupt_lineage_rejects_commit_without_writes(fake_firestore) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    lineage_path = (COLLECTION, ORG, "organization_import_lineage", "csv")
    client.data[lineage_path]["latest_import_id"] = (
        "imp_01ARZ3NDEKTSV4RRFFQ69G5FAW"
    )
    before = deepcopy(client.data)
    client.provider_mutation_attempts = 0

    with pytest.raises(ImportUnavailable, match="^import_unavailable$"):
        repository.commit_graph(
            context,
            draft_id=saved.import_id,
            reviewed_digest=saved.semantic_digest,
        )

    assert client.data == before
    assert client.provider_mutation_attempts == 0


def _numeric_ulid(value: int) -> str:
    return f"{value:026d}"


def large_draft() -> ImportDraft:
    records = tuple(
        SourceRecord(
            record_id=f"rec_{_numeric_ulid(index)}",
            source_ordinal=index,
            source_identity=f"directory/person-{index}",
            fields=(("name", f"Person {index} Ω"),),
        )
        for index in range(1, 5_001)
    )
    subjects = tuple(
        OrganizationSubject(
            subject_id=f"sub_{_numeric_ulid(index)}",
            organization_id=ORG,
            kind=OrganizationSubjectKind.HUMAN,
            lifecycle=SubjectLifecycle.DRAFT_IMPORTED,
            display_name=f"Person {index}",
            source_identity=f"directory/person-{index}",
        )
        for index in range(1, 5_001)
    )
    snapshot = SourceSnapshot(
        snapshot_id="snap_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        organization_id=ORG,
        source_kind="csv",
        captured_at=NOW,
        records=records,
        semantic_digest=DIGEST,
    )
    return ImportDraft(
        import_id="imp_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        organization_id=ORG,
        source_snapshot=snapshot,
        candidate=OrganizationGraphCandidate(
            organization_id=ORG,
            source_snapshot_id=snapshot.snapshot_id,
            subjects=subjects,
        ),
        base_graph_version=0,
        semantic_digest=DIGEST,
        created_at=NOW,
    )


def large_draft_with_edges() -> ImportDraft:
    value = large_draft()
    subjects = value.candidate.subjects
    edges = tuple(
        OrganizationEdge(
            edge_id=f"edge_{_numeric_ulid(index)}",
            organization_id=ORG,
            kind=OrganizationEdgeKind.COLLABORATES_WITH,
            source_subject_id=subjects[index - 1].subject_id,
            target_subject_id=subjects[index % len(subjects)].subject_id,
        )
        for index in range(1, 5_001)
    )
    return value.model_copy(
        update={"candidate": value.candidate.model_copy(update={"edges": edges})}
    )


def first_hundred_draft() -> ImportDraft:
    value = large_draft()
    snapshot = value.source_snapshot.model_copy(
        update={"records": value.source_snapshot.records[:100]}
    )
    candidate = value.candidate.model_copy(
        update={"subjects": value.candidate.subjects[:100]}
    )
    return value.model_copy(
        update={"source_snapshot": snapshot, "candidate": candidate}
    )


def test_firestore_subject_invitation_capacity_fails_before_any_write_attempt(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, large_draft_with_edges())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=SequenceFirestoreIdentifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    repository.initialize_subject_invitation_schema(decisionos, context)
    subject_ids = tuple(item.subject_id for item in saved.candidate.subjects[:100])
    before = deepcopy(client.data)
    before_attempts = client.provider_mutation_attempts

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        repository.create_subject_invitations(
            decisionos,
            context,
            subject_ids=subject_ids,
            role=DecisionOSRole.VIEWER,
            expires_in=timedelta(days=1),
            delivery_route_id=None,
        )

    assert client.data == before
    assert client.provider_mutation_attempts == before_attempts
    assert client.transactions[-1].write_count == 0


def test_firestore_subject_invitation_fitting_capacity_boundary_succeeds(
    fake_firestore,
) -> None:
    _client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, first_hundred_draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    decisionos = FirestoreDecisionOSRepository(
        _client,
        clock=lambda: NOW,
        identifiers=SequenceFirestoreIdentifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
    repository.initialize_subject_invitation_schema(decisionos, context)
    subject_ids = tuple(item.subject_id for item in saved.candidate.subjects)

    grants = repository.create_subject_invitations(
        decisionos,
        context,
        subject_ids=subject_ids,
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
        delivery_route_id=None,
    )

    assert len(grants) == 100
    assert repository.load_graph(context).version == 2


def test_firestore_chunks_five_thousand_record_draft_and_graph_within_bounds(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, large_draft())

    receipt = repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )

    assert receipt.committed_subject_count == 5_000
    assert repository.load_graph(context).version == 1
    assert len(repository.load_graph(context).subjects) == 5_000
    assert client.max_observed_document_bytes <= client.max_document_bytes
    assert all(
        transaction.write_count <= client.max_transaction_writes
        for transaction in client.transactions
    )


def test_firestore_preflights_actual_protobuf_size_before_provider_mutation(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    original = draft()
    adversarial_record = original.source_snapshot.records[0].model_copy(
        update={"fields": tuple((f"f{index}", "x") for index in range(21_930))}
    )
    adversarial = original.model_copy(
        update={
            "source_snapshot": original.source_snapshot.model_copy(
                update={"records": (adversarial_record,)}
            )
        }
    )
    record_payload = adversarial_record.model_dump(mode="python")
    chunk_payload = {
        "schema_version": 1,
        "organization_id": ORG,
        "owner_id": adversarial.import_id,
        "kind": "source_records",
        "index": 0,
        "count": 1,
        "digest": independent_digest((record_payload,)),
        "items": (record_payload,),
    }
    chunk_ref = FakeDocument(
        client,
        (COLLECTION, ORG, "imports", adversarial.import_id, "chunks", "source_records_00000"),
    )
    assert (
        actual_document_bytes(chunk_ref, chunk_payload, include_name=False)
        <= client.max_document_bytes
        < actual_document_bytes(chunk_ref, chunk_payload)
    )
    client.provider_mutation_attempts = 0

    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository.save_import_draft(context, adversarial)

    assert client.provider_mutation_attempts == 0
    assert not any(path[-1] == adversarial.import_id for path in client.data)


def test_firestore_preflights_oversized_committed_manifest_before_any_write(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    import_path = (COLLECTION, ORG, "imports", saved.import_id)
    payload = client.data[import_path]
    descriptors = list(payload["manifest"]["units"])
    empty_digest = independent_digest(())
    draft_ref = FakeDocument(client, import_path)
    index = 0

    def add_empty_unit_descriptor() -> None:
        nonlocal index
        descriptor = {
            "chunk_id": f"units_{index:05d}",
            "index": index,
            "count": 0,
            "digest": empty_digest,
        }
        descriptors.append(descriptor)
        client.data[(*import_path, "chunks", descriptor["chunk_id"])] = {
            "schema_version": 1,
            "organization_id": ORG,
            "owner_id": saved.import_id,
            "kind": "units",
            "index": index,
            "count": 0,
            "digest": empty_digest,
            "items": (),
        }
        payload["manifest"]["units"] = tuple(descriptors)
        index += 1

    while actual_document_bytes(draft_ref, payload) < 449_000:
        for _ in range(100):
            add_empty_unit_descriptor()
    while actual_document_bytes(draft_ref, payload) < 449_700:
        add_empty_unit_descriptor()
    payload["payload_digest"] = independent_digest(
        {key: payload[key] for key in payload if key != "payload_digest"}
    )
    assert actual_document_bytes(draft_ref, payload) <= client.max_document_bytes
    client.provider_mutation_attempts = 0

    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository.commit_graph(
            context,
            draft_id=saved.import_id,
            reviewed_digest=saved.semantic_digest,
        )

    assert client.provider_mutation_attempts == 0


def test_firestore_rejects_manifest_descriptor_index_even_with_valid_outer_digest(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    import_path = (COLLECTION, ORG, "imports", saved.import_id)
    payload = client.data[import_path]
    payload["manifest"]["source_records"][0]["index"] = 999
    payload["payload_digest"] = independent_digest(
        {key: payload[key] for key in payload if key != "payload_digest"}
    )
    before = deepcopy(client.data)

    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository.load_import_draft(context, saved.import_id)

    assert client.data == before


def test_firestore_rejects_receipt_count_even_with_valid_outer_digest(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    import_path = (COLLECTION, ORG, "imports", saved.import_id)
    payload = client.data[import_path]
    payload["receipt"]["committed_subject_count"] = 999
    payload["payload_digest"] = independent_digest(
        {key: payload[key] for key in payload if key != "payload_digest"}
    )
    before = deepcopy(client.data)

    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository.commit_graph(
            context,
            draft_id=saved.import_id,
            reviewed_digest=saved.semantic_digest,
        )

    assert client.data == before


def test_firestore_persists_acknowledgements_and_rejects_corrupt_retry(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    receipt = repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
        acknowledged_codes=("leaderless_team",),
    )
    assert receipt.acknowledged_codes == ("leaderless_team",)

    import_path = (COLLECTION, ORG, "imports", saved.import_id)
    payload = client.data[import_path]
    payload["receipt"]["acknowledged_codes"] = ()
    payload["payload_digest"] = independent_digest(
        {key: payload[key] for key in payload if key != "payload_digest"}
    )
    before = deepcopy(client.data)

    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository.commit_graph(
            context,
            draft_id=saved.import_id,
            reviewed_digest=saved.semantic_digest,
            acknowledged_codes=("leaderless_team",),
        )

    assert client.data == before


def test_firestore_rejects_state_version_digest_mismatch_before_advancing(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    first = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=first.import_id,
        reviewed_digest=first.semantic_digest,
    )
    second_source = first.source_snapshot.model_copy(update={"semantic_digest": "2" * 64})
    second = first.model_copy(
        update={
            "import_id": "imp_01ARZ3NDEKTSV4RRFFQ69G5FAW",
            "source_snapshot": second_source,
            "base_graph_version": 1,
            "semantic_digest": "2" * 64,
        }
    )
    saved_second = repository.save_import_draft(context, second)
    state_path = (COLLECTION, ORG, "organization_graph", "state")
    client.data[state_path]["payload_digest"] = "3" * 64
    before = deepcopy(client.data)

    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository.commit_graph(
            context,
            draft_id=saved_second.import_id,
            reviewed_digest=saved_second.semantic_digest,
        )

    assert client.data == before


def test_firestore_transaction_abort_rolls_back_and_retries_from_precommit(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    before = deepcopy(client.data)
    client.abort_next_transaction = True

    with pytest.raises(ImportUnavailable) as captured:
        repository.commit_graph(
            context,
            draft_id=saved.import_id,
            reviewed_digest=saved.semantic_digest,
        )

    assert PRIVATE not in exception_graph_text(captured.value)
    assert client.data == before
    receipt = repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    assert receipt.graph_version == 1
    assert repository.load_graph(context).version == 1


def test_firestore_rejects_cross_bound_membership_document(fake_firestore) -> None:
    client, repository, context = fake_firestore
    client.data[(COLLECTION, ORG, "members", OWNER.uid)] = OrganizationMembership(
        organization_id="org_01ARZ3NDEKTSV4RRFFQ69G5FAW",
        uid=OWNER.uid,
        role=DecisionOSRole.OWNER,
        status=MembershipStatus.ACTIVE,
    ).model_dump(mode="python")

    with pytest.raises(OrganizationUnavailable):
        repository.load_graph(context)


def test_firestore_rejects_unknown_draft_storage_metadata(fake_firestore) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    import_path = (COLLECTION, ORG, "imports", saved.import_id)
    client.data[import_path]["unexpected_private_metadata"] = PRIVATE

    with pytest.raises(ImportUnavailable) as captured:
        repository.load_import_draft(context, saved.import_id)

    assert PRIVATE not in exception_graph_text(captured.value)


def test_firestore_rejects_cross_bound_chunk_and_wrong_member_uid(fake_firestore) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    chunk_path = (
        COLLECTION,
        ORG,
        "imports",
        saved.import_id,
        "chunks",
        "source_records_00000",
    )
    client.data[chunk_path]["organization_id"] = "org_01ARZ3NDEKTSV4RRFFQ69G5FAW"

    with pytest.raises(ImportUnavailable):
        repository.load_import_draft(context, saved.import_id)

    client.data[(COLLECTION, ORG, "members", OWNER.uid)] = OrganizationMembership(
        organization_id=ORG,
        uid="firebase-other-owner",
        role=DecisionOSRole.OWNER,
        status=MembershipStatus.ACTIVE,
    ).model_dump(mode="python")
    with pytest.raises(OrganizationUnavailable):
        repository.load_graph(context)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("organization_id", "org_01ARZ3NDEKTSV4RRFFQ69G5FAW"),
        ("import_id", "imp_01ARZ3NDEKTSV4RRFFQ69G5FAW"),
        ("source_snapshot_digest", "2" * 64),
        ("receipt_id", "rcp_01ARZ3NDEKTSV4RRFFQ69G5FAW"),
    ),
)
def test_firestore_rejects_committed_receipt_not_bound_to_draft(
    fake_firestore,
    field,
    value,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    client.data[(COLLECTION, ORG, "imports", saved.import_id)]["receipt"][field] = value

    with pytest.raises(ImportUnavailable):
        repository.commit_graph(
            context,
            draft_id=saved.import_id,
            reviewed_digest=saved.semantic_digest,
        )


def _removal_draft(saved: ImportDraft, *, base_version: int) -> ImportDraft:
    snapshot = saved.source_snapshot.model_copy(
        update={"records": (), "semantic_digest": "2" * 64}
    )
    return saved.model_copy(
        update={
            "import_id": "imp_01ARZ3NDEKTSV4RRFFQ69G5FAW",
            "source_snapshot": snapshot,
            "candidate": OrganizationGraphCandidate(
                organization_id=ORG,
                source_snapshot_id=snapshot.snapshot_id,
            ),
            "base_graph_version": base_version,
            "semantic_digest": "2" * 64,
        }
    )


def test_firestore_source_removal_suspends_non_owner_membership_atomically(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    member_uid = "firebase-alice-01"
    client.data[(COLLECTION, ORG, "members", member_uid)] = OrganizationMembership(
        organization_id=ORG,
        uid=member_uid,
        role=DecisionOSRole.VIEWER,
        status=MembershipStatus.ACTIVE,
    ).model_dump(mode="python")
    saved = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    repository.bind_member(context, subject_id=SUBJECT, member_uid=member_uid)
    removal = repository.save_import_draft(context, _removal_draft(saved, base_version=2))

    receipt = repository.commit_graph(
        context,
        draft_id=removal.import_id,
        reviewed_digest=removal.semantic_digest,
    )

    assert receipt.graph_version == 3
    assert client.data[(COLLECTION, ORG, "members", member_uid)]["status"] == "suspended"
    assert repository.load_graph(context).subjects[0].lifecycle is SubjectLifecycle.SUSPENDED


def test_firestore_source_removal_preserves_last_owner_and_graph_version(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    saved = repository.save_import_draft(context, draft())
    repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    repository.bind_member(context, subject_id=SUBJECT, member_uid=OWNER.uid)
    removal = repository.save_import_draft(context, _removal_draft(saved, base_version=2))

    with pytest.raises(LastOwnerRequired):
        repository.commit_graph(
            context,
            draft_id=removal.import_id,
            reviewed_digest=removal.semantic_digest,
        )

    assert client.data[(COLLECTION, ORG, "members", OWNER.uid)]["status"] == "active"
    assert repository.load_graph(context).version == 2


def test_firestore_current_chunk_cleanup_and_duplicate_retry_are_deterministic(
    fake_firestore,
) -> None:
    client, repository, context = fake_firestore
    original = draft()
    unit = OrganizationUnit(
        unit_id="unit_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        organization_id=ORG,
        name="Operations",
    )
    with_unit = original.model_copy(
        update={
            "candidate": original.candidate.model_copy(update={"units": (unit,)})
        }
    )
    saved = repository.save_import_draft(context, with_unit)
    first = repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    assert repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    ) == first
    second = original.model_copy(
        update={
            "import_id": "imp_01ARZ3NDEKTSV4RRFFQ69G5FAW",
            "base_graph_version": 1,
            "semantic_digest": "2" * 64,
            "source_snapshot": original.source_snapshot.model_copy(
                update={"semantic_digest": "2" * 64}
            ),
        }
    )
    saved_second = repository.save_import_draft(context, second)
    repository.commit_graph(
        context,
        draft_id=saved_second.import_id,
        reviewed_digest=saved_second.semantic_digest,
    )

    assert (COLLECTION, ORG, "org_units", "chunk_00000") not in client.data


@pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="requires explicit Firestore emulator",
)
@pytest.mark.firestore_emulator
def test_firestore_emulator_roundtrips_generic_and_subject_invitation_timestamps() -> None:
    from google.cloud import firestore

    suffix = uuid4().hex
    collection = f"humanwire_test_organizations_{suffix}"
    audit_collection = f"humanwire_test_org_audit_{suffix}"
    client = firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT", "humanwire-test"))
    emulator_now = datetime.now(UTC)
    invitation_index_collection = f"humanwire_test_invites_{suffix}"
    identifiers = SequenceFirestoreIdentifiers()
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: emulator_now,
        identifiers=identifiers,
        organization_collection=collection,
        invitation_index_collection=invitation_index_collection,
    )
    repository = FirestoreOrganizationGraphRepository(
        client,
        clock=lambda: NOW,
        organization_collection=collection,
        audit_collection=audit_collection,
    )
    owner = DecisionOSPrincipal(
        uid="firebase-owner-01",
        email_verified=True,
        provider_ids=("google.com",),
    )
    organization = decisionos.create_organization(owner, "Northstar Labs")
    context = decisionos.load_context(owner, organization.organization_id)
    saved = repository.save_import_draft(context, draft())

    receipt = repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )

    assert receipt.graph_version == 1
    assert decisionos.list_organizations(owner) == (organization,)
    assert decisionos.load_context(owner, organization.organization_id) == context
    assert repository.load_graph(context).subjects[0].lifecycle is SubjectLifecycle.DIRECTORY_ONLY
    assert repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    ) == receipt
    assert repository.list_audit(context)[0].receipt == receipt
    current_invitee = DecisionOSPrincipal(
        uid="firebase-current-generic-01",
        email_verified=True,
        provider_ids=("google.com",),
    )
    current_invitation = decisionos.create_invitation(
        context,
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
    )
    current_row = (
        client.collection(collection)
        .document(ORG)
        .collection("invitations")
        .document(current_invitation.invitation_id)
        .get()
    )
    assert type(current_row.to_dict()["expires_at"]) is DatetimeWithNanoseconds
    decisionos.accept_invitation(
        current_invitee,
        current_invitation.token.get_secret_value(),
    )

    legacy_invitation_id = "inv_99999999999999999999999999"
    legacy_token = "historical-generic-invitation-token-01"
    legacy_digest = sha256(legacy_token.encode()).hexdigest()
    legacy_expires_at = emulator_now + timedelta(days=1)
    legacy_invitation_ref = (
        client.collection(collection)
        .document(ORG)
        .collection("invitations")
        .document(legacy_invitation_id)
    )
    legacy_index_ref = client.collection(invitation_index_collection).document(
        legacy_digest
    )

    @firestore.transactional
    def seed_frozen_legacy_generic(transaction):
        transaction.create(
            legacy_invitation_ref,
            {
                "invitation_id": legacy_invitation_id,
                "organization_id": ORG,
                "role": DecisionOSRole.CONTRIBUTOR.value,
                "expires_at": legacy_expires_at,
                "status": "active",
            },
        )
        transaction.create(
            legacy_index_ref,
            {
                "invitation_id": legacy_invitation_id,
                "organization_id": ORG,
                "status": "active",
            },
        )

    seed_frozen_legacy_generic(client.transaction())
    assert type(legacy_invitation_ref.get().to_dict()["expires_at"]) is (
        DatetimeWithNanoseconds
    )
    historical_invitee = DecisionOSPrincipal(
        uid="firebase-historical-generic-01",
        email_verified=True,
        provider_ids=("google.com",),
    )
    restarted_generic = FirestoreDecisionOSRepository(
        client,
        clock=lambda: emulator_now,
        identifiers=identifiers,
        organization_collection=collection,
        invitation_index_collection=invitation_index_collection,
    )
    repository.initialize_subject_invitation_schema(restarted_generic, context)
    assert restarted_generic.accept_invitation(
        historical_invitee,
        legacy_token,
    ).role is DecisionOSRole.CONTRIBUTOR

    pending = repository.create_subject_invitations(
        restarted_generic,
        context,
        subject_ids=(SUBJECT,),
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
        delivery_route_id="consented_test_route",
    )[0]
    sending = restarted_generic.begin_subject_invitation_delivery(context, pending)
    delivered = restarted_generic.record_subject_invitation_delivery(
        context,
        sending,
        delivered=True,
    )
    subject_token = pending.token.get_secret_value()
    assert delivered.delivery_status is SubjectInvitationDeliveryState.DELIVERED
    subject_invitee = DecisionOSPrincipal(
        uid="firebase-subject-invitee-01",
        email_verified=True,
        provider_ids=("google.com",),
    )
    restarted_decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: emulator_now,
        identifiers=identifiers,
        organization_collection=collection,
        invitation_index_collection=invitation_index_collection,
    )
    restarted_repository = FirestoreOrganizationGraphRepository(
        client,
        clock=lambda: NOW,
        organization_collection=collection,
        audit_collection=audit_collection,
    )
    membership, subject = restarted_repository.accept_subject_invitation(
        restarted_decisionos,
        subject_invitee,
        subject_token,
    )
    assert membership.uid == subject_invitee.uid
    assert subject.member_uid == subject_invitee.uid
    assert subject.lifecycle is SubjectLifecycle.ACTIVE

    removal = restarted_repository.save_import_draft(
        context,
        _removal_draft(saved, base_version=3),
    )
    restarted_repository.commit_graph(
        context,
        draft_id=removal.import_id,
        reviewed_digest=removal.semantic_digest,
    )
    with pytest.raises(OrganizationUnavailable):
        restarted_decisionos.load_context(subject_invitee, ORG)
