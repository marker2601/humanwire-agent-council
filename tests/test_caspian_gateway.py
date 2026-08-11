from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from caspian_sdk import CommError
from pydantic import SecretStr

from secondsignal.caspian_gateway import (
    CaspianGateway,
    UnsupportedCaspianChannelError,
)
from secondsignal.config import Settings
from secondsignal.domain import (
    Channel,
    DeliveryInstruction,
    DeliveryKind,
    WorkflowResult,
)

NOW = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)


class FakeRepository:
    def __init__(self) -> None:
        self.statuses: dict[str, tuple[str, datetime]] = {}
        self.cases: dict[str, object] = {}

    def set_runtime_status(self, key: str, value: str, updated_at: datetime) -> None:
        self.statuses[key] = (value, updated_at)

    def get_runtime_status(self, key: str) -> tuple[str, datetime] | None:
        return self.statuses.get(key)

    def get_by_token(self, token: str):
        return self.cases.get(token)


class FakeWorkflow:
    def __init__(self) -> None:
        self.handled = []
        self.result = WorkflowResult()
        self.failed_tokens: list[tuple[str, datetime]] = []
        self.failure_result = WorkflowResult()

    def handle(self, message):
        self.handled.append(message)
        return self.result

    def mark_delivery_failed(self, token: str, now: datetime) -> WorkflowResult:
        self.failed_tokens.append((token, now))
        return self.failure_result


class FakeClient:
    def __init__(self) -> None:
        self.email_connections = []
        self.telegram_connections = []
        self.handlers = []
        self.replies = []
        self.initiated = []
        self.sent = []
        self.listen_calls = []
        self.initiate_error: CommError | None = None

    @property
    def on_message_registration_count(self) -> int:
        return len(self.handlers)

    def connect_email(self, *, username: str):
        self.email_connections.append(username)
        return {"id": "conn_email"}

    def connect_telegram(self, *, bot_token: str):
        self.telegram_connections.append(bot_token)
        return {"connection_id": "conn_telegram"}

    def on_message(self, handler):
        self.handlers.append(handler)
        return handler

    def reply(self, message_id: str, *, text: str):
        self.replies.append((message_id, text))

    def initiate(self, connection_id: str, *, recipient: str, text: str):
        if self.initiate_error:
            raise self.initiate_error
        self.initiated.append((connection_id, recipient, text))

    def send_message(self, conversation_id: str, *, text: str):
        self.sent.append((conversation_id, text))

    def listen(self, *, concurrency: str):
        self.listen_calls.append(concurrency)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        caspian_api_key=SecretStr("caspian-test-key"),
        telegram_bot_token=SecretStr("telegram-test-token"),
        caspian_email_username="secondsignal-demo",
    )


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def repository() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def workflow() -> FakeWorkflow:
    return FakeWorkflow()


@pytest.fixture
def gateway(settings, fake_client, repository, workflow) -> CaspianGateway:
    return CaspianGateway(
        settings=settings,
        workflow=workflow,
        repository=repository,
        client=fake_client,
        clock=lambda: NOW,
    )


def test_registers_exactly_one_handler_for_both_channels(
    gateway,
    fake_client,
    repository,
):
    summary = gateway.connect()
    gateway.connect()

    assert summary.email_connection_id == "conn_email"
    assert summary.telegram_connection_id == "conn_telegram"
    assert fake_client.on_message_registration_count == 1
    assert repository.get_runtime_status("channel.email") == ("ready", NOW)
    assert repository.get_runtime_status("channel.telegram") == ("ready", NOW)


def test_connect_marks_channel_error_before_reraising(
    gateway,
    fake_client,
    repository,
    monkeypatch,
):
    def fail_telegram(*, bot_token: str):
        raise CommError(401, "bad Telegram token")

    monkeypatch.setattr(fake_client, "connect_telegram", fail_telegram)

    with pytest.raises(CommError):
        gateway.connect()

    assert repository.get_runtime_status("channel.email") == ("ready", NOW)
    assert repository.get_runtime_status("channel.telegram") == ("error", NOW)


