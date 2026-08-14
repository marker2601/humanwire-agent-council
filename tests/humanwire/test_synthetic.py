from __future__ import annotations

import hashlib
import inspect
import json
import re
import socket
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest
from persona_engine_fixtures import FixtureDecisionEngineFactory
from pydantic import ValidationError

import humanwire.synthetic as synthetic_module
from humanwire.database import create_session_factory
from humanwire.domain import Channel, EngagementType
from humanwire.persona_runtime import PersonaVisibility
from humanwire.repository import SqlAlchemyHumanWireRepository
from humanwire.synthetic import (
    SUPPORTED_SCHEMA_VERSION,
    SyntheticAction,
    SyntheticIntent,
    SyntheticPersona,
    SyntheticProvenance,
    SyntheticScenario,
    SyntheticTranscript,
    default_synthetic_scenario,
    generate_scenario,
    load_transcript,
)
from tests.humanwire.studio_fixtures import launch_request

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
TRIGGER_DIGEST = "a" * 64


def make_provenance() -> SyntheticProvenance:
    return SyntheticProvenance(
        proof_class="synthetic_multi_persona",
        actor_type="simulated_persona",
        identity_source="synthetic_fixture",
        transport="fake_caspian",
        human_attested=False,
        live_provider_verified=False,
    )


def make_scenario(**changes: object) -> SyntheticScenario:
    values: dict[str, object] = {
        "schema_version": SUPPORTED_SCHEMA_VERSION,
        "scenario_id": "launch-v1",
        "identity_seed": 0,
        "identity_generator_version": "humanwire.synthetic-identities/v1",
        "personas": [
            SyntheticPersona(
                persona_id="owner",
                display_name="Owner Persona",
                role="Operations owner",
                email="owner@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[SyntheticIntent.ACKNOWLEDGE],
            )
        ],
        "provenance": make_provenance(),
    }
    values.update(changes)
    return SyntheticScenario(**values)


def make_action(**changes: object) -> SyntheticAction:
    values: dict[str, object] = {
        "schema_version": SUPPORTED_SCHEMA_VERSION,
        "action_id": "owner-1",
        "persona_id": "owner",
        "channel": Channel.EMAIL,
        "timestamp": NOW,
        "local_sequence": 1,
        "trigger_id": "outbound-1",
        "trigger_digest": TRIGGER_DIGEST,
        "intent": SyntheticIntent.ACKNOWLEDGE,
        "content": "Acknowledged.",
    }
    values.update(changes)
    return SyntheticAction(**values)


def make_transcript(**changes: object) -> SyntheticTranscript:
    values: dict[str, object] = {
        "scenario": make_scenario(),
        "outbound_digests": {"outbound-1": TRIGGER_DIGEST},
        "actions": [make_action()],
    }
    values.update(changes)
    return SyntheticTranscript.create(**values)


def test_generate_scenario_uses_submitted_objective_and_only_one_mandate(tmp_path) -> None:
    request = launch_request()
    scenario = synthetic_module.build_coordination_scenario(
        request, seed=7, scenario_id="launch-run-001"
    )
    result = generate_scenario(
        scenario,
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        mandate_request=request.objective,
        include_change_story=False,
    )
    assert result.terminal_states == ("meeting_ready",)
    session_factory = create_session_factory(
        "sqlite:///" + result.database_path.as_posix()
    )
    repository = SqlAlchemyHumanWireRepository(session_factory)
    mandates = repository.list_recent_mandates(10)
    assert len(mandates) == 1
    assert mandates[0].redacted_request == request.objective
    session_factory.kw["bind"].dispose()


def test_generate_scenario_defaults_preserve_frozen_proof_contract(tmp_path) -> None:
    result = generate_scenario(
        default_synthetic_scenario(seed=0),
        tmp_path / "proof" / "transcript.json",
        tmp_path / "proof",
    )
    assert result.terminal_states == ("meeting_ready", "partial")


def test_schema_rejects_unsupported_version() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        make_scenario(schema_version="v999")


def test_schema_rejects_duplicate_action_ids() -> None:
    action = make_action()
    with pytest.raises(ValidationError, match="action IDs must be unique"):
        make_transcript(actions=[action, action])


def test_schema_rejects_unknown_persona() -> None:
    with pytest.raises(ValidationError, match="unknown persona"):
        make_transcript(actions=[make_action(persona_id="missing")])


def test_schema_rejects_channel_not_available_to_persona() -> None:
    with pytest.raises(ValidationError, match="does not support channel"):
        make_transcript(actions=[make_action(channel=Channel.TELEGRAM)])


def test_schema_rejects_non_monotonic_deterministic_action_order() -> None:
    later = make_action(action_id="owner-2", timestamp=NOW + timedelta(seconds=1), local_sequence=2)
    earlier = make_action(action_id="owner-3", timestamp=NOW, local_sequence=3)
    with pytest.raises(ValidationError, match="deterministic order"):
        make_transcript(
            outbound_digests={"outbound-1": TRIGGER_DIGEST, "outbound-2": "b" * 64},
            actions=[
                later,
                earlier.model_copy(update={"trigger_id": "outbound-2", "trigger_digest": "b" * 64}),
            ],
        )


def test_schema_requires_each_outbound_trigger_to_have_exactly_one_action() -> None:
    with pytest.raises(ValidationError, match="trigger pairing"):
        make_transcript(outbound_digests={"outbound-1": TRIGGER_DIGEST, "outbound-2": "b" * 64})


def test_schema_rejects_mismatched_trigger_digest() -> None:
    with pytest.raises(ValidationError, match="trigger digest"):
        make_transcript(actions=[make_action(trigger_digest="b" * 64)])


def test_schema_forbids_extra_fields() -> None:
    payload = make_scenario().model_dump()
    payload["untrusted"] = "no"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SyntheticScenario.model_validate(payload)


def test_schema_rejects_oversized_content() -> None:
    with pytest.raises(ValidationError, match="600"):
        make_action(content="x" * 601)


def test_provenance_rejects_real_identity_domains() -> None:
    with pytest.raises(ValidationError, match="example\\.test"):
        SyntheticPersona(
            persona_id="owner",
            display_name="Owner Persona",
            role="Operations owner",
            email="owner@real.example",
            channels=[Channel.EMAIL],
            allowed_intents=[SyntheticIntent.ACKNOWLEDGE],
        )


def test_provenance_requires_all_labels() -> None:
    payload = make_scenario().model_dump(mode="json")
    del payload["provenance"]
    with pytest.raises(ValidationError, match="provenance"):
        SyntheticScenario.model_validate(payload)


def test_seeded_identities_are_stable_distinct_and_synthetic() -> None:
    """Break caught: identities become ambient or omit generator provenance."""
    first = synthetic_module.default_synthetic_scenario(seed=8842)
    second = synthetic_module.default_synthetic_scenario(seed=8842)
    changed = synthetic_module.default_synthetic_scenario(seed=8843)

    assert first.model_dump_json() == second.model_dump_json()
    assert first.identity_seed == 8842
    assert first.identity_generator_version == "humanwire.synthetic-identities/v1"
    assert [persona.persona_id for persona in first.personas] == [
        persona.persona_id for persona in changed.personas
    ]
    assert len({persona.display_name for persona in first.personas}) == 9
    assert all(persona.email.endswith("@example.test") for persona in first.personas)
    assert [persona.display_name for persona in first.personas] != [
        persona.display_name for persona in changed.personas
    ]


