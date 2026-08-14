import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from humanwire import studio_projection
from humanwire.domain import Channel
from humanwire.persona_runtime import SyntheticIntent
from humanwire.studio_projection import (
    StudioConversationItem,
    StudioWorkspaceSnapshot,
    create_studio_progress,
)
from humanwire.synthetic import build_coordination_scenario, generate_scenario

from .studio_fixtures import launch_request


@pytest.fixture(scope="module")
def completed_workspace(tmp_path_factory):
    request = launch_request()
    scenario = build_coordination_scenario(
        request,
        seed=7,
        scenario_id="hostile-validation-001",
    )
    store, observer = create_studio_progress(request, scenario)
    run_root = tmp_path_factory.mktemp("hostile-validation") / "run"
    generate_scenario(
        scenario,
        run_root / "transcript.json",
        run_root,
        mandate_request=request.objective,
        include_change_story=False,
        progress_observer=observer,
        presentation_observer=observer,
    )
    return store, store.snapshot()


def test_initial_workspace_is_product_copy_with_approved_graph() -> None:
    """Break caught: internal proof vocabulary or identities reach the product shell."""
    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="launch-001")

    store, _observer = create_studio_progress(request, scenario)

    snapshot = store.snapshot()
    assert snapshot.objective == request.objective
    assert snapshot.requester_name == "Alex Morgan"
    assert snapshot.lifecycle.current == "brief"
    assert [node.label for node in snapshot.graph_nodes[:3]] == [
        "Request",
        "HumanWire",
        "Caspian Gateway",
    ]
    dumped = snapshot.model_dump_json()
    for forbidden in (
        "proof_class",
        "actor_type",
        "simulated_persona",
        "fake_caspian",
        "PRIVATE-PERSONA-SENTINEL",
        "@example.test",
    ):
        assert forbidden not in dumped


def test_workspace_models_omit_identity_and_private_payload_fields() -> None:
    """Break caught: a forbidden internal identity field is added to the public schema."""
    serialized = json.dumps(StudioWorkspaceSnapshot.model_json_schema())

    for forbidden in (
        "email",
        "sender_address",
        "route_id",
        "conversation_id",
        "connection_id",
        "message_id",
        "assignment_id",
        "private_facts",
        "prompt",
    ):
        assert forbidden not in serialized


def test_saved_transition_message_and_data_point_share_one_ordinal(tmp_path) -> None:
    """Break caught: the visible evidence message drifts from its saved event."""
    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="launch-001")
    store, observer = create_studio_progress(request, scenario)

    generate_scenario(
        scenario,
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        mandate_request=request.objective,
        include_change_story=False,
        progress_observer=observer,
        presentation_observer=observer,
    )

    snapshot = store.snapshot()
    evidence = next(
        item
        for item in snapshot.data_points
        if item.label == "Evidence confirmed"
        and any(
            message.event_ordinal == item.event_ordinal
            and message.speaker == "Anika Rao"
            for message in snapshot.conversations
        )
    )
    selected = snapshot.events[evidence.event_ordinal - 1]
    assert selected.active_transition.destination == "evidence"
    assert selected.affected_persona_id == "structured"


def test_product_projection_has_one_transition_and_one_data_point_per_event(tmp_path) -> None:
    """Break caught: graph, lifecycle, and saved-data animation use different ordinals."""
    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="launch-002")
    store, observer = create_studio_progress(request, scenario)
    generate_scenario(
        scenario,
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        mandate_request=request.objective,
        include_change_story=False,
        progress_observer=observer,
        presentation_observer=observer,
    )

    snapshot = store.snapshot()
    assert [event.timeline_ordinal for event in snapshot.events] == list(
        range(1, len(snapshot.events) + 1)
    )
    assert [point.event_ordinal for point in snapshot.data_points] == list(
        range(1, len(snapshot.events) + 1)
    )
    assert sum(edge.active for edge in snapshot.graph_edges) == 1
    assert sum(node.active for node in snapshot.graph_nodes if node.persona_id) <= 1
    assert snapshot.active_transition == snapshot.events[-1].active_transition


def test_inert_attempt_is_no_state_change_without_lifecycle_advance(tmp_path) -> None:
    """Break caught: silence is presented as a successful saved lifecycle advance."""
    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="launch-003")
    store, observer = create_studio_progress(request, scenario)
    generate_scenario(
        scenario,
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        mandate_request=request.objective,
        include_change_story=False,
        progress_observer=observer,
        presentation_observer=observer,
    )

    snapshot = store.snapshot()
    inert = next(event for event in snapshot.events if event.effect == "inert")
    assert snapshot.data_points[inert.timeline_ordinal - 1].summary == "No state change"
    if inert.timeline_ordinal > 1:
        assert inert.stage == snapshot.events[inert.timeline_ordinal - 2].stage
    message = next(
        item
        for item in snapshot.conversations
        if item.event_ordinal == inert.timeline_ordinal
    )
    assert message.status in {"no_response", "rejected"}


