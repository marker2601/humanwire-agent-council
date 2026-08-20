"""Strict, evidence-bound contracts for the DecisionOS specialist council."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from humanwire.decisionos_models import (
    DecisionOSContext,
    WorkspacePlaybook,
)

_ULID = r"[0-9A-HJKMNP-TV-Z]{26}"
_WORKSPACE_ID = rf"^wrk_{_ULID}$"
_CANONICAL_ID = r"^[a-z][a-z0-9_]{2,79}$"
_CLAIM_ID = r"^claim_[a-z0-9_]{1,64}$"
_CANDIDATE_ID = r"^candidate_[a-z0-9_]{1,64}$"
_CHALLENGE_ID = r"^challenge_[a-z0-9_]{1,64}$"
_RECOMMENDATION_ID = r"^recommendation_[a-z0-9_]{1,64}$"
_DECISION_ID = r"^decision_[a-z0-9_]{1,64}$"
_EVIDENCE_ID = r"^evidence_[a-z0-9_]{1,64}$"
_POLICY_VERSION = r"^council-v[1-9][0-9]*$"
_EMAIL = re.compile(r"(?i)(?<![a-z0-9._%+-])[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}")
_PRIVATE_PATH = re.compile(
    r"(?i)(?:\b[a-z]:\\|\\\\|file://|/(?:home|users|private|var/(?:run|lib))/)"
)
_SECRET_MARKER = re.compile(
    r"(?i)(?:authorization\s*:\s*bearer|api[_-]?key\s*=|token\s*=|"
    r"-----begin|\bAIza[0-9A-Za-z_-]{8,}|\bsk-[0-9A-Za-z_-]{8,})"
)

COUNCIL_SPECIALIST_IDS = frozenset(
    {
        "objective_framing",
        "market_intelligence",
        "financial_analysis",
        "product_technical",
        "risk_compliance",
        "stakeholder_authority",
        "investor_fit",
        "diligence_readiness",
        "decision_synthesis",
        "red_team",
        "final_synthesis",
    }
)

SafeQuestion = Annotated[str, Field(min_length=1, max_length=300)]


def _validate_public_text(value: str) -> str:
    if type(value) is not str:
        raise ValueError("council text must be an exact string")
    if value != value.strip() or unicodedata.normalize("NFC", value) != value:
        raise ValueError("council text must be canonical")
    if not value.isprintable() or _EMAIL.search(value):
        raise ValueError("council text contains private or unsafe content")
    if _PRIVATE_PATH.search(value) or _SECRET_MARKER.search(value):
        raise ValueError("council text contains private or unsafe content")
    return value


class _CouncilModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @field_validator("*", mode="after")
    @classmethod
    def public_strings_are_safe(cls, value: object) -> object:
        if type(value) is str:
            _validate_public_text(value)
        elif type(value) in {tuple, frozenset}:
            for item in value:
                if type(item) is str:
                    _validate_public_text(item)
        return value


class ClaimClassification(StrEnum):
    CONFIRMED_FACT = "confirmed_fact"
    SOURCE_ASSERTION = "source_assertion"
    MODEL_INFERENCE = "model_inference"
    HUMAN_ASSUMPTION = "human_assumption"
    UNRESOLVED_CONFLICT = "unresolved_conflict"


class ChallengeSeverity(StrEnum):
    ADVISORY = "advisory"
    MATERIAL = "material"
    BLOCKING = "blocking"


class EvidenceClaim(_CouncilModel):
    claim_id: str = Field(pattern=_CLAIM_ID)
    statement: str = Field(min_length=1, max_length=600)
    classification: ClaimClassification = Field(strict=False)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=20)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def has_truthful_evidence_binding(self) -> Self:
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence IDs must be unique")
        if any(re.fullmatch(_EVIDENCE_ID, item) is None for item in self.evidence_ids):
            raise ValueError("evidence ID is invalid")
        if self.classification in {
            ClaimClassification.CONFIRMED_FACT,
            ClaimClassification.SOURCE_ASSERTION,
        } and not self.evidence_ids:
            raise ValueError("sourced claims require evidence")
        if (
            self.classification is ClaimClassification.HUMAN_ASSUMPTION
            and self.evidence_ids
        ):
            raise ValueError("human assumptions cannot be represented as sourced")
        return self


class CouncilCandidate(_CouncilModel):
    candidate_id: str = Field(pattern=_CANDIDATE_ID)
    specialist_id: str = Field(pattern=_CANONICAL_ID)
    summary: str = Field(min_length=1, max_length=600)
    claims: tuple[EvidenceClaim, ...] = Field(min_length=1, max_length=20)
    questions: tuple[SafeQuestion, ...] = Field(default=(), max_length=10)
    recommended_action: str = Field(min_length=1, max_length=400)
    policy_version: str = Field(pattern=_POLICY_VERSION)

    @model_validator(mode="after")
    def binds_known_specialist_and_unique_claims(self) -> Self:
        if self.specialist_id not in COUNCIL_SPECIALIST_IDS:
            raise ValueError("specialist is not registered")
        claim_ids = tuple(item.claim_id for item in self.claims)
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("claim IDs must be unique")
        return self


class CouncilChallenge(_CouncilModel):
    challenge_id: str = Field(pattern=_CHALLENGE_ID)
    challenger_id: str = Field(pattern=_CANONICAL_ID)
    target_candidate_id: str = Field(pattern=_CANDIDATE_ID)
    challenged_claim_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    severity: ChallengeSeverity = Field(strict=False)
    issue: str = Field(min_length=1, max_length=600)
    required_action: str = Field(min_length=1, max_length=400)
    policy_version: str = Field(pattern=_POLICY_VERSION)

    @model_validator(mode="after")
    def binds_registered_challenger_and_unique_claims(self) -> Self:
        if self.challenger_id not in COUNCIL_SPECIALIST_IDS:
            raise ValueError("challenger is not registered")
        if len(set(self.challenged_claim_ids)) != len(self.challenged_claim_ids):
            raise ValueError("challenged claim IDs must be unique")
        if any(re.fullmatch(_CLAIM_ID, item) is None for item in self.challenged_claim_ids):
            raise ValueError("challenged claim ID is invalid")
        return self


class CouncilRecommendation(_CouncilModel):
    recommendation_id: str = Field(pattern=_RECOMMENDATION_ID)
    summary: str = Field(min_length=1, max_length=800)
    claims: tuple[EvidenceClaim, ...] = Field(min_length=1, max_length=40)
    challenges: tuple[CouncilChallenge, ...] = Field(default=(), max_length=20)
    recommended_action: str = Field(min_length=1, max_length=600)
    required_human_action: str = Field(min_length=1, max_length=400)
    source_candidate_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    policy_version: str = Field(pattern=_POLICY_VERSION)

    @model_validator(mode="after")
    def has_consistent_unique_bindings(self) -> Self:
        claim_ids = tuple(item.claim_id for item in self.claims)
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("recommendation claim IDs must be unique")
        if len(set(self.source_candidate_ids)) != len(self.source_candidate_ids):
            raise ValueError("source candidate IDs must be unique")
        if any(
            re.fullmatch(_CANDIDATE_ID, item) is None
            for item in self.source_candidate_ids
        ):
            raise ValueError("source candidate ID is invalid")
        known_claims = set(claim_ids)
        for challenge in self.challenges:
            if challenge.policy_version != self.policy_version:
                raise ValueError("challenge policy version must match recommendation")
            if not set(challenge.challenged_claim_ids) <= known_claims:
                raise ValueError("challenge must reference recommendation claims")
        return self

    @property
    def semantic_digest(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return hashlib.sha256(payload).hexdigest()


class CouncilRunRequest(_CouncilModel):
    context: DecisionOSContext
    workspace_id: str = Field(pattern=_WORKSPACE_ID)
    decision_id: str = Field(pattern=_DECISION_ID)
    playbook_id: WorkspacePlaybook = Field(strict=False)
    objective: str = Field(min_length=1, max_length=2_000)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=100)
    policy_version: str = Field(pattern=_POLICY_VERSION)

    @model_validator(mode="after")
    def has_unique_canonical_evidence(self) -> Self:
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence IDs must be unique")
        if any(re.fullmatch(_EVIDENCE_ID, item) is None for item in self.evidence_ids):
            raise ValueError("evidence ID is invalid")
        return self

    @property
    def organization_id(self) -> str:
        return self.context.organization_id


class CouncilSpecialist(_CouncilModel):
    specialist_id: str = Field(pattern=_CANONICAL_ID)
    display_name: str = Field(min_length=1, max_length=80)
    purpose: str = Field(min_length=1, max_length=300)
    tool_allowlist: frozenset[str] = Field(max_length=8)
    required_inputs: tuple[str, ...] = Field(min_length=1, max_length=12)
    output_schema: str = Field(pattern=r"^[A-Z][A-Za-z0-9]{2,79}$")
    timeout_seconds: int = Field(ge=1, le=120)
    token_budget: int = Field(ge=128, le=4_096)
    maximum_attempts: int = Field(ge=1, le=2)
    policy_version: str = Field(pattern=_POLICY_VERSION)

    @model_validator(mode="after")
    def has_registered_id_and_safe_tools(self) -> Self:
        if self.specialist_id not in COUNCIL_SPECIALIST_IDS:
            raise ValueError("specialist is not registered")
        allowed = {
            "list_evidence",
            "read_evidence_excerpt",
            "read_prior_decision",
        }
        if not self.tool_allowlist <= allowed:
            raise ValueError("specialist tool is not read-only and allowlisted")
        if len(set(self.required_inputs)) != len(self.required_inputs):
            raise ValueError("required inputs must be unique")
        if any(re.fullmatch(_CANONICAL_ID, item) is None for item in self.required_inputs):
            raise ValueError("required input is invalid")
        return self
