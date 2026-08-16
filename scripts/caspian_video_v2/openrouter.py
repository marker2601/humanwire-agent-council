"""Cost-gated OpenRouter boundary for the professional HumanWire video."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast

import httpx
from pydantic import SecretStr

from scripts.caspian_video_v2.models import (
    MediaReceipt,
    NarrationSpec,
    PreflightResult,
    SpendAuthorization,
    VideoJobSpec,
)

BASE_URL = "https://openrouter.ai"
VIDEO_MODELS_PATH = "/api/v1/videos/models"
TTS_MODELS_PATH = "/api/v1/models?output_modalities=speech"
CREDITS_PATH = "/api/v1/credits"
VIDEO_PATH = "/api/v1/videos"
TTS_PATH = "/api/v1/audio/speech"
MAX_MEDIA_BYTES = 250 * 1024 * 1024
POLL_SECONDS = 10
MAX_POLLS = 90
_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class MediaGenerationError(RuntimeError):
    """Fixed public boundary for provider and media failures."""


def _error() -> MediaGenerationError:
    return MediaGenerationError("Media generation failed")


def _mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping) and all(isinstance(key, str) for key in value):
        return cast(Mapping[str, object], value)
    return None


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _probe_media(path: Path, kind: str) -> None:
    failed = False
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        payload = _mapping(json.loads(completed.stdout))
        streams = payload.get("streams") if payload is not None else None
        expected = "video" if kind == "video" else "audio"
        failed = (
            not isinstance(streams, list)
            or not any(
                _mapping(stream) is not None
                and _mapping(stream).get("codec_type") == expected
                for stream in streams
            )
        )
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        failed = True
    if failed:
        raise _error()


class ProfessionalMediaClient:
    """Explicit synchronous client with no ambient configuration or implicit POST."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        probe: Callable[[Path, str], None] = _probe_media,
    ) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=60.0)
        self._sleep = sleep
        self._probe = probe
        self._preflight_ready = False

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: object | None = None,
    ) -> httpx.Response:
        response: httpx.Response | None = None
        failed = False
        try:
            response = self._client.request(
                method,
                f"{BASE_URL}{path}",
                headers={
                    "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                    "Content-Type": "application/json",
                },
                json=json_body,
            )
        except httpx.HTTPError:
            failed = True
        if failed or response is None or not 200 <= response.status_code < 300:
            raise _error()
        return response

    @staticmethod
    def _json(response: httpx.Response) -> Mapping[str, object]:
        payload: Mapping[str, object] | None = None
        try:
            payload = _mapping(response.json())
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        if payload is None:
            raise _error()
        return payload

    def preflight(
        self,
        video_specs: Sequence[VideoJobSpec],
        narration_spec: NarrationSpec,
    ) -> PreflightResult:
        video_payload = self._json(self._request("GET", VIDEO_MODELS_PATH))
        speech_payload = self._json(self._request("GET", TTS_MODELS_PATH))
        credit_payload = self._json(self._request("GET", CREDITS_PATH))
        videos = video_payload.get("data")
        speeches = speech_payload.get("data")
        credit_data = _mapping(credit_payload.get("data")) or credit_payload

        video_ready = isinstance(videos, list) and all(
            self._catalog_supports(videos, spec) for spec in video_specs
        )
        speech_ids: set[object] = set()
        if isinstance(speeches, list):
            for value in speeches:
                speech = _mapping(value)
                if speech is not None:
                    speech_ids.add(speech.get("id"))
        narration_ready = (
            narration_spec.model in speech_ids
            and narration_spec.fallback_model in speech_ids
        )
        total = _decimal(credit_data.get("total_credits", credit_data.get("credits")))
        used = _decimal(
            credit_data.get("total_usage", credit_data.get("usage", Decimal(0)))
        )
        credit_ready = total is not None and used is not None and total - used > 0
        self._preflight_ready = video_ready and narration_ready and credit_ready
        return PreflightResult(
            credential_valid=True,
            video_models_ready=video_ready,
            narration_models_ready=narration_ready,
            credit_available=credit_ready,
        )

    @staticmethod
    def _catalog_supports(catalog: list[object], spec: VideoJobSpec) -> bool:
        for value in catalog:
            model = _mapping(value)
            if model is None or model.get("id") != spec.model:
                continue
            durations = model.get("supported_durations")
            resolutions = model.get("supported_resolutions")
            aspects = model.get("supported_aspect_ratios")
            frames = model.get("supported_frame_images")
            return (
                isinstance(durations, list)
                and spec.duration_seconds in durations
                and isinstance(resolutions, list)
                and spec.resolution in resolutions
                and isinstance(aspects, list)
                and spec.aspect_ratio in aspects
                and isinstance(frames, list)
                and "first_frame" in frames
            )
        return False

    @staticmethod
    def _ledger_path(repository_root: Path) -> Path:
        return repository_root / "work/caspian-video-v2/openrouter/jobs.json"

    @staticmethod
    def _read_ledger(path: Path) -> dict[str, object]:
        if not path.exists():
            return {}
        data: Mapping[str, object] | None = None
        try:
            data = _mapping(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
        if data is None:
            raise _error()
        return dict(data)

    @classmethod
    def _update_ledger(
        cls,
        path: Path,
        update: Callable[[dict[str, object]], None],
    ) -> None:
        lock = path.with_suffix(".json.lock")
        temporary = path.with_suffix(".json.tmp")
        failed = False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(descriptor)
            data = cls._read_ledger(path)
            update(data)
            temporary.write_text(
                json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        except (OSError, MediaGenerationError):
            failed = True
        finally:
            _remove(temporary)
            _remove(lock)
        if failed:
            raise _error()

    @classmethod
    def _reserve(
        cls,
        ledger: Path,
        *,
        name: str,
        model: str,
        reserved_usd: Decimal,
        authorization: SpendAuthorization,
    ) -> None:
        def update(data: dict[str, object]) -> None:
            if name in data:
                raise _error()
            already_reserved = Decimal(0)
            for raw_record in data.values():
                record = _mapping(raw_record)
                amount = (
                    _decimal(record.get("reserved_usd"))
                    if record is not None
                    else None
                )
                if amount is None or amount < 0:
                    raise _error()
                already_reserved += amount
            if not authorization.can_reserve(
                reserved_usd,
                already_reserved=already_reserved,
            ):
                raise _error()
            data[name] = {
                "cost_usd": "0",
                "model": model,
                "reserved_usd": str(reserved_usd),
                "status": "reserved",
            }

        cls._update_ledger(ledger, update)

    @classmethod
    def _complete(
        cls,
        ledger: Path,
        *,
        name: str,
        cost_usd: Decimal,
    ) -> None:
        def update(data: dict[str, object]) -> None:
            record = _mapping(data.get(name))
            if record is None or record.get("status") != "reserved":
                raise _error()
            data[name] = {
                "cost_usd": str(cost_usd),
                "model": record.get("model"),
                "reserved_usd": record.get("reserved_usd"),
                "status": "completed",
            }

        cls._update_ledger(ledger, update)

    @staticmethod
    def _frame_data(path: Path) -> str:
        media_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }.get(path.suffix.lower())
        contents: bytes | None = None
        try:
            contents = path.read_bytes()
        except OSError:
            pass
        if media_type is None or not contents:
            raise _error()
        return f"data:{media_type};base64,{base64.b64encode(contents).decode('ascii')}"

    def generate_video(
        self,
        spec: VideoJobSpec,
        *,
        authorization: SpendAuthorization,
        repository_root: Path,
    ) -> MediaReceipt:
        if not self._preflight_ready:
            raise _error()
        ledger = self._ledger_path(repository_root)
        self._reserve(
            ledger,
            name=spec.name,
            model=spec.model,
            reserved_usd=spec.reserved_usd,
            authorization=authorization,
        )
        payload = {
            "model": spec.model,
            "prompt": spec.prompt,
            "duration": spec.duration_seconds,
            "resolution": spec.resolution,
            "aspect_ratio": spec.aspect_ratio,
            "generate_audio": False,
            "frame_images": [
                {
                    "type": "image_url",
                    "frame_type": "first_frame",
                    "image_url": {
                        "url": self._frame_data(repository_root / spec.first_frame)
                    },
                }
            ],
        }
        submitted = self._json(
            self._request("POST", VIDEO_PATH, json_body=payload)
        )
        raw_id = submitted.get("id")
        if not isinstance(raw_id, str) or _JOB_ID.fullmatch(raw_id) is None:
            raise _error()
        completed: Mapping[str, object] | None = None
        for index in range(MAX_POLLS):
            job = self._json(self._request("GET", f"{VIDEO_PATH}/{raw_id}"))
            if job.get("id") != raw_id:
                raise _error()
            status = job.get("status")
            if status == "completed":
                completed = job
                break
            if status not in {"pending", "queued", "in_progress", "processing"}:
                raise _error()
            if index + 1 < MAX_POLLS:
                self._sleep(POLL_SECONDS)
        if completed is None:
            raise _error()
        usage = _mapping(completed.get("usage"))
        cost = _decimal(usage.get("cost")) if usage is not None else None
        if cost is None or cost < 0 or cost > spec.reserved_usd:
            raise _error()
        output = repository_root / spec.output_path
        self._download_video(raw_id, output)
        self._probe(output, "video")
        self._complete(ledger, name=spec.name, cost_usd=cost)
        return MediaReceipt(
            name=spec.name,
            model=spec.model,
            status="completed",
            cost_usd=cost,
            output_path=spec.output_path,
            sha256=hashlib.sha256(output.read_bytes()).hexdigest().upper(),
        )

    def _download_video(self, job_id: str, output: Path) -> None:
        response: httpx.Response | None = None
        failed = False
        try:
            response = self._request(
                "GET",
                f"{VIDEO_PATH}/{job_id}/content?index=0",
            )
            content_type = response.headers.get("content-type", "").split(";", 1)[0]
            if (
                content_type.lower() != "video/mp4"
                or not response.content
                or len(response.content) > MAX_MEDIA_BYTES
            ):
                failed = True
            else:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(response.content)
        except (OSError, MediaGenerationError):
            failed = True
        if failed:
            _remove(output)
            raise _error()

    def generate_narration(
        self,
        spec: NarrationSpec,
        *,
        authorization: SpendAuthorization,
        repository_root: Path,
    ) -> MediaReceipt:
        if not self._preflight_ready:
            raise _error()
        ledger = self._ledger_path(repository_root)
        self._reserve(
            ledger,
            name="narration_v2",
            model=spec.model,
            reserved_usd=spec.reserved_usd,
            authorization=authorization,
        )
        response = self._request(
            "POST",
            TTS_PATH,
            json_body={
                "model": spec.model,
                "input": spec.input_text,
                "voice": "alloy",
                "response_format": "mp3",
                "speed": 1.0,
            },
        )
        content_type = response.headers.get("content-type", "").split(";", 1)[0]
        if content_type.lower() != "audio/mpeg" or not response.content:
            raise _error()
        output = repository_root / spec.output_path
        failed = False
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(response.content)
        except OSError:
            failed = True
        if failed:
            _remove(output)
            raise _error()
        self._probe(output, "audio")
        self._complete(
            ledger,
            name="narration_v2",
            cost_usd=spec.reserved_usd,
        )
        return MediaReceipt(
            name="narration_v2",
            model=spec.model,
            status="completed",
            cost_usd=spec.reserved_usd,
            output_path=spec.output_path,
            sha256=hashlib.sha256(output.read_bytes()).hexdigest().upper(),
        )
