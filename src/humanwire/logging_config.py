"""Structured operational logging with a strict metadata allowlist."""

import json
import logging
import sys
from datetime import UTC, datetime

SAFE_METADATA_FIELDS = (
    "mandate_token",
    "event_type",
    "state",
    "person_id",
    "department",
    "direction",
    "channel",
    "attempt",
    "duration_ms",
    "reason",
    "correlation_id",
)


class JsonFormatter(logging.Formatter):
    def __init__(self, *, redact_message: bool = False) -> None:
        super().__init__()
        self.redact_message = redact_message

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "event": "caspian_sdk_event" if self.redact_message else record.getMessage(),
            "logger": record.name,
        }
        for field in SAFE_METADATA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging(level: int = logging.INFO) -> None:
    logger = logging.getLogger("humanwire")
    logger.handlers.clear()
    logger.addHandler(_handler(redact_message=False))
    logger.setLevel(level)
    logger.propagate = False

    caspian_logger = logging.getLogger("caspian_sdk")
    caspian_logger.handlers.clear()
    caspian_logger.addHandler(_handler(redact_message=True))
    caspian_logger.setLevel(level)
    caspian_logger.propagate = False


def _handler(*, redact_message: bool) -> logging.Handler:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter(redact_message=redact_message))
    return handler
