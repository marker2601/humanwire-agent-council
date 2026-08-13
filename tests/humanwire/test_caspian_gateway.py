import threading
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from caspian_sdk import CommError
from caspian_sdk.client import Message
from pydantic import SecretStr

from humanwire.caspian_gateway import CaspianGateway, UnsupportedCaspianChannelError
from humanwire.config import Settings
from humanwire.domain import Channel, DeliveryInstruction, DeliveryKind, WorkflowResult

NOW = datetime(2026, 8, 11, 15, 0, tzinfo=UTC)
ASSIGNMENT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


class FakeRepository:
    def __init__(self) -> None:
        self.statuses: dict[str, tuple[str, datetime]] = {}

    def set_runtime_status(self, key: str, value: str, updated_at: datetime) -> None:
        self.statuses[key] = (value, updated_at)

    def get_runtime_status(self, key: str) -> tuple[str, datetime] | None:
        return self.statuses.get(key)


class FakeWorkflow:
    def __init__(self) -> None:
        self.calls = []
        self.result = WorkflowResult()
        self.delivery_results: list[tuple[DeliveryInstruction, bool, datetime]] = []
        self.failure_result = WorkflowResult()
        self.claim_renewals: list[tuple[DeliveryInstruction, datetime]] = []
        self.claim_renewed = threading.Event()
        self.allow_claim_renewal = True

    def handle(self, message):
        self.calls.append(message)
        return self.result

    def mark_delivery_result(
        self, instruction: DeliveryInstruction, succeeded: bool, now: datetime
    ) -> WorkflowResult:
        self.delivery_results.append((instruction, succeeded, now))
        return WorkflowResult() if succeeded else self.failure_result

    def renew_delivery_claim(
        self,
        instruction: DeliveryInstruction,
        now: datetime,
    ) -> bool:
        self.claim_renewals.append((instruction, now))
        if len(self.claim_renewals) >= 2:
            self.claim_renewed.set()
        return self.allow_claim_renewal


class FakeClient:
    def __init__(self) -> None:
        self.email_connections: list[str] = []
        self.telegram_connections: list[str] = []
        self.handlers = []
        self.replies: list[tuple[str, str]] = []
        self.initiated: list[tuple[str, str, str]] = []
        self.sent: list[tuple[str, str]] = []
        self.listen_calls: list[str] = []
        self.failures: dict[str, CommError] = {}
        self.close_calls = 0

    @property
    def on_message_registration_count(self) -> int:
        return len(self.handlers)

    @property
    def handler(self):
        return self.handlers[0]

    def connect_email(self, *, username: str):
        self.email_connections.append(username)
        return {"id": "email-connection-exact"}

    def connect_telegram(self, *, bot_token: str):
        self.telegram_connections.append(bot_token)
        return {"connection_id": "telegram-connection-exact"}

    def on_message(self, handler):
        self.handlers.append(handler)
        return handler

    def reply(self, message_id: str, *, text: str):
        if error := self.failures.get("reply"):
            raise error
        self.replies.append((message_id, text))
        return {"id": "reply-result"}

    def initiate(self, connection_id: str, *, recipient: str, text: str):
        if error := self.failures.get("initiate"):
            raise error
        self.initiated.append((connection_id, recipient, text))
        return {"id": "initiate-result"}

    def send_message(self, conversation_id: str, *, text: str):
        if error := self.failures.get("send_message"):
            raise error
        self.sent.append((conversation_id, text))
        return {"id": "send-result"}

    def listen(self, *, concurrency: str):
        self.listen_calls.append(concurrency)

    def close(self) -> None:
        self.close_calls += 1


class AwaitableClient(FakeClient):
    async def connect_email(self, *, username: str):
        self.email_connections.append(username)
        return {"id": "email-connection-exact"}

    async def connect_telegram(self, *, bot_token: str):
        self.telegram_connections.append(bot_token)
        return {"connection_id": "telegram-connection-exact"}

    async def reply(self, message_id: str, *, text: str):
        self.replies.append((message_id, text))
        return {"id": "reply-result"}

    async def listen(self, *, concurrency: str):
        self.listen_calls.append(concurrency)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        caspian_api_key=SecretStr("caspian-test-key"),
        telegram_bot_token=SecretStr("telegram-test-token"),
        caspian_email_username="humanwire-demo",
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


