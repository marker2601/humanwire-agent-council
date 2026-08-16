"""Local-only screen capture and irreversible video-redaction helpers."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scripts.caspian_video.models import ProofClass, VideoManifest, VideoSegment

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MEDIA_WORK_ROOT = REPOSITORY_ROOT / "work" / "caspian-video"
_CAPTURE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_PROVIDER_DISCLOSURES = {
    "telegram_authorization": "Telegram provider proof · not recorded",
    "email_evidence": "Email provider proof · not recorded",
}
_PUBLIC_PRODUCT_DISCLOSURE = "Standard agents · no external messages"
_REPOSITORY_URL = "https://github.com/marker2601/humanwire"
_NORMALIZE_FILTER = (
    "scale=1920:1080:force_original_aspect_ratio=decrease,"
    "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x020d1c,"
    "fps=30,format=yuv420p"
)


class MediaPathError(RuntimeError):
    """Fixed-message boundary for unsafe or unavailable media paths."""


class CoverRegion(BaseModel):
    """One irreversible, opaque rectangle in the captured desktop frame."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    x: int = Field(ge=0, le=7680, strict=True)
    y: int = Field(ge=0, le=4320, strict=True)
    width: int = Field(ge=1, le=7680, strict=True)
    height: int = Field(ge=1, le=4320, strict=True)


def _drawtext(label: str, y: int) -> str:
    escaped = label.replace("\\", r"\\").replace("'", r"\'").replace(":", r"\:")
    return (
        "drawtext=fontfile='C\\:/Windows/Fonts/segoeui.ttf':"
        f"text='{escaped}':"
        "fontcolor=white:fontsize=34:"
        "box=1:boxcolor=0x020d1c@0.88:boxborderw=18:"
        f"x=(w-text_w)/2:y={y}"
    )


def _composition_labels(segment: VideoSegment) -> tuple[str, ...]:
    segment_id = segment.id
    proof_class = segment.proof_class
    required_copy = segment.required_copy
    if segment_id in _PROVIDER_DISCLOSURES:
        expected = _PROVIDER_DISCLOSURES[segment_id]
        if proof_class is not ProofClass.GENERATED_VISUAL or expected not in required_copy:
            raise MediaPathError("recorded provider proof unavailable")
        return ("Visual guide", expected)
    if proof_class is ProofClass.GENERATED_VISUAL:
        return ("Visual guide",)
    if proof_class is ProofClass.PUBLIC_PRODUCT:
        return (_PUBLIC_PRODUCT_DISCLOSURE,)
    return ()


def build_compose_commands(
    manifest: VideoManifest, work_root: Path, output: Path
) -> list[list[str]]:
    """Build deterministic FFmpeg normalization and silent-concat commands."""
    root = _resolved_path(work_root)
    compose_root = root / "compose"
    commands: list[list[str]] = []
    for index, segment in enumerate(manifest.segments):
        labels = _composition_labels(segment)
        relative = Path(segment.source).relative_to("work/caspian-video")
        source = root / relative
        if not _is_file(source):
            raise MediaPathError("missing approved asset")
        normalized = compose_root / f"{index:02d}-{segment.id}.mp4"
        filters = [_NORMALIZE_FILTER]
        filters.extend(_drawtext(label, 930 - label_index * 72) for label_index, label in enumerate(labels))
        commands.append(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "warning",
                "-y",
                "-i",
                str(source.resolve()),
                "-t",
                str(segment.duration_seconds),
                "-vf",
                ",".join(filters),
                "-an",
                "-c:v",
                "libx264",
                "-crf",
                "18",
                "-preset",
                "slow",
                "-pix_fmt",
                "yuv420p",
                str(normalized.resolve()),
            ]
        )
    commands.append(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str((compose_root / "segments.txt").resolve()),
            "-f",
            "lavfi",
            "-t",
            str(manifest.total_duration_seconds),
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-t",
            str(manifest.total_duration_seconds),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(_resolved_path(output)),
        ]
    )
    return commands


def validate_repository_url(repository_url: str) -> str:
    """Accept only the exact repository approved for the closing card."""
    if repository_url != _REPOSITORY_URL:
        raise MediaPathError("repository URL invalid")
    return repository_url


