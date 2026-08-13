"""Deterministic in-process Caspian adapter for isolated product proofs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Literal

from caspian_sdk import CommError


@dataclass(frozen=True)
class CapturedDelivery:
    """One outbound provider call captured without sending it anywhere."""

    kind: Literal["reply", "initiate", "send"]
    destination: str
    text: str
    connection_id: str | None = None


def email_envelope(
    *,
    message_id: str,
    conversation_id: str,
    sender_address: str,
    sender_name: str,
    text: str,
    connection_id: str = "offline-email-connection",
    subject: str | None = "HumanWire",
) -> SimpleNamespace:
    """Create a minimal offline email event accepted by ``CaspianGateway``."""
    return SimpleNamespace(
        id=message_id,
        conversation_id=conversation_id,
        connection_id=connection_id,
        channel="email",
        sender={"address": sender_address, "name": sender_name},
        subject=subject,
        text=text,
    )


def telegram_envelope(
    *,
    message_id: str,
    conversation_id: str,
    sender_address: str,
    sender_name: str,
    text: str,
    connection_id: str = "offline-telegram-connection",
) -> SimpleNamespace:
    """Create a minimal offline Telegram event accepted by ``CaspianGateway``."""
    return SimpleNamespace(
        id=message_id,
        conversation_id=conversation_id,
        connection_id=connection_id,
        channel="telegram",
        sender={"address": sender_address, "name": sender_name},
        subject=None,
        text=text,
    )


class OfflineCaspianClient:
    """A no-I/O Caspian client with explicit, inspectable proof traffic."""

    def __init__(self) -> None:
        self.handlers: list[Callable[[Any], Any]] = []
        self.inbound_channels: list[str] = []
        self.replies: list[tuple[str, str]] = []
        self.initiated: list[tuple[str, str, str]] = []
        self.sent: list[tuple[str, str]] = []
        self.deliveries: list[CapturedDelivery] = []
        self._initiate_failures: dict[str, tuple[int, str]] = {}

    @property
    def on_message_registration_count(self) -> int:
        return len(self.handlers)

    def configure_provider_failure(
        self,
        recipient: str,
        *,
        status_code: int = 503,
        body: str = "offline configured provider failure",
    ) -> None:
        """Cause the next email initiation to this synthetic recipient to fail."""
        self._initiate_failures[recipient] = (status_code, body)

    def connect_email(self, *, username: str) -> dict[str, str]:
        del username
        return {"id": "offline-email-connection"}

    def connect_telegram(self, *, bot_token: str) -> dict[str, str]:
        del bot_token
        return {"id": "offline-telegram-connection"}

    def on_message(self, handler: Callable[[Any], Any]) -> Callable[[Any], Any]:
        self.handlers.append(handler)
        return handler

    def emit_inbound(self, message: Any) -> None:
        """Deliver a synthetic inbound envelope through the registered gateway handler."""
        if not self.handlers:
            raise RuntimeError("Offline Caspian client has no registered message handler")
        self.inbound_channels.append(str(message.channel))
        self.handlers[0](message)

    def reply(self, message_id: str, *, text: str) -> dict[str, str]:
        self.replies.append((message_id, text))
        self.deliveries.append(CapturedDelivery("reply", message_id, text))
        return {"id": f"reply-{len(self.replies)}"}

    def initiate(self, connection_id: str, *, recipient: str, text: str) -> dict[str, str]:
        if failure := self._initiate_failures.pop(recipient, None):
            raise CommError(*failure)
        self.initiated.append((connection_id, recipient, text))
        self.deliveries.append(CapturedDelivery("initiate", recipient, text, connection_id))
        return {"id": f"email-{len(self.initiated)}"}

    def send_message(self, conversation_id: str, *, text: str) -> dict[str, str]:
        self.sent.append((conversation_id, text))
        self.deliveries.append(CapturedDelivery("send", conversation_id, text))
        return {"id": f"telegram-{len(self.sent)}"}
