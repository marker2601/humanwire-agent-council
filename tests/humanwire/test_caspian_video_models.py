from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from scripts.caspian_video import models as video_models
from scripts.caspian_video.models import (
    GenerationReceipt,
    GenerationSpec,
    ProofClass,
    SpendApproval,
    VideoManifest,
)

ROOT = Path(__file__).resolve().parents[2]


def test_submission_video_manifest_is_truthful_and_105_seconds() -> None:
    manifest = VideoManifest.load(ROOT / "submission/caspian-video-manifest.json")

    assert manifest.total_duration_seconds == 105
    assert 90 <= manifest.total_duration_seconds <= 110
    assert [segment.id for segment in manifest.segments] == [
        "presenter_hook",
        "telegram_authorization",
        "email_evidence",
        "stakeholder_roles",
        "decision_room",
        "replay_and_downloads",
        "closing_card",
    ]
    assert {
        segment.channel
        for segment in manifest.segments
        if segment.proof_class is ProofClass.RECORDED_CASPIAN
    } == {"telegram", "email"}
    assert all(
        segment.disclosure == "Visual guide"
        for segment in manifest.segments
        if segment.proof_class is ProofClass.GENERATED_VISUAL
    )
    assert any(
        "Standard agents · no external messages" in segment.required_copy
        for segment in manifest.segments
        if segment.proof_class is ProofClass.PUBLIC_PRODUCT
    )

    assert [
        (segment.id, segment.start_seconds, segment.duration_seconds)
        for segment in manifest.segments[:2]
    ] == [
        ("presenter_hook", 0, 8),
        ("telegram_authorization", 8, 14),
    ]
    assert [
        (segment.id, segment.start_seconds, segment.duration_seconds)
        for segment in manifest.segments[3:5]
    ] == [
        ("stakeholder_roles", 38, 8),
        ("decision_room", 46, 32),
    ]


def test_offline_narration_fallback_requires_user_authorization() -> None:
    plan = (
        ROOT
        / "docs/superpowers/plans/2026-08-15-humanwire-caspian-submission-video.md"
    ).read_text(encoding="utf-8")

    assert (
        "After explicit user authorization, use either human-recorded narration or "
        "offline local speech synthesis; make no paid voice request and no additional "
        "provider call."
    ) in plan
    assert (
        "Standing user authorization permits necessary production and submission spend "
        "up to USD $10.00 total without another approval request."
    ) in plan


def test_approved_visuals_must_cover_their_manifest_segments() -> None:
    manifest = VideoManifest.load(ROOT / "submission/caspian-video-manifest.json")

    with pytest.raises(ValueError, match="approved visual duration"):
        video_models.validate_approved_visual_durations(
            manifest,
            {
                "work/caspian-video/approved/presenter.mp4": 6,
                "work/caspian-video/approved/stakeholders.mp4": 8,
            },
        )

    video_models.validate_approved_visual_durations(
        manifest,
        {
            "work/caspian-video/approved/presenter.mp4": 8,
            "work/caspian-video/approved/stakeholders.mp4": 8,
        },
    )


def test_editorial_unicode_is_exact_utf8_not_mojibake() -> None:
    expected_title = "HumanWire — coordination that reaches a decision"
    editorial_files = (
        ROOT / "scripts/caspian_video/models.py",
        ROOT / "submission/caspian-video-manifest.json",
        ROOT / "submission/caspian-video-script.md",
        Path(__file__),
    )
    editorial_text = "\n".join(
        path.read_text(encoding="utf-8") for path in editorial_files
    )

    assert get_args(VideoManifest.model_fields["title"].annotation) == (
        expected_title,
    )
    assert VideoManifest.load(
        ROOT / "submission/caspian-video-manifest.json"
    ).title == expected_title
    assert "## 0–8 seconds" in editorial_text
    assert "Standard agents · no external messages" in editorial_text
    assert not any(
        chr(codepoint) in editorial_text for codepoint in (0x00C3, 0x00C2, 0x00E2)
    )


@pytest.mark.parametrize("approved", [False, None, 0, 1, "false", "true"])
def test_spend_approval_must_be_explicit(approved: object) -> None:
    with pytest.raises(ValidationError):
        SpendApproval(approved=approved, ceiling_usd="3.00")


def test_spend_approval_rejects_more_than_design_ceiling() -> None:
    with pytest.raises(ValidationError):
        SpendApproval(approved=True, ceiling_usd="3.01")


def test_generation_paths_allow_only_their_safe_work_locations() -> None:
    spec = GenerationSpec(
        name="stakeholders",
        model="bytedance/seedance-2.0-fast",
        prompt="Illustrated software-agent stakeholders in a dark enterprise studio.",
        duration=8,
        resolution="720p",
        aspect_ratio="16:9",
        generate_audio=False,
        first_frame=Path("work/caspian-video/references/stakeholders.png"),
    )
    receipt = GenerationReceipt(
        name="presenter",
        model="google/veo-3.1-fast",
        status="completed",
        cost_usd="0.48",
        output_path=Path("work/caspian-video/generated/presenter.mp4"),
    )

    assert spec.first_frame == Path("work/caspian-video/references/stakeholders.png")
    assert receipt.output_path == Path("work/caspian-video/generated/presenter.mp4")


@pytest.mark.parametrize(
    "first_frame",
    [
        "../private.png",
        "C:/Users/private.png",
        "work/caspian-video/../private.png",
        "work/caspian-video/generated/stakeholders.png",
    ],
)
def test_generation_spec_rejects_unsafe_first_frame_paths(first_frame: str) -> None:
    with pytest.raises(ValidationError):
        GenerationSpec(
            name="stakeholders",
            model="bytedance/seedance-2.0-fast",
            prompt="Illustrated software-agent stakeholders in a dark enterprise studio.",
            duration=8,
            resolution="720p",
            aspect_ratio="16:9",
            generate_audio=False,
            first_frame=Path(first_frame),
        )


@pytest.mark.parametrize(
    "output_path",
    [
        "../private.mp4",
        "C:/Users/private.mp4",
        "work/caspian-video/../../private.mp4",
        "work/caspian-video/approved/presenter.mp4",
    ],
)
def test_generation_receipt_rejects_unsafe_output_paths(output_path: str) -> None:
    with pytest.raises(ValidationError):
        GenerationReceipt(
            name="presenter",
            model="google/veo-3.1-fast",
            status="completed",
            cost_usd="0.48",
            output_path=Path(output_path),
        )
