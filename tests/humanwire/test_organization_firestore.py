from __future__ import annotations

import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from google.cloud.firestore_v1 import _helpers as firestore_helpers
from google.cloud.firestore_v1.types import Document as FirestoreDocument
from pydantic_core import to_json

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
    LastOwnerRequired,
    OrganizationUnavailable,
)
from humanwire.organization_models import (
    ImportDraft,
    ImportReceipt,
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
    def __init__(self, client, paths, filters=()):
        self.client = client
        self.paths = tuple(paths)
        self.filters = tuple(filters)

    def where(self, *, filter):
        return FakeQuery(
            self.client,
            self.paths,
            (*self.filters, (filter.field_path, filter.value)),
        )

    def order_by(self, _field):
        return self

    def stream(self, transaction=None):
        if self.client.failure is not None:
            raise RuntimeError(self.client.failure)
        rows = []
        for path in sorted(self.paths):
            data = transaction._get(path) if transaction is not None else self.client.data.get(path)
            if data is None:
                continue
            if all(self._field(data, field) == value for field, value in self.filters):
                rows.append(FakeSnapshot(FakeDocument(self.client, path), data))
        return tuple(rows)

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