def test_availability_interval_is_rendered_as_fixed_product_copy(tmp_path) -> None:
    """Break caught: a valid scheduling interval is mistaken for a filesystem path."""
    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="availability-copy-001")
    store, observer = create_studio_progress(request, scenario)

    generate_scenario(
        scenario,
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        mandate_request=request.objective,
        include_change_story=False,
        progress_observer=observer,
        presentation_observer=observer,
    )

    snapshot = store.snapshot()
    assert snapshot.run_state == "complete"
    availability = [
        item
        for item in snapshot.conversations
        if item.text == "Availability received for the requested window."
    ]
    assert availability
    assert all("/" not in item.text and "\\" not in item.text for item in availability)


@pytest.mark.parametrize(
    "unsafe",
    [
        "private/secrets",
        "2026-08-13T15:00:00/2026-08-13T16:00:00",
        "2026-08-13T16:00:00+00:00/2026-08-13T15:00:00+00:00",
        (
            "2026-08-13T15:00:00+00:00/2026-08-13T16:00:00+00:00 "
            "2026-08-14T15:00:00+00:00/2026-08-14T16:00:00+00:00"
        ),
    ],
)
def test_availability_renderer_rejects_anything_but_one_aware_window(unsafe) -> None:
    """Break caught: fixed availability copy hides malformed or path-shaped input."""
    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="availability-safe-001")
    _store, observer = create_studio_progress(request, scenario)

    with pytest.raises(ValueError, match="availability content"):
        observer.record_decision(
            created_at=datetime(2026, 8, 13, 15, 0, tzinfo=UTC),
            persona_id="availability",
            channel=Channel.EMAIL,
            intent=SyntheticIntent.AVAILABILITY,
            safe_content=unsafe,
        )


@pytest.mark.parametrize(
    "unsafe",
    [
        "PRIVATE-PERSONA-SENTINEL",
        "Use HW-ABCDEF12 for this request.",
        "Send this to risk@example.test.",
        "route_id=route-7 conversation_id=conversation-8",
        "/mandate approve this request",
        "/go approve this request",
        "/confirm approve this request",
        "authorization: Bearer secret-value",
        "Bearer sk-live-secret-value",
        "OPENAI_API_KEY sk-live-private-value",
        "api_key=secret-value",
        '{"password": "private-value"}',
        "database_url=postgresql://operator:secret@internal/db",
        "redis://internal.service/0",
        "https://internal.example/admin/runbook",
        "https：／／internal.example",
        "x:private-value",
        "custom+private:opaque-value",
        "server=db.internal;database=prod;user=admin;password=secret",
        '"password" : "private-value"',
        "password：private-value",
        "API KEY private-value",
        r"Read C:\private\persona.txt",
        "Read C:/private/persona.txt",
        "Read /home/operator/private/persona.txt",
        "Read /opt/app/private/persona.txt",
        "Read /private",
        r"Read \\private-server\share\persona.txt",
        "Read //private-server/share/persona.txt",
        "Read ../../private/persona.txt",
        r"Read .\private\persona.txt",
        r"Read private\persona.txt",
        "Read private/persona.txt",
        "Read private/secrets",
        r"Read private\secrets",
        "Read folder/subfolder",
        r"Read folder\subfolder",
        "PRIVATE_KEY -----BEGIN-KEY-----",
        "private-key: abc123",
        "AWS_ACCESS_KEY_ID AKIA1234567890",
        "AWS-ACCESS-KEY-ID AKIA1234567890",
        "path=private/secrets",
        r"Read ~\private\secrets",
        "Read ~/private/secrets",
        r"Read \private",
        "private//secrets",
        r"private\\secrets",
        r"private/\secrets",
        "private/秘密",
        "private\\秘密",
        "private／secrets",
        "private＼secrets",
        "秘密/秘密",
        r"\\服务器\共享",
        "~/秘密",
        r"~\秘密",
        "private/+secret",
        "private/[secret]",
        "$HOME/private/secrets",
        r"%USERPROFILE%\private\secrets",
        "private+folder/secrets",
        "sk-live-private-value",
        "AKIA1234567890",
        "-----BEGIN PRIVATE KEY-----",
        "go/no-go/private/secrets",
        r"go/no-go\private\secrets",
        "humanwire.studio/v1/private/secrets",
        r"humanwire.studio/v1\private\secrets",
        "humanwire.studio/v1?/private/secrets",
        "Please run /go approve this request",
        "／confirm approve",
        "embedded/go approve this request",
        "4d36e967-e325-11ce-bfc1-08002be10318",
    ],
)
def test_decision_renderer_rejects_private_identity_and_command_content(unsafe) -> None:
    """Break caught: validated agent prose bypasses the product privacy corpus."""
    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="privacy-001")
    _store, observer = create_studio_progress(request, scenario)

    with pytest.raises(ValueError, match="product-safe"):
        observer.record_decision(
            created_at=datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
            persona_id="structured",
            channel=Channel.EMAIL,
            intent=SyntheticIntent.ANSWER,
            safe_content=unsafe,
        )


