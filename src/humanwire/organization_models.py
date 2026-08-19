"""Immutable organization, authority, import, and browser projection contracts."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from humanwire.decisionos_models import _FIREBASE_UID, _ORGANIZATION_ID

_ULID = r"[0-9A-HJKMNP-TV-Z]{26}"
_SUBJECT_ID = rf"^sub_{_ULID}$"
_UNIT_ID = rf"^unit_{_ULID}$"
_EDGE_ID = rf"^edge_{_ULID}$"
_ASSIGNMENT_ID = rf"^auth_{_ULID}$"
_RECORD_ID = rf"^rec_{_ULID}$"
_SNAPSHOT_ID = rf"^snap_{_ULID}$"
_IMPORT_ID = rf"^imp_{_ULID}$"
_RECEIPT_ID = rf"^rcp_{_ULID}$"
_WORKSPACE_ID = rf"^wrk_{_ULID}$"
_SPECIALIST_KEY = r"^[a-z][a-z0-9_]{0,63}$"
_SOURCE_IDENTITY = r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$"
_DECISION_TYPE = r"^[a-z][a-z0-9_]{0,63}$"
_SHA256 = r"^[0-9a-f]{64}$"
_FIELD_NAME = r"^[a-z][a-z0-9_]{0,63}$"


class _OrganizationModel(BaseModel):
    """Strict, immutable wire contracts with deterministic JSON-compatible dumps."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class OrganizationSubjectKind(StrEnum):
    HUMAN = "human"
    AI_SPECIALIST = "ai_specialist"
    EXTERNAL = "external"
    SERVICE = "service"


class SubjectLifecycle(StrEnum):
    DRAFT_IMPORTED = "draft_imported"
    DIRECTORY_ONLY = "directory_only"
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    NEEDS_REVIEW = "needs_review"


class OrganizationEdgeKind(StrEnum):
    MEMBER_OF = "member_of"
    REPORTS_TO = "reports_to"
    COLLABORATES_WITH = "collaborates_with"


class AuthorityFunction(StrEnum):
    DECISION_OWNER = "decision_owner"
    EVIDENCE_CONTRIBUTOR = "evidence_contributor"
    RECOMMENDER = "recommender"
    CHALLENGER = "challenger"
    APPROVER = "approver"
    EXECUTION_OWNER = "execution_owner"
    OBSERVER = "observer"


def _safe_display_text(value: str) -> str:
    """Accept human-readable text without control or compatibility separators."""

    normalized = unicodedata.normalize("NFC", value)
    if normalized != normalized.strip():
        raise ValueError("display text must not have surrounding whitespace")
    for character in normalized:
        category = unicodedata.category(character)
        if category.startswith("C") or (category.startswith("Z") and character != " "):
            raise ValueError("display text contains an unsafe character")
    return normalized


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value


