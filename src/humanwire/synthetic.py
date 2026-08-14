"""Strict, non-live schema for HumanWire synthetic persona transcripts."""

from __future__ import annotations

import hashlib
import heapq
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

from humanwire.alignment import NegotiationCoordinator
from humanwire.caspian_gateway import CaspianGateway
from humanwire.config import Settings
from humanwire.database import create_session_factory
from humanwire.directory import InitiatorPolicy, OrganizationDirectory, OrganizationDocument
from humanwire.domain import (
    Channel,
    ContactRoute,
    Direction,
    EngagementType,
    MandatePlan,
    Person,
    PlannedStakeholder,
)
from humanwire.evidence import EvidenceDraft, RuleBasedEvidenceExtractor
from humanwire.offline_caspian import (
    CapturedDelivery,
    OfflineCaspianClient,
    email_envelope,
    telegram_envelope,
)
from humanwire.planning import ResolvedPlan
from humanwire.repository import SqlAlchemyHumanWireRepository
from humanwire.workflow import HumanWireWorkflow

SUPPORTED_SCHEMA_VERSION = "humanwire.synthetic/v1"
MAX_CONTENT_LENGTH = 600
_STABLE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
_DIGEST_PATTERN = r"^[0-9a-f]{64}$"
_TOKEN_PATTERN = re.compile(r"\bHW-[A-F0-9]{8}\b")
_EMAIL_PATTERN = re.compile(r"\b[^\s@]+@[^\s@]+\b")
_FIXED_TIME = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


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
    ACCEPT_PROPOSAL = "accept_proposal"
    CHANGE_PROPOSAL = "change_proposal"
    SILENCE = "silence"
    ERROR = "error"


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
    private_facts: list[str] = Field(default_factory=list, max_length=8, exclude=True)
    private_fact_digests: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def has_synthetic_identity_and_unique_options(self) -> Self:
        if not self.email.endswith("@example.test"):
            raise ValueError("synthetic persona email must use the .example.test domain")
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("persona channels must be unique")
        if len(set(self.allowed_intents)) != len(self.allowed_intents):
            raise ValueError("persona intents must be unique")
        if any(not fact or len(fact) > MAX_CONTENT_LENGTH for fact in self.private_facts):
            raise ValueError("private fixture facts must be bounded non-empty strings")
        if any(not re.fullmatch(_DIGEST_PATTERN, item) for item in self.private_fact_digests):
            raise ValueError("private fixture digests must be SHA-256 hex digests")
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


def default_synthetic_scenario() -> SyntheticScenario:
    """Return the isolated six-contract scenario used by the public CLI proof."""
    return SyntheticScenario(
        schema_version=SUPPORTED_SCHEMA_VERSION,
        scenario_id="launch-v1",
        personas=[
            SyntheticPersona(
                persona_id="synthetic-manager",
                display_name="Synthetic Manager",
                role="Simulation manager",
                email="synthetic-manager@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[SyntheticIntent.AVAILABILITY],
            ),
            SyntheticPersona(
                persona_id="inform",
                display_name="Inform Persona",
                role="Delivery owner",
                email="inform@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[SyntheticIntent.SILENCE],
            ),
            SyntheticPersona(
                persona_id="ack",
                display_name="Acknowledge Persona",
                role="Executive owner",
                email="ack@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[
                    SyntheticIntent.ACKNOWLEDGE,
                    SyntheticIntent.ACCEPT_PROPOSAL,
                ],
            ),
            SyntheticPersona(
                persona_id="quick-a",
                display_name="Quick Persona A",
                role="Program owner",
                email="quick-a@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[
                    SyntheticIntent.ACKNOWLEDGE,
                    SyntheticIntent.ANSWER,
                    SyntheticIntent.CONFIRM_EVIDENCE,
                    SyntheticIntent.ACCEPT_PROPOSAL,
                ],
            ),
            SyntheticPersona(
                persona_id="quick-b",
                display_name="Quick Persona B",
                role="Operations owner",
                email="quick-b@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[
                    SyntheticIntent.ACKNOWLEDGE,
                    SyntheticIntent.ANSWER,
                    SyntheticIntent.CONFIRM_EVIDENCE,
                    SyntheticIntent.ACCEPT_PROPOSAL,
                ],
            ),
            SyntheticPersona(
                persona_id="structured",
                display_name="Structured Persona",
                role="People owner",
                email="structured@example.test",
                channels=[Channel.EMAIL, Channel.TELEGRAM],
                allowed_intents=[
                    SyntheticIntent.ACKNOWLEDGE,
                    SyntheticIntent.INTERVIEW_RESPONSE,
                    SyntheticIntent.CONFIRM_EVIDENCE,
                    SyntheticIntent.SILENCE,
                    SyntheticIntent.CHANGE_PROPOSAL,
                    SyntheticIntent.AVAILABILITY,
                ],
                private_facts=["PRIVATE-PERSONA-SENTINEL"],
            ),
            SyntheticPersona(
                persona_id="approval",
                display_name="Approval Persona",
                role="Approval owner",
                email="approval@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[
                    SyntheticIntent.APPROVE,
                    SyntheticIntent.ACCEPT_PROPOSAL,
                ],
            ),
            SyntheticPersona(
                persona_id="availability",
                display_name="Availability Persona",
                role="Scheduling owner",
                email="availability@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[
                    SyntheticIntent.AVAILABILITY,
                    SyntheticIntent.ACCEPT_PROPOSAL,
                ],
            ),
            SyntheticPersona(
                persona_id="approval-change",
                display_name="Change Persona",
                role="Independent change authority",
                email="approval-change@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[SyntheticIntent.CHANGE],
            ),
        ],
        provenance=SyntheticProvenance(
            proof_class="synthetic_multi_persona",
            actor_type="simulated_persona",
            identity_source="synthetic_fixture",
            transport="fake_caspian",
            human_attested=False,
            live_provider_verified=False,
        ),
    )


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
            if (
                action.intent not in persona.allowed_intents
                and action.intent not in {SyntheticIntent.SILENCE, SyntheticIntent.ERROR}
            ):
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


class _PersonaTranscriptEntry(_StrictModel):
    timestamp: datetime
    local_sequence: int = Field(ge=1)
    intent: SyntheticIntent
    content: str = Field(min_length=1, max_length=MAX_CONTENT_LENGTH)


class _PolicyProfile(_StrictModel):
    """The only scenario-derived data retained by a persona policy."""

    role: str = Field(min_length=1, max_length=200)
    private_facts: tuple[str, ...] = Field(max_length=8)
    allowed_intents: tuple[SyntheticIntent, ...] = Field(min_length=1, max_length=8)
    engagement_contract: EngagementType


class _PersonaContext(_StrictModel):
    """The complete and deliberately narrow view supplied to one policy."""

    delivered_message: str = Field(min_length=1, max_length=MAX_CONTENT_LENGTH)
    own_inbox: tuple[str, ...] = Field(min_length=1, max_length=64)
    own_transcript: tuple[_PersonaTranscriptEntry, ...] = Field(max_length=64)
    virtual_time: datetime


class _PersonaDecision(_StrictModel):
    time_offset_seconds: int = Field(ge=1, le=60)
    intent: SyntheticIntent
    content: str = Field(min_length=1, max_length=MAX_CONTENT_LENGTH)


class SyntheticInboundEnvelope(_StrictModel):
    """Safe metadata proving that the orchestrator, not a persona, owned identity."""

    persona_id: str = Field(pattern=_STABLE_ID_PATTERN)
    channel: Channel
    message_id: str = Field(pattern=_STABLE_ID_PATTERN)
    conversation_id: str = Field(pattern=_STABLE_ID_PATTERN)
    connection_id: str = Field(pattern=_STABLE_ID_PATTERN)
    sender_address: str = Field(min_length=1, max_length=254)


@dataclass(frozen=True)
class SyntheticRunResult:
    transcript: SyntheticTranscript
    database_path: Path
    gateway_handler_count: int
    inbound_envelopes: tuple[SyntheticInboundEnvelope, ...]
    captured_deliveries: tuple[CapturedDelivery, ...]
    model_client_configured: bool
    final_state: str
    terminal_states: tuple[str, ...]


