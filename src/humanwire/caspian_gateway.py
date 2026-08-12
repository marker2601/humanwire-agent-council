"""Caspian transport boundary for HumanWire's channel-neutral workflow."""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from caspian_sdk import CommClient, CommError

from humanwire.config import Settings
from humanwire.domain import (
    Channel,
    DeliveryInstruction,
    DeliveryKind,
    IncomingMessage,
    WorkflowResult,
)
from humanwire.repository import SqlAlchemyHumanWireRepository
from humanwire.workflow import HumanWireWorkflow

logger = logging.getLogger("humanwire.caspian_gateway")


class UnsupportedCaspianChannelError(ValueError):
    """Raised when a message arrives from a channel HumanWire does not support."""


def _email_visible_reply(text: str) -> str:
    visible_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            break
        if stripped.startswith("On ") and stripped.endswith("wrote:"):
            break
        visible_lines.append(line)
    return "\n".join(visible_lines).strip()


def _complete(value: Any) -> Any:
    """Complete an awaitable returned by a test/client adapter at this sync boundary."""
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)

    result: list[Any] = []
    failure: list[BaseException] = []

    def run() -> None:
        try:
            result.append(asyncio.run(value))
        except BaseException as error:  # noqa: BLE001 - re-raise on the caller thread
            failure.append(error)

    thread = threading.Thread(target=run, name="humanwire-async-boundary")
    thread.start()
    thread.join()
    if failure:
        raise failure[0]
    return result[0]


@dataclass(frozen=True)
class ConnectionSummary:
    email_connection_id: str
    telegram_connection_id: str


