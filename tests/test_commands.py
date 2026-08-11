from secondsignal.commands import (
    CancelCommand,
    StatusCommand,
    VerificationResponse,
    VerifyCommand,
    parse_command,
)


def test_parses_multiline_verify_command() -> None:
    command = parse_command("/verify Asha Rao\n\nBuy five $100 gift cards now.")
    assert command == VerifyCommand(
        claimed_identity="Asha Rao",
        request_text="Buy five $100 gift cards now.",
    )


def test_parses_case_commands_case_insensitively() -> None:
    assert parse_command("/status ss-7k4p2m") == StatusCommand(token="SS-7K4P2M")
    assert parse_command("/cancel SS-7K4P2M") == CancelCommand(token="SS-7K4P2M")
    assert parse_command("no ss-7k4p2m") == VerificationResponse(
        token="SS-7K4P2M",
        approved=False,
    )


def test_rejects_extra_words_in_verification_response() -> None:
    assert parse_command("NO SS-7K4P2M because it is fake").kind == "unsupported"


def test_rejects_verify_command_without_request_body() -> None:
    assert parse_command("/verify Asha Rao").kind == "unsupported"
