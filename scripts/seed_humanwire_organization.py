import argparse
import json
import os
from collections.abc import Sequence

from humanwire.config import Settings
from humanwire.directory import InitiatorPolicy, OrganizationDocument
from humanwire.domain import Channel, ContactRoute, Direction, Person

PEOPLE = (
    ("ceo", "Jordan Lee", "CEO", "Executive", None, "America/Chicago"),
    ("coo", "Maya Chen", "COO", "Executive", "ceo", "America/Chicago"),
    ("vp-support", "Nora Williams", "VP Support", "Support", "coo", "America/Chicago"),
    (
        "support-manager",
        "Arun Patel",
        "Support Manager",
        "Support",
        "vp-support",
        "America/Chicago",
    ),
    (
        "us-team-lead",
        "US Team Lead",
        "US Team Lead",
        "Support",
        "support-manager",
        "America/Chicago",
    ),
    (
        "apac-team-lead",
        "APAC Team Lead",
        "APAC Team Lead",
        "Support",
        "support-manager",
        "Asia/Singapore",
    ),
    ("vp-people", "Priya Raman", "VP People", "People", "coo", "America/Chicago"),
)

POLICIES = (
    ("ceo", {"Executive", "Support", "People"}, 0),
    ("coo", {"Executive", "Support", "People"}, 1),
    ("vp-support", {"Support", "People"}, 1),
    ("support-manager", {"Support", "People"}, 1),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create HumanWire's local organization directory.")
    parser.add_argument("--force", action="store_true", help="replace the organization file")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = Settings()
    required = _required_environment()
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        raise SystemExit("Missing organization contact values: " + ", ".join(missing))
    if settings.organization_path.exists() and not args.force:
        raise SystemExit("Refusing to overwrite existing organization. Use --force.")

    document = OrganizationDocument(
        people=[_person(*details) for details in PEOPLE],
        initiator_policies=[
            InitiatorPolicy(
                person_id=person_id,
                allowed_directions={Direction.DOWNWARD, Direction.LATERAL, Direction.UPWARD},
                allowed_departments=departments,
                max_upward_levels=max_upward_levels,
            )
            for person_id, departments, max_upward_levels in POLICIES
        ],
    )
    settings.organization_path.parent.mkdir(parents=True, exist_ok=True)
    settings.organization_path.write_text(
        json.dumps(document.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
    )
    print("HumanWire organization directory created.")


def _person(
    person_id: str,
    display_name: str,
    role: str,
    department: str,
    manager_id: str | None,
    timezone: str,
) -> Person:
    env_prefix = "HUMANWIRE_" + person_id.replace("-", "_").upper()
    email = os.environ[f"{env_prefix}_EMAIL"]
    telegram_address = os.environ[f"{env_prefix}_TELEGRAM_ADDRESS"]
    telegram_conversation = os.environ[f"{env_prefix}_TELEGRAM_CONVERSATION"]
    return Person(
        person_id=person_id,
        display_name=display_name,
        aliases=[display_name.split()[0]],
        role=role,
        department=department,
        timezone=timezone,
        manager_id=manager_id,
        routes=[
            ContactRoute(
                route_id=f"{person_id}-email",
                channel=Channel.EMAIL,
                sender_address=email,
                recipient=email,
                preferred=True,
            ),
            ContactRoute(
                route_id=f"{person_id}-telegram",
                channel=Channel.TELEGRAM,
                sender_address=telegram_address,
                conversation_id=telegram_conversation,
            ),
        ],
    )


def _required_environment() -> tuple[str, ...]:
    return tuple(
        name
        for person_id, *_ in PEOPLE
        for name in (
            f"HUMANWIRE_{person_id.replace('-', '_').upper()}_EMAIL",
            f"HUMANWIRE_{person_id.replace('-', '_').upper()}_TELEGRAM_ADDRESS",
            f"HUMANWIRE_{person_id.replace('-', '_').upper()}_TELEGRAM_CONVERSATION",
        )
    )


if __name__ == "__main__":
    main()
