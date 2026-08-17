from __future__ import annotations

import json
import multiprocessing
import os
import pickle
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import pytest
from google.adk.models import LlmResponse
from google.genai import types

from humanwire.domain import EngagementType
from humanwire.google_config import GoogleAuthMode, GoogleRuntimeConfig
from humanwire.google_decision_engine import (
    GoogleAdkPersonaDecisionEngine,
    GoogleAdkPersonaDecisionEngineFactory,
)
from humanwire.model_client import ModelFailure
from humanwire.persona_runtime import (
    PersonaContext,
    PersonaDecision,
    PersonaProfile,
    PersonaVisibility,
    SyntheticIntent,
)
from humanwire.synthetic import generate_scenario
from tests.humanwire.test_pydantic_persona import one_person_generation_scenario


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
        virtual_time=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
    )


def model_callback(
    decision: dict,
    calls: list[str],
    prompts: list[str] | None = None,
):
    def callback(*, callback_context, llm_request):
        del callback_context
        calls.append(llm_request.model)
        if prompts is not None:
            prompts.append(
                "\n".join(
                    part.text
                    for content in llm_request.contents or ()
                    for part in content.parts or ()
                    if part.text
                )
            )
        return LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text=json.dumps(decision))],
            ),
            turn_complete=True,
        )

    return callback


def _run_with_mock_adk_boundary(
    target,
    target_args,
    behavior: str,
    request_marker: str,
) -> None:
    import humanwire.google_decision_engine as child_module

    original_agent = child_module.Agent

    def callback(*, callback_context, llm_request):
        del callback_context, llm_request
        Path(request_marker).write_text("called", encoding="utf-8")
        if behavior == "timeout":
            while True:
                time.sleep(0.05)
        return LlmResponse(
            content=types.Content(
                role="model",
                parts=[
                    types.Part.from_text(
                        text=json.dumps(
                            {
                                "time_offset_seconds": 1,
                                "intent": "acknowledge",
                                "content": "Acknowledged.",
                                "visibility": "shareable",
                            }
                        )
                    )
                ],
            ),
            turn_complete=True,
        )

    def agent_with_mock_boundary(*args, **kwargs):
        kwargs["before_model_callback"] = callback
        return original_agent(*args, **kwargs)

    child_module.Agent = agent_with_mock_boundary
    os.environ["GEMINI_API_KEY"] = "PRIVATE-CHILD-ONLY-GEMINI-SENTINEL"
    target(*target_args)


class _MockAdkSpawnContext:
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
            target=_run_with_mock_adk_boundary,
            args=(target, args, self._behavior, str(self._request_marker)),
            name=name,
            daemon=daemon,
        )


def test_real_adk_runner_returns_one_centrally_validated_decision() -> None:
    calls: list[str] = []
    prompts: list[str] = []
    expected = PersonaDecision(
        time_offset_seconds=2,
        intent=SyntheticIntent.ANSWER,
        content="Proceed after rollback ownership is recorded.",
        visibility=PersonaVisibility.SHAREABLE,
    )
    engine = GoogleAdkPersonaDecisionEngine(
        model_identifier="gemini-3.6-flash",
        before_model_callback=model_callback(
            expected.model_dump(mode="json"), calls, prompts
        ),
    )

    result = engine.decide(
        quick_profile(),
        own_context(),
        deadline=time.monotonic() + 5,
        cancellation=Event(),
    )

    assert result == expected
    assert calls == ["gemini-3.6-flash"]
    assert len(prompts) == 1
    assert own_context().delivered_message in prompts[0]
    assert SyntheticIntent.ANSWER.value in prompts[0]


@pytest.mark.parametrize(
    "decision",
    [
        {
            "time_offset_seconds": 1,
            "intent": "approve",
            "content": "Approved.",
            "visibility": "shareable",
        },
        {
            "time_offset_seconds": 1,
            "intent": "answer",
            "content": "PRIVATE-PERSONA-SENTINEL",
            "visibility": "shareable",
        },
    ],
)
def test_google_output_cannot_cross_existing_authority_or_privacy(
    decision: dict,
) -> None:
    engine = GoogleAdkPersonaDecisionEngine(
        model_identifier="gemini-3.6-flash",
        before_model_callback=model_callback(decision, []),
    )

    with pytest.raises(ValueError):
        engine.decide(
            quick_profile(),
            own_context(),
            deadline=time.monotonic() + 5,
            cancellation=Event(),
        )


