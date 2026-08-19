"""Tenant-bound persistence for reviewed organization graph imports."""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
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
    InvitationUnavailable,
    LastOwnerRequired,
    MembershipUnavailable,
    OrganizationUnavailable,
    SubjectInvitationGrant,
    _FirestorePreparedMutation,
    _InMemoryPreparedMutation,
    _InMemoryReferenceReplacement,
    _publish_in_memory_replacements,
    require_permission,
)
from humanwire.organization_canonical import (
    exact_canonical_equal,
    exact_canonical_model,
)
from humanwire.organization_graph import validate_organization_graph
from humanwire.organization_models import (
    AuthorityAssignment,
    ImportDraft,
    ImportReceipt,
    ImportReconciliation,
    OrganizationEdge,
    OrganizationGraph,
    OrganizationSubject,
    OrganizationSubjectKind,
    OrganizationUnit,
    SubjectLifecycle,
)

_ULID = r"[0-9A-HJKMNP-TV-Z]{26}"
_IMPORT_ID = rf"^imp_{_ULID}$"
_SUBJECT_ID = rf"^sub_{_ULID}$"
_SHA256 = r"^[0-9a-f]{64}$"
_FIREBASE_UID = r"^[A-Za-z0-9._:-]{1,128}$"
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CHUNK_TARGET_BYTES = 350_000
_MAX_DOCUMENT_BYTES = 450_000
_MAX_TRANSACTION_WRITES = 450
_MAX_ACTIVATION_TRANSITIONS = 4096
_MAX_CHUNK_ITEMS = 200
_CHUNK_KINDS = (
    "source_records",
    "subjects",
    "units",
    "edges",
    "authority_assignments",
)
_DRAFT_V2_STORAGE_FIELDS = frozenset(
    {
        "schema_version",
        "import_id",
        "supersedes_import_id",
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
_DRAFT_V1_STORAGE_FIELDS = _DRAFT_V2_STORAGE_FIELDS - {"supersedes_import_id"}
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
_LINEAGE_STORAGE_FIELDS = frozenset(
    {
        "schema_version",
        "organization_id",
        "source_kind",
        "latest_import_id",
        "updated_at",
        "payload_digest",
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


class ImportLineageConflict(ImportUnavailable):
    def __init__(self) -> None:
        OrganizationStoreError.__init__(self, "import_lineage_conflict")


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


class _ActivationTransition(BaseModel):
    """Private, digest-bound provenance for post-import activation graph versions."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    organization_id: str
    kind: Literal["invitations_created", "invitation_accepted"]
    subject_ids: tuple[str, ...]
    prior_graph_version: int
    new_graph_version: int
    member_uid: str | None = None
    occurred_at: datetime
    payload_digest: str

    @model_validator(mode="after")
    def is_exact_activation_provenance(self) -> Self:
        if (
            re.fullmatch(rf"^org_{_ULID}$", self.organization_id) is None
            or type(self.prior_graph_version) is not int
            or self.prior_graph_version < 1
            or type(self.new_graph_version) is not int
            or self.new_graph_version != self.prior_graph_version + 1
            or type(self.subject_ids) is not tuple
            or not self.subject_ids
            or len(self.subject_ids) != len(set(self.subject_ids))
            or any(
                type(subject_id) is not str
                or re.fullmatch(_SUBJECT_ID, subject_id) is None
                for subject_id in self.subject_ids
            )
            or self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
        ):
            raise ValueError("activation transition is invalid")
        if self.kind == "invitations_created":
            if self.member_uid is not None:
                raise ValueError("invitation issue transition cannot contain a UID")
        elif (
            len(self.subject_ids) != 1
            or type(self.member_uid) is not str
            or re.fullmatch(_FIREBASE_UID, self.member_uid) is None
        ):
            raise ValueError("invitation acceptance transition is invalid")
        expected = hashlib.sha256(
            to_json(self.model_dump(mode="python", exclude={"payload_digest"}))
        ).hexdigest()
        if (
            type(self.payload_digest) is not str
            or re.fullmatch(_SHA256, self.payload_digest) is None
            or not secrets.compare_digest(self.payload_digest, expected)
        ):
            raise ValueError("activation transition digest is invalid")
        return self


def _activation_transition(
    *,
    organization_id: str,
    kind: Literal["invitations_created", "invitation_accepted"],
    subject_ids: tuple[str, ...],
    prior_graph_version: int,
    member_uid: str | None,
    occurred_at: datetime,
) -> _ActivationTransition:
    payload = {
        "organization_id": organization_id,
        "kind": kind,
        "subject_ids": subject_ids,
        "prior_graph_version": prior_graph_version,
        "new_graph_version": prior_graph_version + 1,
        "member_uid": member_uid,
        "occurred_at": occurred_at,
    }
    return _ActivationTransition(
        **payload,
        payload_digest=hashlib.sha256(to_json(payload)).hexdigest(),
    )


def _canonical_activation_transition(value: object) -> _ActivationTransition | None:
    if type(value) is not _ActivationTransition:
        return None
    canonical = None
    failed = False
    try:
        fields = tuple(_ActivationTransition.model_fields)
        values = object.__getattribute__(value, "__dict__")
        extra = object.__getattribute__(value, "__pydantic_extra__")
        private = object.__getattribute__(value, "__pydantic_private__")
        if (
            type(values) is not dict
            or tuple(values) != fields
            or extra not in (None, {})
            or private not in (None, {})
        ):
            return None
        raw_json = BaseModel.model_dump_json(value, warnings="error")
        canonical = BaseModel.model_validate_json.__func__(
            _ActivationTransition,
            raw_json,
            strict=True,
        )
        if not isinstance(canonical, _ActivationTransition):
            return None
        canonical_json = BaseModel.model_dump_json(canonical, warnings="error")
        if not secrets.compare_digest(raw_json, canonical_json):
            return None
    except Exception:  # noqa: BLE001 - hostile private records fail closed
        failed = True
    if failed:
        return None
    return canonical


def _transition_from_storage(value: object) -> _ActivationTransition:
    parsed = None
    failed = False
    try:
        parsed = _ActivationTransition.model_validate_json(to_json(value), strict=True)
    except Exception:  # noqa: BLE001 - stored corruption details are sealed
        failed = True
    canonical = None if parsed is None else _canonical_activation_transition(parsed)
    if failed or canonical is None:
        raise ImportUnavailable() from None
    return canonical


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
        acknowledged_codes: tuple[str, ...] = (),
    ) -> ImportReceipt: ...

    def load_graph(self, context: DecisionOSContext) -> OrganizationGraph: ...

    def load_import_draft(
        self,
        context: DecisionOSContext,
        draft_id: str,
    ) -> ImportDraft: ...

    def load_import_receipt(
        self,
        context: DecisionOSContext,
        import_id: str,
    ) -> ImportReceipt | None: ...

    def load_committed_import(
        self,
        context: DecisionOSContext,
        graph_version: int,
    ) -> tuple[ImportDraft, ImportReceipt] | None: ...

    def list_imports(self, context: DecisionOSContext) -> tuple[ImportDraft, ...]: ...

    def require_latest_import(
        self,
        context: DecisionOSContext,
        draft_id: str,
    ) -> None: ...

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

    def create_subject_invitations(
        self,
        decisionos: DecisionOSRepository,
        context: DecisionOSContext,
        *,
        subject_ids: tuple[str, ...],
        role: DecisionOSRole,
        expires_in: timedelta,
        delivery_route_id: str | None,
    ) -> tuple[SubjectInvitationGrant, ...]: ...

    def accept_subject_invitation(
        self,
        decisionos: DecisionOSRepository,
        principal: DecisionOSPrincipal,
        token: str,
    ) -> tuple[OrganizationMembership, OrganizationSubject]: ...


class _SavedImport:
    __slots__ = ("draft", "receipt", "status")

    def __init__(self, draft: ImportDraft) -> None:
        self.draft = draft
        self.receipt: ImportReceipt | None = None
        self.status: Literal["draft", "committed"] = "draft"


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
    acknowledged_codes: tuple[str, ...],
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
        acknowledged_codes=acknowledged_codes,
        committed_at=committed_at,
        committed_by_uid=actor_uid,
    )


def _receipt_matches_draft(
    receipt: ImportReceipt,
    draft: ImportDraft,
    *,
    graph_version: int | None = None,
    acknowledged_codes: tuple[str, ...] | None = None,
) -> bool:
    canonical_receipt = exact_canonical_model(receipt, ImportReceipt)
    canonical_draft = exact_canonical_model(draft, ImportDraft)
    if (
        canonical_receipt is None
        or canonical_draft is None
        or any(
            type(code) is not str for code in canonical_receipt.acknowledged_codes
        )
    ):
        return False
    expected_id = (
        f"rcp_{_deterministic_ulid(canonical_draft.organization_id, canonical_draft.import_id)}"
    )
    return (
        canonical_receipt.receipt_id == expected_id
        and canonical_receipt.organization_id == canonical_draft.organization_id
        and canonical_receipt.import_id == canonical_draft.import_id
        and canonical_receipt.source_snapshot_id
        == canonical_draft.source_snapshot.snapshot_id
        and canonical_receipt.source_snapshot_digest
        == canonical_draft.source_snapshot.semantic_digest
        and canonical_receipt.graph_version == canonical_draft.base_graph_version + 1
        and (
            graph_version is None or canonical_receipt.graph_version == graph_version
        )
        and canonical_receipt.committed_subject_count
        == len(canonical_draft.candidate.subjects)
        and (
            acknowledged_codes is None
            or (
                all(type(code) is str for code in acknowledged_codes)
                and canonical_receipt.acknowledged_codes == acknowledged_codes
            )
        )
    )


def _member_binding_is_allowed(
    prior: OrganizationSubject,
    target: OrganizationSubject,
    *,
    target_subjects: tuple[OrganizationSubject, ...],
) -> bool:
    """Accept only the exact subject transition produced by ``bind_member``."""

    canonical_prior = exact_canonical_model(prior, OrganizationSubject)
    canonical_target = exact_canonical_model(target, OrganizationSubject)
    if (
        canonical_prior is None
        or canonical_target is None
        or type(target_subjects) is not tuple
    ):
        return False
    canonical_subjects: list[OrganizationSubject] = []
    for subject in target_subjects:
        canonical_subject = exact_canonical_model(subject, OrganizationSubject)
        if canonical_subject is None:
            return False
        canonical_subjects.append(canonical_subject)
    target_uid = canonical_target.member_uid
    if (
        canonical_prior.kind is not OrganizationSubjectKind.HUMAN
        or canonical_prior.lifecycle is SubjectLifecycle.SUSPENDED
        or canonical_prior.member_uid is not None
        or canonical_target.lifecycle is not SubjectLifecycle.ACTIVE
        or target_uid is None
        or sum(subject.member_uid == target_uid for subject in canonical_subjects) != 1
    ):
        return False
    return to_json(
        canonical_prior.model_dump(exclude={"lifecycle", "member_uid"})
    ) == to_json(canonical_target.model_dump(exclude={"lifecycle", "member_uid"}))


def _graphs_differ_only_by_member_bindings(
    receipt_graph: OrganizationGraph,
    target_graph: OrganizationGraph,
    *,
    receipt_version: int,
    target_version: int,
) -> bool:
    """Recognize the only graph delta that may carry an import receipt forward."""

    canonical_receipt = exact_canonical_model(receipt_graph, OrganizationGraph)
    canonical_target = exact_canonical_model(target_graph, OrganizationGraph)
    if canonical_receipt is None or canonical_target is None:
        return False
    if (
        not validate_organization_graph(canonical_receipt).committable
        or not validate_organization_graph(canonical_target).committable
    ):
        return False
    if (
        canonical_receipt.organization_id != canonical_target.organization_id
        or canonical_receipt.version != receipt_version
        or canonical_target.version != target_version
        or receipt_version > target_version
        or len(canonical_receipt.units) != len(canonical_target.units)
        or any(
            not exact_canonical_equal(left, right, OrganizationUnit)
            for left, right in zip(
                canonical_receipt.units,
                canonical_target.units,
                strict=True,
            )
        )
        or len(canonical_receipt.edges) != len(canonical_target.edges)
        or any(
            not exact_canonical_equal(left, right, OrganizationEdge)
            for left, right in zip(
                canonical_receipt.edges,
                canonical_target.edges,
                strict=True,
            )
        )
        or len(canonical_receipt.authority_assignments)
        != len(canonical_target.authority_assignments)
        or any(
            not exact_canonical_equal(left, right, AuthorityAssignment)
            for left, right in zip(
                canonical_receipt.authority_assignments,
                canonical_target.authority_assignments,
                strict=True,
            )
        )
    ):
        return False
    receipt_subject_ids = tuple(item.subject_id for item in canonical_receipt.subjects)
    target_subject_ids = tuple(item.subject_id for item in canonical_target.subjects)
    if receipt_subject_ids != target_subject_ids:
        return False
    target_member_uids = tuple(
        item.member_uid for item in canonical_target.subjects if item.member_uid is not None
    )
    if len(target_member_uids) != len(set(target_member_uids)):
        return False
    newly_bound = 0
    for prior, current in zip(
        canonical_receipt.subjects,
        canonical_target.subjects,
        strict=True,
    ):
        if exact_canonical_equal(prior, current, OrganizationSubject):
            continue
        if not _member_binding_is_allowed(
            prior,
            current,
            target_subjects=canonical_target.subjects,
        ):
            return False
        newly_bound += 1
    return target_version - receipt_version == newly_bound


def _replays_activation_transition_chain(
    receipt_graph: OrganizationGraph,
    target_graph: OrganizationGraph,
    transitions: tuple[_ActivationTransition, ...],
    *,
    receipt_version: int,
    target_version: int,
) -> bool:
    """Replay only contiguous, canonical activation transitions to the exact target."""

    current = exact_canonical_model(receipt_graph, OrganizationGraph)
    canonical_target = exact_canonical_model(target_graph, OrganizationGraph)
    if (
        current is None
        or canonical_target is None
        or type(transitions) is not tuple
        or current.organization_id != canonical_target.organization_id
        or current.version != receipt_version
        or canonical_target.version != target_version
        or receipt_version > target_version
        or len(transitions) != target_version - receipt_version
        or not validate_organization_graph(current).committable
        or not validate_organization_graph(canonical_target).committable
    ):
        return False
    for expected_version, candidate in enumerate(
        transitions,
        start=receipt_version + 1,
    ):
        transition = _canonical_activation_transition(candidate)
        if (
            transition is None
            or transition.organization_id != current.organization_id
            or transition.prior_graph_version != current.version
            or transition.new_graph_version != expected_version
        ):
            return False
        by_id = {subject.subject_id: subject for subject in current.subjects}
        if len(by_id) != len(current.subjects):
            return False
        replacements: dict[str, OrganizationSubject] = {}
        if transition.kind == "invitations_created":
            for subject_id in transition.subject_ids:
                subject = by_id.get(subject_id)
                if (
                    subject is None
                    or subject.kind is not OrganizationSubjectKind.HUMAN
                    or subject.lifecycle is not SubjectLifecycle.DIRECTORY_ONLY
                    or subject.member_uid is not None
                ):
                    return False
                replacements[subject_id] = _validated_subject(
                    subject,
                    {"lifecycle": SubjectLifecycle.INVITED},
                )
        else:
            subject_id = transition.subject_ids[0]
            subject = by_id.get(subject_id)
            member_uid = transition.member_uid
            if (
                subject is None
                or subject.kind is not OrganizationSubjectKind.HUMAN
                or subject.lifecycle
                not in {SubjectLifecycle.DIRECTORY_ONLY, SubjectLifecycle.INVITED}
                or subject.member_uid is not None
                or type(member_uid) is not str
                or any(item.member_uid == member_uid for item in current.subjects)
            ):
                return False
            replacements[subject_id] = _validated_subject(
                subject,
                {
                    "lifecycle": SubjectLifecycle.ACTIVE,
                    "member_uid": member_uid,
                },
            )
        current = OrganizationGraph(
            organization_id=current.organization_id,
            version=transition.new_graph_version,
            subjects=tuple(
                replacements.get(subject.subject_id, subject)
                for subject in current.subjects
            ),
            units=current.units,
            edges=current.edges,
            authority_assignments=current.authority_assignments,
            created_at=transition.occurred_at,
        )
        if not validate_organization_graph(current).committable:
            return False
    return exact_canonical_equal(current, canonical_target, OrganizationGraph)


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
    validated = exact_canonical_model(draft, ImportDraft)
    if validated is None:
        raise ImportUnavailable() from None
    return validated


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
        self._latest_imports: dict[tuple[str, str], str] = {}
        self._audit: dict[str, list[OrganizationGraphAuditEvent]] = {}
        self._activation_transitions: dict[
            tuple[str, int], _ActivationTransition
        ] = {}

    def __repr__(self) -> str:
        return "InMemoryOrganizationGraphRepository()"

    def _saved_state(
        self,
        organization_id: str,
        saved: object,
    ) -> tuple[ImportDraft, ImportReceipt | None]:
        if type(saved) is not _SavedImport:
            raise ImportUnavailable()
        draft = exact_canonical_model(saved.draft, ImportDraft)
        if draft is None or draft.organization_id != organization_id:
            raise ImportUnavailable()
        if type(saved.status) is not str or saved.status not in {"draft", "committed"}:
            raise ImportUnavailable()
        if saved.status == "draft":
            if saved.receipt is not None:
                raise ImportUnavailable()
            return draft, None
        receipt = exact_canonical_model(saved.receipt, ImportReceipt)
        if receipt is None or not _receipt_matches_draft(receipt, draft):
            raise ImportUnavailable()
        graph = self._graphs.get((organization_id, receipt.graph_version))
        current_version = self._current_versions.get(organization_id, 0)
        canonical_graph = exact_canonical_model(graph, OrganizationGraph)
        if (
            canonical_graph is None
            or canonical_graph.organization_id != organization_id
            or canonical_graph.version != receipt.graph_version
            or current_version < receipt.graph_version
            or not validate_organization_graph(canonical_graph).committable
        ):
            raise ImportUnavailable()
        return draft, receipt

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
            lineage_key = (current.organization_id, draft.source_snapshot.source_kind)
            latest_import_id = self._latest_imports.get(lineage_key)
            if (
                draft.supersedes_import_id is not None
                and draft.supersedes_import_id != latest_import_id
            ):
                raise ImportLineageConflict()
            if key in self._imports:
                raise ImportUnavailable()
            replacement_imports = dict(self._imports)
            replacement_imports[key] = _SavedImport(draft)
            replacement_latest = dict(self._latest_imports)
            replacement_latest[lineage_key] = draft.import_id
            failed = False
            try:
                _publish_in_memory_replacements(
                    _InMemoryPreparedMutation(
                        replacements=(
                            _InMemoryReferenceReplacement(
                                self,
                                "_imports",
                                self._imports,
                                replacement_imports,
                            ),
                            _InMemoryReferenceReplacement(
                                self,
                                "_latest_imports",
                                self._latest_imports,
                                replacement_latest,
                            ),
                        )
                    )
                )
            except Exception:  # noqa: BLE001 - publication failures are sealed
                failed = True
            if failed:
                raise ImportUnavailable() from None
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
            draft, _receipt = self._saved_state(current.organization_id, saved)
            return draft

    def load_import_receipt(
        self,
        context: DecisionOSContext,
        import_id: str,
    ) -> ImportReceipt | None:
        with self._lock:
            current = self._manage(context)
            saved = self._imports.get((current.organization_id, import_id))
            if saved is None:
                raise ImportUnavailable()
            _draft, receipt = self._saved_state(current.organization_id, saved)
            return receipt

    def load_committed_import(
        self,
        context: DecisionOSContext,
        graph_version: int,
    ) -> tuple[ImportDraft, ImportReceipt] | None:
        with self._lock:
            current = self._read(context)
            if type(graph_version) is not int or graph_version < 0:
                raise ImportUnavailable()
            if graph_version == 0:
                return None
            target_graph = self._graphs.get((current.organization_id, graph_version))
            if target_graph is None:
                raise ImportUnavailable()
            selected: tuple[ImportDraft, ImportReceipt] | None = None
            selected_version = -1
            selected_count = 0
            for (organization_id, _import_id), saved in self._imports.items():
                if organization_id != current.organization_id:
                    continue
                saved_draft, receipt = self._saved_state(organization_id, saved)
                if receipt is None:
                    continue
                if receipt.graph_version > graph_version:
                    continue
                if receipt.graph_version > selected_version:
                    selected = (saved_draft, receipt)
                    selected_version = receipt.graph_version
                    selected_count = 1
                elif receipt.graph_version == selected_version:
                    selected_count = min(selected_count + 1, 2)
            if selected is None or selected_count != 1:
                raise ImportUnavailable()
            draft, receipt = selected
            receipt_graph = self._graphs.get(
                (current.organization_id, receipt.graph_version)
            )
            transitions: list[_ActivationTransition] = []
            for version in range(receipt.graph_version + 1, graph_version + 1):
                transition = self._activation_transitions.get(
                    (current.organization_id, version)
                )
                canonical = _canonical_activation_transition(transition)
                if canonical is None:
                    raise ImportUnavailable()
                transitions.append(canonical)
            if receipt_graph is None or not _replays_activation_transition_chain(
                receipt_graph,
                target_graph,
                tuple(transitions),
                receipt_version=receipt.graph_version,
                target_version=graph_version,
            ):
                raise ImportUnavailable()
            return draft, receipt

    def list_imports(self, context: DecisionOSContext) -> tuple[ImportDraft, ...]:
        with self._lock:
            current = self._manage(context)
            return tuple(
                self._saved_state(organization_id, saved)[0]
                for (organization_id, _draft_id), saved in sorted(self._imports.items())
                if organization_id == current.organization_id
            )

    def require_latest_import(
        self,
        context: DecisionOSContext,
        draft_id: str,
    ) -> None:
        with self._lock:
            current = self._manage(context)
            saved = self._imports.get((current.organization_id, draft_id))
            if saved is None:
                raise ImportUnavailable()
            draft, receipt = self._saved_state(current.organization_id, saved)
            if receipt is not None:
                return
            lineage_key = (
                current.organization_id,
                draft.source_snapshot.source_kind,
            )
            if self._latest_imports.get(lineage_key) != draft_id:
                raise ImportLineageConflict()

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
        acknowledged_codes: tuple[str, ...] = (),
    ) -> ImportReceipt:
        with self._lock:
            acknowledged_codes = _canonical_acknowledgements(acknowledged_codes)
            current = self._manage(context)
            saved = self._imports.get((current.organization_id, draft_id))
            if saved is None:
                raise ImportUnavailable()
            draft, existing_receipt = self._saved_state(current.organization_id, saved)
            if not _digest_matches(reviewed_digest, draft.semantic_digest):
                raise ImportUnavailable()
            if existing_receipt is not None:
                if existing_receipt.acknowledged_codes != acknowledged_codes:
                    raise ImportUnavailable()
                return existing_receipt
            lineage_key = (
                current.organization_id,
                draft.source_snapshot.source_kind,
            )
            if self._latest_imports.get(lineage_key) != draft.import_id:
                raise ImportLineageConflict()
            prior = self._graph(current.organization_id)
            if draft.base_graph_version != prior.version:
                raise GraphVersionConflict()
            now = _aware(self._clock())
            graph, carried_member_uids, removed_member_uids = _committed_graph(
                draft,
                prior,
                version=prior.version + 1,
                created_at=now,
            )
            receipt = _receipt_for(
                draft,
                graph_version=graph.version,
                actor_uid=current.principal.uid,
                committed_subject_count=len(draft.candidate.subjects),
                acknowledged_codes=acknowledged_codes,
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
                replacement_saved = _SavedImport(draft)
                replacement_saved.receipt = receipt
                replacement_saved.status = "committed"
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
            publication = _InMemoryPreparedMutation(
                replacements=tuple(
                    _InMemoryReferenceReplacement(self, attribute, prior, replacement)
                    for attribute, prior, replacement in zip(
                        ("_graphs", "_current_versions", "_imports", "_audit"),
                        prior_state,
                        prepared,
                        strict=True,
                    )
                )
            )

            def persist(_transaction) -> _InMemoryPreparedMutation:
                return publication

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

    def create_subject_invitations(
        self,
        decisionos: DecisionOSRepository,
        context: DecisionOSContext,
        *,
        subject_ids: tuple[str, ...],
        role: DecisionOSRole,
        expires_in: timedelta,
        delivery_route_id: str | None,
    ) -> tuple[SubjectInvitationGrant, ...]:
        with self._lock:
            current = self._manage(context)

            def validate(
                _transaction,
                invitation_context: DecisionOSContext,
            ) -> _InMemoryPreparedMutation:
                if (
                    invitation_context.organization_id != current.organization_id
                    or invitation_context.principal.uid != current.principal.uid
                ):
                    raise InvitationUnavailable()
                graph = self._graph(current.organization_id)
                by_id = {subject.subject_id: subject for subject in graph.subjects}
                selected = tuple(by_id.get(subject_id) for subject_id in subject_ids)
                if (
                    len(by_id) != len(graph.subjects)
                    or any(
                        subject is None
                        or subject.kind is not OrganizationSubjectKind.HUMAN
                        or subject.member_uid is not None
                        for subject in selected
                    )
                ):
                    raise InvitationUnavailable()
                lifecycles = {subject.lifecycle for subject in selected if subject is not None}
                if lifecycles == {SubjectLifecycle.INVITED}:
                    return _InMemoryPreparedMutation(replacements=())
                if lifecycles != {SubjectLifecycle.DIRECTORY_ONLY}:
                    raise InvitationUnavailable()
                now = _aware(self._clock())
                invited_by_id = {
                    subject.subject_id: _validated_subject(
                        subject,
                        {"lifecycle": SubjectLifecycle.INVITED},
                    )
                    for subject in selected
                    if subject is not None
                }
                invited_graph = OrganizationGraph(
                    organization_id=graph.organization_id,
                    version=graph.version + 1,
                    subjects=tuple(
                        invited_by_id.get(subject.subject_id, subject)
                        for subject in graph.subjects
                    ),
                    units=graph.units,
                    edges=graph.edges,
                    authority_assignments=graph.authority_assignments,
                    created_at=now,
                )
                transition = _activation_transition(
                    organization_id=current.organization_id,
                    kind="invitations_created",
                    subject_ids=subject_ids,
                    prior_graph_version=graph.version,
                    member_uid=None,
                    occurred_at=now,
                )
                replacement_graphs = dict(self._graphs)
                replacement_graphs[(current.organization_id, invited_graph.version)] = (
                    invited_graph
                )
                replacement_versions = dict(self._current_versions)
                replacement_versions[current.organization_id] = invited_graph.version
                replacement_transitions = dict(self._activation_transitions)
                transition_key = (current.organization_id, invited_graph.version)
                if transition_key in replacement_transitions:
                    raise InvitationUnavailable()
                replacement_transitions[transition_key] = transition
                return _InMemoryPreparedMutation(
                    replacements=(
                        _InMemoryReferenceReplacement(
                            self,
                            "_graphs",
                            self._graphs,
                            replacement_graphs,
                        ),
                        _InMemoryReferenceReplacement(
                            self,
                            "_current_versions",
                            self._current_versions,
                            replacement_versions,
                        ),
                        _InMemoryReferenceReplacement(
                            self,
                            "_activation_transitions",
                            self._activation_transitions,
                            replacement_transitions,
                        ),
                    )
                )

            failed = False
            grants = None
            try:
                grants = decisionos.create_subject_invitations(
                    current,
                    subject_ids=subject_ids,
                    role=role,
                    expires_in=expires_in,
                    delivery_route_id=delivery_route_id,
                    mutation=validate,
                )
            except (DecisionOSStoreError, InvitationUnavailable):
                raise
            except Exception:  # noqa: BLE001 - injected/provider details are sealed
                failed = True
            if failed or grants is None:
                raise InvitationUnavailable() from None
            return grants

    def accept_subject_invitation(
        self,
        decisionos: DecisionOSRepository,
        principal: DecisionOSPrincipal,
        token: str,
    ) -> tuple[OrganizationMembership, OrganizationSubject]:
        with self._lock:
            failed = False
            accepted = None
            try:
                accepted = decisionos.accept_subject_invitation(
                    principal,
                    token,
                    mutation=self._prepare_subject_invitation_acceptance,
                )
            except InvitationUnavailable:
                raise
            except Exception:  # noqa: BLE001 - invitation failures are non-enumerating
                failed = True
            if failed or accepted is None:
                raise InvitationUnavailable() from None
            membership, subject = accepted
            if (
                type(membership) is not OrganizationMembership
                or type(subject) is not OrganizationSubject
                or membership.organization_id != subject.organization_id
                or membership.uid != subject.member_uid
            ):
                raise InvitationUnavailable()
            return membership, subject

    def _prepare_subject_invitation_acceptance(
        self,
        transaction,
        context: DecisionOSContext,
        subject_id: str,
    ) -> tuple[_InMemoryPreparedMutation, OrganizationSubject]:
        if transaction is not None or type(subject_id) is not str:
            raise InvitationUnavailable()
        prior = self._graph(context.organization_id)
        subject = next(
            (item for item in prior.subjects if item.subject_id == subject_id),
            None,
        )
        if (
            subject is None
            or subject.kind is not OrganizationSubjectKind.HUMAN
            or subject.lifecycle is not SubjectLifecycle.INVITED
            or subject.member_uid is not None
            or any(item.member_uid == context.principal.uid for item in prior.subjects)
        ):
            raise InvitationUnavailable()
        bound = _validated_subject(
            subject,
            {
                "member_uid": context.principal.uid,
                "lifecycle": SubjectLifecycle.ACTIVE,
            },
        )
        target_subjects = tuple(
            bound if item.subject_id == subject_id else item for item in prior.subjects
        )
        if not _member_binding_is_allowed(
            subject,
            bound,
            target_subjects=target_subjects,
        ):
            raise InvitationUnavailable()
        now = _aware(self._clock())
        graph = OrganizationGraph(
            organization_id=prior.organization_id,
            version=prior.version + 1,
            subjects=target_subjects,
            units=prior.units,
            edges=prior.edges,
            authority_assignments=prior.authority_assignments,
            created_at=now,
        )
        event = _binding_event(context, prior.version, bound, now)
        transition = _activation_transition(
            organization_id=context.organization_id,
            kind="invitation_accepted",
            subject_ids=(subject_id,),
            prior_graph_version=prior.version,
            member_uid=context.principal.uid,
            occurred_at=now,
        )
        replacement_graphs = dict(self._graphs)
        replacement_graphs[(context.organization_id, graph.version)] = graph
        replacement_versions = dict(self._current_versions)
        replacement_versions[context.organization_id] = graph.version
        replacement_audit = {
            organization_id: list(events)
            for organization_id, events in self._audit.items()
        }
        replacement_audit[context.organization_id] = [
            *replacement_audit.get(context.organization_id, ()),
            event,
        ]
        replacement_transitions = dict(self._activation_transitions)
        transition_key = (context.organization_id, graph.version)
        if transition_key in replacement_transitions:
            raise InvitationUnavailable()
        replacement_transitions[transition_key] = transition
        prepared = _InMemoryPreparedMutation(
            replacements=(
                _InMemoryReferenceReplacement(
                    self,
                    "_graphs",
                    self._graphs,
                    replacement_graphs,
                ),
                _InMemoryReferenceReplacement(
                    self,
                    "_current_versions",
                    self._current_versions,
                    replacement_versions,
                ),
                _InMemoryReferenceReplacement(
                    self,
                    "_audit",
                    self._audit,
                    replacement_audit,
                ),
                _InMemoryReferenceReplacement(
                    self,
                    "_activation_transitions",
                    self._activation_transitions,
                    replacement_transitions,
                ),
            )
        )
        return prepared, bound

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
            target_subjects = tuple(
                bound if item.subject_id == subject_id else item
                for item in prior.subjects
            )
            if not _member_binding_is_allowed(
                subject,
                bound,
                target_subjects=target_subjects,
            ):
                raise OrganizationUnavailable()
            now = _aware(self._clock())
            graph = OrganizationGraph(
                organization_id=prior.organization_id,
                version=prior.version + 1,
                subjects=target_subjects,
                units=prior.units,
                edges=prior.edges,
                authority_assignments=prior.authority_assignments,
                created_at=now,
            )
            event = _binding_event(current, prior.version, bound, now)
            transition = _activation_transition(
                organization_id=current.organization_id,
                kind="invitation_accepted",
                subject_ids=(subject_id,),
                prior_graph_version=prior.version,
                member_uid=member_uid,
                occurred_at=now,
            )
            transition_key = (current.organization_id, graph.version)
            if transition_key in self._activation_transitions:
                raise OrganizationUnavailable()
            self._graphs[(current.organization_id, graph.version)] = graph
            self._current_versions[current.organization_id] = graph.version
            self._audit.setdefault(current.organization_id, []).append(event)
            self._activation_transitions[transition_key] = transition
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


def _canonical_acknowledgements(value: object) -> tuple[str, ...]:
    if (
        type(value) is not tuple
        or any(
            type(code) is not str
            or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) is None
            for code in value
        )
        or len(set(value)) != len(value)
        or value != tuple(sorted(value))
    ):
        raise ImportUnavailable() from None
    return value


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
        "schema_version": 2,
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


def _lineage_payload(draft: ImportDraft) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "organization_id": draft.organization_id,
        "source_kind": draft.source_snapshot.source_kind,
        "latest_import_id": draft.import_id,
        "updated_at": draft.created_at,
    }
    payload["payload_digest"] = _payload_digest(payload)
    return payload


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

    def _activation_transition_ref(self, organization_id: str, version: int):
        if type(version) is not int or version < 1:
            raise ImportUnavailable()
        return (
            self._organization_ref(organization_id)
            .collection("organization_activation_transitions")
            .document(f"{version:020d}")
        )

    def _activation_transition_chain(
        self,
        transaction,
        organization_id: str,
        *,
        receipt_version: int,
        target_version: int,
    ) -> tuple[_ActivationTransition, ...]:
        count = target_version - receipt_version
        if count < 0 or count > _MAX_ACTIVATION_TRANSITIONS:
            raise ImportUnavailable()
        relevant: list[_ActivationTransition] = []
        for version in range(receipt_version + 1, target_version + 1):
            row = self._activation_transition_ref(organization_id, version).get(
                transaction=transaction
            )
            if not row.exists:
                raise ImportUnavailable()
            value = row.to_dict()
            transition = _transition_from_storage(value)
            if (
                transition.organization_id != organization_id
                or transition.new_graph_version != version
                or row.id != f"{version:020d}"
            ):
                raise ImportUnavailable()
            relevant.append(transition)
        return tuple(relevant)

    def _lineage_ref(self, organization_id: str, source_kind: str):
        if re.fullmatch(r"^[a-z][a-z0-9_]{0,31}$", source_kind) is None:
            raise ImportUnavailable()
        return (
            self._organization_ref(organization_id)
            .collection("organization_import_lineage")
            .document(source_kind)
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
        lineage_ref = self._lineage_ref(
            context.organization_id,
            draft.source_snapshot.source_kind,
        )
        lineage_payload = _lineage_payload(draft)
        _require_document_bound(draft_ref, payload)
        _require_document_bound(lineage_ref, lineage_payload)
        for chunk_id, chunk in chunks.items():
            _require_document_bound(
                draft_ref.collection("chunks").document(chunk_id),
                chunk,
            )
        _require_write_bound(2 + len(chunks))

        @firestore.transactional
        def save(transaction):
            current = self._authorize_transaction(
                transaction,
                context,
                DecisionOSPermission.MANAGE_MEMBERS,
            )
            if draft.organization_id != current.organization_id:
                raise ImportUnavailable()
            lineage_row = lineage_ref.get(transaction=transaction)
            latest_import_id, _legacy_lineage = self._lineage_or_legacy_latest(
                transaction,
                current.organization_id,
                draft.source_snapshot.source_kind,
                lineage_row,
            )
            if (
                draft.supersedes_import_id is not None
                and draft.supersedes_import_id != latest_import_id
            ):
                raise ImportLineageConflict()
            if draft_ref.get(transaction=transaction).exists:
                raise ImportUnavailable()
            transaction.create(draft_ref, payload)
            for chunk_id, chunk in chunks.items():
                transaction.create(draft_ref.collection("chunks").document(chunk_id), chunk)
            transaction.set(lineage_ref, lineage_payload)

        save(self._client.transaction())
        return draft

    @_firestore_error_barrier(ImportUnavailable)
    def load_import_draft(
        self,
        context: DecisionOSContext,
        draft_id: str,
    ) -> ImportDraft:
        from google.cloud import firestore

        @firestore.transactional
        def load(transaction):
            current = self._authorize_transaction(
                transaction,
                context,
                DecisionOSPermission.MANAGE_MEMBERS,
            )
            draft_ref = self._import_ref(current.organization_id, draft_id)
            row = draft_ref.get(transaction=transaction)
            if not row.exists:
                raise ImportUnavailable()
            draft = self._draft_from_row(
                row,
                draft_ref,
                organization_id=current.organization_id,
                transaction=transaction,
            )
            self._import_state(
                row.to_dict(),
                draft,
                transaction=transaction,
                organization_id=current.organization_id,
            )
            return draft

        return load(self._client.transaction())

    @_firestore_error_barrier(ImportUnavailable)
    def load_import_receipt(
        self,
        context: DecisionOSContext,
        import_id: str,
    ) -> ImportReceipt | None:
        from google.cloud import firestore

        @firestore.transactional
        def load(transaction):
            current = self._authorize_transaction(
                transaction,
                context,
                DecisionOSPermission.MANAGE_MEMBERS,
            )
            draft_ref = self._import_ref(current.organization_id, import_id)
            row = draft_ref.get(transaction=transaction)
            if not row.exists:
                raise ImportUnavailable()
            draft = self._draft_from_row(
                row,
                draft_ref,
                organization_id=current.organization_id,
                transaction=transaction,
            )
            return self._import_state(
                row.to_dict(),
                draft,
                transaction=transaction,
                organization_id=current.organization_id,
            )

        return load(self._client.transaction())

    @_firestore_error_barrier(ImportUnavailable)
    def load_committed_import(
        self,
        context: DecisionOSContext,
        graph_version: int,
    ) -> tuple[ImportDraft, ImportReceipt] | None:
        from google.cloud import firestore
        from google.cloud.firestore_v1.base_query import FieldFilter

        if type(graph_version) is not int or graph_version < 0:
            raise ImportUnavailable()

        @firestore.transactional
        def load(transaction):
            current = self._authorize_transaction(
                transaction,
                context,
                DecisionOSPermission.READ_WORKSPACE,
            )
            if graph_version == 0:
                return None
            rows = tuple(
                self._organization_ref(current.organization_id)
                .collection("imports")
                .where(
                    filter=FieldFilter(
                        "receipt.graph_version",
                        "<=",
                        graph_version,
                    )
                )
                .order_by("receipt.graph_version", direction=firestore.Query.DESCENDING)
                .limit(2)
                .stream(transaction=transaction)
            )
            if not rows:
                raise ImportUnavailable()
            parsed: list[tuple[ImportDraft, ImportReceipt]] = []
            for row in rows:
                draft_ref = self._import_ref(current.organization_id, row.id)
                draft = self._draft_from_row(
                    row,
                    draft_ref,
                    organization_id=current.organization_id,
                    transaction=transaction,
                )
                receipt = self._import_state(
                    row.to_dict(),
                    draft,
                    transaction=transaction,
                    organization_id=current.organization_id,
                )
                if receipt is None or receipt.graph_version > graph_version:
                    raise ImportUnavailable() from None
                parsed.append((draft, receipt))
            draft, receipt = parsed[0]
            if len(parsed) == 2 and parsed[1][1].graph_version == receipt.graph_version:
                raise ImportUnavailable()
            if any(
                item_receipt.graph_version >= prior_receipt.graph_version
                for (_item_draft, item_receipt), (_prior_draft, prior_receipt) in zip(
                    parsed[1:], parsed
                )
            ):
                raise ImportUnavailable()
            try:
                receipt_graph, _receipt_storage = self._graph_from_row(
                    current.organization_id,
                    receipt.graph_version,
                    self._version_ref(current.organization_id, receipt.graph_version).get(
                        transaction=transaction
                    ),
                    transaction=transaction,
                )
                target_graph, _target_storage = self._graph_from_row(
                    current.organization_id,
                    graph_version,
                    self._version_ref(current.organization_id, graph_version).get(
                        transaction=transaction
                    ),
                    transaction=transaction,
                )
            except OrganizationUnavailable:
                raise ImportUnavailable() from None
            transitions = self._activation_transition_chain(
                transaction,
                current.organization_id,
                receipt_version=receipt.graph_version,
                target_version=graph_version,
            )
            if not _replays_activation_transition_chain(
                receipt_graph,
                target_graph,
                transitions,
                receipt_version=receipt.graph_version,
                target_version=graph_version,
            ):
                raise ImportUnavailable()
            return draft, receipt

        return load(self._client.transaction())

    @_firestore_error_barrier(ImportUnavailable)
    def list_imports(self, context: DecisionOSContext) -> tuple[ImportDraft, ...]:
        from google.cloud import firestore

        @firestore.transactional
        def load(transaction):
            current = self._authorize_transaction(
                transaction,
                context,
                DecisionOSPermission.MANAGE_MEMBERS,
            )
            rows = (
                self._organization_ref(current.organization_id)
                .collection("imports")
                .stream(transaction=transaction)
            )
            drafts = []
            for row in rows:
                draft = self._draft_from_row(
                    row,
                    self._import_ref(current.organization_id, row.id),
                    organization_id=current.organization_id,
                    transaction=transaction,
                )
                self._import_state(
                    row.to_dict(),
                    draft,
                    transaction=transaction,
                    organization_id=current.organization_id,
                )
                drafts.append(draft)
            return tuple(sorted(drafts, key=lambda item: item.import_id))

        return load(self._client.transaction())

    @_firestore_error_barrier(ImportUnavailable)
    def require_latest_import(
        self,
        context: DecisionOSContext,
        draft_id: str,
    ) -> None:
        from google.cloud import firestore

        draft_ref = self._import_ref(context.organization_id, draft_id)

        @firestore.transactional
        def require_latest(transaction):
            current = self._authorize_transaction(
                transaction,
                context,
                DecisionOSPermission.MANAGE_MEMBERS,
            )
            row = draft_ref.get(transaction=transaction)
            if not row.exists:
                raise ImportUnavailable()
            draft = self._draft_from_row(
                row,
                draft_ref,
                organization_id=current.organization_id,
                transaction=transaction,
            )
            payload = row.to_dict()
            receipt = self._import_state(
                payload,
                draft,
                transaction=transaction,
                organization_id=current.organization_id,
            )
            if receipt is not None:
                return
            lineage_ref = self._lineage_ref(
                current.organization_id,
                draft.source_snapshot.source_kind,
            )
            latest_import_id, legacy_lineage = self._lineage_or_legacy_latest(
                transaction,
                current.organization_id,
                draft.source_snapshot.source_kind,
                lineage_ref.get(transaction=transaction),
            )
            if latest_import_id != draft.import_id:
                raise ImportLineageConflict()
            if legacy_lineage is not None:
                transaction.set(lineage_ref, legacy_lineage)

        require_latest(self._client.transaction())

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
        acknowledged_codes: tuple[str, ...] = (),
    ) -> ImportReceipt:
        from google.cloud import firestore

        acknowledged_codes = _canonical_acknowledgements(acknowledged_codes)
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
            receipt = self._import_state(
                payload,
                draft,
                transaction=transaction,
                organization_id=current.organization_id,
            )
            if receipt is not None:
                if receipt.acknowledged_codes != acknowledged_codes:
                    raise ImportUnavailable() from None
                return receipt
            lineage_ref = self._lineage_ref(
                current.organization_id,
                draft.source_snapshot.source_kind,
            )
            latest_import_id, legacy_lineage = self._lineage_or_legacy_latest(
                transaction,
                current.organization_id,
                draft.source_snapshot.source_kind,
                lineage_ref.get(transaction=transaction),
            )
            if latest_import_id != draft.import_id:
                raise ImportLineageConflict()
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
                acknowledged_codes=acknowledged_codes,
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
                + (1 if legacy_lineage is not None else 0)
            )
            if legacy_lineage is not None:
                transaction.set(lineage_ref, legacy_lineage)
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

    @_firestore_error_barrier(InvitationUnavailable)
    def create_subject_invitations(
        self,
        decisionos: DecisionOSRepository,
        context: DecisionOSContext,
        *,
        subject_ids: tuple[str, ...],
        role: DecisionOSRole,
        expires_in: timedelta,
        delivery_route_id: str | None,
    ) -> tuple[SubjectInvitationGrant, ...]:
        def mutate(
            transaction,
            current: DecisionOSContext,
        ) -> _FirestorePreparedMutation:
            if (
                current.organization_id != context.organization_id
                or current.principal.uid != context.principal.uid
            ):
                raise InvitationUnavailable()
            state_row = self._state_ref(current.organization_id).get(
                transaction=transaction
            )
            version, state = self._state_from_row(current.organization_id, state_row)
            if version == 0:
                raise InvitationUnavailable()
            graph_row = self._version_ref(current.organization_id, version).get(
                transaction=transaction
            )
            graph, storage = self._graph_from_row(
                current.organization_id,
                version,
                graph_row,
                transaction=transaction,
            )
            if storage["payload_digest"] != state["payload_digest"]:
                raise InvitationUnavailable()
            by_id = {subject.subject_id: subject for subject in graph.subjects}
            selected = tuple(by_id.get(subject_id) for subject_id in subject_ids)
            if (
                len(by_id) != len(graph.subjects)
                or any(
                    subject is None
                    or subject.kind is not OrganizationSubjectKind.HUMAN
                    or subject.member_uid is not None
                    for subject in selected
                )
            ):
                raise InvitationUnavailable()
            lifecycles = {subject.lifecycle for subject in selected if subject is not None}
            if lifecycles == {SubjectLifecycle.INVITED}:
                return _FirestorePreparedMutation(write_count=0, publish=lambda: None)
            if lifecycles != {SubjectLifecycle.DIRECTORY_ONLY}:
                raise InvitationUnavailable()
            now = _aware(self._clock())
            invited_by_id = {
                subject.subject_id: _validated_subject(
                    subject,
                    {"lifecycle": SubjectLifecycle.INVITED},
                )
                for subject in selected
                if subject is not None
            }
            invited_graph = OrganizationGraph(
                organization_id=graph.organization_id,
                version=version + 1,
                subjects=tuple(
                    invited_by_id.get(subject.subject_id, subject)
                    for subject in graph.subjects
                ),
                units=graph.units,
                edges=graph.edges,
                authority_assignments=graph.authority_assignments,
                created_at=now,
            )
            transition = _activation_transition(
                organization_id=current.organization_id,
                kind="invitations_created",
                subject_ids=subject_ids,
                prior_graph_version=version,
                member_uid=None,
                occurred_at=now,
            )
            organization_ref = self._organization_ref(current.organization_id)
            version_ref = self._version_ref(current.organization_id, invited_graph.version)
            transition_ref = self._activation_transition_ref(
                current.organization_id,
                invited_graph.version,
            )
            if (
                version_ref.get(transaction=transaction).exists
                or transition_ref.get(transaction=transaction).exists
            ):
                raise InvitationUnavailable()
            graph_storage, graph_chunks = _chunked_graph(invited_graph)
            state_payload = {
                "schema_version": 1,
                "organization_id": current.organization_id,
                "current_version": invited_graph.version,
                "current_version_id": f"{invited_graph.version:020d}",
                "payload_digest": graph_storage["payload_digest"],
                "updated_at": now,
            }
            transition_payload = transition.model_dump(mode="python")
            self._preflight_graph_documents(
                version_ref,
                organization_ref,
                graph_storage,
                graph_chunks,
            )
            _require_document_bound(self._state_ref(current.organization_id), state_payload)
            _require_document_bound(transition_ref, transition_payload)
            extra_current_deletes = self._extra_current_chunk_count(
                storage,
                graph_storage,
            )
            write_count = 3 + (2 * len(graph_chunks)) + extra_current_deletes

            def publish() -> None:
                transaction.create(version_ref, graph_storage)
                for chunk_id, chunk in graph_chunks.items():
                    transaction.create(
                        version_ref.collection("chunks").document(chunk_id),
                        chunk,
                    )
                self._write_current_graph(
                    transaction,
                    organization_ref,
                    storage,
                    graph_storage,
                    graph_chunks,
                )
                transaction.set(self._state_ref(current.organization_id), state_payload)
                transaction.create(transition_ref, transition_payload)

            return _FirestorePreparedMutation(
                write_count=write_count,
                publish=publish,
            )

        return decisionos.create_subject_invitations(
            context,
            subject_ids=subject_ids,
            role=role,
            expires_in=expires_in,
            delivery_route_id=delivery_route_id,
            mutation=mutate,
        )

    @_firestore_error_barrier(InvitationUnavailable)
    def accept_subject_invitation(
        self,
        decisionos: DecisionOSRepository,
        principal: DecisionOSPrincipal,
        token: str,
    ) -> tuple[OrganizationMembership, OrganizationSubject]:
        membership, subject = decisionos.accept_subject_invitation(
            principal,
            token,
            mutation=self._prepare_subject_invitation_acceptance,
        )
        if (
            type(membership) is not OrganizationMembership
            or type(subject) is not OrganizationSubject
            or membership.organization_id != subject.organization_id
            or membership.uid != subject.member_uid
        ):
            raise InvitationUnavailable()
        return membership, subject

    def _prepare_subject_invitation_acceptance(
        self,
        transaction,
        context: DecisionOSContext,
        subject_id: str,
    ) -> _FirestorePreparedMutation:
        if re.fullmatch(_SUBJECT_ID, subject_id) is None:
            raise InvitationUnavailable()
        organization_ref = self._organization_ref(context.organization_id)
        state_ref = self._state_ref(context.organization_id)
        state_row = state_ref.get(transaction=transaction)
        version, state = self._state_from_row(context.organization_id, state_row)
        if version == 0:
            raise InvitationUnavailable()
        prior_ref = self._version_ref(context.organization_id, version)
        prior_row = prior_ref.get(transaction=transaction)
        prior, prior_storage = self._graph_from_row(
            context.organization_id,
            version,
            prior_row,
            transaction=transaction,
        )
        if prior_storage["payload_digest"] != state["payload_digest"]:
            raise InvitationUnavailable()
        subject = next(
            (item for item in prior.subjects if item.subject_id == subject_id),
            None,
        )
        if (
            subject is None
            or subject.kind is not OrganizationSubjectKind.HUMAN
            or subject.lifecycle is not SubjectLifecycle.INVITED
            or subject.member_uid is not None
            or any(item.member_uid == context.principal.uid for item in prior.subjects)
        ):
            raise InvitationUnavailable()
        bound = _validated_subject(
            subject,
            {
                "member_uid": context.principal.uid,
                "lifecycle": SubjectLifecycle.ACTIVE,
            },
        )
        target_subjects = tuple(
            bound if item.subject_id == subject_id else item for item in prior.subjects
        )
        if not _member_binding_is_allowed(
            subject,
            bound,
            target_subjects=target_subjects,
        ):
            raise InvitationUnavailable()
        now = _aware(self._clock())
        graph = OrganizationGraph(
            organization_id=prior.organization_id,
            version=version + 1,
            subjects=target_subjects,
            units=prior.units,
            edges=prior.edges,
            authority_assignments=prior.authority_assignments,
            created_at=now,
        )
        event = _binding_event(context, version, bound, now)
        transition = _activation_transition(
            organization_id=context.organization_id,
            kind="invitation_accepted",
            subject_ids=(subject_id,),
            prior_graph_version=version,
            member_uid=context.principal.uid,
            occurred_at=now,
        )
        graph_storage, graph_chunks = _chunked_graph(graph)
        version_ref = self._version_ref(context.organization_id, graph.version)
        transition_ref = self._activation_transition_ref(
            context.organization_id,
            graph.version,
        )
        if (
            version_ref.get(transaction=transaction).exists
            or transition_ref.get(transaction=transaction).exists
        ):
            raise InvitationUnavailable()
        state_payload = {
            "schema_version": 1,
            "organization_id": context.organization_id,
            "current_version": graph.version,
            "current_version_id": f"{graph.version:020d}",
            "payload_digest": graph_storage["payload_digest"],
            "updated_at": now,
        }
        event_ref = self._client.collection(self._audit_collection).document(
            event.event_id
        )
        self._preflight_graph_documents(
            version_ref,
            organization_ref,
            graph_storage,
            graph_chunks,
        )
        _require_document_bound(state_ref, state_payload)
        _require_document_bound(event_ref, event.model_dump(mode="python"))
        _require_document_bound(transition_ref, transition.model_dump(mode="python"))
        extra_current_deletes = self._extra_current_chunk_count(
            prior_storage,
            graph_storage,
        )
        event_payload = event.model_dump(mode="python")
        transition_payload = transition.model_dump(mode="python")
        write_count = 4 + (2 * len(graph_chunks)) + extra_current_deletes

        def publish() -> None:
            transaction.create(version_ref, graph_storage)
            for chunk_id, chunk in graph_chunks.items():
                transaction.create(
                    version_ref.collection("chunks").document(chunk_id),
                    chunk,
                )
            self._write_current_graph(
                transaction,
                organization_ref,
                prior_storage,
                graph_storage,
                graph_chunks,
            )
            transaction.set(state_ref, state_payload)
            transaction.create(event_ref, event_payload)
            transaction.create(transition_ref, transition_payload)

        return _FirestorePreparedMutation(
            write_count=write_count,
            publish=publish,
            result=bound,
        )

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
            target_subjects = tuple(
                bound if item.subject_id == subject_id else item
                for item in prior.subjects
            )
            if not _member_binding_is_allowed(
                subject,
                bound,
                target_subjects=target_subjects,
            ):
                raise OrganizationUnavailable()
            now = _aware(self._clock())
            graph = OrganizationGraph(
                organization_id=prior.organization_id,
                version=version + 1,
                subjects=target_subjects,
                units=prior.units,
                edges=prior.edges,
                authority_assignments=prior.authority_assignments,
                created_at=now,
            )
            event = _binding_event(current, version, bound, now)
            transition = _activation_transition(
                organization_id=current.organization_id,
                kind="invitation_accepted",
                subject_ids=(subject_id,),
                prior_graph_version=version,
                member_uid=member_uid,
                occurred_at=now,
            )
            graph_storage, graph_chunks = _chunked_graph(graph)
            version_ref = self._version_ref(current.organization_id, graph.version)
            transition_ref = self._activation_transition_ref(
                current.organization_id,
                graph.version,
            )
            if transition_ref.get(transaction=transaction).exists:
                raise OrganizationUnavailable()
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
            _require_document_bound(transition_ref, transition.model_dump(mode="python"))
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
            transaction.create(transition_ref, transition.model_dump(mode="python"))
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

    def _lineage_from_row(
        self,
        organization_id: str,
        source_kind: str,
        row,
    ) -> str | None:
        if not row.exists:
            return None
        payload = row.to_dict()
        if (
            set(payload) != _LINEAGE_STORAGE_FIELDS
            or not self._storage_digest_valid(payload)
            or type(payload.get("schema_version")) is not int
            or payload.get("schema_version") != 1
            or payload.get("organization_id") != organization_id
            or payload.get("source_kind") != source_kind
            or re.fullmatch(_IMPORT_ID, payload.get("latest_import_id", "")) is None
        ):
            raise ImportUnavailable()
        return payload["latest_import_id"]

    def _lineage_or_legacy_latest(
        self,
        transaction,
        organization_id: str,
        source_kind: str,
        lineage_row,
    ) -> tuple[str | None, dict[str, Any] | None]:
        latest_import_id = self._lineage_from_row(
            organization_id,
            source_kind,
            lineage_row,
        )
        if latest_import_id is not None:
            return latest_import_id, None
        candidates: list[ImportDraft] = []
        rows = (
            self._organization_ref(organization_id)
            .collection("imports")
            .stream(transaction=transaction)
        )
        for row in rows:
            payload = row.to_dict()
            snapshot_payload = payload.get("source_snapshot")
            if (
                not isinstance(snapshot_payload, dict)
                or snapshot_payload.get("source_kind") != source_kind
            ):
                continue
            if (
                type(payload.get("schema_version")) is not int
                or payload.get("schema_version") != 1
            ):
                raise ImportUnavailable()
            draft = self._draft_from_row(
                row,
                self._import_ref(organization_id, row.id),
                organization_id=organization_id,
                transaction=transaction,
            )
            receipt = self._import_state(
                payload,
                draft,
                transaction=transaction,
                organization_id=organization_id,
            )
            if receipt is None:
                candidates.append(draft)
        if not candidates:
            return None, None
        latest = max(candidates, key=lambda item: (item.created_at, item.import_id))
        return latest.import_id, _lineage_payload(latest)

    def _draft_from_payload(
        self,
        payload: dict[str, Any],
        *,
        chunks: dict[str, tuple[dict[str, Any], ...]],
    ) -> ImportDraft:
        draft = None
        try:
            schema_version = payload.get("schema_version")
            expected_fields = {
                1: _DRAFT_V1_STORAGE_FIELDS,
                2: _DRAFT_V2_STORAGE_FIELDS,
            }.get(schema_version) if type(schema_version) is int else None
            if (
                expected_fields is None
                or set(payload) != expected_fields
                or not self._storage_digest_valid(payload)
            ):
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
                    or type(chunk.get("schema_version")) is not int
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

    def _receipt_matches_draft(
        self,
        receipt: ImportReceipt,
        draft: ImportDraft,
        *,
        acknowledged_codes: tuple[str, ...],
    ) -> bool:
        return _receipt_matches_draft(
            receipt,
            draft,
            acknowledged_codes=acknowledged_codes,
        )

    def _committed_graph_for_receipt(
        self,
        transaction,
        organization_id: str,
        draft: ImportDraft,
        receipt: ImportReceipt,
    ) -> OrganizationGraph:
        canonical_draft = exact_canonical_model(draft, ImportDraft)
        canonical_receipt = exact_canonical_model(receipt, ImportReceipt)
        if canonical_draft is None or canonical_receipt is None:
            raise ImportUnavailable()
        try:
            state_row = self._state_ref(organization_id).get(transaction=transaction)
            current_version, state = self._state_from_row(organization_id, state_row)
            if current_version < canonical_receipt.graph_version:
                raise ImportUnavailable()
            base_version = canonical_draft.base_graph_version
            if base_version == 0:
                base_graph = OrganizationGraph(
                    organization_id=organization_id,
                    version=0,
                    created_at=canonical_draft.created_at,
                )
            else:
                base_row = self._version_ref(organization_id, base_version).get(
                    transaction=transaction
                )
                base_graph, _base_storage = self._graph_from_row(
                    organization_id,
                    base_version,
                    base_row,
                    transaction=transaction,
                )
            version_row = self._version_ref(
                organization_id,
                canonical_receipt.graph_version,
            ).get(transaction=transaction)
            graph, storage = self._graph_from_row(
                organization_id,
                canonical_receipt.graph_version,
                version_row,
                transaction=transaction,
            )
        except OrganizationUnavailable:
            raise ImportUnavailable() from None
        canonical_base = exact_canonical_model(base_graph, OrganizationGraph)
        canonical_graph = exact_canonical_model(graph, OrganizationGraph)
        if (
            canonical_base is None
            or canonical_base.organization_id != organization_id
            or canonical_base.version != canonical_draft.base_graph_version
            or canonical_receipt.graph_version != canonical_draft.base_graph_version + 1
            or not validate_organization_graph(canonical_base).committable
        ):
            raise ImportUnavailable()
        expected_graph, _carried_member_uids, _removed_member_uids = _committed_graph(
            canonical_draft,
            canonical_base,
            version=canonical_receipt.graph_version,
            created_at=canonical_receipt.committed_at,
        )
        canonical_expected = exact_canonical_model(expected_graph, OrganizationGraph)
        if (
            canonical_expected is None
            or canonical_graph is None
            or canonical_graph.organization_id != organization_id
            or canonical_graph.version != canonical_receipt.graph_version
            or not validate_organization_graph(canonical_expected).committable
            or not validate_organization_graph(canonical_graph).committable
            or canonical_receipt.committed_subject_count
            != len(canonical_draft.candidate.subjects)
            or not exact_canonical_equal(
                canonical_expected,
                canonical_graph,
                OrganizationGraph,
            )
            or (
                current_version == canonical_receipt.graph_version
                and storage["payload_digest"] != state["payload_digest"]
            )
        ):
            raise ImportUnavailable()
        return canonical_graph

    def _import_state(
        self,
        payload: dict[str, Any],
        draft: ImportDraft,
        *,
        transaction,
        organization_id: str,
    ) -> ImportReceipt | None:
        status = payload.get("status")
        if type(status) is not str:
            raise ImportUnavailable()
        if status == "draft":
            if payload.get("receipt") is not None:
                raise ImportUnavailable()
            return None
        if status != "committed":
            raise ImportUnavailable()
        raw_receipt = payload.get("receipt")
        raw_codes = (
            raw_receipt.get("acknowledged_codes")
            if isinstance(raw_receipt, dict)
            else None
        )
        if not isinstance(raw_codes, (list, tuple)) or any(
            type(code) is not str for code in raw_codes
        ):
            raise ImportUnavailable()
        receipt = None
        try:
            parsed = ImportReceipt.model_validate_json(to_json(raw_receipt), strict=True)
            receipt = exact_canonical_model(parsed, ImportReceipt)
        except (
            PydanticSerializationError,
            TypeError,
            ValueError,
            ValidationError,
        ):
            pass
        if receipt is None or not self._receipt_matches_draft(
            receipt,
            draft,
            acknowledged_codes=receipt.acknowledged_codes,
        ):
            raise ImportUnavailable() from None
        self._committed_graph_for_receipt(
            transaction,
            organization_id,
            draft,
            receipt,
        )
        return receipt

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