def test_outbound_renderer_accepts_only_known_delivery_kinds() -> None:
    """Break caught: arbitrary provider text is mislabeled as a product message."""
    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="privacy-002")
    _store, observer = create_studio_progress(request, scenario)

    with pytest.raises(ValueError, match="message kind"):
        observer.record_outbound(
            created_at=datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
            persona_id="structured",
            channel=Channel.EMAIL,
            message_kind="raw_provider_payload",
            safe_text="A provider payload that must not be accepted.",
        )


def test_store_rejects_duplicate_or_regressing_public_ordinals(tmp_path) -> None:
    """Break caught: a refresh rewrites or duplicates an already-published event."""
    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="launch-004")
    store, observer = create_studio_progress(request, scenario)
    generate_scenario(
        scenario,
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        mandate_request=request.objective,
        include_change_story=False,
        progress_observer=observer,
        presentation_observer=observer,
    )
    snapshot = store.snapshot()
    duplicated = snapshot.model_copy(
        update={"events": (*snapshot.events, snapshot.events[-1])}
    )

    with pytest.raises((ValidationError, ValueError)):
        store.publish(duplicated)


def test_complete_snapshot_is_copied_and_final_bindings_gate_downloads(tmp_path) -> None:
    """Break caught: callers mutate terminal history or downloads unlock before binding."""
    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="launch-005")
    store, observer = create_studio_progress(request, scenario)
    initial = store.snapshot()
    assert initial.downloads_ready is False
    assert "sha256" not in initial.model_dump_json().casefold()

    generate_scenario(
        scenario,
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        mandate_request=request.objective,
        include_change_story=False,
        progress_observer=observer,
        presentation_observer=observer,
    )
    complete = store.snapshot()
    assert complete.run_state == "complete"
    assert complete.downloads_ready is True
    with pytest.raises(ValidationError):
        StudioConversationItem.model_validate(
            {
                **complete.conversations[0].model_dump(),
                "text": "contact risk@example.test",
            }
        )
    assert store.snapshot() == complete

    missing_binding = complete.model_copy(update={"downloads_ready": True})
    missing_binding._transcript_sha256 = None
    with pytest.raises(ValueError, match="binding"):
        store.publish(missing_binding)

    changed_terminal = complete.model_copy(update={"objective": "A different final objective."})
    with pytest.raises(ValueError, match="immutable"):
        store.publish(changed_terminal)


def test_complete_snapshot_accepts_only_exact_idempotent_republish(completed_workspace) -> None:
    """Break caught: terminal history or outcome changes after downloads become ready."""
    store, complete = completed_workspace

    store.publish(complete)
    changed = complete.model_copy(
        update={
            "outcome": complete.outcome.model_copy(
                update={"summary": "A changed terminal summary."}
            )
        }
    )

    with pytest.raises(ValueError, match="immutable"):
        store.publish(changed)


def test_inert_event_rejects_a_persisted_ordinal(completed_workspace) -> None:
    """Break caught: an inert attempt is counted as repository persistence."""
    store, complete = completed_workspace
    index = next(i for i, event in enumerate(complete.events) if event.effect == "inert")
    events = list(complete.events)
    events[index] = events[index].model_copy(update={"persisted_ordinal": 1})

    with pytest.raises((ValidationError, ValueError), match="persisted ordinal"):
        store.publish(complete.model_copy(update={"events": tuple(events)}))


def test_data_effect_must_match_its_event_effect(completed_workspace) -> None:
    """Break caught: the data trail presents an inert attempt as saved state."""
    store, complete = completed_workspace
    index = next(i for i, event in enumerate(complete.events) if event.effect == "inert")
    data_points = list(complete.data_points)
    data_points[index] = data_points[index].model_copy(update={"effect": "persisted"})

    with pytest.raises((ValidationError, ValueError), match="effect"):
        store.publish(complete.model_copy(update={"data_points": tuple(data_points)}))


def test_data_label_must_match_its_event_transition(completed_workspace) -> None:
    """Break caught: generated data copy is detached from the animated transition."""
    store, complete = completed_workspace
    data_points = list(complete.data_points)
    data_points[0] = data_points[0].model_copy(update={"label": "Unrelated saved result"})

    with pytest.raises((ValidationError, ValueError), match="generated label"):
        store.publish(complete.model_copy(update={"data_points": tuple(data_points)}))


