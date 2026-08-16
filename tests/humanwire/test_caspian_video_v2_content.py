from __future__ import annotations

import re
from pathlib import Path

from scripts.caspian_video_v2.models import ProductionManifest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "submission/caspian-video-v2-manifest.json"
SCRIPT = ROOT / "submission/caspian-video-v2-script.md"
CAPTIONS = ROOT / "submission/caspian-video-v2-captions.srt"
SRT_BLOCK = re.compile(
    r"(?ms)^(\d+)\n"
    r"(\d\d:\d\d:\d\d,\d{3}) --> (\d\d:\d\d:\d\d,\d{3})\n"
    r"(.+?)(?=\n\n|\Z)"
)


def test_timeline_is_exactly_eighty_seconds_and_product_dominant() -> None:
    """Break caught: the story drifts back toward a long generated slideshow."""
    manifest = ProductionManifest.model_validate_json(
        MANIFEST.read_text(encoding="utf-8")
    )

    assert [
        (segment.id, segment.start_seconds, segment.duration_seconds)
        for segment in manifest.segments
    ] == [
        ("hook", 0, 6),
        ("request", 6, 8),
        ("minimum_path", 14, 12),
        ("conflict_to_revision", 26, 18),
        ("approval_to_meeting", 44, 12),
        ("replay_exports", 56, 12),
        ("gateway_truth", 68, 7),
        ("closing", 75, 5),
    ]
    assert manifest.duration_seconds == 80
    assert manifest.product_seconds == 69


def test_narration_is_concise_complete_and_truthful() -> None:
    """Break caught: essential workflow logic is omitted or external proof is invented."""
    narration = "\n".join(
        line.strip()
        for line in SCRIPT.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    )
    words = re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?", narration)

    assert 175 <= len(words) <= 195
    for phrase in (
        "minimum necessary stakeholder path",
        "focused interview",
        "shareable evidence",
        "revises the proposal",
        "approves the revised plan",
        "provide availability",
        "meeting-ready result",
        "matching JSON and CSV",
        "configurable, consented-delivery boundary",
        "Standard agents",
        "no external messages",
    ):
        assert phrase in narration
    assert "live Telegram" not in narration
    assert "live email" not in narration


def test_captions_are_readable_timed_and_preserve_the_truth_boundary() -> None:
    """Break caught: captions wrap into unreadable columns or overclaim live delivery."""
    text = CAPTIONS.read_text(encoding="utf-8").replace("\r\n", "\n")
    blocks = SRT_BLOCK.findall(text)

    assert len(blocks) == 16
    assert [int(number) for number, *_rest in blocks] == list(range(1, 17))
    assert blocks[0][1] == "00:00:00,000"
    assert blocks[-1][2] == "00:01:20,000"
    for _number, _start, _end, copy in blocks:
        lines = copy.splitlines()
        assert 1 <= len(lines) <= 2
        assert all(1 <= len(line) <= 42 for line in lines)

    assert "Standard agents · no external messages" in text
    assert "live Telegram" not in text
    assert "live email" not in text
