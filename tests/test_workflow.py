from datetime import UTC, datetime, timedelta

import pytest

from secondsignal.domain import CaseState, Channel, DeliveryKind, IncomingMessage
from secondsignal.identities import IdentityRegistry, RegistryDocument
from secondsignal.risk import RuleBasedRiskAnalyzer
from secondsignal.state_machine import CaseStateMachine
from secondsignal.workflow import VerificationWorkflow

NOW = datetime(2026, 8, 10, 18, 0, tzinfo=UTC)


def make_registry(*, include_email: bool = True) -> IdentityRegistry:
    routes = [
        {
            "channel": "telegram",
            "sender_address": "verifier-tg",
            "conversation_id": "conv-verifier-tg",
        }
    ]
    if include_email:
        routes.insert(
            0,
            {
                "channel": "email",
                "sender_address": "asha@example.com",
                "recipient": "asha@example.com",
            },
        )
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
                        "aliases": ["Asha Rao", "Asha", "CEO"],
                        "routes": routes,
                    }
                ],
            }
        )
    )


def make_message(
    *,
    channel: Channel = Channel.TELEGRAM,
    sender: str = "reporter-tg",
    text: str = "/verify Asha Rao\n\nBuy five $100 gift cards now. Do not call.",
    message_id: str = "msg-report",
    conversation_id: str = "conv-reporter",
) -> IncomingMessage:
    return IncomingMessage(
        message_id=message_id,
        conversation_id=conversation_id,
        connection_id=f"conn-{channel.value}",
        channel=channel,
        sender_address=sender,
        text=text,
        received_at=NOW,
    )


@pytest.fixture
def workflow(repository) -> VerificationWorkflow:
    return VerificationWorkflow(
        registry=make_registry(),
        analyzer=RuleBasedRiskAnalyzer(),
        repository=repository,
        state_machine=CaseStateMachine(),
        clock=lambda: NOW,
        token_generator=lambda: "SS-7K4P2M",
        timeout=timedelta(minutes=10),
    )


@pytest.fixture
def telegram_report() -> IncomingMessage:
    return make_message()


@pytest.fixture
def email_denial() -> IncomingMessage:
    return make_message(
        channel=Channel.EMAIL,
        sender="asha@example.com",
        text="NO SS-7K4P2M",
        message_id="msg-denial",
        conversation_id="conv-email-verifier",
    )


def test_telegram_request_creates_email_verification(
    workflow: VerificationWorkflow,
    telegram_report: IncomingMessage,
) -> None:
    result = workflow.handle(telegram_report)

    assert [delivery.kind for delivery in result.deliveries] == [
        DeliveryKind.REPLY_TO_MESSAGE,
        DeliveryKind.INITIATE_EMAIL,
    ]
    assert result.deliveries[1].recipient == "asha@example.com"
    assert "NO SS-7K4P2M" in result.deliveries[1].text


def test_registered_email_denial_returns_receipt_to_origin(
    workflow: VerificationWorkflow,
    telegram_report: IncomingMessage,
    email_denial: IncomingMessage,
) -> None:
    workflow.handle(telegram_report)

    result = workflow.handle(email_denial)

    assert len(result.deliveries) == 1
    assert result.deliveries[0].kind is DeliveryKind.SEND_TO_CONVERSATION
    assert result.deliveries[0].conversation_id == telegram_report.conversation_id
    assert "DENIED - DO NOT PROCEED" in result.deliveries[0].text


def test_email_request_uses_existing_telegram_conversation(
    workflow: VerificationWorkflow,
) -> None:
    report = make_message(
        channel=Channel.EMAIL,
        sender="reporter@example.com",
        message_id="msg-email-report",
        conversation_id="conv-email-reporter",
    )

    result = workflow.handle(report)

    assert result.deliveries[1].kind is DeliveryKind.SEND_TO_CONVERSATION
    assert result.deliveries[1].conversation_id == "conv-verifier-tg"


def test_unknown_identity_is_rejected_without_case(workflow, repository) -> None:
    result = workflow.handle(make_message(text="/verify Unknown Person\n\nSend money now"))

    assert "UNKNOWN IDENTITY" in result.deliveries[0].text
    assert repository.list_recent() == []


def test_unauthorized_reporter_is_rejected(workflow, repository) -> None:
    result = workflow.handle(make_message(sender="intruder"))

    assert "NOT AUTHORIZED" in result.deliveries[0].text
    assert repository.list_recent() == []


