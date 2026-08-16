from __future__ import annotations

from dataclasses import replace

import pytest

from scripts.caspian_video_v2.verify import (
    FrameContract,
    MasterProbe,
    VideoVerificationError,
    verify_frame_contract,
    verify_probe,
)


def valid_probe() -> MasterProbe:
    return MasterProbe(
        duration_seconds=80.021333,
        atoms=("ftyp", "moov", "mdat"),
        width=1920,
        height=1080,
        video_codec="h264",
        pixel_format="yuv420p",
        frame_rate=30.0,
        audio_codec="aac",
        sample_rate=48_000,
        channels=2,
        decoded_frames=2_400,
    )


def test_valid_master_probe_is_accepted() -> None:
    assert verify_probe(valid_probe()) == valid_probe()


@pytest.mark.parametrize(
    "probe",
    [
        replace(valid_probe(), duration_seconds=77.9),
        replace(valid_probe(), atoms=("ftyp", "mdat", "moov")),
        replace(valid_probe(), pixel_format="yuvj420p"),
        replace(valid_probe(), decoded_frames=2_399),
    ],
)
def test_wrong_master_properties_are_rejected(probe: MasterProbe) -> None:
    with pytest.raises(VideoVerificationError):
        verify_probe(probe)


def test_product_and_truth_samples_are_required() -> None:
    assert verify_frame_contract(
        FrameContract(product_ratio=0.825, truth_badge_ratio=1.0)
    )
    with pytest.raises(VideoVerificationError):
        verify_frame_contract(
            FrameContract(product_ratio=0.79, truth_badge_ratio=1.0)
        )
    with pytest.raises(VideoVerificationError):
        verify_frame_contract(
            FrameContract(product_ratio=0.825, truth_badge_ratio=0.99)
        )
