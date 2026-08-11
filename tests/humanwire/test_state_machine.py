from datetime import timedelta
from uuid import uuid4

import pytest

from humanwire.domain import (
    Channel,
    Direction,
    Mandate,
    MandateState,
    StakeholderAssignment,
    StakeholderState,
)
from humanwire.state_machine import (
    InvalidTransitionError,
    MandateStateMachine,
    StakeholderStateMachine,
)

MANDATE_TRANSITIONS = {
    MandateState.RECEIVED: {MandateState.PLANNED},
    MandateState.PLANNED: {MandateState.INTERVIEWING},
    MandateState.INTERVIEWING: {MandateState.SYNTHESIZING, MandateState.PARTIAL},
    MandateState.SYNTHESIZING: {MandateState.ALIGNED, MandateState.NEGOTIATING},
    MandateState.NEGOTIATING: {MandateState.ALIGNED, MandateState.MEETING_REQUIRED},
    MandateState.MEETING_REQUIRED: {MandateState.SCHEDULING},
    MandateState.SCHEDULING: {MandateState.MEETING_READY, MandateState.PARTIAL},
}
MANDATE_TERMINALS = (
    MandateState.ALIGNED,
    MandateState.MEETING_READY,
    MandateState.PARTIAL,
    MandateState.EXPIRED,
    MandateState.CANCELLED,
    MandateState.DELIVERY_FAILED,
)


@pytest.fixture
def make_mandate(now):
    def factory(**updates):
        values = {
            "mandate_id": uuid4(),
            "token": "HW-STATE",
            "initiator_id": "manager",
            "origin_channel": Channel.TELEGRAM,
            "origin_conversation_id": "manager-conversation",
            "origin_message_id": "message-1",
            "redacted_request": "Prepare the staffing proposal",
            "objective": "Align staffing plan",
            "plan": {
                "objective": "Align staffing plan",
                "required_decisions": ["Approve the plan"],
                "stakeholders": [
                    {
                        "person_ref": "lead",
                        "reason": "Owns delivery",
                        "direction": Direction.DOWNWARD,
                        "questions": ["Capacity?"],
                    }
                ],
                "completion_conditions": ["All required people respond"],
            },
            "state": MandateState.RECEIVED,
            "created_at": now,
            "updated_at": now,
            "expires_at": now + timedelta(days=1),
            "idempotency_key": "mandate:state",
        }
        values.update(updates)
        return Mandate(**values)

    return factory


@pytest.fixture
def make_assignment(now):
    def factory(**updates):
        values = {
            "assignment_id": uuid4(),
            "mandate_id": uuid4(),
            "person_id": "team-lead",
            "department": "Operations",
            "direction": Direction.DOWNWARD,
            "reason": "Owns delivery",
            "required": True,
            "state": StakeholderState.NOT_CONTACTED,
            "route_ids": ["team-lead-email"],
        }
        values.update(updates)
        return StakeholderAssignment(**values)

    return factory


@pytest.mark.parametrize(
    ("source", "target"),
    [(source, target) for source, targets in MANDATE_TRANSITIONS.items() for target in targets],
)
def test_mandate_allows_documented_normal_transition(make_mandate, now, source, target) -> None:
    updated = MandateStateMachine().transition(make_mandate(state=source), target, "progress", now)

    assert updated.state is target
    assert updated.reason == "progress"
    assert updated.completed_at == (now if target in MANDATE_TERMINALS else None)


@pytest.mark.parametrize("terminal", MANDATE_TERMINALS)
def test_terminal_mandate_is_immutable(make_mandate, now, terminal) -> None:
    mandate = make_mandate(state=terminal)
    with pytest.raises(InvalidTransitionError, match=f"{terminal.value} -> interviewing"):
        MandateStateMachine().transition(mandate, MandateState.INTERVIEWING, "reopen", now)


def test_mandate_cancellation_is_allowed_only_from_nonterminal_states(make_mandate, now) -> None:
    updated = MandateStateMachine().transition(
        make_mandate(state=MandateState.PLANNED), MandateState.CANCELLED, "owner_cancelled", now
    )

    assert updated.completed_at == now


def test_mandate_invalid_transition_names_source_and_target(make_mandate, now) -> None:
    with pytest.raises(InvalidTransitionError, match="received -> aligned"):
        MandateStateMachine().transition(
            make_mandate(state=MandateState.RECEIVED), MandateState.ALIGNED, "skip", now
        )


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (StakeholderState.NOT_CONTACTED, StakeholderState.CONTACT_QUEUED),
        (StakeholderState.CONTACT_QUEUED, StakeholderState.DELIVERED),
        (StakeholderState.DELIVERED, StakeholderState.AWAITING_ACKNOWLEDGEMENT),
        (StakeholderState.AWAITING_ACKNOWLEDGEMENT, StakeholderState.ACKNOWLEDGED),
        (StakeholderState.AWAITING_ACKNOWLEDGEMENT, StakeholderState.FOLLOW_UP_DUE),
        (StakeholderState.ACKNOWLEDGED, StakeholderState.INTERVIEWING),
        (StakeholderState.INTERVIEWING, StakeholderState.COMPLETE),
        (StakeholderState.FOLLOW_UP_DUE, StakeholderState.ALTERNATE_CHANNEL),
        (StakeholderState.ALTERNATE_CHANNEL, StakeholderState.AWAITING_ACKNOWLEDGEMENT),
    ],
)
def test_stakeholder_allows_response_ladder_transition(
    make_assignment, now, source, target
) -> None:
    updated = StakeholderStateMachine().transition(
        make_assignment(state=source), target, "progress", now
    )

    assert updated.state is target
    assert updated.completed_at == (now if target is StakeholderState.COMPLETE else None)


@pytest.mark.parametrize(
    "terminal",
    (
        StakeholderState.COMPLETE,
        StakeholderState.DECLINED,
        StakeholderState.UNREACHABLE,
        StakeholderState.DELIVERY_FAILED,
    ),
)
def test_terminal_assignment_is_immutable(make_assignment, now, terminal) -> None:
    with pytest.raises(InvalidTransitionError):
        StakeholderStateMachine().transition(
            make_assignment(state=terminal), StakeholderState.INTERVIEWING, "reopen", now
        )


def test_required_approver_cannot_be_completed_from_unreachable(make_assignment, now) -> None:
    assignment = make_assignment(required=True, state=StakeholderState.UNREACHABLE)
    with pytest.raises(InvalidTransitionError):
        StakeholderStateMachine().transition(assignment, StakeholderState.COMPLETE, "forced", now)


def test_assignment_terminal_transition_sets_completed_at(make_assignment, now) -> None:
    updated = StakeholderStateMachine().transition(
        make_assignment(state=StakeholderState.INTERVIEWING),
        StakeholderState.COMPLETE,
        "interview_complete",
        now,
    )

    assert updated.completed_at == now
    assert updated.next_action_at is None


def test_assignment_nonterminal_transition_clears_completed_at(make_assignment, now) -> None:
    updated = StakeholderStateMachine().transition(
        make_assignment(
            state=StakeholderState.DELIVERED,
            completed_at=now - timedelta(seconds=1),
        ),
        StakeholderState.AWAITING_ACKNOWLEDGEMENT,
        "delivered",
        now,
    )

    assert updated.completed_at is None
