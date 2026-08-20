"""Tenant-scoped, read-only evidence tools for the DecisionOS council."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol

from google.adk.tools import FunctionTool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from humanwire.council_models import _validate_public_text
from humanwire.decisionos_models import DecisionOSContext

_ULID = r"[0-9A-HJKMNP-TV-Z]{26}"
_ORGANIZATION_ID = rf"^org_{_ULID}$"
_WORKSPACE_ID = rf"^wrk_{_ULID}$"
_EVIDENCE_ID = r"^evidence_[a-z0-9_]{1,64}$"
_DECISION_ID = r"^decision_[a-z0-9_]{1,64}$"
_SHA256 = r"^[0-9a-f]{64}$"
_EXTRACTION_VERSION = r"^extract-v[1-9][0-9]*$"


class CouncilToolDenied(RuntimeError):
    def __init__(self, code: Literal["evidence_unavailable", "decision_unavailable"]):
        super().__init__(code)


class _ToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CouncilEvidenceStatus(StrEnum):
    READY = "ready"
    STALE = "stale"
    QUARANTINED = "quarantined"
    DELETED = "deleted"


class CouncilEvidenceRecord(_ToolModel):
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    workspace_id: str = Field(pattern=_WORKSPACE_ID)
    evidence_id: str = Field(pattern=_EVIDENCE_ID)
    title: str = Field(min_length=1, max_length=160)
    sanitized_text: str = Field(min_length=1, max_length=100_000)
    source_digest: str = Field(pattern=_SHA256)
    extraction_version: str = Field(pattern=_EXTRACTION_VERSION)
    status: CouncilEvidenceStatus = Field(strict=False)

    @model_validator(mode="after")
    def has_safe_model_visible_content(self) -> CouncilEvidenceRecord:
        _validate_public_text(self.title)
        _validate_public_text(self.sanitized_text)
        return self


class CouncilPriorDecision(_ToolModel):
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    workspace_id: str = Field(pattern=_WORKSPACE_ID)
    decision_id: str = Field(pattern=_DECISION_ID)
    summary: str = Field(min_length=1, max_length=1_200)
    semantic_digest: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def has_safe_summary(self) -> CouncilPriorDecision:
        _validate_public_text(self.summary)
        return self


class EvidenceCatalogItem(_ToolModel):
    evidence_id: str = Field(pattern=_EVIDENCE_ID)
    title: str = Field(min_length=1, max_length=160)
    source_digest: str = Field(pattern=_SHA256)
    extraction_version: str = Field(pattern=_EXTRACTION_VERSION)


class EvidenceCatalog(_ToolModel):
    items: tuple[EvidenceCatalogItem, ...] = Field(max_length=100)


class EvidenceExcerpt(_ToolModel):
    evidence_id: str = Field(pattern=_EVIDENCE_ID)
    text: str = Field(min_length=1, max_length=500)
    start_offset: int = Field(ge=0, le=100_000)
    end_offset: int = Field(ge=1, le=100_000)
    source_digest: str = Field(pattern=_SHA256)
    extraction_version: str = Field(pattern=_EXTRACTION_VERSION)

    @model_validator(mode="after")
    def has_exact_span_and_safe_text(self) -> EvidenceExcerpt:
        if self.end_offset - self.start_offset != len(self.text):
            raise ValueError("excerpt span must match text")
        _validate_public_text(self.text)
        return self


class PriorDecisionExcerpt(_ToolModel):
    decision_id: str = Field(pattern=_DECISION_ID)
    summary: str = Field(min_length=1, max_length=1_200)
    semantic_digest: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def has_safe_summary(self) -> PriorDecisionExcerpt:
        _validate_public_text(self.summary)
        return self


class CouncilEvidenceRegistry(Protocol):
    def list_evidence(
        self,
        organization_id: str,
        workspace_id: str,
    ) -> tuple[CouncilEvidenceRecord, ...]:
        raise NotImplementedError

    def load_evidence(
        self,
        organization_id: str,
        workspace_id: str,
        evidence_id: str,
    ) -> CouncilEvidenceRecord | None:
        raise NotImplementedError

    def load_prior_decision(
        self,
        organization_id: str,
        workspace_id: str,
        decision_id: str,
    ) -> CouncilPriorDecision | None:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class CouncilToolContext:
    context: DecisionOSContext
    workspace_id: str
    registry: CouncilEvidenceRegistry = field(repr=False)

    def __post_init__(self) -> None:
        canonical = DecisionOSContext.model_validate(self.context)
        if re.fullmatch(_WORKSPACE_ID, self.workspace_id) is None:
            raise ValueError("workspace is invalid")
        for name in ("list_evidence", "load_evidence", "load_prior_decision"):
            if not callable(getattr(self.registry, name, None)):
                raise TypeError("evidence registry is invalid")
        object.__setattr__(self, "context", canonical)

    @property
    def organization_id(self) -> str:
        return self.context.organization_id


def _canonical_record[RecordT: BaseModel](
    value: object,
    model_type: type[RecordT],
) -> RecordT | None:
    if type(value) is not model_type:
        return None
    values = object.__getattribute__(value, "__dict__")
    fields = type.__getattribute__(model_type, "__pydantic_fields__")
    extra = object.__getattribute__(value, "__pydantic_extra__")
    private = object.__getattribute__(value, "__pydantic_private__")
    if (
        type(values) is not dict
        or type(fields) is not dict
        or dict.__len__(values) != dict.__len__(fields)
        or extra not in (None, {})
        or private not in (None, {})
    ):
        return None
    payload: dict[str, object] = {}
    for name in dict.__iter__(fields):
        if type(name) is not str or name not in values:
            return None
        item = dict.__getitem__(values, name)
        if (
            type(item) in {str, int, float, bool, type(None)}
            or isinstance(item, StrEnum)
            and type(item) is model_type.model_fields[name].annotation
        ):
            payload[name] = item
        else:
            return None
    try:
        return model_type.model_validate(payload, strict=True)
    except Exception:  # noqa: BLE001 - corrupt values fail closed without detail
        return None


def _registry_list(context: CouncilToolContext) -> tuple[CouncilEvidenceRecord, ...]:
    failed = False
    result: object = None
    try:
        result = context.registry.list_evidence(
            context.organization_id,
            context.workspace_id,
        )
    except Exception:  # noqa: BLE001 - provider details never cross the tool boundary
        failed = True
    if failed or type(result) is not tuple or len(result) > 100:
        raise CouncilToolDenied("evidence_unavailable") from None
    records: list[CouncilEvidenceRecord] = []
    for raw in result:
        canonical = _canonical_record(raw, CouncilEvidenceRecord)
        if canonical is None:
            raise CouncilToolDenied("evidence_unavailable") from None
        records.append(canonical)
    return tuple(records)


def _registry_evidence(
    context: CouncilToolContext,
    evidence_id: str,
) -> CouncilEvidenceRecord:
    failed = False
    result: object = None
    try:
        result = context.registry.load_evidence(
            context.organization_id,
            context.workspace_id,
            evidence_id,
        )
    except Exception:  # noqa: BLE001 - provider details never cross the tool boundary
        failed = True
    canonical = None if failed else _canonical_record(result, CouncilEvidenceRecord)
    if canonical is None:
        raise CouncilToolDenied("evidence_unavailable") from None
    return canonical


def _registry_decision(
    context: CouncilToolContext,
    decision_id: str,
) -> CouncilPriorDecision:
    failed = False
    result: object = None
    try:
        result = context.registry.load_prior_decision(
            context.organization_id,
            context.workspace_id,
            decision_id,
        )
    except Exception:  # noqa: BLE001 - provider details never cross the tool boundary
        failed = True
    canonical = None if failed else _canonical_record(result, CouncilPriorDecision)
    if canonical is None:
        raise CouncilToolDenied("decision_unavailable") from None
    return canonical


def list_evidence(context: CouncilToolContext) -> EvidenceCatalog:
    """List safe metadata for ready evidence in the active workspace."""

    items: list[EvidenceCatalogItem] = []
    for record in _registry_list(context):
        if record.organization_id != context.organization_id:
            raise CouncilToolDenied("evidence_unavailable") from None
        if record.workspace_id != context.workspace_id:
            raise CouncilToolDenied("evidence_unavailable") from None
        if record.status is not CouncilEvidenceStatus.READY:
            continue
        items.append(
            EvidenceCatalogItem(
                evidence_id=record.evidence_id,
                title=record.title,
                source_digest=record.source_digest,
                extraction_version=record.extraction_version,
            )
        )
    items.sort(key=lambda item: item.evidence_id)
    return EvidenceCatalog(items=tuple(items))


def read_evidence_excerpt(
    context: CouncilToolContext,
    evidence_id: object,
    start: object,
    length: object,
) -> EvidenceExcerpt:
    """Read one bounded excerpt from ready, sanitized workspace evidence."""

    if (
        type(evidence_id) is not str
        or re.fullmatch(_EVIDENCE_ID, evidence_id) is None
        or type(start) is not int
        or type(length) is not int
        or not 0 <= start <= 99_999
        or not 1 <= length <= 500
    ):
        raise CouncilToolDenied("evidence_unavailable") from None
    record = _registry_evidence(context, evidence_id)
    if (
        record.organization_id != context.organization_id
        or record.workspace_id != context.workspace_id
        or record.evidence_id != evidence_id
        or record.status is not CouncilEvidenceStatus.READY
        or start >= len(record.sanitized_text)
    ):
        raise CouncilToolDenied("evidence_unavailable") from None
    text = record.sanitized_text[start : start + length]
    try:
        return EvidenceExcerpt(
            evidence_id=record.evidence_id,
            text=text,
            start_offset=start,
            end_offset=start + len(text),
            source_digest=record.source_digest,
            extraction_version=record.extraction_version,
        )
    except Exception:  # noqa: BLE001 - invalid sanitized content stays private
        record = None
        text = ""
        raise CouncilToolDenied("evidence_unavailable") from None


def read_prior_decision(
    context: CouncilToolContext,
    decision_id: object,
) -> PriorDecisionExcerpt:
    """Read one minimized prior decision from the active workspace."""

    if type(decision_id) is not str or re.fullmatch(_DECISION_ID, decision_id) is None:
        raise CouncilToolDenied("decision_unavailable") from None
    record = _registry_decision(context, decision_id)
    if (
        record.organization_id != context.organization_id
        or record.workspace_id != context.workspace_id
        or record.decision_id != decision_id
    ):
        raise CouncilToolDenied("decision_unavailable") from None
    return PriorDecisionExcerpt(
        decision_id=record.decision_id,
        summary=record.summary,
        semantic_digest=record.semantic_digest,
    )


def build_council_tools(context: CouncilToolContext) -> tuple[FunctionTool, ...]:
    """Bind the active context privately and expose only read-only ADK functions."""

    def bound_list_evidence() -> EvidenceCatalog:
        return list_evidence(context)

    def bound_read_evidence_excerpt(
        evidence_id: str,
        start: int,
        length: int,
    ) -> EvidenceExcerpt:
        return read_evidence_excerpt(context, evidence_id, start, length)

    def bound_read_prior_decision(decision_id: str) -> PriorDecisionExcerpt:
        return read_prior_decision(context, decision_id)

    bound_list_evidence.__name__ = "list_evidence"
    bound_read_evidence_excerpt.__name__ = "read_evidence_excerpt"
    bound_read_prior_decision.__name__ = "read_prior_decision"
    return (
        FunctionTool(bound_list_evidence),
        FunctionTool(bound_read_evidence_excerpt),
        FunctionTool(bound_read_prior_decision),
    )
