from __future__ import annotations

import json

import pytest

from humanwire.cloud_dispatch import (
    DispatchUnavailable,
    InlineRunDispatcher,
    PubSubRunDispatcher,
    RunDispatchMessage,
)

ALIAS = "coordination-cloud-dispatch"
KEY = "dispatch-key-0000000000000001"


class PublishFuture:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.timeouts: list[float] = []

    def result(self, timeout: float):
        self.timeouts.append(timeout)
        if self.error is not None:
            raise self.error
        return "provider-message-id"


class Publisher:
    def __init__(self, future: PublishFuture) -> None:
        self.future = future
        self.calls = []

    def publish(self, topic: str, payload: bytes, **attributes: str):
        self.calls.append((topic, payload, attributes))
        return self.future


def test_dispatch_message_is_exact_safe_and_contains_no_request_data() -> None:
    message = RunDispatchMessage(
        run_alias=ALIAS,
        idempotency_key=KEY,
    )

    payload = message.to_bytes()

    assert json.loads(payload) == {
        "schema_version": 1,
        "run_alias": ALIAS,
        "idempotency_key": KEY,
    }
    assert b"objective" not in payload
    assert b"participant" not in payload
    assert RunDispatchMessage.from_bytes(payload) == message


def test_pubsub_dispatcher_publishes_once_and_waits_with_a_bound() -> None:
    future = PublishFuture()
    client = Publisher(future)
    dispatcher = PubSubRunDispatcher(
        client,
        topic_path="projects/humanwire-demo/topics/humanwire-runs",
        timeout_seconds=4,
    )

    dispatcher.dispatch(ALIAS, KEY)

    assert len(client.calls) == 1
    topic, payload, attributes = client.calls[0]
    assert topic == "projects/humanwire-demo/topics/humanwire-runs"
    assert RunDispatchMessage.from_bytes(payload).run_alias == ALIAS
    assert attributes == {"content_type": "application/json", "schema_version": "1"}
    assert future.timeouts == [4]


def test_pubsub_failure_is_fixed_and_retains_no_provider_exception_graph() -> None:
    future = PublishFuture(RuntimeError("PRIVATE-PROVIDER-PATH/API-KEY"))
    dispatcher = PubSubRunDispatcher(
        Publisher(future),
        topic_path="projects/humanwire-demo/topics/humanwire-runs",
    )

    with pytest.raises(DispatchUnavailable) as captured:
        dispatcher.dispatch(ALIAS, KEY)

    error = captured.value
    assert str(error) == "dispatch_unavailable"
    assert error.__context__ is None
    assert error.__cause__ is None
    assert "PRIVATE" not in repr(error)


def test_inline_dispatcher_uses_the_same_typed_message() -> None:
    received = []
    dispatcher = InlineRunDispatcher(received.append)

    dispatcher.dispatch(ALIAS, KEY)

    assert received == [RunDispatchMessage(run_alias=ALIAS, idempotency_key=KEY)]


@pytest.mark.parametrize(
    "payload",
    [
        b"{}",
        b'{"schema_version":2,"run_alias":"coordination-a","idempotency_key":"dispatch-key-0000000000000001"}',
        b'{"schema_version":1,"run_alias":"../private","idempotency_key":"dispatch-key-0000000000000001"}',
        b'{"schema_version":1,"run_alias":"coordination-a","idempotency_key":"short"}',
        b'{"schema_version":1,"run_alias":"coordination-a","idempotency_key":"dispatch-key-0000000000000001","extra":true}',
    ],
)
def test_dispatch_message_rejects_malformed_or_extra_content(payload: bytes) -> None:
    with pytest.raises(ValueError, match="dispatch_message_invalid"):
        RunDispatchMessage.from_bytes(payload)
