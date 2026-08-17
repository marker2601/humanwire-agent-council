from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from humanwire.cloud_store import (
    CloudActiveRunError,
    CloudClaimStatus,
    CloudDivergenceError,
    CloudExpiredClaimError,
    CloudRunState,
    CloudTerminalBinding,
    CloudTimelineRecord,
    FirestoreRunRepository,
    InMemoryRunRepository,
    timeline_document_id,
)
from humanwire.studio_models import StudioAgentMode
from humanwire.studio_projection import (
    StudioDataPoint,
    StudioLifecycle,
    StudioLifecycleStage,
    StudioOutcome,
    StudioTimelineEvent,
    StudioTransition,
)
from tests.humanwire.studio_fixtures import launch_request

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
KEY = "dispatch-key-0000000000000001"
OWNER = "worker-owner-000000000000001"
DIGESTS = {
    "semantic_digest": "1" * 64,
    "final_trace_digest": "2" * 64,
    "transcript_digest": "3" * 64,
    "json_digest": "4" * 64,
    "csv_digest": "5" * 64,
}


def google_request(**updates: object):
    return launch_request(agent_mode=StudioAgentMode.GOOGLE_ADK, **updates)


def record(
    ordinal: int,
    *,
    effect: str = "persisted",
    stage: StudioLifecycleStage = StudioLifecycleStage.BRIEF,
    label: str = "Coordination request saved",
) -> CloudTimelineRecord:
    persisted_ordinal = ordinal if effect == "persisted" else None
    transition = StudioTransition(
        source="request",
        destination="humanwire",
        source_label="Request",
        destination_label="HumanWire",
        generated_label=label,
    )
    event = StudioTimelineEvent(
        timeline_ordinal=ordinal,
        persisted_ordinal=persisted_ordinal,
        created_at=NOW + timedelta(seconds=ordinal),
        stage=stage,
        effect=effect,
        active_transition=transition,
        live_copy=f"{label}.",
    )
    return CloudTimelineRecord.create(
        event=event,
        conversations=(),
        data_point=StudioDataPoint(
            event_ordinal=ordinal,
            label=label,
            summary=(
                "Saved to the coordination record."
                if effect == "persisted"
                else "No state change"
            ),
            effect=effect,
        ),
        lifecycle=StudioLifecycle(
            current=stage,
            stages=tuple(StudioLifecycleStage),
            completed=tuple(StudioLifecycleStage)[: tuple(StudioLifecycleStage).index(stage)],
        ),
    )


def complete_binding() -> CloudTerminalBinding:
    return CloudTerminalBinding(
        state=CloudRunState.COMPLETE,
        outcome=StudioOutcome(
            state="meeting_ready",
            headline="Meeting package ready",
            summary="The required attendees and meeting window are confirmed.",
            meeting_start=NOW + timedelta(days=1),
            meeting_end=NOW + timedelta(days=1, hours=1),
            required_attendees=("Alex Morgan", "Sofia Alvarez"),
        ),
        **DIGESTS,
    )


def created_repository() -> tuple[InMemoryRunRepository, str]:
    repository = InMemoryRunRepository()
    creation = repository.create_run(
        google_request(),
        run_alias="coordination-cloud-a",
        idempotency_key=KEY,
        now=NOW,
    )
    return repository, creation.run_alias


def test_timeline_document_ids_are_exactly_padded_and_bounded() -> None:
    assert timeline_document_id(31) == "00000031"
    for invalid in (0, -1, 100_000_000, True):
        with pytest.raises(ValueError, match="timeline ordinal"):
            timeline_document_id(invalid)


def test_concurrent_create_has_one_owner_without_disclosing_its_alias() -> None:
    repository = InMemoryRunRepository()

    def create(alias: str):
        return repository.create_run(
            google_request(),
            run_alias=alias,
            idempotency_key=f"dispatch-key-{alias}",
            now=NOW,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(create, "coordination-cloud-a"),
            executor.submit(create, "coordination-cloud-b"),
        ]
    successes = [future.result() for future in futures if future.exception() is None]
    failures = [future.exception() for future in futures if future.exception() is not None]

    assert len(successes) == 1
    assert len(failures) == 1
    assert type(failures[0]) is CloudActiveRunError
    assert str(failures[0]) == "active_run"
    assert "coordination-cloud" not in repr(failures[0])