def _unique(values: tuple[str, ...], label: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must be unique")


def _validate_graph_records(
    organization_id: str,
    subjects: tuple[OrganizationSubject, ...],
    units: tuple[OrganizationUnit, ...],
    edges: tuple[OrganizationEdge, ...],
    authority_assignments: tuple[AuthorityAssignment, ...],
) -> None:
    _unique(tuple(subject.subject_id for subject in subjects), "subject IDs")
    _unique(tuple(unit.unit_id for unit in units), "unit IDs")
    _unique(tuple(edge.edge_id for edge in edges), "edge IDs")
    _unique(
        tuple(item.assignment_id for item in authority_assignments),
        "authority assignment IDs",
    )
    source_identities = tuple(
        subject.source_identity
        for subject in subjects
        if subject.source_identity is not None
    )
    _unique(source_identities, "subject source identities")
    for record in (*subjects, *units, *edges, *authority_assignments):
        if record.organization_id != organization_id:
            raise ValueError("organization graph contains a cross-tenant record")


class OrganizationSubject(_OrganizationModel):
    subject_id: str = Field(pattern=_SUBJECT_ID)
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    kind: OrganizationSubjectKind = Field(strict=False)
    lifecycle: SubjectLifecycle = Field(strict=False)
    display_name: str = Field(min_length=1, max_length=120)
    source_identity: str | None = Field(default=None, pattern=_SOURCE_IDENTITY)
    member_uid: str | None = Field(default=None, pattern=_FIREBASE_UID)
    specialist_key: str | None = Field(default=None, pattern=_SPECIALIST_KEY)
    unit_id: str | None = Field(default=None, pattern=_UNIT_ID)
    title: str | None = Field(default=None, max_length=120)

    @field_validator("display_name", "title")
    @classmethod
    def display_text_is_safe(cls, value: str | None) -> str | None:
        return None if value is None else _safe_display_text(value)

    @model_validator(mode="after")
    def has_a_kind_appropriate_lifecycle_and_identity(self) -> Self:
        if self.kind is OrganizationSubjectKind.HUMAN:
            if self.specialist_key is not None:
                raise ValueError("human subjects cannot have a specialist key")
            if self.lifecycle is SubjectLifecycle.ACTIVE and self.member_uid is None:
                raise ValueError("active human subjects must bind a member UID")
            if (
                self.lifecycle
                in {
                    SubjectLifecycle.DRAFT_IMPORTED,
                    SubjectLifecycle.DIRECTORY_ONLY,
                    SubjectLifecycle.INVITED,
                    SubjectLifecycle.NEEDS_REVIEW,
                }
                and self.member_uid is not None
            ):
                raise ValueError("this human lifecycle cannot bind a member UID")
            return self

        if self.member_uid is not None:
            raise ValueError("non-human subjects cannot bind a member UID")
        if self.kind is OrganizationSubjectKind.AI_SPECIALIST:
            if self.specialist_key is None:
                raise ValueError("AI specialists require a specialist key")
            if self.lifecycle is not SubjectLifecycle.ACTIVE:
                raise ValueError("AI specialists must use the active lifecycle")
        elif self.specialist_key is not None:
            raise ValueError("only AI specialists may have a specialist key")
        return self


class OrganizationUnit(_OrganizationModel):
    unit_id: str = Field(pattern=_UNIT_ID)
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    name: str = Field(min_length=1, max_length=120)
    parent_unit_id: str | None = Field(default=None, pattern=_UNIT_ID)
    leader_subject_id: str | None = Field(default=None, pattern=_SUBJECT_ID)

    @field_validator("name")
    @classmethod
    def name_is_safe(cls, value: str) -> str:
        return _safe_display_text(value)

    @model_validator(mode="after")
    def does_not_parent_itself(self) -> Self:
        if self.parent_unit_id == self.unit_id:
            raise ValueError("a unit cannot parent itself")
        return self


class OrganizationEdge(_OrganizationModel):
    edge_id: str = Field(pattern=_EDGE_ID)
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    kind: OrganizationEdgeKind = Field(strict=False)
    source_subject_id: str = Field(pattern=_SUBJECT_ID)
    target_subject_id: str | None = Field(default=None, pattern=_SUBJECT_ID)
    target_unit_id: str | None = Field(default=None, pattern=_UNIT_ID)
    is_primary: bool = False

    @model_validator(mode="after")
    def is_a_typed_non_authority_relation(self) -> Self:
        if self.kind is OrganizationEdgeKind.MEMBER_OF:
            if self.target_unit_id is None or self.target_subject_id is not None:
                raise ValueError("membership edges must target exactly one unit")
            if self.is_primary:
                raise ValueError("membership edges cannot be primary managers")
        else:
            if self.target_subject_id is None or self.target_unit_id is not None:
                raise ValueError("subject edges must target exactly one subject")
            if self.source_subject_id == self.target_subject_id:
                raise ValueError("an organization edge cannot reference itself")
            if self.kind is not OrganizationEdgeKind.REPORTS_TO and self.is_primary:
                raise ValueError("only reporting edges can be primary managers")
        return self


class AuthorityAssignment(_OrganizationModel):
    assignment_id: str = Field(pattern=_ASSIGNMENT_ID)
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    subject_id: str = Field(pattern=_SUBJECT_ID)
    workspace_id: str | None = Field(default=None, pattern=_WORKSPACE_ID)
    decision_type: str = Field(pattern=_DECISION_TYPE)
    function: AuthorityFunction = Field(strict=False)
    effective_from: datetime
    effective_until: datetime | None = None
    policy_version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def has_a_valid_effective_interval(self) -> Self:
        _aware(self.effective_from, "effective_from")
        if self.effective_until is not None:
            _aware(self.effective_until, "effective_until")
            if self.effective_until <= self.effective_from:
                raise ValueError("effective_until must be after effective_from")
        return self


class AuthorityRequest(_OrganizationModel):
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    subject_id: str = Field(pattern=_SUBJECT_ID)
    workspace_id: str | None = Field(default=None, pattern=_WORKSPACE_ID)
    decision_type: str = Field(pattern=_DECISION_TYPE)
    function: AuthorityFunction = Field(strict=False)
    occurred_at: datetime

    @model_validator(mode="after")
    def occurred_at_is_aware(self) -> Self:
        _aware(self.occurred_at, "occurred_at")
        return self


class AuthorityDecision(_OrganizationModel):
    allowed: bool
    reason: str | None = Field(default=None, min_length=1, max_length=120)
    assignment_id: str | None = Field(default=None, pattern=_ASSIGNMENT_ID)

    @model_validator(mode="after")
    def has_an_unambiguous_outcome(self) -> Self:
        if self.allowed and (self.reason is not None or self.assignment_id is None):
            raise ValueError("allowed decisions require exactly one assignment")
        if not self.allowed and (self.reason is None or self.assignment_id is not None):
            raise ValueError("denied decisions require a reason and no assignment")
        return self


class SourceRecord(_OrganizationModel):
    record_id: str = Field(pattern=_RECORD_ID)
    source_ordinal: int = Field(ge=1, le=5_000)
    source_identity: str = Field(pattern=_SOURCE_IDENTITY)
    fields: tuple[tuple[str, str], ...] = Field(min_length=1)

    @field_validator("fields")
    @classmethod
    def has_unique_canonical_field_names(
        cls,
        fields: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, str], ...]:
        field_names = tuple(field_name for field_name, _ in fields)
        _unique(field_names, "source record field names")
        normalized_fields: list[tuple[str, str]] = []
        for field_name, value in fields:
            if re.fullmatch(_FIELD_NAME, field_name) is None:
                raise ValueError("source record field name is invalid")
            if not value or len(value) > 120:
                raise ValueError("source record field value is invalid")
            normalized_fields.append((field_name, _safe_display_text(value)))
        return tuple(normalized_fields)


