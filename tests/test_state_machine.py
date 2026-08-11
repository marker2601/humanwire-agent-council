from datetime import UTC, datetime

import pytest

from secondsignal.domain import CaseState
from secondsignal.state_machine import CaseStateMachine, InvalidTransitionError

NOW = datetime(2026, 8, 10, 18, 1, tzinfo=UTC)
ALLOWED = {
    CaseState.RECEIVED: {CaseState.ANALYZED, CaseState.UNVERIFIED},
    CaseState.ANALYZED: {CaseState.AWAITING_VERIFICATION, CaseState.UNVERIFIED},
    CaseState.AWAITING_VERIFICATION: {
        CaseState.VERIFIED,
        CaseState.DENIED,
        CaseState.EXPIRED,
        CaseState.CANCELLED,
        CaseState.DELIVERY_FAILED,
    },
}


@pytest.mark.parametrize(
    ("source", "target"),
    [(source, target) for source, targets in ALLOWED.items() for target in targets],
)
def test_allows_documented_transition(make_case, source, target) -> None:
    case = make_case(state=source)

    updated = CaseStateMachine().transition(case, target, "test", NOW)

    assert updated.state is target
    assert updated.reason == "test"


@pytest.mark.parametrize(
    "terminal",
    [
        CaseState.VERIFIED,
        CaseState.DENIED,
        CaseState.UNVERIFIED,
        CaseState.EXPIRED,
        CaseState.CANCELLED,
        CaseState.DELIVERY_FAILED,
    ],
)
def test_terminal_states_are_immutable(make_case, terminal) -> None:
    with pytest.raises(InvalidTransitionError):
        CaseStateMachine().transition(
            make_case(state=terminal),
            CaseState.VERIFIED,
            "late",
            NOW,
        )


def test_terminal_transition_sets_resolution_time(make_case) -> None:
    updated = CaseStateMachine().transition(
        make_case(state=CaseState.AWAITING_VERIFICATION),
        CaseState.DENIED,
        "human_denied",
        NOW,
    )

    assert updated.resolved_at == NOW


def test_nonterminal_transition_does_not_set_resolution_time(make_case) -> None:
    updated = CaseStateMachine().transition(
        make_case(state=CaseState.RECEIVED),
        CaseState.ANALYZED,
        "risk_analyzed",
        NOW,
    )

    assert updated.resolved_at is None
