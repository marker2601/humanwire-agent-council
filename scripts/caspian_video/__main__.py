"""Explicit command-line gates for Caspian submission-video media work."""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
from pathlib import Path

from scripts.caspian_video.models import SpendApproval, VideoManifest
from scripts.caspian_video.openrouter import (
    OpenRouterMediaClient,
    VideoGenerationError,
    _approved_specs,
    generate_approved_assets,
    load_video_settings,
    synthesize_narration_sections,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.caspian_video")
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "generate", "tts"):
        command = subcommands.add_parser(name)
        command.add_argument("--env-file", type=Path, default=Path(".env.video"))
    generate = subcommands.choices["generate"]
    generate.add_argument("--confirm-paid-generation", action="store_true")
    generate.add_argument("--approve-spend-usd")
    generate.add_argument("--work-root", type=Path, default=Path("."))
    tts = subcommands.choices["tts"]
    tts.add_argument("--script", type=Path, default=Path("submission/caspian-video-script.md"))
    tts.add_argument("--manifest", type=Path, default=Path("submission/caspian-video-manifest.json"))
    tts.add_argument("--output-dir", type=Path, default=Path("work/caspian-video/narration"))
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


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        approval = _approval(parser, args) if args.command == "generate" else None
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
            receipts = generate_approved_assets(settings, approval, args.work_root)
            for receipt in receipts:
                print(f"{receipt.name}_cost_usd={receipt.cost_usd}")
            return 0
        manifest = VideoManifest.load(args.manifest)
        client = OpenRouterMediaClient(api_key=settings.api_key)
        synthesize_narration_sections(args.script, manifest, args.output_dir, client=client)
        return 0
    except (ValueError, VideoGenerationError) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
