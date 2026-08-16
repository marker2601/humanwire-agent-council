import json
import subprocess
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


def test_approved_specs_use_the_authorized_prompts_and_first_frames() -> None:
    """Break caught: the paid CLI drifts from the two exact user-approved requests."""
    settings = VideoSettings(
        api_key=SecretStr("PRIVATE-OPENROUTER-SENTINEL"),
        presenter_model="google/veo-3.1-fast",
        stakeholder_model="bytedance/seedance-2.0-fast",
    )

    generated = openrouter._approved_specs(settings)

    assert [(spec.prompt, spec.first_frame) for spec in generated] == [
        (
            (
                "Six-second 16:9 cinematic commercial shot based on the provided first frame. "
                "A fictional professional visual guide looks into camera with calm confidence, "
                "makes one subtle open-hand gesture, and holds a natural attentive expression. "
                "Slow controlled camera push-in, premium dark enterprise studio, restrained cyan "
                "accent lights, realistic human motion, no speech, no lip-sync emphasis, no text, "
                "no logos, no UI, no extra people, no camera shake."
            ),
            Path("work/caspian-video/references/presenter.png"),
        ),
        (
            (
                "Eight-second 16:9 motion-graphics shot based on the provided first frame. Seven "
                "illustrated enterprise software-agent role cards activate one after another "
                "around a central cyan coordination path; fine connection lines flow from role to "
                "role and converge toward a decision node. Smooth professional motion, coherent "
                "navy and cyan palette, cards and faces remain stable, no speech bubbles, no typed "
                "messages, no text mutation, no logos, no implication of real people or live "
                "communication."
            ),
            Path("work/caspian-video/references/stakeholders.png"),
        ),
    ]


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


def test_generate_approved_assets_writes_only_under_its_canonical_work_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Break caught: a gated generation writes into the process directory, not its work root."""
    real_client = OpenRouterMediaClient

    class FakeClient:
        last_job_id = "job-safe"
        guard_single_job = staticmethod(real_client.guard_single_job)
        record_job = staticmethod(real_client.record_job)
        reserve_job = staticmethod(real_client.reserve_job)
        _ledger_data = staticmethod(real_client._ledger_data)

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
            self, spec: GenerationSpec, approval: SpendApproval, output: Path, *, budget_usd: Decimal
        ) -> GenerationReceipt:
            del approval, budget_usd
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
    monkeypatch.setattr(openrouter, "REPOSITORY_ROOT", tmp_path)
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
        reserve_job = staticmethod(real_client.reserve_job)
        _ledger_data = staticmethod(real_client._ledger_data)

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
            self, spec: GenerationSpec, approval: SpendApproval, output: Path, *, budget_usd: Decimal
        ) -> GenerationReceipt:
            del approval, budget_usd
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
    monkeypatch.setattr(openrouter, "REPOSITORY_ROOT", tmp_path)
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


def test_transport_failure_has_no_recursive_authenticated_exception_graph() -> None:
    """Break caught: an httpx cause retains the Authorization-bearing request graph."""
    sentinel = "PRIVATE-OPENROUTER-SENTINEL"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = OpenRouterMediaClient(
        api_key=SecretStr(sentinel), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(VideoGenerationError) as raised:
        client.generate_video(presenter(), approved(), Path("unused.mp4"))

    seen: set[int] = set()

    def contains_secret(value: object) -> bool:
        if id(value) in seen:
            return False
        seen.add(id(value))
        if isinstance(value, str):
            return sentinel in value
        if isinstance(value, BaseException):
            return any(
                contains_secret(item)
                for item in (*value.args, value.__cause__, value.__context__, value.__traceback__)
                if item is not None
            )
        if isinstance(value, httpx.Request):
            return any(contains_secret(item) for item in value.headers.values())
        return False

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert not contains_secret(raised.value)


def test_catalog_rejects_nested_capability_lookalikes_when_top_level_is_malformed() -> None:
    """Break caught: unrelated nested metadata makes an unsupported paid spec look supported."""
    client = OpenRouterMediaClient(api_key=SecretStr("PRIVATE-OPENROUTER-SENTINEL"))
    hostile_catalog = [
        {
            "id": "google/veo-3.1-fast",
            "supported_durations": [],
            "supported_resolutions": "720p",
            "supported_aspect_ratios": ["16:9"],
            "metadata": {
                "supported_durations": [6],
                "supported_resolutions": ["720p"],
                "supported_aspect_ratios": ["16:9"],
            },
        }
    ]

    assert client.model_supports(presenter(), hostile_catalog) is False


def test_tts_cli_rejects_noncanonical_output_before_loading_credentials(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: narration output can escape its canonical generated directory."""
    monkeypatch.setattr(
        "scripts.caspian_video.__main__.load_video_settings",
        lambda _path: pytest.fail("credentials were read before output-path validation"),
    )

    with pytest.raises(SystemExit) as raised:
        main(["tts", "--output-dir", "../outside"])

    assert raised.value.code == 2


