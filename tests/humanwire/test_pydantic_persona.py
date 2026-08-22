from __future__ import annotations

import json
import multiprocessing
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from pydantic import SecretStr, ValidationError

import humanwire.pydantic_persona as pydantic_persona_module
import humanwire.synthetic as synthetic_module
from humanwire.domain import Channel, EngagementType
from humanwire.model_client import ModelFailure
from humanwire.persona_runtime import (
    PersonaContext,
    PersonaDecision,
    PersonaProfile,
    PersonaVisibility,
    SyntheticIntent,
)
from humanwire.pydantic_persona import (
    PydanticAIPersonaDecisionEngine,
    PydanticAIPersonaDecisionEngineFactory,
)
from humanwire.synthetic import (
    SUPPORTED_SCHEMA_VERSION,
    SyntheticPersona,
    SyntheticProvenance,
    SyntheticScenario,
    generate_scenario,
)


@dataclass
class AgentCall:
    user: str
    model_settings: object


def fake_agent_class(decision: PersonaDecision, calls: list[AgentCall]):
    class FakeAgent:
        def __init__(self, model, *, output_type, system_prompt, retries):
            assert output_type is PersonaDecision
            assert retries == 0
            self.system_prompt = system_prompt

        def run_sync(self, user, *, model_settings):
            calls.append(AgentCall(user=user, model_settings=model_settings))
            return SimpleNamespace(output=decision)

    return FakeAgent


def quick_profile() -> PersonaProfile:
    return PersonaProfile(
        role="Product lead",
        private_facts=("PRIVATE-PERSONA-SENTINEL",),
        allowed_intents=(SyntheticIntent.ANSWER,),
        engagement_contract=EngagementType.QUICK_RESPONSE,
    )


def own_context() -> PersonaContext:
    return PersonaContext(
        delivered_message="Does the launch scope support a decision tomorrow?",
        own_inbox=("Does the launch scope support a decision tomorrow?",),
        own_transcript=(),
        virtual_time=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
    )


def valid_answer() -> PersonaDecision:
    return PersonaDecision(
        time_offset_seconds=1,
        intent=SyntheticIntent.ANSWER,
        content="The launch can proceed if rollback ownership is recorded.",
        visibility=PersonaVisibility.SHAREABLE,
    )


def engine_returning(monkeypatch, decision, calls=None):
    recorded = [] if calls is None else calls
    monkeypatch.setattr(
        "humanwire.pydantic_persona.Agent",
        fake_agent_class(decision, recorded),
    )
    return PydanticAIPersonaDecisionEngine(
        model=object(),
        model_identifier="test-model",
    )