@pytest.mark.parametrize("cancelled", [False, True])
def test_google_engine_fails_before_adk_when_deadline_is_not_live(cancelled: bool) -> None:
    calls: list[str] = []
    cancellation = Event()
    if cancelled:
        cancellation.set()
    engine = GoogleAdkPersonaDecisionEngine(
        model_identifier="gemini-3.6-flash",
        before_model_callback=model_callback(
            {
                "time_offset_seconds": 1,
                "intent": "answer",
                "content": "This must not run.",
                "visibility": "shareable",
            },
            calls,
        ),
    )

    with pytest.raises(ModelFailure, match="timeout"):
        engine.decide(
            quick_profile(),
            own_context(),
            deadline=time.monotonic() - 1,
            cancellation=cancellation,
        )
    assert calls == []


def test_factory_is_picklable_and_contains_no_api_key(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "PRIVATE-GEMINI-SENTINEL")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "false")
    factory = GoogleAdkPersonaDecisionEngineFactory(
        runtime=GoogleRuntimeConfig(
            model_id="gemini-3.6-flash",
            auth_mode=GoogleAuthMode.AI_STUDIO_KEY,
            project_id=None,
            location="us-central1",
        )
    )

    serialized = pickle.dumps(factory)
    engine = factory.build()

    assert engine.model_identifier == "gemini-3.6-flash"
    assert b"PRIVATE-GEMINI-SENTINEL" not in serialized
    assert "PRIVATE-GEMINI-SENTINEL" not in repr(factory)


def test_factory_fails_closed_when_ai_studio_key_is_absent(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    factory = GoogleAdkPersonaDecisionEngineFactory(
        runtime=GoogleRuntimeConfig(
            model_id="gemini-3.6-flash",
            auth_mode=GoogleAuthMode.AI_STUDIO_KEY,
            project_id=None,
            location="us-central1",
        )
    )

    with pytest.raises(ValueError, match="google_credentials_missing"):
        factory.build()


def test_google_factory_decision_traverses_one_gateway_inside_spawned_child(
    tmp_path,
    monkeypatch,
) -> None:
    import humanwire.synthetic as synthetic_module

    request_marker = tmp_path / "adk-called"
    real_context = multiprocessing.get_context("spawn")
    context = _MockAdkSpawnContext(real_context, "success", request_marker)
    monkeypatch.setattr(synthetic_module.multiprocessing, "get_context", lambda method: context)
    factory = GoogleAdkPersonaDecisionEngineFactory(
        runtime=GoogleRuntimeConfig(
            model_id="gemini-3.6-flash",
            auth_mode=GoogleAuthMode.AI_STUDIO_KEY,
            project_id=None,
            location="us-central1",
        )
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
    assert not multiprocessing.active_children()
    assert not any(
        thread.name.startswith("humanwire-persona") for thread in threading.enumerate()
    )


def test_google_factory_hard_timeout_is_inert_and_reaps_spawned_work(
    tmp_path,
    monkeypatch,
) -> None:
    import humanwire.synthetic as synthetic_module

    request_marker = tmp_path / "adk-timeout-called"
    real_context = multiprocessing.get_context("spawn")
    context = _MockAdkSpawnContext(real_context, "timeout", request_marker)
    monkeypatch.setattr(synthetic_module.multiprocessing, "get_context", lambda method: context)
    monkeypatch.setattr(synthetic_module, "MODEL_DECISION_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(synthetic_module, "MODEL_DECISION_CANCELLATION_GRACE_SECONDS", 0.2)
    factory = GoogleAdkPersonaDecisionEngineFactory(
        runtime=GoogleRuntimeConfig(
            model_id="gemini-3.6-flash",
            auth_mode=GoogleAuthMode.AI_STUDIO_KEY,
            project_id=None,
            location="us-central1",
        )
    )

    started = time.monotonic()
    result = generate_scenario(
        one_person_generation_scenario(),
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        decision_engine=factory,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 9
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
