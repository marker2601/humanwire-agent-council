import json
import logging
import sys
from io import StringIO

from caspian_sdk import CommError

from humanwire.logging_config import configure_logging


def test_json_handler_follows_the_current_stderr_stream(monkeypatch) -> None:
    first = StringIO()
    second = StringIO()
    monkeypatch.setattr(sys, "stderr", first)
    configure_logging()
    logger = logging.getLogger("humanwire.stream")
    logger.info("first_event")

    monkeypatch.setattr(sys, "stderr", second)
    logger.info("second_event")

    assert json.loads(first.getvalue())["event"] == "first_event"
    assert json.loads(second.getvalue())["event"] == "second_event"


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


def test_caspian_sdk_logging_redacts_message_arguments_and_exception_details(capsys) -> None:
    configure_logging()
    logger = logging.getLogger("caspian_sdk")
    try:
        raise CommError(
            503,
            "PRIVATE provider body for private.person@example.test token=telegram-secret",
        )
    except CommError:
        logger.warning(
            "provider failure body=%s email=%s token=%s",
            "PRIVATE provider response",
            "destination@example.test",
            "caspian-secret-token",
            exc_info=True,
            extra={
                "reason": "provider_retry",
                "provider_body": "PRIVATE extra body",
                "recipient": "second-destination@example.test",
            },
        )

    raw = capsys.readouterr().err
    assert raw, "Caspian SDK logger was not routed through the safe JSON handler"
    payload = json.loads(raw)
    assert payload["event"] == "caspian_sdk_event"
    assert payload["reason"] == "provider_retry"
    for private_value in (
        "PRIVATE provider response",
        "destination@example.test",
        "caspian-secret-token",
        "PRIVATE provider body",
        "private.person@example.test",
        "telegram-secret",
        "PRIVATE extra body",
        "second-destination@example.test",
        "CommError",
    ):
        assert private_value not in raw
