from __future__ import annotations

import re
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

from humanwire.persona_runtime import SyntheticGenerationMode
from humanwire.replay_projection import project_replay_labels
from humanwire.synthetic import default_synthetic_scenario, generate_scenario
from humanwire.synthetic_progress import (
    RepositoryProgressObserver,
    SyntheticProgressStore,
    SyntheticRunState,
    SyntheticRuntimeStatus,
    evidence_bundle,
    initial_progress,
)


class BlockingProgressObserver:
    """Block generation only after its real projected snapshot reaches a saved boundary."""

    def __init__(self, store: SyntheticProgressStore, release_after_event_count: int) -> None:
        self._delegate = RepositoryProgressObserver(store)
        self._release_after_event_count = release_after_event_count
        self._blocked = threading.Event()
        self._release = threading.Event()
        self._snapshot = None
        self._published_counts: list[int] = []

    def capture(self, *args: object, **kwargs: object) -> None:
        self._delegate.capture(*args, **kwargs)
        snapshot = self._delegate.snapshot()
        self._published_counts.append(snapshot.saved_event_count)
        if (
            not self._blocked.is_set()
            and snapshot.saved_event_count >= self._release_after_event_count
        ):
            self._snapshot = snapshot
            self._blocked.set()
            self._release.wait(timeout=5)

    def mark_unavailable(self) -> None:
        self._delegate.mark_unavailable()

    def record_inert_attempt(self, **kwargs: object) -> None:
        self._delegate.record_inert_attempt(**kwargs)

    def wait_for_block(self, timeout: float):
        assert self._blocked.wait(timeout)
        assert self._snapshot is not None
        return self._snapshot

    def release(self) -> None:
        self._release.set()

    @property
    def published_counts(self) -> tuple[int, ...]:
        return tuple(self._published_counts)


def test_shared_labels_preserve_existing_reach_contract() -> None:
    """Break caught: Reach and progress drift on a persisted evidence label."""
    labels = project_replay_labels("interview.evidence_confirmed", "Avery Chen")

    assert labels.model_dump() == {
        "stage": "Evidence",
        "source": "Avery Chen",
        "destination": "HumanWire",
        "data_point": "Evidence confirmed",
    }


def test_mid_run_snapshot_contains_only_persisted_steps(tmp_path) -> None:
    """Break caught: projection leaks predicted steps before workflow persistence."""
    scenario = default_synthetic_scenario(seed=8)
    store = SyntheticProgressStore(initial_progress(scenario))
    observer = BlockingProgressObserver(store, release_after_event_count=5)
    worker = threading.Thread(
        target=generate_scenario,
        kwargs={
            "scenario": scenario,
            "output_path": tmp_path / "run" / "transcript.json",
            "run_root": tmp_path / "run",
            "progress_observer": observer,
        },
    )
    worker.start()

    snapshot = observer.wait_for_block(timeout=5)
    assert snapshot.run_state == SyntheticRunState.RUNNING
    assert snapshot.saved_event_count >= 5
    assert len(snapshot.events) == snapshot.saved_event_count
    assert all(event.effect == "persisted" for event in snapshot.events)
    assert [event.persisted_ordinal for event in snapshot.events] == list(
        range(1, snapshot.saved_event_count + 1)
    )
    assert snapshot.final_trace_sha256 is None
    assert observer.published_counts[-2] < 5

    observer.release()
    worker.join(timeout=10)
    assert not worker.is_alive()


@pytest.fixture
def completed_progress(tmp_path):
    """Produce a completed snapshot through the real offline workflow boundary."""
    scenario = default_synthetic_scenario(seed=19)
    store = SyntheticProgressStore(initial_progress(scenario))
    observer = RepositoryProgressObserver(store)
    generate_scenario(
        scenario,
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        progress_observer=observer,
    )
    return observer.snapshot()


