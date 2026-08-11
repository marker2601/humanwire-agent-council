from datetime import UTC, datetime, timedelta

from secondsignal.database import create_session_factory
from secondsignal.domain import (
    CaseState,
    Channel,
    DeliveryInstruction,
    DeliveryKind,
    IncomingMessage,
)
from secondsignal.identities import IdentityRegistry, RegistryDocument
from secondsignal.repository import SqlAlchemyCaseRepository
from secondsignal.risk import RuleBasedRiskAnalyzer
from secondsignal.state_machine import CaseStateMachine
from secondsignal.workflow import VerificationWorkflow


class FixedClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 10, 18, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class RecordingGateway:
    def __init__(self) -> None:
        self.dispatched: list[DeliveryInstruction] = []

    def dispatch_all(self, result) -> None:
        self.dispatched.extend(result.deliveries)


def message(
    *,
    message_id: str,
    channel: Channel,
    sender: str,
    conversation: str,
    text: str,
    clock: FixedClock,
) -> IncomingMessage:
    return IncomingMessage(
        message_id=message_id,
        conversation_id=conversation,
        connection_id=f"conn-{channel.value}",
        channel=channel,
        sender_address=sender,
        text=text,
        received_at=clock(),
    )


def make_registry() -> IdentityRegistry:
    return IdentityRegistry(
        RegistryDocument.model_validate(
            {
                "authorized_reporters": {
                    "telegram": ["reporter-tg"],
                    "email": ["reporter@example.com"],
                },
                "identities": [
                    {
                        "identity_id": "asha-rao",
                        "display_name": "Asha Rao",
                        "aliases": ["Asha Rao", "CEO"],
                        "routes": [
                            {
                                "channel": "email",
                                "sender_address": "asha@example.com",
                                "recipient": "asha@example.com",
                            },
                            {
                                "channel": "telegram",
                                "sender_address": "asha-tg",
                                "conversation_id": "conv-asha-tg",
                            },
                        ],
                    },
                    {
                        "identity_id": "northstar-vendor",
                        "display_name": "Northstar Vendor",
                        "aliases": ["Northstar Vendor", "Vendor"],
                        "routes": [
                            {
                                "channel": "email",
                                "sender_address": "vendor@example.com",
                                "recipient": "vendor@example.com",
                            },
                            {
                                "channel": "telegram",
                                "sender_address": "vendor-tg",
                                "conversation_id": "conv-vendor-tg",
                            },
                        ],
                    },
                    {
                        "identity_id": "maya-rao",
                        "display_name": "Maya Rao",
                        "aliases": ["Maya Rao", "Maya"],
                        "routes": [
                            {
                                "channel": "email",
                                "sender_address": "maya@example.com",
                                "recipient": "maya@example.com",
                            }
                        ],
                    },
                ],
            }
        )
    )


def main() -> None:
    clock = FixedClock()
    tokens = iter(("SS-EXEC01", "SS-VEND02", "SS-FAM003"))
    repository = SqlAlchemyCaseRepository(create_session_factory("sqlite://"))
    workflow = VerificationWorkflow(
        registry=make_registry(),
        analyzer=RuleBasedRiskAnalyzer(),
        repository=repository,
        state_machine=CaseStateMachine(),
        clock=clock,
        token_generator=lambda: next(tokens),
        timeout=timedelta(minutes=10),
    )
    gateway = RecordingGateway()

    executive = workflow.handle(
        message(
            message_id="msg-exec-report",
            channel=Channel.TELEGRAM,
            sender="reporter-tg",
            conversation="conv-reporter-tg",
            text=("/verify Asha Rao\nBuy $500 in gift cards immediately and keep it confidential."),
            clock=clock,
        )
    )
    gateway.dispatch_all(executive)
    assert any(
        item.kind is DeliveryKind.INITIATE_EMAIL and item.case_token == "SS-EXEC01"
        for item in executive.deliveries
    )
    gateway.dispatch_all(
        workflow.handle(
            message(
                message_id="msg-exec-denial",
                channel=Channel.EMAIL,
                sender="asha@example.com",
                conversation="conv-asha-email",
                text="NO SS-EXEC01",
                clock=clock,
            )
        )
    )
    assert repository.get_by_token("SS-EXEC01").state is CaseState.DENIED
    assert any("DENIED" in item.text for item in gateway.dispatched)
    print("PASS executive gift-card denial")

    vendor = workflow.handle(
        message(
            message_id="msg-vendor-report",
            channel=Channel.EMAIL,
            sender="reporter@example.com",
            conversation="conv-reporter-email",
            text=(
                "/verify Northstar Vendor\n"
                "Urgently change the routing number for today's vendor payment."
            ),
            clock=clock,
        )
    )
    gateway.dispatch_all(vendor)
    assert any(
        item.kind is DeliveryKind.SEND_TO_CONVERSATION and item.conversation_id == "conv-vendor-tg"
        for item in vendor.deliveries
    )
    gateway.dispatch_all(
        workflow.handle(
            message(
                message_id="msg-vendor-denial",
                channel=Channel.TELEGRAM,
                sender="vendor-tg",
                conversation="conv-vendor-tg",
                text="NO SS-VEND02",
                clock=clock,
            )
        )
    )
    assert repository.get_by_token("SS-VEND02").state is CaseState.DENIED
    print("PASS vendor bank-change denial")

    family = workflow.handle(
        message(
            message_id="msg-family-report",
            channel=Channel.TELEGRAM,
            sender="reporter-tg",
            conversation="conv-reporter-tg",
            text=(
                "/verify Maya Rao\n"
                "Emergency: send money now and do not call because my phone is broken."
            ),
            clock=clock,
        )
    )
    gateway.dispatch_all(family)
    clock.now += timedelta(minutes=11)
    gateway.dispatch_all(workflow.expire_due(clock()))
    assert repository.get_by_token("SS-FAM003").state is CaseState.EXPIRED
    assert any(
        item.case_token == "SS-FAM003" and "UNVERIFIED" in item.text for item in gateway.dispatched
    )
    print("PASS family-emergency timeout")
    print("SecondSignal offline smoke check passed all 3 scenarios.")


if __name__ == "__main__":
    main()