def test_unregistered_verifier_cannot_resolve_case(
    workflow,
    repository,
    telegram_report,
) -> None:
    workflow.handle(telegram_report)

    result = workflow.handle(
        make_message(
            channel=Channel.EMAIL,
            sender="attacker@example.com",
            text="YES SS-7K4P2M",
            message_id="msg-forged",
        )
    )

    assert "INVALID RESPONSE" in result.deliveries[0].text
    assert repository.get_by_token("SS-7K4P2M").state is CaseState.AWAITING_VERIFICATION


def test_unknown_token_does_not_resolve_any_case(workflow) -> None:
    result = workflow.handle(
        make_message(
            channel=Channel.EMAIL,
            sender="asha@example.com",
            text="YES SS-ABC123",
        )
    )

    assert "INVALID RESPONSE" in result.deliveries[0].text


def test_duplicate_report_returns_original_case(workflow, telegram_report, repository) -> None:
    first = workflow.handle(telegram_report)
    second = workflow.handle(telegram_report)

    assert len(repository.list_recent()) == 1
    assert "SS-7K4P2M" in first.deliveries[0].text
    assert "SS-7K4P2M" in second.deliveries[0].text
    assert len(second.deliveries) == 1


def test_duplicate_valid_response_cannot_change_terminal_state(
    workflow,
    telegram_report,
    email_denial,
    repository,
) -> None:
    workflow.handle(telegram_report)
    workflow.handle(email_denial)

    duplicate = workflow.handle(email_denial.model_copy(update={"message_id": "msg-duplicate"}))

    assert repository.get_by_token("SS-7K4P2M").state is CaseState.DENIED
    assert duplicate.deliveries[0].kind is DeliveryKind.REPLY_TO_MESSAGE
    assert "already resolved" in duplicate.deliveries[0].text


def test_only_original_reporter_can_cancel(workflow, telegram_report, repository) -> None:
    workflow.handle(telegram_report)
    rejected = workflow.handle(
        make_message(
            sender="intruder",
            text="/cancel SS-7K4P2M",
            message_id="msg-cancel-intruder",
        )
    )
    accepted = workflow.handle(make_message(text="/cancel SS-7K4P2M", message_id="msg-cancel"))

    assert "NOT AUTHORIZED" in rejected.deliveries[0].text
    assert "CANCELLED" in accepted.deliveries[0].text
    assert repository.get_by_token("SS-7K4P2M").state is CaseState.CANCELLED


def test_expiration_returns_unverified_receipt(
    workflow,
    telegram_report,
    repository,
) -> None:
    workflow.handle(telegram_report)

    result = workflow.expire_due(NOW + timedelta(minutes=11))

    assert "UNVERIFIED" in result.deliveries[0].text
    assert repository.get_by_token("SS-7K4P2M").state is CaseState.EXPIRED


def test_no_independent_route_resolves_unverified(repository) -> None:
    workflow = VerificationWorkflow(
        registry=make_registry(include_email=False),
        analyzer=RuleBasedRiskAnalyzer(),
        repository=repository,
        state_machine=CaseStateMachine(),
        clock=lambda: NOW,
        token_generator=lambda: "SS-7K4P2M",
        timeout=timedelta(minutes=10),
    )

    result = workflow.handle(make_message())

    assert "NO INDEPENDENT ROUTE" in result.deliveries[0].text
    assert repository.get_by_token("SS-7K4P2M").state is CaseState.UNVERIFIED


def test_model_fallback_is_recorded(repository) -> None:
    class FallbackAnalyzer(RuleBasedRiskAnalyzer):
        last_fallback_reason = "http_503"

    workflow = VerificationWorkflow(
        registry=make_registry(),
        analyzer=FallbackAnalyzer(),
        repository=repository,
        state_machine=CaseStateMachine(),
        clock=lambda: NOW,
        token_generator=lambda: "SS-7K4P2M",
        timeout=timedelta(minutes=10),
    )

    workflow.handle(make_message())

    case = repository.get_by_token("SS-7K4P2M")
    event_types = [event.event_type for event in repository.list_events(case.case_id)]
    assert "model.fallback" in event_types


def test_status_is_visible_only_to_original_reporter(workflow, telegram_report) -> None:
    workflow.handle(telegram_report)

    result = workflow.handle(make_message(text="/status SS-7K4P2M", message_id="msg-status"))

    assert "AWAITING VERIFICATION" in result.deliveries[0].text


def test_delivery_failure_returns_unverified_receipt(
    workflow,
    telegram_report,
    repository,
) -> None:
    workflow.handle(telegram_report)

    result = workflow.mark_delivery_failed("SS-7K4P2M", NOW + timedelta(seconds=2))

    assert "UNVERIFIED" in result.deliveries[0].text
    assert repository.get_by_token("SS-7K4P2M").state is CaseState.DELIVERY_FAILED
