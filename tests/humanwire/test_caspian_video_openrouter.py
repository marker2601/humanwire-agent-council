import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from scripts.caspian_video import openrouter
from scripts.caspian_video.__main__ import main
from scripts.caspian_video.models import (
    GenerationReceipt,
    GenerationSpec,
    SpendApproval,
    VideoManifest,
    VideoSettings,
)
from scripts.caspian_video.openrouter import (
    OpenRouterMediaClient,
    VideoGenerationError,
    load_video_settings,
    synthesize_narration_sections,
)


def approved() -> SpendApproval:
    return SpendApproval(approved=True, ceiling_usd=Decimal("3.00"))


def presenter() -> GenerationSpec:
    return GenerationSpec(
        name="presenter",
        model="google/veo-3.1-fast",
        prompt="Fictional professional visual guide in a dark enterprise studio, subtle push-in",
        duration=6,
        resolution="720p",
        aspect_ratio="16:9",
        generate_audio=False,
    )


def test_submit_poll_and_download_use_exact_openrouter_routes(tmp_path: Path) -> None:
    """Break caught: a paid video job uses a wrong route or enables audio."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(202, json={"id": "job-safe", "status": "pending"})
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"safe-mp4", headers={"content-type": "video/mp4"})
        return httpx.Response(
            200,
            json={
                "id": "job-safe",
                "status": "completed",
                "usage": {"cost": 0.48, "is_byok": False},
            },
        )

    client = OpenRouterMediaClient(
        api_key=SecretStr("PRIVATE-OPENROUTER-SENTINEL"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
    )
    output = tmp_path / "presenter.mp4"
    receipt = client.generate_video(presenter(), approved(), output)

    assert output.read_bytes() == b"safe-mp4"
    assert receipt.cost_usd == Decimal("0.48")
    assert [request.url.path for request in calls] == [
        "/api/v1/videos",
        "/api/v1/videos/job-safe",
        "/api/v1/videos/job-safe/content",
    ]
    assert json.loads(calls[0].content)["generate_audio"] is False


def test_generation_failure_never_retains_secret() -> None:
    """Break caught: a provider error leaks a credential through the public exception."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="PRIVATE-OPENROUTER-SENTINEL")

    client = OpenRouterMediaClient(
        api_key=SecretStr("PRIVATE-OPENROUTER-SENTINEL"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(VideoGenerationError) as raised:
        client.generate_video(presenter(), approved(), Path("unused.mp4"))
    assert str(raised.value) == "OpenRouter request failed"
    assert "PRIVATE" not in repr(raised.value)


def test_existing_ledger_blocks_a_second_paid_job(tmp_path: Path) -> None:
    """Break caught: a retry can submit another paid job after one was recorded."""
    ledger = tmp_path / "jobs.json"
    ledger.write_text('{"presenter":{"status":"completed"}}', encoding="utf-8")
    with pytest.raises(VideoGenerationError, match="job already recorded"):
        OpenRouterMediaClient.guard_single_job(ledger, "presenter")


def test_load_video_settings_accepts_only_the_three_declared_keys(tmp_path: Path) -> None:
    """Break caught: ambient or unrelated dotenv values can affect paid media work."""
    env_file = tmp_path / ".env.video"
    env_file.write_text(
        "OPENROUTER_API_KEY=PRIVATE-OPENROUTER-SENTINEL\n"
        "OPENROUTER_STAKEHOLDER_MODEL=bytedance/seedance-2.0-fast\n"
        "OPENROUTER_PRESENTER_MODEL=google/veo-3.1-fast",
        encoding="utf-8",
    )

    settings = load_video_settings(env_file)

    assert settings.api_key.get_secret_value() == "PRIVATE-OPENROUTER-SENTINEL"
    assert settings.presenter_model == "google/veo-3.1-fast"
    env_file.write_text(env_file.read_text(encoding="utf-8") + "\nUNRELATED=value", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid video settings"):
        load_video_settings(env_file)


def test_catalog_and_credits_preflight_use_only_get_routes() -> None:
    """Break caught: preflight posts media or exposes credit-account details."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "google/veo-3.1-fast",
                            "supported_durations": [6],
                            "supported_resolutions": ["720p"],
                            "supported_aspect_ratios": ["16:9"],
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"data": {"total_credits": 2, "total_usage": 1}})

    client = OpenRouterMediaClient(
        api_key=SecretStr("PRIVATE-OPENROUTER-SENTINEL"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    capable = client.model_supports(presenter())
    credit_available = client.credit_available()

    assert capable is True
    assert credit_available is True
    assert [(request.method, request.url.path) for request in calls] == [
        ("GET", "/api/v1/videos/models"),
        ("GET", "/api/v1/credits"),
    ]


def test_synthesize_narration_requires_seven_manifest_boundaries(tmp_path: Path) -> None:
    """Break caught: narration headings drift from the approved editorial timeline."""
    manifest = VideoManifest.load(Path("submission/caspian-video-manifest.json"))
    script = tmp_path / "script.md"
    script.write_text("## 0–6 seconds\n\nOnly one section.", encoding="utf-8")

    with pytest.raises(VideoGenerationError, match="Narration script invalid"):
        synthesize_narration_sections(script, manifest, tmp_path / "audio")


def test_empty_tts_response_is_rejected_without_writing_an_audio_file(tmp_path: Path) -> None:
    """Break caught: an empty provider body becomes a seemingly usable narration asset."""
    client = OpenRouterMediaClient(
        api_key=SecretStr("PRIVATE-OPENROUTER-SENTINEL"),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=b"", headers={"content-type": "audio/mpeg"})
            )
        ),
    )
    output = tmp_path / "section.mp3"

    with pytest.raises(VideoGenerationError, match="narration response invalid"):
        client.synthesize_speech("A safe narration line.", output)

    assert not output.exists()


def test_generate_approved_assets_writes_only_under_its_supplied_work_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Break caught: a gated generation writes into the process directory, not its work root."""
    real_client = OpenRouterMediaClient

    class FakeClient:
        last_job_id = "job-safe"
        guard_single_job = staticmethod(real_client.guard_single_job)
        record_job = staticmethod(real_client.record_job)

        def __init__(self, *, api_key: SecretStr) -> None:
            del api_key

        def video_models(self) -> list[dict[str, object]]:
            return []

        def model_supports(self, spec: GenerationSpec, catalog: object) -> bool:
            del spec, catalog
            return True

        def credit_available(self) -> bool:
            return True

        def generate_video(
            self, spec: GenerationSpec, approval: SpendApproval, output: Path
        ) -> GenerationReceipt:
            del approval
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"safe-mp4")
            return GenerationReceipt(
                name=spec.name,
                model=spec.model,
                status="completed",
                cost_usd=Decimal("0.48"),
                output_path=Path(f"work/caspian-video/generated/{spec.name}.mp4"),
            )

    monkeypatch.setattr(openrouter, "OpenRouterMediaClient", FakeClient)
    settings = VideoSettings(
        api_key=SecretStr("PRIVATE-OPENROUTER-SENTINEL"),
        presenter_model="google/veo-3.1-fast",
        stakeholder_model="bytedance/seedance-2.0-fast",
    )

    openrouter.generate_approved_assets(settings, approved(), tmp_path)

    assert (tmp_path / "work/caspian-video/generated/presenter.mp4").read_bytes() == b"safe-mp4"
    assert (tmp_path / "work/caspian-video/generated/stakeholders.mp4").read_bytes() == b"safe-mp4"


def test_generate_cli_requires_both_spend_flags_before_loading_credentials(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: generate reads credentials or reaches a provider without both approvals."""
    monkeypatch.setattr(
        "scripts.caspian_video.__main__.load_video_settings",
        lambda _path: pytest.fail("credential file was read before the paid-command gate"),
    )

    with pytest.raises(SystemExit) as raised:
        main(["generate"])

    assert raised.value.code == 2


def test_completed_job_is_ledgered_before_the_post_job_credit_check_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Break caught: a successful paid job is unrecorded when its trailing credit check fails."""
    real_client = OpenRouterMediaClient

    class FakeClient:
        last_job_id = "job-safe"
        guard_single_job = staticmethod(real_client.guard_single_job)
        record_job = staticmethod(real_client.record_job)

        def __init__(self, *, api_key: SecretStr) -> None:
            del api_key
            self.credit_checks = 0

        def video_models(self) -> list[dict[str, object]]:
            return []

        def model_supports(self, spec: GenerationSpec, catalog: object) -> bool:
            del spec, catalog
            return True

        def credit_available(self) -> bool:
            self.credit_checks += 1
            return self.credit_checks == 1

        def generate_video(
            self, spec: GenerationSpec, approval: SpendApproval, output: Path
        ) -> GenerationReceipt:
            del approval
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"safe-mp4")
            return GenerationReceipt(
                name=spec.name,
                model=spec.model,
                status="completed",
                cost_usd=Decimal("0.48"),
                output_path=Path(f"work/caspian-video/generated/{spec.name}.mp4"),
            )

    monkeypatch.setattr(openrouter, "OpenRouterMediaClient", FakeClient)
    settings = VideoSettings(
        api_key=SecretStr("PRIVATE-OPENROUTER-SENTINEL"),
        presenter_model="google/veo-3.1-fast",
        stakeholder_model="bytedance/seedance-2.0-fast",
    )

    with pytest.raises(VideoGenerationError, match="credits unavailable"):
        openrouter.generate_approved_assets(settings, approved(), tmp_path)

    assert json.loads((tmp_path / "work/caspian-video/openrouter/jobs.json").read_text()) == {
        "presenter": {
            "cost_usd": "0.48",
            "job_id": "job-safe",
            "model": "google/veo-3.1-fast",
            "status": "completed",
        }
    }
