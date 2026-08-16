"""Cost-gated OpenRouter media operations for the Caspian submission video."""

from __future__ import annotations

import base64
import json
import math
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast

import httpx
from pydantic import SecretStr, ValidationError

from scripts.caspian_video.models import (
    GenerationReceipt,
    GenerationSpec,
    SpendApproval,
    VideoManifest,
    VideoSettings,
)

BASE_URL = "https://openrouter.ai"
VIDEO_MODELS_PATH = "/api/v1/videos/models"
VIDEO_PATH = "/api/v1/videos"
TTS_PATH = "/api/v1/audio/speech"
CREDITS_PATH = "/api/v1/credits"
POLL_INTERVAL_SECONDS = 30
MAX_POLLS = 40
MAX_VIDEO_BYTES = 200 * 1024 * 1024
_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SECTION_HEADING = re.compile(r"^## (\d+)–(\d+) seconds$")
_SETTINGS_KEYS = frozenset(
    {
        "OPENROUTER_API_KEY",
        "OPENROUTER_STAKEHOLDER_MODEL",
        "OPENROUTER_PRESENTER_MODEL",
    }
)


class VideoGenerationError(RuntimeError):
    """Fixed-message boundary for media generation failures."""


def _error(message: str) -> VideoGenerationError:
    """Create a public error without carrying a provider response or secret."""
    return VideoGenerationError(message)


def _as_mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping) and all(isinstance(key, str) for key in value):
        return cast(Mapping[str, object], value)
    return None


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() else None


