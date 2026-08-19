"""Deterministic organization import mapping, review, and commit orchestration."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import multiprocessing
import re
import secrets
import unicodedata
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from time import monotonic
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
from humanwire.organization_store import (
    ImportLineageConflict,
    ImportUnavailable,
    OrganizationGraphRepository,
)

_ULID = r"[0-9A-HJKMNP-TV-Z]{26}"
_IMPORT_ID = rf"^imp_{_ULID}$"
_RECORD_ID = rf"^rec_{_ULID}$"
_SHA256 = r"^[0-9a-f]{64}$"
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_FACTORY_MODULE = r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$"
_FACTORY_NAME = r"^[A-Za-z_]\w*$"
_MAX_MAPPER_CONFIG_BYTES = 16_384
_MAX_MAPPER_PACKET_BYTES = 8_000_000
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


def _worker_config_is_safe(value: object, *, depth: int = 0) -> bool:
    if depth > 8:
        return False
    if value is None or type(value) in {bool, str}:
        return type(value) is not str or len(value) <= 1_000
    if type(value) is int:
        return -(2**63) <= value <= (2**63) - 1
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is list:
        return len(value) <= 100 and all(
            _worker_config_is_safe(item, depth=depth + 1) for item in value
        )
    if type(value) is dict:
        return len(value) <= 100 and all(
            type(key) is str
            and re.fullmatch(r"^[a-z][a-z0-9_]{0,63}$", key) is not None
            and _worker_config_is_safe(item, depth=depth + 1)
            for key, item in value.items()
        )
    return False


def _reject_json_constant(_value: str) -> None:
    raise ValueError("mapper config is invalid")


class OrganizationMapperWorkerSpec(BaseModel):
    """Strict primitive instructions for constructing a mapper inside a child."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    factory_module: str = Field(min_length=1, max_length=255, pattern=_FACTORY_MODULE)
    factory_qualname: str = Field(min_length=1, max_length=120, pattern=_FACTORY_NAME)
    config_json: str = Field(min_length=2, max_length=_MAX_MAPPER_CONFIG_BYTES)

    @field_validator("config_json")
    @classmethod
    def config_is_canonical_safe_json(cls, value: str) -> str:
        try:
            config = json.loads(
                value,
                parse_constant=_reject_json_constant,
            )
            canonical = json.dumps(
                config,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (RecursionError, TypeError, ValueError):
            raise ValueError("mapper config is invalid") from None
        if (
            len(value.encode("utf-8")) > _MAX_MAPPER_CONFIG_BYTES
            or type(config) is not dict
            or not _worker_config_is_safe(config)
            or canonical != value
        ):
            raise ValueError("mapper config is invalid")
        return value


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


@dataclass(frozen=True, slots=True)
class _ControlDiagnostics:
    ambiguous_record_ids: frozenset[str]
    blocking_codes: tuple[str, ...]
    conflicting_parent_units: frozenset[str]
    multiple_leader_units: frozenset[str]
    structurally_ambiguous_units: frozenset[str]


def _control_diagnostics(records: tuple[SourceRecord, ...]) -> _ControlDiagnostics:
    ambiguous: set[str] = set()
    codes: set[str] = set()
    records_by_unit: dict[str, list[SourceRecord]] = {}
    parents_by_unit: dict[str, set[str]] = {}
    leaders_by_unit: dict[str, list[SourceRecord]] = {}
    malformed_unit_controls: set[str] = set()
    for record in records:
        fields = _fields(record)
        unit_name = fields.get("unit_name")
        if unit_name is not None:
            records_by_unit.setdefault(unit_name, []).append(record)
            parent_name = fields.get("unit_parent_name")
            if parent_name is not None:
                parents_by_unit.setdefault(unit_name, set()).add(parent_name)
                if parent_name == unit_name:
                    ambiguous.add(record.record_id)
                    codes.add("invalid_control_value")
                    malformed_unit_controls.add(unit_name)
        elif "unit_parent_name" in fields:
            ambiguous.add(record.record_id)
            codes.add("invalid_control_value")
        for boolean_field in ("authority_required", "unit_leader"):
            if boolean_field in fields and fields[boolean_field] not in {"false", "true"}:
                ambiguous.add(record.record_id)
                codes.add("invalid_control_value")
                if boolean_field == "unit_leader" and unit_name is not None:
                    malformed_unit_controls.add(unit_name)
        if "unit_leader" in fields and unit_name is None:
            ambiguous.add(record.record_id)
            codes.add("invalid_control_value")
        if unit_name is not None and fields.get("unit_leader") == "true":
            leaders_by_unit.setdefault(unit_name, []).append(record)
        has_function = "authority_function" in fields
        has_decision_type = "decision_type" in fields
        if has_function != has_decision_type:
            ambiguous.add(record.record_id)
            codes.add("incomplete_authority")
        elif has_function:
            try:
                AuthorityFunction(fields["authority_function"])
            except ValueError:
                ambiguous.add(record.record_id)
                codes.add("invalid_control_value")

    conflicting_parent_units = frozenset(
        unit_name
        for unit_name, parent_names in parents_by_unit.items()
        if len(parent_names) > 1
    )
    multiple_leader_units = frozenset(
        unit_name
        for unit_name, leaders in leaders_by_unit.items()
        if len(leaders) > 1
    )
    if conflicting_parent_units:
        codes.add("conflicting_unit_parent")
    if multiple_leader_units:
        codes.add("multiple_unit_leaders")
    structurally_ambiguous_units = (
        conflicting_parent_units | multiple_leader_units | malformed_unit_controls
    )
    for unit_name in structurally_ambiguous_units:
        ambiguous.update(record.record_id for record in records_by_unit[unit_name])
    return _ControlDiagnostics(
        ambiguous_record_ids=frozenset(ambiguous),
        blocking_codes=tuple(sorted(codes)),
        conflicting_parent_units=conflicting_parent_units,
        multiple_leader_units=multiple_leader_units,
        structurally_ambiguous_units=frozenset(structurally_ambiguous_units),
    )


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
        diagnostics = _control_diagnostics(active_records)
        unit_names: set[str] = set()
        declared_parents: dict[str, set[str]] = {}
        for record in active_records:
            fields = _fields(record)
            unit_name = fields.get("unit_name")
            parent_name = fields.get("unit_parent_name")
            if unit_name is not None:
                unit_names.add(unit_name)
                if parent_name is not None:
                    unit_names.add(parent_name)
                    declared_parents.setdefault(unit_name, set()).add(parent_name)
        parent_by_unit = {
            unit_name: next(iter(parent_names))
            for unit_name, parent_names in declared_parents.items()
            if unit_name not in diagnostics.structurally_ambiguous_units
        }
        unit_ids = {
            name: f"unit_{_deterministic_ulid(snapshot.organization_id, 'unit', name)}"
            for name in sorted(unit_names)
        }

        subjects: list[OrganizationSubject] = []
        subject_by_source: dict[str, OrganizationSubject] = {}
        record_by_source = {record.source_identity: record for record in active_records}
        for record in active_records:
            fields = _fields(record)
            subject = self._subject(
                snapshot,
                record,
                fields,
                unit_ids,
                record_by_source,
                diagnostics,
            )
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
                leader_subject_id=(
                    min(leaders[name])
                    if leaders.get(name)
                    and name not in diagnostics.structurally_ambiguous_units
                    else None
                ),
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
        diagnostics: _ControlDiagnostics,
    ) -> OrganizationSubject:
        ambiguous = record.record_id in diagnostics.ambiguous_record_ids
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


