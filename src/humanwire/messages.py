"""Channel-neutral text for HumanWire's stakeholder interview workflow."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from humanwire.domain import MeetingPackage, Proposal
from humanwire.evidence import ShareableEvidence
from humanwire.redaction import redact_sensitive

if TYPE_CHECKING:
    from humanwire.meetings import MeetingCoordinator


def render_interview_intro(
    token: str, mandate_summary: str, reason: str, question_count: int
) -> str:
    return (
        f"HUMANWIRE INTERVIEW · {token}\n\n"
        f"Mandate: {mandate_summary}\n"
        f"Why you were contacted: {reason}\n"
        f"This interview has {question_count} question{'s' if question_count != 1 else ''}.\n\n"
        "Reply normally. Prefix an answer with SHAREABLE, ANONYMOUS, or PRIVATE to choose "
        "how that answer may be used.\n\n"
        f"Reply ACK {token} to begin."
    )


def render_question(question: str, index: int, total: int) -> str:
    return f"Question {index} of {total}:\n{question}"


def render_reminder(token: str) -> str:
    return (
        f"HUMANWIRE INTERVIEW · {token}\n\n"
        f"Please acknowledge this interview when ready: ACK {token}."
    )


def render_channel_switch(token: str, mandate_summary: str, reason: str, question_count: int) -> str:
    return (
        f"HUMANWIRE INTERVIEW · {token}\n\n"
        "The previous channel did not receive an acknowledgement. The same interview will "
        "continue here.\n\n"
        f"Mandate: {mandate_summary}\n"
        f"Why you were contacted: {reason}\n"
        f"This interview has {question_count} question{'s' if question_count != 1 else ''}.\n\n"
        "Reply normally. Prefix an answer with SHAREABLE, ANONYMOUS, or PRIVATE to choose "
        "how that answer may be used.\n\n"
        f"Reply ACK {token} to continue."
    )


def render_unreachable_notice(token: str, stakeholder_name: str) -> str:
    return (
        f"HUMANWIRE STATUS · {token}\n\n"
        f"{stakeholder_name} did not acknowledge the registered interview routes. "
        "No agreement, approval, or interview response was recorded."
    )


def render_proposal(
    token: str,
    proposal: Proposal,
    evidence: Iterable[ShareableEvidence],
    deadline: datetime | None = None,
) -> str:
    """Render only shared evidence; anonymous sources are never named."""
    summaries = [
        item.statement
        for item in evidence
        if isinstance(item, ShareableEvidence) and item.status.value == "confirmed"
    ]
    evidence_summary = "\n".join(f"- {summary}" for summary in summaries[:5])
    if not evidence_summary:
        evidence_summary = "- No confirmed shared evidence summary is available."
    due = deadline or proposal.expires_at
    return (
        f"HUMANWIRE DRAFT PROPOSAL · {token}\n\n"
        f"Round {proposal.round_number} of 2\n"
        f"Deadline: {due.isoformat()}\n\n"
        f"Related shared evidence:\n{evidence_summary}\n\n"
        f"{proposal.text}\n\n"
        f"Reply ACCEPT {token}\n"
        f"Reply REJECT {token}\n"
        f"Reply CHANGE {token} <requested change>"
    )


def render_alignment_brief(token: str, evidence: Iterable[ShareableEvidence]) -> str:
    """Render a durable public-only summary; private evidence never crosses this boundary."""
    statements = [
        item.statement
        for item in evidence
        if isinstance(item, ShareableEvidence) and item.status.value == "confirmed"
    ]
    summary = "\n".join(f"- {statement}" for statement in statements[:5])
    return (
        f"HUMANWIRE ALIGNMENT BRIEF · {token}\n\n"
        f"Confirmed shared inputs:\n{summary or '- No confirmed shared inputs were recorded.'}\n\n"
        "This is a recorded alignment summary, not a new approval request."
    )


def render_availability_request(token: str, purpose: str) -> str:
    """Ask for timezone-aware availability without exposing routes or private evidence."""
    return (
        f"HUMANWIRE AVAILABILITY REQUEST \u00b7 {token}\n\n"
        f"Purpose: {redact_sensitive(purpose)}\n\n"
        "Reply AVAILABLE "
        f"{token} <start>/<end> [<start>/<end> ...] using ISO-8601 timestamps with offsets."
    )


def render_meeting_confirmation(
    token: str,
    package: MeetingPackage,
    *,
    acknowledged_attendee_ids: Iterable[str] = (),
    coordinator: MeetingCoordinator | None = None,
) -> str:
    """Render a proposed meeting until every required attendee acknowledges its slot."""
    acknowledged = set(acknowledged_attendee_ids)
    verified = _has_verified_slot(package, coordinator)
    confirmed = verified and set(package.required_attendee_ids).issubset(acknowledged)
    label = "Meeting confirmed" if confirmed else "Proposed meeting"
    return (
        f"HUMANWIRE {label.upper()} \u00b7 {token}\n\n"
        f"{label}: {_slot_text(package, verified)}\n"
        f"Purpose: {redact_sensitive(package.purpose)}\n\n"
        "Agenda:\n" + "\n".join(package.agenda)
    )


def render_meeting_reminder(
    token: str, package: MeetingPackage, *, coordinator: MeetingCoordinator | None = None
) -> str:
    """Render a privacy-safe reminder for the local calendar artifact."""
    verified = _has_verified_slot(package, coordinator)
    return (
        f"HUMANWIRE MEETING REMINDER \u00b7 {token}\n\n"
        f"Proposed meeting: {_slot_text(package, verified)}\n"
        f"Purpose: {redact_sensitive(package.purpose)}"
    )


def _has_verified_slot(
    package: MeetingPackage, coordinator: MeetingCoordinator | None
) -> bool:
    return coordinator is not None and coordinator.has_current_verified_package(package)


def _slot_text(package: MeetingPackage, verified: bool) -> str:
    if not verified:
        return "awaiting confirmed availability"
    return (
        f"{package.proposed_start.astimezone(UTC).isoformat()} to "
        f"{package.proposed_end.astimezone(UTC).isoformat()}"
    )
