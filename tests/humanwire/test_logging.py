import json
import logging

from humanwire.logging_config import configure_logging


def test_json_logging_emits_only_allowlisted_operational_metadata(capsys) -> None:
    configure_logging()
    logger = logging.getLogger("humanwire.test")
    logger.info(
        "mandate_transition",
        extra={
            "mandate_token": "HW-EXACT",
            "event_type": "mandate.transitioned",
            "state": "interviewing",
            "person_id": "team-lead",
            "department": "Operations",
            "direction": "downward",
            "channel": "email",
            "attempt": 2,
            "duration_ms": 17,
            "reason": "delivery_failed",
            "correlation_id": "correlation-safe",
            "message_text": "PRIVATE board statement",
            "sender_address": "private.person@example.test",
            "recipient": "destination@example.test",
            "api_key": "secret-key-value",
            "provider_body": "PRIVATE provider response",
            "arbitrary": "not-allowlisted",
        },
    )

    raw = capsys.readouterr().err
    payload = json.loads(raw)

    assert payload["event"] == "mandate_transition"
    assert payload["mandate_token"] == "HW-EXACT"
    assert payload["event_type"] == "mandate.transitioned"
    assert payload["state"] == "interviewing"
    assert payload["attempt"] == 2
    for private_value in (
        "PRIVATE board statement",
        "private.person@example.test",
        "destination@example.test",
        "secret-key-value",
        "PRIVATE provider response",
        "not-allowlisted",
    ):
        assert private_value not in raw