def test_generation_never_reads_or_writes_private_live_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: synthetic generation consults an ambient live directory."""
    private_directory = tmp_path / "private-organization.json"
    private_directory.write_bytes(b'{"sentinel":"LIVE-DIRECTORY-BYTES"}')
    monkeypatch.setenv("ORGANIZATION_PATH", str(private_directory))

    result = generate_scenario(
        synthetic_module.default_synthetic_scenario(seed=77),
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
    )

    assert result.transcript.scenario.identity_seed == 77
    assert private_directory.read_bytes() == b'{"sentinel":"LIVE-DIRECTORY-BYTES"}'


def test_tampered_transcript_digest_fails_closed(tmp_path) -> None:
    path = tmp_path / "transcript.json"
    payload = make_transcript().model_dump(mode="json")
    payload["actions"][0]["content"] = "Tampered content."
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="digest mismatch"):
        load_transcript(path)


def make_generation_scenario() -> SyntheticScenario:
    return make_scenario(
        personas=[
            SyntheticPersona(
                persona_id="synthetic-manager",
                display_name="Manager Persona",
                role="Simulation manager",
                email="manager@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[SyntheticIntent.AVAILABILITY],
            ),
            SyntheticPersona(
                persona_id="inform",
                display_name="Inform Persona",
                role="Delivery owner",
                email="inform@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[SyntheticIntent.SILENCE],
            ),
            SyntheticPersona(
                persona_id="ack",
                display_name="Acknowledge Persona",
                role="Executive owner",
                email="ack@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[SyntheticIntent.ACKNOWLEDGE],
            ),
            SyntheticPersona(
                persona_id="quick",
                display_name="Quick Persona",
                role="Program owner",
                email="quick@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[
                    SyntheticIntent.ACKNOWLEDGE,
                    SyntheticIntent.ANSWER,
                    SyntheticIntent.CONFIRM_EVIDENCE,
                ],
            ),
            SyntheticPersona(
                persona_id="structured",
                display_name="Structured Persona",
                role="People owner",
                email="structured@example.test",
                channels=[Channel.EMAIL, Channel.TELEGRAM],
                allowed_intents=[
                    SyntheticIntent.ACKNOWLEDGE,
                    SyntheticIntent.INTERVIEW_RESPONSE,
                    SyntheticIntent.CONFIRM_EVIDENCE,
                ],
                private_facts=["PRIVATE-PERSONA-SENTINEL"],
            ),
            SyntheticPersona(
                persona_id="approval",
                display_name="Approval Persona",
                role="Approval owner",
                email="approval@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[SyntheticIntent.APPROVE, SyntheticIntent.CHANGE],
            ),
            SyntheticPersona(
                persona_id="availability",
                display_name="Availability Persona",
                role="Scheduling owner",
                email="availability@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[SyntheticIntent.AVAILABILITY],
            ),
        ]
    )


def _generate(tmp_path, scenario: SyntheticScenario | None = None):
    run_root = tmp_path / "run"
    output_path = run_root / "transcript.json"
    return generate_scenario(scenario or make_generation_scenario(), output_path, run_root)


def concurrent_generation_scenario() -> SyntheticScenario:
    return make_scenario(
        personas=[
            SyntheticPersona(
                persona_id="synthetic-manager",
                display_name="Manager Persona",
                role="Simulation manager",
                email="manager@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[SyntheticIntent.AVAILABILITY],
            ),
            SyntheticPersona(
                persona_id="beta",
                display_name="Beta Persona",
                role="Beta owner",
                email="beta@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[SyntheticIntent.ACKNOWLEDGE],
            ),
            SyntheticPersona(
                persona_id="alpha",
                display_name="Alpha Persona",
                role="Alpha owner",
                email="alpha@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[SyntheticIntent.ACKNOWLEDGE],
            ),
        ]
    )


def one_person_generation_scenario() -> SyntheticScenario:
    scenario = concurrent_generation_scenario()
    return scenario.model_copy(
        update={
            "personas": [
                persona
                for persona in scenario.personas
                if persona.persona_id != "alpha"
            ]
        }
    )


def test_model_decisions_may_overlap_but_commit_in_canonical_order(tmp_path) -> None:
    """Break caught: worker completion timing determines public action order."""
    factory = FixtureDecisionEngineFactory(
        mode="ack",
        delay_seconds=0.35,
        model_identifier="fixture/delayed",
    )
    serial_started = time.monotonic()
    generate_scenario(
        concurrent_generation_scenario(),
        tmp_path / "serial" / "transcript.json",
        tmp_path / "serial",
        decision_engine=factory,
        max_decision_workers=1,
    )
    serial_elapsed = time.monotonic() - serial_started
    parallel_started = time.monotonic()
    first = generate_scenario(
        concurrent_generation_scenario(),
        tmp_path / "a" / "transcript.json",
        tmp_path / "a",
        decision_engine=factory,
        max_decision_workers=2,
    )
    parallel_elapsed = time.monotonic() - parallel_started
    second = generate_scenario(
        concurrent_generation_scenario(),
        tmp_path / "b" / "transcript.json",
        tmp_path / "b",
        decision_engine=factory,
        max_decision_workers=2,
    )

    assert parallel_elapsed + 0.15 < serial_elapsed
    assert first.transcript.model_dump_json() == second.transcript.model_dump_json()
    keys = [
        synthetic_module.canonical_action_order(first.transcript.scenario, action)
        for action in first.transcript.actions
    ]
    assert keys == sorted(keys)
    assert [action.persona_id for action in first.transcript.actions] == ["beta", "alpha"]


def test_model_decision_worker_bound_is_clamped_to_eight(tmp_path) -> None:
    """Break caught: an operator-supplied worker count creates unbounded model calls."""
    scenario = concurrent_generation_scenario()
    contracts = list(scenario.personas)
    for index in range(3, 10):
        contracts.append(
            SyntheticPersona(
                persona_id=f"worker-{index}",
                display_name=f"Worker {index}",
                role=f"Worker owner {index}",
                email=f"worker-{index}@example.test",
                channels=[Channel.EMAIL],
                allowed_intents=[SyntheticIntent.ACKNOWLEDGE],
            )
        )
    result = generate_scenario(
        scenario.model_copy(update={"personas": contracts}),
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        decision_engine=FixtureDecisionEngineFactory(
            mode="concurrency_probe",
            delay_seconds=0.25,
            concurrency_target=9,
            model_identifier="fixture/measuring",
        ),
        max_decision_workers=100,
    )

    assert len(result.transcript.actions) == 9
    assert {action.content for action in result.transcript.actions} == {
        "Observed concurrency 8."
    }


def test_one_persona_has_only_one_model_decision_in_flight(tmp_path) -> None:
    """Break caught: duplicate deliveries concurrently mutate one persona's local state."""
    scenario = make_generation_scenario()
    result = generate_scenario(
        scenario.model_copy(
            update={
                "personas": [
                    persona
                    for persona in scenario.personas
                    if persona.persona_id in {"synthetic-manager", "quick"}
                ]
            }
        ),
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        decision_engine=FixtureDecisionEngineFactory(
            mode="tracking",
            delay_seconds=0.01,
            model_identifier="fixture/tracking",
        ),
        max_decision_workers=8,
    )

    quick_actions = [
        action for action in result.transcript.actions if action.persona_id == "quick"
    ]
    assert [action.local_sequence for action in quick_actions] == [1, 2, 3]
    assert [action.trigger_id for action in quick_actions] == [
        "outbound-1",
        "outbound-2",
        "outbound-3",
    ]


def test_model_failure_records_error_without_gateway_authority(tmp_path) -> None:
    """Break caught: model exception details or partial output reach the gateway."""
    result = generate_scenario(
        one_person_generation_scenario(),
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        decision_engine=FixtureDecisionEngineFactory(
            mode="failure",
            value="timeout",
            model_identifier="fixture/failing",
        ),
    )

    action = result.transcript.actions[0]
    assert action.intent is SyntheticIntent.ERROR
    assert action.content == "synthetic_model_timeout"
    assert result.inbound_envelopes == ()


def test_model_decision_disallowed_intent_is_inert(tmp_path) -> None:
    """Break caught: an injected engine bypasses the persona intent contract."""

    result = generate_scenario(
        one_person_generation_scenario(),
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        decision_engine=FixtureDecisionEngineFactory(
            mode="disallowed",
            model_identifier="fixture/disallowed",
        ),
    )

    assert result.transcript.actions[0].intent is SyntheticIntent.ERROR
    assert result.transcript.actions[0].content == "synthetic_model_invalid_output"
    assert result.inbound_envelopes == ()


@pytest.mark.parametrize(
    "unsafe_content",
    ["PRIVATE-PERSONA-SENTINEL", "sender_address=forged"],
)
def test_generic_engine_private_or_identity_content_is_inert(
    tmp_path, unsafe_content
) -> None:
    """Break caught: central generation trusts privacy checks unique to one adapter."""

    scenario = one_person_generation_scenario()
    personas = list(scenario.personas)
    personas[1] = personas[1].model_copy(
        update={"private_facts": ["PRIVATE-PERSONA-SENTINEL"]}
    )
    result = generate_scenario(
        scenario.model_copy(update={"personas": personas}),
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        decision_engine=FixtureDecisionEngineFactory(
            mode="unsafe",
            value=unsafe_content,
            model_identifier="fixture/unsafe-content",
        ),
    )

    assert result.transcript.actions[0].intent is SyntheticIntent.ERROR
    assert result.transcript.actions[0].content == "synthetic_model_invalid_output"
    assert unsafe_content not in result.transcript.model_dump_json()
    assert unsafe_content not in (tmp_path / "run" / "transcript.json").read_text()
    assert result.inbound_envelopes == ()


def test_engine_without_cooperative_deadline_contract_is_never_called(tmp_path) -> None:
    """Break caught: generation silently invokes an unbounded legacy engine shape."""
    marker = tmp_path / "legacy-engine-was-called"
    result = generate_scenario(
        one_person_generation_scenario(),
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        decision_engine=FixtureDecisionEngineFactory(
            mode="legacy",
            value=str(marker),
            model_identifier="fixture/legacy-shape",
        ),
    )

    assert not marker.exists()
    assert result.transcript.actions[0].intent is SyntheticIntent.ERROR
    assert result.transcript.actions[0].content == "synthetic_model_invalid_output"
    assert result.inbound_envelopes == ()


def test_cooperative_late_result_is_cancelled_and_never_committed(
    tmp_path, monkeypatch
) -> None:
    """Break caught: a late valid decision is accepted or leaves model work running."""

    monkeypatch.setattr(
        synthetic_module,
        "MODEL_DECISION_TIMEOUT_SECONDS",
        0.05,
        raising=False,
    )
    monkeypatch.setattr(
        synthetic_module,
        "MODEL_DECISION_CANCELLATION_GRACE_SECONDS",
        0.2,
        raising=False,
    )
    started = time.monotonic()
    result = generate_scenario(
        one_person_generation_scenario(),
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        decision_engine=FixtureDecisionEngineFactory(
            mode="cooperative_late",
            model_identifier="fixture/cooperative-late",
        ),
    )
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert result.transcript.actions[0].intent is SyntheticIntent.ERROR
    assert result.transcript.actions[0].content == "synthetic_model_timeout"
    transcript_bytes = (tmp_path / "run" / "transcript.json").read_bytes()
    sidecar_bytes = (tmp_path / "run" / "provenance.json").read_bytes()
    time.sleep(0.05)
    assert (tmp_path / "run" / "transcript.json").read_bytes() == transcript_bytes
    assert (tmp_path / "run" / "provenance.json").read_bytes() == sidecar_bytes
    assert not any(
        thread.name.startswith("humanwire-persona") for thread in threading.enumerate()
    )


