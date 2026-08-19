"""Deterministic organization import mapping, review, and commit orchestration."""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import unicodedata
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic_core import to_json

from humanwire.decisionos_models import DecisionOSContext
from humanwire.organization_graph import validate_organization_graph
from humanwire.organization_models import (
    AuthorityAssignment,
    AuthorityFunction,
    CommitImportRequest,
    ImportDraft,
    ImportReceipt,
    ImportReconciliation,
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
from humanwire.organization_store import ImportUnavailable, OrganizationGraphRepository

_ULID = r"[0-9A-HJKMNP-TV-Z]{26}"
_IMPORT_ID = rf"^imp_{_ULID}$"
_RECORD_ID = rf"^rec_{_ULID}$"
_SHA256 = r"^[0-9a-f]{64}$"
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CORRECTION_FIELDS = frozenset(
    {
        "authority_function",
        "authority_required",
        "decision_type",
        "display_name",
        "kind",
        "manager_source_identity",
        "specialist_key",
        "title",
        "unit_leader",
        "unit_name",
        "unit_parent_name",
    }
)


class OrganizationImportUnavailable(RuntimeError):
    """Fixed-safe import service failure."""

    def __init__(self) -> None:
        super().__init__("organization_import_unavailable")


class OrganizationImportReviewRequired(OrganizationImportUnavailable):
    def __init__(self) -> None:
        RuntimeError.__init__(self, "organization_import_review_required")


class OrganizationImportStale(OrganizationImportUnavailable):
    def __init__(self) -> None:
        RuntimeError.__init__(self, "organization_import_stale")


class ImportCorrectionKind(StrEnum):
    CORRECT_RECORD = "correct_record"
    MERGE_DUPLICATES = "merge_duplicates"


class ImportCorrectionRequest(BaseModel):
    """One exact reviewed operation against immutable draft/source semantics."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    import_id: str = Field(pattern=_IMPORT_ID)
    reviewed_digest: str = Field(pattern=_SHA256)
    kind: ImportCorrectionKind = Field(strict=False)
    source_record_ids: tuple[str, ...] = Field(min_length=1)
    replacement_fields: tuple[tuple[str, str], ...] = Field(min_length=1)

    @field_validator("source_record_ids")
    @classmethod
    def source_ids_are_exact_and_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or value != tuple(sorted(value)):
            raise ValueError("source record IDs must be sorted and unique")
        if any(re.fullmatch(_RECORD_ID, record_id) is None for record_id in value):
            raise ValueError("source record ID is invalid")
        return value

    @field_validator("replacement_fields")
    @classmethod
    def replacements_are_allowlisted_and_normalized(
        cls,
        value: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, str], ...]:
        keys = tuple(key for key, _item in value)
        if len(set(keys)) != len(keys) or keys != tuple(sorted(keys)):
            raise ValueError("replacement fields must be sorted and unique")
        normalized: list[tuple[str, str]] = []
        for key, item in value:
            if key not in _CORRECTION_FIELDS or not 1 <= len(item) <= 120:
                raise ValueError("replacement field is invalid")
            clean = unicodedata.normalize("NFC", item)
            if clean != clean.strip() or any(
                unicodedata.category(character).startswith("C")
                or (
                    unicodedata.category(character).startswith("Z")
                    and character != " "
                )
                for character in clean
            ):
                raise ValueError("replacement field is invalid")
            normalized.append((key, clean))
        return tuple(normalized)

    @model_validator(mode="after")
    def operation_has_the_exact_arity(self) -> Self:
        if self.kind is ImportCorrectionKind.CORRECT_RECORD:
            if len(self.source_record_ids) != 1:
                raise ValueError("record correction requires exactly one source record")
        elif len(self.source_record_ids) < 2:
            raise ValueError("duplicate merge requires at least two source records")
        return self


class OrganizationMapper(Protocol):
    def map(
        self,
        snapshot: SourceSnapshot,
        current_graph: OrganizationGraph,
    ) -> OrganizationGraphCandidate: ...


def _deterministic_ulid(*parts: str) -> str:
    value = int.from_bytes(
        hashlib.sha256("\0".join(parts).encode("utf-8")).digest()[:16],
        "big",
    )
    return "".join(
        _ULID_ALPHABET[(value >> (5 * index)) & 31]
        for index in range(25, -1, -1)
    )


def _fields(record: SourceRecord) -> dict[str, str]:
    return dict(record.fields)


def _truthy(value: str | None) -> bool:
    return value == "true"


class RuleOrganizationMapper:
    """Map only source-declared identity, hierarchy, unit, and authority fields."""

    def map(
        self,
        snapshot: SourceSnapshot,
        current_graph: OrganizationGraph,
    ) -> OrganizationGraphCandidate:
        del current_graph
        records = tuple(sorted(snapshot.records, key=lambda item: (item.source_ordinal, item.record_id)))
        active_records = tuple(record for record in records if "merged_into" not in _fields(record))
        unit_names: set[str] = set()
        parent_by_unit: dict[str, str] = {}
        for record in active_records:
            fields = _fields(record)
            unit_name = fields.get("unit_name")
            parent_name = fields.get("unit_parent_name")
            if unit_name is not None:
                unit_names.add(unit_name)
                if parent_name is not None:
                    unit_names.add(parent_name)
                    parent_by_unit[unit_name] = parent_name
        unit_ids = {
            name: f"unit_{_deterministic_ulid(snapshot.organization_id, 'unit', name)}"
            for name in sorted(unit_names)
        }

        subjects: list[OrganizationSubject] = []
        subject_by_source: dict[str, OrganizationSubject] = {}
        record_by_source = {record.source_identity: record for record in active_records}
        for record in active_records:
            fields = _fields(record)
            subject = self._subject(snapshot, record, fields, unit_ids, record_by_source)
            subjects.append(subject)
            subject_by_source[record.source_identity] = subject

        leaders: dict[str, list[str]] = {}
        for record in active_records:
            fields = _fields(record)
            unit_name = fields.get("unit_name")
            if unit_name is not None and _truthy(fields.get("unit_leader")):
                leaders.setdefault(unit_name, []).append(
                    subject_by_source[record.source_identity].subject_id
                )
        units = tuple(
            OrganizationUnit(
                unit_id=unit_ids[name],
                organization_id=snapshot.organization_id,
                name=name,
                parent_unit_id=(
                    unit_ids[parent_by_unit[name]] if name in parent_by_unit else None
                ),
                leader_subject_id=(min(leaders[name]) if leaders.get(name) else None),
            )
            for name in sorted(unit_names)
        )

        edges: list[OrganizationEdge] = []
        assignments: list[AuthorityAssignment] = []
        for record in active_records:
            fields = _fields(record)
            subject = subject_by_source[record.source_identity]
            manager_source = fields.get("manager_source_identity")
            if manager_source in subject_by_source and manager_source != record.source_identity:
                edges.append(
                    OrganizationEdge(
                        edge_id=f"edge_{_deterministic_ulid(snapshot.organization_id, 'manager', record.source_identity, manager_source)}",
                        organization_id=snapshot.organization_id,
                        kind=OrganizationEdgeKind.REPORTS_TO,
                        source_subject_id=subject.subject_id,
                        target_subject_id=subject_by_source[manager_source].subject_id,
                        is_primary=True,
                    )
                )
            function = fields.get("authority_function")
            decision_type = fields.get("decision_type")
            if function is not None and decision_type is not None:
                try:
                    authority_function = AuthorityFunction(function)
                    assignments.append(
                        AuthorityAssignment(
                            assignment_id=f"auth_{_deterministic_ulid(snapshot.organization_id, 'authority', record.source_identity, decision_type, function)}",
                            organization_id=snapshot.organization_id,
                            subject_id=subject.subject_id,
                            decision_type=decision_type,
                            function=authority_function,
                            effective_from=snapshot.captured_at,
                        )
                    )
                except (ValueError, ValidationError):
                    pass
        return OrganizationGraphCandidate(
            organization_id=snapshot.organization_id,
            source_snapshot_id=snapshot.snapshot_id,
            subjects=tuple(sorted(subjects, key=lambda item: item.subject_id)),
            units=tuple(sorted(units, key=lambda item: item.unit_id)),
            edges=tuple(sorted(edges, key=lambda item: item.edge_id)),
            authority_assignments=tuple(
                sorted(assignments, key=lambda item: item.assignment_id)
            ),
        )

    def _subject(
        self,
        snapshot: SourceSnapshot,
        record: SourceRecord,
        fields: dict[str, str],
        unit_ids: dict[str, str],
        record_by_source: dict[str, SourceRecord],
    ) -> OrganizationSubject:
        ambiguous = False
        try:
            kind = OrganizationSubjectKind(fields["kind"])
        except (KeyError, ValueError):
            kind = OrganizationSubjectKind.HUMAN
            ambiguous = True
        display_name = fields.get("display_name")
        if display_name is None:
            display_name = f"Needs review record {record.source_ordinal}"
            ambiguous = True
        unit_name = fields.get("unit_name")
        unit_id = unit_ids.get(unit_name) if unit_name is not None else None
        manager_source = fields.get("manager_source_identity")
        if manager_source is not None and manager_source not in record_by_source:
            ambiguous = True
        if "duplicate_of" in fields:
            ambiguous = True
        specialist_key = fields.get("specialist_key")
        if kind is OrganizationSubjectKind.AI_SPECIALIST:
            if specialist_key is None:
                kind = OrganizationSubjectKind.HUMAN
                ambiguous = True
                specialist_key = None
            lifecycle = SubjectLifecycle.ACTIVE if not ambiguous else SubjectLifecycle.NEEDS_REVIEW
        else:
            lifecycle = (
                SubjectLifecycle.NEEDS_REVIEW
                if ambiguous
                else SubjectLifecycle.DRAFT_IMPORTED
            )
        try:
            return OrganizationSubject(
                subject_id=f"sub_{_deterministic_ulid(snapshot.organization_id, 'subject', record.source_identity)}",
                organization_id=snapshot.organization_id,
                kind=kind,
                lifecycle=lifecycle,
                display_name=display_name,
                source_identity=record.source_identity,
                specialist_key=specialist_key,
                unit_id=unit_id,
                title=fields.get("title"),
            )
        except ValidationError:
            return OrganizationSubject(
                subject_id=f"sub_{_deterministic_ulid(snapshot.organization_id, 'subject', record.source_identity)}",
                organization_id=snapshot.organization_id,
                kind=OrganizationSubjectKind.HUMAN,
                lifecycle=SubjectLifecycle.NEEDS_REVIEW,
                display_name=f"Needs review record {record.source_ordinal}",
                source_identity=record.source_identity,
                unit_id=unit_id,
            )


class OrganizationImportService:
    """Authorize, stage, reconcile, correct, and commit reviewed graph imports."""

    def __init__(
        self,
        *,
        repository: OrganizationGraphRepository,
        mapper: OrganizationMapper | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._mapper = RuleOrganizationMapper() if mapper is None else mapper
        self._fallback = RuleOrganizationMapper()
        self._clock = (lambda: datetime.now(UTC)) if clock is None else clock
        self._lock = threading.RLock()
        self._latest_source: dict[tuple[str, str], str] = {}
        self._superseded: set[tuple[str, str]] = set()

    def __repr__(self) -> str:
        return "OrganizationImportService()"

    @staticmethod
    def empty_graph(organization_id: str, created_at: datetime) -> OrganizationGraph:
        return OrganizationGraph(
            organization_id=organization_id,
            version=0,
            created_at=created_at,
        )

    def create_draft(
        self,
        context: DecisionOSContext,
        snapshot: SourceSnapshot,
    ) -> ImportDraft:
        with self._lock:
            self._repository.list_imports(context)
            if snapshot.organization_id != context.organization_id:
                raise ImportUnavailable() from None
            current = self._repository.load_graph(context)
            canonical = _canonical_snapshot(snapshot)
            draft = self._draft(canonical, current)
            saved = self._repository.save_import_draft(context, draft)
            self._latest_source[(context.organization_id, canonical.source_kind)] = (
                canonical.semantic_digest
            )
            return saved

    def reconcile(
        self,
        context: DecisionOSContext,
        import_id: str,
    ) -> ImportReconciliation:
        with self._lock:
            draft = self._repository.load_import_draft(context, import_id)
            return _reconcile(draft)

    def apply_correction(
        self,
        context: DecisionOSContext,
        request: ImportCorrectionRequest,
    ) -> ImportDraft:
        with self._lock:
            draft = self._repository.load_import_draft(context, request.import_id)
            key = (context.organization_id, request.import_id)
            if key in self._superseded or not _digest_matches(
                request.reviewed_digest,
                draft.semantic_digest,
            ):
                raise OrganizationImportStale() from None
            current = self._repository.load_graph(context)
            if current.version != draft.base_graph_version:
                raise OrganizationImportStale() from None
            record_ids = {record.record_id for record in draft.source_snapshot.records}
            if any(record_id not in record_ids for record_id in request.source_record_ids):
                raise OrganizationImportUnavailable() from None
            corrected_snapshot = _corrected_snapshot(draft.source_snapshot, request)
            corrected = self._draft(corrected_snapshot, current)
            saved = self._repository.save_import_draft(context, corrected)
            self._superseded.add(key)
            self._latest_source[
                (context.organization_id, corrected_snapshot.source_kind)
            ] = corrected_snapshot.semantic_digest
            return saved

    def commit(
        self,
        context: DecisionOSContext,
        request: CommitImportRequest,
    ) -> ImportReceipt:
        with self._lock:
            draft = self._repository.load_import_draft(context, request.import_id)
            if (context.organization_id, request.import_id) in self._superseded:
                raise OrganizationImportStale() from None
            latest = self._latest_source.get(
                (context.organization_id, draft.source_snapshot.source_kind)
            )
            if latest is not None and not _digest_matches(
                latest,
                draft.source_snapshot.semantic_digest,
            ):
                raise OrganizationImportStale() from None
            if not _digest_matches(request.reviewed_digest, draft.semantic_digest):
                raise OrganizationImportReviewRequired() from None
            reconciliation = _reconcile(draft)
            if reconciliation.blocking_codes or (
                request.acknowledged_codes != reconciliation.acknowledged_codes
            ):
                raise OrganizationImportReviewRequired() from None
            return self._repository.commit_graph(
                context,
                draft_id=draft.import_id,
                reviewed_digest=request.reviewed_digest,
                acknowledged_codes=request.acknowledged_codes,
            )

    def _draft(
        self,
        snapshot: SourceSnapshot,
        current: OrganizationGraph,
    ) -> ImportDraft:
        candidate = self._mapped_candidate(snapshot, current)
        created_at = _aware(self._clock())
        digest = _draft_digest(snapshot, candidate, current.version)
        return ImportDraft(
            import_id=f"imp_{_deterministic_ulid(snapshot.organization_id, digest)}",
            organization_id=snapshot.organization_id,
            source_snapshot=snapshot,
            candidate=candidate,
            base_graph_version=current.version,
            semantic_digest=digest,
            created_at=created_at,
        )

    def _mapped_candidate(
        self,
        snapshot: SourceSnapshot,
        current: OrganizationGraph,
    ) -> OrganizationGraphCandidate:
        failed = False
        candidate: object = None
        try:
            candidate = self._mapper.map(snapshot, current)
        except Exception:  # noqa: BLE001 - mapper/provider details are sealed
            failed = True
        if not failed:
            candidate = _validated_candidate(candidate, snapshot)
            failed = candidate is None
        if failed:
            fallback: OrganizationGraphCandidate | None = None
            try:
                fallback = self._fallback.map(snapshot, current)
            except Exception:  # noqa: BLE001 - source/provider details are sealed
                fallback = None
            if fallback is None:
                raise OrganizationImportUnavailable() from None
            return fallback
        assert isinstance(candidate, OrganizationGraphCandidate)
        return candidate


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OrganizationImportUnavailable() from None
    return value.astimezone(UTC)


def _digest_matches(candidate: object, expected: str) -> bool:
    return (
        type(candidate) is str
        and re.fullmatch(_SHA256, candidate) is not None
        and secrets.compare_digest(candidate, expected)
    )


def _canonical_snapshot(snapshot: SourceSnapshot) -> SourceSnapshot:
    records = tuple(
        record.model_copy(update={"fields": tuple(sorted(record.fields))})
        for record in sorted(
            snapshot.records,
            key=lambda item: (item.source_ordinal, item.record_id),
        )
    )
    return snapshot.model_copy(update={"records": records})


def _validated_candidate(
    candidate: object,
    snapshot: SourceSnapshot,
) -> OrganizationGraphCandidate | None:
    try:
        if not isinstance(candidate, OrganizationGraphCandidate):
            return None
        validated = OrganizationGraphCandidate.model_validate_json(
            to_json(candidate.model_dump(mode="python"))
        )
    except Exception:  # noqa: BLE001 - hostile mapper output is sealed
        return None
    if (
        validated.organization_id != snapshot.organization_id
        or validated.source_snapshot_id != snapshot.snapshot_id
    ):
        return None
    records = {record.source_identity: record for record in snapshot.records}
    subjects = {
        subject.subject_id: subject
        for subject in validated.subjects
        if subject.source_identity in records
    }
    if len(subjects) != len(validated.subjects):
        return None
    for edge in validated.edges:
        if edge.kind is not OrganizationEdgeKind.REPORTS_TO:
            continue
        source = subjects.get(edge.source_subject_id)
        target = subjects.get(edge.target_subject_id or "")
        if source is None or target is None:
            return None
        source_fields = _fields(records[source.source_identity or ""])
        if source_fields.get("manager_source_identity") != target.source_identity:
            return None
    for assignment in validated.authority_assignments:
        subject = subjects.get(assignment.subject_id)
        if subject is None or subject.source_identity is None:
            return None
        source_fields = _fields(records[subject.source_identity])
        if (
            source_fields.get("authority_function") != assignment.function.value
            or source_fields.get("decision_type") != assignment.decision_type
        ):
            return None
    return validated


def _draft_digest(
    snapshot: SourceSnapshot,
    candidate: OrganizationGraphCandidate,
    base_version: int,
) -> str:
    return hashlib.sha256(
        to_json(
            {
                "base_graph_version": base_version,
                "candidate": candidate.model_dump(mode="python"),
                "source_snapshot": snapshot.model_dump(mode="python"),
            }
        )
    ).hexdigest()


def _reconcile(draft: ImportDraft) -> ImportReconciliation:
    graph = OrganizationGraph(
        organization_id=draft.organization_id,
        version=draft.base_graph_version + 1,
        subjects=draft.candidate.subjects,
        units=draft.candidate.units,
        edges=draft.candidate.edges,
        authority_assignments=draft.candidate.authority_assignments,
        created_at=draft.created_at,
    )
    blocking = set(validate_organization_graph(graph).blocking_codes)
    nonblocking: set[str] = set()
    subject_by_source = {
        subject.source_identity: subject
        for subject in draft.candidate.subjects
        if subject.source_identity is not None
    }
    subject_ids = {subject.subject_id for subject in draft.candidate.subjects}
    manager_edges = {
        edge.source_subject_id
        for edge in draft.candidate.edges
        if edge.kind is OrganizationEdgeKind.REPORTS_TO
    }
    authority_subjects = {
        assignment.subject_id for assignment in draft.candidate.authority_assignments
    }
    for record in draft.source_snapshot.records:
        fields = _fields(record)
        subject = subject_by_source.get(record.source_identity)
        if "duplicate_of" in fields:
            blocking.update(("duplicate_identity", "unresolved_duplicate"))
        if subject is None:
            continue
        if subject.lifecycle is SubjectLifecycle.NEEDS_REVIEW:
            blocking.add("needs_review")
        if "manager_source_identity" in fields and subject.subject_id not in manager_edges:
            blocking.add("unresolved_manager")
        if _truthy(fields.get("authority_required")) and subject.subject_id not in authority_subjects:
            blocking.add("missing_authority")
    if any(unit.leader_subject_id is None for unit in draft.candidate.units):
        nonblocking.add("leaderless_team")
    if any(subject.unit_id is None for subject in draft.candidate.subjects):
        nonblocking.add("unassigned_subject")
    if any(
        unit.leader_subject_id is not None and unit.leader_subject_id not in subject_ids
        for unit in draft.candidate.units
    ):
        blocking.add("unknown_unit_leader")
    counts = Counter(subject.lifecycle for subject in draft.candidate.subjects)
    normalized = len(draft.candidate.subjects)
    source_count = len(draft.source_snapshot.records)
    rejected = source_count - normalized
    if rejected < 0:
        raise OrganizationImportUnavailable() from None
    return ImportReconciliation(
        import_id=draft.import_id,
        organization_id=draft.organization_id,
        source_count=source_count,
        normalized_count=normalized,
        rejected_count=rejected,
        lifecycle_counts=tuple(sorted(counts.items(), key=lambda item: item[0].value)),
        blocking_codes=tuple(sorted(blocking)),
        acknowledged_codes=tuple(sorted(nonblocking)),
    )


def _corrected_snapshot(
    snapshot: SourceSnapshot,
    request: ImportCorrectionRequest,
) -> SourceSnapshot:
    selected = set(request.source_record_ids)
    replacements = dict(request.replacement_fields)
    primary_id = request.source_record_ids[0]
    primary = next(record for record in snapshot.records if record.record_id == primary_id)
    records: list[SourceRecord] = []
    for record in snapshot.records:
        fields = _fields(record)
        if request.kind is ImportCorrectionKind.CORRECT_RECORD and record.record_id in selected:
            fields.update(replacements)
        elif request.kind is ImportCorrectionKind.MERGE_DUPLICATES:
            if record.record_id == primary_id:
                fields.pop("duplicate_of", None)
                fields.update(replacements)
            elif record.record_id in selected:
                fields = {"merged_into": primary.source_identity}
        records.append(record.model_copy(update={"fields": tuple(sorted(fields.items()))}))
    canonical_records = tuple(
        sorted(records, key=lambda item: (item.source_ordinal, item.record_id))
    )
    digest = hashlib.sha256(
        to_json(
            {
                "captured_at": snapshot.captured_at,
                "organization_id": snapshot.organization_id,
                "records": tuple(
                    record.model_dump(mode="python") for record in canonical_records
                ),
                "source_kind": snapshot.source_kind,
            }
        )
    ).hexdigest()
    return SourceSnapshot(
        snapshot_id=f"snap_{_deterministic_ulid(snapshot.organization_id, digest)}",
        organization_id=snapshot.organization_id,
        source_kind=snapshot.source_kind,
        captured_at=snapshot.captured_at,
        records=canonical_records,
        semantic_digest=digest,
    )
