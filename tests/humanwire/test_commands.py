import pytest

import humanwire
from humanwire.commands import (
    AcknowledgeCommand,
    AvailabilityCommand,
    CancelCommand,
    EngageCommand,
    EngagementDecisionCommand,
    FreeTextCommand,
    GoCommand,
    MandateCommand,
    ProposalResponseCommand,
    StatusCommand,
    parse_command,
)
from humanwire.domain import EngagementDecisionKind, EngagementType, ProposalResponseKind


def test_imports_the_initialized_production_package() -> None:
    assert humanwire.__version__ == "0.1.0"


def test_parses_multiline_mandate() -> None:
    command = parse_command("/mandate\nCoordinate weekend coverage before Friday.")
    assert command == MandateCommand(body="Coordinate weekend coverage before Friday.")


def test_parses_case_actions_case_insensitively() -> None:
    assert parse_command("/status hw-2411") == StatusCommand(token="HW-2411")
    assert parse_command("/cancel hw-2411") == CancelCommand(token="HW-2411")
    assert parse_command("ACK HW-2411") == AcknowledgeCommand(token="HW-2411")
    assert parse_command("ACCEPT hw-2411") == ProposalResponseCommand(
        token="HW-2411", response=ProposalResponseKind.ACCEPT, change_text=None
    )
    assert parse_command("CHANGE HW-2411 Start Monday") == ProposalResponseCommand(
        token="HW-2411",
        response=ProposalResponseKind.CHANGE,
        change_text="Start Monday",
    )


def test_parses_timezone_aware_availability() -> None:
    command = parse_command(
        "AVAILABLE HW-2411 2026-08-14T15:00:00-05:00/2026-08-14T16:00:00-05:00"
    )
    assert isinstance(command, AvailabilityCommand)
    assert command.token == "HW-2411"
    assert command.windows[0].start.isoformat() == "2026-08-14T15:00:00-05:00"


def test_invalid_availability_remains_free_text() -> None:
    invalid_commands = [
        "AVAILABLE HW-2411 2026-08-14T15:00:00-05:00",
        "AVAILABLE HW-2411 not-a-timestamp/2026-08-14T16:00:00-05:00",
        "AVAILABLE HW-2411 2026-08-14T15:00:00/2026-08-14T16:00:00",
        "AVAILABLE HW-2411 2026-08-14T16:00:00-05:00/2026-08-14T15:00:00-05:00",
        "\nAVAILABLE HW-2411 2026-08-14T15:00:00-05:00/2026-08-14T16:00:00-05:00",
        "AVAILABLE HW-2411 2026-08-14T15:00:00-05:00/2026-08-14T16:00:00-05:00\n",
    ]

    for text in invalid_commands:
        assert parse_command(text) == FreeTextCommand(text=text)


@pytest.mark.parametrize(
    ("text", "response", "change_text"),
    [
        ("DECIDE HW-2411 APPROVE", EngagementDecisionKind.APPROVE, None),
        ("decide hw-2411 reject", EngagementDecisionKind.REJECT, None),
        (
            "DECIDE HW-2411 REJECT Missing authority",
            EngagementDecisionKind.REJECT,
            "Missing authority",
        ),
        (
            "DECIDE HW-2411 CHANGE Add a security review",
            EngagementDecisionKind.CHANGE,
            "Add a security review",
        ),
    ],
)
def test_parses_exact_engagement_decisions_case_insensitively(
    text: str,
    response: EngagementDecisionKind,
    change_text: str | None,
) -> None:
    assert parse_command(text) == EngagementDecisionCommand(
        token="HW-2411",
        response=response,
        change_text=change_text,
    )


def test_malformed_decision_commands_remain_free_text() -> None:
    overlong = "x" * 401
    invalid_commands = [
        "DECIDE HW-2411 APPROVE because yes",
        "DECIDE HW-2411 CHANGE",
        "DECIDE HW-2411 CHANGE   ",
        "DECIDE HW-2411 MAYBE",
        "DECISION HW-2411 APPROVE",
        "DECIDE HW-12 APPROVE",
        "\nDECIDE HW-2411 APPROVE",
        "DECIDE HW-2411 APPROVE\n",
        "DECIDE HW-2411 APPROVE\nDECIDE HW-2411 REJECT",
        f"DECIDE HW-2411 REJECT {overlong}",
        f"DECIDE HW-2411 CHANGE {overlong}",
    ]

    for text in invalid_commands:
        assert parse_command(text) == FreeTextCommand(text=text)


@pytest.mark.parametrize(
    "text",
    [
        "DECİDE HW-2411 APPROVE",
        "AVAİLABLE HW-2411 2026-08-14T15:00:00-05:00/2026-08-14T16:00:00-05:00",
        "DECIDE HW-24İ1 APPROVE",
        "ACCEPT HW-24ı1",
        "ACK HW-2411",
        "/ſtatus HW-2411",
        "/cancel HW-24K1",
    ],
)
def test_ascii_commands_reject_unicode_casefold_lookalikes(text: str) -> None:
    assert parse_command(text) == FreeTextCommand(text=text)


def test_unstructured_reply_remains_free_text() -> None:
    assert parse_command("We need 72 hours of notice.") == FreeTextCommand(
        text="We need 72 hours of notice."
    )


def test_parses_exact_go_and_engage_commands_with_ascii_case_folding() -> None:
    assert parse_command("go hw-2411") == GoCommand(token="HW-2411")
    assert parse_command("EnGaGe hw-2411 team-lead QUICK_RESPONSE") == EngageCommand(
        token="HW-2411",
        person_id="team-lead",
        engagement_type=EngagementType.QUICK_RESPONSE,
    )


@pytest.mark.parametrize(
    "text",
    [
        "GO HW-2411 now",
        "GO HW-12",
        "\nGO HW-2411",
        "GO HW-2411\n",
        "G\u212a HW-2411",
        "GO HW-24\u01301",
        "ENGAGE HW-2411",
        "ENGAGE HW-2411 team-lead",
        "ENGAGE HW-2411 team lead quick_response",
        "ENGAGE HW-2411 team-lead telephone_call",
        "ENGAGE HW-2411 -team-lead inform",
        "ENGAGE HW-2411 team-lead inform trailing",
        "\nENGAGE HW-2411 team-lead inform",
        "ENGAGE HW-2411 team-lead inform\n",
        "ENGA\u0130E HW-2411 team-lead inform",
        "ENGAGE HW-2411 team-\u212alead inform",
        f"ENGAGE HW-2411 {'p' * 129} inform",
    ],
)
def test_malformed_or_unicode_go_and_engage_commands_remain_free_text(text: str) -> None:
    assert parse_command(text) == FreeTextCommand(text=text)
