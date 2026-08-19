"""Tenant-bound persistence for reviewed organization graph imports."""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator
from pydantic_core import PydanticSerializationError, to_json

from humanwire.decisionos_models import (
    DecisionOSContext,
    DecisionOSPrincipal,
    DecisionOSRole,
    MembershipStatus,
    OrganizationMembership,
)
from humanwire.decisionos_store import (
    DecisionOSAuditEvent,
    DecisionOSPermission,
    DecisionOSRepository,
    DecisionOSStoreError,
    LastOwnerRequired,
    MembershipUnavailable,
    OrganizationUnavailable,
    require_permission,
)
from humanwire.organization_graph import validate_organization_graph
from humanwire.organization_models import (
    ImportDraft,
    ImportReceipt,
    ImportReconciliation,
    OrganizationGraph,
    OrganizationSubject,
    OrganizationSubjectKind,
    SubjectLifecycle,
)

_ULID = r"[0-9A-HJKMNP-TV-Z]{26}"
_IMPORT_ID = rf"^imp_{_ULID}$"
_SUBJECT_ID = rf"^sub_{_ULID}$"
_SHA256 = r"^[0-9a-f]{64}$"
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CHUNK_TARGET_BYTES = 350_000
_MAX_DOCUMENT_BYTES = 450_000
_MAX_TRANSACTION_WRITES = 450
_MAX_CHUNK_ITEMS = 200
_CHUNK_KINDS = (
    "source_records",
    "subjects",
    "units",
    "edges",
    "authority_assignments",
)
_DRAFT_STORAGE_FIELDS = frozenset(
    {
        "schema_version",
        "import_id",
        "organization_id",
        "source_snapshot",
        "candidate",
        "base_graph_version",
        "semantic_digest",
        "created_at",
        "status",
        "receipt",
        "manifest",
        "payload_digest",
    }
)
_GRAPH_STORAGE_FIELDS = frozenset(
    {
        "schema_version",
        "organization_id",
        "version",
        "created_at",
        "manifest",
        "payload_digest",
    }
)
_STATE_STORAGE_FIELDS = frozenset(
    {
        "schema_version",
        "organization_id",
        "current_version",
        "current_version_id",
        "payload_digest",
        "updated_at",
    }
)
_CHUNK_STORAGE_FIELDS = frozenset(
    {
        "schema_version",
        "organization_id",
        "owner_id",
        "kind",
        "index",
        "count",
        "digest",
        "items",
    }
)


class OrganizationStoreError(RuntimeError):
    """Fixed, content-free organization repository failure."""


class ImportUnavailable(OrganizationStoreError):
    def __init__(self) -> None:
        super().__init__("import_unavailable")


class GraphVersionConflict(ImportUnavailable):
    def __init__(self) -> None:
        OrganizationStoreError.__init__(self, "graph_version_conflict")


class OrganizationGraphInvalid(ImportUnavailable):
    def __init__(self) -> None:
        OrganizationStoreError.__init__(self, "organization_graph_invalid")