def email_message():
    return Message(
        id="Email-Message-ID/Exact",
        conversation_id="Gmail-Thread-ID/Exact",
        connection_id="Email-Connection-ID/Exact",
        customer_id="Customer-ID/Exact",
        agent_id="Agent-ID/Exact",
        channel="email",
        sender={"address": "Case.Sensitive+tag@Example.TEST", "name": "Morgan Exact"},
        subject="Re: HumanWire",
        text=(
            "ACK HW-EXACT\n\n"
            "On Tue, Aug 11, 2026 at 3:30 PM HumanWire wrote:\n"
            "> HUMANWIRE INTERVIEW"
        ),
        html="<p>ACK HW-EXACT</p>",
        _client=FakeClient(),
        media=[],
    )


def telegram_message():
    return Message(
        id="Telegram-Message-ID/Exact",
        conversation_id="Telegram-Conversation-ID/Exact",
        connection_id="Telegram-Connection-ID/Exact",
        customer_id="Customer-ID/Exact",
        agent_id="Agent-ID/Exact",
        channel="telegram",
        sender={"address": "Telegram_User-Exact", "display_name": "Telegram Exact"},
        subject=None,
        text="ACK HW-EXACT",
        html=None,
        _client=FakeClient(),
        media=[],
    )


def assigned_delivery(kind: DeliveryKind, **kwargs) -> DeliveryInstruction:
    return DeliveryInstruction(
        kind=kind,
        text="Transport body that must not become a destination",
        mandate_token="HW-EXACT",
        assignment_id=ASSIGNMENT_ID,
        **kwargs,
    )


def test_connect_registers_one_handler_and_records_both_channels_ready(
    gateway, fake_client, repository
) -> None:
    summary = gateway.connect()
    gateway.connect()

    assert summary.email_connection_id == "email-connection-exact"
    assert summary.telegram_connection_id == "telegram-connection-exact"
    assert fake_client.on_message_registration_count == 1
    assert repository.get_runtime_status("channel.email") == ("ready", NOW)
    assert repository.get_runtime_status("channel.telegram") == ("ready", NOW)


def test_one_handler_processes_both_channels(gateway, fake_client, workflow) -> None:
    gateway.connect()
    assert fake_client.on_message_registration_count == 1
    fake_client.handler(email_message())
    fake_client.handler(telegram_message())
    assert [call.channel for call in workflow.calls] == [Channel.EMAIL, Channel.TELEGRAM]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            email_message,
            {
                "message_id": "Email-Message-ID/Exact",
                "conversation_id": "Gmail-Thread-ID/Exact",
                "connection_id": "Email-Connection-ID/Exact",
                "sender_address": "Case.Sensitive+tag@Example.TEST",
                "sender_name": "Morgan Exact",
                "text": "ACK HW-EXACT",
            },
        ),
        (
            telegram_message,
            {
                "message_id": "Telegram-Message-ID/Exact",
                "conversation_id": "Telegram-Conversation-ID/Exact",
                "connection_id": "Telegram-Connection-ID/Exact",
                "sender_address": "Telegram_User-Exact",
                "sender_name": "Telegram Exact",
                "text": "ACK HW-EXACT",
            },
        ),
    ],
)
def test_normalization_preserves_transport_correlation_exactly(gateway, source, expected) -> None:
    incoming = gateway.to_incoming_message(source())

    assert incoming.received_at == NOW
    for field, value in expected.items():
        assert getattr(incoming, field) == value


def test_rejects_unsupported_channel_without_using_provider_body(gateway) -> None:
    message = telegram_message()
    message.channel = "sms"
    message.text = "PRIVATE provider body should not appear in the error"

    with pytest.raises(UnsupportedCaspianChannelError, match="sms") as caught:
        gateway.to_incoming_message(message)

    assert "PRIVATE provider body" not in str(caught.value)


def test_listen_uses_queue_concurrency(gateway, fake_client) -> None:
    gateway.listen()

    assert fake_client.listen_calls == ["queue"]


