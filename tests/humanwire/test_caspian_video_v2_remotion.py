from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REMOTION = ROOT / "scripts/caspian_video_v2/remotion"


def test_composition_is_exact_product_first_and_truthful() -> None:
    """Break caught: the edit drifts back toward generated cards and hidden product."""
    root = (REMOTION / "src/Root.tsx").read_text(encoding="utf-8")
    video = (REMOTION / "src/HumanWireVideo.tsx").read_text(encoding="utf-8")

    assert 'id="HumanWireProfessional"' in root
    assert "durationInFrames={2400}" in root
    assert "fps={30}" in root
    assert "width={1920}" in root
    assert "height={1080}" in root
    assert video.count("<ProductStage") >= 6
    assert "public-product-clean.webm" in video
    assert "Standard agents · no external messages" in video
    assert "live Telegram" not in video
    assert "live email" not in video


def test_generated_people_and_agents_are_visibly_labeled() -> None:
    """Break caught: illustrative AI footage is presented as recorded product proof."""
    video = (REMOTION / "src/HumanWireVideo.tsx").read_text(encoding="utf-8")
    overlay = (REMOTION / "src/components/AgentOverlay.tsx").read_text(
        encoding="utf-8"
    )

    assert "Fictional visual guide" in video
    assert "Software agents · illustrative" in overlay
    assert "agent-flow.mp4" in video


def test_product_stage_keeps_authentic_pixels_dominant() -> None:
    """Break caught: product video is shrunk into an unreadable decorative window."""
    stage = (REMOTION / "src/components/ProductStage.tsx").read_text(
        encoding="utf-8"
    )

    assert "minWidth: 1382" in stage
    assert "minHeight: 778" in stage
    assert "OffthreadVideo" in stage
    assert "TruthBadge" in stage


def test_product_stage_never_fades_the_recorded_product_to_black() -> None:
    """Break caught: exact chapter cuts briefly render an empty black frame."""
    stage = (REMOTION / "src/components/ProductStage.tsx").read_text(
        encoding="utf-8"
    )

    assert "opacity: fade" not in stage


def test_caption_layer_uses_authored_short_cues_without_covering_the_ui() -> None:
    """Break caught: subtitles wrap into columns or mask judge-critical controls."""
    layer = (REMOTION / "src/components/CaptionLayer.tsx").read_text(
        encoding="utf-8"
    )

    assert "maxWidth: 760" in layer
    assert "fontSize: 32" in layer
    assert "lineHeight: 1.25" in layer
    assert "pointerEvents: \"none\"" in layer
