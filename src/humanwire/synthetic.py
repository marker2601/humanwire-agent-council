"""Strict, non-live schema for HumanWire synthetic persona transcripts."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from humanwire.domain import Channel

SUPPORTED_SCHEMA_VERSION = "humanwire.synthetic/v1"
MAX_CONTENT_LENGTH = 600
_STABLE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
_DIGEST_PATTERN = r"^[0-9a-f]{64}$"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SyntheticIntent(StrEnum):
    ACKNOWLEDGE = "acknowledge"
    ANSWER = "answer"
    INTERVIEW_RESPONSE = "interview_response"
    CONFIRM_EVIDENCE = "confirm_evidence"
    APPROVE = "approve"
    CHANGE = "change"
    AVAILABILITY = "availability"
    SILENCE = "silence"


class SyntheticProvenance(_StrictModel):
    """Required labels that prevent a fixture from being represented as live proof."""

    proof_class: Literal["synthetic_multi_persona"]
    actor_type: Literal["simulated_persona"]
    identity_source: Literal["synthetic_fixture"]
    transport: Literal["fake_caspian"]
    human_attested: Literal[False]
    live_provider_verified: Literal[False]


class SyntheticPersona(_StrictModel):
    persona_id: str = Field(pattern=_STABLE_ID_PATTERN)
    display_name: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=1, max_length=254)
    channels: list[Channel] = Field(min_length=1, max_length=2)
    allowed_intents: list[SyntheticIntent] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def has_synthetic_identity_and_unique_options(self) -> Self:
        if not self.email.endswith("@example.test"):
            raise ValueError("synthetic persona email must use the .example.test domain")
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("persona channels must be unique")
        if len(set(self.allowed_intents)) != len(self.allowed_intents):
            raise ValueError("persona intents must be unique")
        return self


class SyntheticScenario(_StrictModel):
    schema_version: Literal["humanwire.synthetic/v1"]
    scenario_id: str = Field(pattern=_STABLE_ID_PATTERN)
    personas: list[SyntheticPersona] = Field(min_length=1, max_length=32)
    provenance: SyntheticProvenance

    @model_validator(mode="after")
    def has_unique_personas(self) -> Self:
        persona_ids = [persona.persona_id for persona in self.personas]
        if len(set(persona_ids)) != len(persona_ids):
            raise ValueError("persona IDs must be unique")
        return self


class SyntheticAction(_StrictModel):
    schema_version: Literal["humanwire.synthetic/v1"]
    action_id: str = Field(pattern=_STABLE_ID_PATTERN)
    persona_id: str = Field(pattern=_STABLE_ID_PATTERN)
    channel: Channel
    timestamp: datetime
    local_sequence: int = Field(ge=0)
    trigger_id: str = Field(pattern=_STABLE_ID_PATTERN)
    trigger_digest: str = Field(pattern=_DIGEST_PATTERN)
    intent: SyntheticIntent
    content: str = Field(min_length=1, max_length=MAX_CONTENT_LENGTH)

    @model_validator(mode="after")
    def has_utc_offset(self) -> Self:
        if self.timestamp.utcoffset() is None:
            raise ValueError("synthetic action timestamps require a timezone offset")
        return self


class SyntheticTranscript(_StrictModel):
    scenario: SyntheticScenario
    outbound_digests: dict[str, str] = Field(min_length=1, max_length=256)
    actions: list[SyntheticAction] = Field(min_length=1, max_length=512)
    digest: str = Field(pattern=_DIGEST_PATTERN)

    @classmethod
    def create(
        cls,
        *,
        scenario: SyntheticScenario,
        outbound_digests: dict[str, str],
        actions: list[SyntheticAction],
    ) -> Self:
        """Build a validated transcript with its canonical SHA-256 digest."""
        payload = {
            "scenario": scenario.model_dump(mode="json"),
            "outbound_digests": outbound_digests,
            "actions": [action.model_dump(mode="json") for action in actions],
        }
        return cls.model_validate_json(
            json.dumps({**payload, "digest": _digest_payload(payload)})
        )

    @model_validator(mode="after")
    def is_valid_transcript(self) -> Self:
        _validate_outbound_digests(self.outbound_digests)
        persona_by_id = {persona.persona_id: persona for persona in self.scenario.personas}
        action_ids = [action.action_id for action in self.actions]
        if len(set(action_ids)) != len(action_ids):
            raise ValueError("action IDs must be unique")

        previous_order: tuple[datetime, str, int] | None = None
        trigger_ids: list[str] = []
        for action in self.actions:
            persona = persona_by_id.get(action.persona_id)
            if persona is None:
                raise ValueError(f"action {action.action_id} references unknown persona {action.persona_id}")
            if action.channel not in persona.channels:
                raise ValueError(
                    f"persona {action.persona_id} does not support channel {action.channel.value}"
                )
            if action.intent not in persona.allowed_intents:
                raise ValueError(f"persona {action.persona_id} is not allowed intent {action.intent.value}")

            order = (action.timestamp, action.persona_id, action.local_sequence)
            if previous_order is not None and order <= previous_order:
                raise ValueError("actions must use strict deterministic order")
            previous_order = order

            expected_digest = self.outbound_digests.get(action.trigger_id)
            if expected_digest is None:
                raise ValueError(f"action {action.action_id} references an unknown trigger")
            if action.trigger_digest != expected_digest:
                raise ValueError(f"action {action.action_id} has a mismatched trigger digest")
            trigger_ids.append(action.trigger_id)

        if len(set(trigger_ids)) != len(trigger_ids) or set(trigger_ids) != set(self.outbound_digests):
            raise ValueError("outbound trigger pairing must be exact and one-to-one")

        if self.digest != transcript_digest(self):
            raise ValueError("synthetic transcript digest mismatch")
        return self


def _validate_outbound_digests(outbound_digests: dict[str, str]) -> None:
    for trigger_id, digest in outbound_digests.items():
        if not re.fullmatch(_STABLE_ID_PATTERN, trigger_id):
            raise ValueError("outbound trigger IDs must be ASCII stable IDs")
        if not re.fullmatch(_DIGEST_PATTERN, digest):
            raise ValueError("outbound trigger digests must be SHA-256 hex digests")


def _digest_payload(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def transcript_digest(transcript: SyntheticTranscript) -> str:
    """Return the SHA-256 digest of the canonical transcript without its digest field."""
    payload = transcript.model_dump(mode="json", exclude={"digest"})
    return _digest_payload(payload)


def validate_transcript(transcript: SyntheticTranscript | dict[str, object]) -> SyntheticTranscript:
    """Re-validate an in-memory transcript and fail closed on integrity errors."""
    if isinstance(transcript, SyntheticTranscript):
        return SyntheticTranscript.model_validate_json(transcript.model_dump_json())
    return SyntheticTranscript.model_validate(transcript)


def load_transcript(path: str | Path) -> SyntheticTranscript:
    """Load a transcript JSON file through the strict, integrity-checking model."""
    return SyntheticTranscript.model_validate_json(Path(path).read_text(encoding="utf-8"))
