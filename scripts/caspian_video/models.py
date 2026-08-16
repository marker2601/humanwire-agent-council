from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)


def _validate_work_media_path(
    path: Path, *, location: str, suffixes: frozenset[str]
) -> Path:
    required_prefix = ("work", "caspian-video", location)
    if (
        path.is_absolute()
        or path.drive
        or ".." in path.parts
        or path.parts[:3] != required_prefix
        or path.suffix.lower() not in suffixes
    ):
        raise ValueError(
            f"path must be relative and under work/caspian-video/{location}/"
        )
    return path


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

    title: Literal["HumanWire — coordination that reaches a decision"]
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
            raise ValueError("editorial duration must be 90–110 seconds")
        return self

    @classmethod
    def load(cls, path: Path) -> VideoManifest:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


def validate_approved_visual_durations(
    manifest: VideoManifest, durations: Mapping[str, object]
) -> None:
    """Require every generated visual to cover its complete editorial segment."""
    for segment in manifest.segments:
        if segment.proof_class is not ProofClass.GENERATED_VISUAL:
            continue
        raw_duration = durations.get(segment.source)
        try:
            duration = Decimal(str(raw_duration))
        except (InvalidOperation, ValueError):
            duration = Decimal("NaN")
        if not duration.is_finite() or duration < segment.duration_seconds:
            raise ValueError("approved visual duration is shorter than its segment")


class SpendApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approved: Literal[True]
    ceiling_usd: Decimal

    @field_validator("approved", mode="before")
    @classmethod
    def validate_explicit_approval(cls, value: object) -> bool:
        if type(value) is not bool or value is not True:
            raise ValueError("spend approval must be the explicit boolean True")
        return value

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

    @field_validator("first_frame")
    @classmethod
    def validate_first_frame(cls, path: Path | None) -> Path | None:
        if path is None:
            return None
        return _validate_work_media_path(
            path,
            location="references",
            suffixes=frozenset({".png", ".jpg", ".jpeg"}),
        )

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

    @field_validator("output_path")
    @classmethod
    def validate_output_path(cls, path: Path) -> Path:
        return _validate_work_media_path(
            path,
            location="generated",
            suffixes=frozenset({".mp4"}),
        )


class VideoSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key: SecretStr
    presenter_model: Literal["google/veo-3.1-fast"]
    stakeholder_model: Literal["bytedance/seedance-2.0-fast"]
