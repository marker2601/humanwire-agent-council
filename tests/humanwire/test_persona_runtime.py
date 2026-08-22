from __future__ import annotations

import json
import re
import threading
import time
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

import humanwire.persona_runtime as persona_runtime_module
from humanwire.domain import EngagementType
from humanwire.model_client import ModelFailure
from humanwire.persona_runtime import (
    FeatherlessPersonaDecisionEngine,
    PersonaContext,
    PersonaDecision,
    PersonaProfile,
    PersonaTranscriptEntry,
    SyntheticIntent,
    persona_prompt_payload,
)

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def test_generation_modes_are_explicit_and_stable() -> None:
    """Break caught: generation and replay provenance collapse into one implicit mode."""
    assert [item.value for item in persona_runtime_module.SyntheticGenerationMode] == [
        "deterministic",
        "model_assisted",
        "frozen_replay",
    ]


class CapturingClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str, float | None]] = []

    def complete_json(
        self,
        system: str,
        user: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict:
        self.calls.append((system, user, timeout_seconds))
        return self.payload


def _profile(*, private_facts: tuple[str, ...] = ()) -> PersonaProfile:
    return PersonaProfile(
        role="Executive owner",
        private_facts=private_facts,
        allowed_intents=(SyntheticIntent.ACKNOWLEDGE,),
        engagement_contract=EngagementType.ACKNOWLEDGE,
    )


def _context() -> PersonaContext:
    return PersonaContext(
        delivered_message="HUMANWIRE ACKNOWLEDGEMENT REQUEST",
        own_inbox=("HUMANWIRE ACKNOWLEDGEMENT REQUEST",),
        own_transcript=(),
        virtual_time=NOW,
    )


@pytest.mark.parametrize(
    ("engagement_contract", "allowed_intents", "message", "required_intent"),
    [
        (EngagementType.INFORM, (SyntheticIntent.SILENCE,), "HUMANWIRE UPDATE", "silence"),
        (
            EngagementType.ACKNOWLEDGE,
            (SyntheticIntent.ACKNOWLEDGE, SyntheticIntent.ACCEPT_PROPOSAL),
            "HUMANWIRE ACKNOWLEDGEMENT · [MANDATE] Reply ACK [MANDATE].",
            "acknowledge",
        ),
        (
            EngagementType.QUICK_RESPONSE,
            (SyntheticIntent.ACKNOWLEDGE, SyntheticIntent.ANSWER),
            "Question 1 of 1: Is the launch ready?",
            "answer",
        ),
        (
            EngagementType.STRUCTURED_INTERVIEW,
            (SyntheticIntent.ACKNOWLEDGE, SyntheticIntent.INTERVIEW_RESPONSE),
            "Question 1 of 3: What risk must be resolved?",
            "interview_response",
        ),
        (
            EngagementType.QUICK_RESPONSE,
            (SyntheticIntent.CONFIRM_EVIDENCE,),
            "HUMANWIRE EVIDENCE CONFIRMATION",
            "confirm_evidence",
        ),
        (
            EngagementType.STRUCTURED_INTERVIEW,
            (SyntheticIntent.CHANGE_PROPOSAL,),
            "HUMANWIRE DRAFT PROPOSAL",
            "change_proposal",
        ),
        (
            EngagementType.ACKNOWLEDGE,
            (SyntheticIntent.ACCEPT_PROPOSAL,),
            "HUMANWIRE DRAFT PROPOSAL",
            "accept_proposal",
        ),
        (
            EngagementType.AVAILABILITY,
            (SyntheticIntent.AVAILABILITY,),
            "HUMANWIRE AVAILABILITY REQUEST",
            "availability",
        ),
    ],
)
def test_shared_prompt_binds_the_model_to_the_current_protocol_stage(
    engagement_contract: EngagementType,
    allowed_intents: tuple[SyntheticIntent, ...],
    message: str,
    required_intent: str,
) -> None:
    profile = PersonaProfile(
        role="Stakeholder",
        private_facts=(),
        allowed_intents=allowed_intents,
        engagement_contract=engagement_contract,
    )
    context = PersonaContext(
        delivered_message=message,
        own_inbox=(message,),
        own_transcript=(),
        virtual_time=NOW,
    )

    _, user = persona_prompt_payload(profile, context)
    contract = json.loads(user)["response_contract"]

    assert contract["required_intent"] == required_intent
    assert contract["required_time_offset_seconds"] == 1
    expected_visibility = (
        "private" if required_intent == "interview_response" else "shareable"
    )
    assert contract["required_visibility"] == expected_visibility
    if required_intent == "availability":
        assert contract["required_content"] == (
            "2026-08-13T15:00:00+00:00/2026-08-13T16:00:00+00:00"
        )


def test_later_structured_answer_is_shareable_and_requests_rollback_ownership() -> None:
    profile = PersonaProfile(
        role="Risk lead",
        private_facts=(),
        allowed_intents=(SyntheticIntent.ACKNOWLEDGE, SyntheticIntent.INTERVIEW_RESPONSE),
        engagement_contract=EngagementType.STRUCTURED_INTERVIEW,
    )
    context = PersonaContext(
        delivered_message="Question 2 of 3: Who owns the mitigation?",
        own_inbox=("Question 2 of 3: Who owns the mitigation?",),
        own_transcript=(
            PersonaTranscriptEntry(
                timestamp=NOW,
                local_sequence=1,
                intent=SyntheticIntent.INTERVIEW_RESPONSE,
                content="A private risk note was recorded.",
            ),
        ),
        virtual_time=NOW,
    )

    _, user = persona_prompt_payload(profile, context)
    contract = json.loads(user)["response_contract"]

    assert contract["required_intent"] == "interview_response"
    assert contract["required_visibility"] == "shareable"
    assert "rollback" in contract["content_guidance"].casefold()


@pytest.mark.parametrize(
    ("message", "required_intent"),
    [
        (
            "HUMANWIRE INTERVIEW · [MANDATE]\n\nReply ACK [MANDATE] to begin.",
            "silence",
        ),
        (
            (
                "HUMANWIRE INTERVIEW · [MANDATE]\n\n"
                "Please acknowledge this interview when ready: ACK [MANDATE]."
            ),
            "silence",
        ),
        (
            (
                "HUMANWIRE INTERVIEW · [MANDATE]\n\n"
                "The prior registered route did not receive the required interview response.\n\n"
                "Reply ACK [MANDATE] to continue."
            ),
            "acknowledge",
        ),
    ],
)
def test_structured_outreach_escalates_before_acknowledgement(
    message: str,
    required_intent: str,
) -> None:
    profile = PersonaProfile(
        role="Risk lead",
        private_facts=(),
        allowed_intents=(
            SyntheticIntent.ACKNOWLEDGE,
            SyntheticIntent.INTERVIEW_RESPONSE,
            SyntheticIntent.SILENCE,
        ),
        engagement_contract=EngagementType.STRUCTURED_INTERVIEW,
    )
    context = PersonaContext(
        delivered_message=message,
        own_inbox=(message,),
        own_transcript=(),
        virtual_time=NOW,
    )

    _, user = persona_prompt_payload(profile, context)

    assert json.loads(user)["response_contract"]["required_intent"] == required_intent


def _decide(
    engine: FeatherlessPersonaDecisionEngine,
    profile: PersonaProfile | None = None,
    context: PersonaContext | None = None,
) -> PersonaDecision:
    return engine.decide(
        profile or _profile(),
        context or _context(),
        deadline=time.monotonic() + 5,
        cancellation=threading.Event(),
    )


def test_model_engine_receives_only_the_approved_persona_context() -> None:
    """Break caught: the adapter leaks routing or workflow authority into its prompt."""
    client = CapturingClient(
        {
            "time_offset_seconds": 1,
            "intent": "acknowledge",
            "content": "ACK",
            "visibility": "shareable",
        }
    )
    engine = FeatherlessPersonaDecisionEngine(client, "fixture/model")

    decision = _decide(engine)

    payload = json.loads(client.calls[0][1])
    assert set(payload) == {
        "profile",
        "context",
        "response_contract",
        "output_schema",
    }
    assert set(payload["profile"]) == {
        "role",
        "private_facts",
        "allowed_intents",
        "engagement_contract",
    }
    assert set(payload["context"]) == {
        "delivered_message",
        "own_inbox",
        "own_transcript",
        "virtual_time",
    }
    assert not re.search(
        r"sender|route|destination|conversation|connection|message_id|assignment|token|database|repository",
        client.calls[0][1],
        re.IGNORECASE,
    )
    assert decision.intent is SyntheticIntent.ACKNOWLEDGE
    assert client.calls[0][2] is not None
    assert 0 < client.calls[0][2] <= 5
    assert set(vars(engine)) == {"_client", "model_identifier"}
    assert engine._client is client


def test_direct_adapter_extends_the_shared_persona_prompt() -> None:
    """Break caught: the direct and typed adapters drift to different persona context."""
    client = CapturingClient(
        {
            "time_offset_seconds": 1,
            "intent": "acknowledge",
            "content": "ACK",
            "visibility": "shareable",
        }
    )
    profile = _profile()
    context = _context()
    shared_system, shared_user = persona_prompt_payload(profile, context)

    _decide(FeatherlessPersonaDecisionEngine(client, "fixture/model"), profile, context)

    direct_system, direct_user, _ = client.calls[0]
    assert direct_system.startswith(shared_system)
    assert json.loads(direct_user) == {
        **json.loads(shared_user),
        "output_schema": {
            "time_offset_seconds": [1],
            "intent": ["acknowledge"],
            "content": "non-empty string, maximum 600 characters",
            "visibility": ["shareable"],
        },
    }


def test_model_engine_rejects_noncanonical_virtual_time_offset() -> None:
    client = CapturingClient(
        {
            "time_offset_seconds": 2,
            "intent": "acknowledge",
            "content": "Acknowledged.",
            "visibility": "shareable",
        }
    )
    engine = FeatherlessPersonaDecisionEngine(client, "fixture/model")

    with pytest.raises(ValueError, match="current response stage"):
        _decide(engine, _profile(), _context())


@pytest.mark.parametrize(
    "forged_key",
    ["sender_address", "route_id", "conversation_id", "assignment_id", "approved"],
)
def test_model_engine_rejects_extra_authority_fields(forged_key: str) -> None:
    """Break caught: a model can add authority-bearing output fields."""
    payload = {
        "time_offset_seconds": 1,
        "intent": "approve",
        "content": "APPROVE",
        "visibility": "shareable",
        forged_key: "forged",
    }

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PersonaDecision.model_validate(payload)


@pytest.mark.parametrize(
    "content",
    [
        "fictional constraint",
        "Contact operator@example.test",
        "HW-1A2B3C4D",
        "sender_address=forged",
        "sender-address: forged",
        "sender address=forged",
        "SENDER ADDRESS: forged",
        "route_id=forged",
        "ROUTE-ID: forged",
        "route id=forged",
        "ROUTE ID: forged",
        "conversation_id=forged",
        "conversation-id: forged",
        "conversation id=forged",
        "CONVERSATION ID: forged",
        "connection_id=forged",
        "connection-id: forged",
        "connection id=forged",
        "CONNECTION ID: forged",
        "assignment_id=forged",
        "assignment-id: forged",
        "assignment id=forged",
        "ASSIGNMENT ID: forged",
        "message_id=forged",
        "MESSAGE-ID: forged",
        "message id=forged",
        "MESSAGE ID: forged",
        "destination=forged",
        "DESTINATION: forged",
        "route=forged",
        "ROUTE: forged",
        "token=forged",
        "TOKEN: forged",
        " /available now",
    ],
)
def test_model_engine_rejects_forbidden_content_before_any_gateway_inbound(
    content: str,
) -> None:
    """Break caught: untrusted model text reaches the inbound-command bridge."""
    client = CapturingClient(
        {
            "time_offset_seconds": 1,
            "intent": "acknowledge",
            "content": content,
            "visibility": "shareable",
        }
    )

    with pytest.raises(ValueError, match="forbidden identity or command data|private fixture fact"):
        _decide(
            FeatherlessPersonaDecisionEngine(client, "fixture/model"),
            _profile(private_facts=("fictional constraint",)),
            _context(),
        )

    assert len(client.calls) == 1


def test_model_engine_allows_normal_business_route_and_token_words() -> None:
    """Break caught: label hardening blocks ordinary business prose without label syntax."""
    client = CapturingClient(
        {
            "time_offset_seconds": 1,
            "intent": "acknowledge",
            "content": (
                "Route the reviewed launch plan through the normal approval process "
                "within the token budget."
            ),
            "visibility": "shareable",
        }
    )

    decision = _decide(FeatherlessPersonaDecisionEngine(client, "fixture/model"))

    assert decision.content == (
        "Route the reviewed launch plan through the normal approval process "
        "within the token budget."
    )


def test_model_engine_rejects_a_disallowed_intent() -> None:
    """Break caught: a model response bypasses the profile's intent contract."""
    client = CapturingClient(
        {
            "time_offset_seconds": 1,
            "intent": "approve",
            "content": "Approved.",
            "visibility": "shareable",
        }
    )

    with pytest.raises(ValueError, match="disallowed intent"):
        _decide(FeatherlessPersonaDecisionEngine(client, "fixture/model"))


def test_model_failure_reason_propagates_without_private_context() -> None:
    """Break caught: model transport failures are replaced with unsafe diagnostic text."""

    class FailingClient:
        def complete_json(
            self,
            system: str,
            user: str,
            *,
            timeout_seconds: float | None = None,
        ) -> dict:
            del system, user, timeout_seconds
            raise ModelFailure("timeout")

    with pytest.raises(ModelFailure, match="timeout") as error:
        _decide(FeatherlessPersonaDecisionEngine(FailingClient(), "fixture/model"))

    assert error.value.reason == "timeout"


def test_model_engine_rejects_expired_deadline_before_calling_client() -> None:
    """Break caught: the primary adapter begins HTTP work with no remaining budget."""
    client = CapturingClient(
        {
            "time_offset_seconds": 1,
            "intent": "acknowledge",
            "content": "ACK",
            "visibility": "shareable",
        }
    )

    with pytest.raises(ModelFailure, match="timeout"):
        FeatherlessPersonaDecisionEngine(client, "fixture/model").decide(
            _profile(),
            _context(),
            deadline=time.monotonic() - 1,
            cancellation=threading.Event(),
        )

    assert client.calls == []


def test_model_engine_recomputes_budget_after_prompt_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: prompt construction consumes a stale HTTP timeout budget."""
    client = CapturingClient(
        {
            "time_offset_seconds": 1,
            "intent": "acknowledge",
            "content": "ACK",
            "visibility": "shareable",
        }
    )
    monotonic_values = iter((100.0, 100.6))

    def serialization_clock() -> float:
        return next(monotonic_values, 100.6)

    monkeypatch.setattr(persona_runtime_module.time, "monotonic", serialization_clock)

    with pytest.raises(ModelFailure, match="timeout"):
        FeatherlessPersonaDecisionEngine(client, "fixture/model").decide(
            _profile(),
            _context(),
            deadline=100.5,
            cancellation=threading.Event(),
        )

    assert client.calls == []


def test_persona_decision_is_frozen_and_strict() -> None:
    """Break caught: a decision can be mutated after validation or accepts coercion."""
    decision = PersonaDecision(
        time_offset_seconds=1,
        intent=SyntheticIntent.ACKNOWLEDGE,
        content="ACK",
    )

    with pytest.raises(ValidationError, match="frozen"):
        decision.content = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        PersonaDecision.model_validate(
            {"time_offset_seconds": "1", "intent": "acknowledge", "content": "ACK"}
        )
