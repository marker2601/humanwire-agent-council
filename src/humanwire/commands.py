import re
from dataclasses import dataclass
from datetime import datetime

from humanwire.domain import AvailabilityWindow, ProposalResponseKind

TOKEN = r"HW-[A-Z0-9]{4,8}"
PROPOSAL = re.compile(
    r"^(?P<answer>ACCEPT|REJECT|CHANGE)[ \t]+(?P<token>HW-[A-Z0-9]{4,8})"
    r"(?:[ \t]+(?P<change>[^\r\n].*))?$",
    re.IGNORECASE,
)
ACKNOWLEDGEMENT = re.compile(rf"^ACK[ \t]+(?P<token>{TOKEN})$", re.IGNORECASE)
AVAILABILITY = re.compile(
    rf"^AVAILABLE[ \t]+(?P<token>{TOKEN})[ \t]+(?P<windows>[^\r\n]+)$",
    re.IGNORECASE,
)
STATUS = re.compile(rf"^/status[ \t]+(?P<token>{TOKEN})$", re.IGNORECASE)
CANCEL = re.compile(rf"^/cancel[ \t]+(?P<token>{TOKEN})$", re.IGNORECASE)
MANDATE = re.compile(r"^/mandate[ \t]*\r?\n(?P<body>[\s\S]+)$", re.IGNORECASE)


@dataclass(frozen=True)
class MandateCommand:
    body: str


@dataclass(frozen=True)
class StatusCommand:
    token: str


@dataclass(frozen=True)
class CancelCommand:
    token: str


@dataclass(frozen=True)
class AcknowledgeCommand:
    token: str


@dataclass(frozen=True)
class ProposalResponseCommand:
    token: str
    response: ProposalResponseKind
    change_text: str | None


@dataclass(frozen=True)
class AvailabilityCommand:
    token: str
    windows: tuple[AvailabilityWindow, ...]


@dataclass(frozen=True)
class FreeTextCommand:
    text: str


type ParsedCommand = (
    MandateCommand
    | StatusCommand
    | CancelCommand
    | AcknowledgeCommand
    | ProposalResponseCommand
    | AvailabilityCommand
    | FreeTextCommand
)


def parse_command(text: str) -> ParsedCommand:
    source = text.strip()

    if match := PROPOSAL.fullmatch(source):
        return ProposalResponseCommand(
            token=match.group("token").upper(),
            response=ProposalResponseKind(match.group("answer").lower()),
            change_text=match.group("change"),
        )
    if match := ACKNOWLEDGEMENT.fullmatch(source):
        return AcknowledgeCommand(token=match.group("token").upper())
    if match := AVAILABILITY.fullmatch(source):
        try:
            windows = _parse_windows(match.group("windows"))
        except ValueError:
            return FreeTextCommand(text=text)
        return AvailabilityCommand(token=match.group("token").upper(), windows=windows)
    if match := STATUS.fullmatch(source):
        return StatusCommand(token=match.group("token").upper())
    if match := CANCEL.fullmatch(source):
        return CancelCommand(token=match.group("token").upper())
    if match := MANDATE.fullmatch(source):
        body = match.group("body").strip()
        if body:
            return MandateCommand(body=body)
    return FreeTextCommand(text=text)


def _parse_windows(source: str) -> tuple[AvailabilityWindow, ...]:
    windows = []
    for item in source.split():
        start_text, separator, end_text = item.partition("/")
        if not separator or not start_text or not end_text:
            raise ValueError("availability windows must be slash-delimited")
        windows.append(
            AvailabilityWindow(
                start=datetime.fromisoformat(start_text),
                end=datetime.fromisoformat(end_text),
            )
        )
    if not windows:
        raise ValueError("availability requires a window")
    return tuple(windows)
