"""Local-only screen capture and irreversible video-redaction helpers."""

from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MEDIA_WORK_ROOT = REPOSITORY_ROOT / "work" / "caspian-video"
_CAPTURE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class MediaPathError(RuntimeError):
    """Fixed-message boundary for unsafe or unavailable media paths."""


class CoverRegion(BaseModel):
    """One irreversible, opaque rectangle in the captured desktop frame."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    x: int = Field(ge=0, le=7680, strict=True)
    y: int = Field(ge=0, le=4320, strict=True)
    width: int = Field(ge=1, le=7680, strict=True)
    height: int = Field(ge=1, le=4320, strict=True)


def _resolved_path(path: Path) -> Path:
    failed = False
    resolved: Path | None = None
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError, ValueError):
        failed = True
    if failed or resolved is None:
        raise MediaPathError("media path invalid")
    return resolved


def _trusted_media_root() -> Path:
    repository_root = _resolved_path(REPOSITORY_ROOT)
    configured_root = _resolved_path(MEDIA_WORK_ROOT)
    expected_root = _resolved_path(repository_root / "work" / "caspian-video")
    if configured_root != expected_root or not configured_root.is_relative_to(repository_root):
        raise MediaPathError("media path invalid")
    return configured_root


def _make_parent(path: Path) -> None:
    failed = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, RuntimeError, ValueError):
        failed = True
    if failed:
        raise MediaPathError("media path invalid")


def _is_file(path: Path) -> bool:
    failed = False
    result = False
    try:
        result = path.is_file()
    except (OSError, RuntimeError, ValueError):
        failed = True
    if failed:
        raise MediaPathError("media path invalid")
    return result


def _exists(path: Path) -> bool:
    failed = False
    result = False
    try:
        result = path.exists()
    except (OSError, RuntimeError, ValueError):
        failed = True
    if failed:
        raise MediaPathError("media path invalid")
    return result


def safe_media_path(work_root: Path, value: str | Path) -> Path:
    """Resolve a relative path only when it stays inside ``work_root``."""
    failed = False
    candidate: Path | None = None
    try:
        candidate = Path(value)
    except (OSError, RuntimeError, TypeError, ValueError):
        failed = True
    if failed or candidate is None:
        raise MediaPathError("media path invalid")
    if candidate.is_absolute() or candidate.drive or ".." in candidate.parts:
        raise MediaPathError("media path invalid")
    root = _resolved_path(work_root)
    resolved = _resolved_path(root / candidate)
    if not resolved.is_relative_to(root):
        raise MediaPathError("media path invalid")
    return resolved


def build_capture_command(output: Path, fps: int) -> list[str]:
    """Build the non-shell Windows desktop-capture argument vector."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "gdigrab",
        "-framerate",
        str(fps),
        "-draw_mouse",
        "1",
        "-i",
        "desktop",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-n",
        str(_resolved_path(output)),
    ]


def cover_filter(*, x: int, y: int, width: int, height: int) -> str:
    """Return an irreversible opaque privacy-cover filter."""
    return f"drawbox=x={x}:y={y}:w={width}:h={height}:color=0x081522@1:t=fill"


def _owned_existing_path(path: Path) -> Path:
    root = _trusted_media_root()
    resolved = _resolved_path(path)
    if not resolved.is_relative_to(root) or not _is_file(resolved):
        raise MediaPathError("media path invalid")
    return resolved


def _owned_output_path(path: Path) -> Path:
    root = _trusted_media_root()
    resolved = _resolved_path(path)
    if (
        not resolved.is_relative_to(root)
        or resolved.suffix.lower() != ".mp4"
        or _exists(resolved)
    ):
        raise MediaPathError("media output exists" if _exists(resolved) else "media path invalid")
    return resolved


def _run_ffmpeg(command: list[str], message: str) -> None:
    failed = False
    try:
        subprocess.run(command, check=True, shell=False)
    except (OSError, subprocess.CalledProcessError):
        failed = True
    if failed:
        raise MediaPathError(message)


def run_capture(name: str, work_root: Path) -> Path:
    """Record the Windows desktop until FFmpeg receives ``q`` on this terminal's stdin."""
    if not _CAPTURE_NAME.fullmatch(name):
        raise MediaPathError("media path invalid")
    root = _resolved_path(work_root)
    if root != _trusted_media_root():
        raise MediaPathError("media path invalid")
    output = safe_media_path(root, Path("raw") / f"{name}.mp4")
    if _exists(output):
        raise MediaPathError("media output exists")
    _make_parent(output)
    _run_ffmpeg(build_capture_command(output, fps=30), "media capture failed")
    return output


def _valid_timing(start: float, duration: float) -> bool:
    return (
        type(start) in (int, float)
        and type(duration) in (int, float)
        and math.isfinite(start)
        and math.isfinite(duration)
        and start >= 0
        and duration > 0
    )


def trim_clip(source: Path, output: Path, start: float, duration: float) -> Path:
    """Create a video-only cut without preserving any channel audio."""
    if not _valid_timing(start, duration):
        raise MediaPathError("media trim invalid")
    source_path = _owned_existing_path(source)
    output_path = _owned_output_path(output)
    _make_parent(output_path)
    _run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-ss",
            str(start),
            "-i",
            str(source_path),
            "-t",
            str(duration),
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-n",
            str(output_path),
        ],
        "media trim failed",
    )
    return output_path


def cover_regions(source: Path, output: Path, regions: tuple[CoverRegion, ...]) -> Path:
    """Create a video-only clip with non-reversible opaque privacy covers."""
    if not 1 <= len(regions) <= 20:
        raise MediaPathError("redaction regions invalid")
    source_path = _owned_existing_path(source)
    output_path = _owned_output_path(output)
    filters = ",".join(
        cover_filter(x=region.x, y=region.y, width=region.width, height=region.height)
        for region in regions
    )
    _make_parent(output_path)
    _run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-i",
            str(source_path),
            "-vf",
            filters,
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-n",
            str(output_path),
        ],
        "media cover failed",
    )
    return output_path


def load_cover_regions(regions_file: Path) -> tuple[CoverRegion, ...]:
    """Load at most twenty strict rectangle records from ignored local storage."""
    failed = False
    loaded: object = None
    try:
        resolved = _owned_existing_path(regions_file)
        loaded = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(loaded, list) or not 1 <= len(loaded) <= 20:
            failed = True
        else:
            return tuple(CoverRegion.model_validate(region) for region in loaded)
    except (MediaPathError, OSError, RuntimeError, ValueError, json.JSONDecodeError, ValidationError, TypeError):
        failed = True
    if failed:
        raise MediaPathError("redaction regions invalid")
    raise AssertionError("redaction loading must return or fail")