class _DeterministicPersonaPolicy:
    """Shared mechanics for a policy isolated to a sanitized frozen profile."""

    def __init__(self, profile: _PolicyProfile) -> None:
        self.profile = profile
        self.complete = False

    def respond(self, context: _PersonaContext) -> _PersonaDecision:
        intent, content = self._choose(context)
        if intent not in self.profile.allowed_intents:
            raise ValueError("persona strategy chose an intent outside its profile")
        return _PersonaDecision(
            time_offset_seconds=1,
            intent=intent,
            content=content,
        )

    def _choose(self, context: _PersonaContext) -> tuple[SyntheticIntent, str]:
        raise NotImplementedError


class _InformPolicy(_DeterministicPersonaPolicy):
    def _choose(self, context: _PersonaContext) -> tuple[SyntheticIntent, str]:
        del context
        self.complete = True
        return SyntheticIntent.SILENCE, "synthetic_silence"


class _AcknowledgePolicy(_DeterministicPersonaPolicy):
    def _choose(self, context: _PersonaContext) -> tuple[SyntheticIntent, str]:
        if "humanwire draft proposal" in context.delivered_message.casefold():
            return SyntheticIntent.ACCEPT_PROPOSAL, "Accepted."
        self.complete = True
        return SyntheticIntent.ACKNOWLEDGE, "Acknowledged."


class _QuickResponsePolicy(_DeterministicPersonaPolicy):
    def _choose(self, context: _PersonaContext) -> tuple[SyntheticIntent, str]:
        prompt = context.delivered_message.casefold()
        if "humanwire draft proposal" in prompt:
            return SyntheticIntent.ACCEPT_PROPOSAL, "Accepted."
        if "evidence confirmation" in prompt and "evidence confirmed" not in prompt:
            self.complete = True
            return SyntheticIntent.CONFIRM_EVIDENCE, "Confirmed."
        if prompt.startswith("question "):
            return SyntheticIntent.ANSWER, "Launch date is 2026-09-01."
        return SyntheticIntent.ACKNOWLEDGE, "Acknowledged."


class _StructuredInterviewPolicy(_DeterministicPersonaPolicy):
    def _choose(self, context: _PersonaContext) -> tuple[SyntheticIntent, str]:
        prompt = context.delivered_message.casefold()
        if "humanwire availability request" in prompt:
            return (
                SyntheticIntent.AVAILABILITY,
                "2026-08-13T15:00:00+00:00/2026-08-13T16:00:00+00:00",
            )
        if "humanwire draft proposal" in prompt:
            return SyntheticIntent.CHANGE_PROPOSAL, "Keep human review on the agenda."
        if "evidence confirmation" in prompt and "evidence confirmed" not in prompt:
            self.complete = True
            return SyntheticIntent.CONFIRM_EVIDENCE, "Confirmed."
        if prompt.startswith("question "):
            prior_answers = sum(
                item.intent is SyntheticIntent.INTERVIEW_RESPONSE
                for item in context.own_transcript
            )
            if prior_answers == 0 and self.profile.private_facts:
                digest = _sha256(self.profile.private_facts[0])
                return (
                    SyntheticIntent.INTERVIEW_RESPONSE,
                    f"PRIVATE: must preserve sha256:{digest}",
                )
            if prior_answers == 1:
                return (
                    SyntheticIntent.INTERVIEW_RESPONSE,
                    "The team must keep human review on the agenda.",
                )
            return (
                SyntheticIntent.INTERVIEW_RESPONSE,
                "The team can support a reviewed launch.",
            )
        if "prior registered route" in prompt and "reply ack" in prompt:
            return SyntheticIntent.ACKNOWLEDGE, "Acknowledged."
        if SyntheticIntent.SILENCE in self.profile.allowed_intents:
            return SyntheticIntent.SILENCE, "synthetic_silence"
        return SyntheticIntent.ACKNOWLEDGE, "Acknowledged."


class _ReviewApprovalPolicy(_DeterministicPersonaPolicy):
    def _choose(self, context: _PersonaContext) -> tuple[SyntheticIntent, str]:
        if "humanwire draft proposal" in context.delivered_message.casefold():
            return SyntheticIntent.ACCEPT_PROPOSAL, "Accepted."
        self.complete = True
        if SyntheticIntent.APPROVE in self.profile.allowed_intents:
            return SyntheticIntent.APPROVE, "Approved."
        return SyntheticIntent.CHANGE, "Use the reviewed launch plan."


class _AvailabilityPolicy(_DeterministicPersonaPolicy):
    def _choose(self, context: _PersonaContext) -> tuple[SyntheticIntent, str]:
        if "humanwire draft proposal" in context.delivered_message.casefold():
            return SyntheticIntent.ACCEPT_PROPOSAL, "Accepted."
        self.complete = True
        return (
            SyntheticIntent.AVAILABILITY,
            "2026-08-13T15:00:00+00:00/2026-08-13T16:00:00+00:00",
        )


def _contract_for(persona: SyntheticPersona) -> EngagementType:
    intents = set(persona.allowed_intents)
    if intents == {SyntheticIntent.SILENCE}:
        return EngagementType.INFORM
    if SyntheticIntent.INTERVIEW_RESPONSE in intents:
        return EngagementType.STRUCTURED_INTERVIEW
    if SyntheticIntent.ANSWER in intents:
        return EngagementType.QUICK_RESPONSE
    if intents & {SyntheticIntent.APPROVE, SyntheticIntent.CHANGE}:
        return EngagementType.REVIEW_APPROVAL
    if SyntheticIntent.AVAILABILITY in intents:
        return EngagementType.AVAILABILITY
    return EngagementType.ACKNOWLEDGE


def _build_policy(persona: SyntheticPersona) -> _DeterministicPersonaPolicy:
    contract = _contract_for(persona)
    strategies: dict[EngagementType, type[_DeterministicPersonaPolicy]] = {
        EngagementType.INFORM: _InformPolicy,
        EngagementType.ACKNOWLEDGE: _AcknowledgePolicy,
        EngagementType.QUICK_RESPONSE: _QuickResponsePolicy,
        EngagementType.STRUCTURED_INTERVIEW: _StructuredInterviewPolicy,
        EngagementType.REVIEW_APPROVAL: _ReviewApprovalPolicy,
        EngagementType.AVAILABILITY: _AvailabilityPolicy,
    }
    profile = _PolicyProfile(
        role=persona.role,
        private_facts=tuple(persona.private_facts),
        allowed_intents=tuple(persona.allowed_intents),
        engagement_contract=contract,
    )
    return strategies[contract](profile)


class _SyntheticPlanner:
    def __init__(self, people: dict[str, Person], scenario: SyntheticScenario) -> None:
        self.people = people
        self.scenario = scenario

    def plan(self, text: str, initiator: Person) -> ResolvedPlan:
        del initiator
        change_story = "required approval change" in text.casefold()
        stakeholders: list[PlannedStakeholder] = []
        people: list[Person] = []
        for persona in self.scenario.personas:
            if persona.persona_id == "synthetic-manager":
                continue
            if change_story != (persona.persona_id == "approval-change"):
                continue
            contract = _contract_for(persona)
            questions: list[str] = []
            if contract is EngagementType.QUICK_RESPONSE:
                questions = ["Which launch date is recorded?"]
            elif contract is EngagementType.STRUCTURED_INTERVIEW:
                questions = [
                    "What private context should remain internal?",
                    "Which launch date is recorded?",
                    "What can the team support?",
                ]
            reason = (
                "Approve the synthetic launch"
                if contract is EngagementType.REVIEW_APPROVAL
                else f"Provide the {contract.value} contribution"
            )
            stakeholders.append(
                PlannedStakeholder(
                    person_ref=persona.persona_id,
                    reason=reason,
                    direction=Direction.DOWNWARD,
                    required=contract is not EngagementType.INFORM,
                    engagement_type=contract,
                    response_required=contract is not EngagementType.INFORM,
                    questions=questions,
                )
            )
            people.append(self.people[persona.persona_id])
        return ResolvedPlan(
            plan=MandatePlan(
                objective=(
                    "Record a required approval change without escalation"
                    if change_story
                    else "Coordinate the deterministic synthetic launch"
                ),
                required_decisions=["Approve the synthetic launch"],
                stakeholders=stakeholders,
                completion_conditions=["Every required synthetic contribution is recorded"],
            ),
            people=people,
            planner="deterministic_synthetic",
        )