def test_creation_stores_only_normalized_request_and_hashed_dispatch_key() -> None:
    repository, alias = created_repository()

    metadata = repository.load_metadata(alias)
    snapshot = repository.load_snapshot(alias)

    assert metadata.state is CloudRunState.QUEUED
    assert metadata.request.agent_mode is StudioAgentMode.GOOGLE_ADK
    assert metadata.idempotency_key_hash != KEY
    assert len(metadata.idempotency_key_hash) == 64
    assert KEY not in metadata.model_dump_json()
    assert snapshot.run_alias == alias
    assert snapshot.run_state == "starting"
    assert snapshot.events == ()


def test_claim_is_idempotent_for_owner_and_fences_a_healthy_duplicate() -> None:
    repository, alias = created_repository()

    first = repository.claim_run(alias, KEY, OWNER, now=NOW, lease_seconds=60)
    duplicate = repository.claim_run(
        alias, KEY, OWNER, now=NOW + timedelta(seconds=1), lease_seconds=60
    )
    conflict = repository.claim_run(
        alias,
        KEY,
        "worker-owner-000000000000002",
        now=NOW + timedelta(seconds=2),
        lease_seconds=60,
    )

    assert first.status is CloudClaimStatus.CLAIMED
    assert duplicate.status is CloudClaimStatus.DUPLICATE
    assert conflict.status is CloudClaimStatus.CONFLICT
    assert repository.load_metadata(alias).version == 2


def test_claim_renewal_is_owner_fenced_and_idempotency_mismatch_is_inert() -> None:
    repository, alias = created_repository()
    repository.claim_run(alias, KEY, OWNER, now=NOW, lease_seconds=30)

    assert repository.renew_claim(
        alias, OWNER, now=NOW + timedelta(seconds=20), lease_seconds=30
    ) is True
    assert repository.renew_claim(
        alias,
        "worker-owner-000000000000002",
        now=NOW + timedelta(seconds=21),
        lease_seconds=30,
    ) is False
    with pytest.raises(CloudDivergenceError, match="idempotency_mismatch"):
        repository.claim_run(
            alias,
            "dispatch-key-0000000000000099",
            OWNER,
            now=NOW + timedelta(seconds=22),
            lease_seconds=30,
        )
    assert repository.load_metadata(alias).lease_expires_at == NOW + timedelta(seconds=50)


def test_unsafe_public_request_fails_before_active_ownership_is_created() -> None:
    repository = InMemoryRunRepository()

    with pytest.raises(ValueError, match="product-safe"):
        repository.create_run(
            google_request(
                objective="Coordinate the launch with private.person@example.test tomorrow."
            ),
            run_alias="coordination-cloud-a",
            idempotency_key=KEY,
            now=NOW,
        )

    assert repository.active_run is None


def test_timeline_append_is_contiguous_idempotent_and_divergence_safe() -> None:
    repository, alias = created_repository()
    repository.claim_run(alias, KEY, OWNER, now=NOW, lease_seconds=60)
    first = record(1)

    assert repository.append_timeline(alias, OWNER, first, now=NOW) is True
    assert repository.append_timeline(alias, OWNER, first, now=NOW) is False
    with pytest.raises(CloudDivergenceError, match="timeline_divergence"):
        repository.append_timeline(
            alias,
            OWNER,
            record(1, label="Different saved record"),
            now=NOW,
        )
    with pytest.raises(CloudDivergenceError, match="timeline_gap"):
        repository.append_timeline(alias, OWNER, record(3), now=NOW)

    metadata = repository.load_metadata(alias)
    assert metadata.timeline_count == 1
    assert metadata.saved_ordinal == 1


def test_snapshot_reconstructs_the_exact_saved_prefix() -> None:
    repository, alias = created_repository()
    repository.claim_run(alias, KEY, OWNER, now=NOW, lease_seconds=60)
    repository.append_timeline(alias, OWNER, record(1), now=NOW)
    repository.append_timeline(
        alias,
        OWNER,
        record(2, stage=StudioLifecycleStage.OUTREACH, label="Outreach sent"),
        now=NOW,
    )

    snapshot = repository.load_snapshot(alias)

    assert snapshot.run_state == "running"
    assert [event.timeline_ordinal for event in snapshot.events] == [1, 2]
    assert [point.event_ordinal for point in snapshot.data_points] == [1, 2]
    assert snapshot.lifecycle.current is StudioLifecycleStage.OUTREACH
    assert snapshot.active_transition == snapshot.events[-1].active_transition
    assert len([edge for edge in snapshot.graph_edges if edge.active]) == 1