def _mapper_worker(
    spec_json: str,
    snapshot_json: str,
    graph_json: str,
    sender,
) -> None:
    payload = b""
    try:
        spec = OrganizationMapperWorkerSpec.model_validate_json(spec_json)
        module = importlib.import_module(spec.factory_module)
        factory = vars(module).get(spec.factory_qualname)
        if not callable(factory):
            raise TypeError("mapper factory is unavailable")
        mapper = factory(json.loads(spec.config_json))
        snapshot = SourceSnapshot.model_validate_json(snapshot_json)
        graph = OrganizationGraph.model_validate_json(graph_json)
        candidate = mapper.map(snapshot, graph)
        if isinstance(candidate, OrganizationGraphCandidate):
            payload = candidate.model_dump_json().encode("utf-8")
    except Exception:  # noqa: BLE001 - child details never cross the boundary
        payload = b""
    with suppress(Exception):
        sender.send_bytes(payload)
    sender.close()


def _mapper_worker_spec(mapper: OrganizationMapper) -> OrganizationMapperWorkerSpec | None:
    try:
        state = object.__getattribute__(mapper, "__dict__")
    except Exception:  # noqa: BLE001 - hostile mapper access is sealed
        return None
    if type(state) is not dict:
        return None
    spec = state.get("_humanwire_mapper_spec")
    if type(spec) is not OrganizationMapperWorkerSpec:
        return None
    try:
        spec_state = object.__getattribute__(spec, "__dict__")
    except Exception:  # noqa: BLE001 - hostile spec access is sealed
        return None
    if type(spec_state) is not dict or len(spec_state) != 3:
        return None
    factory_module = spec_state.get("factory_module")
    factory_qualname = spec_state.get("factory_qualname")
    config_json = spec_state.get("config_json")
    if any(type(item) is not str for item in (factory_module, factory_qualname, config_json)):
        return None
    try:
        return OrganizationMapperWorkerSpec.model_validate(
            {
                "factory_module": factory_module,
                "factory_qualname": factory_qualname,
                "config_json": config_json,
            }
        )
    except (RecursionError, TypeError, ValueError, ValidationError):
        return None