class _SyntheticEvidenceExtractor(RuleBasedEvidenceExtractor):
    """Bind one public synthetic constraint to the approved launch decision."""

    def extract(self, *args, **kwargs) -> list[EvidenceDraft]:
        drafts = super().extract(*args, **kwargs)
        return [
            draft.model_copy(
                update={"related_decision": "Approve the synthetic launch"}
            )
            if draft.statement == "The team must keep human review on the agenda."
            else draft
            for draft in drafts
        ]


class _SyntheticNegotiationCoordinator(NegotiationCoordinator):
    """Normalize issue order before deterministic proposal drafting."""

    def prepare_proposal(self, mandate, report, round_number, now):
        normalized = report.model_copy(
            update={
                "issues": sorted(
                    report.issues,
                    key=lambda issue: (
                        issue.issue_type.value,
                        issue.summary,
                        tuple(issue.stakeholder_ids),
                    ),
                )
            }
        )
        return super().prepare_proposal(mandate, normalized, round_number, now)


@dataclass(frozen=True)
class _QueuedAction:
    action: SyntheticAction
    raw_delivery: str
    mandate_token: str


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_scenario(scenario: SyntheticScenario) -> SyntheticScenario:
    personas = [
        persona.model_copy(
            update={
                "private_facts": [],
                "private_fact_digests": [
                    _sha256(fact) for fact in persona.private_facts
                ],
            }
        )
        for persona in scenario.personas
    ]
    return scenario.model_copy(update={"personas": personas})


def _persona_visible_message(text: str, private_facts: list[str]) -> str:
    visible = _TOKEN_PATTERN.sub("[MANDATE_TOKEN]", text)
    visible = _EMAIL_PATTERN.sub("[SYNTHETIC_IDENTITY]", visible)
    for fact in private_facts:
        visible = visible.replace(fact, f"[PRIVATE_FACT:{_sha256(fact)}]")
    return visible[:MAX_CONTENT_LENGTH]


def _synthetic_directory(
    scenario: SyntheticScenario,
) -> tuple[OrganizationDirectory, dict[str, Person]]:
    manager = Person(
        person_id="synthetic-manager",
        display_name="Synthetic Manager",
        role="Simulation manager",
        department="Synthetic",
        timezone="UTC",
        routes=[
            ContactRoute(
                route_id="synthetic-manager-email",
                channel=Channel.EMAIL,
                sender_address="synthetic-manager@example.test",
                recipient="synthetic-manager@example.test",
                preferred=True,
            )
        ],
    )
    people = {manager.person_id: manager}
    for persona in scenario.personas:
        if persona.persona_id == manager.person_id:
            continue
        routes = []
        for index, channel in enumerate(persona.channels):
            routes.append(
                ContactRoute(
                    route_id=f"{persona.persona_id}-{channel.value}",
                    channel=channel,
                    sender_address=persona.email,
                    recipient=persona.email if channel is Channel.EMAIL else None,
                    conversation_id=(
                        f"synthetic-{persona.persona_id}-conversation"
                        if channel is Channel.TELEGRAM
                        else None
                    ),
                    preferred=index == 0,
                )
            )
        people[persona.persona_id] = Person(
            person_id=persona.persona_id,
            display_name=persona.display_name,
            role=persona.role,
            department="Synthetic",
            timezone="UTC",
            manager_id=manager.person_id,
            routes=routes,
        )
    directory = OrganizationDirectory(
        OrganizationDocument(
            people=list(people.values()),
            initiator_policies=[
                InitiatorPolicy(
                    person_id=manager.person_id,
                    allowed_directions={Direction.DOWNWARD},
                    allowed_departments={"Synthetic"},
                )
            ],
        )
    )
    return directory, people


def _isolated_settings(database_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        caspian_api_key=SecretStr("synthetic-offline-caspian"),
        caspian_base_url="https://offline.invalid",
        telegram_bot_token=SecretStr("synthetic-offline-telegram"),
        caspian_email_username="synthetic-humanwire",
        featherless_api_key=None,
        analytics_read_token=None,
        featherless_base_url="https://offline.invalid/v1",
        featherless_model="synthetic-disabled",
        database_url=f"sqlite:///{database_path.as_posix()}",
        organization_path=database_path.parent / "directory-not-loaded.json",
        acknowledgement_seconds=10,
        reminder_seconds=10,
        mandate_timeout_seconds=86_400,
        engagement_preview_seconds=0,
        engagement_require_go=False,
        due_action_poll_seconds=5,
        dashboard_host="127.0.0.1",
        dashboard_port=8000,
        public_demo=False,
    )


def _wire_command(
    intent: SyntheticIntent,
    content: str,
    raw_delivery: str,
    mandate_token: str,
) -> str | None:
    if intent in {SyntheticIntent.SILENCE, SyntheticIntent.ERROR}:
        return None
    token = mandate_token
    if intent is SyntheticIntent.ACKNOWLEDGE:
        return f"ACK {token}"
    if intent in {SyntheticIntent.ANSWER, SyntheticIntent.INTERVIEW_RESPONSE}:
        return content
    if intent is SyntheticIntent.CONFIRM_EVIDENCE:
        return f"CONFIRM {token}"
    if intent is SyntheticIntent.APPROVE:
        if "DRAFT PROPOSAL" in raw_delivery:
            return f"ACCEPT {token}"
        return f"DECIDE {token} APPROVE"
    if intent is SyntheticIntent.CHANGE:
        if "DRAFT PROPOSAL" in raw_delivery:
            return f"CHANGE {token} {content}"
        return f"DECIDE {token} CHANGE {content}"
    if intent is SyntheticIntent.AVAILABILITY:
        return f"AVAILABLE {token} {content}"
    if intent is SyntheticIntent.ACCEPT_PROPOSAL:
        return f"ACCEPT {token}"
    if intent is SyntheticIntent.CHANGE_PROPOSAL:
        return f"CHANGE {token} {content}"
    raise ValueError("unsupported synthetic intent")


def _delivery_persona(
    delivery: CapturedDelivery,
    people: dict[str, Person],
    inbound_owner: dict[str, tuple[str, Channel]],
) -> tuple[str, Channel] | None:
    if delivery.kind == "reply":
        owner = inbound_owner.get(delivery.destination)
        if owner is None:
            return None
        return owner
    matches: list[tuple[str, Channel]] = []
    for persona_id, person in people.items():
        for route in person.routes:
            if delivery.kind == "initiate" and route.recipient == delivery.destination:
                matches.append((persona_id, Channel.EMAIL))
            if (
                delivery.kind == "send"
                and route.conversation_id == delivery.destination
            ):
                matches.append((persona_id, Channel.TELEGRAM))
    unique_matches = list(dict.fromkeys(matches))
    if len(unique_matches) > 1:
        raise ValueError("ambiguous synthetic identity mapping")
    return unique_matches[0] if unique_matches else None


def _persona_context(
    visible_delivery: str,
    inbox: list[str],
    history: list[_PersonaTranscriptEntry],
    now: datetime,
) -> _PersonaContext:
    return _PersonaContext(
        delivered_message=visible_delivery,
        own_inbox=(*inbox, visible_delivery),
        own_transcript=tuple(history),
        virtual_time=now,
    )


def _validated_output_path(output_path: str | Path, run_root: str | Path) -> tuple[Path, Path]:
    root = Path(run_root).absolute()
    output = Path(output_path).absolute()
    try:
        output.relative_to(root)
    except ValueError as error:
        raise ValueError("synthetic output path must be inside run root") from error
    if output == root:
        raise ValueError("synthetic output path must be inside run root")
    return output, root


def _prepare_fresh_run_root(run_root: str | Path) -> Path:
    root = Path(run_root).resolve()
    try:
        root.mkdir(exist_ok=False)
    except FileExistsError as error:
        raise FileExistsError("synthetic proof requires a fresh run root") from error
    return root


def _reserve_database_path(root: Path) -> Path:
    database_path = root / "humanwire-synthetic.sqlite3"
    with database_path.open("x+b"):
        pass
    return database_path