def _run_with_mock_provider_http(
    target,
    target_args,
    behavior: str,
    request_marker: str,
) -> None:
    import httpx
    from pydantic_ai.providers.openai import OpenAIProvider as RealOpenAIProvider

    import humanwire.pydantic_persona as child_module

    def handler(request: httpx.Request) -> httpx.Response:
        Path(request_marker).write_text("called", encoding="utf-8")
        if behavior == "timeout":
            while True:
                time.sleep(0.05)
        return httpx.Response(
            200,
            json={
                "id": "typed-persona-result",
                "created": 0,
                "model": "test-model",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "time_offset_seconds": 1,
                                    "intent": "acknowledge",
                                    "content": "Acknowledged.",
                                    "visibility": "shareable",
                                }
                            ),
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    def mock_provider(*, api_key: str, base_url: str):
        return RealOpenAIProvider(
            api_key=api_key,
            base_url=base_url,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    child_module.OpenAIProvider = mock_provider
    target(*target_args)


class _MockProviderSpawnContext:
    def __init__(self, real_context, behavior: str, request_marker: Path) -> None:
        self._real_context = real_context
        self._behavior = behavior
        self._request_marker = request_marker

    def Pipe(self, *args, **kwargs):
        return self._real_context.Pipe(*args, **kwargs)

    def Event(self, *args, **kwargs):
        return self._real_context.Event(*args, **kwargs)

    def Process(self, *, target, args, name, daemon):
        return self._real_context.Process(
            target=_run_with_mock_provider_http,
            args=(target, args, self._behavior, str(self._request_marker)),
            name=name,
            daemon=daemon,
        )


def one_person_generation_scenario() -> SyntheticScenario:
    return SyntheticScenario(
        schema_version=SUPPORTED_SCHEMA_VERSION,
        scenario_id="typed-persona-test",
        identity_seed=0,
        identity_generator_version="humanwire.synthetic-identities/v1",
        personas=[
            SyntheticPersona(
                persona_id="synthetic-manager",
                display_name="Manager Persona",
                role="Simulation manager",
                email="manager@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[SyntheticIntent.AVAILABILITY],
            ),
            SyntheticPersona(
                persona_id="owner",
                display_name="Owner Persona",
                role="Operations owner",
                email="owner@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[SyntheticIntent.ACKNOWLEDGE],
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


def test_pydantic_factory_is_strict_spawn_safe_configuration() -> None:
    factory = PydanticAIPersonaDecisionEngineFactory(
        api_key=SecretStr("private-test-key"),
        model_identifier="Qwen/Qwen2.5-7B-Instruct",
        base_url="https://api.featherless.ai/v1",
    )
    assert factory.model_identifier == "Qwen/Qwen2.5-7B-Instruct"
    assert "private-test-key" not in repr(factory)
    with pytest.raises(ValidationError):
        PydanticAIPersonaDecisionEngineFactory(
            api_key=SecretStr("private-test-key"),
            model_identifier="model",
            base_url="https://api.featherless.ai/v1",
            extra=True,
        )


def test_pydantic_factory_builds_with_only_explicit_provider_configuration(
    monkeypatch,
) -> None:
    key = "PRIVATE-EXPLICIT-PROVIDER-KEY"
    calls: dict[str, object] = {}

    class FakeProvider:
        def __init__(self, *, api_key, base_url):
            calls["provider"] = {"api_key": api_key, "base_url": base_url}

    class FakeModel:
        def __init__(self, model_identifier, *, provider):
            calls["model"] = {"model_identifier": model_identifier, "provider": provider}

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setattr(pydantic_persona_module, "OpenAIProvider", FakeProvider)
    monkeypatch.setattr(pydantic_persona_module, "OpenAIChatModel", FakeModel)
    factory = PydanticAIPersonaDecisionEngineFactory(
        api_key=SecretStr(key),
        model_identifier="Qwen/Qwen2.5-7B-Instruct",
        base_url="https://api.featherless.ai/v1",
    )

    engine = factory.build()

    assert calls["provider"] == {
        "api_key": key,
        "base_url": "https://api.featherless.ai/v1",
    }
    assert calls["model"]["model_identifier"] == "Qwen/Qwen2.5-7B-Instruct"
    assert calls["model"]["provider"] is not None
    assert engine.model_identifier == "Qwen/Qwen2.5-7B-Instruct"
    assert key not in repr(factory)


def test_pydantic_engine_returns_centrally_validated_typed_decision(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "humanwire.pydantic_persona.Agent",
        fake_agent_class(valid_answer(), calls),
    )
    engine = PydanticAIPersonaDecisionEngine(
        model=object(),
        model_identifier="test-model",
    )
    decision = engine.decide(
        quick_profile(),
        own_context(),
        deadline=time.monotonic() + 2,
        cancellation=Event(),
    )
    assert decision.intent is SyntheticIntent.ANSWER
    assert len(calls) == 1
    assert set(json.loads(calls[0].user)) == {
        "context",
        "profile",
        "response_contract",
    }
    assert "sender_address" not in calls[0].user


@pytest.mark.parametrize(
    "decision",
    [
        PersonaDecision(
            time_offset_seconds=1,
            intent=SyntheticIntent.APPROVE,
            content="Approved",
            visibility=PersonaVisibility.SHAREABLE,
        ),
        PersonaDecision(
            time_offset_seconds=1,
            intent=SyntheticIntent.ANSWER,
            content="PRIVATE-PERSONA-SENTINEL",
            visibility=PersonaVisibility.SHAREABLE,
        ),
        PersonaDecision(
            time_offset_seconds=1,
            intent=SyntheticIntent.ANSWER,
            content="route_id=forged",
            visibility=PersonaVisibility.SHAREABLE,
        ),
    ],
)
def test_pydantic_engine_cannot_bypass_central_validation(monkeypatch, decision) -> None:
    engine = engine_returning(monkeypatch, decision)
    with pytest.raises(ValueError):
        engine.decide(
            quick_profile(),
            own_context(),
            deadline=time.monotonic() + 2,
            cancellation=Event(),
        )


def test_pydantic_engine_refuses_expired_or_cancelled_work_without_call(monkeypatch) -> None:
    calls = []
    engine = engine_returning(monkeypatch, valid_answer(), calls)
    cancellation = Event()
    cancellation.set()
    with pytest.raises(ModelFailure, match="timeout"):
        engine.decide(
            quick_profile(),
            own_context(),
            deadline=time.monotonic() - 1,
            cancellation=cancellation,
        )
    assert calls == []


def test_pydantic_factory_typed_decision_stays_inside_spawned_generation_child(
    tmp_path,
    monkeypatch,
) -> None:
    key = "PRIVATE-SPAWN-SUCCESS-KEY"
    request_marker = tmp_path / "success-http-called"
    real_context = multiprocessing.get_context("spawn")
    context = _MockProviderSpawnContext(real_context, "success", request_marker)
    monkeypatch.setattr(synthetic_module.multiprocessing, "get_context", lambda method: context)
    factory = PydanticAIPersonaDecisionEngineFactory(
        api_key=SecretStr(key),
        model_identifier="test-model",
        base_url="https://models.example.test/v1",
    )

    result = generate_scenario(
        one_person_generation_scenario(),
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        decision_engine=factory,
    )

    assert request_marker.read_text(encoding="utf-8") == "called"
    assert [(item.intent, item.content) for item in result.transcript.actions] == [
        (SyntheticIntent.ACKNOWLEDGE, "Acknowledged.")
    ]
    assert result.gateway_handler_count == 1
    assert len(result.inbound_envelopes) == 1
    assert set(vars(factory)) == {"api_key", "model_identifier", "base_url"}
    assert all(value != key for value in vars(factory).values())
    assert key not in repr(factory)
    assert not multiprocessing.active_children()
    assert not any(
        thread.name.startswith("humanwire-persona") for thread in threading.enumerate()
    )


def test_pydantic_factory_hard_timeout_is_inert_and_reaps_spawned_work(
    tmp_path,
    monkeypatch,
) -> None:
    request_marker = tmp_path / "timeout-http-called"
    real_context = multiprocessing.get_context("spawn")
    context = _MockProviderSpawnContext(real_context, "timeout", request_marker)
    monkeypatch.setattr(synthetic_module.multiprocessing, "get_context", lambda method: context)
    monkeypatch.setattr(synthetic_module, "MODEL_DECISION_TIMEOUT_SECONDS", 4.0)
    monkeypatch.setattr(synthetic_module, "MODEL_DECISION_CANCELLATION_GRACE_SECONDS", 0.2)
    factory = PydanticAIPersonaDecisionEngineFactory(
        api_key=SecretStr("PRIVATE-SPAWN-TIMEOUT-KEY"),
        model_identifier="test-model",
        base_url="https://models.example.test/v1",
    )

    started = time.monotonic()
    result = generate_scenario(
        one_person_generation_scenario(),
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        decision_engine=factory,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 8
    assert request_marker.read_text(encoding="utf-8") == "called"
    assert [(item.intent, item.content) for item in result.transcript.actions] == [
        (SyntheticIntent.ERROR, "synthetic_model_timeout")
    ]
    assert result.inbound_envelopes == ()
    assert result.gateway_handler_count == 1
    assert not multiprocessing.active_children()
    assert not any(
        thread.name.startswith("humanwire-persona") for thread in threading.enumerate()
    )
