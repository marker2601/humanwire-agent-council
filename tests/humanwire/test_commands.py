import humanwire
from humanwire.commands import (
    AcknowledgeCommand,
    AvailabilityCommand,
    CancelCommand,
    FreeTextCommand,
    MandateCommand,
    ProposalResponseCommand,
    StatusCommand,
    parse_command,
)
from humanwire.domain import ProposalResponseKind


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
    ]

    for text in invalid_commands:
        assert parse_command(text) == FreeTextCommand(text=text)


def test_unstructured_reply_remains_free_text() -> None:
    assert parse_command("We need 72 hours of notice.") == FreeTextCommand(
        text="We need 72 hours of notice."
    )
