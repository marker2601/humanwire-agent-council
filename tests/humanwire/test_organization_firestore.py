from __future__ import annotations

import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

import pytest
from google.cloud.firestore_v1 import _helpers as firestore_helpers
from google.cloud.firestore_v1.types import Document as FirestoreDocument
from pydantic_core import to_json

from humanwire import organization_store
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
    def __init__(self, reference, data):
        self.reference = reference
        self.id = reference.id
        self._data = data
        self.exists = data is not None

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
            data = transaction._get(path) if transaction is not None else self.client.data.get(path)
            if data is None:
                continue
            if all(
                self._matches(self._field(data, field), operator, value)
                for field, operator, value in self.filters
            ):
                rows.append(FakeSnapshot(FakeDocument(self.client, path), data))
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
        return FakeSnapshot(self, data)


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
        encoded_size = actual_document_bytes(reference, data)
        if encoded_size > self.client.max_document_bytes:
            raise AssertionError(f"document exceeds bound: {encoded_size}")
        self.write_count += 1
        if self.write_count > self.client.max_transaction_writes:
            raise AssertionError(f"transaction exceeds write bound: {self.write_count}")
        self.client.max_observed_document_bytes = max(
            self.client.max_observed_document_bytes,
            encoded_size,
        )
        self.staged[reference.path] = deepcopy(data)

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
        for path, data in self.staged.items():
            if data is None:
                self.client.data.pop(path, None)
            else:
                self.client.data[path] = deepcopy(data)
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


def test_firestore_unknown_delivery_survives_restart_without_redelivery_grant(
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
    grant = repository.create_subject_invitations(
        decisionos,
        context,
        subject_ids=(SUBJECT,),
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
        delivery_route_id="consented_test_route",
    )[0]
    token = grant.token.get_secret_value()
    before_pending_retry = client.provider_mutation_attempts
    pending_retry = repository.create_subject_invitations(
        decisionos,
        context,
        subject_ids=(SUBJECT,),
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
        delivery_route_id="consented_test_route",
    )[0]
    assert pending_retry.invitation_id == grant.invitation_id
    assert pending_retry.token is None
    assert (
        pending_retry.delivery_status
        is SubjectInvitationDeliveryState.DELIVERY_PENDING
    )
    assert client.provider_mutation_attempts == before_pending_retry
    sending = decisionos.begin_subject_invitation_delivery(context, grant)
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

    assert retry.invitation_id == grant.invitation_id
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
        "missing_global_index",
        "extra_global_index_field",
        "boolean_invitation_schema",
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
        identifiers=Identifiers(),
        organization_collection=COLLECTION,
        invitation_index_collection="test_subject_invites",
    )
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
    elif corruption == "missing_global_index":
        del client.data[index_path]
    elif corruption == "extra_global_index_field":
        client.data[index_path]["private"] = PRIVATE
    elif corruption == "boolean_invitation_schema":
        client.data[invitation_path]["schema_version"] = True
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
    assert client.query_limits[-2:] == [2, 2]


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
def test_firestore_emulator_commits_one_tenant_bound_version_transactionally() -> None:
    from google.cloud import firestore

    suffix = uuid4().hex
    collection = f"humanwire_test_organizations_{suffix}"
    audit_collection = f"humanwire_test_org_audit_{suffix}"
    client = firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT", "humanwire-test"))
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=Identifiers(),
        organization_collection=collection,
        invitation_index_collection=f"humanwire_test_invites_{suffix}",
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
    invitee = DecisionOSPrincipal(
        uid="firebase-alice-01",
        email_verified=True,
        provider_ids=("google.com",),
    )
    invitation = decisionos.create_invitation(
        context,
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
    )
    decisionos.accept_invitation(invitee, invitation.token.get_secret_value())
    repository.bind_member(context, subject_id=SUBJECT, member_uid=invitee.uid)
    removal = repository.save_import_draft(context, _removal_draft(saved, base_version=2))
    repository.commit_graph(
        context,
        draft_id=removal.import_id,
        reviewed_digest=removal.semantic_digest,
    )
    with pytest.raises(OrganizationUnavailable):
        decisionos.load_context(invitee, ORG)