def _run_mapper_with_deadline(
    spec: OrganizationMapperWorkerSpec,
    snapshot: SourceSnapshot,
    current: OrganizationGraph,
    timeout_seconds: float,
) -> object:
    process_context = multiprocessing.get_context("spawn")
    receiver, sender = process_context.Pipe(duplex=False)
    process = process_context.Process(
        target=_mapper_worker,
        args=(
            spec.model_dump_json(),
            snapshot.model_dump_json(),
            current.model_dump_json(),
            sender,
        ),
        daemon=True,
    )
    started = False
    payload: bytes | None = None
    deadline = monotonic() + timeout_seconds
    try:
        process.start()
        started = True
        sender.close()
        remaining = max(0.0, deadline - monotonic())
        if receiver.poll(remaining):
            payload = receiver.recv_bytes(maxlength=_MAX_MAPPER_PACKET_BYTES)
            process.join(max(0.0, deadline - monotonic()))
    except Exception:  # noqa: BLE001 - spawn/pickle/provider details are sealed
        payload = None
    finally:
        with suppress(Exception):
            sender.close()
        if started and process.is_alive():
            process.terminate()
            process.join(1)
        if started and process.is_alive():
            process.kill()
            process.join()
        if started and process.exitcode not in {0, None}:
            payload = None
        receiver.close()
    if not payload:
        return None
    try:
        primitive = json.loads(payload)
        if type(primitive) is not dict:
            return None
        return OrganizationGraphCandidate.model_validate_json(payload)
    except Exception:  # noqa: BLE001 - hostile packet/output is sealed
        return None


