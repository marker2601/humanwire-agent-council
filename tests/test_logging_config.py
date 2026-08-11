import json
import logging

from secondsignal.logging_config import configure_logging


def test_json_log_contains_case_metadata_without_message_content(capsys):
    configure_logging()
    logger = logging.getLogger("secondsignal.test")
    logger.info(
        "case_transition",
        extra={
            "case_token": "SS-7K4P2M",
            "origin_channel": "telegram",
            "verification_channel": "email",
            "original_message": "Buy gift cards now",
            "sender_address": "private@example.com",
            "api_key": "secret",
            "arbitrary": "not-whitelisted",
        },
    )

    payload = json.loads(capsys.readouterr().err)

    assert payload["event"] == "case_transition"
    assert payload["case_token"] == "SS-7K4P2M"
    assert payload["origin_channel"] == "telegram"
    assert payload["verification_channel"] == "email"
    assert "original_message" not in payload
    assert "sender_address" not in payload
    assert "api_key" not in payload
    assert "arbitrary" not in payload
