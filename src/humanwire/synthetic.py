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

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

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
from humanwire.evidence import RuleBasedEvidenceExtractor
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
    persona_id: str = Field(pattern=_STABLE_ID_PATTERN)
    timestamp: datetime
    local_sequence: int = Field(ge=1)
    intent: SyntheticIntent
    content: str = Field(min_length=1, max_length=MAX_CONTENT_LENGTH)


class _PersonaContext(_StrictModel):
    """The complete and deliberately narrow view supplied to one policy."""

    persona_id: str = Field(pattern=_STABLE_ID_PATTERN)
    role: str = Field(min_length=1, max_length=200)
    private_facts: tuple[str, ...] = Field(max_length=8)
    allowed_intents: tuple[SyntheticIntent, ...] = Field(min_length=1, max_length=8)
    engagement_contract: str = Field(min_length=1, max_length=64)
    delivered_message: str = Field(min_length=1, max_length=MAX_CONTENT_LENGTH)
    own_inbox: tuple[str, ...] = Field(min_length=1, max_length=64)
    own_transcript: tuple[_PersonaTranscriptEntry, ...] = Field(max_length=64)
    virtual_time: datetime


class _PersonaOutput(_StrictModel):
    schema_version: Literal["humanwire.synthetic/v1"]
    persona_id: str = Field(pattern=_STABLE_ID_PATTERN)
    action_id: str = Field(pattern=_STABLE_ID_PATTERN)
    trigger_digest: str = Field(pattern=_DIGEST_PATTERN)
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
    model_client_configured: bool
    final_state: str


class _DeterministicPersonaPolicy:
    """A stateful policy that can inspect only its explicit persona context."""

    def __init__(self, persona: SyntheticPersona, engagement_contract: EngagementType) -> None:
        self.persona = persona
        self.engagement_contract = engagement_contract
        self.complete = False

    def respond(self, context: _PersonaContext) -> _PersonaOutput:
        sequence = len(context.own_transcript) + 1
        intent, content = self._choose(context)
        return _PersonaOutput(
            schema_version=SUPPORTED_SCHEMA_VERSION,
            persona_id=context.persona_id,
            action_id=f"{context.persona_id}-{sequence}",
            trigger_digest=_sha256(context.delivered_message),
            time_offset_seconds=1,
            intent=intent,
            content=content,
        )

    def _choose(self, context: _PersonaContext) -> tuple[SyntheticIntent, str]:
        prompt = context.delivered_message.casefold()
        if self.engagement_contract is EngagementType.INFORM:
            self.complete = True
            return SyntheticIntent.SILENCE, "synthetic_silence"
        if "evidence confirmation" in prompt and "evidence confirmed" not in prompt:
            self.complete = True
            return SyntheticIntent.CONFIRM_EVIDENCE, "Confirmed."
        if prompt.startswith("question "):
            if self.engagement_contract is EngagementType.QUICK_RESPONSE:
                return SyntheticIntent.ANSWER, "Launch date is 2026-09-01."
            prior_answers = sum(
                item.intent is SyntheticIntent.INTERVIEW_RESPONSE
                for item in context.own_transcript
            )
            if prior_answers == 0 and context.private_facts:
                digest = _sha256(context.private_facts[0])
                return SyntheticIntent.INTERVIEW_RESPONSE, f"PRIVATE: sha256:{digest}"
            return (
                SyntheticIntent.INTERVIEW_RESPONSE,
                "The team can support a reviewed launch.",
            )
        if "approval review" in prompt:
            self.complete = True
            if SyntheticIntent.APPROVE in context.allowed_intents:
                return SyntheticIntent.APPROVE, "Approved."
            return SyntheticIntent.CHANGE, "Use the reviewed launch plan."
        if "availability request" in prompt:
            self.complete = True
            return (
                SyntheticIntent.AVAILABILITY,
                "2026-08-13T15:00:00+00:00/2026-08-13T16:00:00+00:00",
            )
        if "reply ack" in prompt or "acknowledgement" in prompt:
            if self.engagement_contract is EngagementType.ACKNOWLEDGE:
                self.complete = True
            return SyntheticIntent.ACKNOWLEDGE, "Acknowledged."
        self.complete = True
        return SyntheticIntent.SILENCE, "synthetic_silence"


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
    return _DeterministicPersonaPolicy(persona, _contract_for(persona))


