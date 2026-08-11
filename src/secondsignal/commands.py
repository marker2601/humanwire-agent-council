import re
from dataclasses import dataclass, field
from typing import Literal

VERIFY = re.compile(
    r"^/verify[ \t]+(?P<identity>[^\r\n]+)\r?\n(?:\r?\n)?(?P<body>[\s\S]+)$",
    re.IGNORECASE,
)
STATUS = re.compile(r"^/status[ \t]+(?P<token>SS-[A-Z0-9]{6})$", re.IGNORECASE)
CANCEL = re.compile(r"^/cancel[ \t]+(?P<token>SS-[A-Z0-9]{6})$", re.IGNORECASE)
RESPONSE = re.compile(
    r"^(?P<answer>YES|NO)[ \t]+(?P<token>SS-[A-Z0-9]{6})$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class VerifyCommand:
    claimed_identity: str
    request_text: str
    kind: Literal["verify"] = field(default="verify", init=False)


@dataclass(frozen=True)
class StatusCommand:
    token: str
    kind: Literal["status"] = field(default="status", init=False)


@dataclass(frozen=True)
class CancelCommand:
    token: str
    kind: Literal["cancel"] = field(default="cancel", init=False)


@dataclass(frozen=True)
class VerificationResponse:
    token: str
    approved: bool
    kind: Literal["verification_response"] = field(
        default="verification_response",
        init=False,
    )


@dataclass(frozen=True)
class UnsupportedCommand:
    kind: Literal["unsupported"] = field(default="unsupported", init=False)


type ParsedCommand = (
    VerifyCommand | StatusCommand | CancelCommand | VerificationResponse | UnsupportedCommand
)


def parse_command(text: str) -> ParsedCommand:
    source = text.strip()

    if match := RESPONSE.fullmatch(source):
        return VerificationResponse(
            token=match.group("token").upper(),
            approved=match.group("answer").upper() == "YES",
        )
    if match := STATUS.fullmatch(source):
        return StatusCommand(token=match.group("token").upper())
    if match := CANCEL.fullmatch(source):
        return CancelCommand(token=match.group("token").upper())
    if match := VERIFY.fullmatch(source):
        identity = match.group("identity").strip()
        body = match.group("body").strip()
        if identity and body:
            return VerifyCommand(claimed_identity=identity, request_text=body)
    return UnsupportedCommand()
