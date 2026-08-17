"""Versioned safe Pub/Sub dispatch contracts for cloud coordination runs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError

_SAFE_ALIAS = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
_SAFE_OPAQUE = r"^[A-Za-z0-9][A-Za-z0-9._-]{15,127}$"
_TOPIC = r"^projects/[a-z][a-z0-9-]{4,62}/topics/[A-Za-z][A-Za-z0-9._~-]{2,254}$"
_MAX_MESSAGE_BYTES = 2048


class DispatchUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("dispatch_unavailable")


class RunDispatchMessage(BaseModel):
    """The only data allowed to cross the public-web to worker queue."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    run_alias: str = Field(pattern=_SAFE_ALIAS)
    idempotency_key: str = Field(pattern=_SAFE_OPAQUE)

    def to_bytes(self) -> bytes:
        return self.model_dump_json().encode("utf-8")

    @classmethod
    def from_bytes(cls, payload: bytes) -> Self:
        invalid = not isinstance(payload, bytes) or not 2 <= len(payload) <= _MAX_MESSAGE_BYTES
        message: Self | None = None
        if not invalid:
            try:
                message = cls.model_validate_json(payload)
            except (ValidationError, ValueError, TypeError):
                invalid = True
        if invalid or message is None:
            raise ValueError("dispatch_message_invalid") from None
        return message


class InlineRunDispatcher:
    """Explicit local/test dispatch using the exact cloud message type."""

    def __init__(self, handler: Callable[[RunDispatchMessage], object]) -> None:
        self._handler = handler

    def dispatch(self, run_alias: str, idempotency_key: str) -> None:
        self._handler(
            RunDispatchMessage(run_alias=run_alias, idempotency_key=idempotency_key)
        )


class PubSubRunDispatcher:
    """Publish one bounded message without retaining provider failures."""

    def __init__(
        self,
        publisher: Any,
        *,
        topic_path: str,
        timeout_seconds: float = 5,
    ) -> None:
        from re import fullmatch

        if not isinstance(topic_path, str) or fullmatch(_TOPIC, topic_path) is None:
            raise ValueError("Pub/Sub topic path is invalid")
        if not 0.1 <= timeout_seconds <= 30:
            raise ValueError("Pub/Sub timeout must be between 0.1 and 30 seconds")
        self._publisher = publisher
        self._topic_path = topic_path
        self._timeout_seconds = timeout_seconds

    def dispatch(self, run_alias: str, idempotency_key: str) -> None:
        message = RunDispatchMessage(
            run_alias=run_alias,
            idempotency_key=idempotency_key,
        )
        failed = False
        try:
            future = self._publisher.publish(
                self._topic_path,
                message.to_bytes(),
                content_type="application/json",
                schema_version="1",
            )
            future.result(timeout=self._timeout_seconds)
        except Exception:  # noqa: BLE001 - provider details never cross this boundary
            failed = True
        if failed:
            raise DispatchUnavailable() from None
