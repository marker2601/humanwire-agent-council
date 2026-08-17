"""Google ADK/Gemini implementation of the HumanWire persona decision contract."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from collections.abc import Callable
from threading import Event
from typing import Any

from google.adk.agents import Agent
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import Field, ValidationError

from humanwire.google_agents import GoogleAdkCoordinator
from humanwire.google_config import GoogleAuthMode, GoogleRuntimeConfig
from humanwire.model_client import ModelFailure
from humanwire.persona_runtime import (
    PersonaContext,
    PersonaDecision,
    PersonaProfile,
    StrictPersonaModel,
    persona_prompt_payload,
    validate_persona_decision,
)

BeforeModelCallback = Callable[..., Any]


class GoogleAdkPersonaDecisionEngine:
    """Run one bounded specialist through the real ADK runner."""

    def __init__(
        self,
        *,
        model_identifier: str,
        max_output_tokens: int = 900,
        before_model_callback: BeforeModelCallback | None = None,
    ) -> None:
        GoogleRuntimeConfig(
            model_id=model_identifier,
            auth_mode=GoogleAuthMode.AI_STUDIO_KEY,
            project_id=None,
        )
        if not 1 <= max_output_tokens <= 4096:
            raise ValueError("Google output-token limit is invalid")
        self.model_identifier = model_identifier
        self._max_output_tokens = max_output_tokens
        self._before_model_callback = before_model_callback
        self._coordinator = GoogleAdkCoordinator()

    def decide(
        self,
        profile: PersonaProfile,
        context: PersonaContext,
        *,
        deadline: float,
        cancellation: Event,
    ) -> PersonaDecision:
        if cancellation.is_set() or deadline <= time.monotonic():
            raise ModelFailure("timeout")
        profile = PersonaProfile.model_validate(profile)
        context = PersonaContext.model_validate(context)
        specialist = self._coordinator.select(profile)
        system, user = persona_prompt_payload(profile, context)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ModelFailure("timeout")

        runner: InMemoryRunner | None = None
        events = []
        failure_reason: str | None = None
        try:
            agent = Agent(
                name=specialist.agent_name,
                description=specialist.description,
                model=self.model_identifier,
                instruction=f"{system} {specialist.instruction}",
                output_schema=PersonaDecision,
                mode="task",
                include_contents="none",
                disallow_transfer_to_parent=True,
                disallow_transfer_to_peers=True,
                timeout=remaining,
                generate_content_config=types.GenerateContentConfig(
                    temperature=0,
                    max_output_tokens=self._max_output_tokens,
                ),
                before_model_callback=self._before_model_callback,
            )
            runner = InMemoryRunner(agent=agent, app_name="humanwire_google_persona")
            session_id = f"persona-{secrets.token_hex(12)}"
            asyncio.run(
                runner.session_service.create_session(
                    app_name="humanwire_google_persona",
                    user_id="humanwire-persona",
                    session_id=session_id,
                )
            )
            events = list(
                runner.run(
                    user_id="humanwire-persona",
                    session_id=session_id,
                    new_message=types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(
                                text=(
                                    "UNTRUSTED_CONTENT_START\n"
                                    f"{user}\n"
                                    "UNTRUSTED_CONTENT_END"
                                )
                            )
                        ],
                    ),
                )
            )
        except Exception:  # noqa: BLE001 - provider details never cross this boundary
            failure_reason = "network_error"
        finally:
            if runner is not None:
                try:
                    asyncio.run(runner.close())
                except Exception:  # noqa: BLE001 - cleanup failure is fixed and safe
                    failure_reason = failure_reason or "network_error"

        if failure_reason is not None:
            raise ModelFailure(failure_reason)
        if cancellation.is_set() or deadline <= time.monotonic():
            raise ModelFailure("timeout")

        response_text: str | None = None
        for event in events:
            if event.error_code is not None:
                raise ModelFailure("invalid_response")
            if event.content is None:
                continue
            for part in event.content.parts or ():
                if part.text:
                    response_text = part.text
        if response_text is None:
            raise ModelFailure("invalid_response")

        invalid_schema = False
        decision: PersonaDecision | None = None
        try:
            decision = PersonaDecision.model_validate_json(response_text)
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError):
            invalid_schema = True
        if invalid_schema or decision is None:
            raise ModelFailure("invalid_schema")
        if cancellation.is_set() or deadline <= time.monotonic():
            raise ModelFailure("timeout")
        return validate_persona_decision(profile, decision)


class GoogleAdkPersonaDecisionEngineFactory(StrictPersonaModel):
    """Spawn-safe Google runtime description; credentials remain ambient."""

    runtime: GoogleRuntimeConfig
    max_output_tokens: int = Field(default=900, ge=1, le=4096)

    @property
    def model_identifier(self) -> str:
        return self.runtime.model_id

    def build(self) -> GoogleAdkPersonaDecisionEngine:
        if self.runtime.auth_mode is GoogleAuthMode.VERTEX_AI_ADC:
            assert self.runtime.project_id is not None
            os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
            os.environ["GOOGLE_CLOUD_PROJECT"] = self.runtime.project_id
            os.environ["GOOGLE_CLOUD_LOCATION"] = self.runtime.location
        else:
            key = os.environ.get("GEMINI_API_KEY", "")
            if not key.strip():
                raise ValueError("google_credentials_missing")
            os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "false"
        return GoogleAdkPersonaDecisionEngine(
            model_identifier=self.runtime.model_id,
            max_output_tokens=self.max_output_tokens,
        )
