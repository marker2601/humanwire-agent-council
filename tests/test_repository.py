from datetime import timedelta
from uuid import uuid4

import pytest
from conftest import NOW

from secondsignal.domain import CaseEvent, CaseState
from secondsignal.repository import DuplicateCaseError


def test_round_trips_case_and_events(repository, sample_case) -> None:
    repository.add_case(sample_case)
    repository.append_event(
        sample_case.case_id,
        CaseEvent(event_type="case.created", created_at=NOW),
    )

    assert repository.get_by_token(sample_case.token) == sample_case
    assert [event.event_type for event in repository.list_events(sample_case.case_id)] == [
        "case.created"
    ]


def test_idempotency_key_is_unique(repository, sample_case) -> None:
    repository.add_case(sample_case)
    duplicate = sample_case.model_copy(update={"case_id": uuid4(), "token": "SS-ABC123"})

    with pytest.raises(DuplicateCaseError):
        repository.add_case(duplicate)


def test_saves_state_transition(repository, sample_case) -> None:
    repository.add_case(sample_case)
    denied = sample_case.model_copy(
        update={"state": CaseState.DENIED, "reason": "human_denied", "resolved_at": NOW}
    )

    repository.save_case(denied)

    assert repository.get_by_token(sample_case.token) == denied


def test_lists_only_due_pending_cases(repository, make_case) -> None:
    due = make_case(expires_at=NOW - timedelta(seconds=1))
    future = make_case(
        case_id=uuid4(),
        token="SS-FUTURE",
        idempotency_key="idem-future",
        expires_at=NOW + timedelta(seconds=1),
    )
    resolved = make_case(
        case_id=uuid4(),
        token="SS-DENIED",
        idempotency_key="idem-denied",
        expires_at=NOW - timedelta(seconds=1),
        state=CaseState.DENIED,
        resolved_at=NOW,
    )
    for case in (due, future, resolved):
        repository.add_case(case)

    assert repository.list_expired_pending(NOW) == [due]


def test_round_trips_runtime_status(repository) -> None:
    repository.set_runtime_status("channel.email", "ready", NOW)

    assert repository.get_runtime_status("channel.email") == ("ready", NOW)
