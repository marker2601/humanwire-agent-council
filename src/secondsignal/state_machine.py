from datetime import datetime

from secondsignal.domain import CaseState, VerificationCase


class InvalidTransitionError(ValueError):
    """Raised when a verification case attempts an undocumented transition."""


ALLOWED_TRANSITIONS: dict[CaseState, frozenset[CaseState]] = {
    CaseState.RECEIVED: frozenset({CaseState.ANALYZED, CaseState.UNVERIFIED}),
    CaseState.ANALYZED: frozenset({CaseState.AWAITING_VERIFICATION, CaseState.UNVERIFIED}),
    CaseState.AWAITING_VERIFICATION: frozenset(
        {
            CaseState.VERIFIED,
            CaseState.DENIED,
            CaseState.EXPIRED,
            CaseState.CANCELLED,
            CaseState.DELIVERY_FAILED,
        }
    ),
}

TERMINAL_STATES = frozenset(
    {
        CaseState.VERIFIED,
        CaseState.DENIED,
        CaseState.UNVERIFIED,
        CaseState.EXPIRED,
        CaseState.CANCELLED,
        CaseState.DELIVERY_FAILED,
    }
)


class CaseStateMachine:
    def transition(
        self,
        case: VerificationCase,
        target: CaseState,
        reason: str,
        now: datetime,
    ) -> VerificationCase:
        if target not in ALLOWED_TRANSITIONS.get(case.state, frozenset()):
            raise InvalidTransitionError(f"{case.state.value} -> {target.value}")

        return case.model_copy(
            update={
                "state": target,
                "reason": reason,
                "resolved_at": now if target in TERMINAL_STATES else None,
            }
        )