class OrganizationImportService:
    """Authorize, stage, reconcile, correct, and commit reviewed graph imports."""

    def __init__(
        self,
        *,
        repository: OrganizationGraphRepository,
        mapper: OrganizationMapper | None = None,
        mapper_timeout_seconds: float = 2.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            isinstance(mapper_timeout_seconds, bool)
            or not isinstance(mapper_timeout_seconds, (int, float))
            or not 0 < mapper_timeout_seconds <= 30
        ):
            raise ValueError("mapper timeout is invalid")
        self._repository = repository
        self._mapper = RuleOrganizationMapper() if mapper is None else mapper
        self._fallback = RuleOrganizationMapper()
        self._mapper_timeout_seconds = float(mapper_timeout_seconds)
        self._clock = (lambda: datetime.now(UTC)) if clock is None else clock

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
        self._repository.list_imports(context)
        if snapshot.organization_id != context.organization_id:
            raise ImportUnavailable() from None
        current = self._repository.load_graph(context)
        canonical = _canonical_snapshot(snapshot)
        draft = self._draft(canonical, current)
        return self._repository.save_import_draft(context, draft)

    def reconcile(
        self,
        context: DecisionOSContext,
        import_id: str,
    ) -> ImportReconciliation:
        draft = self._repository.load_import_draft(context, import_id)
        return _reconcile(draft)

    def load_import(
        self,
        context: DecisionOSContext,
        import_id: str,
    ) -> tuple[ImportDraft, ImportReconciliation, ImportReceipt | None]:
        draft = self._repository.load_import_draft(context, import_id)
        receipt = self._repository.load_import_receipt(context, import_id)
        reconciliation = _reconcile(draft)
        if not _loaded_import_is_bound(
            context,
            import_id,
            draft,
            reconciliation,
            receipt,
        ):
            raise OrganizationImportUnavailable() from None
        return draft, reconciliation, receipt

    def review_for_graph(
        self,
        context: DecisionOSContext,
        graph_version: int,
    ) -> tuple[ImportReconciliation, ImportReceipt] | None:
        committed = self._repository.load_committed_import(context, graph_version)
        if committed is None:
            if graph_version != 0:
                raise OrganizationImportUnavailable() from None
            return None
        draft, receipt = committed
        reconciliation = _reconcile(draft)
        if not _loaded_import_is_bound(
            context,
            draft.import_id,
            draft,
            reconciliation,
            receipt,
            graph_version=graph_version,
        ):
            raise OrganizationImportUnavailable() from None
        return reconciliation, receipt

    def apply_correction(
        self,
        context: DecisionOSContext,
        request: ImportCorrectionRequest,
    ) -> ImportDraft:
        draft = self._repository.load_import_draft(context, request.import_id)
        if not _digest_matches(request.reviewed_digest, draft.semantic_digest):
            raise OrganizationImportStale() from None
        current = self._repository.load_graph(context)
        if current.version != draft.base_graph_version:
            raise OrganizationImportStale() from None
        record_ids = {record.record_id for record in draft.source_snapshot.records}
        if any(record_id not in record_ids for record_id in request.source_record_ids):
            raise OrganizationImportUnavailable() from None
        corrected_snapshot = _corrected_snapshot(draft.source_snapshot, request)
        corrected = self._draft(
            corrected_snapshot,
            current,
            supersedes_import_id=draft.import_id,
        )
        try:
            return self._repository.save_import_draft(context, corrected)
        except ImportLineageConflict:
            raise OrganizationImportStale() from None

    def commit(
        self,
        context: DecisionOSContext,
        request: CommitImportRequest,
    ) -> ImportReceipt:
        draft = self._repository.load_import_draft(context, request.import_id)
        if not _digest_matches(request.reviewed_digest, draft.semantic_digest):
            raise OrganizationImportReviewRequired() from None
        try:
            self._repository.require_latest_import(context, draft.import_id)
        except ImportLineageConflict:
            raise OrganizationImportStale() from None
        reconciliation = _reconcile(draft)
        if reconciliation.blocking_codes or (
            request.acknowledged_codes != reconciliation.acknowledged_codes
        ):
            raise OrganizationImportReviewRequired() from None
        try:
            return self._repository.commit_graph(
                context,
                draft_id=draft.import_id,
                reviewed_digest=request.reviewed_digest,
                acknowledged_codes=request.acknowledged_codes,
            )
        except ImportLineageConflict:
            raise OrganizationImportStale() from None

    def _draft(
        self,
        snapshot: SourceSnapshot,
        current: OrganizationGraph,
        *,
        supersedes_import_id: str | None = None,
    ) -> ImportDraft:
        candidate = self._mapped_candidate(snapshot, current)
        created_at = _aware(self._clock())
        digest = _draft_digest(
            snapshot,
            candidate,
            current.version,
            supersedes_import_id,
        )
        return ImportDraft(
            import_id=f"imp_{_deterministic_ulid(snapshot.organization_id, digest)}",
            supersedes_import_id=supersedes_import_id,
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
        if type(self._mapper) is RuleOrganizationMapper:
            candidate: object = self._mapper.map(snapshot, current)
        else:
            spec = _mapper_worker_spec(self._mapper)
            candidate = (
                None
                if spec is None
                else _run_mapper_with_deadline(
                    spec,
                    snapshot,
                    current,
                    self._mapper_timeout_seconds,
                )
            )
        failed = candidate is None
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


def _loaded_import_is_bound(
    context: DecisionOSContext,
    import_id: str,
    draft: ImportDraft,
    reconciliation: ImportReconciliation,
    receipt: ImportReceipt | None,
    *,
    graph_version: int | None = None,
) -> bool:
    if (
        type(draft) is not ImportDraft
        or type(reconciliation) is not ImportReconciliation
        or draft.organization_id != context.organization_id
        or draft.import_id != import_id
        or reconciliation.organization_id != context.organization_id
        or reconciliation.import_id != import_id
    ):
        return False
    if receipt is None:
        return graph_version is None
    return (
        type(receipt) is ImportReceipt
        and receipt.organization_id == context.organization_id
        and receipt.import_id == import_id
        and receipt.source_snapshot_id == draft.source_snapshot.snapshot_id
        and receipt.source_snapshot_digest == draft.source_snapshot.semantic_digest
        and receipt.graph_version == draft.base_graph_version + 1
        and (graph_version is None or receipt.graph_version == graph_version)
        and receipt.committed_subject_count == len(draft.candidate.subjects)
        and receipt.acknowledged_codes == reconciliation.acknowledged_codes
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
    active_records = tuple(
        record for record in snapshot.records if "merged_into" not in _fields(record)
    )
    records = {record.source_identity: record for record in active_records}
    candidate_source_identities = {
        subject.source_identity
        for subject in validated.subjects
        if subject.source_identity is not None
    }
    if candidate_source_identities != set(records):
        return None
    subjects = {
        subject.subject_id: subject
        for subject in validated.subjects
        if subject.source_identity in records
    }
    if len(subjects) != len(validated.subjects):
        return None
    diagnostics = _control_diagnostics(active_records)
    ambiguous_source_identities = {
        record.source_identity
        for record in active_records
        if record.record_id in diagnostics.ambiguous_record_ids
    }
    if any(
        subject.source_identity in ambiguous_source_identities
        and subject.lifecycle is not SubjectLifecycle.NEEDS_REVIEW
        for subject in validated.subjects
    ):
        return None
    subjects_by_source_identity = {
        subject.source_identity: subject
        for subject in validated.subjects
        if subject.source_identity is not None
    }
    poisoned_participants_by_unit_name: dict[str, set[str]] = {}
    for record in active_records:
        source_unit_name = _fields(record).get("unit_name")
        if source_unit_name in diagnostics.structurally_ambiguous_units:
            poisoned_participants_by_unit_name.setdefault(
                source_unit_name,
                set(),
            ).add(record.source_identity)
    candidate_units_by_name: dict[str, list[OrganizationUnit]] = {}
    for unit in validated.units:
        candidate_units_by_name.setdefault(unit.name, []).append(unit)
    for source_unit_name, source_identities in poisoned_participants_by_unit_name.items():
        matching_units = candidate_units_by_name.get(source_unit_name, [])
        if len(matching_units) != 1:
            return None
        source_bound_unit = matching_units[0]
        if (
            source_bound_unit.parent_unit_id is not None
            or source_bound_unit.leader_subject_id is not None
        ):
            return None
        if any(
            (subject := subjects_by_source_identity.get(source_identity)) is None
            or subject.unit_id != source_bound_unit.unit_id
            for source_identity in source_identities
        ):
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
    supersedes_import_id: str | None,
) -> str:
    return hashlib.sha256(
        to_json(
            {
                "base_graph_version": base_version,
                "candidate": candidate.model_dump(mode="python"),
                "source_snapshot": snapshot.model_dump(mode="python"),
                "supersedes_import_id": supersedes_import_id,
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
    active_records = tuple(
        record
        for record in draft.source_snapshot.records
        if "merged_into" not in _fields(record)
    )
    blocking.update(_control_diagnostics(active_records).blocking_codes)
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
