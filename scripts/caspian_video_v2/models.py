from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def safe_work_path(
    path: Path,
    *,
    location: Literal["references", "generated", "final", "review"],
    suffixes: frozenset[str],
) -> Path:
    """Confine media to the repository-owned ignored v2 production root."""
    required_prefix = ("work", "caspian-video-v2", location)
    if (
        path.is_absolute()
        or path.drive
        or ".." in path.parts
        or path.parts[:3] != required_prefix
        or path.suffix.lower() not in suffixes
    ):
        raise ValueError(
            f"path must be relative and under the professional video work root/{location}/"
        )
    return path


class SpendAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    cap_usd: Decimal = Decimal("10.00")
    prior_exposure_usd: Decimal = Decimal("1.00")

    @model_validator(mode="after")
    def validate_bounds(self) -> SpendAuthorization:
        if (
            self.cap_usd != Decimal("10.00")
            or self.prior_exposure_usd != Decimal("1.00")
        ):
            raise ValueError("spend authorization must match the standing user cap")
        return self

    def can_reserve(self, amount: Decimal, *, already_reserved: Decimal) -> bool:
        total = self.prior_exposure_usd + already_reserved + amount
        return amount > 0 and already_reserved >= 0 and total <= self.cap_usd


class VideoJobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: Literal["visual_guide_v2", "agent_flow_v2"]
    model: Literal["kwaivgi/kling-v3.0-std", "bytedance/seedance-2.0"]
    duration_seconds: Literal[6]
    resolution: Literal["720p"]
    aspect_ratio: Literal["16:9"]
    generate_audio: Literal[False]
    prompt: str = Field(min_length=60, max_length=2_000)
    first_frame: Path
    output_path: Path
    reserved_usd: Decimal

    @field_validator("first_frame")
    @classmethod
    def validate_first_frame(cls, path: Path) -> Path:
        return safe_work_path(
            path,
            location="references",
            suffixes=frozenset({".png", ".jpg", ".jpeg"}),
        )

    @field_validator("output_path")
    @classmethod
    def validate_output_path(cls, path: Path) -> Path:
        return safe_work_path(
            path,
            location="generated",
            suffixes=frozenset({".mp4"}),
        )

    @model_validator(mode="after")
    def validate_model_binding(self) -> VideoJobSpec:
        expected = {
            "kwaivgi/kling-v3.0-std": (
                "visual_guide_v2",
                Decimal("0.51"),
            ),
            "bytedance/seedance-2.0": (
                "agent_flow_v2",
                Decimal("0.91"),
            ),
        }
        if (self.name, self.reserved_usd) != expected[self.model]:
            raise ValueError("name must match the approved model and reservation")
        return self


class NarrationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    model: Literal["google/gemini-3.1-flash-tts-preview"]
    fallback_model: Literal["minimax/speech-2.8-hd"]
    input_text: str = Field(min_length=80, max_length=2_000)
    voice: Literal["professional_female"]
    output_path: Path
    reserved_usd: Literal[Decimal("0.50")]

    @field_validator("output_path")
    @classmethod
    def validate_output_path(cls, path: Path) -> Path:
        return safe_work_path(
            path,
            location="generated",
            suffixes=frozenset({".mp3", ".wav"}),
        )


class ProductionSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,40}$")
    start_seconds: int = Field(ge=0, le=80)
    duration_seconds: int = Field(ge=1, le=80)
    source_kind: Literal["generated_visual", "public_product", "title_card"]


class ProductionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    width: Literal[1920] = 1920
    height: Literal[1080] = 1080
    fps: Literal[30] = 30
    segments: tuple[ProductionSegment, ...]

    @property
    def duration_seconds(self) -> int:
        return sum(segment.duration_seconds for segment in self.segments)

    @property
    def product_seconds(self) -> int:
        return sum(
            segment.duration_seconds
            for segment in self.segments
            if segment.source_kind == "public_product"
        )

    @model_validator(mode="after")
    def validate_story(self) -> ProductionManifest:
        expected_start = 0
        for segment in self.segments:
            if segment.start_seconds != expected_start:
                raise ValueError("segments must be contiguous and ordered")
            expected_start += segment.duration_seconds
        if expected_start != 80:
            raise ValueError("professional video must be exactly 80 seconds")
        if self.product_seconds < 56:
            raise ValueError("product footage must occupy at least 70 percent")
        return self
