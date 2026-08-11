import re
from collections.abc import Callable

PatternReplacement = tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]

PATTERNS: tuple[PatternReplacement, ...] = (
    (
        re.compile(r"(?i)\b(?P<label>otp|one[- ]time password)\s*[:#-]?\s*\d{4,8}\b"),
        lambda match: f"{match.group('label')} [REDACTED]",
    ),
    (
        re.compile(
            r"(?i)\b(?P<label>recovery code)\s*[:#-]?\s*"
            r"[A-Z0-9]{4}(?:-[A-Z0-9]{4})+\b"
        ),
        lambda match: f"{match.group('label')} [REDACTED]",
    ),
    (
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~-]+\b"),
        "Bearer [REDACTED]",
    ),
)


def redact_sensitive(text: str) -> str:
    """Redact common credential material without removing financial amounts."""
    result = text
    for pattern, replacement in PATTERNS:
        result = pattern.sub(replacement, result)
    return result