def test_tts_cli_accepts_only_the_canonical_narration_directory(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: the approved narration output directory is rejected with the unsafe ones."""
    settings = VideoSettings(
        api_key=SecretStr("PRIVATE-OPENROUTER-SENTINEL"),
        presenter_model="google/veo-3.1-fast",
        stakeholder_model="bytedance/seedance-2.0-fast",
    )
    received: list[Path] = []
    monkeypatch.setattr("scripts.caspian_video.__main__.load_video_settings", lambda _path: settings)
    monkeypatch.setattr("scripts.caspian_video.__main__.OpenRouterMediaClient", lambda **_kwargs: object())
    monkeypatch.setattr(
        "scripts.caspian_video.__main__.synthesize_narration_sections",
        lambda _script, _manifest, output_dir, **_kwargs: received.append(output_dir),
    )

    assert main(["tts", "--output-dir", "work/caspian-video/generated/narration"]) == 0
    assert received == [Path("work/caspian-video/generated/narration")]


def test_script_sections_stop_at_later_markdown_subheadings(tmp_path: Path) -> None:
    """Break caught: a subheading is narrated or discards the paragraph that follows it."""
    manifest = VideoManifest.load(Path("submission/caspian-video-manifest.json"))
    source = Path("submission/caspian-video-script.md").read_text(encoding="utf-8")
    script = tmp_path / "script.md"
    script.write_text(source.replace("\n\n## 6–22 seconds", "\n\n### Not narration\n\nIgnore this label.\n\n## 6–22 seconds"), encoding="utf-8")

    sections = openrouter._script_sections(script, manifest)

    assert "Not narration" not in sections[0]
    assert "Ignore this label" in sections[0]


def test_probe_timeout_removes_rejected_mp3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Break caught: a timed-out duration probe leaves a potentially invalid MP3 behind."""
    output = tmp_path / "section.mp3"
    output.write_bytes(b"mp3")
    monkeypatch.setattr(
        openrouter.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("ffprobe", 1)),
    )

    with pytest.raises(VideoGenerationError, match="Narration audio invalid"):
        openrouter._validate_mp3_duration(output, 6)

    assert not output.exists()


