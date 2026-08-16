from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.caspian_video.models import ProofClass, SpendApproval, VideoManifest

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
        "Standard agents Â· no external messages" in segment.required_copy
        for segment in manifest.segments
        if segment.proof_class is ProofClass.PUBLIC_PRODUCT
    )


@pytest.mark.parametrize("approved", [False, None])
def test_spend_approval_must_be_explicit(approved: bool | None) -> None:
    with pytest.raises(ValidationError):
        SpendApproval(approved=approved, ceiling_usd="3.00")


def test_spend_approval_rejects_more_than_design_ceiling() -> None:
    with pytest.raises(ValidationError):
        SpendApproval(approved=True, ceiling_usd="3.01")
