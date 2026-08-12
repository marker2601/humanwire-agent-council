"""Command-line entrypoints for HumanWire."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import uvicorn
from sqlalchemy.engine import make_url

from humanwire.caspian_gateway import CaspianGateway
from humanwire.config import Settings
from humanwire.container import ApplicationContainer, DueActionWorker
from humanwire.database import create_session_factory
from humanwire.logging_config import configure_logging


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


def run_smoke() -> None:
    from scripts.smoke_check import main as smoke_main

    smoke_main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="humanwire",
        description="AI chief of staff that interviews the organization",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("init-db")
    subcommands.add_parser("listen")
    subcommands.add_parser("web")
    subcommands.add_parser("smoke")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    configure_logging()
    args = build_parser().parse_args(argv)
    settings = Settings()
    if args.command == "init-db":
        init_database(settings)
    elif args.command == "listen":
        run_listener(settings)
    elif args.command == "web":
        run_web(settings)
    elif args.command == "smoke":
        run_smoke()
    else:
        raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    main()
