from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from humanwire.domain import EngagementType
from humanwire.model_client import ModelFailure
from humanwire.persona_runtime import (
    FeatherlessPersonaDecisionEngine,
    PersonaContext,
    PersonaDecision,
    PersonaProfile,
    SyntheticIntent,
)

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


class CapturingClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> dict:
        self.calls.append((system, user))
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


def test_model_engine_receives_only_the_approved_persona_context() -> None:
    """Break caught: the adapter leaks routing or workflow authority into its prompt."""
    client = CapturingClient(
        {
            "time_offset_seconds": 2,
            "intent": "acknowledge",
            "content": "ACK",
            "visibility": "shareable",
        }
    )
    engine = FeatherlessPersonaDecisionEngine(client, "fixture/model")

    decision = engine.decide(_profile(), _context())

    payload = json.loads(client.calls[0][1])
    assert set(payload) == {"profile", "context", "output_schema"}
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
    assert set(vars(engine)) == {"_client", "model_identifier"}
    assert engine._client is client


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
        "route_id=forged",
        "ROUTE-ID: forged",
        "conversation_id=forged",
        "conversation-id: forged",
        "connection_id=forged",
        "connection-id: forged",
        "assignment_id=forged",
        "assignment-id: forged",
        "message_id=forged",
        "MESSAGE-ID: forged",
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
        FeatherlessPersonaDecisionEngine(client, "fixture/model").decide(
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

    decision = FeatherlessPersonaDecisionEngine(client, "fixture/model").decide(
        _profile(), _context()
    )

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
        FeatherlessPersonaDecisionEngine(client, "fixture/model").decide(_profile(), _context())


def test_model_failure_reason_propagates_without_private_context() -> None:
    """Break caught: model transport failures are replaced with unsafe diagnostic text."""

    class FailingClient:
        def complete_json(self, system: str, user: str) -> dict:
            raise ModelFailure("timeout")

    with pytest.raises(ModelFailure, match="timeout") as error:
        FeatherlessPersonaDecisionEngine(FailingClient(), "fixture/model").decide(
            _profile(), _context()
        )

    assert error.value.reason == "timeout"


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