def test_uncooperative_engine_is_hard_timed_out_without_a_surviving_worker(
    tmp_path: Path,
) -> None:
    """Break caught: a lying cooperative engine blocks generation and leaks its worker."""
    probe = Path(__file__).with_name("persona_timeout_probe.py")
    process = subprocess.Popen(
        [sys.executable, str(probe), str(tmp_path / "run")],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=5)
        pytest.fail(
            "uncooperative persona decision exceeded the subprocess bound; "
            f"stdout={stdout!r}, stderr={stderr!r}"
        )

    assert process.returncode == 0, stderr
    payload = json.loads(stdout)
    assert payload["elapsed_seconds"] < 2
    assert payload["actions"] == [
        {"intent": "error", "content": "synthetic_model_timeout"}
    ]
    assert payload["inbound_count"] == 0
    assert payload["live_children"] == []
    assert payload["live_workers"] == []


def test_hard_batch_timeout_preserves_only_decisions_completed_before_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: one hung persona discards a distinct persona's timely decision."""
    monkeypatch.setattr(synthetic_module, "MODEL_DECISION_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(synthetic_module, "MODEL_DECISION_CANCELLATION_GRACE_SECONDS", 0.1)

    result = generate_scenario(
        concurrent_generation_scenario(),
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        decision_engine=FixtureDecisionEngineFactory(
            mode="partial_timeout",
            model_identifier="fixture/partial-timeout",
        ),
        max_decision_workers=2,
    )

    actions = {action.persona_id: action for action in result.transcript.actions}
    assert actions["beta"].intent is SyntheticIntent.ACKNOWLEDGE
    assert actions["beta"].content == "Acknowledged."
    assert actions["alpha"].intent is SyntheticIntent.ERROR
    assert actions["alpha"].content == "synthetic_model_timeout"
    assert [attempt.persona_id for attempt in result.inbound_envelopes] == ["beta"]
    assert not synthetic_module.multiprocessing.active_children()


def test_completed_decision_expiring_before_final_acceptance_is_inert(
    tmp_path, monkeypatch
) -> None:
    """Break caught: coordinator wake-up accepts a decision after its batch deadline."""
    real_decode = synthetic_module._decode_isolated_result

    def delayed_coordinator_wake(raw, candidates):
        result = real_decode(raw, candidates)
        time.sleep(0.6)
        return result

    monkeypatch.setattr(
        synthetic_module,
        "MODEL_DECISION_TIMEOUT_SECONDS",
        0.5,
        raising=False,
    )
    monkeypatch.setattr(
        synthetic_module,
        "_decode_isolated_result",
        delayed_coordinator_wake,
    )

    result = generate_scenario(
        one_person_generation_scenario(),
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        decision_engine=FixtureDecisionEngineFactory(),
    )

    assert result.transcript.actions[0].intent is SyntheticIntent.ERROR
    assert result.transcript.actions[0].content == "synthetic_model_timeout"
    assert result.inbound_envelopes == ()


def test_zero_offset_uses_the_decision_context_virtual_time(tmp_path) -> None:
    """Break caught: a valid zero offset creates an action before its own context."""
    result = generate_scenario(
        one_person_generation_scenario(),
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        decision_engine=FixtureDecisionEngineFactory(
            mode="zero",
            model_identifier="fixture/zero-offset",
        ),
    )

    action = result.transcript.actions[0]
    assert action.timestamp == datetime.fromisoformat(
        action.content.removeprefix("Context time ")
    )
    assert result.inbound_envelopes


def test_worker_count_preserves_repeated_trigger_order_and_transcript(tmp_path) -> None:
    """Break caught: parallelism changes saved trigger order for a repeated persona."""

    scenario = make_generation_scenario()
    scenario = scenario.model_copy(
        update={
            "personas": [
                persona
                for persona in scenario.personas
                if persona.persona_id in {"synthetic-manager", "ack", "quick"}
            ]
        }
    )
    serial = generate_scenario(
        scenario,
        tmp_path / "serial" / "transcript.json",
        tmp_path / "serial",
        decision_engine=FixtureDecisionEngineFactory(
            mode="workflow",
            model_identifier="fixture/worker-invariance",
        ),
        max_decision_workers=1,
    )
    parallel = generate_scenario(
        scenario,
        tmp_path / "parallel" / "transcript.json",
        tmp_path / "parallel",
        decision_engine=FixtureDecisionEngineFactory(
            mode="workflow",
            model_identifier="fixture/worker-invariance",
        ),
        max_decision_workers=8,
    )

    assert serial.transcript.model_dump_json() == parallel.transcript.model_dump_json()
    quick_actions = [
        action for action in serial.transcript.actions if action.persona_id == "quick"
    ]
    assert [action.trigger_id for action in quick_actions] == [
        "outbound-2",
        "outbound-3",
        "outbound-4",
    ]
    assert [action.local_sequence for action in quick_actions] == [1, 2, 3]
    assert [
        synthetic_module.canonical_action_order(serial.transcript.scenario, action)
        for action in serial.transcript.actions
    ] == sorted(
        synthetic_module.canonical_action_order(serial.transcript.scenario, action)
        for action in serial.transcript.actions
    )


def test_same_trigger_retry_is_visible_once_only_and_worker_invariant(
    tmp_path, monkeypatch
) -> None:
    """Break caught: a same-message retry duplicates workflow authority or disappears."""

    def emit_twice(client, envelope, record_attempt) -> None:
        for _ in range(2):
            record_attempt()
            client.emit_inbound(envelope)

    monkeypatch.setattr(
        synthetic_module,
        "_emit_persona_inbound_attempts",
        emit_twice,
    )
    serial = generate_scenario(
        one_person_generation_scenario(),
        tmp_path / "serial" / "transcript.json",
        tmp_path / "serial",
        decision_engine=FixtureDecisionEngineFactory(),
        max_decision_workers=1,
    )
    parallel = generate_scenario(
        one_person_generation_scenario(),
        tmp_path / "parallel" / "transcript.json",
        tmp_path / "parallel",
        decision_engine=FixtureDecisionEngineFactory(),
        max_decision_workers=8,
    )

    assert [(item.action_id, item.trigger_id) for item in serial.transcript.actions] == [
        ("beta-1", "outbound-1")
    ]
    assert [item.message_id for item in serial.inbound_envelopes] == [
        "synthetic-beta-1",
        "synthetic-beta-1",
    ]
    assert serial.gateway_handler_count == parallel.gateway_handler_count == 1
    serial_trace = synthetic_module._semantic_trace(serial)
    parallel_trace = synthetic_module._semantic_trace(parallel)
    assert [item["action"] for item in serial_trace["inbound_attempts"]] == [
        "beta-1",
        "beta-1",
    ]
    assert [
        event["type"]
        for event in serial_trace["events"]
        if event["type"] == "stakeholder.acknowledged"
    ] == ["stakeholder.acknowledged"]
    assert serial.transcript.model_dump_json() == parallel.transcript.model_dump_json()
    assert serial.inbound_envelopes == parallel.inbound_envelopes
    assert serial_trace["inbound_attempts"] == parallel_trace["inbound_attempts"]
    assert synthetic_module.semantic_trace_hash(serial) == (
        synthetic_module.semantic_trace_hash(parallel)
    )


def test_model_decision_writes_only_strict_private_provenance_sidecar(tmp_path) -> None:
    """Break caught: prompts, decisions, routing, or diagnostics enter public provenance."""
    result = generate_scenario(
        one_person_generation_scenario(),
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        decision_engine=FixtureDecisionEngineFactory(
            mode="failure",
            value="timeout",
            model_identifier="fixture/failing",
        ),
    )

    assert result.mode.value == "model_assisted"
    assert result.model_identifier == "fixture/failing"
    assert result.provenance_path == tmp_path / "run" / "provenance.json"
    payload = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    assert set(payload) == {
        "schema_version",
        "mode",
        "model_identifier",
        "prompt_version",
        "identity_seed",
        "transcript_sha256",
        "provenance",
    }
    assert payload["transcript_sha256"] == result.transcript.digest
    serialized = result.provenance_path.read_text(encoding="utf-8")
    assert "synthetic_model_timeout" not in serialized
    assert "Acknowledged." not in serialized
    assert "sender_address" not in serialized
    assert "conversation_id" not in serialized
    assert "PRIVATE-PERSONA-SENTINEL" not in serialized


def test_model_sidecar_write_failure_does_not_rewrite_transcript(
    tmp_path, monkeypatch
) -> None:
    """Break caught: sidecar failure retries effects or overwrites public transcript."""
    original_write = synthetic_module._write_transcript_exclusively

    def fail_sidecar(path, root, content):
        if path.name == "provenance.json":
            raise OSError("PRIVATE-DIAGNOSTIC-SENTINEL")
        return original_write(path, root, content)

    monkeypatch.setattr(
        synthetic_module,
        "_write_transcript_exclusively",
        fail_sidecar,
    )
    transcript_path = tmp_path / "run" / "transcript.json"

    with pytest.raises(OSError, match="PRIVATE-DIAGNOSTIC-SENTINEL"):
        generate_scenario(
            one_person_generation_scenario(),
            transcript_path,
            tmp_path / "run",
            decision_engine=FixtureDecisionEngineFactory(
                mode="failure",
                value="timeout",
                model_identifier="fixture/failing",
            ),
        )

    assert load_transcript(transcript_path).actions[0].content == "synthetic_model_timeout"
    assert not (tmp_path / "run" / "provenance.json").exists()


def test_generation_runs_six_distinct_deterministic_persona_policies(tmp_path) -> None:
    result = _generate(tmp_path)
    intents = {
        persona.persona_id: [
            action.intent
            for action in result.transcript.actions
            if action.persona_id == persona.persona_id
        ]
        for persona in result.transcript.scenario.personas
    }

    assert intents["inform"] == [SyntheticIntent.SILENCE]
    assert intents["ack"] == [SyntheticIntent.ACKNOWLEDGE]
    assert intents["quick"] == [
        SyntheticIntent.ACKNOWLEDGE,
        SyntheticIntent.ANSWER,
        SyntheticIntent.CONFIRM_EVIDENCE,
    ]
    assert intents["structured"] == [
        SyntheticIntent.ACKNOWLEDGE,
        SyntheticIntent.INTERVIEW_RESPONSE,
        SyntheticIntent.INTERVIEW_RESPONSE,
        SyntheticIntent.INTERVIEW_RESPONSE,
        SyntheticIntent.CONFIRM_EVIDENCE,
    ]
    assert intents["approval"] == [SyntheticIntent.APPROVE]
    assert intents["availability"] == [SyntheticIntent.AVAILABILITY]


def test_default_scenario_executes_the_approved_primary_and_change_stories(tmp_path) -> None:
    """Break caught: the frozen proof stops at counts instead of the approved outcomes."""
    result = _generate(tmp_path, synthetic_module.default_synthetic_scenario())
    trace = synthetic_module._semantic_trace(result)

    contracts = {assignment["engagement_type"] for assignment in trace["assignments"]}
    assert contracts == {
        "inform",
        "acknowledge",
        "quick_response",
        "structured_interview",
        "review_approval",
        "availability",
    }
    quick_people = {
        assignment["person"]
        for assignment in trace["assignments"]
        if assignment["engagement_type"] == "quick_response"
    }
    assert quick_people == {"quick-a", "quick-b"}

    mandates_by_state = {mandate["state"]: mandate for mandate in trace["mandates"]}
    assert set(mandates_by_state) == {"meeting_ready", "partial"}
    primary_token = mandates_by_state["meeting_ready"]["mandate"]
    change_token = mandates_by_state["partial"]["mandate"]

    primary_events = [
        event for event in trace["events"] if event["mandate"] == primary_token
    ]
    primary_types = {event["type"] for event in primary_events}
    assert {
        "outreach.alternate_sent",
        "interview.evidence_confirmed",
        "engagement.decision_recorded",
        "availability.recorded",
        "mandate.meeting_required",
        "meeting.package_created",
        "mandate.meeting_ready",
    } <= primary_types
    assert [
        proposal["round"]
        for proposal in trace["proposals"]
        if proposal["mandate"] == primary_token
    ] == [1, 2]
    primary_decisions = [
        decision
        for decision in trace["engagement_decisions"]
        if decision["assignment"].startswith(f"{primary_token}/")
    ]
    assert [decision["response"] for decision in primary_decisions] == ["approve"]
    meeting = next(
        item for item in trace["meetings"] if item["mandate"] == primary_token
    )
    assert meeting["required_attendees"] == ["structured", "synthetic-manager"]
    assert meeting["proposed_start"] == "2026-08-13T15:00:00Z"
    assert meeting["proposed_end"] == "2026-08-13T15:30:00Z"
    assert {
        action["persona"]
        for action in trace["actions"]
        if action["intent"] == "availability"
    } == {"availability", "structured", "synthetic-manager"}

    structured_actions = [
        action for action in trace["actions"] if action["persona"] == "structured"
    ]
    assert structured_actions[0]["channel"] == "email"
    assert any(action["channel"] == "telegram" for action in structured_actions)
    assert any(
        action["channel"] == "telegram" and action["intent"] == "confirm_evidence"
        for action in structured_actions
    )
    assert result.gateway_handler_count == 1
    assert {
        item["action"] for item in trace["inbound_attempts"]
    } == {
        action["action_id"]
        for action in trace["actions"]
        if action["intent"] != "silence"
    }

    change_decisions = [
        decision
        for decision in trace["engagement_decisions"]
        if decision["assignment"].startswith(f"{change_token}/")
    ]
    assert [decision["response"] for decision in change_decisions] == ["change"]
    assert not any(
        proposal["mandate"] == change_token for proposal in trace["proposals"]
    )
    assert not any(meeting["mandate"] == change_token for meeting in trace["meetings"])


def test_frozen_replay_preserves_both_terminal_outcomes_and_semantic_hash(tmp_path) -> None:
    """Break caught: checked-in replay diverges from generation or drops one scenario."""
    generated = generate_scenario(
        synthetic_module.default_synthetic_scenario(),
        tmp_path / "generated" / "transcript.json",
        tmp_path / "generated",
    )
    frozen = synthetic_module.replay_transcript(
        Path("tests/fixtures/humanwire/synthetic_launch_v1.json"),
        tmp_path / "frozen",
    )

    assert generated.terminal_states == ("meeting_ready", "partial")
    assert frozen.terminal_states == generated.terminal_states
    assert synthetic_module.semantic_trace_hash(frozen) == (
        synthetic_module.semantic_trace_hash(generated)
    )


def _policy_object_graph(policy) -> tuple[set[str], list[object]]:
    names: set[str] = set()
    values: list[object] = []
    pending = [policy, policy.respond, policy._choose]
    seen: set[int] = set()
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        values.append(value)
        if isinstance(value, (str, bytes, int, float, bool, type(None), Enum, type)):
            continue
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
            continue
        if isinstance(value, (list, tuple, set, frozenset)):
            pending.extend(value)
            continue
        attributes = vars(value) if hasattr(value, "__dict__") else {}
        names.update(attributes)
        pending.extend(attributes.values())
        function = value.__func__ if inspect.ismethod(value) else value
        closure = getattr(function, "__closure__", None) or ()
        pending.extend(cell.cell_contents for cell in closure)
    return names, values


def test_policy_instances_expose_only_sanitized_profile_and_local_state() -> None:
    scenario = make_generation_scenario()
    forbidden_names = {
        "persona",
        "persona_id",
        "email",
        "channels",
        "display_name",
        "route",
        "sender_address",
        "connection_id",
        "conversation_id",
        "message_id",
        "database",
        "repository",
        "system_log",
        "expected_state",
        "outcome",
        "directory",
        "scenario",
        "people",
    }

    for persona in scenario.personas:
        policy = synthetic_module._build_policy(persona)
        attribute_names, reachable = _policy_object_graph(policy)
        primitive_values = {
            value for value in reachable if isinstance(value, (str, bytes, Enum))
        }

        assert set(vars(policy)) == {"profile", "complete"}
        assert set(vars(policy.profile)) == {
            "role",
            "private_facts",
            "allowed_intents",
            "engagement_contract",
        }
        assert policy.profile.model_config["frozen"] is True
        assert forbidden_names.isdisjoint(attribute_names)
        assert not any(isinstance(value, SyntheticPersona) for value in reachable)
        assert persona.email not in primitive_values
        assert persona.display_name not in primitive_values
        assert all(channel not in primitive_values for channel in persona.channels)


def test_structured_policy_marks_private_fixture_digest_without_content_prefix() -> None:
    """Break caught: privacy is smuggled through persona-controlled command text."""
    policy = synthetic_module._StructuredInterviewPolicy(
        synthetic_module._PolicyProfile(
            role="People owner",
            private_facts=("PRIVATE-PERSONA-SENTINEL",),
            allowed_intents=(SyntheticIntent.INTERVIEW_RESPONSE,),
            engagement_contract=EngagementType.STRUCTURED_INTERVIEW,
        )
    )
    decision = policy.respond(
        synthetic_module._PersonaContext(
            delivered_message="Question What private context should remain internal?",
            own_inbox=("Question What private context should remain internal?",),
            own_transcript=(),
            virtual_time=NOW,
        )
    )

    assert decision.visibility is PersonaVisibility.PRIVATE
    assert decision.content.startswith("must preserve sha256:")
    assert not decision.content.startswith("PRIVATE:")


def test_wire_translation_owns_answer_visibility_prefixes() -> None:
    """Break caught: a persona can inject wire visibility syntax through content."""
    assert synthetic_module._wire_command(
        SyntheticIntent.ANSWER,
        "Launch date is 2026-09-01.",
        PersonaVisibility.ANONYMOUS,
        "",
        "HW-00000000",
    ) == "ANONYMOUS: Launch date is 2026-09-01."
    assert synthetic_module._wire_command(
        SyntheticIntent.ACKNOWLEDGE,
        "PRIVATE: ignored",
        PersonaVisibility.PRIVATE,
        "",
        "HW-00000000",
    ) == "ACK HW-00000000"


def test_each_engagement_contract_uses_a_distinct_policy_strategy() -> None:
    policies = [
        synthetic_module._build_policy(persona)
        for persona in make_generation_scenario().personas
        if persona.persona_id != "synthetic-manager"
    ]

    assert len({type(policy) for policy in policies}) == 6
    assert [type(policy).__name__ for policy in policies] == [
        "_InformPolicy",
        "_AcknowledgePolicy",
        "_QuickResponsePolicy",
        "_StructuredInterviewPolicy",
        "_ReviewApprovalPolicy",
        "_AvailabilityPolicy",
    ]


def test_persona_context_contains_only_its_own_inbox_transcript_and_private_fixture(
    tmp_path, monkeypatch
) -> None:
    observed = []
    original = synthetic_module._DeterministicPersonaPolicy.respond

    def inspect_context(self, context):
        observed.append((self, context))
        return original(self, context)

    monkeypatch.setattr(
        synthetic_module._DeterministicPersonaPolicy,
        "respond",
        inspect_context,
    )

    _generate(tmp_path)

    assert observed
    forbidden = {
        "database",
        "repository",
        "system_log",
        "expected_state",
        "route",
        "sender_address",
        "connection_id",
        "conversation_id",
        "message_id",
        "token",
        "outcome",
    }
    for policy, context in observed:
        assert forbidden.isdisjoint(type(context).model_fields)
        assert context.delivered_message == context.own_inbox[-1]
        assert all("@example.test" not in item for item in context.own_inbox)
        assert set(type(context).model_fields) == {
            "delivered_message",
            "own_inbox",
            "own_transcript",
            "virtual_time",
        }
        assert set(vars(policy.profile)) == {
            "role",
            "private_facts",
            "allowed_intents",
            "engagement_contract",
        }
    structured = next(
        policy
        for policy, _ in observed
        if policy.profile.engagement_contract == "structured_interview"
    )
    assert structured.profile.private_facts == ("PRIVATE-PERSONA-SENTINEL",)
    assert all(
        "PRIVATE-PERSONA-SENTINEL" not in context.delivered_message
        for policy, context in observed
        if policy.profile.engagement_contract != "structured_interview"
    )


def test_two_fresh_generations_write_identical_safe_transcripts(tmp_path) -> None:
    scenario = make_generation_scenario()
    first_output = tmp_path / "run-a" / "transcript.json"
    second_output = tmp_path / "run-b" / "transcript.json"

    first = generate_scenario(scenario, first_output, tmp_path / "run-a")
    second = generate_scenario(scenario, second_output, tmp_path / "run-b")

    assert first.transcript == second.transcript
    assert first_output.read_bytes() == second_output.read_bytes()
    safe_bytes = first_output.read_bytes()
    assert b"PRIVATE-PERSONA-SENTINEL" not in safe_bytes
    assert hashlib.sha256(b"PRIVATE-PERSONA-SENTINEL").hexdigest().encode() in safe_bytes
    assert first.mode.value == "deterministic"
    assert first.model_identifier is None
    assert first.provenance_path is None
    assert not (tmp_path / "run-a" / "provenance.json").exists()


def test_generation_orders_actions_by_saved_scenario_rank_and_trigger(tmp_path) -> None:
    result = _generate(tmp_path)
    keys = [
        synthetic_module.canonical_action_order(result.transcript.scenario, action)
        for action in result.transcript.actions
    ]

    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))