class SourceSnapshot(_OrganizationModel):
    snapshot_id: str = Field(pattern=_SNAPSHOT_ID)
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    source_kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    captured_at: datetime
    records: tuple[SourceRecord, ...] = Field(max_length=5_000)
    semantic_digest: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def is_an_aware_unique_bounded_snapshot(self) -> Self:
        _aware(self.captured_at, "captured_at")
        _unique(tuple(record.record_id for record in self.records), "source record IDs")
        _unique(
            tuple(record.source_identity for record in self.records),
            "source identities",
        )
        _unique(
            tuple(str(record.source_ordinal) for record in self.records),
            "source ordinals",
        )
        return self


class OrganizationGraph(_OrganizationModel):
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    version: int = Field(ge=0)
    subjects: tuple[OrganizationSubject, ...] = ()
    units: tuple[OrganizationUnit, ...] = ()
    edges: tuple[OrganizationEdge, ...] = ()
    authority_assignments: tuple[AuthorityAssignment, ...] = ()
    created_at: datetime

    @model_validator(mode="after")
    def has_unique_tenant_bound_records(self) -> Self:
        _aware(self.created_at, "created_at")
        _validate_graph_records(
            self.organization_id,
            self.subjects,
            self.units,
            self.edges,
            self.authority_assignments,
        )
        return self


class OrganizationGraphCandidate(_OrganizationModel):
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    source_snapshot_id: str = Field(pattern=_SNAPSHOT_ID)
    subjects: tuple[OrganizationSubject, ...] = ()
    units: tuple[OrganizationUnit, ...] = ()
    edges: tuple[OrganizationEdge, ...] = ()
    authority_assignments: tuple[AuthorityAssignment, ...] = ()

    @model_validator(mode="after")
    def is_tenant_bound_and_duplicate_free(self) -> Self:
        _validate_graph_records(
            self.organization_id,
            self.subjects,
            self.units,
            self.edges,
            self.authority_assignments,
        )
        return self


class ImportDraft(_OrganizationModel):
    import_id: str = Field(pattern=_IMPORT_ID)
    supersedes_import_id: str | None = Field(default=None, pattern=_IMPORT_ID)
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    source_snapshot: SourceSnapshot
    candidate: OrganizationGraphCandidate
    base_graph_version: int = Field(ge=0)
    semantic_digest: str = Field(pattern=_SHA256)
    created_at: datetime

    @model_validator(mode="after")
    def binds_one_snapshot_candidate_and_tenant(self) -> Self:
        _aware(self.created_at, "created_at")
        if self.supersedes_import_id == self.import_id:
            raise ValueError("an import cannot supersede itself")
        if self.source_snapshot.organization_id != self.organization_id:
            raise ValueError("import draft snapshot is cross-tenant")
        if self.candidate.organization_id != self.organization_id:
            raise ValueError("import draft candidate is cross-tenant")
        if self.candidate.source_snapshot_id != self.source_snapshot.snapshot_id:
            raise ValueError("import draft candidate snapshot does not match")
        return self