@pytest.mark.parametrize("malformation", ["stages", "completed"])
def test_lifecycle_shape_is_the_exact_approved_prefix(
    completed_workspace,
    malformation,
) -> None:
    """Break caught: malformed lifecycle metadata disagrees with the event history."""
    store, complete = completed_workspace
    update = (
        {"stages": tuple(reversed(complete.lifecycle.stages))}
        if malformation == "stages"
        else {"completed": ()}
    )
    lifecycle = complete.lifecycle.model_copy(update=update)

    with pytest.raises((ValidationError, ValueError), match="lifecycle"):
        store.publish(complete.model_copy(update={"lifecycle": lifecycle}))


def test_private_decision_digest_is_not_published_in_product_conversation(tmp_path) -> None:
    """Break caught: private persona content or its digest reaches the named timeline."""
    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="privacy-003")
    store, observer = create_studio_progress(request, scenario)
    generate_scenario(
        scenario,
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        mandate_request=request.objective,
        include_change_story=False,
        progress_observer=observer,
        presentation_observer=observer,
    )

    dumped = store.snapshot().model_dump_json().casefold()
    assert "sha256:" not in dumped
    assert "80399b2136673ca66ff3614f8446333ad7afd54b0e9bb745029545c653071adf" not in dumped


def test_lifecycle_uses_only_saved_allowlisted_phase_advances(tmp_path) -> None:
    """Break caught: approval/scheduling advance early or an unknown event advances resolve."""
    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="lifecycle-001")
    store, observer = create_studio_progress(request, scenario)
    generate_scenario(
        scenario,
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        mandate_request=request.objective,
        include_change_story=False,
        progress_observer=observer,
        presentation_observer=observer,
    )

    snapshot = store.snapshot()
    stage_by_label = {
        point.label: snapshot.events[point.event_ordinal - 1].stage
        for point in snapshot.data_points
    }
    assert stage_by_label["Coordination request saved"] == "brief"
    assert stage_by_label["Outreach sent"] == "outreach"
    assert stage_by_label["Interview answer recorded"] == "resolve"
    assert stage_by_label["Approval complete"] == "approve"
    assert stage_by_label["Scheduling started"] == "schedule"
    assert studio_projection._event_phase(
        "new.saved.event",
        None,
        negotiation_started=False,
        approval_started=False,
    ) is None


@pytest.mark.parametrize(
    "event_type",
    [
        "outreach.unknown_private_event",
        "meeting.secret_uploaded",
        "engagement.plan_unrecognized",
    ],
)
def test_unknown_prefixed_events_do_not_advance_lifecycle(event_type) -> None:
    """Break caught: a new internal event inherits a public lifecycle phase by prefix."""
    assert studio_projection._event_phase(
        event_type,
        None,
        negotiation_started=False,
        approval_started=False,
    ) is None


def test_failed_initial_snapshot_has_no_binding_or_downloads() -> None:
    """Break caught: an observer failure leaves final artifacts available."""
    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="failed-001")
    store, observer = create_studio_progress(request, scenario)

    observer.mark_unavailable()

    failed = store.snapshot()
    assert failed.run_state == "failed"
    assert failed.downloads_ready is False
    assert failed._final_trace_sha256 is None
    assert failed._transcript_sha256 is None

    store.publish(failed)
    changed = failed.model_copy(
        update={
            "outcome": failed.outcome.model_copy(
                update={"summary": "A changed failed summary."}
            )
        }
    )
    with pytest.raises(ValueError, match="immutable"):
        store.publish(changed)


def test_scenario_cannot_spoof_catalog_name_or_role() -> None:
    """Break caught: scenario-controlled display copy impersonates a product stakeholder."""
    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="spoofed-001")
    personas = [
        persona.model_copy(
            update={"display_name": "Untrusted Operator", "role": "Private administrator"}
        )
        if persona.persona_id == "structured"
        else persona
        for persona in scenario.personas
    ]
    spoofed = scenario.model_copy(update={"personas": personas})

    with pytest.raises(ValueError, match="product catalog"):
        create_studio_progress(request, spoofed)


def test_product_json_omits_primary_ui_proof_vocabulary(tmp_path) -> None:
    """Break caught: proof-only terms appear in the completed workspace payload."""
    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="launch-006")
    store, observer = create_studio_progress(request, scenario)
    generate_scenario(
        scenario,
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        mandate_request=request.objective,
        include_change_story=False,
        progress_observer=observer,
        presentation_observer=observer,
    )
    dumped = store.snapshot().model_dump_json().casefold()
    for forbidden in ("proof", "synthetic", "fake", "simulated", "@example.test", "hw-"):
        assert forbidden not in dumped