def test_generation_uses_fresh_file_database_and_ignores_ambient_settings(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://ambient.invalid/humanwire")
    monkeypatch.setenv("FEATHERLESS_API_KEY", "ambient-model-secret")
    result = _generate(tmp_path)

    assert result.database_path.is_file()
    assert result.database_path.parent == tmp_path / "run"
    assert result.model_client_configured is False


def test_timeout_and_invalid_persona_output_are_recorded_without_inbound(
    tmp_path, monkeypatch
) -> None:
    class BrokenPolicy:
        def __init__(self, failure):
            self.failure = failure

        def respond(self, context):
            if self.failure == "timeout":
                raise TimeoutError("synthetic deadline")
            return {"intent": "forged", "sender_address": "attacker@example.test"}

    original = synthetic_module._build_policy

    def broken_for(persona):
        if persona.persona_id == "ack":
            return BrokenPolicy("timeout")
        if persona.persona_id == "approval":
            return BrokenPolicy("invalid")
        return original(persona)

    monkeypatch.setattr(synthetic_module, "_build_policy", broken_for)
    result = _generate(tmp_path)
    by_persona = {
        action.persona_id: action
        for action in result.transcript.actions
        if action.persona_id in {"ack", "approval"}
    }

    assert by_persona["ack"].intent is SyntheticIntent.SILENCE
    assert by_persona["ack"].content == "synthetic_timeout"
    assert by_persona["approval"].intent is SyntheticIntent.ERROR
    assert by_persona["approval"].content == "synthetic_invalid_output"
    assert {item.persona_id for item in result.inbound_envelopes}.isdisjoint(
        {"ack", "approval"}
    )


def test_all_persona_inbound_uses_one_gateway_handler_and_orchestrator_identity(tmp_path) -> None:
    result = _generate(tmp_path)

    assert result.gateway_handler_count == 1
    assert result.inbound_envelopes
    for envelope in result.inbound_envelopes:
        assert envelope.message_id.startswith(f"synthetic-{envelope.persona_id}-")
        assert envelope.sender_address == f"{envelope.persona_id}@example.test"
        assert envelope.connection_id == f"offline-{envelope.channel.value}-connection"
        assert envelope.conversation_id == f"synthetic-{envelope.persona_id}-conversation"


def _write_transcript(path: Path, transcript: SyntheticTranscript) -> None:
    path.write_text(transcript.model_dump_json(indent=2) + "\n", encoding="utf-8")


def test_generated_and_replayed_runs_have_the_same_semantic_trace_hash(tmp_path) -> None:
    generated_path = tmp_path / "generate-run" / "transcript.json"
    generated = generate_scenario(
        make_generation_scenario(),
        generated_path,
        tmp_path / "generate-run",
    )

    replayed = synthetic_module.replay_transcript(
        generated_path,
        tmp_path / "replay-run",
    )

    assert synthetic_module.semantic_trace_hash(generated) == (
        synthetic_module.semantic_trace_hash(replayed)
    )


def test_semantic_trace_hash_is_independent_of_uuids_and_temporary_paths(tmp_path) -> None:
    first = generate_scenario(
        make_generation_scenario(),
        tmp_path / "first-run" / "transcript.json",
        tmp_path / "first-run",
    )
    second = generate_scenario(
        make_generation_scenario(),
        tmp_path / "second-run" / "transcript.json",
        tmp_path / "second-run",
    )

    assert first.database_path != second.database_path
    assert first.database_path.read_bytes() != second.database_path.read_bytes()
    assert synthetic_module.semantic_trace_hash(first) == (
        synthetic_module.semantic_trace_hash(second)
    )


def test_replay_never_builds_or_calls_persona_policies(tmp_path, monkeypatch) -> None:
    generated_path = tmp_path / "generate-run" / "transcript.json"
    generate_scenario(
        make_generation_scenario(),
        generated_path,
        tmp_path / "generate-run",
    )

    def policy_use_is_a_failure(*args, **kwargs):
        del args, kwargs
        raise AssertionError("replay invoked persona generation")

    monkeypatch.setattr(synthetic_module, "_build_policy", policy_use_is_a_failure)
    monkeypatch.setattr(synthetic_module, "_profile_for", policy_use_is_a_failure)
    monkeypatch.setattr(
        synthetic_module,
        "_evaluate_model_batch",
        policy_use_is_a_failure,
    )

    replayed = synthetic_module.replay_transcript(
        generated_path,
        tmp_path / "replay-run",
    )

    assert replayed.gateway_handler_count == 1
    assert replayed.inbound_envelopes
    assert replayed.mode.value == "frozen_replay"
    assert replayed.model_identifier is None
    assert replayed.provenance_path is None


def test_frozen_replay_hash_is_equal_after_a_fresh_replay_restart(tmp_path) -> None:
    fixture = Path("tests/fixtures/humanwire/synthetic_launch_v1.json")

    first = synthetic_module.replay_transcript(fixture, tmp_path / "restart-a")
    second = synthetic_module.replay_transcript(fixture, tmp_path / "restart-b")

    assert synthetic_module.semantic_trace_hash(first) == (
        synthetic_module.semantic_trace_hash(second)
    )


def test_semantic_trace_hash_changes_for_a_material_action_change(tmp_path) -> None:
    original_path = tmp_path / "generate-run" / "transcript.json"
    original = generate_scenario(
        make_generation_scenario(),
        original_path,
        tmp_path / "generate-run",
    )
    changed_actions = list(original.transcript.actions)
    changed_actions[0] = changed_actions[0].model_copy(
        update={"content": "Acknowledged with a safe material change."}
    )
    changed_transcript = SyntheticTranscript.create(
        scenario=original.transcript.scenario,
        outbound_digests=original.transcript.outbound_digests,
        actions=changed_actions,
    )
    changed_path = tmp_path / "changed.json"
    _write_transcript(changed_path, changed_transcript)

    changed = synthetic_module.replay_transcript(
        changed_path,
        tmp_path / "changed-run",
    )

    assert synthetic_module.semantic_trace_hash(original) != (
        synthetic_module.semantic_trace_hash(changed)
    )


def test_duplicate_inbound_attempt_remains_visible_in_semantic_hash(tmp_path) -> None:
    result = _generate(tmp_path)
    duplicated = replace(
        result,
        inbound_envelopes=(result.inbound_envelopes[0], *result.inbound_envelopes),
    )

    assert synthetic_module.semantic_trace_hash(result) != (
        synthetic_module.semantic_trace_hash(duplicated)
    )


def test_replay_fails_closed_on_ambiguous_synthetic_identity_mapping(tmp_path) -> None:
    generated_path = tmp_path / "generate-run" / "transcript.json"
    generated = generate_scenario(
        make_generation_scenario(),
        generated_path,
        tmp_path / "generate-run",
    )
    personas = list(generated.transcript.scenario.personas)
    ack = next(persona for persona in personas if persona.persona_id == "ack")
    approval_index = next(
        index for index, persona in enumerate(personas) if persona.persona_id == "approval"
    )
    personas[approval_index] = personas[approval_index].model_copy(
        update={"email": ack.email}
    )
    ambiguous_scenario = generated.transcript.scenario.model_copy(
        update={"personas": personas}
    )
    ambiguous_transcript = SyntheticTranscript.create(
        scenario=ambiguous_scenario,
        outbound_digests=generated.transcript.outbound_digests,
        actions=list(generated.transcript.actions),
    )
    ambiguous_path = tmp_path / "ambiguous.json"
    _write_transcript(ambiguous_path, ambiguous_transcript)

    with pytest.raises(ValueError, match="ambiguous synthetic identity"):
        synthetic_module.replay_transcript(ambiguous_path, tmp_path / "replay-run")


def _parse_safe_cli_output(value: str) -> dict[str, str]:
    lines = value.splitlines()
    assert lines
    assert all(line.count("=") == 1 for line in lines)
    return dict(line.split("=", 1) for line in lines)


def test_generate_requires_explicit_output_and_run_root(capsys) -> None:
    from humanwire.__main__ import main

    with pytest.raises(SystemExit) as caught:
        main(["synthetic", "generate"])
    captured = capsys.readouterr()

    assert caught.value.code != 0
    assert captured.out == ""
    assert "--output" in captured.err
    assert "--run-root" in captured.err


def test_watch_cli_binds_only_loopback_and_starts_generation_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: watch exposes a configurable/public bind or skips generation."""
    from humanwire.__main__ import main

    calls: dict[str, object] = {}

    def fake_uvicorn_run(app, *, host, port, **kwargs):
        calls.update(app=app, host=host, port=port, kwargs=kwargs)

    monkeypatch.setattr("humanwire.synthetic_watch.uvicorn.run", fake_uvicorn_run)
    run_root = tmp_path / "run"

    assert main(
        [
            "synthetic",
            "watch",
            "--output",
            str(run_root / "transcript.json"),
            "--run-root",
            str(run_root),
            "--seed",
            "8842",
            "--agent-mode",
            "deterministic",
            "--port",
            "8766",
            "--step-delay-ms",
            "0",
            "--max-decision-workers",
            "1",
        ]
    ) == 0
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 8766
    assert load_transcript(run_root / "transcript.json").scenario.identity_seed == 8842


def test_deterministic_watch_ignores_ambient_featherless_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: deterministic watch reads model settings or builds a client."""
    from humanwire.__main__ import main

    def forbidden(*_args, **_kwargs):
        raise AssertionError("deterministic watch configured a model")

    monkeypatch.setenv("FEATHERLESS_API_KEY", "DO-NOT-READ")
    monkeypatch.setattr("humanwire.synthetic_watch.Settings", forbidden)
    monkeypatch.setattr(
        "humanwire.synthetic_watch.FeatherlessPersonaDecisionEngineFactory",
        forbidden,
    )
    monkeypatch.setattr("humanwire.synthetic_watch.uvicorn.run", lambda *_a, **_k: None)
    run_root = tmp_path / "deterministic-run"

    assert main(
        [
            "synthetic",
            "watch",
            "--output",
            str(run_root / "transcript.json"),
            "--run-root",
            str(run_root),
            "--agent-mode",
            "deterministic",
            "--step-delay-ms",
            "0",
        ]
    ) == 0
    assert load_transcript(run_root / "transcript.json").scenario.identity_seed == 0


def test_featherless_watch_without_key_fails_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Break caught: missing credentials start work or disclose configuration details."""
    from humanwire.__main__ import main

    monkeypatch.setattr(
        "humanwire.synthetic_watch.Settings",
        lambda: SimpleNamespace(featherless_api_key=None),
    )
    monkeypatch.setattr(
        "humanwire.synthetic_watch.uvicorn.run",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("viewer started")),
    )
    run_root = tmp_path / "missing-key-run"

    assert main(
        [
            "synthetic",
            "watch",
            "--output",
            str(run_root / "transcript.json"),
            "--run-root",
            str(run_root),
            "--agent-mode",
            "featherless",
            "--step-delay-ms",
            "0",
        ]
    ) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "synthetic_status=failed\nfailure_reason=model_credentials_missing\n"
    )
    assert not run_root.exists()


@pytest.mark.parametrize(
    ("flag", "first", "second"),
    [
        ("--output", "first.json", "second.json"),
        ("--run-root", "first", "second"),
        ("--seed", "1", "2"),
        ("--agent-mode", "deterministic", "featherless"),
        ("--port", "8766", "8767"),
        ("--step-delay-ms", "0", "1"),
        ("--max-decision-workers", "1", "2"),
    ],
)
def test_watch_cli_rejects_duplicate_options(
    flag: str, first: str, second: str, capsys
) -> None:
    """Break caught: repeated security-relevant watch options silently override."""
    from humanwire.__main__ import build_parser

    required: list[str] = []
    if flag != "--output":
        required.extend(("--output", "transcript.json"))
    if flag != "--run-root":
        required.extend(("--run-root", "run"))

    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args(
            ["synthetic", "watch", *required, flag, first, flag, second]
        )

    assert caught.value.code == 2
    assert "may be supplied only once" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--port", "1023"),
        ("--port", "65536"),
        ("--step-delay-ms", "-1"),
        ("--step-delay-ms", "3001"),
        ("--max-decision-workers", "0"),
        ("--max-decision-workers", "9"),
        ("--agent-mode", "ambient"),
    ],
)
def test_watch_cli_rejects_invalid_options_before_starting_work(
    flag: str, value: str, tmp_path: Path, capsys
) -> None:
    """Break caught: invalid watch bounds reach threads, files, or network setup."""
    from humanwire.__main__ import main

    run_root = tmp_path / "invalid-run"
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "synthetic",
                "watch",
                "--output",
                str(run_root / "transcript.json"),
                "--run-root",
                str(run_root),
                flag,
                value,
            ]
        )

    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert flag in captured.err
    assert not run_root.exists()


def test_watch_cli_has_no_host_option(tmp_path: Path, capsys) -> None:
    """Break caught: watch accepts caller-controlled binding hosts."""
    from humanwire.__main__ import main

    run_root = tmp_path / "host-run"
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "synthetic",
                "watch",
                "--output",
                str(run_root / "transcript.json"),
                "--run-root",
                str(run_root),
                "--host",
                "0.0.0.0",
            ]
        )

    assert caught.value.code == 2
    assert "unrecognized arguments: --host" in capsys.readouterr().err
    assert not run_root.exists()


def test_generate_cli_seed_selects_identity_fixture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Break caught: generate parses but ignores its explicit identity seed."""
    from humanwire.__main__ import main

    run_root = tmp_path / "seeded-run"
    assert main(
        [
            "synthetic",
            "generate",
            "--output",
            str(run_root / "transcript.json"),
            "--run-root",
            str(run_root),
            "--seed",
            "8842",
        ]
    ) == 0

    assert capsys.readouterr().err == ""
    assert load_transcript(run_root / "transcript.json").scenario.identity_seed == 8842


def test_watch_creates_viewer_before_non_daemon_worker_and_joins_on_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: viewer close abandons a daemon worker or races app/store creation."""
    from humanwire.synthetic_watch import SyntheticWatchOptions, run_synthetic_watch

    calls: list[str] = []
    generation_started = threading.Event()
    release_generation = threading.Event()
    generation_finished = threading.Event()
    captured: dict[str, object] = {}

    def fake_create_app(store, transcript_path):
        calls.append("viewer_created")
        captured.update(store=store, transcript_path=transcript_path)
        return "viewer-app"

    def fake_generate(scenario, output_path, run_root, **kwargs):
        calls.append("generation_started")
        captured.update(
            scenario=scenario,
            output_path=output_path,
            run_root=run_root,
            generation_thread=threading.current_thread(),
            generation_kwargs=kwargs,
        )
        generation_started.set()
        assert release_generation.wait(timeout=5)
        generation_finished.set()

    def fake_uvicorn(app, *, host, port, **kwargs):
        assert generation_started.wait(timeout=5)
        calls.append("viewer_started")
        captured.update(app=app, host=host, port=port, uvicorn_kwargs=kwargs)
        release_generation.set()

    monkeypatch.setattr("humanwire.synthetic_watch.create_synthetic_viewer_app", fake_create_app)
    monkeypatch.setattr("humanwire.synthetic_watch.generate_scenario", fake_generate)
    monkeypatch.setattr("humanwire.synthetic_watch.uvicorn.run", fake_uvicorn)
    run_root = tmp_path / "lifecycle-run"

    assert run_synthetic_watch(
        SyntheticWatchOptions(
            output=run_root / "transcript.json",
            run_root=run_root,
            seed=73,
            agent_mode="deterministic",
            port=9123,
            step_delay_ms=0,
            max_decision_workers=3,
        )
    ) == 0

    assert calls[0] == "viewer_created"
    assert generation_finished.is_set()
    assert captured["app"] == "viewer-app"
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9123
    assert captured["generation_thread"].name == "humanwire-synthetic-generation"
    assert captured["generation_thread"].daemon is False
    assert captured["scenario"].identity_seed == 73
    assert captured["generation_kwargs"]["decision_engine"] is None
    assert captured["generation_kwargs"]["max_decision_workers"] == 3


def test_watch_worker_failure_publishes_only_fixed_safe_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Break caught: worker exceptions leak text or leave progress falsely running."""
    from humanwire.synthetic_watch import SyntheticWatchOptions, run_synthetic_watch

    captured: dict[str, object] = {}
    failed = threading.Event()

    def fake_create_app(store, transcript_path):
        captured.update(store=store, transcript_path=transcript_path)
        return "viewer-app"

    def fail_generation(*_args, **_kwargs):
        failed.set()
        raise RuntimeError("PRIVATE-PROVIDER-BODY")

    def fake_uvicorn(*_args, **_kwargs):
        assert failed.wait(timeout=5)

    monkeypatch.setattr("humanwire.synthetic_watch.create_synthetic_viewer_app", fake_create_app)
    monkeypatch.setattr("humanwire.synthetic_watch.generate_scenario", fail_generation)
    monkeypatch.setattr("humanwire.synthetic_watch.uvicorn.run", fake_uvicorn)
    run_root = tmp_path / "failed-run"

    assert run_synthetic_watch(
        SyntheticWatchOptions(
            output=run_root / "transcript.json",
            run_root=run_root,
            step_delay_ms=0,
        )
    ) == 0
    capture = capsys.readouterr()
    snapshot = captured["store"].snapshot()

    assert capture.out == "viewer_url=http://127.0.0.1:8766\n"
    assert capture.err == ""
    assert "PRIVATE-PROVIDER-BODY" not in capture.out + capture.err
    assert snapshot.run_state.value == "failed"
    assert snapshot.runtime_status.value == "terminal_failure"
    assert snapshot.final_trace_sha256 is None


def test_featherless_watch_uses_central_client_without_exposing_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Break caught: explicit model mode bypasses the central adapter or publishes its key."""
    from pydantic import SecretStr

    from humanwire.model_client import FeatherlessJsonClient
    from humanwire.persona_runtime import (
        FeatherlessPersonaDecisionEngine,
        FeatherlessPersonaDecisionEngineFactory,
    )
    from humanwire.synthetic_watch import SyntheticWatchOptions, run_synthetic_watch

    key = "PRIVATE-FEATHERLESS-WATCH-KEY"
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "humanwire.synthetic_watch.Settings",
        lambda: SimpleNamespace(
            featherless_api_key=SecretStr(key),
            featherless_model="fixture/model-v1",
            featherless_base_url="https://models.example.test/v1",
        ),
    )
    monkeypatch.setattr(
        "humanwire.synthetic_watch.create_synthetic_viewer_app",
        lambda store, path: captured.update(store=store, path=path) or "viewer-app",
    )

    def fake_generate(*_args, **kwargs):
        captured["engine"] = kwargs["decision_engine"]

    monkeypatch.setattr("humanwire.synthetic_watch.generate_scenario", fake_generate)
    monkeypatch.setattr("humanwire.synthetic_watch.uvicorn.run", lambda *_a, **_k: None)
    run_root = tmp_path / "model-run"

    assert run_synthetic_watch(
        SyntheticWatchOptions(
            output=run_root / "transcript.json",
            run_root=run_root,
            agent_mode="featherless",
            step_delay_ms=0,
        )
    ) == 0
    capture = capsys.readouterr()
    factory = captured["engine"]

    assert isinstance(factory, FeatherlessPersonaDecisionEngineFactory)
    assert factory.model_identifier == "fixture/model-v1"
    assert factory.base_url == "https://models.example.test/v1"
    assert key not in repr(factory)
    engine = factory.build()
    try:
        assert isinstance(engine, FeatherlessPersonaDecisionEngine)
        assert isinstance(engine._client, FeatherlessJsonClient)
        assert engine.model_identifier == "fixture/model-v1"
    finally:
        engine._client._client.close()
    assert key not in capture.out + capture.err
    assert key not in captured["store"].snapshot().model_dump_json()


def test_watch_pacing_occurs_after_publication_without_changing_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: presentation pacing precedes publication or changes evidence semantics."""
    from humanwire.synthetic_watch import SyntheticWatchOptions, run_synthetic_watch

    captured: dict[str, object] = {}
    observed_counts: list[int] = []

    def fake_create_app(store, path):
        captured.update(store=store, path=path)
        return "viewer-app"

    def observe_sleep(seconds: float) -> None:
        assert seconds == 0.001
        observed_counts.append(captured["store"].snapshot().saved_event_count)

    monkeypatch.setattr("humanwire.synthetic_watch.create_synthetic_viewer_app", fake_create_app)
    monkeypatch.setattr("humanwire.synthetic_watch.uvicorn.run", lambda *_a, **_k: None)
    monkeypatch.setattr("humanwire.synthetic_watch.time.sleep", observe_sleep)
    paced_root = tmp_path / "paced-run"

    assert run_synthetic_watch(
        SyntheticWatchOptions(
            output=paced_root / "transcript.json",
            run_root=paced_root,
            seed=91,
            step_delay_ms=1,
        )
    ) == 0
    paced = load_transcript(paced_root / "transcript.json")
    plain_root = tmp_path / "plain-run"
    plain = generate_scenario(
        synthetic_module.default_synthetic_scenario(seed=91),
        plain_root / "transcript.json",
        plain_root,
    )

    assert observed_counts
    assert observed_counts == sorted(set(observed_counts))
    assert paced.model_dump_json() == plain.transcript.model_dump_json()


def test_generation_rejects_output_outside_run_root_before_writing(tmp_path) -> None:
    run_root = tmp_path / "run"
    outside = tmp_path / "published" / "transcript.json"

    with pytest.raises(ValueError, match="output path must be inside run root"):
        generate_scenario(make_generation_scenario(), outside, run_root)

    assert not outside.exists()


@pytest.mark.parametrize("mode", ["generate", "replay"])
def test_synthetic_run_rejects_populated_run_root_without_changing_bytes(
    tmp_path, mode
) -> None:
    run_root = tmp_path / f"{mode}-run"
    run_root.mkdir()
    existing = run_root / "operator-note.txt"
    original = b"operator-owned bytes must survive"
    existing.write_bytes(original)

    with pytest.raises(FileExistsError, match="fresh run root"):
        if mode == "generate":
            generate_scenario(
                make_generation_scenario(),
                run_root / "transcript.json",
                run_root,
            )
        else:
            synthetic_module.replay_transcript(
                Path("tests/fixtures/humanwire/synthetic_launch_v1.json"),
                run_root,
            )

    assert existing.read_bytes() == original
    assert {path.name for path in run_root.iterdir()} == {"operator-note.txt"}


def test_generation_rejects_preexisting_output_without_overwriting_it(tmp_path) -> None:
    run_root = tmp_path / "generate-run"
    run_root.mkdir()
    output = run_root / "transcript.json"
    original = b"operator-owned transcript bytes"
    output.write_bytes(original)

    with pytest.raises(FileExistsError, match="fresh run root"):
        generate_scenario(make_generation_scenario(), output, run_root)

    assert output.read_bytes() == original
    assert {path.name for path in run_root.iterdir()} == {"transcript.json"}


@pytest.mark.parametrize("mode", ["generate", "replay"])
def test_synthetic_run_accepts_nonexistent_root(tmp_path, mode) -> None:
    run_root = tmp_path / mode

    if mode == "generate":
        result = generate_scenario(
            make_generation_scenario(),
            run_root / "transcript.json",
            run_root,
        )
        assert (run_root / "transcript.json").is_file()
    else:
        result = synthetic_module.replay_transcript(
            Path("tests/fixtures/humanwire/synthetic_launch_v1.json"),
            run_root,
        )

    assert result.database_path == run_root.resolve() / "humanwire-synthetic.sqlite3"
    assert result.database_path.is_file()


@pytest.mark.parametrize("mode", ["generate", "replay"])
def test_synthetic_run_rejects_existing_empty_root(tmp_path, mode) -> None:
    run_root = tmp_path / mode
    run_root.mkdir()

    with pytest.raises(FileExistsError, match="fresh run root"):
        if mode == "generate":
            generate_scenario(
                make_generation_scenario(),
                run_root / "transcript.json",
                run_root,
            )
        else:
            synthetic_module.replay_transcript(
                Path("tests/fixtures/humanwire/synthetic_launch_v1.json"),
                run_root,
            )

    assert list(run_root.iterdir()) == []


def test_two_concurrent_generations_have_exactly_one_run_root_owner(
    tmp_path, monkeypatch
) -> None:
    run_root = tmp_path / "shared-run"
    output = run_root / "transcript.json"
    entered_settings: list[int] = []
    settings_lock = threading.Lock()
    second_entered = threading.Event()
    original_settings = synthetic_module._isolated_settings

    def hold_first_owner(database_path):
        with settings_lock:
            entered_settings.append(threading.get_ident())
            entry_count = len(entered_settings)
            if entry_count == 2:
                second_entered.set()
        if entry_count == 1:
            second_entered.wait(timeout=0.5)
        return original_settings(database_path)

    monkeypatch.setattr(synthetic_module, "_isolated_settings", hold_first_owner)
    results: list[object] = []

    def run() -> None:
        try:
            results.append(
                generate_scenario(make_generation_scenario(), output, run_root)
            )
        except BaseException as error:  # noqa: BLE001 - inspected by the test
            results.append(error)

    workers = [threading.Thread(target=run) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=15)

    assert all(not worker.is_alive() for worker in workers)
    assert len(entered_settings) == 1
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(failures) == 1
    assert isinstance(failures[0], FileExistsError)
    assert output.is_file()
    assert load_transcript(output).digest
    assert {path.name for path in run_root.iterdir()} == {
        "humanwire-synthetic.sqlite3",
        "transcript.json",
    }


def test_two_cli_processes_have_one_safe_winner_and_one_safe_loser(tmp_path) -> None:
    run_root = tmp_path / "shared-cli-run"
    output = run_root / "transcript.json"
    command = [
        sys.executable,
        "-m",
        "humanwire",
        "synthetic",
        "generate",
        "--output",
        str(output),
        "--run-root",
        str(run_root),
    ]
    processes = [
        subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(2)
    ]
    completed = [process.communicate(timeout=30) for process in processes]
    return_codes = [process.returncode for process in processes]

    assert sorted(return_codes) == [0, 1]
    winner = return_codes.index(0)
    loser = return_codes.index(1)
    assert completed[winner][1] == ""
    assert _parse_safe_cli_output(completed[winner][0])["proof_class"] == (
        "synthetic_multi_persona"
    )
    assert completed[loser] == (
        "",
        "synthetic_status=failed\nfailure_reason=isolated_run_failed\n",
    )
    assert load_transcript(output).digest
    assert {path.name for path in run_root.iterdir()} == {
        "humanwire-synthetic.sqlite3",
        "transcript.json",
    }


def test_generation_creates_nested_output_parents_inside_claimed_root(tmp_path) -> None:
    run_root = tmp_path / "run"
    output = run_root / "artifacts" / "frozen" / "transcript.json"

    result = generate_scenario(make_generation_scenario(), output, run_root)

    assert result.database_path.parent == run_root.resolve()
    assert load_transcript(output) == result.transcript
    assert not any(path.is_file() for path in tmp_path.iterdir())


def test_generation_exclusively_creates_output_after_claim(
    tmp_path, monkeypatch
) -> None:
    run_root = tmp_path / "run"
    output = run_root / "transcript.json"
    original = b"concurrent owner bytes must survive"
    original_dump = SyntheticTranscript.model_dump_json

    def inject_competing_output(self, *args, **kwargs):
        output.write_bytes(original)
        return original_dump(self, *args, **kwargs)

    monkeypatch.setattr(
        SyntheticTranscript,
        "model_dump_json",
        inject_competing_output,
    )

    with pytest.raises(FileExistsError):
        generate_scenario(make_generation_scenario(), output, run_root)

    assert output.read_bytes() == original


def _symlink_or_skip(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as error:
        pytest.skip(f"symbolic links are unavailable on this Windows host: {error.winerror}")


def test_generation_rejects_post_claim_output_symlink_escape_without_changing_target(
    tmp_path, monkeypatch
) -> None:
    run_root = tmp_path / "run"
    outside = tmp_path / "outside.json"
    original = b"outside bytes must survive"
    outside.write_bytes(original)
    output = run_root / "transcript.json"
    original_dump = SyntheticTranscript.model_dump_json

    def inject_output_symlink(self, *args, **kwargs):
        _symlink_or_skip(output, outside)
        return original_dump(self, *args, **kwargs)

    monkeypatch.setattr(SyntheticTranscript, "model_dump_json", inject_output_symlink)

    with pytest.raises(ValueError, match="output path must be inside run root"):
        generate_scenario(make_generation_scenario(), output, run_root)

    assert outside.read_bytes() == original


def test_generation_rejects_post_claim_intermediate_symlink_escape_without_writing(
    tmp_path, monkeypatch
) -> None:
    run_root = tmp_path / "run"
    outside = tmp_path / "outside"
    outside.mkdir()
    intermediate = run_root / "escaped"
    original_dump = SyntheticTranscript.model_dump_json

    def inject_intermediate_symlink(self, *args, **kwargs):
        _symlink_or_skip(intermediate, outside, target_is_directory=True)
        return original_dump(self, *args, **kwargs)

    monkeypatch.setattr(
        SyntheticTranscript,
        "model_dump_json",
        inject_intermediate_symlink,
    )

    with pytest.raises(ValueError, match="output path must be inside run root"):
        generate_scenario(
            make_generation_scenario(),
            intermediate / "transcript.json",
            run_root,
        )

    assert list(outside.iterdir()) == []


def test_synthetic_generate_cli_ignores_ambient_config_and_writes_only_in_run_root(
    tmp_path, monkeypatch, capsys
) -> None:
    from humanwire.__main__ import main

    ambient_secret = "PRIVATE-AMBIENT-SYNTHETIC-SECRET"
    for key, value in {
        "CASPIAN_API_KEY": ambient_secret,
        "CASPIAN_BASE_URL": "https://ambient-provider.example.test",
        "TELEGRAM_BOT_TOKEN": ambient_secret,
        "FEATHERLESS_API_KEY": ambient_secret,
        "FEATHERLESS_BASE_URL": "https://ambient-model.example.test/v1",
        "DATABASE_URL": "sqlite:///ambient.sqlite3",
        "ORGANIZATION_PATH": "ambient-directory.json",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"CASPIAN_API_KEY={ambient_secret}\n", encoding="utf-8")
    (tmp_path / "ambient-directory.json").write_text(ambient_secret, encoding="utf-8")
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    run_root = tmp_path / "explicit-run"
    output = run_root / "transcript.json"

    assert main(
        [
            "synthetic",
            "generate",
            "--output",
            str(output),
            "--run-root",
            str(run_root),
        ]
    ) == 0
    captured = capsys.readouterr()

    after_outside = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file() and run_root not in path.parents
    }
    assert after_outside == before
    assert output.is_file()
    assert (run_root / "humanwire-synthetic.sqlite3").is_file()
    assert {path.name for path in run_root.iterdir()} == {
        "humanwire-synthetic.sqlite3",
        "transcript.json",
    }
    published = output.read_text(encoding="utf-8")
    combined = captured.out + captured.err + published
    assert ambient_secret not in combined
    assert "ambient-provider" not in combined
    assert "ambient-model" not in combined
    assert "ambient-directory" not in combined


def test_synthetic_cli_denies_network_provider_and_model_calls(
    tmp_path, monkeypatch, capsys
) -> None:
    from humanwire.__main__ import main

    def forbidden(*args, **kwargs):
        del args, kwargs
        raise AssertionError("synthetic proof attempted forbidden external I/O")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr("humanwire.caspian_gateway.CommClient", forbidden)
    monkeypatch.setattr("humanwire.model_client.httpx.Client", forbidden)
    run_root = tmp_path / "generate-run"

    assert main(
        [
            "synthetic",
            "generate",
            "--output",
            str(run_root / "transcript.json"),
            "--run-root",
            str(run_root),
        ]
    ) == 0
    assert capsys.readouterr().err == ""


def test_synthetic_generate_and_replay_print_only_safe_proof_summary(
    tmp_path, capsys
) -> None:
    from humanwire.__main__ import main

    private_text = "PRIVATE-PERSONA-SENTINEL"
    generated_root = tmp_path / "generated-run"
    generated_path = generated_root / "transcript.json"
    assert main(
        [
            "synthetic",
            "generate",
            "--output",
            str(generated_path),
            "--run-root",
            str(generated_root),
        ]
    ) == 0
    generated_capture = capsys.readouterr()
    replay_root = tmp_path / "replay-run"
    assert main(
        [
            "synthetic",
            "replay",
            "--transcript",
            str(generated_path),
            "--run-root",
            str(replay_root),
        ]
    ) == 0
    replay_capture = capsys.readouterr()

    expected_keys = {
        "proof_class",
        "actor_type",
        "identity_source",
        "transport",
        "human_attested",
        "live_provider_verified",
        "scenario_id",
        "run_id",
        "action_count",
        "inbound_attempt_count",
        "delivery_count",
        "terminal_state",
        "terminal_states",
        "trace_sha256",
    }
    for capture in (generated_capture, replay_capture):
        assert capture.err == ""
        summary = _parse_safe_cli_output(capture.out)
        assert set(summary) == expected_keys
        assert summary["proof_class"] == "synthetic_multi_persona"
        assert summary["actor_type"] == "simulated_persona"
        assert summary["identity_source"] == "synthetic_fixture"
        assert summary["transport"] == "fake_caspian"
        assert summary["human_attested"] == "false"
        assert summary["live_provider_verified"] == "false"
        assert summary["scenario_id"] == "launch-v1"
        assert re.fullmatch(r"launch-v1-[0-9a-f]{12}", summary["run_id"])
        assert all(
            summary[key].isdigit()
            for key in ("action_count", "inbound_attempt_count", "delivery_count")
        )
        assert summary["terminal_state"] in {
            "aligned",
            "negotiating",
            "partial",
            "scheduling",
        }
        assert summary["terminal_states"] == "meeting_ready,partial"
        assert re.fullmatch(r"[0-9a-f]{64}", summary["trace_sha256"])
        forbidden = (
            private_text,
            "@example.test",
            "synthetic-manager",
            "conversation",
            "connection",
            "message_id",
            "destination",
            "CapturedDelivery",
            str(tmp_path),
        )
        assert all(value not in capture.out for value in forbidden)
    assert generated_capture.out == replay_capture.out
    assert private_text not in generated_path.read_text(encoding="utf-8")
    assert "CapturedDelivery" not in generated_path.read_text(encoding="utf-8")
    assert not any(
        path.suffix in {".json", ".txt", ".log"} for path in replay_root.rglob("*")
    )


def test_synthetic_replay_tamper_returns_nonzero_safe_error(tmp_path, capsys) -> None:
    from humanwire.__main__ import main

    private_text = "PRIVATE-TAMPERED-CONTENT"
    payload = json.loads(
        Path("tests/fixtures/humanwire/synthetic_launch_v1.json").read_text(
            encoding="utf-8"
        )
    )
    payload["actions"][0]["content"] = private_text
    transcript_path = tmp_path / "tampered.json"
    transcript_path.write_text(json.dumps(payload), encoding="utf-8")

    assert main(
        [
            "synthetic",
            "replay",
            "--transcript",
            str(transcript_path),
            "--run-root",
            str(tmp_path / "replay-run"),
        ]
    ) != 0
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "synthetic_status=failed\nfailure_reason=invalid_transcript\n"
    assert private_text not in captured.err
    assert str(transcript_path) not in captured.err


def test_repository_synthetic_script_is_a_thin_installed_module_wrapper(
    tmp_path, capsys
) -> None:
    from scripts.synthetic_humanwire import main

    run_root = tmp_path / "wrapper-run"
    assert main(
        [
            "generate",
            "--output",
            str(run_root / "transcript.json"),
            "--run-root",
            str(run_root),
        ]
    ) == 0
    summary = _parse_safe_cli_output(capsys.readouterr().out)

    assert summary["proof_class"] == "synthetic_multi_persona"