class ImportReconciliation(_OrganizationModel):
    import_id: str = Field(pattern=_IMPORT_ID)
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    source_count: int = Field(ge=0, le=5_000)
    normalized_count: int = Field(ge=0, le=5_000)
    rejected_count: int = Field(ge=0, le=5_000)
    lifecycle_counts: tuple[tuple[SubjectLifecycle, int], ...] = ()
    blocking_codes: tuple[str, ...] = ()
    acknowledged_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def has_reconcilable_counts_and_codes(self) -> Self:
        lifecycle_names = tuple(item.value for item, _ in self.lifecycle_counts)
        _unique(lifecycle_names, "lifecycle counts")
        _unique(self.blocking_codes, "blocking codes")
        _unique(self.acknowledged_codes, "acknowledged codes")
        if self.normalized_count + self.rejected_count != self.source_count:
            raise ValueError("reconciliation counts must equal source count")
        if any(count < 0 for _, count in self.lifecycle_counts):
            raise ValueError("lifecycle counts cannot be negative")
        if sum(count for _, count in self.lifecycle_counts) != self.normalized_count:
            raise ValueError("lifecycle counts must equal normalized count")
        return self

    @property
    def committable(self) -> bool:
        return not self.blocking_codes


class CommitImportRequest(_OrganizationModel):
    import_id: str = Field(pattern=_IMPORT_ID)
    reviewed_digest: str = Field(pattern=_SHA256)
    acknowledged_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def has_unique_acknowledgements(self) -> Self:
        _unique(self.acknowledged_codes, "acknowledged codes")
        return self


class ImportReceipt(_OrganizationModel):
    receipt_id: str = Field(pattern=_RECEIPT_ID)
    import_id: str = Field(pattern=_IMPORT_ID)
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    source_snapshot_id: str = Field(pattern=_SNAPSHOT_ID)
    source_snapshot_digest: str = Field(pattern=_SHA256)
    graph_version: int = Field(ge=1)
    committed_subject_count: int = Field(ge=0)
    acknowledged_codes: tuple[str, ...] = ()
    committed_at: datetime
    committed_by_uid: str = Field(pattern=_FIREBASE_UID)

    @field_validator("acknowledged_codes")
    @classmethod
    def acknowledgements_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or value != tuple(sorted(value)):
            raise ValueError("acknowledged codes must be sorted and unique")
        if any(re.fullmatch(_FIELD_NAME, code) is None for code in value):
            raise ValueError("acknowledged code is invalid")
        return value

    @model_validator(mode="after")
    def committed_at_is_aware(self) -> Self:
        _aware(self.committed_at, "committed_at")
        return self


class OrganizationProjectionSubject(_OrganizationModel):
    """Browser-safe view of a subject without source or Firebase bindings."""

    subject_id: str = Field(pattern=_SUBJECT_ID)
    kind: OrganizationSubjectKind = Field(strict=False)
    lifecycle: SubjectLifecycle = Field(strict=False)
    display_name: str = Field(min_length=1, max_length=120)
    unit_id: str | None = Field(default=None, pattern=_UNIT_ID)
    title: str | None = Field(default=None, max_length=120)

    @field_validator("display_name", "title")
    @classmethod
    def display_text_is_safe(cls, value: str | None) -> str | None:
        return None if value is None else _safe_display_text(value)

    @model_validator(mode="after")
    def has_kind_appropriate_lifecycle(self) -> Self:
        if (
            self.kind is OrganizationSubjectKind.AI_SPECIALIST
            and self.lifecycle is not SubjectLifecycle.ACTIVE
        ):
            raise ValueError("AI specialists must use the active lifecycle")
        return self


class OrganizationProjection(_OrganizationModel):
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    graph_version: int = Field(ge=0)
    subjects: tuple[OrganizationProjectionSubject, ...] = ()
    units: tuple[OrganizationUnit, ...] = ()
    edges: tuple[OrganizationEdge, ...] = ()
    authority_assignments: tuple[AuthorityAssignment, ...] = ()
    source_kind: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]{0,31}$",
    )
    synchronized_at: datetime | None = None
    reconciliation: ImportReconciliation | None = None
    generated_at: datetime

    @model_validator(mode="after")
    def is_an_aware_tenant_bound_projection(self) -> Self:
        _aware(self.generated_at, "generated_at")
        if self.synchronized_at is not None:
            _aware(self.synchronized_at, "synchronized_at")
        if (
            self.reconciliation is not None
            and self.reconciliation.organization_id != self.organization_id
        ):
            raise ValueError("projection reconciliation is cross-tenant")
        _unique(tuple(subject.subject_id for subject in self.subjects), "subject IDs")
        _unique(tuple(unit.unit_id for unit in self.units), "unit IDs")
        _unique(tuple(edge.edge_id for edge in self.edges), "edge IDs")
        _unique(
            tuple(item.assignment_id for item in self.authority_assignments),
            "authority assignment IDs",
        )
        for record in (*self.units, *self.edges, *self.authority_assignments):
            if record.organization_id != self.organization_id:
                raise ValueError("organization projection contains a cross-tenant record")
        return self