def test_converts_caspian_message_and_extracts_sender(gateway):
    message = SimpleNamespace(
        id="msg_1",
        conversation_id="conv_1",
        connection_id="conn_telegram",
        channel="telegram",
        sender={"address": "telegram-user-42", "name": "Dev Rao"},
        subject=None,
        text="/verify Asha Rao\nPlease send $2,400 now",
    )

    incoming = gateway.to_incoming_message(message)

    assert incoming.message_id == "msg_1"
    assert incoming.channel is Channel.TELEGRAM
    assert incoming.sender_address == "telegram-user-42"
    assert incoming.sender_name == "Dev Rao"
    assert incoming.received_at == NOW


def test_rejects_unsupported_channel(gateway):
    message = SimpleNamespace(
        id="msg_1",
        conversation_id="conv_1",
        connection_id="conn_sms",
        channel="sms",
        sender={"address": "+15555550123"},
        subject=None,
        text="hello",
    )

    with pytest.raises(UnsupportedCaspianChannelError, match="sms"):
        gateway.to_incoming_message(message)


def test_single_handler_runs_workflow_and_dispatches_result(
    gateway,
    fake_client,
    workflow,
):
    workflow.result = WorkflowResult(
        deliveries=[
            DeliveryInstruction(
                kind=DeliveryKind.REPLY_TO_MESSAGE,
                message_id="msg_1",
                text="Case opened",
            )
        ]
    )
    gateway.connect()
    message = SimpleNamespace(
        id="msg_1",
        conversation_id="conv_1",
        connection_id="conn_telegram",
        channel="telegram",
        sender={"address": "telegram-user-42"},
        subject=None,
        text="/status SS-ABC123",
    )

    fake_client.handlers[0](message)

    assert len(workflow.handled) == 1
    assert fake_client.replies == [("msg_1", "Case opened")]


def test_dispatches_email_with_initiate(gateway, fake_client):
    gateway.connect()
    gateway.dispatch(
        DeliveryInstruction(
            kind=DeliveryKind.INITIATE_EMAIL,
            recipient="asha@example.com",
            text="Verify case",
            case_token="SS-7K4P2M",
        )
    )

    assert fake_client.initiated == [("conn_email", "asha@example.com", "Verify case")]


def test_dispatches_telegram_with_existing_conversation(gateway, fake_client):
    gateway.dispatch(
        DeliveryInstruction(
            kind=DeliveryKind.SEND_TO_CONVERSATION,
            conversation_id="conv_asha_telegram",
            text="Verify case",
        )
    )

    assert fake_client.sent == [("conv_asha_telegram", "Verify case")]


def test_dispatches_reply_to_original_message(gateway, fake_client):
    gateway.dispatch(
        DeliveryInstruction(
            kind=DeliveryKind.REPLY_TO_MESSAGE,
            message_id="msg_origin",
            text="Acknowledged",
        )
    )

    assert fake_client.replies == [("msg_origin", "Acknowledged")]


def test_failed_verification_delivery_marks_case_and_sends_origin_receipt(
    gateway,
    fake_client,
    workflow,
):
    fake_client.initiate_error = CommError(503, "email unavailable")
    workflow.failure_result = WorkflowResult(
        deliveries=[
            DeliveryInstruction(
                kind=DeliveryKind.SEND_TO_CONVERSATION,
                conversation_id="conv_origin",
                text="DELIVERY FAILED receipt",
                case_token="SS-7K4P2M",
            )
        ]
    )
    gateway.connect()

    gateway.dispatch(
        DeliveryInstruction(
            kind=DeliveryKind.INITIATE_EMAIL,
            recipient="asha@example.com",
            text="Verify case",
            case_token="SS-7K4P2M",
        )
    )

    assert workflow.failed_tokens == [("SS-7K4P2M", NOW)]
    assert fake_client.sent == [("conv_origin", "DELIVERY FAILED receipt")]


def test_listen_uses_queue_concurrency(gateway, fake_client):
    gateway.listen()

    assert fake_client.listen_calls == ["queue"]