@pytest.mark.parametrize(
    ("delivery", "recording_attribute", "expected"),
    [
        (
            assigned_delivery(DeliveryKind.REPLY_TO_MESSAGE, message_id="source-message-exact"),
            "replies",
            [("source-message-exact", "Transport body that must not become a destination")],
        ),
        (
            assigned_delivery(
                DeliveryKind.SEND_TO_CONVERSATION,
                conversation_id="directory-conversation-exact",
            ),
            "sent",
            [
                (
                    "directory-conversation-exact",
                    "Transport body that must not become a destination",
                )
            ],
        ),
        (
            assigned_delivery(
                DeliveryKind.INITIATE_EMAIL,
                recipient="directory-recipient@example.test",
            ),
            "initiated",
            [
                (
                    "email-connection-exact",
                    "directory-recipient@example.test",
                    "Transport body that must not become a destination",
                )
            ],
        ),
    ],
)
def test_dispatch_uses_only_instruction_destination_and_marks_success(
    gateway, fake_client, workflow, delivery, recording_attribute, expected
) -> None:
    gateway.connect()

    gateway.dispatch(delivery)

    assert getattr(fake_client, recording_attribute) == expected
    assert workflow.delivery_results == [(delivery, True, NOW)]


def test_dispatch_fences_a_stale_durable_claim_before_provider_io(
    gateway, fake_client, workflow
) -> None:
    workflow.allow_claim_renewal = False
    delivery = assigned_delivery(
        DeliveryKind.INITIATE_EMAIL,
        recipient="directory-recipient@example.test",
        message_id="durable-attempt",
        dispatch_claim_id="stale-owner",
    )
    gateway.connect()

    gateway.dispatch(delivery)

    assert len(workflow.claim_renewals) == 1
    assert fake_client.initiated == []
    assert workflow.delivery_results == []


def test_dispatch_heartbeats_claim_while_provider_call_is_in_flight(
    settings, repository, workflow
) -> None:
    entered_provider = threading.Event()
    release_provider = threading.Event()
    clock = [NOW]

    class BlockingClient(FakeClient):
        def initiate(self, connection_id: str, *, recipient: str, text: str):
            entered_provider.set()
            release_provider.wait(timeout=2)
            return super().initiate(connection_id, recipient=recipient, text=text)

    client = BlockingClient()
    gateway = CaspianGateway(
        settings=settings,
        workflow=workflow,
        repository=repository,
        client=client,
        clock=lambda: clock[0],
    )
    gateway._delivery_claim_heartbeat_seconds = 0.01
    delivery = assigned_delivery(
        DeliveryKind.INITIATE_EMAIL,
        recipient="directory-recipient@example.test",
        message_id="durable-in-flight",
        dispatch_claim_id="live-owner",
    )
    gateway.connect()

    dispatcher = threading.Thread(target=gateway.dispatch, args=(delivery,))
    dispatcher.start()
    assert entered_provider.wait(timeout=1)
    clock[0] = NOW + timedelta(seconds=40)
    assert workflow.claim_renewed.wait(timeout=1)
    release_provider.set()
    dispatcher.join(timeout=1)

    assert not dispatcher.is_alive()
    assert workflow.claim_renewals[0] == (delivery, NOW)
    assert any(at == NOW + timedelta(seconds=40) for _, at in workflow.claim_renewals)
    assert client.initiated == [
        (
            "email-connection-exact",
            "directory-recipient@example.test",
            "Transport body that must not become a destination",
        )
    ]


def test_dispatch_heartbeats_claim_until_local_callback_finishes(
    settings, repository
) -> None:
    entered_callback = threading.Event()
    release_callback = threading.Event()
    clock = [NOW]

    class BlockingCallbackWorkflow(FakeWorkflow):
        def mark_delivery_result(
            self,
            instruction: DeliveryInstruction,
            succeeded: bool,
            now: datetime,
        ) -> WorkflowResult:
            entered_callback.set()
            release_callback.wait(timeout=2)
            return super().mark_delivery_result(instruction, succeeded, now)

    workflow = BlockingCallbackWorkflow()
    client = FakeClient()
    gateway = CaspianGateway(
        settings=settings,
        workflow=workflow,
        repository=repository,
        client=client,
        clock=lambda: clock[0],
    )
    gateway._delivery_claim_heartbeat_seconds = 0.01
    delivery = assigned_delivery(
        DeliveryKind.INITIATE_EMAIL,
        recipient="directory-recipient@example.test",
        message_id="durable-callback",
        dispatch_claim_id="callback-owner",
    )
    gateway.connect()

    dispatcher = threading.Thread(target=gateway.dispatch, args=(delivery,))
    dispatcher.start()
    assert entered_callback.wait(timeout=1)
    clock[0] = NOW + timedelta(seconds=40)
    assert workflow.claim_renewed.wait(timeout=1)
    release_callback.set()
    dispatcher.join(timeout=1)

    assert not dispatcher.is_alive()
    assert workflow.delivery_results == [(delivery, True, NOW)]