def test_generation_reserves_the_job_in_its_ledger_before_post_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Break caught: a pre-POST interruption leaves the job name eligible for a paid retry."""
    real_client = OpenRouterMediaClient

    class FakeClient:
        guard_single_job = staticmethod(real_client.guard_single_job)
        record_job = staticmethod(real_client.record_job)
        reserve_job = staticmethod(real_client.reserve_job)
        _ledger_data = staticmethod(real_client._ledger_data)

        def __init__(self, *, api_key: SecretStr) -> None:
            del api_key

        def video_models(self) -> list[dict[str, object]]:
            return []

        def model_supports(self, spec: GenerationSpec, catalog: object) -> bool:
            del spec, catalog
            return True

        def credit_available(self) -> bool:
            return True

        def generate_video(self, *args: object, **kwargs: object) -> GenerationReceipt:
            del args, kwargs
            ledger = tmp_path / "work/caspian-video/openrouter/jobs.json"
            assert json.loads(ledger.read_text())["presenter"]["status"] == "reserved"
            raise VideoGenerationError("OpenRouter request failed")

    monkeypatch.setattr(openrouter, "OpenRouterMediaClient", FakeClient)
    monkeypatch.setattr(openrouter, "REPOSITORY_ROOT", tmp_path)
    settings = VideoSettings(
        api_key=SecretStr("PRIVATE-OPENROUTER-SENTINEL"),
        presenter_model="google/veo-3.1-fast",
        stakeholder_model="bytedance/seedance-2.0-fast",
    )

    with pytest.raises(VideoGenerationError, match="request failed"):
        openrouter.generate_approved_assets(settings, approved(), tmp_path)

    with pytest.raises(VideoGenerationError, match="job already recorded"):
        real_client.guard_single_job(tmp_path / "work/caspian-video/openrouter/jobs.json", "presenter")


def test_second_two_dollar_job_is_rejected_against_remaining_total_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Break caught: each job is checked against $3 instead of their cumulative approved total."""
    real_client = OpenRouterMediaClient

    class FakeClient:
        guard_single_job = staticmethod(real_client.guard_single_job)
        record_job = staticmethod(real_client.record_job)
        reserve_job = staticmethod(real_client.reserve_job)
        _ledger_data = staticmethod(real_client._ledger_data)
        last_job_id = "job-safe"
        post_count = 0

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
            self, spec: GenerationSpec, approval: SpendApproval, output: Path, *, budget_usd: Decimal
        ) -> GenerationReceipt:
            del approval, output
            FakeClient.post_count += 1
            return GenerationReceipt(
                name=spec.name,
                model=spec.model,
                status="completed",
                cost_usd=Decimal("2.00"),
                output_path=Path(f"work/caspian-video/generated/{spec.name}.mp4"),
            )

    monkeypatch.setattr(openrouter, "OpenRouterMediaClient", FakeClient)
    monkeypatch.setattr(openrouter, "REPOSITORY_ROOT", tmp_path)
    settings = VideoSettings(
        api_key=SecretStr("PRIVATE-OPENROUTER-SENTINEL"),
        presenter_model="google/veo-3.1-fast",
        stakeholder_model="bytedance/seedance-2.0-fast",
    )

    with pytest.raises(VideoGenerationError, match="cost exceeds approval"):
        openrouter.generate_approved_assets(settings, approved(), tmp_path)

    ledger = json.loads((tmp_path / "work/caspian-video/openrouter/jobs.json").read_text())
    assert ledger["presenter"]["cost_usd"] == "2.00"
    assert "stakeholders" not in ledger
    assert FakeClient.post_count == 1


def test_second_work_root_is_rejected_before_creating_a_media_client(tmp_path: Path) -> None:
    """Break caught: a new root selects a fresh ledger and bypasses a prior reservation."""
    settings = VideoSettings(
        api_key=SecretStr("PRIVATE-OPENROUTER-SENTINEL"),
        presenter_model="google/veo-3.1-fast",
        stakeholder_model="bytedance/seedance-2.0-fast",
    )

    with pytest.raises(VideoGenerationError, match="work root invalid"):
        openrouter.generate_approved_assets(settings, approved(), tmp_path)


def test_generate_cli_uses_the_source_owned_repository_root_after_chdir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Break caught: changing CWD creates a fresh ledger identity before a provider POST."""
    settings = VideoSettings(
        api_key=SecretStr("PRIVATE-OPENROUTER-SENTINEL"),
        presenter_model="google/veo-3.1-fast",
        stakeholder_model="bytedance/seedance-2.0-fast",
    )
    received: list[Path] = []
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setattr("scripts.caspian_video.__main__.load_video_settings", lambda _path: settings)
    monkeypatch.setattr(
        "scripts.caspian_video.__main__.generate_approved_assets",
        lambda _settings, _approval, root: received.append(root) or (),
    )

    for directory in (first, second):
        monkeypatch.chdir(directory)
        assert main(["generate", "--confirm-paid-generation", "--approve-spend-usd", "3.00"]) == 0

    assert received == [openrouter.REPOSITORY_ROOT, openrouter.REPOSITORY_ROOT]


@pytest.mark.parametrize("headers", [{"content-length": "5"}, {}])
def test_oversized_video_content_is_rejected_without_leaving_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, headers: dict[str, str]
) -> None:
    """Break caught: declared or chunked oversized content is buffered or left on disk."""
    monkeypatch.setattr(openrouter, "MAX_VIDEO_BYTES", 4)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"id": "job-safe", "status": "pending"})
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"12345", headers={"content-type": "video/mp4", **headers})
        return httpx.Response(
            200, json={"id": "job-safe", "status": "completed", "usage": {"cost": 0.48}}
        )

    client = OpenRouterMediaClient(
        api_key=SecretStr("PRIVATE-OPENROUTER-SENTINEL"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
    )
    output = tmp_path / "presenter.mp4"

    with pytest.raises(VideoGenerationError, match="video content invalid"):
        client.generate_video(presenter(), approved(), output)

    assert not output.exists()
