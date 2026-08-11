from secondsignal.domain import CaseState, VerificationCase


def _channel_label(case: VerificationCase) -> str:
    if case.verification_route is None:
        return "Unavailable"
    return f"Registered {case.verification_route.channel.value}"


def render_acknowledgement(case: VerificationCase) -> str:
    signals = ", ".join(case.risk.risk_signals) or "user-requested verification"
    return (
        f"Case {case.token} created.\n"
        "Do not act while verification is pending.\n"
        f"Risk indicators: {signals}.\n"
        f"Independent route: {_channel_label(case)}."
    )


def render_verification_request(case: VerificationCase) -> str:
    return (
        "SECOND SIGNAL VERIFICATION REQUEST\n"
        f"Case: {case.token}\n"
        f"Did you authorize this request? {case.risk.safe_summary}\n\n"
        f"Reply exactly YES {case.token} or NO {case.token}."
    )


def render_receipt(case: VerificationCase) -> str:
    human_response = "NOT RECEIVED"
    verdict = "UNVERIFIED - DO NOT PROCEED WITHOUT MANUAL CONFIRMATION"
    if case.state is CaseState.VERIFIED:
        human_response = "YES"
        verdict = "VERIFIED - REQUEST CONFIRMED"
    elif case.state is CaseState.DENIED:
        human_response = "NO"
        verdict = "DENIED - DO NOT PROCEED"

    duration = "unresolved"
    if case.resolved_at is not None:
        seconds = max(0, round((case.resolved_at - case.created_at).total_seconds()))
        duration = f"{seconds} seconds"

    return (
        "SECOND SIGNAL RECEIPT\n"
        f"Case: {case.token}\n"
        f"Case state: {case.state.value.upper()}\n"
        f"Claimed sender: {case.claimed_identity_name}\n"
        f"Request: {case.risk.requested_action}\n"
        f"Origin: {case.origin_channel.value.title()}\n"
        f"Verified through: {_channel_label(case)}\n"
        f"Human response: {human_response}\n"
        f"Verdict: {verdict}\n"
        f"Resolved in: {duration}"
    )


def render_status(case: VerificationCase) -> str:
    label = case.state.value.replace("_", " ").upper()
    return (
        f"Case {case.token}: {label}\n"
        f"Claimed sender: {case.claimed_identity_name}\n"
        f"Independent route: {_channel_label(case)}"
    )
