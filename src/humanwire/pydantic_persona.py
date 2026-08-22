"""Typed PydanticAI boundary for fictional persona decisions."""

from __future__ import annotations

import time
from threading import Event

from pydantic import Field, SecretStr
from pydantic_ai import Agent, ModelSettings
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from humanwire.model_client import ModelFailure
from humanwire.persona_runtime import (
    PersonaContext,
    PersonaDecision,
    PersonaProfile,
    StrictPersonaModel,
    persona_prompt_payload,
    validate_persona_stage_decision,
)


class PydanticAIPersonaDecisionEngine:
    """Request one typed persona decision without tools or workflow dependencies."""

    def __init__(self, model: Model, model_identifier: str) -> None:
        self._model = model
        self.model_identifier = model_identifier

    def decide(
        self,
        profile: PersonaProfile,
        context: PersonaContext,
        *,
        deadline: float,
        cancellation: Event,
    ) -> PersonaDecision:
        remaining = deadline - time.monotonic()
        if cancellation.is_set() or remaining <= 0:
            raise ModelFailure("timeout")
        system, user = persona_prompt_payload(profile, context)
        agent = Agent(
            self._model,
            output_type=PersonaDecision,
            system_prompt=system,
            retries=0,
        )
        try:
            result = agent.run_sync(
                user,
                model_settings=ModelSettings(timeout=remaining, temperature=0),
            )
        except Exception as error:
            if cancellation.is_set() or time.monotonic() >= deadline:
                raise ModelFailure("timeout") from error
            raise ModelFailure("invalid_response") from error
        if cancellation.is_set() or time.monotonic() >= deadline:
            raise ModelFailure("timeout")
        return validate_persona_stage_decision(profile, context, result.output)


class PydanticAIPersonaDecisionEngineFactory(StrictPersonaModel):
    """Serializable private configuration for a child-owned PydanticAI engine."""

    api_key: SecretStr
    model_identifier: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=2048)

    def build(self) -> PydanticAIPersonaDecisionEngine:
        provider = OpenAIProvider(
            api_key=self.api_key.get_secret_value(),
            base_url=self.base_url,
        )
        model = OpenAIChatModel(self.model_identifier, provider=provider)
        return PydanticAIPersonaDecisionEngine(model, self.model_identifier)
