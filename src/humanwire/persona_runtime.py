"""Strict boundary for fictional persona decisions."""

from __future__ import annotations

import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from humanwire.domain import EngagementType
from humanwire.model_client import JsonModelClient

MAX_PERSONA_CONTENT_LENGTH = 600
PERSONA_PROMPT_VERSION = "humanwire.persona-decision/v1"


class StrictPersonaModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SyntheticIntent(StrEnum):
    ACKNOWLEDGE = "acknowledge"
    ANSWER = "answer"
    INTERVIEW_RESPONSE = "interview_response"
    CONFIRM_EVIDENCE = "confirm_evidence"
    APPROVE = "approve"
    CHANGE = "change"
    AVAILABILITY = "availability"
    ACCEPT_PROPOSAL = "accept_proposal"
    CHANGE_PROPOSAL = "change_proposal"
    SILENCE = "silence"
    ERROR = "error"


class SyntheticProvenance(StrictPersonaModel):
    proof_class: Literal["synthetic_multi_persona"]
    actor_type: Literal["simulated_persona"]
    identity_source: Literal["synthetic_fixture"]
    transport: Literal["fake_caspian"]
    human_attested: Literal[False]
    live_provider_verified: Literal[False]


class PersonaVisibility(StrEnum):
    SHAREABLE = "shareable"
    ANONYMOUS = "anonymous"
    PRIVATE = "private"


class PersonaTranscriptEntry(StrictPersonaModel):
    timestamp: datetime
    local_sequence: int = Field(ge=1)
    intent: SyntheticIntent
    content: str = Field(min_length=1, max_length=MAX_PERSONA_CONTENT_LENGTH)


class PersonaProfile(StrictPersonaModel):
    role: str = Field(min_length=1, max_length=200)
    private_facts: tuple[str, ...] = Field(max_length=8)
    allowed_intents: tuple[SyntheticIntent, ...] = Field(min_length=1, max_length=8)
    engagement_contract: EngagementType


class PersonaContext(StrictPersonaModel):
    delivered_message: str = Field(min_length=1, max_length=MAX_PERSONA_CONTENT_LENGTH)
    own_inbox: tuple[str, ...] = Field(min_length=1, max_length=64)
    own_transcript: tuple[PersonaTranscriptEntry, ...] = Field(max_length=64)
    virtual_time: datetime


class PersonaDecision(StrictPersonaModel):
    time_offset_seconds: int = Field(ge=0, le=60)
    intent: SyntheticIntent
    content: str = Field(min_length=1, max_length=MAX_PERSONA_CONTENT_LENGTH)
    visibility: PersonaVisibility = PersonaVisibility.SHAREABLE


class PersonaDecisionEngine(Protocol):
    def decide(self, profile: PersonaProfile, context: PersonaContext) -> PersonaDecision:
        raise NotImplementedError


class FeatherlessPersonaDecisionEngine:
    """Translate a bounded fictional persona prompt to a validated decision."""

    def __init__(self, client: JsonModelClient, model_identifier: str) -> None:
        if not model_identifier or len(model_identifier) > 200:
            raise ValueError("model identifier must be bounded")
        self._client = client
        self.model_identifier = model_identifier

    def decide(self, profile: PersonaProfile, context: PersonaContext) -> PersonaDecision:
        system = (
            "You are one fictional HumanWire simulation persona. "
            "Use only the supplied profile and your own context. "
            "Return one JSON object matching output_schema exactly. "
            "Never invent identity, routing, authority, credentials, tools, or workflow state."
        )
        user = json.dumps(
            {
                "profile": profile.model_dump(mode="json"),
                "context": context.model_dump(mode="json"),
                "output_schema": {
                    "time_offset_seconds": "integer 0..60",
                    "intent": [item.value for item in profile.allowed_intents],
                    "content": "non-empty string, maximum 600 characters",
                    "visibility": [item.value for item in PersonaVisibility],
                },
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        decision = PersonaDecision.model_validate_json(
            json.dumps(self._client.complete_json(system, user))
        )
        if decision.intent not in profile.allowed_intents:
            raise ValueError("persona decision used a disallowed intent")
        folded = decision.content.casefold()
        if any(fact.casefold() in folded for fact in profile.private_facts):
            raise ValueError("persona decision exposed a private fixture fact")
        if re.search(
            r"\bHW-[A-F0-9]{8}\b|\b[^\s@]+@[^\s@]+\b|"
            r"\b(?:api[_-]?key|authorization|route_id|conversation_id|connection_id|assignment_id)\b|"
            r"\b(?:sender(?:(?:[_-]|\s+)address)?|route(?:(?:[_-]|\s+)id)?|"
            r"conversation(?:(?:[_-]|\s+)id)?|connection(?:(?:[_-]|\s+)id)?|"
            r"message(?:(?:[_-]|\s+)id)?|assignment(?:(?:[_-]|\s+)id)?|"
            r"destination|token)\b\s*(?:[:=])|"
            r"^\s*/(?:mandate|go|confirm|decide|available)\b",
            decision.content,
            re.IGNORECASE,
        ):
            raise ValueError("persona decision contained forbidden identity or command data")
        return decision
