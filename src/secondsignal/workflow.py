import hashlib
import secrets
import string
from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import uuid4

from secondsignal.commands import (
    CancelCommand,
    StatusCommand,
    UnsupportedCommand,
    VerificationResponse,
    VerifyCommand,
    parse_command,
)
from secondsignal.domain import (
    CaseEvent,
    CaseState,
    Channel,
    DeliveryInstruction,
    DeliveryKind,
    IncomingMessage,
    VerificationCase,
    VerificationRoute,
    WorkflowResult,
)
from secondsignal.identities import (
    AmbiguousIdentityError,
    IdentityRegistry,
    UnknownIdentityError,
    normalize_address,
)
from secondsignal.receipts import (
    render_acknowledgement,
    render_receipt,
    render_status,
    render_verification_request,
)
from secondsignal.redaction import redact_sensitive
from secondsignal.repository import SqlAlchemyCaseRepository
from secondsignal.risk import RiskAnalyzer
from secondsignal.state_machine import CaseStateMachine, InvalidTransitionError


def generate_case_token() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "SS-" + "".join(secrets.choice(alphabet) for _ in range(6))


class VerificationWorkflow:
    def __init__(
        self,
        registry: IdentityRegistry,
        analyzer: RiskAnalyzer,
        repository: SqlAlchemyCaseRepository,
        state_machine: CaseStateMachine,
        clock: Callable[[], datetime],
        token_generator: Callable[[], str] = generate_case_token,
        timeout: timedelta = timedelta(minutes=10),
    ) -> None:
        self.registry = registry
        self.analyzer = analyzer
        self.repository = repository
        self.state_machine = state_machine
        self.clock = clock
        self.token_generator = token_generator
        self.timeout = timeout

    @staticmethod
    def _reply(message: IncomingMessage, text: str) -> WorkflowResult:
        return WorkflowResult(
            deliveries=[
                DeliveryInstruction(
                    kind=DeliveryKind.REPLY_TO_MESSAGE,
                    message_id=message.message_id,
                    text=text,
                )
            ]
        )

    @staticmethod
    def _idempotency_key(message: IncomingMessage) -> str:
        value = f"{message.channel.value}|{message.message_id}|{message.connection_id}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _append_event(
        self,
        case: VerificationCase,
        event_type: str,
        now: datetime,
        metadata: dict | None = None,
    ) -> None:
        self.repository.append_event(
            case.case_id,
            CaseEvent(
                event_type=event_type,
                created_at=now,
                metadata=metadata or {},
            ),
        )

    def _transition(
        self,
        case: VerificationCase,
        target: CaseState,
        reason: str,
        now: datetime,
    ) -> VerificationCase:
        updated = self.state_machine.transition(case, target, reason, now)
        self.repository.save_case(updated)
        self._append_event(
            updated,
            f"case.{target.value}",
            now,
            {"reason": reason},
        )
        return updated

    def handle(self, message: IncomingMessage) -> WorkflowResult:
        command = parse_command(message.text)
        if isinstance(command, VerificationResponse):
            return self._handle_verification_response(message, command)

        if not self.registry.is_authorized(message):
            return self._reply(message, "NOT AUTHORIZED - this reporter is not registered.")

        if isinstance(command, VerifyCommand):
            return self._handle_verify(message, command)
        if isinstance(command, StatusCommand):
            return self._handle_status(message, command)
        if isinstance(command, CancelCommand):
            return self._handle_cancel(message, command)
        if isinstance(command, UnsupportedCommand):
            return self._reply(
                message,
                "Send /verify <claimed name> followed by a new line and the suspicious request.",
            )
        raise AssertionError("unhandled command")

    def _handle_verify(
        self,
        message: IncomingMessage,
        command: VerifyCommand,
    ) -> WorkflowResult:
        idempotency_key = self._idempotency_key(message)
        if existing := self.repository.get_by_idempotency_key(idempotency_key):
            return self._reply(message, render_acknowledgement(existing))

        try:
            identity = self.registry.resolve(command.claimed_identity)
        except UnknownIdentityError:
            return self._reply(message, "UNKNOWN IDENTITY - no verified route is registered.")
        except AmbiguousIdentityError:
            return self._reply(message, "AMBIGUOUS IDENTITY - use the full registered name.")

        now = self.clock()
        risk = self.analyzer.analyze(command.request_text)
        route = self.registry.select_independent_route(identity, message.channel)
        case = VerificationCase(
            case_id=uuid4(),
            token=self.token_generator().upper(),
            reporter_address=normalize_address(message.channel, message.sender_address),
            origin_channel=message.channel,
            origin_conversation_id=message.conversation_id,
            origin_message_id=message.message_id,
            redacted_message=redact_sensitive(command.request_text),
            claimed_identity_id=identity.identity_id,
            claimed_identity_name=identity.display_name,
            risk=risk,
            verification_route=route,
            state=CaseState.RECEIVED,
            created_at=now,
            expires_at=now + self.timeout,
            idempotency_key=idempotency_key,
        )
        self.repository.add_case(case)
        self._append_event(case, "case.created", now)
        case = self._transition(case, CaseState.ANALYZED, "risk_analyzed", now)

        if fallback_reason := getattr(self.analyzer, "last_fallback_reason", None):
            self._append_event(
                case,
                "model.fallback",
                now,
                {"reason": fallback_reason},
            )

        if route is None:
            case = self._transition(
                case,
                CaseState.UNVERIFIED,
                "no_independent_route",
                now,
            )
            return self._reply(
                message,
                "NO INDEPENDENT ROUTE - UNVERIFIED. Do not proceed without manual confirmation.\n"
                + render_receipt(case),
            )

        case = self._transition(
            case,
            CaseState.AWAITING_VERIFICATION,
            "verification_requested",
            now,
        )
        self._append_event(
            case,
            "verification.requested",
            now,
            {"channel": route.channel.value},
        )
        verification_delivery = self._verification_delivery(case, route)
        return WorkflowResult(
            deliveries=[
                DeliveryInstruction(
                    kind=DeliveryKind.REPLY_TO_MESSAGE,
                    message_id=message.message_id,
                    case_token=case.token,
                    text=render_acknowledgement(case),
                ),
                verification_delivery,
            ]
        )

    @staticmethod
    def _verification_delivery(
        case: VerificationCase,
        route: VerificationRoute,
    ) -> DeliveryInstruction:
        text = render_verification_request(case)
        if route.channel is Channel.EMAIL:
            return DeliveryInstruction(
                kind=DeliveryKind.INITIATE_EMAIL,
                recipient=route.recipient,
                text=text,
                case_token=case.token,
            )
        return DeliveryInstruction(
            kind=DeliveryKind.SEND_TO_CONVERSATION,
            conversation_id=route.conversation_id,
            text=text,
            case_token=case.token,
        )

    def _handle_verification_response(
        self,
        message: IncomingMessage,
        command: VerificationResponse,
    ) -> WorkflowResult:
        case = self.repository.get_by_token(command.token)
        if case is None or case.verification_route is None:
            return self._reply(message, "INVALID RESPONSE - unknown verification case.")

        route = case.verification_route
        sender_matches = normalize_address(
            message.channel,
            message.sender_address,
        ) == normalize_address(route.channel, route.sender_address)
        if message.channel is not route.channel or not sender_matches:
            self._append_event(case, "verification.invalid_response", self.clock())
            return self._reply(message, "INVALID RESPONSE - sender or channel is not registered.")

        if case.state is not CaseState.AWAITING_VERIFICATION:
            return self._reply(
                message,
                f"Case {case.token} is already resolved as {case.state.value.upper()}.",
            )

        now = self.clock()
        target = CaseState.VERIFIED if command.approved else CaseState.DENIED
        reason = "human_verified" if command.approved else "human_denied"
        case = self._transition(case, target, reason, now)
        return WorkflowResult(
            deliveries=[
                DeliveryInstruction(
                    kind=DeliveryKind.SEND_TO_CONVERSATION,
                    conversation_id=case.origin_conversation_id,
                    text=render_receipt(case),
                    case_token=case.token,
                )
            ]
        )

    def _is_original_reporter(
        self,
        message: IncomingMessage,
        case: VerificationCase,
    ) -> bool:
        return (
            message.channel is case.origin_channel
            and normalize_address(message.channel, message.sender_address) == case.reporter_address
        )

    def _handle_status(
        self,
        message: IncomingMessage,
        command: StatusCommand,
    ) -> WorkflowResult:
        case = self.repository.get_by_token(command.token)
        if case is None or not self._is_original_reporter(message, case):
            return self._reply(message, "CASE NOT FOUND for this reporter.")
        return self._reply(message, render_status(case))

    def _handle_cancel(
        self,
        message: IncomingMessage,
        command: CancelCommand,
    ) -> WorkflowResult:
        case = self.repository.get_by_token(command.token)
        if case is None or not self._is_original_reporter(message, case):
            return self._reply(message, "CASE NOT FOUND for this reporter.")
        if case.state is not CaseState.AWAITING_VERIFICATION:
            return self._reply(
                message,
                f"Case {case.token} is already resolved as {case.state.value.upper()}.",
            )
        case = self._transition(
            case,
            CaseState.CANCELLED,
            "reporter_cancelled",
            self.clock(),
        )
        return self._reply(message, render_receipt(case))

    def expire_due(self, now: datetime) -> WorkflowResult:
        deliveries = []
        for case in self.repository.list_expired_pending(now):
            try:
                case = self._transition(case, CaseState.EXPIRED, "timeout", now)
            except InvalidTransitionError:
                continue
            deliveries.append(
                DeliveryInstruction(
                    kind=DeliveryKind.SEND_TO_CONVERSATION,
                    conversation_id=case.origin_conversation_id,
                    text=render_receipt(case),
                    case_token=case.token,
                )
            )
        return WorkflowResult(deliveries=deliveries)

    def mark_delivery_failed(self, token: str, now: datetime) -> WorkflowResult:
        case = self.repository.get_by_token(token)
        if case is None or case.state is not CaseState.AWAITING_VERIFICATION:
            return WorkflowResult()
        case = self._transition(
            case,
            CaseState.DELIVERY_FAILED,
            "verification_delivery_failed",
            now,
        )
        return WorkflowResult(
            deliveries=[
                DeliveryInstruction(
                    kind=DeliveryKind.SEND_TO_CONVERSATION,
                    conversation_id=case.origin_conversation_id,
                    text=render_receipt(case),
                    case_token=case.token,
                )
            ]
        )