def test_comm_error_marks_failure_once_and_dispatches_recovery_without_recursing(
    gateway, fake_client, workflow
) -> None:
    failed = assigned_delivery(
        DeliveryKind.INITIATE_EMAIL,
        recipient="directory-recipient@example.test",
    )
    recovery = assigned_delivery(
        DeliveryKind.SEND_TO_CONVERSATION,
        conversation_id="directory-alternate-conversation",
    )
    fake_client.failures["initiate"] = CommError(503, "PRIVATE provider response")
    fake_client.failures["send_message"] = CommError(503, "PRIVATE second response")
    workflow.failure_result = WorkflowResult(deliveries=[recovery])
    gateway.connect()

    gateway.dispatch(failed)

    assert workflow.delivery_results == [(failed, False, NOW)]
    assert fake_client.sent == []


def test_successful_recovery_delivery_feeds_callback(gateway, fake_client, workflow) -> None:
    failed = assigned_delivery(
        DeliveryKind.INITIATE_EMAIL,
        recipient="directory-recipient@example.test",
    )
    recovery = assigned_delivery(
        DeliveryKind.SEND_TO_CONVERSATION,
        conversation_id="directory-alternate-conversation",
    )
    fake_client.failures["initiate"] = CommError(503, "unavailable")
    workflow.failure_result = WorkflowResult(deliveries=[recovery])
    gateway.connect()

    gateway.dispatch(failed)

    assert workflow.delivery_results == [(failed, False, NOW), (recovery, True, NOW)]
    assert fake_client.sent == [
        ("directory-alternate-conversation", "Transport body that must not become a destination")
    ]


def test_awaitable_sdk_methods_are_completed_at_sync_gateway_boundary(
    settings, repository, workflow
) -> None:
    client = AwaitableClient()
    gateway = CaspianGateway(
        settings=settings,
        workflow=workflow,
        repository=repository,
        client=client,
        clock=lambda: NOW,
    )
    delivery = assigned_delivery(DeliveryKind.REPLY_TO_MESSAGE, message_id="awaited-message")

    gateway.connect()
    gateway.dispatch(delivery)
    gateway.listen()

    assert client.email_connections == ["humanwire-demo"]
    assert client.telegram_connections == ["telegram-test-token"]
    assert client.replies == [
        ("awaited-message", "Transport body that must not become a destination")
    ]
    assert client.listen_calls == ["queue"]


@pytest.mark.asyncio
async def test_awaitable_sdk_methods_complete_when_caller_has_a_running_event_loop(
    settings, repository, workflow
) -> None:
    client = AwaitableClient()
    gateway = CaspianGateway(
        settings=settings,
        workflow=workflow,
        repository=repository,
        client=client,
        clock=lambda: NOW,
    )
    delivery = assigned_delivery(DeliveryKind.REPLY_TO_MESSAGE, message_id="loop-message")

    gateway.connect()
    gateway.dispatch(delivery)

    assert client.replies == [
        ("loop-message", "Transport body that must not become a destination")
    ]


def test_connect_marks_channel_error_before_reraising(
    gateway, fake_client, repository, monkeypatch
) -> None:
    def fail_telegram(*, bot_token: str):
        del bot_token
        raise CommError(401, "PRIVATE bad token response")

    monkeypatch.setattr(fake_client, "connect_telegram", fail_telegram)

    with pytest.raises(CommError):
        gateway.connect()

    gateway.close()

    assert repository.get_runtime_status("channel.email") == ("stopped", NOW)
    assert repository.get_runtime_status("channel.telegram") == ("error", NOW)
    assert fake_client.close_calls == 1


def test_close_stops_open_channels_closes_client_once_and_is_idempotent(
    gateway, fake_client, repository
) -> None:
    gateway.connect()

    gateway.close()
    gateway.close()

    assert fake_client.close_calls == 1
    assert repository.get_runtime_status("channel.email") == ("stopped", NOW)
    assert repository.get_runtime_status("channel.telegram") == ("stopped", NOW)


def test_adaptive_product_flow_uses_one_real_handler_across_channels(tmp_path) -> None:
    from scripts.smoke_humanwire import run_offline_proof

    proof = run_offline_proof(tmp_path)

    assert proof.gateway_handler_count == 1
    assert proof.gateway_channels == ("email", "telegram")
    assert proof.provider_callback_count > 0
    assert proof.provider_failure_safe is True
