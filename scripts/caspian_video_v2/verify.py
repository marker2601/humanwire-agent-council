from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path


class VideoVerificationError(ValueError):
    """The rendered master does not satisfy the locked release contract."""


@dataclass(frozen=True, slots=True)
class MasterProbe:
    duration_seconds: float
    atoms: tuple[str, ...]
    width: int
    height: int
    video_codec: str
    pixel_format: str
    frame_rate: float
    audio_codec: str
    sample_rate: int
    channels: int
    decoded_frames: int


@dataclass(frozen=True, slots=True)
class FrameContract:
    product_ratio: float
    truth_badge_ratio: float


@dataclass(frozen=True, slots=True)
class VerificationReport:
    probe: MasterProbe
    frame_contract: FrameContract
    sha256: str


def verify_probe(probe: MasterProbe) -> MasterProbe:
    checks = (
        (79.9 <= probe.duration_seconds <= 80.1, "duration"),
        (probe.atoms == ("ftyp", "moov", "mdat"), "faststart"),
        ((probe.width, probe.height) == (1920, 1080), "canvas"),
        (probe.video_codec == "h264", "video_codec"),
        (probe.pixel_format == "yuv420p", "pixel_format"),
        (abs(probe.frame_rate - 30.0) < 0.001, "frame_rate"),
        (probe.audio_codec == "aac", "audio_codec"),
        (probe.sample_rate == 48_000, "sample_rate"),
        (probe.channels == 2, "channels"),
        (probe.decoded_frames == 2_400, "decoded_frames"),
    )
    failed = [name for passed, name in checks if not passed]
    if failed:
        raise VideoVerificationError("master contract failed: " + ", ".join(failed))
    return probe


def verify_frame_contract(contract: FrameContract) -> FrameContract:
    if contract.product_ratio < 0.80:
        raise VideoVerificationError("product coverage is below 80 percent")
    if contract.truth_badge_ratio != 1.0:
        raise VideoVerificationError("truth badge is not present on every product frame")
    return contract


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VideoVerificationError("media verification command failed") from error
    if result.returncode != 0:
        raise VideoVerificationError("media verification command failed")
    return result


def probe_master(path: Path) -> MasterProbe:
    if not path.is_file():
        raise VideoVerificationError("master is unavailable")

    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-show_entries",
            (
                "format=duration:stream=index,codec_type,codec_name,width,height,"
                "pix_fmt,r_frame_rate,sample_rate,channels,nb_read_frames"
            ),
            "-of",
            "json",
            os.fspath(path),
        ]
    )
    payload = json.loads(result.stdout)
    video = next(
        stream for stream in payload["streams"] if stream["codec_type"] == "video"
    )
    audio = next(
        stream for stream in payload["streams"] if stream["codec_type"] == "audio"
    )

    data = path.read_bytes()
    atom_offsets = {name: data.find(name.encode("ascii")) for name in ("ftyp", "moov", "mdat")}
    if any(offset < 0 for offset in atom_offsets.values()):
        atoms: tuple[str, ...] = ()
    else:
        atoms = tuple(sorted(atom_offsets, key=atom_offsets.__getitem__))

    _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            os.fspath(path),
            "-map",
            "0:v:0",
            "-f",
            "null",
            os.devnull,
        ]
    )

    return MasterProbe(
        duration_seconds=float(payload["format"]["duration"]),
        atoms=atoms,
        width=int(video["width"]),
        height=int(video["height"]),
        video_codec=str(video["codec_name"]),
        pixel_format=str(video["pix_fmt"]),
        frame_rate=float(Fraction(video["r_frame_rate"])),
        audio_codec=str(audio["codec_name"]),
        sample_rate=int(audio["sample_rate"]),
        channels=int(audio["channels"]),
        decoded_frames=int(video["nb_read_frames"]),
    )


def verify_master(path: Path, frame_contract: FrameContract) -> VerificationReport:
    probe = verify_probe(probe_master(path))
    verified_frames = verify_frame_contract(frame_contract)
    digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    return VerificationReport(
        probe=probe,
        frame_contract=verified_frames,
        sha256=digest,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the HumanWire release master")
    parser.add_argument("master", type=Path)
    parser.add_argument("--product-ratio", type=float, default=0.825)
    parser.add_argument("--truth-badge-ratio", type=float, default=1.0)
    args = parser.parse_args()
    report = verify_master(
        args.master,
        FrameContract(
            product_ratio=args.product_ratio,
            truth_badge_ratio=args.truth_badge_ratio,
        ),
    )
    print(json.dumps(asdict(report), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
