"""Strict boundary for fictional persona decisions."""

from __future__ import annotations

import json
import math
import re
import time
from datetime import UTC, datetime, timedelta
from datetime import time as datetime_time
from enum import StrEnum
from threading import Event
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from humanwire.domain import EngagementType
from humanwire.model_client import FeatherlessJsonClient, JsonModelClient, ModelFailure

MAX_PERSONA_CONTENT_LENGTH = 600
PERSONA_PROMPT_VERSION = "humanwire.persona-decision/v2"


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


class SyntheticGenerationMode(StrEnum):
    DETERMINISTIC = "deterministic"
    MODEL_ASSISTED = "model_assisted"
    FROZEN_REPLAY = "frozen_replay"


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
    model_identifier: str

    def decide(
        self,
        profile: PersonaProfile,
        context: PersonaContext,
        *,
        deadline: float,
        cancellation: Event,
    ) -> PersonaDecision:
        raise NotImplementedError


class PersonaDecisionEngineFactory(Protocol):
    """Spawn-safe description that constructs an engine inside an isolated worker."""

    model_identifier: str

    def build(self) -> PersonaDecisionEngine:
        raise NotImplementedError


def validate_persona_decision(
    profile: PersonaProfile,
    decision: PersonaDecision,
) -> PersonaDecision:
    """Apply the one authoritative intent and privacy boundary to any engine output."""
    decision = PersonaDecision.model_validate(decision)
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


def persona_response_contract(
    profile: PersonaProfile,
    context: PersonaContext,
) -> dict[str, str | int | None]:
    """Bind model creativity to the current workflow protocol state."""
    profile = PersonaProfile.model_validate(profile)
    context = PersonaContext.model_validate(context)
    allowed = set(profile.allowed_intents)
    prompt = context.delivered_message.casefold().lstrip()

    required: SyntheticIntent | None = None
    required_visibility = PersonaVisibility.SHAREABLE
    content_guidance = "Respond concisely from your supplied role and context only."
    required_content: str | None = None
    if "humanwire draft proposal" in prompt:
        if SyntheticIntent.CHANGE_PROPOSAL in allowed:
            required = SyntheticIntent.CHANGE_PROPOSAL
            content_guidance = (
                "Request one concrete role-specific revision grounded in your prior shared answers."
            )
        elif SyntheticIntent.ACCEPT_PROPOSAL in allowed:
            required = SyntheticIntent.ACCEPT_PROPOSAL
            content_guidance = "State concise acceptance of the reviewed proposal."
        elif SyntheticIntent.APPROVE in allowed:
            required = SyntheticIntent.APPROVE
    elif "humanwire availability request" in prompt:
        required = SyntheticIntent.AVAILABILITY
        availability_date = getattr(profile, "availability_date", context.virtual_time.date())
        start = datetime.combine(availability_date, datetime_time(hour=15), tzinfo=UTC)
        required_content = f"{start.isoformat()}/{(start + timedelta(hours=1)).isoformat()}"
        content_guidance = "Return the exact supplied availability interval."
    elif "evidence confirmation" in prompt and "evidence confirmed" not in prompt:
        required = SyntheticIntent.CONFIRM_EVIDENCE
        content_guidance = "Confirm the recorded evidence without adding new facts."
    elif prompt.startswith("question "):
        required = (
            SyntheticIntent.INTERVIEW_RESPONSE
            if profile.engagement_contract is EngagementType.STRUCTURED_INTERVIEW
            else SyntheticIntent.ANSWER
        )
        prior_interview_answers = sum(
            entry.intent is SyntheticIntent.INTERVIEW_RESPONSE
            for entry in context.own_transcript
        )
        if (
            profile.engagement_contract is EngagementType.STRUCTURED_INTERVIEW
            and prior_interview_answers == 0
        ):
            required_visibility = PersonaVisibility.PRIVATE
            content_guidance = (
                "Record one concise nonshareable risk note from your role without copying "
                "private facts or inventing workflow state."
            )
        elif profile.engagement_contract is EngagementType.STRUCTURED_INTERVIEW:
            content_guidance = (
                "State that Engineering must own the rollback checkpoint before approval, "
                "then answer the question from your role without inventing workflow state."
            )
        else:
            content_guidance = (
                "Answer the question from your role. Do not reveal private facts or invent "
                "workflow state."
            )
    elif (
        profile.engagement_contract is EngagementType.STRUCTURED_INTERVIEW
        and "humanwire interview" in prompt
    ):
        if "prior registered route" in prompt:
            required = SyntheticIntent.ACKNOWLEDGE
            content_guidance = "Acknowledge the alternate-route interview in one sentence."
        else:
            required = SyntheticIntent.SILENCE
            content_guidance = "Record that no response is sent on this outreach attempt."
    elif "humanwire approval review" in prompt:
        required = (
            SyntheticIntent.APPROVE
            if SyntheticIntent.APPROVE in allowed
            else SyntheticIntent.CHANGE
        )
    elif (
        "humanwire acknowledgement" in prompt
        or "humanwire quick response" in prompt
        or "humanwire interview" in prompt
    ) and "reply ack" in prompt:
        required = SyntheticIntent.ACKNOWLEDGE
        content_guidance = "Acknowledge receipt in one concise sentence."
    elif "humanwire update" in prompt:
        required = SyntheticIntent.SILENCE
        content_guidance = "Record that no reply is required."
    elif len(allowed) == 1:
        required = next(iter(allowed))

    if required is None or required not in allowed:
        raise ValueError("persona response stage is not supported")
    if required_content is None:
        content_guidance += (
            " Use plain prose without slash or backslash characters, URLs, paths, "
            "email addresses, credentials, or command prefixes."
        )
    return {
        "required_time_offset_seconds": 1,
        "required_intent": required.value,
        "required_visibility": required_visibility.value,
        "required_content": required_content,
        "content_guidance": content_guidance,
    }


