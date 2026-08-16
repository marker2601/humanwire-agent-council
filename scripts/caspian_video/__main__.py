"""Explicit command-line gates for Caspian submission-video media work."""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
from pathlib import Path

from scripts.caspian_video import media
from scripts.caspian_video.media import (
    MediaPathError,
    cover_regions,
    load_cover_regions,
    run_capture,
    trim_clip,
)
from scripts.caspian_video.models import SpendApproval, VideoManifest
from scripts.caspian_video.openrouter import (
    REPOSITORY_ROOT,
    OpenRouterMediaClient,
    VideoGenerationError,
    _approved_specs,
    generate_approved_assets,
    load_video_settings,
    synthesize_narration_sections,
)

_NARRATION_OUTPUT = Path("work/caspian-video/generated/narration")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.caspian_video")
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "generate", "tts"):
        command = subcommands.add_parser(name)
        command.add_argument("--env-file", type=Path, default=Path(".env.video"))
    generate = subcommands.choices["generate"]
    generate.add_argument("--confirm-paid-generation", action="store_true")
    generate.add_argument("--approve-spend-usd")
    tts = subcommands.choices["tts"]
    tts.add_argument("--script", type=Path, default=Path("submission/caspian-video-script.md"))
    tts.add_argument("--manifest", type=Path, default=Path("submission/caspian-video-manifest.json"))
    tts.add_argument("--output-dir", type=Path, default=_NARRATION_OUTPUT)
    capture = subcommands.add_parser("capture")
    capture.add_argument("--name", required=True)
    trim = subcommands.add_parser("trim")
    trim.add_argument("--source", required=True)
    trim.add_argument("--output", required=True)
    trim.add_argument("--start", required=True, type=float)
    trim.add_argument("--duration", required=True, type=float)
    cover = subcommands.add_parser("cover")
    cover.add_argument("--source", required=True)
    cover.add_argument("--output", required=True)
    cover.add_argument("--regions", default="redactions.json")
    return parser


def _approval(parser: argparse.ArgumentParser, args: argparse.Namespace) -> SpendApproval:
    if not args.confirm_paid_generation or args.approve_spend_usd is None:
        parser.error("generate requires --confirm-paid-generation --approve-spend-usd 3.00")
    try:
        amount = Decimal(args.approve_spend_usd)
    except InvalidOperation:
        parser.error("--approve-spend-usd must equal 3.00")
    try:
        return SpendApproval(approved=True, ceiling_usd=amount)
    except ValueError:
        parser.error("--approve-spend-usd must equal 3.00")
    raise AssertionError("argparse.error exits")


def _narration_output(parser: argparse.ArgumentParser, output_dir: Path) -> Path:
    if output_dir.is_absolute() or ".." in output_dir.parts or output_dir != _NARRATION_OUTPUT:
        parser.error("--output-dir must equal work/caspian-video/generated/narration")
    return output_dir


def _media_argument(parser: argparse.ArgumentParser, value: str) -> Path:
    try:
        return media.safe_media_path(media.MEDIA_WORK_ROOT, value)
    except MediaPathError as exc:
        parser.error(str(exc))
    raise AssertionError("argparse.error exits")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command in {"capture", "trim", "cover"}:
        try:
            if args.command == "capture":
                run_capture(args.name, media.MEDIA_WORK_ROOT.resolve())
            elif args.command == "trim":
                trim_clip(
                    _media_argument(parser, args.source),
                    _media_argument(parser, args.output),
                    start=args.start,
                    duration=args.duration,
                )
            else:
                cover_regions(
                    _media_argument(parser, args.source),
                    _media_argument(parser, args.output),
                    load_cover_regions(_media_argument(parser, args.regions)),
                )
            return 0
        except MediaPathError as exc:
            parser.error(str(exc))
    try:
        approval = _approval(parser, args) if args.command == "generate" else None
        narration_output = _narration_output(parser, args.output_dir) if args.command == "tts" else None
        settings = load_video_settings(args.env_file)
        if args.command == "preflight":
            client = OpenRouterMediaClient(api_key=settings.api_key)
            catalog = client.video_models()
            presenter, stakeholders = _approved_specs(settings)
            print("credential_valid=true")
            print(f"presenter_capable={str(client.model_supports(presenter, catalog)).lower()}")
            print(f"stakeholder_capable={str(client.model_supports(stakeholders, catalog)).lower()}")
            print(f"credit_available={str(client.credit_available()).lower()}")
            return 0
        if args.command == "generate":
            if approval is None:
                raise AssertionError("generate approval is required")
            receipts = generate_approved_assets(settings, approval, REPOSITORY_ROOT)
            for receipt in receipts:
                print(f"{receipt.name}_cost_usd={receipt.cost_usd}")
            return 0
        manifest = VideoManifest.load(args.manifest)
        client = OpenRouterMediaClient(api_key=settings.api_key)
        if narration_output is None:
            raise AssertionError("narration output is required")
        synthesize_narration_sections(args.script, manifest, narration_output, client=client)
        return 0
    except (ValueError, VideoGenerationError) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
