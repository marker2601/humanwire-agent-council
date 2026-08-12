from datetime import datetime

from humanwire.domain import Mandate, MandateState, StakeholderAssignment, StakeholderState


class InvalidTransitionError(ValueError):
    """Raised when a HumanWire aggregate attempts an undocumented transition."""


MANDATE_TERMINAL_STATES = frozenset(
    {
        MandateState.ALIGNED,
        MandateState.MEETING_READY,
        MandateState.PARTIAL,
        MandateState.EXPIRED,
        MandateState.CANCELLED,
        MandateState.DELIVERY_FAILED,
    }
)
MANDATE_TRANSITIONS: dict[MandateState, frozenset[MandateState]] = {
    MandateState.RECEIVED: frozenset(
        {
            MandateState.PLANNED,
            MandateState.CANCELLED,
            MandateState.EXPIRED,
            MandateState.DELIVERY_FAILED,
        }
    ),
    MandateState.PLANNED: frozenset(
        {
            MandateState.INTERVIEWING,
            MandateState.CANCELLED,
            MandateState.EXPIRED,
            MandateState.DELIVERY_FAILED,
        }
    ),
    MandateState.INTERVIEWING: frozenset(
        {
            MandateState.SYNTHESIZING,
            MandateState.PARTIAL,
            MandateState.CANCELLED,
            MandateState.EXPIRED,
            MandateState.DELIVERY_FAILED,
        }
    ),
    MandateState.SYNTHESIZING: frozenset(
        {
            MandateState.ALIGNED,
            MandateState.NEGOTIATING,
            MandateState.CANCELLED,
            MandateState.EXPIRED,
            MandateState.DELIVERY_FAILED,
        }
    ),
    MandateState.NEGOTIATING: frozenset(
        {
            MandateState.ALIGNED,
            MandateState.MEETING_REQUIRED,
            MandateState.CANCELLED,
            MandateState.EXPIRED,
            MandateState.DELIVERY_FAILED,
        }
    ),
    MandateState.MEETING_REQUIRED: frozenset(
        {
            MandateState.SCHEDULING,
            MandateState.CANCELLED,
            MandateState.EXPIRED,
            MandateState.DELIVERY_FAILED,
        }
    ),
    MandateState.SCHEDULING: frozenset(
        {
            MandateState.MEETING_READY,
            MandateState.PARTIAL,
            MandateState.CANCELLED,
            MandateState.EXPIRED,
            MandateState.DELIVERY_FAILED,
        }
    ),
}

ASSIGNMENT_TERMINAL_STATES = frozenset(
    {
        StakeholderState.COMPLETE,
        StakeholderState.DECLINED,
        StakeholderState.UNREACHABLE,
        StakeholderState.DELIVERY_FAILED,
    }
)
ASSIGNMENT_TRANSITIONS: dict[StakeholderState, frozenset[StakeholderState]] = {
    StakeholderState.NOT_CONTACTED: frozenset(
        {StakeholderState.CONTACT_QUEUED, StakeholderState.DELIVERY_FAILED}
    ),
    StakeholderState.CONTACT_QUEUED: frozenset(
        {StakeholderState.DELIVERED, StakeholderState.DELIVERY_FAILED}
    ),
    StakeholderState.DELIVERED: frozenset(
        {
            StakeholderState.AWAITING_ACKNOWLEDGEMENT,
            StakeholderState.COMPLETE,
            StakeholderState.DELIVERY_FAILED,
        }
    ),
    StakeholderState.AWAITING_ACKNOWLEDGEMENT: frozenset(
        {
            StakeholderState.ACKNOWLEDGED,
            StakeholderState.COMPLETE,
            StakeholderState.FOLLOW_UP_DUE,
            StakeholderState.DECLINED,
            StakeholderState.ALTERNATE_CHANNEL,
            StakeholderState.DELIVERY_FAILED,
        }
    ),
    StakeholderState.ACKNOWLEDGED: frozenset(
        {
            StakeholderState.INTERVIEWING,
            StakeholderState.COMPLETE,
            StakeholderState.DECLINED,
        }
    ),
    StakeholderState.INTERVIEWING: frozenset(
        {StakeholderState.COMPLETE, StakeholderState.DECLINED}
    ),
    StakeholderState.FOLLOW_UP_DUE: frozenset(
        {
            StakeholderState.AWAITING_ACKNOWLEDGEMENT,
            StakeholderState.ALTERNATE_CHANNEL,
            StakeholderState.UNREACHABLE,
            StakeholderState.DELIVERY_FAILED,
        }
    ),
    StakeholderState.ALTERNATE_CHANNEL: frozenset(
        {
            StakeholderState.AWAITING_ACKNOWLEDGEMENT,
            StakeholderState.UNREACHABLE,
            StakeholderState.DELIVERY_FAILED,
        }
    ),
}


class MandateStateMachine:
    def transition(
        self, mandate: Mandate, target: MandateState, reason: str, now: datetime
    ) -> Mandate:
        if target not in MANDATE_TRANSITIONS.get(mandate.state, frozenset()):
            raise InvalidTransitionError(f"{mandate.state.value} -> {target.value}")
        terminal = target in MANDATE_TERMINAL_STATES
        return mandate.model_copy(
            update={
                "state": target,
                "reason": reason,
                "updated_at": now,
                "next_action_at": None if terminal else mandate.next_action_at,
                "completed_at": now if terminal else None,
            }
        )


class StakeholderStateMachine:
    def transition(
        self,
        assignment: StakeholderAssignment,
        target: StakeholderState,
        reason: str,
        now: datetime,
    ) -> StakeholderAssignment:
        if target not in ASSIGNMENT_TRANSITIONS.get(assignment.state, frozenset()):
            raise InvalidTransitionError(f"{assignment.state.value} -> {target.value}")
        terminal = target in ASSIGNMENT_TERMINAL_STATES
        return assignment.model_copy(
            update={
                "state": target,
                "next_action_at": None if terminal else assignment.next_action_at,
                "completed_at": now if terminal else None,
                "failure_reason": reason
                if target
                in {
                    StakeholderState.DECLINED,
                    StakeholderState.UNREACHABLE,
                    StakeholderState.DELIVERY_FAILED,
                }
                else assignment.failure_reason,
            }
        )
