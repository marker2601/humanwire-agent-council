"""Redaction for values that must not be persisted or shared as evidence."""

import re
from collections.abc import Callable

PatternReplacement = tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]

PATTERNS: tuple[PatternReplacement, ...] = (
    (
        re.compile(
            r"(?i)\b(?P<label>otp|one[- ]time password)\s*(?:is\s*)?[:#-]?\s*\d{4,8}\b"
        ),
        lambda match: f"{match.group('label')} [REDACTED]",
    ),
    (
        re.compile(
            r"(?i)\b(?P<label>recovery code)\s*[:#-]?\s*"
            r"[A-Z0-9]{4}(?:-[A-Z0-9]{4})+\b"
        ),
        lambda match: f"{match.group('label')} [REDACTED]",
    ),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~-]+\b"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"), "[REDACTED]"),
    (
        re.compile(r"(?:\+\d[\d(). -]{6,}\d|\d{3}[(). -]+\d{3}[(). -]+\d{4}|\d{10,})"),
        "[REDACTED]",
    ),
    (re.compile(r"(?i)(?:https?://)?(?:t\.me|telegram\.me)/[A-Z0-9_]+\b"), "[REDACTED]"),
    (re.compile(r"(?<![\w/])@[A-Z0-9_]{3,}\b", re.IGNORECASE), "[REDACTED]"),
)


def redact_sensitive(text: str) -> str:
    """Remove credentials and direct contact values while preserving useful evidence."""
    result = text
    for pattern, replacement in PATTERNS:
        result = pattern.sub(replacement, result)
    return result
