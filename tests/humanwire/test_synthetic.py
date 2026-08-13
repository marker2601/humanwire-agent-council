from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path

import pytest
from pydantic import ValidationError

import humanwire.synthetic as synthetic_module
from humanwire.domain import Channel
from humanwire.synthetic import (
    SUPPORTED_SCHEMA_VERSION,
    SyntheticAction,
    SyntheticIntent,
    SyntheticPersona,
    SyntheticProvenance,
    SyntheticScenario,
    SyntheticTranscript,
    generate_scenario,
    load_transcript,
)

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
    output_path = tmp_path / "transcript.json"
    return generate_scenario(scenario or make_generation_scenario(), output_path, run_root)


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


def test_each_engagement_contract_uses_a_distinct_policy_strategy() -> None:
    policies = [
        synthetic_module._build_policy(persona)
        for persona in make_generation_scenario().personas
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
    first_output = tmp_path / "first.json"
    second_output = tmp_path / "second.json"

    first = generate_scenario(scenario, first_output, tmp_path / "run-a")
    second = generate_scenario(scenario, second_output, tmp_path / "run-b")

    assert first.transcript == second.transcript
    assert first_output.read_bytes() == second_output.read_bytes()
    safe_bytes = first_output.read_bytes()
    assert b"PRIVATE-PERSONA-SENTINEL" not in safe_bytes
    assert hashlib.sha256(b"PRIVATE-PERSONA-SENTINEL").hexdigest().encode() in safe_bytes


def test_generation_orders_actions_by_timestamp_persona_and_local_sequence(tmp_path) -> None:
    result = _generate(tmp_path)
    keys = [
        (action.timestamp, action.persona_id, action.local_sequence)
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
    generated_path = tmp_path / "generated.json"
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
        tmp_path / "first.json",
        tmp_path / "first-run",
    )
    second = generate_scenario(
        make_generation_scenario(),
        tmp_path / "second.json",
        tmp_path / "second-run",
    )

    assert first.database_path != second.database_path
    assert first.database_path.read_bytes() != second.database_path.read_bytes()
    assert synthetic_module.semantic_trace_hash(first) == (
        synthetic_module.semantic_trace_hash(second)
    )


def test_replay_never_builds_or_calls_persona_policies(tmp_path, monkeypatch) -> None:
    generated_path = tmp_path / "generated.json"
    generate_scenario(
        make_generation_scenario(),
        generated_path,
        tmp_path / "generate-run",
    )

    def policy_use_is_a_failure(*args, **kwargs):
        del args, kwargs
        raise AssertionError("replay invoked persona generation")

    monkeypatch.setattr(synthetic_module, "_build_policy", policy_use_is_a_failure)

    replayed = synthetic_module.replay_transcript(
        generated_path,
        tmp_path / "replay-run",
    )

    assert replayed.gateway_handler_count == 1
    assert replayed.inbound_envelopes


def test_frozen_replay_hash_is_equal_after_a_fresh_replay_restart(tmp_path) -> None:
    fixture = Path("tests/fixtures/humanwire/synthetic_launch_v1.json")

    first = synthetic_module.replay_transcript(fixture, tmp_path / "restart-a")
    second = synthetic_module.replay_transcript(fixture, tmp_path / "restart-b")

    assert synthetic_module.semantic_trace_hash(first) == (
        synthetic_module.semantic_trace_hash(second)
    )


def test_semantic_trace_hash_changes_for_a_material_action_change(tmp_path) -> None:
    original_path = tmp_path / "original.json"
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
    generated_path = tmp_path / "generated.json"
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
