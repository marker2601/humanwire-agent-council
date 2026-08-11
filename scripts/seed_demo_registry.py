import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from secondsignal.domain import Channel, VerificationRoute, VerifiedIdentity
from secondsignal.identities import RegistryDocument

REQUIRED_ENVIRONMENT = (
    "DEMO_REPORTER_TELEGRAM_ADDRESS",
    "DEMO_REPORTER_EMAIL",
    "DEMO_VERIFIER_EMAIL",
    "DEMO_VERIFIER_TELEGRAM_ADDRESS",
    "DEMO_VERIFIER_TELEGRAM_CONVERSATION",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create SecondSignal's private local demo identity registry."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace the output file if it already exists",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    missing = [name for name in REQUIRED_ENVIRONMENT if not os.getenv(name, "").strip()]
    if missing:
        raise SystemExit("Missing demo registry values: " + ", ".join(missing))

    output = Path(os.getenv("DEMO_REGISTRY_OUTPUT", "data/identities.json"))
    if output.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite existing registry: {output}. Use --force.")

    document = RegistryDocument(
        authorized_reporters={
            Channel.TELEGRAM: [os.environ["DEMO_REPORTER_TELEGRAM_ADDRESS"]],
            Channel.EMAIL: [os.environ["DEMO_REPORTER_EMAIL"]],
        },
        identities=[
            VerifiedIdentity(
                identity_id="asha-rao",
                display_name="Asha Rao",
                aliases=["Asha", "Asha Rao", "CEO"],
                routes=[
                    VerificationRoute(
                        channel=Channel.EMAIL,
                        sender_address=os.environ["DEMO_VERIFIER_EMAIL"],
                        recipient=os.environ["DEMO_VERIFIER_EMAIL"],
                    ),
                    VerificationRoute(
                        channel=Channel.TELEGRAM,
                        sender_address=os.environ["DEMO_VERIFIER_TELEGRAM_ADDRESS"],
                        conversation_id=os.environ["DEMO_VERIFIER_TELEGRAM_CONVERSATION"],
                    ),
                ],
            )
        ],
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"SecondSignal demo registry created at {output}")


if __name__ == "__main__":
    main()
