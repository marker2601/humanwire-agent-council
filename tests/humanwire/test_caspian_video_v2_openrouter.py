from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from scripts.caspian_video_v2.models import (
    NarrationSpec,
    SpendAuthorization,
    VideoJobSpec,
)
from scripts.caspian_video_v2.openrouter import (
    MediaGenerationError,
    ProfessionalMediaClient,
)


def _guide_spec() -> VideoJobSpec:
    return VideoJobSpec(
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


def _narration_spec() -> NarrationSpec:
    return NarrationSpec(
        model="google/gemini-3.1-flash-tts-preview",
        fallback_model="minimax/speech-2.8-hd",
        input_text=(
            "HumanWire turns one mandate into the right conversations. It saves "
            "conflict, confirmed evidence, revision, approval, availability, and "
            "a decision-ready meeting in one inspectable workflow."
        ),
        voice="professional_female",
        output_path=Path("work/caspian-video-v2/generated/narration.mp3"),
        reserved_usd=Decimal("0.50"),
    )


def _catalog_payload() -> dict[str, object]:
    return {
        "data": [
            {
                "id": "kwaivgi/kling-v3.0-std",
                "supported_durations": [3, 4, 5, 6],
                "supported_resolutions": ["720p"],
                "supported_aspect_ratios": ["16:9"],
                "supported_frame_images": ["first_frame", "last_frame"],
            },
            {
                "id": "bytedance/seedance-2.0",
                "supported_durations": [4, 5, 6],
                "supported_resolutions": ["720p", "1080p"],
                "supported_aspect_ratios": ["16:9"],
                "supported_frame_images": ["first_frame", "last_frame"],
            },
        ]
    }


def test_paid_video_post_follows_preflight_and_atomic_reservation(tmp_path: Path) -> None:
    """Break caught: the paid provider is called before capability or ledger fencing."""
    reference = tmp_path / "work/caspian-video-v2/references/visual-guide.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"safe-reference")
    ledger = tmp_path / "work/caspian-video-v2/openrouter/jobs.json"
    observed: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append((request.method, request.url.path))
        if request.url.path == "/api/v1/videos/models":
            return httpx.Response(200, json=_catalog_payload())
        if request.url.path == "/api/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "google/gemini-3.1-flash-tts-preview"},
                        {"id": "minimax/speech-2.8-hd"},
                    ]
                },
            )
        if request.url.path == "/api/v1/credits":
            return httpx.Response(
                200,
                json={"data": {"total_credits": 20, "total_usage": 0}},
            )
        if request.method == "POST" and request.url.path == "/api/v1/videos":
            assert json.loads(ledger.read_text())["visual_guide_v2"]["status"] == (
                "reserved"
            )
            body = json.loads(request.content)
            assert body["generate_audio"] is False
            assert body["model"] == "kwaivgi/kling-v3.0-std"
            assert body["frame_images"][0]["frame_type"] == "first_frame"
            return httpx.Response(
                202,
                json={
                    "id": "guide-job",
                    "status": "pending",
                    "polling_url": "https://openrouter.ai/api/v1/videos/guide-job",
                },
            )
        if request.url.path == "/api/v1/videos/guide-job":
            return httpx.Response(
                200,
                json={
                    "id": "guide-job",
                    "status": "completed",
                },
            )
        if request.url.path == "/api/v1/videos/guide-job/content":
            return httpx.Response(
                200,
                headers={"content-type": "video/mp4"},
                content=b"safe-mp4",
            )
        raise AssertionError(f"unexpected route: {request.method} {request.url}")

    client = ProfessionalMediaClient(
        api_key=SecretStr("PRIVATE-SENTINEL"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
        probe=lambda _path, _kind: None,
    )
    preflight = client.preflight((_guide_spec(),), _narration_spec())
    receipt = client.generate_video(
        _guide_spec(),
        authorization=SpendAuthorization(),
        repository_root=tmp_path,
    )

    assert preflight.credential_valid is True
    assert preflight.video_models_ready is True
    assert preflight.narration_models_ready is True
    assert receipt.output_path == Path(
        "work/caspian-video-v2/generated/visual-guide.mp4"
    )
    assert (tmp_path / receipt.output_path).read_bytes() == b"safe-mp4"
    assert json.loads(ledger.read_text())["visual_guide_v2"] == {
        "cost_usd": "0.51",
        "model": "kwaivgi/kling-v3.0-std",
        "reserved_usd": "0.51",
        "status": "completed",
    }
    assert observed[:4] == [
        ("GET", "/api/v1/videos/models"),
        ("GET", "/api/v1/models"),
        ("GET", "/api/v1/credits"),
        ("POST", "/api/v1/videos"),
    ]


def test_narration_post_is_reserved_and_written_under_safe_root(tmp_path: Path) -> None:
    """Break caught: professional narration bypasses the shared cumulative ledger."""
    ledger = tmp_path / "work/caspian-video-v2/openrouter/jobs.json"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/videos/models":
            return httpx.Response(200, json=_catalog_payload())
        if request.url.path == "/api/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "google/gemini-3.1-flash-tts-preview"},
                        {"id": "minimax/speech-2.8-hd"},
                    ]
                },
            )
        if request.url.path == "/api/v1/credits":
            return httpx.Response(
                200,
                json={"data": {"total_credits": 20, "total_usage": 0}},
            )
        if request.method == "POST" and request.url.path == "/api/v1/audio/speech":
            assert json.loads(ledger.read_text())["narration_v2"]["status"] == (
                "reserved"
            )
            body = json.loads(request.content)
            assert body["voice"] == "alloy"
            return httpx.Response(
                200,
                headers={"content-type": "audio/mpeg"},
                content=b"safe-mp3",
            )
        raise AssertionError(f"unexpected route: {request.method} {request.url}")

    client = ProfessionalMediaClient(
        api_key=SecretStr("PRIVATE-SENTINEL"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        probe=lambda _path, _kind: None,
    )
    client.preflight((_guide_spec(),), _narration_spec())
    receipt = client.generate_narration(
        _narration_spec(),
        authorization=SpendAuthorization(),
        repository_root=tmp_path,
    )

    assert (tmp_path / receipt.output_path).read_bytes() == b"safe-mp3"
    assert json.loads(ledger.read_text())["narration_v2"]["status"] == "completed"


def test_provider_failure_is_fixed_and_drops_authenticated_exception_graph(
    tmp_path: Path,
) -> None:
    """Break caught: an httpx exception retains the bearer header or private path."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/videos/models":
            return httpx.Response(200, json=_catalog_payload())
        if request.url.path == "/api/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "google/gemini-3.1-flash-tts-preview"},
                        {"id": "minimax/speech-2.8-hd"},
                    ]
                },
            )
        if request.url.path == "/api/v1/credits":
            return httpx.Response(
                200,
                json={"data": {"total_credits": 20, "total_usage": 0}},
            )
        raise httpx.ConnectError(
            "PRIVATE-SENTINEL C:/private/provider-body",
            request=request,
        )

    reference = tmp_path / "work/caspian-video-v2/references/visual-guide.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"safe-reference")
    client = ProfessionalMediaClient(
        api_key=SecretStr("PRIVATE-SENTINEL"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        probe=lambda _path, _kind: None,
    )
    client.preflight((_guide_spec(),), _narration_spec())

    with pytest.raises(MediaGenerationError) as raised:
        client.generate_video(
            _guide_spec(),
            authorization=SpendAuthorization(),
            repository_root=tmp_path,
        )

    assert str(raised.value) == "Media generation failed"
    assert "PRIVATE-SENTINEL" not in repr(raised.value)
    assert "C:/private" not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