def _path_within_run_root(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError("synthetic output path must be inside run root") from error
    return resolved


def _write_transcript_exclusively(output: Path, root: Path, content: str) -> None:
    relative_output = output.relative_to(root)
    parent = root
    for part in relative_output.parts[:-1]:
        candidate = parent / part
        try:
            candidate.mkdir(exist_ok=False)
        except FileExistsError:
            pass
        parent = _path_within_run_root(candidate, root)
        if not parent.is_dir():
            raise FileExistsError("synthetic output parent must be an owned directory")

    final_output = _path_within_run_root(parent / relative_output.name, root)
    with final_output.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def generate_scenario(
    scenario: SyntheticScenario,
    output_path: str | Path,
    run_root: str | Path,
) -> SyntheticRunResult:
    """Generate one isolated deterministic run through the real gateway boundary."""
    scenario = SyntheticScenario.model_validate(scenario)
    output, root = _validated_output_path(output_path, run_root)
    root = _prepare_fresh_run_root(root)
    database_path = _reserve_database_path(root)

    settings = _isolated_settings(database_path)
    directory, people = _synthetic_directory(scenario)
    session_factory = create_session_factory(settings.database_url)
    repository = SqlAlchemyHumanWireRepository(session_factory)
    negotiation = _SyntheticNegotiationCoordinator(repository)
    workflow = HumanWireWorkflow(
        directory,
        repository,
        _SyntheticPlanner(people, scenario),
        _SyntheticEvidenceExtractor(),
        settings,
        negotiation_coordinator=negotiation,
    )
    client = OfflineCaspianClient()
    clock = [_FIXED_TIME]
    gateway = CaspianGateway(
        settings,
        workflow,
        repository,
        client=client,
        clock=lambda: clock[0],
    )
    gateway.connect()

    manager_message = email_envelope(
        message_id="synthetic-manager-mandate",
        conversation_id="synthetic-manager-conversation",
        sender_address="synthetic-manager@example.test",
        sender_name="Synthetic Manager",
        text="/mandate\nCoordinate the deterministic synthetic launch",
    )
    client.emit_inbound(manager_message)
    released = workflow.process_due(clock[0])
    gateway.dispatch_all(released)

    persona_by_id = {persona.persona_id: persona for persona in scenario.personas}
    policies = {persona.persona_id: _build_policy(persona) for persona in scenario.personas}
    histories: dict[str, list[_PersonaTranscriptEntry]] = {
        persona.persona_id: [] for persona in scenario.personas
    }
    inboxes: dict[str, list[str]] = {persona.persona_id: [] for persona in scenario.personas}
    local_sequences = {persona.persona_id: 0 for persona in scenario.personas}
    inbound_owner: dict[str, tuple[str, Channel]] = {}
    mandate_tokens: dict[str, str] = {}
    inbound_envelopes: list[SyntheticInboundEnvelope] = []
    outbound_digests: dict[str, str] = {}
    actions: list[SyntheticAction] = []
    queued: list[tuple[datetime, str, int, _QueuedAction]] = []
    delivery_cursor = 0
    trigger_sequence = 0

    def collect_deliveries(now: datetime) -> None:
        nonlocal delivery_cursor, trigger_sequence
        while delivery_cursor < len(client.deliveries):
            delivery = client.deliveries[delivery_cursor]
            delivery_cursor += 1
            resolved = _delivery_persona(delivery, people, inbound_owner)
            if resolved is None:
                continue
            persona_id, channel = resolved
            if (
                persona_id == "synthetic-manager"
                and "HUMANWIRE AVAILABILITY REQUEST" not in delivery.text
            ):
                continue
            policy = policies[persona_id]
            is_proposal = "HUMANWIRE DRAFT PROPOSAL" in delivery.text
            is_availability = "HUMANWIRE AVAILABILITY REQUEST" in delivery.text
            can_answer_proposal = bool(
                {
                    SyntheticIntent.ACCEPT_PROPOSAL,
                    SyntheticIntent.CHANGE_PROPOSAL,
                }.intersection(persona_by_id[persona_id].allowed_intents)
            )
            can_answer_availability = (
                SyntheticIntent.AVAILABILITY
                in persona_by_id[persona_id].allowed_intents
            )
            if getattr(policy, "complete", False) and (
                not (
                    (is_proposal and can_answer_proposal)
                    or (is_availability and can_answer_availability)
                )
            ):
                continue
            persona = persona_by_id[persona_id]
            visible = _persona_visible_message(delivery.text, persona.private_facts)
            token_match = _TOKEN_PATTERN.search(delivery.text)
            if token_match is not None:
                mandate_tokens[persona_id] = token_match.group(0)
            mandate_token = mandate_tokens.get(persona_id)
            if mandate_token is None:
                raise ValueError("persona delivery arrived before its mandate token")
            trigger_sequence += 1
            trigger_id = f"outbound-{trigger_sequence}"
            trigger_digest = _sha256(visible)
            local_sequences[persona_id] += 1
            local_sequence = local_sequences[persona_id]
            context = _persona_context(
                visible,
                inboxes[persona_id],
                histories[persona_id],
                now + timedelta(seconds=1),
            )
            try:
                raw_output = policy.respond(context)
                decision = _PersonaDecision.model_validate(raw_output)
                if decision.intent not in persona.allowed_intents:
                    raise ValueError("persona output violated its response contract")
                action_time = now + timedelta(seconds=decision.time_offset_seconds)
                action = SyntheticAction(
                    schema_version=SUPPORTED_SCHEMA_VERSION,
                    action_id=f"{persona_id}-{local_sequence}",
                    persona_id=persona_id,
                    channel=channel,
                    timestamp=action_time,
                    local_sequence=local_sequence,
                    trigger_id=trigger_id,
                    trigger_digest=trigger_digest,
                    intent=decision.intent,
                    content=decision.content,
                )
            except TimeoutError:
                policy.complete = True
                action_time = now + timedelta(seconds=1)
                action = SyntheticAction(
                    schema_version=SUPPORTED_SCHEMA_VERSION,
                    action_id=f"{persona_id}-{local_sequence}",
                    persona_id=persona_id,
                    channel=channel,
                    timestamp=action_time,
                    local_sequence=local_sequence,
                    trigger_id=trigger_id,
                    trigger_digest=trigger_digest,
                    intent=SyntheticIntent.SILENCE,
                    content="synthetic_timeout",
                )
            except (ValidationError, ValueError, TypeError):
                policy.complete = True
                action_time = now + timedelta(seconds=1)
                action = SyntheticAction(
                    schema_version=SUPPORTED_SCHEMA_VERSION,
                    action_id=f"{persona_id}-{local_sequence}",
                    persona_id=persona_id,
                    channel=channel,
                    timestamp=action_time,
                    local_sequence=local_sequence,
                    trigger_id=trigger_id,
                    trigger_digest=trigger_digest,
                    intent=SyntheticIntent.ERROR,
                    content="synthetic_invalid_output",
                )
            outbound_digests[trigger_id] = trigger_digest
            heapq.heappush(
                queued,
                (
                    action.timestamp,
                    action.persona_id,
                    action.local_sequence,
                    _QueuedAction(action, delivery.text, mandate_token),
                ),
            )

    collect_deliveries(clock[0])
    change_story_enabled = "approval-change" in persona_by_id
    change_story_started = False
    due_advances = 0
    while True:
        if not queued:
            mandates = sorted(
                repository.list_recent_mandates(1000),
                key=lambda item: item.created_at,
            )
            if (
                change_story_enabled
                and not change_story_started
                and mandates
                and mandates[0].state.value == "meeting_ready"
            ):
                change_story_started = True
                client.emit_inbound(
                    email_envelope(
                        message_id="synthetic-manager-change-mandate",
                        conversation_id="synthetic-manager-change-conversation",
                        sender_address="synthetic-manager@example.test",
                        sender_name="Synthetic Manager",
                        text=(
                            "/mandate\nRecord the required approval change safely"
                        ),
                    )
                )
                gateway.dispatch_all(workflow.process_due(clock[0]))
                collect_deliveries(clock[0])
                continue

            due_times = [
                assignment.next_action_at
                for mandate in mandates
                if mandate.state.value == "interviewing"
                for assignment in repository.list_assignments(mandate.mandate_id)
                if assignment.next_action_at is not None
                and assignment.next_action_at > clock[0]
            ]
            if due_times:
                due_advances += 1
                if due_advances > 32:
                    raise ValueError("synthetic due-work progression exceeded its bound")
                clock[0] = min(due_times)
                gateway.dispatch_all(workflow.process_due(clock[0]))
                collect_deliveries(clock[0])
                continue
            break

        _, persona_id, _, queued_action = heapq.heappop(queued)
        action = queued_action.action
        clock[0] = action.timestamp
        actions.append(action)
        visible = _persona_visible_message(
            queued_action.raw_delivery,
            persona_by_id[persona_id].private_facts,
        )
        inboxes[persona_id].append(visible)
        histories[persona_id].append(
            _PersonaTranscriptEntry(
                timestamp=action.timestamp,
                local_sequence=action.local_sequence,
                intent=action.intent,
                content=action.content,
            )
        )
        command = _wire_command(
            action.intent,
            action.content,
            queued_action.raw_delivery,
            queued_action.mandate_token,
        )
        if command is not None:
            message_id = f"synthetic-{persona_id}-{action.local_sequence}"
            channel = action.channel
            envelope_args = {
                "message_id": message_id,
                "conversation_id": f"synthetic-{persona_id}-conversation",
                "sender_address": persona_by_id[persona_id].email,
                "sender_name": persona_by_id[persona_id].display_name,
                "text": command,
            }
            envelope = (
                email_envelope(**envelope_args)
                if channel is Channel.EMAIL
                else telegram_envelope(**envelope_args)
            )
            inbound_owner[message_id] = (persona_id, channel)
            inbound_envelopes.append(
                SyntheticInboundEnvelope(
                    persona_id=persona_id,
                    channel=channel,
                    message_id=message_id,
                    conversation_id=envelope_args["conversation_id"],
                    connection_id=f"offline-{channel.value}-connection",
                    sender_address=persona_by_id[persona_id].email,
                )
            )
            client.emit_inbound(envelope)
            collect_deliveries(clock[0])

    transcript = SyntheticTranscript.create(
        scenario=_safe_scenario(scenario),
        outbound_digests=outbound_digests,
        actions=actions,
    )
    _write_transcript_exclusively(
        output,
        root,
        transcript.model_dump_json(indent=2) + "\n",
    )
    all_mandates = sorted(
        repository.list_recent_mandates(1000), key=lambda item: item.created_at
    )
    latest = repository.list_recent_mandates(1)
    final_state = latest[0].state.value if latest else "missing"
    terminal_states = tuple(item.state.value for item in all_mandates)
    session_factory.kw["bind"].dispose()
    return SyntheticRunResult(
        transcript=transcript,
        database_path=database_path,
        gateway_handler_count=client.on_message_registration_count,
        inbound_envelopes=tuple(inbound_envelopes),
        captured_deliveries=tuple(client.deliveries),
        model_client_configured=False,
        final_state=final_state,
        terminal_states=terminal_states,
    )


def replay_transcript(
    path: str | Path,
    run_root: str | Path,
) -> SyntheticRunResult:
    """Reinject a validated frozen transcript through the offline gateway boundary."""
    transcript = load_transcript(path)
    scenario = transcript.scenario
    root = _prepare_fresh_run_root(run_root)
    database_path = _reserve_database_path(root)

    settings = _isolated_settings(database_path)
    directory, people = _synthetic_directory(scenario)
    session_factory = create_session_factory(settings.database_url)
    repository = SqlAlchemyHumanWireRepository(session_factory)
    negotiation = _SyntheticNegotiationCoordinator(repository)
    workflow = HumanWireWorkflow(
        directory,
        repository,
        _SyntheticPlanner(people, scenario),
        _SyntheticEvidenceExtractor(),
        settings,
        negotiation_coordinator=negotiation,
    )
    client = OfflineCaspianClient()
    clock = [_FIXED_TIME]
    gateway = CaspianGateway(
        settings,
        workflow,
        repository,
        client=client,
        clock=lambda: clock[0],
    )
    gateway.connect()

    client.emit_inbound(
        email_envelope(
            message_id="synthetic-manager-mandate",
            conversation_id="synthetic-manager-conversation",
            sender_address="synthetic-manager@example.test",
            sender_name="Synthetic Manager",
            text="/mandate\nCoordinate the deterministic synthetic launch",
        )
    )
    gateway.dispatch_all(workflow.process_due(clock[0]))

    persona_by_id = {persona.persona_id: persona for persona in scenario.personas}
    actions_by_trigger = {action.trigger_id: action for action in transcript.actions}
    pending_by_persona: dict[str, int] = {
        persona.persona_id: sum(
            action.persona_id == persona.persona_id for action in transcript.actions
        )
        for persona in scenario.personas
    }
    inbound_owner: dict[str, tuple[str, Channel]] = {}
    mandate_tokens: dict[str, str] = {}
    inbound_envelopes: list[SyntheticInboundEnvelope] = []
    queued: list[tuple[datetime, str, int, _QueuedAction]] = []
    delivery_cursor = 0
    trigger_sequence = 0

    def collect_frozen_deliveries() -> None:
        nonlocal delivery_cursor, trigger_sequence
        while delivery_cursor < len(client.deliveries):
            delivery = client.deliveries[delivery_cursor]
            delivery_cursor += 1
            resolved = _delivery_persona(delivery, people, inbound_owner)
            if resolved is None:
                continue
            persona_id, channel = resolved
            if (
                persona_id == "synthetic-manager"
                and "HUMANWIRE AVAILABILITY REQUEST" not in delivery.text
            ):
                continue
            if pending_by_persona[persona_id] == 0:
                continue
            candidate_id = f"outbound-{trigger_sequence + 1}"
            action = actions_by_trigger.get(candidate_id)
            if action is None:
                raise ValueError("replay produced an unrecorded outbound trigger")
            visible = _persona_visible_message(delivery.text, [])
            if _sha256(visible) != transcript.outbound_digests[candidate_id]:
                continue
            trigger_sequence += 1
            trigger_id = candidate_id
            actions_by_trigger.pop(trigger_id)
            if action.persona_id != persona_id or action.channel is not channel:
                raise ValueError("replay outbound identity mapping mismatch")
            token_match = _TOKEN_PATTERN.search(delivery.text)
            if token_match is not None:
                mandate_tokens[persona_id] = token_match.group(0)
            mandate_token = mandate_tokens.get(persona_id)
            if mandate_token is None:
                raise ValueError("replay delivery arrived before its mandate token")
            pending_by_persona[persona_id] -= 1
            heapq.heappush(
                queued,
                (
                    action.timestamp,
                    action.persona_id,
                    action.local_sequence,
                    _QueuedAction(action, delivery.text, mandate_token),
                ),
            )

    collect_frozen_deliveries()
    change_story_enabled = "approval-change" in persona_by_id
    change_story_started = False
    due_advances = 0
    while True:
        if not queued:
            mandates = sorted(
                repository.list_recent_mandates(1000),
                key=lambda item: item.created_at,
            )
            if (
                change_story_enabled
                and not change_story_started
                and mandates
                and mandates[0].state.value == "meeting_ready"
            ):
                change_story_started = True
                client.emit_inbound(
                    email_envelope(
                        message_id="synthetic-manager-change-mandate",
                        conversation_id="synthetic-manager-change-conversation",
                        sender_address="synthetic-manager@example.test",
                        sender_name="Synthetic Manager",
                        text=(
                            "/mandate\nRecord the required approval change safely"
                        ),
                    )
                )
                gateway.dispatch_all(workflow.process_due(clock[0]))
                collect_frozen_deliveries()
                continue

            due_times = [
                assignment.next_action_at
                for mandate in mandates
                if mandate.state.value == "interviewing"
                for assignment in repository.list_assignments(mandate.mandate_id)
                if assignment.next_action_at is not None
                and assignment.next_action_at > clock[0]
            ]
            if due_times:
                due_advances += 1
                if due_advances > 32:
                    raise ValueError("synthetic due-work progression exceeded its bound")
                clock[0] = min(due_times)
                gateway.dispatch_all(workflow.process_due(clock[0]))
                collect_frozen_deliveries()
                continue
            break

        _, persona_id, _, queued_action = heapq.heappop(queued)
        action = queued_action.action
        clock[0] = action.timestamp
        command = _wire_command(
            action.intent,
            action.content,
            queued_action.raw_delivery,
            queued_action.mandate_token,
        )
        if command is not None:
            message_id = f"synthetic-{persona_id}-{action.local_sequence}"
            envelope_args = {
                "message_id": message_id,
                "conversation_id": f"synthetic-{persona_id}-conversation",
                "sender_address": persona_by_id[persona_id].email,
                "sender_name": persona_by_id[persona_id].display_name,
                "text": command,
            }
            envelope = (
                email_envelope(**envelope_args)
                if action.channel is Channel.EMAIL
                else telegram_envelope(**envelope_args)
            )
            inbound_owner[message_id] = (persona_id, action.channel)
            inbound_envelopes.append(
                SyntheticInboundEnvelope(
                    persona_id=persona_id,
                    channel=action.channel,
                    message_id=message_id,
                    conversation_id=envelope_args["conversation_id"],
                    connection_id=f"offline-{action.channel.value}-connection",
                    sender_address=persona_by_id[persona_id].email,
                )
            )
            client.emit_inbound(envelope)
            collect_frozen_deliveries()

    if actions_by_trigger:
        raise ValueError("replay did not reproduce every frozen outbound trigger")
    all_mandates = sorted(
        repository.list_recent_mandates(1000), key=lambda item: item.created_at
    )
    latest = repository.list_recent_mandates(1)
    final_state = latest[0].state.value if latest else "missing"
    terminal_states = tuple(item.state.value for item in all_mandates)
    session_factory.kw["bind"].dispose()
    return SyntheticRunResult(
        transcript=transcript,
        database_path=database_path,
        gateway_handler_count=client.on_message_registration_count,
        inbound_envelopes=tuple(inbound_envelopes),
        captured_deliveries=tuple(client.deliveries),
        model_client_configured=False,
        final_state=final_state,
        terminal_states=terminal_states,
    )


def _semantic_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _unique_aliases(values: dict[object, str], entity: str) -> None:
    aliases = list(values.values())
    if len(aliases) != len(set(aliases)):
        raise ValueError(f"ambiguous semantic {entity} identity")


def _semantic_trace(result: SyntheticRunResult) -> dict[str, object]:
    transcript = validate_transcript(result.transcript)
    if not result.database_path.is_file():
        raise ValueError("synthetic semantic trace database is missing")

    scenario = transcript.scenario
    route_alias_by_id: dict[str, str] = {}
    for persona in scenario.personas:
        for index, channel in enumerate(persona.channels):
            route_id = f"{persona.persona_id}-{channel.value}"
            alias = f"{persona.persona_id}:{channel.value}:{index}"
            if route_id in route_alias_by_id:
                raise ValueError("ambiguous semantic route identity")
            route_alias_by_id[route_id] = alias

    expected_message_actions: dict[str, list[SyntheticAction]] = {}
    for action in transcript.actions:
        if _wire_command(action.intent, action.content, "", "HW-00000000") is None:
            continue
        message_id = f"synthetic-{action.persona_id}-{action.local_sequence}"
        expected_message_actions.setdefault(message_id, []).append(action)

    def action_alias_for_message(message_id: str) -> str:
        candidates = expected_message_actions.get(message_id, [])
        if len(candidates) != 1:
            raise ValueError("ambiguous semantic source message identity")
        return candidates[0].action_id

    inbound = []
    for envelope in result.inbound_envelopes:
        action_alias = action_alias_for_message(envelope.message_id)
        action = next(
            item for item in transcript.actions if item.action_id == action_alias
        )
        if envelope.persona_id != action.persona_id or envelope.channel is not action.channel:
            raise ValueError("ambiguous semantic inbound identity")
        inbound.append(
            {
                "action": action_alias,
                "persona": envelope.persona_id,
                "channel": envelope.channel.value,
                "route": f"{envelope.persona_id}:{envelope.channel.value}",
                "sender_digest": _sha256(envelope.sender_address),
                "conversation_digest": _sha256(envelope.conversation_id),
                "connection_digest": _sha256(envelope.connection_id),
            }
        )

    email_owners: dict[str, list[str]] = {}
    telegram_owners: dict[str, list[str]] = {}
    for persona in scenario.personas:
        if Channel.EMAIL in persona.channels:
            email_owners.setdefault(persona.email, []).append(persona.persona_id)
        if Channel.TELEGRAM in persona.channels:
            telegram_owners.setdefault(
                f"synthetic-{persona.persona_id}-conversation", []
            ).append(persona.persona_id)

    deliveries = []
    for delivery in result.captured_deliveries:
        if delivery.kind == "reply":
            manager_destinations = {
                "synthetic-manager-mandate": "manager/primary",
                "synthetic-manager-change-mandate": "manager/change",
            }
            destination = manager_destinations.get(delivery.destination)
            if destination is None:
                destination = action_alias_for_message(delivery.destination)
        elif delivery.kind == "initiate":
            owners = email_owners.get(delivery.destination, [])
            if len(owners) != 1:
                raise ValueError("ambiguous semantic delivery identity")
            destination = f"{owners[0]}:email"
        else:
            owners = telegram_owners.get(delivery.destination, [])
            if len(owners) != 1:
                raise ValueError("ambiguous semantic delivery identity")
            destination = f"{owners[0]}:telegram"
        deliveries.append(
            {
                "kind": delivery.kind,
                "destination": destination,
                "text_digest": _sha256(delivery.text),
                "connection_digest": (
                    _sha256(delivery.connection_id)
                    if delivery.connection_id is not None
                    else None
                ),
            }
        )

    settings = _isolated_settings(result.database_path)
    session_factory = create_session_factory(settings.database_url)
    repository = SqlAlchemyHumanWireRepository(session_factory)
    try:
        mandates = repository.list_recent_mandates(1000)
        mandate_alias = {mandate.mandate_id: mandate.token for mandate in mandates}
        _unique_aliases(mandate_alias, "mandate")

        assignments = [
            assignment
            for mandate in mandates
            for assignment in repository.list_assignments(mandate.mandate_id)
        ]
        assignment_alias = {
            assignment.assignment_id: (
                f"{mandate_alias[assignment.mandate_id]}/{assignment.person_id}"
            )
            for assignment in assignments
        }
        _unique_aliases(assignment_alias, "assignment")

        normalized_assignments = []
        for assignment in assignments:
            routes = []
            for route_id in assignment.route_ids:
                route = route_alias_by_id.get(route_id)
                if route is None:
                    raise ValueError("ambiguous semantic assignment route")
                routes.append(route)
            normalized_assignments.append(
                {
                    "assignment": assignment_alias[assignment.assignment_id],
                    "mandate": mandate_alias[assignment.mandate_id],
                    "person": assignment.person_id,
                    "department": assignment.department,
                    "direction": assignment.direction.value,
                    "reason_digest": _sha256(assignment.reason),
                    "required": assignment.required,
                    "engagement_type": assignment.engagement_type.value,
                    "response_required": assignment.response_required,
                    "state": assignment.state.value,
                    "routes": routes,
                    "active_route": routes[assignment.active_route_index],
                    "attempt_count": assignment.attempt_count,
                    "has_interview": assignment.interview_id is not None,
                    "first_contact_at": _semantic_time(assignment.first_contact_at),
                    "last_delivery_at": _semantic_time(assignment.last_delivery_at),
                    "next_action_at": _semantic_time(assignment.next_action_at),
                    "acknowledged_at": _semantic_time(assignment.acknowledged_at),
                    "completed_at": _semantic_time(assignment.completed_at),
                    "failure_reason": assignment.failure_reason,
                }
            )
        normalized_assignments.sort(key=lambda item: str(item["assignment"]))

        interviews = [
            interview
            for mandate in mandates
            for interview in repository.list_interviews(mandate.mandate_id)
        ]
        interview_alias = {
            interview.session_id: assignment_alias[interview.assignment_id]
            for interview in interviews
        }
        _unique_aliases(interview_alias, "interview")
        normalized_interviews = []
        for interview in interviews:
            current_route = None
            if interview.current_route_id is not None:
                current_route = route_alias_by_id.get(interview.current_route_id)
                if current_route is None:
                    raise ValueError("ambiguous semantic interview route")
            normalized_interviews.append(
                {
                    "interview": interview_alias[interview.session_id],
                    "assignment": assignment_alias[interview.assignment_id],
                    "question_digests": [_sha256(item) for item in interview.questions],
                    "current_question_index": interview.current_question_index,
                    "current_channel": (
                        interview.current_channel.value
                        if interview.current_channel is not None
                        else None
                    ),
                    "current_route": current_route,
                    "channel_history": [item.value for item in interview.channel_history],
                    "default_visibility": interview.default_visibility.value,
                    "acknowledged_at": _semantic_time(interview.acknowledged_at),
                    "started_at": _semantic_time(interview.started_at),
                    "updated_at": _semantic_time(interview.updated_at),
                    "completed_at": _semantic_time(interview.completed_at),
                }
            )
        normalized_interviews.sort(key=lambda item: str(item["interview"]))

        evidence_items = [
            evidence
            for mandate in mandates
            for evidence in repository.list_evidence(mandate.mandate_id)
        ]
        evidence_sort_keys: dict[UUID, tuple[object, ...]] = {}
        for evidence in evidence_items:
            source = action_alias_for_message(evidence.source_message_id)
            evidence_sort_keys[evidence.evidence_id] = (
                assignment_alias[evidence.assignment_id],
                source,
                _semantic_time(evidence.created_at),
                evidence.evidence_type.value,
                _sha256(evidence.statement),
                evidence.visibility.value,
                evidence.status.value,
                evidence.channel.value,
            )
        if len(set(evidence_sort_keys.values())) != len(evidence_sort_keys):
            raise ValueError("ambiguous semantic evidence identity")
        evidence_alias: dict[UUID, str] = {}
        evidence_ordinals: dict[tuple[str, str], int] = {}
        normalized_evidence = []
        for evidence in sorted(evidence_items, key=lambda item: evidence_sort_keys[item.evidence_id]):
            source = action_alias_for_message(evidence.source_message_id)
            group = (assignment_alias[evidence.assignment_id], source)
            evidence_ordinals[group] = evidence_ordinals.get(group, 0) + 1
            alias = f"{group[0]}/{source}/{evidence_ordinals[group]}"
            evidence_alias[evidence.evidence_id] = alias
            normalized_evidence.append(
                {
                    "evidence": alias,
                    "assignment": group[0],
                    "source": source,
                    "ordinal": evidence_ordinals[group],
                    "stakeholder": evidence.stakeholder_id,
                    "type": evidence.evidence_type.value,
                    "statement_digest": _sha256(evidence.statement),
                    "visibility": evidence.visibility.value,
                    "status": evidence.status.value,
                    "channel": evidence.channel.value,
                    "created_at": _semantic_time(evidence.created_at),
                    "related_decision_digest": (
                        _sha256(evidence.related_decision)
                        if evidence.related_decision is not None
                        else None
                    ),
                    "deadline": _semantic_time(evidence.deadline),
                    "resource_digest": (
                        _sha256(evidence.resource)
                        if evidence.resource is not None
                        else None
                    ),
                }
            )

        issues = [
            issue
            for mandate in mandates
            for issue in repository.list_issues(mandate.mandate_id)
        ]
        issue_sort_keys = {
            issue.issue_id: (
                mandate_alias[issue.mandate_id],
                issue.issue_type.value,
                _sha256(issue.summary),
                issue.blocking,
                tuple(issue.stakeholder_ids),
                tuple(sorted(evidence_alias[item] for item in issue.evidence_ids)),
            )
            for issue in issues
        }
        if len(set(issue_sort_keys.values())) != len(issue_sort_keys):
            raise ValueError("ambiguous semantic issue identity")
        issue_alias: dict[UUID, str] = {}
        normalized_issues = []
        issue_number: dict[str, int] = {}
        for issue in sorted(issues, key=lambda item: issue_sort_keys[item.issue_id]):
            token = mandate_alias[issue.mandate_id]
            issue_number[token] = issue_number.get(token, 0) + 1
            alias = f"{token}/issue/{issue_number[token]}"
            issue_alias[issue.issue_id] = alias
            normalized_issues.append(
                {
                    "issue": alias,
                    "mandate": token,
                    "type": issue.issue_type.value,
                    "evidence": sorted(evidence_alias[item] for item in issue.evidence_ids),
                    "stakeholders": sorted(issue.stakeholder_ids),
                    "related_decision_digest": (
                        _sha256(issue.related_decision)
                        if issue.related_decision is not None
                        else None
                    ),
                    "summary_digest": _sha256(issue.summary),
                    "blocking": issue.blocking,
                    "resolution_digest": (
                        _sha256(issue.resolution) if issue.resolution is not None else None
                    ),
                }
            )

        events_by_mandate = {
            mandate.mandate_id: repository.list_events(mandate.mandate_id)
            for mandate in mandates
        }
        proposal_ids: set[UUID] = set()
        for events in events_by_mandate.values():
            for event in events:
                proposal_id = event.metadata.get("proposal_id")
                if proposal_id is not None:
                    proposal_ids.add(UUID(str(proposal_id)))
        proposals = []
        for proposal_id in proposal_ids:
            proposal = repository.get_proposal(proposal_id)
            if proposal is None:
                raise ValueError("semantic proposal reference is missing")
            proposals.append(proposal)
        proposal_alias = {
            proposal.proposal_id: (
                f"{mandate_alias[proposal.mandate_id]}/proposal/{proposal.round_number}"
            )
            for proposal in proposals
        }
        _unique_aliases(proposal_alias, "proposal")
        normalized_proposals = []
        normalized_proposal_responses = []
        for proposal in sorted(proposals, key=lambda item: proposal_alias[item.proposal_id]):
            alias = proposal_alias[proposal.proposal_id]
            normalized_proposals.append(
                {
                    "proposal": alias,
                    "mandate": mandate_alias[proposal.mandate_id],
                    "round": proposal.round_number,
                    "text_digest": _sha256(proposal.text),
                    "issues": sorted(issue_alias[item] for item in proposal.issue_ids),
                    "required_respondents": sorted(proposal.required_respondent_ids),
                    "state": proposal.state.value,
                    "created_at": _semantic_time(proposal.created_at),
                    "expires_at": _semantic_time(proposal.expires_at),
                }
            )
            for response in repository.list_proposal_responses(proposal.proposal_id):
                normalized_proposal_responses.append(
                    {
                        "proposal": alias,
                        "stakeholder": response.stakeholder_id,
                        "response": response.response.value,
                        "change_digest": (
                            _sha256(response.change_text)
                            if response.change_text is not None
                            else None
                        ),
                        "source": action_alias_for_message(response.source_message_id),
                        "created_at": _semantic_time(response.created_at),
                    }
                )
        normalized_proposal_responses.sort(
            key=lambda item: (
                str(item["proposal"]),
                str(item["stakeholder"]),
                str(item["source"]),
            )
        )

        decisions = [
            decision
            for mandate in mandates
            for decision in repository.list_engagement_decisions(mandate.mandate_id)
        ]
        normalized_decisions = [
            {
                "assignment": assignment_alias[decision.assignment_id],
                "stakeholder": decision.stakeholder_id,
                "response": decision.response.value,
                "change_digest": (
                    _sha256(decision.change_text)
                    if decision.change_text is not None
                    else None
                ),
                "source": action_alias_for_message(decision.source_message_id),
                "created_at": _semantic_time(decision.created_at),
            }
            for decision in decisions
        ]
        normalized_decisions.sort(key=lambda item: str(item["assignment"]))

        meeting_alias: dict[UUID, str] = {}
        normalized_meetings = []
        for mandate in mandates:
            meeting = repository.get_meeting_package(mandate.mandate_id)
            if meeting is None:
                continue
            alias = f"{mandate.token}/meeting"
            if alias in meeting_alias.values():
                raise ValueError("ambiguous semantic meeting identity")
            meeting_alias[meeting.meeting_id] = alias
            normalized_meetings.append(
                {
                    "meeting": alias,
                    "mandate": mandate.token,
                    "purpose_digest": _sha256(meeting.purpose),
                    "decision_owner": meeting.decision_owner_id,
                    "required_attendees": sorted(meeting.required_attendee_ids),
                    "optional_attendees": sorted(meeting.optional_attendee_ids),
                    "proposed_start": _semantic_time(meeting.proposed_start),
                    "proposed_end": _semantic_time(meeting.proposed_end),
                    "timezone": meeting.timezone,
                    "agreed_fact_digests": sorted(_sha256(item) for item in meeting.agreed_facts),
                    "open_decision_digests": sorted(
                        _sha256(item) for item in meeting.open_decisions
                    ),
                    "agenda_digests": [_sha256(item) for item in meeting.agenda],
                    "pre_read_evidence": sorted(
                        evidence_alias[item] for item in meeting.pre_read_evidence_ids
                    ),
                    "calendar_written": meeting.calendar_written,
                    "created_at": _semantic_time(meeting.created_at),
                }
            )
        normalized_meetings.sort(key=lambda item: str(item["meeting"]))

        outbox = [
            item
            for mandate in mandates
            for item in repository.list_release_outbox(mandate.mandate_id)
        ]
        outbox_alias: dict[str, str] = {}
        delivery_alias: dict[str, str] = {}
        normalized_outbox = []
        for item in outbox:
            assignment = next(
                value for value in assignments if value.assignment_id == item.assignment_id
            )
            routes = [route_alias_by_id[route_id] for route_id in assignment.route_ids]
            if item.route_index >= len(routes):
                raise ValueError("ambiguous semantic outbox route")
            alias = (
                f"{assignment_alias[item.assignment_id]}/attempt/{item.attempt_count}/"
                f"{routes[item.route_index]}"
            )
            if alias in outbox_alias.values():
                raise ValueError("ambiguous semantic outbox identity")
            outbox_alias[item.outbox_id] = alias
            delivery_alias[item.delivery_id] = alias
            normalized_outbox.append(
                {
                    "outbox": alias,
                    "assignment": assignment_alias[item.assignment_id],
                    "attempt": item.attempt_count,
                    "route": routes[item.route_index],
                    "state": item.state,
                    "created_at": _semantic_time(item.created_at),
                    "claimed_at": _semantic_time(item.claimed_at),
                    "completed_at": _semantic_time(item.completed_at),
                }
            )
        normalized_outbox.sort(key=lambda item: str(item["outbox"]))

        def normalize_event_metadata(event) -> dict[str, object]:
            normalized: dict[str, object] = {}
            for key, value in sorted(event.metadata.items()):
                if key == "assignment_id":
                    normalized[key] = assignment_alias[UUID(str(value))]
                elif key == "evidence_id":
                    normalized[key] = evidence_alias[UUID(str(value))]
                elif key == "issue_id":
                    normalized[key] = issue_alias[UUID(str(value))]
                elif key == "proposal_id":
                    normalized[key] = proposal_alias[UUID(str(value))]
                elif key == "meeting_id":
                    normalized[key] = meeting_alias[UUID(str(value))]
                elif key == "delivery_id":
                    alias = delivery_alias.get(str(value))
                    if alias is None:
                        if event.assignment_id is None:
                            raise ValueError("semantic delivery identity lacks assignment")
                        assignment = next(
                            item
                            for item in assignments
                            if item.assignment_id == event.assignment_id
                        )
                        route_index = int(event.metadata.get("route_index", 0))
                        attempt = int(event.metadata.get("attempt_count", 0))
                        alias = (
                            f"{assignment_alias[event.assignment_id]}/attempt/{attempt}/"
                            f"{route_alias_by_id[assignment.route_ids[route_index]]}"
                        )
                    normalized[key] = alias
                elif key == "message_id":
                    normalized[key] = action_alias_for_message(str(value))
                elif key == "route_fingerprint":
                    if event.assignment_id is None:
                        raise ValueError("semantic route fingerprint lacks assignment")
                    assignment = next(
                        item for item in assignments if item.assignment_id == event.assignment_id
                    )
                    route_index = int(event.metadata.get("route_index", 0))
                    normalized[key] = route_alias_by_id[assignment.route_ids[route_index]]
                elif isinstance(value, (bool, int, float)) or value is None:
                    normalized[key] = value
                else:
                    normalized[key] = _sha256(str(value))
            return normalized

        normalized_events = []
        for mandate in sorted(mandates, key=lambda item: item.token):
            for ordinal, event in enumerate(events_by_mandate[mandate.mandate_id], start=1):
                normalized_events.append(
                    {
                        "mandate": mandate.token,
                        "ordinal": ordinal,
                        "type": event.event_type,
                        "created_at": _semantic_time(event.created_at),
                        "actor": event.actor_id,
                        "assignment": (
                            assignment_alias[event.assignment_id]
                            if event.assignment_id is not None
                            else None
                        ),
                        "person": event.person_id,
                        "department": event.department,
                        "direction": event.direction.value if event.direction is not None else None,
                        "channel": event.channel.value if event.channel is not None else None,
                        "previous_state": event.previous_state,
                        "new_state": event.new_state,
                        "metadata": normalize_event_metadata(event),
                    }
                )

        normalized_mandates = [
            {
                "mandate": mandate.token,
                "initiator": mandate.initiator_id,
                "origin_channel": mandate.origin_channel.value,
                "request_digest": _sha256(mandate.redacted_request),
                "objective_digest": _sha256(mandate.objective),
                "plan_digest": _digest_payload(
                    {"plan": mandate.plan.model_dump(mode="json")}
                ),
                "state": mandate.state.value,
                "reason": mandate.reason,
                "next_action_at": _semantic_time(mandate.next_action_at),
                "created_at": _semantic_time(mandate.created_at),
                "updated_at": _semantic_time(mandate.updated_at),
                "expires_at": _semantic_time(mandate.expires_at),
                "completed_at": _semantic_time(mandate.completed_at),
            }
            for mandate in sorted(mandates, key=lambda item: item.token)
        ]
    finally:
        session_factory.kw["bind"].dispose()

    return {
        "schema_version": SUPPORTED_SCHEMA_VERSION,
        "scenario": {
            "scenario_id": scenario.scenario_id,
            "provenance": scenario.provenance.model_dump(mode="json"),
            "personas": [
                {
                    "persona_id": persona.persona_id,
                    "display_name": persona.display_name,
                    "role": persona.role,
                    "identity_digest": _sha256(persona.email),
                    "channels": [channel.value for channel in persona.channels],
                    "allowed_intents": [intent.value for intent in persona.allowed_intents],
                    "private_fact_digests": persona.private_fact_digests,
                }
                for persona in scenario.personas
            ],
        },
        "actions": [
            {
                "action_id": action.action_id,
                "persona": action.persona_id,
                "channel": action.channel.value,
                "timestamp": _semantic_time(action.timestamp),
                "local_sequence": action.local_sequence,
                "trigger_id": action.trigger_id,
                "trigger_digest": action.trigger_digest,
                "intent": action.intent.value,
                "content_digest": _sha256(action.content),
            }
            for action in transcript.actions
        ],
        "inbound_attempts": inbound,
        "deliveries": deliveries,
        "mandates": normalized_mandates,
        "assignments": normalized_assignments,
        "interviews": normalized_interviews,
        "evidence": normalized_evidence,
        "issues": normalized_issues,
        "proposals": normalized_proposals,
        "proposal_responses": normalized_proposal_responses,
        "engagement_decisions": normalized_decisions,
        "meetings": normalized_meetings,
        "outbox": normalized_outbox,
        "events": normalized_events,
        "gateway_handler_count": result.gateway_handler_count,
        "model_client_configured": result.model_client_configured,
        "final_state": result.final_state,
        "terminal_states": list(result.terminal_states),
    }


def semantic_trace_hash(result: SyntheticRunResult) -> str:
    """Hash a UUID- and path-independent semantic projection of one synthetic run."""
    canonical = json.dumps(
        _semantic_trace(result),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if re.search(rb"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", canonical):
        raise ValueError("semantic trace contains a private database UUID")
    return hashlib.sha256(canonical).hexdigest()