def test_expired_claim_requires_an_explicit_recovery_record() -> None:
    repository, alias = created_repository()
    repository.claim_run(alias, KEY, OWNER, now=NOW, lease_seconds=30)
    next_owner = "worker-owner-000000000000002"

    with pytest.raises(CloudExpiredClaimError, match="expired_claim_requires_recovery"):
        repository.claim_run(
            alias,
            KEY,
            next_owner,
            now=NOW + timedelta(seconds=31),
            lease_seconds=30,
        )

    recovered = repository.claim_run(
        alias,
        KEY,
        next_owner,
        now=NOW + timedelta(seconds=31),
        lease_seconds=30,
        recovery_record=record(1, effect="inert", label="Worker recovery started"),
    )

    assert recovered.status is CloudClaimStatus.RECOVERED
    assert repository.load_metadata(alias).claim_owner == next_owner
    assert repository.load_snapshot(alias).events[0].effect == "inert"


def test_terminal_binding_and_active_release_are_atomic_and_exact_idempotent() -> None:
    repository, alias = created_repository()
    repository.claim_run(alias, KEY, OWNER, now=NOW, lease_seconds=60)
    repository.append_timeline(alias, OWNER, record(1), now=NOW)
    binding = complete_binding()

    assert repository.finish_run(alias, OWNER, binding, now=NOW) is True
    assert repository.finish_run(alias, OWNER, binding, now=NOW) is False
    snapshot = repository.load_snapshot(alias)
    assert snapshot.run_state == "complete"
    assert snapshot.downloads_ready is True
    assert snapshot.outcome.headline == "Meeting package ready"
    assert repository.active_run is None

    with pytest.raises(CloudDivergenceError, match="terminal_divergence"):
        repository.finish_run(
            alias,
            OWNER,
            binding.model_copy(update={"csv_digest": "6" * 64}),
            now=NOW,
        )
    second = repository.create_run(
        google_request(),
        run_alias="coordination-cloud-b",
        idempotency_key="dispatch-key-0000000000000002",
        now=NOW,
    )
    assert second.run_alias == "coordination-cloud-b"


def test_failed_terminal_binding_keeps_prefix_but_never_enables_exports() -> None:
    repository, alias = created_repository()
    repository.claim_run(alias, KEY, OWNER, now=NOW, lease_seconds=60)
    repository.append_timeline(alias, OWNER, record(1), now=NOW)
    failed = CloudTerminalBinding(
        state=CloudRunState.FAILED,
        outcome=StudioOutcome(
            state="failed",
            headline="Coordination stopped",
            summary="The saved workspace remains available for review.",
        ),
    )

    repository.finish_run(alias, OWNER, failed, now=NOW)

    snapshot = repository.load_snapshot(alias)
    assert snapshot.run_state == "failed"
    assert snapshot.current_event_ordinal == 1
    assert snapshot.downloads_ready is False
    assert snapshot.outcome.state == "failed"


@pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="requires explicit Firestore emulator",
)
@pytest.mark.firestore_emulator
def test_firestore_emulator_preserves_create_claim_append_and_finish() -> None:
    from google.cloud import firestore

    suffix = uuid4().hex
    client = firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT", "humanwire-test"))
    repository = FirestoreRunRepository(
        client,
        run_collection=f"humanwire_test_runs_{suffix}",
        control_collection=f"humanwire_test_control_{suffix}",
    )
    def create(alias: str, key: str):
        return repository.create_run(
            google_request(),
            run_alias=alias,
            idempotency_key=key,
            now=NOW,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(create, "coordination-cloud-a", KEY),
            executor.submit(
                create,
                "coordination-cloud-b",
                "dispatch-key-0000000000000002",
            ),
        ]
    creations = [future.result() for future in futures if future.exception() is None]
    failures = [future.exception() for future in futures if future.exception() is not None]
    assert len(creations) == 1
    assert len(failures) == 1
    assert type(failures[0]) is CloudActiveRunError
    creation = creations[0]
    repository.claim_run(
        creation.run_alias,
        creation.idempotency_key,
        OWNER,
        now=NOW,
        lease_seconds=60,
    )
    repository.append_timeline(creation.run_alias, OWNER, record(1), now=NOW)
    repository.finish_run(creation.run_alias, OWNER, complete_binding(), now=NOW)

    snapshot = repository.load_snapshot(creation.run_alias)
    assert snapshot.run_state == "complete"
    assert snapshot.current_event_ordinal == 1
    assert snapshot.downloads_ready is True
