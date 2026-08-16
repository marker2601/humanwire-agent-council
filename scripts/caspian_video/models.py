from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class ProofClass(StrEnum):
    GENERATED_VISUAL = "generated_visual"
    RECORDED_CASPIAN = "recorded_caspian"
    PUBLIC_PRODUCT = "public_product"
    TITLE_CARD = "title_card"


class VideoSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,40}$")
    start_seconds: int = Field(ge=0, le=180)
    duration_seconds: int = Field(ge=1, le=60)
    source: str = Field(pattern=r"^work/caspian-video/approved/[a-z0-9_-]+\.mp4$")
    proof_class: ProofClass
    channel: Literal["telegram", "email"] | None = None
    disclosure: Literal["Visual guide"] | None = None
    required_copy: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_truth_boundary(self) -> VideoSegment:
        if self.proof_class is ProofClass.RECORDED_CASPIAN and self.channel is None:
            raise ValueError("recorded Caspian segments require a channel")
        if (
            self.proof_class is ProofClass.GENERATED_VISUAL
            and self.disclosure != "Visual guide"
        ):
            raise ValueError("generated visuals require the fixed disclosure")
        return self


class VideoManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: Literal["HumanWire â€” coordination that reaches a decision"]
    width: Literal[1920]
    height: Literal[1080]
    fps: Literal[30]
    segments: tuple[VideoSegment, ...]

    @property
    def total_duration_seconds(self) -> int:
        return sum(segment.duration_seconds for segment in self.segments)

    @model_validator(mode="after")
    def validate_timeline(self) -> VideoManifest:
        expected_start = 0
        for segment in self.segments:
            if segment.start_seconds != expected_start:
                raise ValueError("segments must be contiguous and ordered")
            expected_start += segment.duration_seconds
        if not 90 <= expected_start <= 110:
            raise ValueError("editorial duration must be 90â€“110 seconds")
        return self

    @classmethod
    def load(cls, path: Path) -> VideoManifest:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class SpendApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approved: Literal[True]
    ceiling_usd: Decimal

    @model_validator(mode="after")
    def validate_ceiling(self) -> SpendApproval:
        if self.ceiling_usd != Decimal("3.00"):
            raise ValueError("spend ceiling must equal the approved design ceiling")
        return self


class GenerationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["presenter", "stakeholders"]
    model: Literal["google/veo-3.1-fast", "bytedance/seedance-2.0-fast"]
    prompt: str = Field(min_length=40, max_length=2_000)
    duration: Literal[6, 8]
    resolution: Literal["720p"]
    aspect_ratio: Literal["16:9"]
    generate_audio: Literal[False]
    first_frame: Path | None = None

    @model_validator(mode="after")
    def validate_model_duration(self) -> GenerationSpec:
        expected = {
            "google/veo-3.1-fast": ("presenter", 6),
            "bytedance/seedance-2.0-fast": ("stakeholders", 8),
        }
        if (self.name, self.duration) != expected[self.model]:
            raise ValueError("generation name and duration must match the approved model")
        return self


class GenerationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["presenter", "stakeholders"]
    model: Literal["google/veo-3.1-fast", "bytedance/seedance-2.0-fast"]
    status: Literal["completed"]
    cost_usd: Decimal = Field(ge=0, le=3)
    output_path: Path


class VideoSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key: SecretStr
    presenter_model: Literal["google/veo-3.1-fast"]
    stakeholder_model: Literal["bytedance/seedance-2.0-fast"]
