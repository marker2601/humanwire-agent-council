from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from caspian_sdk import CommClient, CommError

from secondsignal.config import Settings
from secondsignal.domain import (
    Channel,
    DeliveryInstruction,
    DeliveryKind,
    IncomingMessage,
)
from secondsignal.repository import SqlAlchemyCaseRepository
from secondsignal.workflow import VerificationWorkflow


class UnsupportedCaspianChannelError(ValueError):
    """Raised when a message arrives from a channel SecondSignal does not trust."""


@dataclass(frozen=True)
class ConnectionSummary:
    email_connection_id: str
    telegram_connection_id: str


class CaspianGateway:
    def __init__(
        self,
        settings: Settings,
        workflow: VerificationWorkflow,
        repository: SqlAlchemyCaseRepository,
        client: CommClient | Any | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.workflow = workflow
        self.repository = repository
        self.client = client
        self.clock = clock or (lambda: datetime.now(UTC))
        self.email_connection_id: str | None = None
        self.telegram_connection_id: str | None = None
        self._handler_registered = False

    @staticmethod
    def _connection_id(connection: Any) -> str:
        if isinstance(connection, dict):
            value = connection.get("connection_id") or connection.get("id")
        else:
            value = getattr(connection, "connection_id", None) or getattr(
                connection,
                "id",
                None,
            )
        if not value:
            raise ValueError("Caspian connection response did not contain an ID")
        return str(value)

    def _set_channel_status(self, channel: Channel, value: str) -> None:
        self.repository.set_runtime_status(
            f"channel.{channel.value}",
            value,
            self.clock(),
        )

    def connect(self) -> ConnectionSummary:
        api_key, telegram_bot_token = self.settings.require_listener_credentials()
        if self.client is None:
            self.client = CommClient(
                api_key=api_key,
                base_url=self.settings.caspian_base_url,
            )

        if self.email_connection_id is None:
            try:
                email = self.client.connect_email(username=self.settings.caspian_email_username)
                self.email_connection_id = self._connection_id(email)
                self._set_channel_status(Channel.EMAIL, "ready")
            except Exception:
                self._set_channel_status(Channel.EMAIL, "error")
                raise

        if self.telegram_connection_id is None:
            try:
                telegram = self.client.connect_telegram(bot_token=telegram_bot_token)
                self.telegram_connection_id = self._connection_id(telegram)
                self._set_channel_status(Channel.TELEGRAM, "ready")
            except Exception:
                self._set_channel_status(Channel.TELEGRAM, "error")
                raise

        if not self._handler_registered:
            self.client.on_message(self._handle_message)
            self._handler_registered = True

        return ConnectionSummary(
            email_connection_id=self.email_connection_id,
            telegram_connection_id=self.telegram_connection_id,
        )

    def listen(self) -> None:
        if self.email_connection_id is None or self.telegram_connection_id is None:
            self.connect()
        self.client.listen(concurrency="queue")

    def to_incoming_message(self, message: Any) -> IncomingMessage:
        try:
            channel = Channel(str(message.channel).lower())
        except ValueError as error:
            raise UnsupportedCaspianChannelError(
                f"Unsupported Caspian channel: {message.channel}"
            ) from error

        sender = message.sender or {}
        return IncomingMessage(
            message_id=str(message.id),
            conversation_id=str(message.conversation_id),
            connection_id=str(message.connection_id),
            channel=channel,
            sender_address=str(sender.get("address", "")),
            sender_name=sender.get("name") or sender.get("display_name"),
            subject=message.subject,
            text=message.text or "",
            received_at=self.clock(),
        )

    def _handle_message(self, message: Any) -> None:
        incoming = self.to_incoming_message(message)
        result = self.workflow.handle(incoming)
        for delivery in result.deliveries:
            self.dispatch(delivery)

    def _is_verification_delivery(self, delivery: DeliveryInstruction) -> bool:
        if not delivery.case_token:
            return False
        if delivery.kind is DeliveryKind.INITIATE_EMAIL:
            return True
        if delivery.kind is not DeliveryKind.SEND_TO_CONVERSATION:
            return False

        case = self.repository.get_by_token(delivery.case_token)
        route = case.verification_route if case else None
        return bool(
            route
            and route.channel is Channel.TELEGRAM
            and route.conversation_id == delivery.conversation_id
        )

    def dispatch(
        self,
        delivery: DeliveryInstruction,
        *,
        _allow_failure_callback: bool = True,
    ) -> None:
        if self.client is None:
            raise RuntimeError("Caspian gateway is not connected")

        try:
            if delivery.kind is DeliveryKind.REPLY_TO_MESSAGE:
                if not delivery.message_id:
                    raise ValueError("Reply delivery requires message_id")
                self.client.reply(delivery.message_id, text=delivery.text)
            elif delivery.kind is DeliveryKind.SEND_TO_CONVERSATION:
                if not delivery.conversation_id:
                    raise ValueError("Conversation delivery requires conversation_id")
                self.client.send_message(delivery.conversation_id, text=delivery.text)
            elif delivery.kind is DeliveryKind.INITIATE_EMAIL:
                if not self.email_connection_id:
                    raise RuntimeError("Email channel is not connected")
                if not delivery.recipient:
                    raise ValueError("Email delivery requires recipient")
                self.client.initiate(
                    self.email_connection_id,
                    recipient=delivery.recipient,
                    text=delivery.text,
                )
            else:
                raise ValueError(f"Unsupported delivery kind: {delivery.kind}")
        except CommError:
            if _allow_failure_callback and self._is_verification_delivery(delivery):
                result = self.workflow.mark_delivery_failed(
                    delivery.case_token,
                    self.clock(),
                )
                for receipt in result.deliveries:
                    self.dispatch(receipt, _allow_failure_callback=False)
                return
            raise
