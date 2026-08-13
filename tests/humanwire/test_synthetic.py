from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from humanwire.domain import Channel
from humanwire.synthetic import (
    SUPPORTED_SCHEMA_VERSION,
    SyntheticAction,
    SyntheticIntent,
    SyntheticPersona,
    SyntheticProvenance,
    SyntheticScenario,
    SyntheticTranscript,
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