class OrganizationGraphAuditEvent(BaseModel):
    """Immutable, content-free attribution for a graph version mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    event_id: str
    organization_id: str
    event_name: Literal["organization_graph_committed", "organization_member_bound"]
    actor_uid: str
    prior_graph_version: int
    new_graph_version: int
    source_snapshot_digest: str | None = None
    receipt: ImportReceipt | None = None
    subject_id: str | None = None
    member_uid: str | None = None
    occurred_at: datetime

    @model_validator(mode="after")
    def has_event_specific_attribution(self) -> Self:
        if self.event_name == "organization_graph_committed":
            if (
                self.source_snapshot_digest is None
                or self.receipt is None
                or self.subject_id is not None
                or self.member_uid is not None
            ):
                raise ValueError("commit audit attribution is incomplete")
        elif (
            self.source_snapshot_digest is not None
            or self.receipt is not None
            or self.subject_id is None
            or self.member_uid is None
        ):
            raise ValueError("binding audit attribution is incomplete")
        if self.new_graph_version != self.prior_graph_version + 1:
            raise ValueError("audit graph versions must be consecutive")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("audit timestamp must be timezone-aware")
        return self


class OrganizationGraphRepository(Protocol):
    def save_import_draft(
        self,
        context: DecisionOSContext,
        draft: ImportDraft,
    ) -> ImportDraft: ...

    def commit_graph(
        self,
        context: DecisionOSContext,
        *,
        draft_id: str,
        reviewed_digest: str,
    ) -> ImportReceipt: ...

    def load_graph(self, context: DecisionOSContext) -> OrganizationGraph: ...

    def load_import_draft(
        self,
        context: DecisionOSContext,
        draft_id: str,
    ) -> ImportDraft: ...

    def list_imports(self, context: DecisionOSContext) -> tuple[ImportDraft, ...]: ...

    def reconcile_import(
        self,
        context: DecisionOSContext,
        draft_id: str,
    ) -> ImportReconciliation: ...

    def bind_member(
        self,
        context: DecisionOSContext,
        *,
        subject_id: str,
        member_uid: str,
    ) -> OrganizationSubject: ...


class _SavedImport:
    __slots__ = ("draft", "receipt")

    def __init__(self, draft: ImportDraft) -> None:
        self.draft = draft
        self.receipt: ImportReceipt | None = None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


def _deterministic_ulid(*parts: str) -> str:
    value = int.from_bytes(
        hashlib.sha256("\0".join(parts).encode("utf-8")).digest()[:16],
        "big",
    )
    return "".join(
        _ULID_ALPHABET[(value >> (5 * index)) & 31]
        for index in range(25, -1, -1)
    )


def _receipt_for(
    draft: ImportDraft,
    *,
    graph_version: int,
    actor_uid: str,
    committed_subject_count: int,
    committed_at: datetime,
) -> ImportReceipt:
    return ImportReceipt(
        receipt_id=f"rcp_{_deterministic_ulid(draft.organization_id, draft.import_id)}",
        import_id=draft.import_id,
        organization_id=draft.organization_id,
        source_snapshot_id=draft.source_snapshot.snapshot_id,
        source_snapshot_digest=draft.source_snapshot.semantic_digest,
        graph_version=graph_version,
        committed_subject_count=committed_subject_count,
        committed_at=committed_at,
        committed_by_uid=actor_uid,
    )


def _committed_graph(
    draft: ImportDraft,
    prior: OrganizationGraph,
    *,
    version: int,
    created_at: datetime,
) -> tuple[OrganizationGraph, tuple[str, ...], tuple[str, ...]]:
    prior_by_subject_id = {item.subject_id: item for item in prior.subjects}
    prior_by_source = {
        item.source_identity: item
        for item in prior.subjects
        if item.source_identity is not None
    }
    committed: dict[str, OrganizationSubject] = {}
    present_sources: set[str] = set()
    carried_member_uids: set[str] = set()
    removed_member_uids: set[str] = set()
    for candidate in draft.candidate.subjects:
        if candidate.member_uid is not None or (
            candidate.kind is OrganizationSubjectKind.HUMAN
            and candidate.lifecycle is SubjectLifecycle.ACTIVE
        ):
            raise OrganizationGraphInvalid()
        if candidate.source_identity is not None:
            present_sources.add(candidate.source_identity)
        previous = prior_by_subject_id.get(candidate.subject_id)
        if previous is None and candidate.source_identity is not None:
            previous = prior_by_source.get(candidate.source_identity)
        if previous is not None and previous.member_uid is not None:
            carried_member_uids.add(previous.member_uid)
            candidate = _validated_subject(
                candidate,
                {
                    "lifecycle": SubjectLifecycle.ACTIVE,
                    "member_uid": previous.member_uid,
                },
            )
        elif candidate.lifecycle is SubjectLifecycle.DRAFT_IMPORTED:
            candidate = _validated_subject(
                candidate,
                {"lifecycle": SubjectLifecycle.DIRECTORY_ONLY},
            )
        committed[candidate.subject_id] = candidate
    for previous in prior.subjects:
        if (
            previous.source_identity is not None
            and previous.source_identity not in present_sources
            and previous.subject_id not in committed
        ):
            if previous.member_uid is not None:
                removed_member_uids.add(previous.member_uid)
            committed[previous.subject_id] = _validated_subject(
                previous,
                {"lifecycle": SubjectLifecycle.SUSPENDED},
            )
    graph = OrganizationGraph(
        organization_id=draft.organization_id,
        version=version,
        subjects=tuple(sorted(committed.values(), key=lambda item: item.subject_id)),
        units=tuple(sorted(draft.candidate.units, key=lambda item: item.unit_id)),
        edges=tuple(sorted(draft.candidate.edges, key=lambda item: item.edge_id)),
        authority_assignments=tuple(
            sorted(draft.candidate.authority_assignments, key=lambda item: item.assignment_id)
        ),
        created_at=created_at,
    )
    if not validate_organization_graph(graph).committable:
        raise OrganizationGraphInvalid()
    return (
        graph,
        tuple(sorted(carried_member_uids)),
        tuple(sorted(removed_member_uids)),
    )


def _validated_subject(
    subject: OrganizationSubject,
    updates: dict[str, Any],
) -> OrganizationSubject:
    payload = subject.model_dump(mode="python")
    payload.update(updates)
    try:
        return OrganizationSubject.model_validate(payload)
    except ValidationError:
        raise OrganizationGraphInvalid() from None


def _validated_draft(draft: ImportDraft) -> ImportDraft:
    try:
        return ImportDraft.model_validate_json(to_json(draft.model_dump(mode="python")))
    except (PydanticSerializationError, ValidationError):
        raise ImportUnavailable() from None


def _reconciliation(draft: ImportDraft) -> ImportReconciliation:
    graph = OrganizationGraph(
        organization_id=draft.organization_id,
        version=draft.base_graph_version + 1,
        subjects=draft.candidate.subjects,
        units=draft.candidate.units,
        edges=draft.candidate.edges,
        authority_assignments=draft.candidate.authority_assignments,
        created_at=draft.created_at,
    )
    validation = validate_organization_graph(graph)
    counts = Counter(subject.lifecycle for subject in draft.candidate.subjects)
    return ImportReconciliation(
        import_id=draft.import_id,
        organization_id=draft.organization_id,
        source_count=len(draft.source_snapshot.records),
        normalized_count=len(draft.candidate.subjects),
        rejected_count=len(draft.source_snapshot.records) - len(draft.candidate.subjects),
        lifecycle_counts=tuple(sorted(counts.items(), key=lambda item: item[0].value)),
        blocking_codes=validation.blocking_codes,
    )


class InMemoryOrganizationGraphRepository:
    """Single-lock semantic reference with fresh DecisionOS authorization."""

    def __init__(
        self,
        *,
        decisionos: DecisionOSRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._decisionos = decisionos
        self._clock = (lambda: datetime.now(UTC)) if clock is None else clock
        self._lock = threading.RLock()
        self._current_versions: dict[str, int] = {}
        self._graphs: dict[tuple[str, int], OrganizationGraph] = {}
        self._imports: dict[tuple[str, str], _SavedImport] = {}
        self._audit: dict[str, list[OrganizationGraphAuditEvent]] = {}

    def __repr__(self) -> str:
        return "InMemoryOrganizationGraphRepository()"

    def load_context(
        self,
        principal: DecisionOSPrincipal,
        organization_id: str,
    ) -> DecisionOSContext:
        return self._decisionos.load_context(principal, organization_id)

    def save_import_draft(
        self,
        context: DecisionOSContext,
        draft: ImportDraft,
    ) -> ImportDraft:
        with self._lock:
            current = self._manage(context)
            draft = _validated_draft(draft)
            if draft.organization_id != current.organization_id:
                raise ImportUnavailable()
            key = (current.organization_id, draft.import_id)
            if key in self._imports:
                raise ImportUnavailable()
            self._imports[key] = _SavedImport(draft)
            return draft

    def load_import_draft(
        self,
        context: DecisionOSContext,
        draft_id: str,
    ) -> ImportDraft:
        with self._lock:
            current = self._manage(context)
            saved = self._imports.get((current.organization_id, draft_id))
            if saved is None:
                raise ImportUnavailable()
            return saved.draft

    def list_imports(self, context: DecisionOSContext) -> tuple[ImportDraft, ...]:
        with self._lock:
            current = self._manage(context)
            return tuple(
                saved.draft
                for (organization_id, _draft_id), saved in sorted(self._imports.items())
                if organization_id == current.organization_id
            )

    def reconcile_import(
        self,
        context: DecisionOSContext,
        draft_id: str,
    ) -> ImportReconciliation:
        return _reconciliation(self.load_import_draft(context, draft_id))

    def commit_graph(
        self,
        context: DecisionOSContext,
        *,
        draft_id: str,
        reviewed_digest: str,
    ) -> ImportReceipt:
        with self._lock:
            current = self._manage(context)
            saved = self._imports.get((current.organization_id, draft_id))
            if saved is None or not _digest_matches(reviewed_digest, saved.draft.semantic_digest):
                raise ImportUnavailable()
            if saved.receipt is not None:
                return saved.receipt
            prior = self._graph(current.organization_id)
            if saved.draft.base_graph_version != prior.version:
                raise GraphVersionConflict()
            now = _aware(self._clock())
            graph, carried_member_uids, removed_member_uids = _committed_graph(
                saved.draft,
                prior,
                version=prior.version + 1,
                created_at=now,
            )
            receipt = _receipt_for(
                saved.draft,
                graph_version=graph.version,
                actor_uid=current.principal.uid,
                committed_subject_count=len(saved.draft.candidate.subjects),
                committed_at=now,
            )
            event = _commit_event(current, prior.version, receipt, now)

            prepared = None
            preparation_failed = False
            try:
                replacement_graphs = dict(self._graphs)
                replacement_graphs[(current.organization_id, graph.version)] = graph
                replacement_versions = dict(self._current_versions)
                replacement_versions[current.organization_id] = graph.version
                replacement_saved = _SavedImport(saved.draft)
                replacement_saved.receipt = receipt
                replacement_imports = dict(self._imports)
                replacement_imports[(current.organization_id, draft_id)] = replacement_saved
                replacement_audit = {
                    organization_id: list(events)
                    for organization_id, events in self._audit.items()
                }
                replacement_audit[current.organization_id] = [
                    *replacement_audit.get(current.organization_id, ()),
                    event,
                ]
                prepared = (
                    replacement_graphs,
                    replacement_versions,
                    replacement_imports,
                    replacement_audit,
                )
            except Exception:  # noqa: BLE001 - injected container failures are sealed
                preparation_failed = True
            if preparation_failed or prepared is None:
                raise ImportUnavailable() from None
            prior_state = (
                self._graphs,
                self._current_versions,
                self._imports,
                self._audit,
            )

            def persist(_transaction) -> Callable[[], None]:
                def rollback() -> None:
                    self._graphs, self._current_versions, self._imports, self._audit = prior_state

                try:
                    self._graphs, self._current_versions, self._imports, self._audit = prepared
                except Exception:
                    rollback()
                    raise
                return rollback

            membership_invalid = False
            provider_failed = False
            try:
                self._decisionos.apply_organization_graph_membership_change(
                    current,
                    carried_member_uids=carried_member_uids,
                    removed_member_uids=removed_member_uids,
                    mutation=persist,
                )
            except MembershipUnavailable:
                membership_invalid = True
            except DecisionOSStoreError:
                raise
            except Exception:  # noqa: BLE001 - injected/provider failures are sealed
                provider_failed = True
            if membership_invalid:
                raise OrganizationGraphInvalid() from None
            if provider_failed:
                raise ImportUnavailable() from None
            return receipt

    def load_graph(self, context: DecisionOSContext) -> OrganizationGraph:
        with self._lock:
            current = self._read(context)
            return self._graph(current.organization_id)

    def bind_member(
        self,
        context: DecisionOSContext,
        *,
        subject_id: str,
        member_uid: str,
    ) -> OrganizationSubject:
        with self._lock:
            current = self._manage(context)
            member_context = self._decisionos.load_context(
                DecisionOSPrincipal(uid=member_uid, email_verified=True),
                current.organization_id,
            )
            if member_context.membership.status is not MembershipStatus.ACTIVE:
                raise OrganizationUnavailable()
            prior = self._graph(current.organization_id)
            subject = next(
                (item for item in prior.subjects if item.subject_id == subject_id),
                None,
            )
            if subject is None or subject.kind is not OrganizationSubjectKind.HUMAN:
                raise OrganizationUnavailable()
            if subject.lifecycle is SubjectLifecycle.SUSPENDED:
                raise OrganizationUnavailable()
            if subject.member_uid == member_uid:
                return subject
            if subject.member_uid is not None or any(
                item.member_uid == member_uid for item in prior.subjects
            ):
                raise OrganizationUnavailable()
            bound = _validated_subject(
                subject,
                {"member_uid": member_uid, "lifecycle": SubjectLifecycle.ACTIVE},
            )
            now = _aware(self._clock())
            graph = OrganizationGraph(
                organization_id=prior.organization_id,
                version=prior.version + 1,
                subjects=tuple(
                    bound if item.subject_id == subject_id else item
                    for item in prior.subjects
                ),
                units=prior.units,
                edges=prior.edges,
                authority_assignments=prior.authority_assignments,
                created_at=now,
            )
            event = _binding_event(current, prior.version, bound, now)
            self._graphs[(current.organization_id, graph.version)] = graph
            self._current_versions[current.organization_id] = graph.version
            self._audit.setdefault(current.organization_id, []).append(event)
            return bound

    def list_audit(
        self,
        context: DecisionOSContext,
    ) -> tuple[OrganizationGraphAuditEvent, ...]:
        with self._lock:
            current = self._read(context)
            return tuple(self._audit.get(current.organization_id, ()))

    def _graph(self, organization_id: str) -> OrganizationGraph:
        version = self._current_versions.get(organization_id, 0)
        graph = self._graphs.get((organization_id, version))
        if graph is not None:
            return graph
        return OrganizationGraph(
            organization_id=organization_id,
            version=0,
            created_at=_aware(self._clock()),
        )

    def _manage(self, context: DecisionOSContext) -> DecisionOSContext:
        return self._decisionos.authorize_context(
            context,
            DecisionOSPermission.MANAGE_MEMBERS,
        )

    def _read(self, context: DecisionOSContext) -> DecisionOSContext:
        return self._decisionos.authorize_context(
            context,
            DecisionOSPermission.READ_WORKSPACE,
        )


def _digest_matches(candidate: str, expected: str) -> bool:
    return (
        type(candidate) is str
        and re.fullmatch(_SHA256, candidate) is not None
        and secrets.compare_digest(candidate, expected)
    )


def _commit_event(
    context: DecisionOSContext,
    prior_version: int,
    receipt: ImportReceipt,
    occurred_at: datetime,
) -> OrganizationGraphAuditEvent:
    return OrganizationGraphAuditEvent(
        event_id=f"org_audit_{context.organization_id}_{receipt.graph_version:020d}",
        organization_id=context.organization_id,
        event_name="organization_graph_committed",
        actor_uid=context.principal.uid,
        prior_graph_version=prior_version,
        new_graph_version=receipt.graph_version,
        source_snapshot_digest=receipt.source_snapshot_digest,
        receipt=receipt,
        occurred_at=occurred_at,
    )


def _binding_event(
    context: DecisionOSContext,
    prior_version: int,
    subject: OrganizationSubject,
    occurred_at: datetime,
) -> OrganizationGraphAuditEvent:
    return OrganizationGraphAuditEvent(
        event_id=f"org_audit_{context.organization_id}_{prior_version + 1:020d}",
        organization_id=context.organization_id,
        event_name="organization_member_bound",
        actor_uid=context.principal.uid,
        prior_graph_version=prior_version,
        new_graph_version=prior_version + 1,
        subject_id=subject.subject_id,
        member_uid=subject.member_uid,
        occurred_at=occurred_at,
    )


def _collection_name(value: str) -> str:
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", value) is None:
        raise ValueError("organization collection name is invalid")
    return value


def _model_from_snapshot(model_type, snapshot):
    if not snapshot.exists:
        return None
    result = None
    try:
        result = model_type.model_validate_json(to_json(snapshot.to_dict()))
    except (
        KeyError,
        PydanticSerializationError,
        TypeError,
        ValueError,
        ValidationError,
    ):
        pass
    if result is None:
        raise OrganizationUnavailable() from None
    return result


_MISSING = object()


def _firestore_error_barrier(error_type):
    """Translate unknown provider failures after their exception context has cleared."""

    def decorate(method):
        @wraps(method)
        def guarded(*args, **kwargs):
            result = _MISSING
            failed = False
            try:
                result = method(*args, **kwargs)
            except (OrganizationStoreError, DecisionOSStoreError):
                raise
            except Exception:  # noqa: BLE001 - provider SDK failures are intentionally sealed
                failed = True
            if failed:
                raise error_type() from None
            return result

        return guarded

    return decorate


def _payload_digest(value: Any) -> str:
    return hashlib.sha256(to_json(value)).hexdigest()


def _firestore_document_bytes(reference: Any, value: dict[str, Any]) -> int:
    failed = False
    size = 0
    try:
        from google.cloud.firestore_v1 import _helpers
        from google.cloud.firestore_v1.types import Document

        name = reference._document_path
        if type(name) is not str or not name:
            raise ValueError("document reference has no resource name")
        size = Document(name=name, fields=_helpers.encode_dict(value))._pb.ByteSize()
    except Exception:  # noqa: BLE001 - serializer failures are fixed-safe
        failed = True
    if failed:
        raise ImportUnavailable() from None
    return size


def _require_document_bound(reference: Any, value: dict[str, Any]) -> None:
    if _firestore_document_bytes(reference, value) > _MAX_DOCUMENT_BYTES:
        raise ImportUnavailable()


def _bounded_chunks(
    *,
    organization_id: str,
    owner_id: str,
    kind: str,
    items: tuple[dict[str, Any], ...],
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    chunks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_bytes = 2
    for item in items:
        item_bytes = len(to_json(item)) + (1 if current else 0)
        if item_bytes + 256 > _CHUNK_TARGET_BYTES:
            raise ImportUnavailable()
        if current and (
            len(current) >= _MAX_CHUNK_ITEMS
            or current_bytes + item_bytes > _CHUNK_TARGET_BYTES
        ):
            chunks.append(
                _chunk_document(organization_id, owner_id, kind, len(chunks), current)
            )
            current = []
            current_bytes = 2
        current.append(item)
        current_bytes += item_bytes
    if current:
        chunks.append(_chunk_document(organization_id, owner_id, kind, len(chunks), current))
    manifest = tuple(
        {
            "chunk_id": f"{kind}_{chunk['index']:05d}",
            "index": chunk["index"],
            "count": chunk["count"],
            "digest": chunk["digest"],
        }
        for chunk in chunks
    )
    return tuple(chunks), manifest


def _chunk_document(
    organization_id: str,
    owner_id: str,
    kind: str,
    index: int,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    document = {
        "schema_version": 1,
        "organization_id": organization_id,
        "owner_id": owner_id,
        "kind": kind,
        "index": index,
        "count": len(items),
        "digest": _payload_digest(items),
        "items": tuple(items),
    }
    return document


def _chunked_draft(draft: ImportDraft) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    payload = draft.model_dump(mode="python")
    source_records = tuple(payload["source_snapshot"].pop("records"))
    candidate = payload["candidate"]
    values = {
        "source_records": source_records,
        "subjects": tuple(candidate.pop("subjects")),
        "units": tuple(candidate.pop("units")),
        "edges": tuple(candidate.pop("edges")),
        "authority_assignments": tuple(candidate.pop("authority_assignments")),
    }
    chunks: dict[str, dict[str, Any]] = {}
    manifest = {}
    for kind in _CHUNK_KINDS:
        kind_chunks, kind_manifest = _bounded_chunks(
            organization_id=draft.organization_id,
            owner_id=draft.import_id,
            kind=kind,
            items=values[kind],
        )
        manifest[kind] = kind_manifest
        chunks.update(
            {f"{kind}_{chunk['index']:05d}": chunk for chunk in kind_chunks}
        )
    storage = {
        "schema_version": 1,
        **payload,
        "status": "draft",
        "receipt": None,
        "manifest": manifest,
    }
    storage["payload_digest"] = _payload_digest(
        {key: storage[key] for key in storage if key != "payload_digest"}
    )
    return storage, chunks


def _chunked_graph(graph: OrganizationGraph) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    payload = graph.model_dump(mode="python")
    values = {
        "subjects": tuple(payload.pop("subjects")),
        "units": tuple(payload.pop("units")),
        "edges": tuple(payload.pop("edges")),
        "authority_assignments": tuple(payload.pop("authority_assignments")),
    }
    chunks: dict[str, dict[str, Any]] = {}
    manifest = {"source_records": ()}
    for kind in _CHUNK_KINDS[1:]:
        kind_chunks, kind_manifest = _bounded_chunks(
            organization_id=graph.organization_id,
            owner_id=f"{graph.version:020d}",
            kind=kind,
            items=values[kind],
        )
        manifest[kind] = kind_manifest
        chunks.update(
            {f"{kind}_{chunk['index']:05d}": chunk for chunk in kind_chunks}
        )
    storage = {"schema_version": 1, **payload, "manifest": manifest}
    storage["payload_digest"] = _payload_digest(
        {key: storage[key] for key in storage if key != "payload_digest"}
    )
    return storage, chunks


def _require_write_bound(write_count: int) -> None:
    if write_count > _MAX_TRANSACTION_WRITES:
        raise ImportUnavailable()


class FirestoreOrganizationGraphRepository:
    """Firestore parity using tenant-scoped paths and serialized graph commits."""

    def __init__(
        self,
        client: Any,
        *,
        clock: Callable[[], datetime] | None = None,
        organization_collection: str = "organizations",
        audit_collection: str = "humanwire_audit",
    ) -> None:
        self._client = client
        self._clock = (lambda: datetime.now(UTC)) if clock is None else clock
        self._organization_collection = _collection_name(organization_collection)
        self._audit_collection = _collection_name(audit_collection)

    def __repr__(self) -> str:
        return "FirestoreOrganizationGraphRepository()"

    def _organization_ref(self, organization_id: str):
        if re.fullmatch(rf"^org_{_ULID}$", organization_id) is None:
            raise OrganizationUnavailable()
        return self._client.collection(self._organization_collection).document(organization_id)

    def _import_ref(self, organization_id: str, draft_id: str):
        if re.fullmatch(_IMPORT_ID, draft_id) is None:
            raise ImportUnavailable()
        return self._organization_ref(organization_id).collection("imports").document(draft_id)

    def _version_ref(self, organization_id: str, version: int):
        return (
            self._organization_ref(organization_id)
            .collection("organization_graph_versions")
            .document(f"{version:020d}")
        )

    def _state_ref(self, organization_id: str):
        return (
            self._organization_ref(organization_id)
            .collection("organization_graph")
            .document("state")
        )

    @_firestore_error_barrier(OrganizationUnavailable)
    def load_context(
        self,
        principal: DecisionOSPrincipal,
        organization_id: str,
    ) -> DecisionOSContext:
        return self._load_context(principal, organization_id)

    def _load_context(
        self,
        principal: DecisionOSPrincipal,
        organization_id: str,
    ) -> DecisionOSContext:
        organization = self._organization_ref(organization_id).get()
        member = _model_from_snapshot(
            OrganizationMembership,
            self._organization_ref(organization_id)
            .collection("members")
            .document(principal.uid)
            .get(),
        )
        if (
            not organization.exists
            or member is None
            or member.status is not MembershipStatus.ACTIVE
            or member.organization_id != organization_id
            or member.uid != principal.uid
        ):
            raise OrganizationUnavailable()
        return DecisionOSContext(principal=principal, membership=member)

    @_firestore_error_barrier(ImportUnavailable)
    def save_import_draft(
        self,
        context: DecisionOSContext,
        draft: ImportDraft,
    ) -> ImportDraft:
        from google.cloud import firestore

        if draft.organization_id != context.organization_id:
            raise ImportUnavailable()
        draft = _validated_draft(draft)
        payload, chunks = _chunked_draft(draft)
        draft_ref = self._import_ref(context.organization_id, draft.import_id)
        _require_document_bound(draft_ref, payload)
        for chunk_id, chunk in chunks.items():
            _require_document_bound(
                draft_ref.collection("chunks").document(chunk_id),
                chunk,
            )
        _require_write_bound(1 + len(chunks))

        @firestore.transactional
        def save(transaction):
            current = self._authorize_transaction(
                transaction,
                context,
                DecisionOSPermission.MANAGE_MEMBERS,
            )
            if draft.organization_id != current.organization_id or draft_ref.get(
                transaction=transaction
            ).exists:
                raise ImportUnavailable()
            transaction.create(draft_ref, payload)
            for chunk_id, chunk in chunks.items():
                transaction.create(draft_ref.collection("chunks").document(chunk_id), chunk)

        save(self._client.transaction())
        return draft

    @_firestore_error_barrier(ImportUnavailable)
    def load_import_draft(
        self,
        context: DecisionOSContext,
        draft_id: str,
    ) -> ImportDraft:
        current = self._authorize(context, DecisionOSPermission.MANAGE_MEMBERS)
        draft_ref = self._import_ref(current.organization_id, draft_id)
        row = draft_ref.get()
        if not row.exists:
            raise ImportUnavailable()
        return self._draft_from_row(
            row,
            draft_ref,
            organization_id=current.organization_id,
        )

    @_firestore_error_barrier(ImportUnavailable)
    def list_imports(self, context: DecisionOSContext) -> tuple[ImportDraft, ...]:
        current = self._authorize(context, DecisionOSPermission.MANAGE_MEMBERS)
        rows = self._organization_ref(current.organization_id).collection("imports").stream()
        return tuple(
            sorted(
                (
                    self._draft_from_row(
                        row,
                        self._import_ref(current.organization_id, row.id),
                        organization_id=current.organization_id,
                    )
                    for row in rows
                ),
                key=lambda item: item.import_id,
            )
        )

    @_firestore_error_barrier(ImportUnavailable)
    def reconcile_import(
        self,
        context: DecisionOSContext,
        draft_id: str,
    ) -> ImportReconciliation:
        return _reconciliation(self.load_import_draft(context, draft_id))

    @_firestore_error_barrier(ImportUnavailable)
    def commit_graph(
        self,
        context: DecisionOSContext,
        *,
        draft_id: str,
        reviewed_digest: str,
    ) -> ImportReceipt:
        from google.cloud import firestore

        draft_ref = self._import_ref(context.organization_id, draft_id)
        organization_ref = self._organization_ref(context.organization_id)
        state_ref = self._state_ref(context.organization_id)

        @firestore.transactional
        def commit(transaction):
            current = self._authorize_transaction(
                transaction,
                context,
                DecisionOSPermission.MANAGE_MEMBERS,
            )
            draft_row = draft_ref.get(transaction=transaction)
            if not draft_row.exists:
                raise ImportUnavailable()
            payload = draft_row.to_dict()
            draft = self._draft_from_row(
                draft_row,
                draft_ref,
                organization_id=current.organization_id,
                transaction=transaction,
            )
            if (
                draft.organization_id != current.organization_id
                or not _digest_matches(reviewed_digest, draft.semantic_digest)
            ):
                raise ImportUnavailable()
            if payload.get("status") == "committed":
                receipt = None
                try:
                    receipt = ImportReceipt.model_validate_json(to_json(payload["receipt"]))
                except (
                    KeyError,
                    PydanticSerializationError,
                    TypeError,
                    ValueError,
                    ValidationError,
                ):
                    pass
                if receipt is None or not self._receipt_matches_draft(receipt, draft):
                    raise ImportUnavailable() from None
                return receipt
            if payload.get("status") != "draft":
                raise ImportUnavailable()
            state_row = state_ref.get(transaction=transaction)
            current_version, _state = self._state_from_row(
                current.organization_id,
                state_row,
            )
            prior_row = (
                self._version_ref(current.organization_id, current_version).get(
                    transaction=transaction
                )
                if current_version
                else None
            )
            prior, prior_storage = self._graph_from_row(
                current.organization_id,
                current_version,
                prior_row,
                transaction=transaction,
            )
            if current_version and prior_storage["payload_digest"] != _state["payload_digest"]:
                raise ImportUnavailable()
            if draft.base_graph_version != current_version:
                raise GraphVersionConflict()
            now = _aware(self._clock())
            graph, carried_member_uids, removed_member_uids = _committed_graph(
                draft,
                prior,
                version=current_version + 1,
                created_at=now,
            )
            active_removed = self._membership_change_transaction(
                transaction,
                current,
                carried_member_uids=carried_member_uids,
                removed_member_uids=removed_member_uids,
            )
            receipt = _receipt_for(
                draft,
                graph_version=graph.version,
                actor_uid=current.principal.uid,
                committed_subject_count=len(draft.candidate.subjects),
                committed_at=now,
            )
            event = _commit_event(current, current_version, receipt, now)
            graph_storage, graph_chunks = _chunked_graph(graph)
            version_ref = self._version_ref(current.organization_id, graph.version)
            committed_payload = dict(payload)
            committed_payload.update(
                {"status": "committed", "receipt": receipt.model_dump(mode="python")}
            )
            committed_payload["payload_digest"] = _payload_digest(
                {
                    key: committed_payload[key]
                    for key in committed_payload
                    if key != "payload_digest"
                }
            )
            state_payload = {
                "schema_version": 1,
                "organization_id": current.organization_id,
                "current_version": graph.version,
                "current_version_id": f"{graph.version:020d}",
                "payload_digest": graph_storage["payload_digest"],
                "updated_at": now,
            }
            membership_documents = self._membership_suspension_documents(
                current,
                active_removed,
                graph.version,
                now,
            )
            event_ref = self._client.collection(self._audit_collection).document(event.event_id)
            self._preflight_graph_documents(
                version_ref,
                organization_ref,
                graph_storage,
                graph_chunks,
            )
            _require_document_bound(draft_ref, committed_payload)
            _require_document_bound(state_ref, state_payload)
            _require_document_bound(event_ref, event.model_dump(mode="python"))
            for member_ref, member_payload, audit_ref, audit_payload in membership_documents:
                _require_document_bound(member_ref, member_payload)
                _require_document_bound(audit_ref, audit_payload)
            extra_current_deletes = self._extra_current_chunk_count(
                prior_storage,
                graph_storage,
            )
            _require_write_bound(
                4
                + (2 * len(graph_chunks))
                + extra_current_deletes
                + (2 * len(active_removed))
            )
            transaction.create(
                version_ref,
                graph_storage,
            )
            for chunk_id, chunk in graph_chunks.items():
                transaction.create(version_ref.collection("chunks").document(chunk_id), chunk)
            self._write_current_graph(
                transaction,
                organization_ref,
                prior_storage,
                graph_storage,
                graph_chunks,
            )
            transaction.set(draft_ref, committed_payload)
            transaction.set(state_ref, state_payload)
            self._write_membership_suspensions(
                transaction,
                membership_documents,
            )
            transaction.create(
                event_ref,
                event.model_dump(mode="python"),
            )
            return receipt

        return commit(self._client.transaction())

    @_firestore_error_barrier(OrganizationUnavailable)
    def load_graph(self, context: DecisionOSContext) -> OrganizationGraph:
        current = self._authorize(context, DecisionOSPermission.READ_WORKSPACE)
        state_row = self._state_ref(current.organization_id).get()
        version, _state = self._state_from_row(current.organization_id, state_row)
        row = self._version_ref(current.organization_id, version).get() if version else None
        graph, storage = self._graph_from_row(current.organization_id, version, row)
        if version and storage["payload_digest"] != _state["payload_digest"]:
            raise OrganizationUnavailable()
        return graph

    @_firestore_error_barrier(OrganizationUnavailable)
    def bind_member(
        self,
        context: DecisionOSContext,
        *,
        subject_id: str,
        member_uid: str,
    ) -> OrganizationSubject:
        from google.cloud import firestore

        if re.fullmatch(_SUBJECT_ID, subject_id) is None:
            raise OrganizationUnavailable()
        organization_ref = self._organization_ref(context.organization_id)
        state_ref = self._state_ref(context.organization_id)

        @firestore.transactional
        def bind(transaction):
            current = self._authorize_transaction(
                transaction,
                context,
                DecisionOSPermission.MANAGE_MEMBERS,
            )
            state_row = state_ref.get(transaction=transaction)
            member_row = (
                organization_ref.collection("members")
                .document(member_uid)
                .get(transaction=transaction)
            )
            member = _model_from_snapshot(OrganizationMembership, member_row)
            if (
                member is None
                or member.status is not MembershipStatus.ACTIVE
                or member.organization_id != current.organization_id
                or member.uid != member_uid
            ):
                raise OrganizationUnavailable()
            version, _state = self._state_from_row(current.organization_id, state_row)
            if version == 0:
                raise OrganizationUnavailable()
            prior_row = self._version_ref(current.organization_id, version).get(
                transaction=transaction
            )
            prior, prior_storage = self._graph_from_row(
                current.organization_id,
                version,
                prior_row,
                transaction=transaction,
            )
            if prior_storage["payload_digest"] != _state["payload_digest"]:
                raise OrganizationUnavailable()
            subject = next((item for item in prior.subjects if item.subject_id == subject_id), None)
            if (
                subject is None
                or subject.kind is not OrganizationSubjectKind.HUMAN
                or subject.lifecycle is SubjectLifecycle.SUSPENDED
            ):
                raise OrganizationUnavailable()
            if subject.member_uid == member_uid:
                return subject
            if subject.member_uid is not None or any(
                item.member_uid == member_uid for item in prior.subjects
            ):
                raise OrganizationUnavailable()
            bound = _validated_subject(
                subject,
                {"member_uid": member_uid, "lifecycle": SubjectLifecycle.ACTIVE},
            )
            now = _aware(self._clock())
            graph = OrganizationGraph(
                organization_id=prior.organization_id,
                version=version + 1,
                subjects=tuple(
                    bound if item.subject_id == subject_id else item
                    for item in prior.subjects
                ),
                units=prior.units,
                edges=prior.edges,
                authority_assignments=prior.authority_assignments,
                created_at=now,
            )
            event = _binding_event(current, version, bound, now)
            graph_storage, graph_chunks = _chunked_graph(graph)
            version_ref = self._version_ref(current.organization_id, graph.version)
            state_payload = {
                "schema_version": 1,
                "organization_id": current.organization_id,
                "current_version": graph.version,
                "current_version_id": f"{graph.version:020d}",
                "payload_digest": graph_storage["payload_digest"],
                "updated_at": now,
            }
            event_ref = self._client.collection(self._audit_collection).document(event.event_id)
            self._preflight_graph_documents(
                version_ref,
                organization_ref,
                graph_storage,
                graph_chunks,
            )
            _require_document_bound(state_ref, state_payload)
            _require_document_bound(event_ref, event.model_dump(mode="python"))
            extra_current_deletes = self._extra_current_chunk_count(
                prior_storage,
                graph_storage,
            )
            _require_write_bound(3 + (2 * len(graph_chunks)) + extra_current_deletes)
            transaction.create(
                version_ref,
                graph_storage,
            )
            for chunk_id, chunk in graph_chunks.items():
                transaction.create(version_ref.collection("chunks").document(chunk_id), chunk)
            self._write_current_graph(
                transaction,
                organization_ref,
                prior_storage,
                graph_storage,
                graph_chunks,
            )
            transaction.set(state_ref, state_payload)
            transaction.create(
                event_ref,
                event.model_dump(mode="python"),
            )
            return bound

        return bind(self._client.transaction())

    @_firestore_error_barrier(OrganizationUnavailable)
    def list_audit(
        self,
        context: DecisionOSContext,
    ) -> tuple[OrganizationGraphAuditEvent, ...]:
        from google.cloud.firestore_v1.base_query import FieldFilter

        current = self._authorize(context, DecisionOSPermission.READ_WORKSPACE)
        rows = self._client.collection(self._audit_collection).where(
            filter=FieldFilter("organization_id", "==", current.organization_id)
        ).stream()
        events = None
        try:
            events = tuple(
                sorted(
                    (
                        OrganizationGraphAuditEvent.model_validate_json(to_json(row.to_dict()))
                        for row in rows
                    ),
                    key=lambda item: item.new_graph_version,
                )
            )
        except (
            KeyError,
            PydanticSerializationError,
            TypeError,
            ValueError,
            ValidationError,
        ):
            pass
        if events is None:
            raise OrganizationUnavailable() from None
        if any(event.organization_id != current.organization_id for event in events):
            raise OrganizationUnavailable()
        return events

    def _authorize(
        self,
        context: DecisionOSContext,
        permission: DecisionOSPermission,
    ) -> DecisionOSContext:
        current = self._load_context(context.principal, context.organization_id)
        require_permission(current, permission)
        return current

    def _authorize_transaction(
        self,
        transaction,
        context: DecisionOSContext,
        permission: DecisionOSPermission,
    ) -> DecisionOSContext:
        organization_ref = self._organization_ref(context.organization_id)
        organization = organization_ref.get(transaction=transaction)
        member = _model_from_snapshot(
            OrganizationMembership,
            organization_ref.collection("members")
            .document(context.principal.uid)
            .get(transaction=transaction),
        )
        if (
            not organization.exists
            or member is None
            or member.status is not MembershipStatus.ACTIVE
            or member.organization_id != context.organization_id
            or member.uid != context.principal.uid
        ):
            raise OrganizationUnavailable()
        current = DecisionOSContext(principal=context.principal, membership=member)
        require_permission(current, permission)
        return current

    def _draft_from_row(
        self,
        row,
        draft_ref,
        *,
        organization_id: str,
        transaction=None,
    ) -> ImportDraft:
        payload = row.to_dict()
        chunks = self._read_chunks(
            draft_ref,
            payload,
            organization_id=organization_id,
            owner_id=draft_ref.id,
            transaction=transaction,
        )
        draft = self._draft_from_payload(payload, chunks=chunks)
        if draft.organization_id != organization_id or draft.import_id != draft_ref.id:
            raise ImportUnavailable()
        return draft

    def _draft_from_payload(
        self,
        payload: dict[str, Any],
        *,
        chunks: dict[str, tuple[dict[str, Any], ...]],
    ) -> ImportDraft:
        draft = None
        try:
            if set(payload) != _DRAFT_STORAGE_FIELDS or not self._storage_digest_valid(payload):
                raise ValueError("invalid draft storage metadata")
            draft_payload = {
                key: value for key, value in payload.items() if key in ImportDraft.model_fields
            }
            draft_payload["source_snapshot"] = dict(draft_payload["source_snapshot"])
            draft_payload["source_snapshot"]["records"] = chunks["source_records"]
            draft_payload["candidate"] = dict(draft_payload["candidate"])
            for kind in _CHUNK_KINDS[1:]:
                draft_payload["candidate"][kind] = chunks[kind]
            draft = ImportDraft.model_validate_json(to_json(draft_payload))
        except (
            KeyError,
            PydanticSerializationError,
            TypeError,
            ValueError,
            ValidationError,
        ):
            pass
        if draft is None:
            raise ImportUnavailable() from None
        return draft

    def _graph_from_row(
        self,
        organization_id: str,
        version: int,
        row,
        *,
        transaction=None,
    ) -> tuple[OrganizationGraph, dict[str, Any]]:
        if version == 0:
            graph = OrganizationGraph(
                organization_id=organization_id, version=0, created_at=_aware(self._clock())
            )
            return graph, {
                "manifest": {kind: () for kind in _CHUNK_KINDS},
                "payload_digest": None,
            }
        if row is None or not row.exists:
            raise OrganizationUnavailable()
        graph = None
        try:
            payload = row.to_dict()
            if set(payload) != _GRAPH_STORAGE_FIELDS or not self._storage_digest_valid(payload):
                raise ValueError("invalid graph storage metadata")
            if payload["organization_id"] != organization_id or payload["version"] != version:
                raise ValueError("cross-bound graph storage metadata")
            chunks = self._read_chunks(
                row.reference,
                payload,
                organization_id=organization_id,
                owner_id=f"{version:020d}",
                transaction=transaction,
            )
            graph_payload = {
                "organization_id": payload["organization_id"],
                "version": payload["version"],
                "created_at": payload["created_at"],
                **{kind: chunks[kind] for kind in _CHUNK_KINDS[1:]},
            }
            graph = OrganizationGraph.model_validate_json(to_json(graph_payload))
        except (
            KeyError,
            PydanticSerializationError,
            TypeError,
            ValueError,
            ValidationError,
        ):
            pass
        if graph is None:
            raise OrganizationUnavailable() from None
        return graph, payload

    def _read_chunks(
        self,
        owner_ref,
        payload: dict[str, Any],
        *,
        organization_id: str,
        owner_id: str,
        transaction=None,
    ) -> dict[str, tuple[dict[str, Any], ...]]:
        manifest = payload.get("manifest")
        if not isinstance(manifest, dict) or set(manifest) != set(_CHUNK_KINDS):
            raise ImportUnavailable()
        result = {}
        for kind in _CHUNK_KINDS:
            descriptors = manifest[kind]
            if not isinstance(descriptors, (list, tuple)):
                raise ImportUnavailable()
            items = []
            for expected_index, descriptor in enumerate(descriptors):
                if not isinstance(descriptor, dict) or set(descriptor) != {
                    "chunk_id",
                    "index",
                    "count",
                    "digest",
                }:
                    raise ImportUnavailable()
                expected_id = f"{kind}_{expected_index:05d}"
                if (
                    descriptor.get("chunk_id") != expected_id
                    or descriptor.get("index") != expected_index
                ):
                    raise ImportUnavailable()
                row = owner_ref.collection("chunks").document(expected_id).get(
                    transaction=transaction
                )
                if not row.exists:
                    raise ImportUnavailable()
                chunk = row.to_dict()
                if (
                    set(chunk) != _CHUNK_STORAGE_FIELDS
                    or chunk.get("schema_version") != 1
                    or chunk.get("organization_id") != organization_id
                    or chunk.get("owner_id") != owner_id
                    or chunk.get("kind") != kind
                    or chunk.get("index") != expected_index
                    or chunk.get("count") != descriptor.get("count")
                    or chunk.get("digest") != descriptor.get("digest")
                    or not isinstance(chunk.get("items"), (list, tuple))
                    or len(chunk["items"]) != chunk["count"]
                    or _payload_digest(chunk["items"]) != chunk["digest"]
                    or _firestore_document_bytes(row.reference, chunk) > _MAX_DOCUMENT_BYTES
                ):
                    raise ImportUnavailable()
                items.extend(chunk["items"])
            result[kind] = tuple(items)
        return result

    def _state_from_row(self, organization_id: str, row) -> tuple[int, dict[str, Any]]:
        if not row.exists:
            return 0, {
                "organization_id": organization_id,
                "current_version": 0,
                "current_version_id": "00000000000000000000",
                "payload_digest": None,
            }
        payload = row.to_dict()
        if (
            set(payload) != _STATE_STORAGE_FIELDS
            or payload.get("schema_version") != 1
            or payload.get("organization_id") != organization_id
            or type(payload.get("current_version")) is not int
            or payload["current_version"] < 1
            or payload.get("current_version_id") != f"{payload['current_version']:020d}"
            or re.fullmatch(_SHA256, payload.get("payload_digest", "")) is None
            or not isinstance(payload.get("updated_at"), datetime)
            or payload["updated_at"].tzinfo is None
            or payload["updated_at"].utcoffset() is None
        ):
            raise OrganizationUnavailable()
        return payload["current_version"], payload

    def _storage_digest_valid(self, payload: dict[str, Any]) -> bool:
        digest = payload.get("payload_digest")
        return (
            type(digest) is str
            and re.fullmatch(_SHA256, digest) is not None
            and secrets.compare_digest(
                digest,
                _payload_digest({key: payload[key] for key in payload if key != "payload_digest"}),
            )
        )

    def _receipt_matches_draft(self, receipt: ImportReceipt, draft: ImportDraft) -> bool:
        expected_id = f"rcp_{_deterministic_ulid(draft.organization_id, draft.import_id)}"
        return (
            receipt.receipt_id == expected_id
            and receipt.organization_id == draft.organization_id
            and receipt.import_id == draft.import_id
            and receipt.source_snapshot_id == draft.source_snapshot.snapshot_id
            and receipt.source_snapshot_digest == draft.source_snapshot.semantic_digest
            and receipt.graph_version == draft.base_graph_version + 1
            and receipt.committed_subject_count == len(draft.candidate.subjects)
        )

    def _membership_change_transaction(
        self,
        transaction,
        context: DecisionOSContext,
        *,
        carried_member_uids: tuple[str, ...],
        removed_member_uids: tuple[str, ...],
    ) -> tuple[OrganizationMembership, ...]:
        from google.cloud.firestore_v1.base_query import FieldFilter

        if set(carried_member_uids) & set(removed_member_uids):
            raise OrganizationGraphInvalid()
        organization_ref = self._organization_ref(context.organization_id)
        for uid in carried_member_uids:
            row = organization_ref.collection("members").document(uid).get(
                transaction=transaction
            )
            member = _model_from_snapshot(OrganizationMembership, row)
            if (
                member is None
                or member.organization_id != context.organization_id
                or member.uid != uid
                or member.status is not MembershipStatus.ACTIVE
            ):
                raise OrganizationGraphInvalid()
        active_removed = []
        for uid in removed_member_uids:
            row = organization_ref.collection("members").document(uid).get(
                transaction=transaction
            )
            member = _model_from_snapshot(OrganizationMembership, row)
            if member is None:
                continue
            if member.organization_id != context.organization_id or member.uid != uid:
                raise OrganizationUnavailable()
            if member.status is MembershipStatus.ACTIVE:
                active_removed.append(member)
        if context.membership.role is DecisionOSRole.ADMIN and any(
            member.role is DecisionOSRole.OWNER for member in active_removed
        ):
            from humanwire.decisionos_store import DecisionOSAuthorizationDenied

            raise DecisionOSAuthorizationDenied()
        removed_uids = {member.uid for member in active_removed}
        if any(member.role is DecisionOSRole.OWNER for member in active_removed):
            owner_rows = organization_ref.collection("members").where(
                filter=FieldFilter("role", "==", DecisionOSRole.OWNER.value)
            ).stream(transaction=transaction)
            other_owner = False
            for row in owner_rows:
                member = _model_from_snapshot(OrganizationMembership, row)
                if (
                    member is None
                    or member.organization_id != context.organization_id
                    or member.uid != row.id
                ):
                    raise OrganizationUnavailable()
                if member.uid not in removed_uids and member.status is MembershipStatus.ACTIVE:
                    other_owner = True
            if not other_owner:
                raise LastOwnerRequired()
        return tuple(active_removed)

    def _membership_suspension_documents(
        self,
        context: DecisionOSContext,
        members: tuple[OrganizationMembership, ...],
        graph_version: int,
        occurred_at: datetime,
    ) -> tuple[tuple[Any, dict[str, Any], Any, dict[str, Any]], ...]:
        organization_ref = self._organization_ref(context.organization_id)
        documents = []
        for member in members:
            updated = OrganizationMembership.model_validate(
                {**member.model_dump(mode="python"), "status": MembershipStatus.SUSPENDED}
            )
            number = int(
                hashlib.sha256(
                    f"{context.organization_id}\0{graph_version}\0{member.uid}".encode()
                ).hexdigest()[:16],
                16,
            )
            audit_id = f"audit_{number:020d}"
            event = DecisionOSAuditEvent(
                audit_id=audit_id,
                organization_id=context.organization_id,
                event_name="member_suspended",
                actor_uid=context.principal.uid,
                target_uid=member.uid,
                occurred_at=occurred_at,
            )
            documents.append(
                (
                    organization_ref.collection("members").document(member.uid),
                    updated.model_dump(mode="python"),
                    organization_ref.collection("audit").document(audit_id),
                    event.model_dump(mode="python"),
                )
            )
        return tuple(documents)

    def _write_membership_suspensions(
        self,
        transaction,
        documents: tuple[tuple[Any, dict[str, Any], Any, dict[str, Any]], ...],
    ) -> None:
        for member_ref, member_payload, audit_ref, audit_payload in documents:
            transaction.set(member_ref, member_payload)
            transaction.create(audit_ref, audit_payload)

    def _preflight_graph_documents(
        self,
        version_ref,
        organization_ref,
        graph_storage: dict[str, Any],
        chunks: dict[str, dict[str, Any]],
    ) -> None:
        _require_document_bound(version_ref, graph_storage)
        collections = {
            "subjects": "org_subjects",
            "units": "org_units",
            "edges": "org_edges",
            "authority_assignments": "authority_policies",
        }
        for chunk_id, chunk in chunks.items():
            _require_document_bound(version_ref.collection("chunks").document(chunk_id), chunk)
        for kind, collection_name in collections.items():
            collection = organization_ref.collection(collection_name)
            for index in range(len(graph_storage["manifest"][kind])):
                _require_document_bound(
                    collection.document(f"chunk_{index:05d}"),
                    chunks[f"{kind}_{index:05d}"],
                )

    def _extra_current_chunk_count(
        self,
        prior_storage: dict[str, Any],
        graph_storage: dict[str, Any],
    ) -> int:
        return sum(
            max(0, len(prior_storage["manifest"][kind]) - len(graph_storage["manifest"][kind]))
            for kind in _CHUNK_KINDS[1:]
        )

    def _write_current_graph(
        self,
        transaction,
        organization_ref,
        prior_storage: dict[str, Any],
        graph_storage: dict[str, Any],
        chunks: dict[str, dict[str, Any]],
    ) -> None:
        collections = {
            "subjects": "org_subjects",
            "units": "org_units",
            "edges": "org_edges",
            "authority_assignments": "authority_policies",
        }
        for kind, collection_name in collections.items():
            collection = organization_ref.collection(collection_name)
            old_count = len(prior_storage["manifest"][kind])
            new_count = len(graph_storage["manifest"][kind])
            for index in range(new_count, old_count):
                transaction.delete(collection.document(f"chunk_{index:05d}"))
            for index in range(new_count):
                chunk = chunks[f"{kind}_{index:05d}"]
                transaction.set(collection.document(f"chunk_{index:05d}"), chunk)
