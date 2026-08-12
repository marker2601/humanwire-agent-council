import re
from dataclasses import dataclass
from datetime import datetime

from humanwire.domain import (
    AvailabilityWindow,
    EngagementDecisionKind,
    EngagementType,
    ProposalResponseKind,
)

TOKEN = r"HW-[A-Z0-9]{4,8}"
ASCII_CASE_INSENSITIVE = re.IGNORECASE | re.ASCII
PROPOSAL = re.compile(
    r"^(?P<answer>ACCEPT|REJECT|CHANGE)[ \t]+(?P<token>HW-[A-Z0-9]{4,8})"
    r"(?:[ \t]+(?P<change>[^\r\n].*))?$",
    ASCII_CASE_INSENSITIVE,
)
ACKNOWLEDGEMENT = re.compile(
    rf"^ACK[ \t]+(?P<token>{TOKEN})$", ASCII_CASE_INSENSITIVE
)
ENGAGEMENT_DECISION = re.compile(
    rf"^DECIDE[ \t]+(?P<token>{TOKEN})[ \t]+"
    r"(?P<answer>APPROVE|REJECT|CHANGE)(?:[ \t]+(?P<change>[^\r\n].*))?$",
    ASCII_CASE_INSENSITIVE,
)
AVAILABILITY = re.compile(
    rf"^AVAILABLE[ \t]+(?P<token>{TOKEN})[ \t]+(?P<windows>[^\r\n]+)$",
    ASCII_CASE_INSENSITIVE,
)
STATUS = re.compile(rf"^/status[ \t]+(?P<token>{TOKEN})$", ASCII_CASE_INSENSITIVE)
CANCEL = re.compile(rf"^/cancel[ \t]+(?P<token>{TOKEN})$", ASCII_CASE_INSENSITIVE)
GO = re.compile(rf"^GO[ \t]+(?P<token>{TOKEN})$", ASCII_CASE_INSENSITIVE)
ENGAGE = re.compile(
    rf"^ENGAGE[ \t]+(?P<token>{TOKEN})[ \t]+"
    r"(?P<person_id>[A-Z0-9][A-Z0-9._:-]{0,127})[ \t]+"
    r"(?P<engagement_type>INFORM|ACKNOWLEDGE|QUICK_RESPONSE|"
    r"STRUCTURED_INTERVIEW|REVIEW_APPROVAL|AVAILABILITY)$",
    ASCII_CASE_INSENSITIVE,
)
MANDATE = re.compile(
    r"^/mandate[ \t]*\r?\n(?P<body>[\s\S]+)$", ASCII_CASE_INSENSITIVE
)


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
class GoCommand:
    token: str


@dataclass(frozen=True)
class EngageCommand:
    token: str
    person_id: str
    engagement_type: EngagementType


@dataclass(frozen=True)
class ProposalResponseCommand:
    token: str
    response: ProposalResponseKind
    change_text: str | None


@dataclass(frozen=True)
class EngagementDecisionCommand:
    token: str
    response: EngagementDecisionKind
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
    | GoCommand
    | EngageCommand
    | ProposalResponseCommand
    | EngagementDecisionCommand
    | AvailabilityCommand
    | FreeTextCommand
)


def parse_command(text: str) -> ParsedCommand:
    source = text.strip()
    single_line = "\r" not in text and "\n" not in text
    ascii_command_source = text.strip(" \t")
    strict_ascii_command = single_line and text.isascii()

    if match := PROPOSAL.fullmatch(source):
        return ProposalResponseCommand(
            token=match.group("token").upper(),
            response=ProposalResponseKind(match.group("answer").lower()),
            change_text=match.group("change"),
        )
    if match := ACKNOWLEDGEMENT.fullmatch(source):
        return AcknowledgeCommand(token=match.group("token").upper())
    if strict_ascii_command and (match := GO.fullmatch(ascii_command_source)):
        return GoCommand(token=match.group("token").upper())
    if strict_ascii_command and (match := ENGAGE.fullmatch(ascii_command_source)):
        return EngageCommand(
            token=match.group("token").upper(),
            person_id=match.group("person_id"),
            engagement_type=EngagementType(match.group("engagement_type").lower()),
        )
    if single_line and (match := ENGAGEMENT_DECISION.fullmatch(source)):
        response = EngagementDecisionKind(match.group("answer").lower())
        change_text = match.group("change")
        if change_text is not None:
            change_text = change_text.strip()
        if response is EngagementDecisionKind.APPROVE and change_text is not None:
            return FreeTextCommand(text=text)
        if response is EngagementDecisionKind.CHANGE and not change_text:
            return FreeTextCommand(text=text)
        if change_text is not None and len(change_text) > 400:
            return FreeTextCommand(text=text)
        return EngagementDecisionCommand(
            token=match.group("token").upper(),
            response=response,
            change_text=change_text,
        )
    if single_line and (match := AVAILABILITY.fullmatch(source)):
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