def load_video_settings(path: Path) -> VideoSettings:
    """Read only the three approved dotenv keys without changing process state."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError("invalid video settings") from exc

    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key.strip() != key or key not in _SETTINGS_KEYS or key in values:
            raise ValueError("invalid video settings")
        cleaned_value = value.strip()
        if len(cleaned_value) >= 2 and cleaned_value[0] == cleaned_value[-1] in {"'", '"'}:
            cleaned_value = cleaned_value[1:-1]
        if not cleaned_value:
            raise ValueError("invalid video settings")
        values[key] = cleaned_value

    if set(values) != _SETTINGS_KEYS:
        raise ValueError("invalid video settings")
    try:
        return VideoSettings(
            api_key=SecretStr(values["OPENROUTER_API_KEY"]),
            presenter_model=values["OPENROUTER_PRESENTER_MODEL"],
            stakeholder_model=values["OPENROUTER_STAKEHOLDER_MODEL"],
        )
    except ValidationError as exc:
        raise ValueError("invalid video settings") from exc


class OpenRouterMediaClient:
    """A synchronous, explicit client that has no implicit paid operations."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=60.0)
        self._sleep = sleep
        self._last_job_id: str | None = None

    @property
    def last_job_id(self) -> str | None:
        """The validated ID of the last completed job; never a credential."""
        return self._last_job_id

    def _request(self, method: str, path: str, *, json_body: object | None = None) -> httpx.Response:
        try:
            response = self._client.request(
                method,
                f"{BASE_URL}{path}",
                headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
                json=json_body,
            )
        except httpx.HTTPError as exc:
            raise _error("OpenRouter request failed") from exc
        if not 200 <= response.status_code < 300:
            raise _error("OpenRouter request failed")
        return response

    @staticmethod
    def _json(response: httpx.Response) -> Mapping[str, object]:
        try:
            payload = _as_mapping(response.json())
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        if payload is None:
            raise _error("OpenRouter video response invalid")
        return payload

    @staticmethod
    def _validated_job_id(value: object) -> str:
        if not isinstance(value, str) or not _JOB_ID.fullmatch(value):
            raise _error("OpenRouter video response invalid")
        return value

    @staticmethod
    def _frame_data_url(first_frame: Path) -> str:
        suffix = first_frame.suffix.lower()
        media_type = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(suffix)
        if media_type is None:
            raise _error("OpenRouter video request invalid")
        try:
            contents = first_frame.read_bytes()
        except OSError as exc:
            raise _error("OpenRouter video request invalid") from exc
        if not contents:
            raise _error("OpenRouter video request invalid")
        encoded = base64.b64encode(contents).decode("ascii")
        return f"data:{media_type};base64,{encoded}"

    def _video_payload(self, spec: GenerationSpec) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": spec.model,
            "prompt": spec.prompt,
            "duration": spec.duration,
            "resolution": spec.resolution,
            "aspect_ratio": spec.aspect_ratio,
            "generate_audio": False,
        }
        if spec.first_frame is not None:
            payload["frame_images"] = [
                {
                    "frame_type": "first_frame",
                    "image_url": self._frame_data_url(Path.cwd() / spec.first_frame),
                }
            ]
        return payload

    @staticmethod
    def _cost(job: Mapping[str, object]) -> Decimal:
        usage = _as_mapping(job.get("usage"))
        cost = _decimal(usage.get("cost")) if usage is not None else None
        if cost is None or cost < 0:
            raise _error("OpenRouter video response invalid")
        return cost

    @staticmethod
    def _receipt_path(spec: GenerationSpec, output_path: Path) -> Path:
        expected = Path("work/caspian-video/generated") / f"{spec.name}.mp4"
        if output_path == expected:
            return output_path
        return expected

    def generate_video(
        self, spec: GenerationSpec, approval: SpendApproval, output_path: Path
    ) -> GenerationReceipt:
        """Submit exactly one approved video, then poll and download its first asset."""
        if approval.approved is not True:
            raise _error("OpenRouter paid generation requires approval")
        submitted = self._json(self._request("POST", VIDEO_PATH, json_body=self._video_payload(spec)))
        job_id = self._validated_job_id(submitted.get("id"))
        completed: Mapping[str, object] | None = None
        for poll_number in range(MAX_POLLS):
            job = self._json(self._request("GET", f"{VIDEO_PATH}/{job_id}"))
            if job.get("id") != job_id:
                raise _error("OpenRouter video response invalid")
            status = job.get("status")
            if status == "completed":
                completed = job
                break
            if status in {"pending", "in_progress"}:
                if poll_number + 1 < MAX_POLLS:
                    self._sleep(POLL_INTERVAL_SECONDS)
                continue
            if status in {"failed", "cancelled", "expired"}:
                raise _error("OpenRouter video generation failed")
            raise _error("OpenRouter video response invalid")
        if completed is None:
            raise _error("OpenRouter video generation timed out")

        cost = self._cost(completed)
        if cost > approval.ceiling_usd:
            raise _error("OpenRouter video cost exceeds approval")
        content = self._request("GET", f"{VIDEO_PATH}/{job_id}/content?index=0")
        content_type = content.headers.get("content-type", "").split(";", 1)[0].lower()
        content_length = _decimal(content.headers.get("content-length"))
        if content_type != "video/mp4" or (content_length is not None and content_length > MAX_VIDEO_BYTES):
            raise _error("OpenRouter video content invalid")
        try:
            body = content.content
        except httpx.HTTPError as exc:
            raise _error("OpenRouter video content invalid") from exc
        if not body or len(body) > MAX_VIDEO_BYTES:
            raise _error("OpenRouter video content invalid")
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(body)
        except OSError as exc:
            raise _error("OpenRouter video output failed") from exc
        self._last_job_id = job_id
        return GenerationReceipt(
            name=spec.name,
            model=spec.model,
            status="completed",
            cost_usd=cost,
            output_path=self._receipt_path(spec, output_path),
        )

    def video_models(self) -> Sequence[Mapping[str, object]]:
        payload = self._json(self._request("GET", VIDEO_MODELS_PATH))
        data = payload.get("data")
        if not isinstance(data, list):
            raise _error("OpenRouter video response invalid")
        models: list[Mapping[str, object]] = []
        for item in data:
            model = _as_mapping(item)
            if model is not None:
                models.append(model)
        return models

    @staticmethod
    def _capability_values(model: Mapping[str, object], field: str) -> set[str]:
        aliases = {
            "duration": {"duration", "durations", "supported_durations"},
            "resolution": {"resolution", "resolutions", "supported_resolutions"},
            "aspect_ratio": {"aspect_ratio", "aspect_ratios", "supported_aspect_ratios"},
        }[field]
        values: set[str] = set()

        def visit(value: object) -> None:
            mapping = _as_mapping(value)
            if mapping is not None:
                for key, nested in mapping.items():
                    if key in aliases:
                        if isinstance(nested, list):
                            values.update(str(item) for item in nested)
                        else:
                            values.add(str(nested))
                    visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)

        visit(model)
        return values

    def model_supports(
        self, spec: GenerationSpec, catalog: Sequence[Mapping[str, object]] | None = None
    ) -> bool:
        """Verify every paid-job setting against the current provider catalog."""
        for model in catalog if catalog is not None else self.video_models():
            if model.get("id", model.get("model")) != spec.model:
                continue
            return (
                str(spec.duration) in self._capability_values(model, "duration")
                and spec.resolution in self._capability_values(model, "resolution")
                and spec.aspect_ratio in self._capability_values(model, "aspect_ratio")
            )
        return False

    def credit_available(self) -> bool:
        """Return only whether credits are available; never expose account data."""
        payload = self._json(self._request("GET", CREDITS_PATH))
        data = _as_mapping(payload.get("data")) or payload
        total = _decimal(data.get("total_credits", data.get("credits")))
        used = _decimal(data.get("total_usage", data.get("usage", 0)))
        return total is not None and used is not None and total - used > 0

    @staticmethod
    def guard_single_job(ledger: Path, name: str) -> None:
        """Fail closed when a named job was previously recorded in the local ledger."""
        if not ledger.exists():
            return
        try:
            parsed = _as_mapping(json.loads(ledger.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            raise _error("OpenRouter job ledger invalid") from exc
        if parsed is None:
            raise _error("OpenRouter job ledger invalid")
        if name in parsed:
            raise _error("OpenRouter job already recorded")

    @staticmethod
    def record_job(
        ledger: Path, *, name: str, job_id: str, model: str, status: str, cost_usd: Decimal
    ) -> None:
        """Persist only the minimum local paid-job audit record."""
        OpenRouterMediaClient.guard_single_job(ledger, name)
        record = {"job_id": job_id, "model": model, "status": status, "cost_usd": str(cost_usd)}
        try:
            existing = (
                _as_mapping(json.loads(ledger.read_text(encoding="utf-8"))) if ledger.exists() else {}
            )
            if existing is None:
                raise _error("OpenRouter job ledger invalid")
            ledger.parent.mkdir(parents=True, exist_ok=True)
            ledger.write_text(
                json.dumps({**existing, name: record}, sort_keys=True) + "\n", encoding="utf-8"
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise _error("OpenRouter job ledger invalid") from exc

    def synthesize_speech(self, narration: str, output_path: Path) -> None:
        """Generate one free TTS section, without a paid fallback path."""
        if not narration.strip():
            raise _error("OpenRouter narration request invalid")
        response = self._request(
            "POST",
            TTS_PATH,
            json_body={
                "model": "deepgram/flux-tts:free",
                "input": narration,
                "response_format": "mp3",
            },
        )
        if response.headers.get("content-type", "").split(";", 1)[0].lower() != "audio/mpeg":
            raise _error("OpenRouter narration response invalid")
        try:
            body = response.content
            if not body:
                raise _error("OpenRouter narration response invalid")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(body)
        except (OSError, httpx.HTTPError) as exc:
            raise _error("OpenRouter narration response invalid") from exc


def _approved_specs(settings: VideoSettings) -> tuple[GenerationSpec, GenerationSpec]:
    return (
        GenerationSpec(
            name="presenter",
            model=settings.presenter_model,
            prompt="Fictional professional visual guide in a dark enterprise studio, subtle push-in.",
            duration=6,
            resolution="720p",
            aspect_ratio="16:9",
            generate_audio=False,
        ),
        GenerationSpec(
            name="stakeholders",
            model=settings.stakeholder_model,
            prompt="Role-specific fictional software-agent stakeholders in a dark enterprise studio.",
            duration=8,
            resolution="720p",
            aspect_ratio="16:9",
            generate_audio=False,
        ),
    )


def generate_approved_assets(
    settings: VideoSettings, approval: SpendApproval, work_root: Path
) -> tuple[GenerationReceipt, GenerationReceipt]:
    """Perform the two explicit, catalog-checked generation jobs after spend approval."""
    client = OpenRouterMediaClient(api_key=settings.api_key)
    specs = _approved_specs(settings)
    catalog = client.video_models()
    if not all(client.model_supports(spec, catalog) for spec in specs):
        raise _error("OpenRouter model capability unavailable")
    ledger = work_root / "work/caspian-video/openrouter/jobs.json"
    receipts: list[GenerationReceipt] = []
    for spec in specs:
        OpenRouterMediaClient.guard_single_job(ledger, spec.name)
        if not client.credit_available():
            raise _error("OpenRouter credits unavailable")
        output = work_root / "work/caspian-video/generated" / f"{spec.name}.mp4"
        receipt = client.generate_video(spec, approval, output)
        if client.last_job_id is None:
            raise _error("OpenRouter video response invalid")
        OpenRouterMediaClient.record_job(
            ledger,
            name=spec.name,
            job_id=client.last_job_id,
            model=receipt.model,
            status=receipt.status,
            cost_usd=receipt.cost_usd,
        )
        if not client.credit_available():
            raise _error("OpenRouter credits unavailable")
        receipts.append(receipt)
    return cast(tuple[GenerationReceipt, GenerationReceipt], tuple(receipts))


def _script_sections(script: Path, manifest: VideoManifest) -> tuple[str, ...]:
    try:
        lines = script.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise _error("Narration script invalid") from exc
    sections: list[tuple[int, int, list[str]]] = []
    current: list[str] | None = None
    for line in lines:
        heading = _SECTION_HEADING.fullmatch(line)
        if heading is not None:
            sections.append((int(heading.group(1)), int(heading.group(2)), []))
            current = sections[-1][2]
        elif current is not None and line.strip():
            current.append(line.strip())
    expected = [(segment.start_seconds, segment.start_seconds + segment.duration_seconds) for segment in manifest.segments]
    actual = [(start, end) for start, end, _ in sections]
    narrations = tuple(" ".join(paragraphs) for _, _, paragraphs in sections)
    if len(sections) != 7 or actual != expected or any(not text for text in narrations):
        raise _error("Narration script invalid")
    return narrations


def _probe_mp3_duration(path: Path) -> float:
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
        duration = float(probe.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise _error("Narration audio invalid") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise _error("Narration audio invalid")
    return duration


def synthesize_narration_sections(
    script: Path,
    manifest: VideoManifest,
    output_dir: Path,
    *,
    client: OpenRouterMediaClient | None = None,
) -> tuple[Path, ...]:
    """Synthesize and duration-check the seven approved narration sections."""
    narrations = _script_sections(script, manifest)
    if client is None:
        raise _error("OpenRouter narration client required")
    outputs: list[Path] = []
    for index, (narration, segment) in enumerate(zip(narrations, manifest.segments, strict=True), start=1):
        output = output_dir / f"{index:02d}-{segment.id}.mp3"
        client.synthesize_speech(narration, output)
        if _probe_mp3_duration(output) > segment.duration_seconds:
            try:
                output.unlink(missing_ok=True)
            except OSError:
                pass
            raise _error("Narration audio invalid")
        outputs.append(output)
    return tuple(outputs)
