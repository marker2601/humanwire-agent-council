from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

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


def test_persona_context_contains_only_its_own_inbox_transcript_and_private_fixture(
    tmp_path, monkeypatch
) -> None:
    observed = []
    original = synthetic_module._DeterministicPersonaPolicy.respond

    def inspect_context(self, context):
        observed.append(context)
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
    for context in observed:
        assert forbidden.isdisjoint(type(context).model_fields)
        assert all(item.persona_id == context.persona_id for item in context.own_transcript)
        assert context.delivered_message == context.own_inbox[-1]
        assert all("@example.test" not in item for item in context.own_inbox)
    structured = next(item for item in observed if item.persona_id == "structured")
    assert structured.private_facts == ("PRIVATE-PERSONA-SENTINEL",)
    assert all(
        "PRIVATE-PERSONA-SENTINEL" not in item.delivered_message
        for item in observed
        if item.persona_id != "structured"
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