class CaspianGateway:
    _delivery_claim_heartbeat_seconds = 10.0

    def __init__(
        self,
        settings: Settings,
        workflow: HumanWireWorkflow,
        repository: SqlAlchemyHumanWireRepository,
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
        self._closed = False

    @staticmethod
    def _connection_id(connection: Any) -> str:
        if isinstance(connection, dict):
            value = connection.get("connection_id") or connection.get("id")
        else:
            value = getattr(connection, "connection_id", None) or getattr(
                connection, "id", None
            )
        if not value:
            raise ValueError("Caspian connection response did not contain an ID")
        return str(value)

    def _set_channel_status(self, channel: Channel, value: str) -> None:
        self.repository.set_runtime_status(f"channel.{channel.value}", value, self.clock())

    def connect(self) -> ConnectionSummary:
        if self._closed:
            raise RuntimeError("Caspian gateway is closed")
        api_key, telegram_bot_token = self.settings.require_listener_credentials()
        if self.client is None:
            self.client = CommClient(api_key=api_key, base_url=self.settings.caspian_base_url)

        if self.email_connection_id is None:
            try:
                email = _complete(
                    self.client.connect_email(username=self.settings.caspian_email_username)
                )
                self.email_connection_id = self._connection_id(email)
                self._set_channel_status(Channel.EMAIL, "ready")
            except Exception:
                self._set_channel_status(Channel.EMAIL, "error")
                raise

        if self.telegram_connection_id is None:
            try:
                telegram = _complete(
                    self.client.connect_telegram(bot_token=telegram_bot_token)
                )
                self.telegram_connection_id = self._connection_id(telegram)
                self._set_channel_status(Channel.TELEGRAM, "ready")
            except Exception:
                self._set_channel_status(Channel.TELEGRAM, "error")
                raise

        if not self._handler_registered:
            _complete(self.client.on_message(self._handle_message))
            self._handler_registered = True

        return ConnectionSummary(
            email_connection_id=self.email_connection_id,
            telegram_connection_id=self.telegram_connection_id,
        )

    def close(self) -> None:
        if self._closed:
            return
        if self.client is None:
            self._closed = True
            return
        try:
            close = getattr(self.client, "close", None)
            if close is not None:
                _complete(close())
        except Exception:
            if self.email_connection_id is not None:
                self._set_channel_status(Channel.EMAIL, "error")
            if self.telegram_connection_id is not None:
                self._set_channel_status(Channel.TELEGRAM, "error")
            raise
        if self.email_connection_id is not None:
            self._set_channel_status(Channel.EMAIL, "stopped")
        if self.telegram_connection_id is not None:
            self._set_channel_status(Channel.TELEGRAM, "stopped")
        self._closed = True

    def listen(self) -> None:
        if self.email_connection_id is None or self.telegram_connection_id is None:
            self.connect()
        _complete(self.client.listen(concurrency="queue"))

    def to_incoming_message(self, message: Any) -> IncomingMessage:
        try:
            channel = Channel(str(message.channel).lower())
        except ValueError as error:
            raise UnsupportedCaspianChannelError(
                f"Unsupported Caspian channel: {message.channel}"
            ) from error

        sender = message.sender or {}
        text = message.text or ""
        if channel is Channel.EMAIL:
            text = _email_visible_reply(text)
        return IncomingMessage(
            message_id=str(message.id),
            conversation_id=str(message.conversation_id),
            connection_id=str(message.connection_id),
            channel=channel,
            sender_address=str(sender.get("address", "")),
            sender_name=sender.get("name") or sender.get("display_name"),
            subject=message.subject,
            text=text,
            received_at=self.clock(),
        )

    def _handle_message(self, message: Any) -> None:
        incoming = self.to_incoming_message(message)
        result = _complete(self.workflow.handle(incoming))
        self.dispatch_all(result)

    def dispatch_all(self, result: WorkflowResult) -> None:
        for delivery in result.deliveries:
            self.dispatch(delivery)

    def dispatch(
        self,
        delivery: DeliveryInstruction,
        *,
        _allow_failure_callback: bool = True,
    ) -> None:
        if self.client is None:
            raise RuntimeError("Caspian gateway is not connected")

        claim_stop: threading.Event | None = None
        claim_thread: threading.Thread | None = None
        if delivery.dispatch_claim_id is not None:
            if not _complete(self.workflow.renew_delivery_claim(delivery, self.clock())):
                return
            claim_stop = threading.Event()

            def renew_claim() -> None:
                assert claim_stop is not None
                while not claim_stop.wait(self._delivery_claim_heartbeat_seconds):
                    try:
                        if not _complete(
                            self.workflow.renew_delivery_claim(delivery, self.clock())
                        ):
                            logger.warning(
                                "delivery_claim_lost",
                                extra={
                                    "mandate_token": delivery.mandate_token,
                                    "event_type": "delivery.claim_lost",
                                    "channel": self._delivery_channel(delivery),
                                    "reason": "dispatch_fence_rejected",
                                },
                            )
                            return
                    except Exception:  # noqa: BLE001 - retry remains lease bounded
                        logger.warning(
                            "delivery_claim_renewal_failed",
                            extra={
                                "mandate_token": delivery.mandate_token,
                                "event_type": "delivery.claim_renewal_failed",
                                "channel": self._delivery_channel(delivery),
                                "reason": "claim_store_error",
                            },
                        )

            claim_thread = threading.Thread(
                target=renew_claim,
                name="humanwire-delivery-claim",
                daemon=True,
            )
            claim_thread.start()

        def stop_claim_heartbeat() -> None:
            if claim_stop is not None:
                claim_stop.set()
            if claim_thread is not None:
                claim_thread.join()

        try:
            if delivery.kind is DeliveryKind.REPLY_TO_MESSAGE:
                if not delivery.message_id:
                    raise ValueError("Reply delivery requires message_id")
                _complete(self.client.reply(delivery.message_id, text=delivery.text))
            elif delivery.kind is DeliveryKind.SEND_TO_CONVERSATION:
                if not delivery.conversation_id:
                    raise ValueError("Conversation delivery requires conversation_id")
                _complete(
                    self.client.send_message(delivery.conversation_id, text=delivery.text)
                )
            elif delivery.kind is DeliveryKind.INITIATE_EMAIL:
                if not self.email_connection_id:
                    raise RuntimeError("Email channel is not connected")
                if not delivery.recipient:
                    raise ValueError("Email delivery requires recipient")
                _complete(
                    self.client.initiate(
                        self.email_connection_id,
                        recipient=delivery.recipient,
                        text=delivery.text,
                    )
                )
            else:
                raise ValueError(f"Unsupported delivery kind: {delivery.kind}")
        except CommError:
            logger.warning(
                "delivery_failed",
                extra={
                    "mandate_token": delivery.mandate_token,
                    "event_type": "delivery.failed",
                    "channel": self._delivery_channel(delivery),
                    "reason": "provider_error",
                },
            )
            if _allow_failure_callback:
                try:
                    recovery = _complete(
                        self.workflow.mark_delivery_result(delivery, False, self.clock())
                    )
                finally:
                    stop_claim_heartbeat()
                for instruction in recovery.deliveries:
                    self.dispatch(instruction, _allow_failure_callback=False)
            else:
                stop_claim_heartbeat()
            return
        except BaseException:
            stop_claim_heartbeat()
            raise

        try:
            _complete(self.workflow.mark_delivery_result(delivery, True, self.clock()))
        finally:
            stop_claim_heartbeat()

    @staticmethod
    def _delivery_channel(delivery: DeliveryInstruction) -> str:
        if delivery.kind is DeliveryKind.INITIATE_EMAIL:
            return Channel.EMAIL.value
        if delivery.kind is DeliveryKind.SEND_TO_CONVERSATION:
            return Channel.TELEGRAM.value
        return "reply"
