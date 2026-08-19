"""Tenant-bound persistence for reviewed organization graph imports."""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator
from pydantic_core import PydanticSerializationError, to_json

from humanwire.decisionos_models import (
    DecisionOSContext,
    DecisionOSPrincipal,
    MembershipStatus,
    OrganizationMembership,
)
from humanwire.decisionos_store import (
    DecisionOSPermission,
    DecisionOSRepository,
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
) -> OrganizationGraph:
    prior_by_subject_id = {item.subject_id: item for item in prior.subjects}
    prior_by_source = {
        item.source_identity: item
        for item in prior.subjects
        if item.source_identity is not None
    }
    committed: dict[str, OrganizationSubject] = {}
    present_sources: set[str] = set()
    for candidate in draft.candidate.subjects:
        if candidate.source_identity is not None:
            present_sources.add(candidate.source_identity)
        previous = prior_by_subject_id.get(candidate.subject_id)
        if previous is None and candidate.source_identity is not None:
            previous = prior_by_source.get(candidate.source_identity)
        if previous is not None and previous.member_uid is not None:
            candidate = candidate.model_copy(
                update={
                    "lifecycle": SubjectLifecycle.ACTIVE,
                    "member_uid": previous.member_uid,
                }
            )
        elif candidate.lifecycle is SubjectLifecycle.DRAFT_IMPORTED:
            candidate = candidate.model_copy(update={"lifecycle": SubjectLifecycle.DIRECTORY_ONLY})
        committed[candidate.subject_id] = candidate
    for previous in prior.subjects:
        if (
            previous.source_identity is not None
            and previous.source_identity not in present_sources
            and previous.subject_id not in committed
        ):
            committed[previous.subject_id] = previous.model_copy(
                update={"lifecycle": SubjectLifecycle.SUSPENDED}
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
    return graph


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
            graph = _committed_graph(
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
            self._graphs[(current.organization_id, graph.version)] = graph
            self._current_versions[current.organization_id] = graph.version
            saved.receipt = receipt
            self._audit.setdefault(current.organization_id, []).append(event)
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
            bound = subject.model_copy(
                update={"member_uid": member_uid, "lifecycle": SubjectLifecycle.ACTIVE}
            )
            now = _aware(self._clock())
            graph = prior.model_copy(
                update={
                    "version": prior.version + 1,
                    "subjects": tuple(
                        bound if item.subject_id == subject_id else item
                        for item in prior.subjects
                    ),
                    "created_at": now,
                }
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

    def load_context(
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
        ):
            raise OrganizationUnavailable()
        return DecisionOSContext(principal=principal, membership=member)

    def save_import_draft(
        self,
        context: DecisionOSContext,
        draft: ImportDraft,
    ) -> ImportDraft:
        from google.cloud import firestore

        if draft.organization_id != context.organization_id:
            raise ImportUnavailable()
        draft_ref = self._import_ref(context.organization_id, draft.import_id)

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
            payload = draft.model_dump(mode="python")
            records = payload["source_snapshot"].pop("records")
            payload.update({"status": "draft", "receipt": None})
            transaction.create(draft_ref, payload)
            for record in records:
                record_ref = draft_ref.collection("records").document(record["record_id"])
                transaction.create(record_ref, record)

        save(self._client.transaction())
        return draft

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
        return self._draft_from_row(row, draft_ref)

    def list_imports(self, context: DecisionOSContext) -> tuple[ImportDraft, ...]:
        current = self._authorize(context, DecisionOSPermission.MANAGE_MEMBERS)
        rows = self._organization_ref(current.organization_id).collection("imports").stream()
        return tuple(
            sorted(
                (
                    self._draft_from_row(
                        row,
                        self._import_ref(current.organization_id, row.id),
                    )
                    for row in rows
                ),
                key=lambda item: item.import_id,
            )
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
        from google.cloud import firestore

        draft_ref = self._import_ref(context.organization_id, draft_id)
        organization_ref = self._organization_ref(context.organization_id)

        @firestore.transactional
        def commit(transaction):
            current = self._authorize_transaction(
                transaction,
                context,
                DecisionOSPermission.MANAGE_MEMBERS,
            )
            organization_row = organization_ref.get(transaction=transaction)
            draft_row = draft_ref.get(transaction=transaction)
            if not draft_row.exists:
                raise ImportUnavailable()
            payload = draft_row.to_dict()
            draft = self._draft_from_payload(payload)
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
                if receipt is None:
                    raise ImportUnavailable() from None
                return receipt
            if payload.get("status") != "draft":
                raise ImportUnavailable()
            current_version = organization_row.to_dict().get("organization_graph_version", 0)
            if type(current_version) is not int or current_version < 0:
                raise GraphVersionConflict()
            prior_row = (
                self._version_ref(current.organization_id, current_version).get(
                    transaction=transaction
                )
                if current_version
                else None
            )
            prior = self._graph_from_row(current.organization_id, current_version, prior_row)
            if draft.base_graph_version != current_version:
                raise GraphVersionConflict()
            now = _aware(self._clock())
            graph = _committed_graph(
                draft,
                prior,
                version=current_version + 1,
                created_at=now,
            )
            receipt = _receipt_for(
                draft,
                graph_version=graph.version,
                actor_uid=current.principal.uid,
                committed_subject_count=len(draft.candidate.subjects),
                committed_at=now,
            )
            event = _commit_event(current, current_version, receipt, now)
            transaction.create(
                self._version_ref(current.organization_id, graph.version),
                graph.model_dump(mode="python"),
            )
            self._write_current_graph(transaction, organization_ref, prior, graph)
            transaction.update(
                draft_ref,
                {"status": "committed", "receipt": receipt.model_dump(mode="python")},
            )
            transaction.create(
                self._client.collection(self._audit_collection).document(event.event_id),
                event.model_dump(mode="python"),
            )
            return receipt

        return commit(self._client.transaction())

    def load_graph(self, context: DecisionOSContext) -> OrganizationGraph:
        current = self._authorize(context, DecisionOSPermission.READ_WORKSPACE)
        organization_row = self._organization_ref(current.organization_id).get()
        version = organization_row.to_dict().get("organization_graph_version", 0)
        if type(version) is not int or version < 0:
            raise OrganizationUnavailable()
        row = self._version_ref(current.organization_id, version).get() if version else None
        return self._graph_from_row(current.organization_id, version, row)

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

        @firestore.transactional
        def bind(transaction):
            current = self._authorize_transaction(
                transaction,
                context,
                DecisionOSPermission.MANAGE_MEMBERS,
            )
            organization_row = organization_ref.get(transaction=transaction)
            member_row = (
                organization_ref.collection("members")
                .document(member_uid)
                .get(transaction=transaction)
            )
            member = _model_from_snapshot(OrganizationMembership, member_row)
            if member is None or member.status is not MembershipStatus.ACTIVE:
                raise OrganizationUnavailable()
            version = organization_row.to_dict().get("organization_graph_version", 0)
            prior_row = self._version_ref(current.organization_id, version).get(
                transaction=transaction
            )
            prior = self._graph_from_row(current.organization_id, version, prior_row)
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
            bound = subject.model_copy(
                update={"member_uid": member_uid, "lifecycle": SubjectLifecycle.ACTIVE}
            )
            now = _aware(self._clock())
            graph = prior.model_copy(
                update={
                    "version": version + 1,
                    "subjects": tuple(
                        bound if item.subject_id == subject_id else item
                        for item in prior.subjects
                    ),
                    "created_at": now,
                }
            )
            event = _binding_event(current, version, bound, now)
            transaction.create(
                self._version_ref(current.organization_id, graph.version),
                graph.model_dump(mode="python"),
            )
            self._write_current_graph(transaction, organization_ref, prior, graph)
            transaction.create(
                self._client.collection(self._audit_collection).document(event.event_id),
                event.model_dump(mode="python"),
            )
            return bound

        return bind(self._client.transaction())

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
        return events

    def _authorize(
        self,
        context: DecisionOSContext,
        permission: DecisionOSPermission,
    ) -> DecisionOSContext:
        current = self.load_context(context.principal, context.organization_id)
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
        ):
            raise OrganizationUnavailable()
        current = DecisionOSContext(principal=context.principal, membership=member)
        require_permission(current, permission)
        return current

    def _draft_from_row(self, row, draft_ref) -> ImportDraft:
        payload = row.to_dict()
        records = tuple(item.to_dict() for item in draft_ref.collection("records").stream())
        return self._draft_from_payload(payload, records=records)

    def _draft_from_payload(
        self,
        payload: dict[str, Any],
        *,
        records: tuple[dict[str, Any], ...] = (),
    ) -> ImportDraft:
        draft = None
        try:
            draft_payload = {key: value for key, value in payload.items() if key in ImportDraft.model_fields}
            draft_payload["source_snapshot"] = dict(draft_payload["source_snapshot"])
            draft_payload["source_snapshot"]["records"] = records
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

    def _graph_from_row(self, organization_id: str, version: int, row) -> OrganizationGraph:
        if version == 0:
            return OrganizationGraph(
                organization_id=organization_id,
                version=0,
                created_at=_aware(self._clock()),
            )
        if row is None or not row.exists:
            raise OrganizationUnavailable()
        graph = None
        try:
            graph = OrganizationGraph.model_validate_json(to_json(row.to_dict()))
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
        if graph.organization_id != organization_id or graph.version != version:
            raise OrganizationUnavailable()
        return graph

    def _write_current_graph(self, transaction, organization_ref, prior, graph) -> None:
        transaction.update(organization_ref, {"organization_graph_version": graph.version})
        collections = (
            ("org_subjects", "subject_id", prior.subjects, graph.subjects),
            ("org_units", "unit_id", prior.units, graph.units),
            ("org_edges", "edge_id", prior.edges, graph.edges),
            (
                "authority_policies",
                "assignment_id",
                prior.authority_assignments,
                graph.authority_assignments,
            ),
        )
        for collection_name, id_field, old_records, new_records in collections:
            collection = organization_ref.collection(collection_name)
            new_ids = {getattr(item, id_field) for item in new_records}
            for item in old_records:
                item_id = getattr(item, id_field)
                if item_id not in new_ids:
                    transaction.delete(collection.document(item_id))
            for item in new_records:
                transaction.set(
                    collection.document(getattr(item, id_field)),
                    item.model_dump(mode="python"),
                )
