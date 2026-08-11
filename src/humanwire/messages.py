"""Channel-neutral text for HumanWire's stakeholder interview workflow."""


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