def _color_card_command(
    output: Path, duration: int, lines: tuple[tuple[str, int, int], ...]
) -> list[str]:
    filters = [_drawtext(text, y).replace("fontsize=34", f"fontsize={size}") for text, y, size in lines]
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x020d1c:s=1920x1080:r=30:d={duration}",
        "-vf",
        ",".join(filters),
        "-t",
        str(duration),
        "-an",
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-preset",
        "slow",
        "-pix_fmt",
        "yuv420p",
        str(output.resolve()),
    ]


def build_local_card_commands(
    manifest: VideoManifest, work_root: Path, repository_url: str
) -> list[list[str]]:
    """Build truthful local disclosure cards and the final repository card."""
    validate_repository_url(repository_url)
    root = _resolved_path(work_root)
    by_id = {segment.id: segment for segment in manifest.segments}
    commands: list[list[str]] = []
    for segment_id, disclosure in _PROVIDER_DISCLOSURES.items():
        segment = by_id[segment_id]
        if (
            segment.proof_class is not ProofClass.GENERATED_VISUAL
            or disclosure not in segment.required_copy
        ):
            raise MediaPathError("recorded provider proof unavailable")
        output = root / Path(segment.source).relative_to("work/caspian-video")
        commands.append(
            _color_card_command(
                output,
                segment.duration_seconds,
                (
                    ("HumanWire workflow disclosure", 360, 50),
                    (disclosure, 490, 54),
                    (_PUBLIC_PRODUCT_DISCLOSURE, 610, 34),
                ),
            )
        )
    closing = by_id["closing_card"]
    closing_output = root / Path(closing.source).relative_to("work/caspian-video")
    commands.append(
        _color_card_command(
            closing_output,
            closing.duration_seconds,
            (
                ("HumanWire", 260, 76),
                ("One mandate. The right conversations.", 420, 42),
                ("A decision-ready meeting.", 485, 42),
                ("secondsignal.vercel.app", 650, 34),
                ("github.com/marker2601/humanwire", 720, 34),
            ),
        )
    )
    return commands


_SRT_TIME = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> "
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})$"
)


def _srt_seconds(values: tuple[str, str, str, str]) -> float:
    hours, minutes, seconds, milliseconds = (int(value) for value in values)
    if minutes >= 60 or seconds >= 60:
        raise ValueError("invalid SRT time")
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def validate_captions(manifest: VideoManifest, captions: Path) -> Path:
    """Validate SRT readability and keep every cue within one segment window."""
    try:
        resolved = captions.resolve()
        blocks = re.split(r"\r?\n\r?\n", resolved.read_text(encoding="utf-8-sig").strip())
        if not blocks or blocks == [""]:
            raise ValueError("empty captions")
        previous_end = -1.0
        for expected_index, block in enumerate(blocks, start=1):
            lines = block.splitlines()
            if len(lines) not in (3, 4) or lines[0] != str(expected_index):
                raise ValueError("invalid cue")
            match = _SRT_TIME.fullmatch(lines[1])
            if match is None:
                raise ValueError("invalid time")
            start = _srt_seconds(match.groups()[:4])
            end = _srt_seconds(match.groups()[4:])
            spoken_lines = lines[2:]
            if (
                start < previous_end
                or end <= start
                or any(not line or len(line) > 42 for line in spoken_lines)
            ):
                raise ValueError("invalid cue bounds")
            if not any(
                start >= segment.start_seconds
                and end <= segment.start_seconds + segment.duration_seconds
                for segment in manifest.segments
            ):
                raise ValueError("cue crosses segment")
            previous_end = end
    except (OSError, RuntimeError, ValueError):
        raise MediaPathError("captions invalid") from None
    return resolved


def _subtitle_filter(captions: Path) -> str:
    escaped = captions.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")
    style = (
        "FontName=Segoe UI,FontSize=16,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00020D1C,BorderStyle=1,Outline=2,Shadow=0,"
        "Alignment=7,MarginL=20,MarginR=200,MarginV=20"
    )
    return f"subtitles=filename='{escaped}':force_style='{style}'"