def test_progress_json_has_no_private_or_identity_fields(completed_progress) -> None:
    """Break caught: a progress projection serializes a private workflow identifier."""
    raw = completed_progress.model_dump_json()
    forbidden = re.compile(
        r"PRIVATE-PERSONA-SENTINEL|api[_-]?key|route|address|destination_id|"
        r"conversation|connection|message_id|assignment_id|database|"
        r"[0-9a-f]{8}-[0-9a-f-]{27,}",
        re.IGNORECASE,
    )

    assert forbidden.search(raw) is None


def test_terminal_evidence_uses_the_scenario_identity_seed(completed_progress) -> None:
    """Break caught: terminal evidence loses its persisted synthetic seed provenance."""
    evidence = evidence_bundle(completed_progress)

    assert evidence is not None
    assert evidence.identity_seed == 19
    assert evidence.trace_sha256 == completed_progress.final_trace_sha256


def test_store_returns_immutable_independent_snapshots() -> None:
    """Break caught: a caller can mutate a published snapshot shared with another reader."""
    store = SyntheticProgressStore(initial_progress(default_synthetic_scenario(seed=5)))
    first = store.snapshot()
    second = store.snapshot()

    assert first is not second
    with pytest.raises(ValidationError):
        first.run_state = SyntheticRunState.COMPLETE
    assert second.run_state == SyntheticRunState.STARTING


def test_invalid_person_bindings_remain_saved_but_neutral() -> None:
    """Break caught: an invalid binding borrows another persona's public identity."""
    base = default_synthetic_scenario(seed=23)
    ack = next(persona for persona in base.personas if persona.persona_id == "ack")
    quick = next(persona for persona in base.personas if persona.persona_id == "quick-a")
    mandate_id = UUID("00000000-0000-0000-0000-000000000101")
    assignment_id = UUID("00000000-0000-0000-0000-000000000102")
    created_at = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    assignment = SimpleNamespace(
        mandate_id=mandate_id,
        assignment_id=assignment_id,
        person_id=ack.persona_id,
        engagement_type=SimpleNamespace(value="acknowledge"),
        state=SimpleNamespace(value="complete"),
    )
    mandate = SimpleNamespace(mandate_id=mandate_id, created_at=created_at, state=SimpleNamespace(value="meeting_ready"))

    cases = (
        ("cross-assignment", assignment_id, quick.persona_id, "interview.evidence_confirmed", base),
        ("missing-person", assignment_id, "missing", "interview.evidence_confirmed", base),
        (
            "duplicate-person",
            assignment_id,
            ack.persona_id,
            "interview.evidence_confirmed",
            SimpleNamespace(
                scenario_id=base.scenario_id,
                identity_seed=base.identity_seed,
                provenance=base.provenance,
                personas=(*base.personas, ack),
            ),
        ),
        ("unknown-event", assignment_id, ack.persona_id, "unrecognized.event", base),
    )

    for case_index, (_case, event_assignment_id, person_id, event_type, scenario) in enumerate(cases):
        event = SimpleNamespace(
            event_type=event_type,
            created_at=created_at + timedelta(seconds=case_index),
            assignment_id=event_assignment_id,
            person_id=person_id,
            channel=None,
            direction=None,
        )
        repository = SimpleNamespace(
            list_recent_mandates=lambda _limit, mandate=mandate: [mandate],
            list_assignments=lambda _mandate_id, assignment=assignment: [assignment],
            list_events=lambda _mandate_id, event=event: [event],
        )
        observer = RepositoryProgressObserver(SyntheticProgressStore(initial_progress(scenario)))

        observer.capture(
            repository,
            scenario,
            mode=SyntheticGenerationMode.DETERMINISTIC,
            run_state=SyntheticRunState.RUNNING,
            runtime_status=SyntheticRuntimeStatus.PERSISTED,
        )
        projected = observer.snapshot().events

        assert len(projected) == 1
        assert projected[0].effect == "persisted"
        assert projected[0].highlight_target == "none"
        assert projected[0].persona_label is None
        assert projected[0].data_point == "No public data point"