def persona_decision_output_schema(
    profile: PersonaProfile,
    context: PersonaContext,
) -> dict[str, object]:
    """Return a strict stage-bound JSON schema for providers that support it."""
    contract = persona_response_contract(profile, context)
    schema = PersonaDecision.model_json_schema()
    definitions = schema["$defs"]
    definitions["SyntheticIntent"]["enum"] = [contract["required_intent"]]
    definitions["PersonaVisibility"]["enum"] = [contract["required_visibility"]]
    schema["properties"]["time_offset_seconds"]["minimum"] = contract[
        "required_time_offset_seconds"
    ]
    schema["properties"]["time_offset_seconds"]["maximum"] = contract[
        "required_time_offset_seconds"
    ]
    schema["required"] = [
        "time_offset_seconds",
        "intent",
        "content",
        "visibility",
    ]
    if contract["required_content"] is not None:
        schema["properties"]["content"]["enum"] = [contract["required_content"]]
    return schema


def validate_persona_stage_decision(
    profile: PersonaProfile,
    context: PersonaContext,
    decision: PersonaDecision,
) -> PersonaDecision:
    """Validate both persona policy and the protocol stage selected by HumanWire."""
    decision = validate_persona_decision(profile, decision)
    contract = persona_response_contract(profile, context)
    if decision.time_offset_seconds != contract["required_time_offset_seconds"]:
        raise ValueError("persona decision did not match the current response stage")
    if decision.intent.value != contract["required_intent"]:
        raise ValueError("persona decision did not match the current response stage")
    if decision.visibility.value != contract["required_visibility"]:
        raise ValueError("persona decision visibility did not match the current response stage")
    required_content = contract["required_content"]
    if required_content is not None and decision.content != required_content:
        raise ValueError("persona decision content did not match the current response stage")
    return decision


def persona_prompt_payload(
    profile: PersonaProfile,
    context: PersonaContext,
) -> tuple[str, str]:
    system = (
        "You are one HumanWire stakeholder. Use only the supplied role, constraints, "
        "allowed actions, and your own conversation. Follow response_contract exactly; "
        "HumanWire selects the protocol action while you supply role-appropriate content. "
        "Return one typed response. "
        "Never invent identity, routing, authority, credentials, tools, or workflow state."
    )
    user = json.dumps(
        {
            "profile": profile.model_dump(mode="json"),
            "context": context.model_dump(mode="json"),
            "response_contract": persona_response_contract(profile, context),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return system, user


class FeatherlessPersonaDecisionEngine:
    """Translate a bounded fictional persona prompt to a validated decision."""

    def __init__(self, client: JsonModelClient, model_identifier: str) -> None:
        if not model_identifier or len(model_identifier) > 200:
            raise ValueError("model identifier must be bounded")
        self._client = client
        self.model_identifier = model_identifier

    def decide(
        self,
        profile: PersonaProfile,
        context: PersonaContext,
        *,
        deadline: float,
        cancellation: Event,
    ) -> PersonaDecision:
        if cancellation.is_set() or not math.isfinite(deadline):
            raise ModelFailure("timeout")
        if time.monotonic() >= deadline:
            raise ModelFailure("timeout")
        system, shared_user = persona_prompt_payload(profile, context)
        system += " Return one JSON object matching output_schema exactly."
        user_payload = json.loads(shared_user)
        response_contract = user_payload["response_contract"]
        content_schema: str | list[str] = "non-empty string, maximum 600 characters"
        if response_contract["required_content"] is not None:
            content_schema = [response_contract["required_content"]]
        user = json.dumps(
            {
                **user_payload,
                "output_schema": {
                    "time_offset_seconds": [
                        response_contract["required_time_offset_seconds"]
                    ],
                    "intent": [response_contract["required_intent"]],
                    "content": content_schema,
                    "visibility": [response_contract["required_visibility"]],
                },
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        remaining = deadline - time.monotonic()
        if cancellation.is_set() or remaining <= 0:
            raise ModelFailure("timeout")
        decision = PersonaDecision.model_validate_json(
            json.dumps(
                self._client.complete_json(
                    system,
                    user,
                    timeout_seconds=remaining,
                )
            )
        )
        if cancellation.is_set() or time.monotonic() >= deadline:
            raise ModelFailure("timeout")
        return validate_persona_stage_decision(profile, context, decision)


class FeatherlessPersonaDecisionEngineFactory(StrictPersonaModel):
    """Serializable private configuration for constructing the direct adapter in a child."""

    api_key: SecretStr
    model_identifier: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=2048)
    max_tokens: int = Field(default=900, ge=1, le=4096)

    def build(self) -> FeatherlessPersonaDecisionEngine:
        client = FeatherlessJsonClient(
            api_key=self.api_key.get_secret_value(),
            model=self.model_identifier,
            base_url=self.base_url,
            max_tokens=self.max_tokens,
        )
        return FeatherlessPersonaDecisionEngine(client, self.model_identifier)
