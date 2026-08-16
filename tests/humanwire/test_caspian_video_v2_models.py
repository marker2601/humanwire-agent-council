from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.caspian_video_v2.models import (
    NarrationSpec,
    ProductionManifest,
    ProductionSegment,
    SpendAuthorization,
    VideoJobSpec,
    safe_work_path,
)


def test_budget_counts_prior_exposure_before_new_reservations() -> None:
    """Break caught: a fresh v2 ledger ignores the ambiguous prior provider exposure."""
    authorization = SpendAuthorization(
        cap_usd=Decimal("10.00"),
        prior_exposure_usd=Decimal("1.00"),
    )

    assert authorization.can_reserve(
        Decimal("8.99"), already_reserved=Decimal("0.01")
    )
    assert not authorization.can_reserve(
        Decimal("9.01"), already_reserved=Decimal("0.00")
    )


@pytest.mark.parametrize(
    "path",
    [
        Path("../private.mp4"),
        Path("C:/Users/private.mp4"),
        Path("work/caspian-video-v2/../private.mp4"),
        Path("work/caspian-video/generated/private.mp4"),
    ],
)
def test_safe_work_path_rejects_escape_and_cross_project_paths(path: Path) -> None:
    """Break caught: provider output can escape the ignored v2 media root."""
    with pytest.raises(ValueError, match="professional video work root"):
        safe_work_path(path, location="generated", suffixes=frozenset({".mp4"}))


def test_video_job_model_name_duration_and_cost_are_bound_together() -> None:
    """Break caught: a cheaper reservation is paired with a more expensive provider job."""
    guide = VideoJobSpec(
        name="visual_guide_v2",
        model="kwaivgi/kling-v3.0-std",
        duration_seconds=6,
        resolution="720p",
        aspect_ratio="16:9",
        generate_audio=False,
        prompt=(
            "Six-second restrained cinematic shot of one fictional visual guide "
            "in a premium navy enterprise studio, no text, logos, UI, or extra people."
        ),
        first_frame=Path("work/caspian-video-v2/references/visual-guide.png"),
        output_path=Path("work/caspian-video-v2/generated/visual-guide.mp4"),
        reserved_usd=Decimal("0.51"),
    )

    assert guide.reserved_usd == Decimal("0.51")
    with pytest.raises(ValidationError, match="approved model and reservation"):
        VideoJobSpec(
            **{
                **guide.model_dump(),
                "model": "bytedance/seedance-2.0",
            }
        )


def test_seedance_replacement_has_a_separate_conservative_reservation() -> None:
    """Break caught: a failed completed-job receipt blocks the one bounded replacement."""
    replacement = VideoJobSpec(
        name="agent_flow_v2_retry",
        model="bytedance/seedance-2.0",
        duration_seconds=6,
        resolution="720p",
        aspect_ratio="16:9",
        generate_audio=False,
        prompt=(
            "Six-second premium motion-graphics sequence with seven fictional "
            "enterprise agents, one cyan coordination path, no text or channel UI."
        ),
        first_frame=Path("work/caspian-video-v2/references/agent-flow.png"),
        output_path=Path("work/caspian-video-v2/generated/agent-flow.mp4"),
        reserved_usd=Decimal("1.50"),
    )

    assert replacement.reserved_usd == Decimal("1.50")


def test_narration_is_bound_to_professional_models_and_safe_output() -> None:
    """Break caught: the rejected desktop voice or an arbitrary output path returns."""
    narration = NarrationSpec(
        model="google/gemini-3.1-flash-tts-preview",
        fallback_model="minimax/speech-2.8-hd",
        input_text=(
            "HumanWire turns one mandate into the right conversations and a "
            "decision-ready meeting built on confirmed evidence."
        ),
        voice="professional_female",
        output_path=Path("work/caspian-video-v2/generated/narration.mp3"),
        reserved_usd=Decimal("0.50"),
    )

    assert narration.output_path == Path(
        "work/caspian-video-v2/generated/narration.mp3"
    )
    with pytest.raises(ValidationError):
        NarrationSpec(
            **{
                **narration.model_dump(),
                "output_path": Path("work/caspian-video/generated/narration.mp3"),
            }
        )


def test_manifest_requires_contiguous_eighty_second_product_dominant_story() -> None:
    """Break caught: editorial changes shrink authentic product evidence below 70%."""
    segments = (
        ProductionSegment(
            id="hook",
            start_seconds=0,
            duration_seconds=6,
            source_kind="generated_visual",
        ),
        ProductionSegment(
            id="product",
            start_seconds=6,
            duration_seconds=69,
            source_kind="public_product",
        ),
        ProductionSegment(
            id="closing",
            start_seconds=75,
            duration_seconds=5,
            source_kind="title_card",
        ),
    )

    manifest = ProductionManifest(segments=segments)

    assert manifest.duration_seconds == 80
    assert manifest.product_seconds == 69
    with pytest.raises(ValidationError, match="product footage"):
        ProductionManifest(
            segments=(
                segments[0],
                ProductionSegment(
                    id="product",
                    start_seconds=6,
                    duration_seconds=50,
                    source_kind="public_product",
                ),
                ProductionSegment(
                    id="closing",
                    start_seconds=56,
                    duration_seconds=24,
                    source_kind="title_card",
                ),
            )
        )
