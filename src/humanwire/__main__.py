"""Command-line entrypoints for HumanWire."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from humanwire.caspian_gateway import CaspianGateway
from humanwire.config import Settings
from humanwire.container import ApplicationContainer, DueActionWorker
from humanwire.database import create_session_factory
from humanwire.logging_config import configure_logging


class _OnceFlag(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None) -> None:
        del values
        if getattr(namespace, self.dest, False):
            parser.error(f"{option_string} may be supplied only once")
        setattr(namespace, self.dest, True)


def _safe_database_url(database_url: str) -> str:
    return make_url(database_url).render_as_string(hide_password=True)


def init_database(settings: Settings) -> None:
    create_session_factory(settings.database_url)
    print(f"HumanWire database initialized: {_safe_database_url(settings.database_url)}")


def run_listener(settings: Settings) -> None:
    container = ApplicationContainer.build(settings)
    gateway = CaspianGateway(
        settings=settings,
        workflow=container.workflow,
        repository=container.repository,
    )
    worker = DueActionWorker(
        workflow=container.workflow,
        gateway=gateway,
        repository=container.repository,
        poll_seconds=settings.due_action_poll_seconds,
    )
    try:
        gateway.connect()
        worker.start()
        gateway.listen()
    finally:
        try:
            worker.stop()
        finally:
            gateway.close()


def run_web(settings: Settings) -> None:
    from humanwire.web import create_app

    container = ApplicationContainer.build(settings)
    app = create_app(container.repository, settings)
    uvicorn.run(app, host=settings.dashboard_host, port=settings.dashboard_port)


def run_smoke(argv: Sequence[str] | None = None) -> int:
    from humanwire.smoke import main as smoke_main

    return smoke_main([] if argv is None else argv)


def _print_synthetic_summary(result) -> None:
    from humanwire.synthetic import semantic_trace_hash

    provenance = result.transcript.scenario.provenance
    trace_hash = semantic_trace_hash(result)
    values = (
        ("proof_class", provenance.proof_class),
        ("actor_type", provenance.actor_type),
        ("identity_source", provenance.identity_source),
        ("transport", provenance.transport),
        ("human_attested", str(provenance.human_attested).lower()),
        ("live_provider_verified", str(provenance.live_provider_verified).lower()),
        ("scenario_id", result.transcript.scenario.scenario_id),
        (
            "run_id",
            f"{result.transcript.scenario.scenario_id}-{result.transcript.digest[:12]}",
        ),
        ("action_count", str(len(result.transcript.actions))),
        ("inbound_attempt_count", str(len(result.inbound_envelopes))),
        ("delivery_count", str(len(result.captured_deliveries))),
        ("terminal_state", result.final_state),
        ("terminal_states", ",".join(result.terminal_states)),
        ("trace_sha256", trace_hash),
    )
    for label, value in values:
        print(f"{label}={value}")


def run_synthetic(args: argparse.Namespace) -> int:
    from humanwire.synthetic import (
        default_synthetic_scenario,
        generate_scenario,
        replay_transcript,
    )

    try:
        if args.synthetic_command == "generate":
            result = generate_scenario(
                default_synthetic_scenario(),
                Path(args.output),
                Path(args.run_root),
            )
        elif args.synthetic_command == "replay":
            result = replay_transcript(Path(args.transcript), Path(args.run_root))
        else:
            raise AssertionError(f"Unhandled synthetic command: {args.synthetic_command}")
    except ValidationError:
        print("synthetic_status=failed", file=sys.stderr)
        print("failure_reason=invalid_transcript", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print("synthetic_status=failed", file=sys.stderr)
        print("failure_reason=invalid_transcript", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 - fail closed without exposing private exception text
        print("synthetic_status=failed", file=sys.stderr)
        print("failure_reason=isolated_run_failed", file=sys.stderr)
        return 1

    _print_synthetic_summary(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="humanwire",
        description="AI chief of staff for adaptive human coordination",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("init-db")
    subcommands.add_parser("listen")
    subcommands.add_parser("web")
    smoke = subcommands.add_parser("smoke")
    smoke.add_argument("--live", nargs=0, action=_OnceFlag, default=False)
    smoke.add_argument("--confirm-live", nargs=0, action=_OnceFlag, default=False)
    synthetic = subcommands.add_parser(
        "synthetic",
        help="run deterministic non-live synthetic proof",
    )
    synthetic_modes = synthetic.add_subparsers(dest="synthetic_command", required=True)
    generate = synthetic_modes.add_parser("generate")
    generate.add_argument("--output", required=True)
    generate.add_argument("--run-root", required=True)
    replay = synthetic_modes.add_parser("replay")
    replay.add_argument("--transcript", required=True)
    replay.add_argument("--run-root", required=True)
    sandbox = subcommands.add_parser(
        "sandbox",
        help="run read-only private sandbox readiness tooling",
    )
    sandbox.add_argument("sandbox_command", choices=("check", "checklist"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    if args.command == "smoke":
        smoke_args = [
            flag
            for enabled, flag in (
                (args.live, "--live"),
                (args.confirm_live, "--confirm-live"),
            )
            if enabled
        ]
        return run_smoke(smoke_args)
    if args.command == "synthetic":
        return run_synthetic(args)
    if args.command == "sandbox":
        from humanwire.sandbox import main as sandbox_main

        return sandbox_main([args.sandbox_command])
    settings = Settings()
    if args.command == "init-db":
        init_database(settings)
        return 0
    elif args.command == "listen":
        run_listener(settings)
        return 0
    elif args.command == "web":
        run_web(settings)
        return 0
    else:
        raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
