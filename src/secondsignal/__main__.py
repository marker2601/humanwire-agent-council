import argparse
from collections.abc import Sequence
from datetime import UTC, datetime

import uvicorn
from sqlalchemy.engine import make_url

from secondsignal.caspian_gateway import CaspianGateway
from secondsignal.config import Settings
from secondsignal.container import ApplicationContainer, ExpiryWorker
from secondsignal.database import create_session_factory
from secondsignal.logging_config import configure_logging


def _safe_database_url(database_url: str) -> str:
    return make_url(database_url).render_as_string(hide_password=True)


def init_database(settings: Settings) -> None:
    create_session_factory(settings.database_url)
    print(f"SecondSignal database initialized: {_safe_database_url(settings.database_url)}")


def run_listener(settings: Settings) -> None:
    container = ApplicationContainer.build(settings)
    gateway = CaspianGateway(
        settings=settings,
        workflow=container.workflow,
        repository=container.repository,
    )
    worker = ExpiryWorker(
        workflow=container.workflow,
        gateway=gateway,
        repository=container.repository,
        poll_seconds=settings.expiry_poll_seconds,
    )
    connected = False
    try:
        gateway.connect()
        connected = True
        worker.start()
        gateway.listen()
    finally:
        worker.stop()
        if connected:
            stopped_at = datetime.now(UTC)
            container.repository.set_runtime_status(
                "channel.email",
                "stopped",
                stopped_at,
            )
            container.repository.set_runtime_status(
                "channel.telegram",
                "stopped",
                stopped_at,
            )


def run_web(settings: Settings) -> None:
    from secondsignal.web import create_app

    container = ApplicationContainer.build(settings)
    app = create_app(container.repository, settings)
    uvicorn.run(
        app,
        host=settings.dashboard_host,
        port=settings.dashboard_port,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="secondsignal",
        description="Cross-channel verification for suspicious requests",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("init-db")
    subcommands.add_parser("listen")
    subcommands.add_parser("web")
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
    else:
        raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    main()
