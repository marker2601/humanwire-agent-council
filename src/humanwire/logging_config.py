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
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            "logger": record.name,
        }
        for field in SAFE_METADATA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("humanwire")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