def build_final_command(
    manifest: VideoManifest,
    video_only: Path,
    captions: Path,
    narration_dir: Path,
    output: Path,
) -> list[str]:
    """Build the deterministic narration mix, caption burn, and final encode."""
    command = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", "-i", str(video_only.resolve())]
    narration_paths: list[Path] = []
    for index, segment in enumerate(manifest.segments):
        narration = narration_dir / f"{index:02d}-{segment.id}.mp3"
        if not _is_file(narration):
            raise MediaPathError("missing narration asset")
        narration_paths.append(narration.resolve())
        command.extend(("-i", str(narration.resolve())))
    command.extend(
        (
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
        )
    )
    audio_filters = []
    for index, segment in enumerate(manifest.segments, start=1):
        delay = segment.start_seconds * 1000
        audio_filters.append(
            f"[{index}:a]atrim=duration={segment.duration_seconds},"
            f"adelay={delay}|{delay},aresample=48000[a{index - 1}]"
        )
    audio_filters.append(
        f"[8:a]atrim=duration={manifest.total_duration_seconds}[bed]"
    )
    inputs = "[bed]" + "".join(f"[a{index}]" for index in range(7))
    audio_filters.append(
        f"{inputs}amix=inputs=8:duration=longest:normalize=0[aout]"
    )
    command.extend(
        (
            "-filter_complex",
            ";".join(audio_filters),
            "-vf",
            _subtitle_filter(captions),
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-t",
            str(manifest.total_duration_seconds),
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-preset",
            "slow",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output.resolve()),
        )
    )
    return command


def _require_public_repository(repository_url: str) -> None:
    url = validate_repository_url(repository_url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "HumanWire-submission-video/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status != 200:
                raise MediaPathError("repository URL unreachable")
    except (OSError, urllib.error.URLError):
        raise MediaPathError("repository URL unreachable") from None


def compose_video(
    manifest: VideoManifest,
    work_root: Path,
    captions: Path,
    narration_dir: Path,
    repository_url: str,
    output: Path,
) -> Path:
    """Render the local cards and deterministic 105-second submission MP4."""
    _require_public_repository(repository_url)
    validated_captions = validate_captions(manifest, captions)
    root = _resolved_path(work_root)
    compose_root = root / "compose"
    compose_root.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    for command in build_local_card_commands(manifest, root, repository_url):
        Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
        _run_ffmpeg(command, "local title card render failed")
    video_only = compose_root / "video-only.mp4"
    commands = build_compose_commands(manifest, root, video_only)
    normalized = [Path(command[-1]) for command in commands[:-1]]
    for command in commands[:-1]:
        Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
        _run_ffmpeg(command, "segment normalization failed")
    concat_file = compose_root / "segments.txt"
    concat_file.write_text(
        "".join(f"file '{path.as_posix()}'\n" for path in normalized),
        encoding="utf-8",
    )
    _run_ffmpeg(commands[-1], "segment concat failed")
    final_command = build_final_command(
        manifest,
        video_only,
        validated_captions,
        narration_dir,
        output,
    )
    _run_ffmpeg(final_command, "final composition failed")
    return output.resolve()


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


def _is_redirected_path(path: Path) -> bool:
    """Return whether an existing Windows path is a symlink, junction, or reparse point."""
    failed = False
    redirected = False
    try:
        metadata = path.lstat()
        reparse_point = bool(
            getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        )
        redirected = stat.S_ISLNK(getattr(metadata, "st_mode", 0)) or os.path.isjunction(path) or reparse_point
    except FileNotFoundError:
        redirected = False
    except (OSError, RuntimeError, ValueError):
        failed = True
    if failed:
        raise MediaPathError("media path invalid")
    return redirected


def _trusted_media_root() -> Path:
    repository_root = _resolved_path(REPOSITORY_ROOT)
    literal_root = repository_root / "work" / "caspian-video"
    if MEDIA_WORK_ROOT != literal_root:
        raise MediaPathError("media path invalid")
    if any(_is_redirected_path(component) for component in (repository_root / "work", literal_root)):
        raise MediaPathError("media path invalid")
    configured_root = _resolved_path(MEDIA_WORK_ROOT)
    expected_root = _resolved_path(literal_root)
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