class _SyntheticPlanner:
    def __init__(self, people: dict[str, Person], scenario: SyntheticScenario) -> None:
        self.people = people
        self.scenario = scenario

    def plan(self, text: str, initiator: Person) -> ResolvedPlan:
        del text, initiator
        stakeholders: list[PlannedStakeholder] = []
        people: list[Person] = []
        for persona in self.scenario.personas:
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
            stakeholders.append(
                PlannedStakeholder(
                    person_ref=persona.persona_id,
                    reason=f"Provide the {contract.value} contribution",
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
                objective="Coordinate the deterministic synthetic launch",
                required_decisions=["Approve the synthetic launch"],
                stakeholders=stakeholders,
                completion_conditions=["Every required synthetic contribution is recorded"],
            ),
            people=people,
            planner="deterministic_synthetic",
        )


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
    for persona_id, person in people.items():
        if persona_id == "synthetic-manager":
            continue
        for route in person.routes:
            if delivery.kind == "initiate" and route.recipient == delivery.destination:
                return persona_id, Channel.EMAIL
            if (
                delivery.kind == "send"
                and route.conversation_id == delivery.destination
            ):
                return persona_id, Channel.TELEGRAM
    return None


def _persona_context(
    persona: SyntheticPersona,
    contract: EngagementType,
    visible_delivery: str,
    inbox: list[str],
    history: list[_PersonaTranscriptEntry],
    now: datetime,
) -> _PersonaContext:
    return _PersonaContext(
        persona_id=persona.persona_id,
        role=persona.role,
        private_facts=tuple(persona.private_facts),
        allowed_intents=tuple(persona.allowed_intents),
        engagement_contract=contract.value,
        delivered_message=visible_delivery,
        own_inbox=(*inbox, visible_delivery),
        own_transcript=tuple(history),
        virtual_time=now,
    )


def generate_scenario(
    scenario: SyntheticScenario,
    output_path: str | Path,
    run_root: str | Path,
) -> SyntheticRunResult:
    """Generate one isolated deterministic run through the real gateway boundary."""
    scenario = SyntheticScenario.model_validate(scenario)
    output = Path(output_path)
    root = Path(run_root)
    root.mkdir(parents=True, exist_ok=True)
    database_path = root / "humanwire-synthetic.sqlite3"
    if database_path.exists():
        raise FileExistsError("synthetic generation requires a fresh run root")

    settings = _isolated_settings(database_path)
    directory, people = _synthetic_directory(scenario)
    session_factory = create_session_factory(settings.database_url)
    repository = SqlAlchemyHumanWireRepository(session_factory)
    workflow = HumanWireWorkflow(
        directory,
        repository,
        _SyntheticPlanner(people, scenario),
        RuleBasedEvidenceExtractor(),
        settings,
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
            policy = policies[persona_id]
            if getattr(policy, "complete", False):
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
                persona,
                _contract_for(persona),
                visible,
                inboxes[persona_id],
                histories[persona_id],
                now + timedelta(seconds=1),
            )
            try:
                raw_output = policy.respond(context)
                output_value = _PersonaOutput.model_validate(raw_output)
                expected_action_id = f"{persona_id}-{local_sequence}"
                if (
                    output_value.persona_id != persona_id
                    or output_value.action_id != expected_action_id
                    or output_value.trigger_digest != trigger_digest
                    or output_value.intent not in persona.allowed_intents
                ):
                    raise ValueError("persona output violated its response contract")
                action_time = now + timedelta(seconds=output_value.time_offset_seconds)
                action = SyntheticAction(
                    schema_version=SUPPORTED_SCHEMA_VERSION,
                    action_id=output_value.action_id,
                    persona_id=persona_id,
                    channel=channel,
                    timestamp=action_time,
                    local_sequence=local_sequence,
                    trigger_id=trigger_id,
                    trigger_digest=trigger_digest,
                    intent=output_value.intent,
                    content=output_value.content,
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
    while queued:
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
                persona_id=persona_id,
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
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        transcript.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    latest = repository.list_recent_mandates(1)
    final_state = latest[0].state.value if latest else "missing"
    session_factory.kw["bind"].dispose()
    return SyntheticRunResult(
        transcript=transcript,
        database_path=database_path,
        gateway_handler_count=client.on_message_registration_count,
        inbound_envelopes=tuple(inbound_envelopes),
        model_client_configured=False,
        final_state=final_state,
    )
