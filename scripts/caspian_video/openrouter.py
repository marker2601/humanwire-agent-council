"""Cost-gated OpenRouter media operations for the Caspian submission video."""

from __future__ import annotations

import base64
import json
import math
import os
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
FFPROBE_TIMEOUT_SECONDS = 10
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
JOB_RESERVATIONS = {"presenter": Decimal("1.00"), "stakeholders": Decimal("2.00")}
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


def _remove_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


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
        failed = False
        response: httpx.Response | None = None
        try:
            response = self._client.request(
                method,
                f"{BASE_URL}{path}",
                headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
                json=json_body,
            )
        except httpx.HTTPError:
            failed = True
        if failed or response is None or not 200 <= response.status_code < 300:
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
                    "image_url": self._frame_data_url(REPOSITORY_ROOT / spec.first_frame),
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

    def _download_video(self, job_id: str, output_path: Path) -> None:
        error_message: str | None = None
        try:
            with self._client.stream(
                "GET",
                f"{BASE_URL}{VIDEO_PATH}/{job_id}/content?index=0",
                headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
            ) as response:
                if not 200 <= response.status_code < 300:
                    error_message = "OpenRouter request failed"
                else:
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    content_length = _decimal(response.headers.get("content-length"))
                    if (
                        content_type != "video/mp4"
                        or content_length is None and "content-length" in response.headers
                        or content_length is not None and content_length > MAX_VIDEO_BYTES
                    ):
                        error_message = "OpenRouter video content invalid"
                    else:
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        total = 0
                        with output_path.open("wb") as output:
                            for chunk in response.iter_bytes():
                                total += len(chunk)
                                if total > MAX_VIDEO_BYTES:
                                    error_message = "OpenRouter video content invalid"
                                    break
                                output.write(chunk)
                        if total == 0 and error_message is None:
                            error_message = "OpenRouter video content invalid"
        except httpx.HTTPError:
            error_message = "OpenRouter request failed"
        except OSError:
            error_message = "OpenRouter video output failed"
        if error_message is not None:
            _remove_file(output_path)
            raise _error(error_message)

    def generate_video(
        self, spec: GenerationSpec, approval: SpendApproval, output_path: Path, *, budget_usd: Decimal | None = None
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
        if cost > (budget_usd if budget_usd is not None else approval.ceiling_usd):
            raise _error("OpenRouter video cost exceeds approval")
        self._download_video(job_id, output_path)
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

    def model_supports(
        self, spec: GenerationSpec, catalog: Sequence[Mapping[str, object]] | None = None
    ) -> bool:
        """Verify every paid-job setting against the current provider catalog."""
        for model in catalog if catalog is not None else self.video_models():
            if model.get("id", model.get("model")) != spec.model:
                continue
            durations = model.get("supported_durations")
            resolutions = model.get("supported_resolutions")
            aspect_ratios = model.get("supported_aspect_ratios")
            if (
                not isinstance(durations, list)
                or not durations
                or not all(type(value) is int for value in durations)
                or not isinstance(resolutions, list)
                or not resolutions
                or not all(isinstance(value, str) and value for value in resolutions)
                or not isinstance(aspect_ratios, list)
                or not aspect_ratios
                or not all(isinstance(value, str) and value for value in aspect_ratios)
            ):
                return False
            return (
                spec.duration in durations
                and spec.resolution in resolutions
                and spec.aspect_ratio in aspect_ratios
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
    def _ledger_data(ledger: Path) -> dict[str, object]:
        if not ledger.exists():
            return {}
        try:
            data = _as_mapping(json.loads(ledger.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            data = None
        if data is None:
            raise _error("OpenRouter job ledger invalid")
        return dict(data)

    @staticmethod
    def _lock_ledger(ledger: Path) -> Path:
        lock = ledger.with_suffix(f"{ledger.suffix}.lock")
        try:
            ledger.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(descriptor)
        except OSError:
            raise _error("OpenRouter job ledger locked")
        return lock

    @classmethod
    def _update_ledger(cls, ledger: Path, update: Callable[[dict[str, object]], None]) -> None:
        lock = cls._lock_ledger(ledger)
        temporary = ledger.with_suffix(f"{ledger.suffix}.tmp")
        try:
            data = cls._ledger_data(ledger)
            update(data)
            temporary.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(temporary, ledger)
        except OSError:
            raise _error("OpenRouter job ledger invalid")
        finally:
            _remove_file(temporary)
            _remove_file(lock)

    @classmethod
    def guard_single_job(cls, ledger: Path, name: str) -> None:
        """Fail closed when a named job was previously reserved or recorded."""
        if name in cls._ledger_data(ledger):
            raise _error("OpenRouter job already recorded")

    @classmethod
    def reserve_job(cls, ledger: Path, *, name: str, model: str, reserved_usd: Decimal) -> None:
        """Atomically fence a named paid job before the first provider POST."""
        def reserve(data: dict[str, object]) -> None:
            if name in data:
                raise _error("OpenRouter job already recorded")
            data[name] = {
                "model": model,
                "status": "reserved",
                "cost_usd": "0",
                "reserved_usd": str(reserved_usd),
            }

        cls._update_ledger(ledger, reserve)

    @classmethod
    def record_job(
        cls, ledger: Path, *, name: str, job_id: str, model: str, status: str, cost_usd: Decimal
    ) -> None:
        """Convert a prior reservation into the completed job's minimum audit record."""
        def record(data: dict[str, object]) -> None:
            if name not in data:
                raise _error("OpenRouter job ledger invalid")
            data[name] = {"job_id": job_id, "model": model, "status": status, "cost_usd": str(cost_usd)}

        cls._update_ledger(ledger, record)

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
            prompt=(
                "Six-second 16:9 cinematic commercial shot based on the provided first frame. "
                "A fictional professional visual guide looks into camera with calm confidence, "
                "makes one subtle open-hand gesture, and holds a natural attentive expression. "
                "Slow controlled camera push-in, premium dark enterprise studio, restrained cyan "
                "accent lights, realistic human motion, no speech, no lip-sync emphasis, no text, "
                "no logos, no UI, no extra people, no camera shake."
            ),
            duration=6,
            resolution="720p",
            aspect_ratio="16:9",
            generate_audio=False,
            first_frame=Path("work/caspian-video/references/presenter.png"),
        ),
        GenerationSpec(
            name="stakeholders",
            model=settings.stakeholder_model,
            prompt=(
                "Eight-second 16:9 motion-graphics shot based on the provided first frame. Seven "
                "illustrated enterprise software-agent role cards activate one after another around "
                "a central cyan coordination path; fine connection lines flow from role to role and "
                "converge toward a decision node. Smooth professional motion, coherent navy and cyan "
                "palette, cards and faces remain stable, no speech bubbles, no typed messages, no text "
                "mutation, no logos, no implication of real people or live communication."
            ),
            duration=8,
            resolution="720p",
            aspect_ratio="16:9",
            generate_audio=False,
            first_frame=Path("work/caspian-video/references/stakeholders.png"),
        ),
    )


def generate_approved_assets(
    settings: VideoSettings, approval: SpendApproval, work_root: Path
) -> tuple[GenerationReceipt, GenerationReceipt]:
    """Perform the two explicit, catalog-checked generation jobs after spend approval."""
    canonical_root = REPOSITORY_ROOT
    if work_root.resolve() != canonical_root:
        raise _error("OpenRouter work root invalid")
    client = OpenRouterMediaClient(api_key=settings.api_key)
    specs = _approved_specs(settings)
    catalog = client.video_models()
    if not all(client.model_supports(spec, catalog) for spec in specs):
        raise _error("OpenRouter model capability unavailable")
    ledger = canonical_root / "work/caspian-video/openrouter/jobs.json"
    receipts: list[GenerationReceipt] = []
    for spec in specs:
        ledger_data = OpenRouterMediaClient._ledger_data(ledger)
        committed = Decimal(0)
        for record in ledger_data.values():
            mapped = _as_mapping(record)
            if mapped is None:
                raise _error("OpenRouter job ledger invalid")
            amount = _decimal(mapped.get("cost_usd", mapped.get("reserved_usd")))
            if amount is None or amount < 0:
                raise _error("OpenRouter job ledger invalid")
            committed += amount
        remaining = approval.ceiling_usd - committed
        reservation = JOB_RESERVATIONS[spec.name]
        if remaining < reservation:
            raise _error("OpenRouter video cost exceeds approval")
        OpenRouterMediaClient.reserve_job(
            ledger, name=spec.name, model=spec.model, reserved_usd=reservation
        )
        if not client.credit_available():
            raise _error("OpenRouter credits unavailable")
        output = canonical_root / "work/caspian-video/generated" / f"{spec.name}.mp4"
        receipt = client.generate_video(spec, approval, output, budget_usd=remaining)
        if receipt.cost_usd > remaining:
            raise _error("OpenRouter video cost exceeds approval")
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
        elif line.startswith("#"):
            continue
        elif current is not None and line.strip():
            current.append(line.strip())
    expected = [(segment.start_seconds, segment.start_seconds + segment.duration_seconds) for segment in manifest.segments]
    actual = [(start, end) for start, end, _ in sections]
    narrations = tuple(" ".join(paragraphs) for _, _, paragraphs in sections)
    if len(sections) != 7 or actual != expected or any(not text for text in narrations):
        raise _error("Narration script invalid")
    return narrations


def _probe_mp3_duration(path: Path) -> float:
    failed = False
    duration: float | None = None
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT_SECONDS,
        )
        duration = float(probe.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        failed = True
    if failed or duration is None or not math.isfinite(duration) or duration <= 0:
        raise _error("Narration audio invalid")
    return duration


def _validate_mp3_duration(path: Path, maximum_seconds: int) -> None:
    try:
        if _probe_mp3_duration(path) > maximum_seconds:
            raise _error("Narration audio invalid")
    except VideoGenerationError:
        _remove_file(path)
        raise


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
        _validate_mp3_duration(output, segment.duration_seconds)
        outputs.append(output)
    return tuple(outputs)
