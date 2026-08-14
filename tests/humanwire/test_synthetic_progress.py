from __future__ import annotations

import re
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from humanwire.persona_runtime import SyntheticGenerationMode
from humanwire.replay_projection import project_replay_labels
from humanwire.synthetic import (
    default_synthetic_scenario,
    generate_scenario,
    replay_transcript,
)
from humanwire.synthetic_progress import (
    RepositoryProgressObserver,
    SyntheticAggregateCounts,
    SyntheticProgressEvent,
    SyntheticProgressSnapshot,
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
        self._persisted_boundaries: list[tuple[tuple[datetime, str], ...]] = []

    def capture(self, *args: object, **kwargs: object) -> None:
        self._delegate.capture(*args, **kwargs)
        snapshot = self._delegate.snapshot()
        self._published_counts.append(snapshot.saved_event_count)
        repository = args[0]
        mandates = sorted(
            repository.list_recent_mandates(1000),
            key=lambda mandate: (mandate.created_at, str(mandate.mandate_id)),
        )
        saved_events = [
            (event.created_at, mandate_index, saved_order, event.event_type)
            for mandate_index, mandate in enumerate(mandates)
            for saved_order, event in enumerate(
                repository.list_events(mandate.mandate_id), start=1
            )
        ]
        self._persisted_boundaries.append(
            tuple(
                (created_at, event_type)
                for created_at, _mandate_index, _saved_order, event_type in sorted(
                    saved_events
                )
            )
        )
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

    @property
    def persisted_boundaries(self) -> tuple[tuple[tuple[datetime, str], ...], ...]:
        return tuple(self._persisted_boundaries)


def _persisted_event(
    ordinal: int,
    *,
    data_point: str = "Mandate created",
    created_at: datetime = datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
) -> SyntheticProgressEvent:
    return SyntheticProgressEvent(
        timeline_ordinal=ordinal,
        persisted_ordinal=ordinal,
        created_at=created_at,
        story="primary",
        effect="persisted",
        stage="Mandate",
        source="HumanWire",
        destination="Decision Room",
        data_point=data_point,
        description=f"Mandate: {data_point}",
        highlight_target="origin",
    )


def _snapshot_with_persisted(
    initial: SyntheticProgressSnapshot,
    events: tuple[SyntheticProgressEvent, ...],
    *,
    run_state: SyntheticRunState = SyntheticRunState.RUNNING,
    final_trace_sha256: str | None = None,
) -> SyntheticProgressSnapshot:
    return initial.model_copy(
        update={
            "run_state": run_state,
            "final_trace_sha256": final_trace_sha256,
            "saved_event_count": len(events),
            "timeline_event_count": len(events),
            "current_timeline_ordinal": len(events),
            "current_persisted_ordinal": len(events),
            "events": events,
            "aggregate_counts": SyntheticAggregateCounts(
                personas=len(initial.personas),
                persisted_events=len(events),
                inert_attempts=0,
                complete_assignments=0,
                pending_assignments=0,
                terminal_mandates=0,
            ),
        }
    )


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
    expected_saved = observer.persisted_boundaries[-1]
    assert len(expected_saved) == snapshot.saved_event_count
    assert [(event.created_at, event.data_point) for event in snapshot.events] == [
        (created_at, project_replay_labels(event_type, None).data_point)
        for created_at, event_type in expected_saved
    ]

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


def test_completed_store_binds_exact_transcript_digest_without_serializing_it(
    tmp_path,
) -> None:
    """Break caught: final evidence can be detached from the transcript that produced it."""
    scenario = default_synthetic_scenario(seed=29)
    store = SyntheticProgressStore(initial_progress(scenario))
    observer = RepositoryProgressObserver(store)
    run_root = tmp_path / "bound-run"
    result = generate_scenario(
        scenario,
        run_root / "transcript.json",
        run_root,
        progress_observer=observer,
    )

    binding = store.final_evidence_binding()
    public_snapshot = store.snapshot()

    assert binding is not None
    evidence, transcript_sha256 = binding
    assert transcript_sha256 == result.transcript.digest
    assert evidence.trace_sha256 == public_snapshot.final_trace_sha256
    assert transcript_sha256 not in public_snapshot.model_dump_json()
    assert transcript_sha256 not in evidence.model_dump_json()

    public_snapshot._transcript_sha256 = "f" * 64
    assert store.final_evidence_binding() == binding


def test_incomplete_store_has_no_transcript_evidence_binding() -> None:
    """Break caught: a transcript digest becomes available before completed finality."""
    store = SyntheticProgressStore(
        initial_progress(default_synthetic_scenario(seed=30))
    )

    assert store.final_evidence_binding() is None


@pytest.mark.parametrize(
    "run_state",
    (
        SyntheticRunState.STARTING,
        SyntheticRunState.RUNNING,
        SyntheticRunState.FAILED,
    ),
)
def test_store_constructor_rejects_a_transcript_binding_in_every_noncomplete_state(
    run_state: SyntheticRunState,
) -> None:
    """Break caught: constructor retention bypasses transcript-binding finality."""
    snapshot = initial_progress(default_synthetic_scenario(seed=33)).model_copy(
        update={"run_state": run_state}
    )
    snapshot._transcript_sha256 = "a" * 64

    with pytest.raises(
        ValueError,
        match="^synthetic transcript binding requires completed finality$",
    ):
        SyntheticProgressStore(snapshot)


def test_store_constructor_requires_full_finality_for_a_transcript_binding() -> None:
    """Break caught: a complete label without a final trace authorizes bound evidence."""
    initial = initial_progress(default_synthetic_scenario(seed=34))
    incomplete_finality = initial.model_copy(
        update={"run_state": SyntheticRunState.COMPLETE}
    )
    incomplete_finality._transcript_sha256 = "b" * 64

    with pytest.raises(
        ValueError,
        match="^synthetic transcript binding requires completed finality$",
    ):
        SyntheticProgressStore(incomplete_finality)

    complete = initial.model_copy(
        update={
            "run_state": SyntheticRunState.COMPLETE,
            "final_trace_sha256": "c" * 64,
        }
    )
    complete._transcript_sha256 = "d" * 64

    binding = SyntheticProgressStore(complete).final_evidence_binding()
    assert binding is not None
    assert binding[0].trace_sha256 == "c" * 64
    assert binding[1] == "d" * 64


def test_replay_store_binds_the_validated_source_transcript_digest(tmp_path) -> None:
    """Break caught: replay completion binds a digest other than its validated input."""
    scenario = default_synthetic_scenario(seed=32)
    source_root = tmp_path / "source-run"
    generated = generate_scenario(
        scenario,
        source_root / "transcript.json",
        source_root,
    )
    store = SyntheticProgressStore(initial_progress(scenario))
    observer = RepositoryProgressObserver(store)

    replayed = replay_transcript(
        source_root / "transcript.json",
        tmp_path / "replay-run",
        progress_observer=observer,
    )

    binding = store.final_evidence_binding()
    assert binding is not None
    assert binding[1] == generated.transcript.digest
    assert binding[1] == replayed.transcript.digest


@pytest.mark.parametrize(
    "leaked_uuid",
    [
        "1f0d5584-9b8e-4c73-b2e7-3a9f5c1d7e42",
        "00000000-0000-0000-0000-000000000101",
    ],
)
def test_uuid_shaped_scenario_alias_never_leaves_progress_or_evidence(leaked_uuid) -> None:
    """Break caught: a UUID-shaped run alias reaches a public progress artifact."""
    scenario = default_synthetic_scenario(seed=11).model_copy(
        update={"scenario_id": leaked_uuid}
    )
    initial = initial_progress(scenario)
    complete = _snapshot_with_persisted(
        initial,
        (),
        run_state=SyntheticRunState.COMPLETE,
        final_trace_sha256="a" * 64,
    )
    evidence = evidence_bundle(complete)

    assert initial.run_alias == "synthetic-run"
    uuid_pattern = re.compile(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        re.IGNORECASE,
    )
    assert uuid_pattern.search(complete.model_dump_json()) is None
    assert evidence is not None
    assert uuid_pattern.search(evidence.model_dump_json()) is None


@pytest.mark.parametrize("state", [SyntheticRunState.RUNNING, SyntheticRunState.FAILED])
def test_nonterminal_states_reject_final_trace_and_terminal_evidence(state) -> None:
    """Break caught: a nonterminal projection advertises a final proof artifact."""
    initial = initial_progress(default_synthetic_scenario(seed=12))
    payload = initial.model_dump()
    payload.update({"run_state": state, "final_trace_sha256": "b" * 64})

    with pytest.raises(ValidationError, match="final trace"):
        SyntheticProgressSnapshot.model_validate(payload)

    bypassed = initial.model_copy(update={"run_state": state, "final_trace_sha256": "b" * 64})
    assert evidence_bundle(bypassed) is None


def test_store_rejects_same_length_persisted_rewrite() -> None:
    """Break caught: a later publication rewrites an already saved event in place."""
    initial = initial_progress(default_synthetic_scenario(seed=13))
    store = SyntheticProgressStore(initial)
    first = _snapshot_with_persisted(initial, (_persisted_event(1),))
    rewritten = _snapshot_with_persisted(
        initial,
        (_persisted_event(1, data_point="Mandate received"),),
    )
    store.publish(first)

    with pytest.raises(ValueError, match="persisted prefix"):
        store.publish(rewritten)


def test_store_copies_a_published_terminal_snapshot_before_private_state_changes() -> None:
    """Break caught: a caller-held published object can alter the stored terminal evidence."""
    initial = initial_progress(default_synthetic_scenario(seed=14))
    store = SyntheticProgressStore(initial)
    published = _snapshot_with_persisted(
        _snapshot_with_persisted(initial, (_persisted_event(1),)),
        (_persisted_event(1),),
        run_state=SyntheticRunState.COMPLETE,
        final_trace_sha256="c" * 64,
    )
    store.publish(_snapshot_with_persisted(initial, (_persisted_event(1),)))
    store.publish(published)
    published._identity_seed = 999

    evidence = store.evidence_bundle()
    assert evidence is not None
    assert evidence.identity_seed == 14


def test_store_never_regresses_from_terminal_to_running() -> None:
    """Break caught: an old running snapshot replaces a complete public run."""
    initial = initial_progress(default_synthetic_scenario(seed=15))
    store = SyntheticProgressStore(initial)
    complete = _snapshot_with_persisted(
        _snapshot_with_persisted(initial, (_persisted_event(1),)),
        (_persisted_event(1),),
        run_state=SyntheticRunState.COMPLETE,
        final_trace_sha256="d" * 64,
    )
    store.publish(_snapshot_with_persisted(initial, (_persisted_event(1),)))
    store.publish(complete)

    with pytest.raises(ValueError, match="state"):
        store.publish(_snapshot_with_persisted(initial, (_persisted_event(1),)))


def test_store_rejects_skipping_from_starting_to_complete() -> None:
    """Break caught: final evidence can be published before a run is observed running."""
    initial = initial_progress(default_synthetic_scenario(seed=17))
    store = SyntheticProgressStore(initial)

    with pytest.raises(ValueError, match="state"):
        store.publish(
            _snapshot_with_persisted(
                initial,
                (),
                run_state=SyntheticRunState.COMPLETE,
                final_trace_sha256="e" * 64,
            )
        )


def test_persisted_ordinals_follow_global_saved_event_order() -> None:
    """Break caught: mandate grouping assigns persisted ordinals before global event sorting."""
    scenario = default_synthetic_scenario(seed=16)
    start = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    first_mandate = SimpleNamespace(
        mandate_id=object(), created_at=start, state=SimpleNamespace(value="meeting_ready")
    )
    second_mandate = SimpleNamespace(
        mandate_id=object(),
        created_at=start + timedelta(seconds=1),
        state=SimpleNamespace(value="partial"),
    )
    first_event = SimpleNamespace(
        event_type="mandate.created",
        created_at=start + timedelta(seconds=3),
        assignment_id=None,
        person_id=None,
        channel=None,
        direction=None,
    )
    second_event = SimpleNamespace(
        event_type="mandate.received",
        created_at=start + timedelta(seconds=2),
        assignment_id=None,
        person_id=None,
        channel=None,
        direction=None,
    )
    repository = SimpleNamespace(
        list_recent_mandates=lambda _limit: [first_mandate, second_mandate],
        list_assignments=lambda _mandate_id: [],
        list_events=lambda mandate_id: (
            [first_event] if mandate_id is first_mandate.mandate_id else [second_event]
        ),
    )
    observer = RepositoryProgressObserver(SyntheticProgressStore(initial_progress(scenario)))

    observer.capture(
        repository,
        scenario,
        mode=SyntheticGenerationMode.DETERMINISTIC,
        run_state=SyntheticRunState.RUNNING,
        runtime_status=SyntheticRuntimeStatus.PERSISTED,
    )

    snapshot = observer.snapshot()
    assert [(event.persisted_ordinal, event.data_point) for event in snapshot.events] == [
        (1, "Mandate received"),
        (2, "Mandate created"),
    ]


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
    mandate_id = object()
    assignment_id = object()
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
