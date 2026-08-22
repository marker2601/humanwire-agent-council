"""Strict, secret-free configuration projected into the Google runtime."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_QUALIFYING_MODEL = re.compile(
    r"^gemini-(?P<major>[0-9]+)\.(?P<minor>[0-9]+)-[a-z0-9][a-z0-9.-]{0,127}$"
)
_SAFE_PROJECT = r"^[a-z][a-z0-9-]{4,62}$"
_SAFE_LOCATION = r"^[a-z][a-z0-9-]{1,62}$"


class GoogleAuthMode(StrEnum):
    VERTEX_AI_ADC = "vertex_ai_adc"
    AI_STUDIO_KEY = "ai_studio_key"


class GoogleRuntimeConfig(BaseModel):
    """Spawn-safe public configuration; credentials are intentionally absent."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    model_id: str = Field(min_length=1, max_length=160)
    auth_mode: GoogleAuthMode
    project_id: str | None = Field(default=None, pattern=_SAFE_PROJECT)
    location: str = Field(default="global", pattern=_SAFE_LOCATION)

    @model_validator(mode="after")
    def is_qualifying_and_internally_consistent(self) -> Self:
        match = _QUALIFYING_MODEL.fullmatch(self.model_id)
        if match is None or (int(match["major"]), int(match["minor"])) < (3, 5):
            raise ValueError("Google runtime requires a qualifying Gemini 3.5+ model")
        if self.auth_mode is GoogleAuthMode.VERTEX_AI_ADC and self.project_id is None:
            raise ValueError("Vertex AI runtime requires a Google Cloud project")
        if self.auth_mode is GoogleAuthMode.AI_STUDIO_KEY and self.project_id is not None:
            raise ValueError("AI Studio runtime must not retain a cloud project")
        return self
