"""Channel-neutral text for HumanWire's stakeholder interview workflow."""

from collections.abc import Iterable
from datetime import datetime

from humanwire.domain import Proposal
from humanwire.evidence import ShareableEvidence


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
