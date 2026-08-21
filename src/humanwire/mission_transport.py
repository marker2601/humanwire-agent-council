"""Fail-closed external delivery boundary for connected HumanWire missions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Protocol, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from humanwire.decisionos_models import DecisionOSContext
from humanwire.domain import Channel
from humanwire.mission_models import (
    MissionActorType,
    MissionMode,
    MissionParticipant,
    MissionSnapshot,
)

_ULID = r"[0-9A-HJKMNP-TV-Z]{26}"
_ORGANIZATION_ID = rf"^org_{_ULID}$"
_MISSION_ID = rf"^mis_{_ULID}$"
_SUBJECT_ID = rf"^sub_{_ULID}$"
_PARTICIPANT_ID = r"^[a-z][a-z0-9-]{2,63}$"
_ROUTE_ID = r"^[a-z][a-z0-9-]{2,63}$"
_DELIVERY_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$"


class _TransportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MissionRoute(_TransportModel):
    route_id: str = Field(pattern=_ROUTE_ID)
    provider_route_id: str = Field(pattern=_ROUTE_ID)
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    subject_id: str = Field(pattern=_SUBJECT_ID)
    channel: Channel = Field(strict=False)
    consented: bool
    active: bool


class PreparedMissionOutreach(_TransportModel):
    mission_id: str = Field(pattern=_MISSION_ID)
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    participant_id: str = Field(pattern=_PARTICIPANT_ID)
    subject_id: str = Field(pattern=_SUBJECT_ID)
    route_id: str = Field(pattern=_ROUTE_ID)
    channel: Channel = Field(strict=False)
    text: str = Field(min_length=12, max_length=1_000)
    prepared_at: datetime

    @field_validator("prepared_at")
    @classmethod
    def prepared_time_is_aware(cls, value: datetime) -> datetime:
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("prepared_at must be timezone-aware")
        return value.astimezone(UTC)


class MissionDeliveryReceipt(_TransportModel):
    delivery_id: str = Field(pattern=_DELIVERY_ID)
    mission_id: str = Field(pattern=_MISSION_ID)
    participant_id: str = Field(pattern=_PARTICIPANT_ID)
    subject_id: str = Field(pattern=_SUBJECT_ID)
    route_id: str = Field(pattern=_ROUTE_ID)
    delivered_at: datetime

    @field_validator("delivered_at")
    @classmethod
    def delivery_time_is_aware(cls, value: datetime) -> datetime:
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("delivered_at must be timezone-aware")
        return value.astimezone(UTC)


class MissionDispatchOutcome(_TransportModel):
    code: Literal[
        "demo_inert",
        "no_eligible_participant",
        "no_consented_route",
        "provider_not_configured",
        "delivered",
        "delivery_state_unknown",
    ]
    delivery_id: str | None = Field(default=None, pattern=_DELIVERY_ID)
    route_id: str | None = Field(default=None, pattern=_ROUTE_ID)
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def outcome_time_is_aware(cls, value: datetime) -> datetime:
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def receipt_fields_match_delivery(self) -> Self:
        delivered = self.code == "delivered"
        if delivered != (self.delivery_id is not None and self.route_id is not None):
            raise ValueError("delivery outcome is inconsistent")
        return self


class MissionInboundResponse(_TransportModel):
    mission_id: str = Field(pattern=_MISSION_ID)
    participant_id: str = Field(pattern=_PARTICIPANT_ID)
    response_kind: Literal[
        "acknowledgement",
        "fact",
        "constraint",
        "change_request",
        "approval",
        "rejection",
        "availability",
    ]
    safe_summary: str = Field(min_length=1, max_length=240)
    received_at: datetime

    @field_validator("received_at")
    @classmethod
    def received_time_is_aware(cls, value: datetime) -> datetime:
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")
        return value.astimezone(UTC)


class MissionRouteRegistry(Protocol):
    def consented_routes(
        self,
        context: DecisionOSContext,
        subject_id: str,
    ) -> tuple[MissionRoute, ...]: ...


class MissionTransport(Protocol):
    route_id: str

    def deliver(self, outreach: PreparedMissionOutreach) -> MissionDeliveryReceipt: ...


class NoConfiguredMissionRoutes:
    """Explicit deployment state for Demo-only installations."""

    def consented_routes(
        self,
        _context: DecisionOSContext,
        _subject_id: str,
    ) -> tuple[MissionRoute, ...]:
        return ()


class MissionTransportUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("mission_transport_unavailable")


class CaspianMissionTarget(_TransportModel):
    route_id: str = Field(pattern=_ROUTE_ID)
    channel: Channel = Field(strict=False)
    recipient: SecretStr | None = None
    conversation_id: SecretStr | None = None

    @model_validator(mode="after")
    def has_one_channel_destination(self) -> Self:
        if self.channel is Channel.EMAIL:
            valid = self.recipient is not None and self.conversation_id is None
        else:
            valid = self.conversation_id is not None and self.recipient is None
        if not valid:
            raise ValueError("Caspian mission target is invalid")
        return self

    @classmethod
    def email(cls, *, route_id: str, recipient: str) -> CaspianMissionTarget:
        return cls(
            route_id=route_id,
            channel=Channel.EMAIL,
            recipient=SecretStr(recipient),
        )

    @classmethod
    def telegram(
        cls,
        *,
        route_id: str,
        conversation_id: str,
    ) -> CaspianMissionTarget:
        return cls(
            route_id=route_id,
            channel=Channel.TELEGRAM,
            conversation_id=SecretStr(conversation_id),
        )


class CaspianMissionTransport:
    """Use an already-connected Caspian client; never register another handler."""

    route_id = "provider-caspian"

    def __init__(
        self,
        *,
        client: object,
        email_connection_id: str | None,
        targets: tuple[CaspianMissionTarget, ...],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if (
            type(targets) is not tuple
            or any(type(item) is not CaspianMissionTarget for item in targets)
            or len({item.route_id for item in targets}) != len(targets)
            or (
                email_connection_id is not None
                and (
                    type(email_connection_id) is not str
                    or not 1 <= len(email_connection_id) <= 128
                )
            )
        ):
            raise ValueError("Caspian mission transport is invalid")
        self._client = client
        self._email_connection_id = email_connection_id
        self._targets = {item.route_id: item for item in targets}
        self._clock = clock

    def __repr__(self) -> str:
        return "CaspianMissionTransport()"

    @staticmethod
    def _delivery_id(value: object) -> str | None:
        if type(value) is dict:
            candidate = value.get("id")
        else:
            try:
                candidate = object.__getattribute__(value, "id")
            except Exception:  # noqa: BLE001 - provider values are untrusted
                candidate = None
        if (
            type(candidate) is str
            and 2 <= len(candidate) <= 128
            and all(character.isalnum() or character in "._:-" for character in candidate)
        ):
            return candidate
        return None

    def deliver(self, outreach: PreparedMissionOutreach) -> MissionDeliveryReceipt:
        if type(outreach) is not PreparedMissionOutreach:
            raise MissionTransportUnavailable()
        target = self._targets.get(outreach.route_id)
        if target is None or target.channel is not outreach.channel:
            raise MissionTransportUnavailable()
        failed = False
        response = None
        try:
            from humanwire.caspian_gateway import _complete

            if target.channel is Channel.EMAIL:
                if self._email_connection_id is None or target.recipient is None:
                    raise ValueError
                response = _complete(
                    self._client.initiate(
                        self._email_connection_id,
                        recipient=target.recipient.get_secret_value(),
                        text=outreach.text,
                    )
                )
            else:
                if target.conversation_id is None:
                    raise ValueError
                response = _complete(
                    self._client.send_message(
                        target.conversation_id.get_secret_value(),
                        text=outreach.text,
                    )
                )
        except Exception:  # noqa: BLE001 - provider details stay private
            failed = True
        delivery_id = self._delivery_id(response)
        if failed or delivery_id is None:
            raise MissionTransportUnavailable()
        return MissionDeliveryReceipt(
            delivery_id=delivery_id,
            mission_id=outreach.mission_id,
            participant_id=outreach.participant_id,
            subject_id=outreach.subject_id,
            route_id=outreach.route_id,
            delivered_at=_now(self._clock),
        )


def _now(clock: Callable[[], datetime]) -> datetime:
    failed = False
    value = None
    try:
        value = clock()
    except Exception:  # noqa: BLE001 - clock details stay private
        failed = True
    if (
        failed
        or type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("mission clock is invalid")
    return value.astimezone(UTC)


class ConnectedMissionDispatcher:
    def __init__(
        self,
        *,
        routes: MissionRouteRegistry,
        transport: MissionTransport | None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._routes = routes
        self._transport = transport
        self._clock = clock

    def check_readiness(
        self,
        context: DecisionOSContext,
        snapshot: MissionSnapshot,
        participant: MissionParticipant,
    ) -> str | None:
        if (
            type(context) is not DecisionOSContext
            or type(snapshot) is not MissionSnapshot
            or type(participant) is not MissionParticipant
            or snapshot.mode is not MissionMode.CONNECTED_ORGANIZATION
            or snapshot.organization_id != context.organization_id
            or participant.actor_type is not MissionActorType.HUMAN_MEMBER
            or participant.subject_id is None
            or participant not in snapshot.participants
        ):
            return "no_eligible_participant"
        failed = False
        raw_routes = None
        try:
            raw_routes = self._routes.consented_routes(context, participant.subject_id)
        except Exception:  # noqa: BLE001 - directory details stay private
            failed = True
        if failed or type(raw_routes) is not tuple:
            raw_routes = ()
        eligible = tuple(
            item
            for item in raw_routes
            if type(item) is MissionRoute
            and item.organization_id == context.organization_id
            and item.subject_id == participant.subject_id
            and item.consented
            and item.active
        )
        if not eligible:
            return "no_consented_route"
        transport = self._transport
        if transport is None:
            return "provider_not_configured"
        try:
            provider_route_id = object.__getattribute__(transport, "route_id")
        except Exception:  # noqa: BLE001 - provider descriptor details stay private
            provider_route_id = None
        if type(provider_route_id) is not str or not any(
            item.provider_route_id == provider_route_id for item in eligible
        ):
            return "provider_not_configured"
        return None

    def dispatch(
        self,
        context: DecisionOSContext,
        snapshot: MissionSnapshot,
        participant: MissionParticipant,
    ) -> MissionDispatchOutcome:
        occurred_at = _now(self._clock)
        if snapshot.mode is MissionMode.DEMO_RUN:
            return MissionDispatchOutcome(code="demo_inert", occurred_at=occurred_at)
        if (
            type(context) is not DecisionOSContext
            or type(snapshot) is not MissionSnapshot
            or type(participant) is not MissionParticipant
            or snapshot.organization_id != context.organization_id
            or participant.actor_type is not MissionActorType.HUMAN_MEMBER
            or participant.subject_id is None
            or participant not in snapshot.participants
        ):
            return MissionDispatchOutcome(
                code="no_eligible_participant",
                occurred_at=occurred_at,
            )
        failed = False
        raw_routes = None
        try:
            raw_routes = self._routes.consented_routes(context, participant.subject_id)
        except Exception:  # noqa: BLE001 - directory details stay private
            failed = True
        if failed or type(raw_routes) is not tuple:
            raw_routes = ()
        eligible = tuple(
            item
            for item in raw_routes
            if type(item) is MissionRoute
            and item.organization_id == context.organization_id
            and item.subject_id == participant.subject_id
            and item.consented
            and item.active
        )
        if not eligible:
            return MissionDispatchOutcome(
                code="no_consented_route",
                occurred_at=occurred_at,
            )
        transport = self._transport
        if transport is None:
            return MissionDispatchOutcome(
                code="provider_not_configured",
                occurred_at=occurred_at,
            )
        try:
            provider_route_id = object.__getattribute__(transport, "route_id")
        except Exception:  # noqa: BLE001 - provider descriptor details stay private
            provider_route_id = None
        route = next(
            (
                item
                for item in eligible
                if type(provider_route_id) is str
                and item.provider_route_id == provider_route_id
            ),
            None,
        )
        if route is None:
            return MissionDispatchOutcome(
                code="provider_not_configured",
                occurred_at=occurred_at,
            )
        outreach = PreparedMissionOutreach(
            mission_id=snapshot.mission_id,
            organization_id=snapshot.organization_id,
            participant_id=participant.participant_id,
            subject_id=participant.subject_id,
            route_id=route.route_id,
            channel=route.channel,
            text=(
                f"HumanWire is coordinating this decision: {snapshot.objective} "
                "Please reply with the requested facts, constraints, or decision."
            ),
            prepared_at=occurred_at,
        )
        failed = False
        receipt = None
        try:
            receipt = transport.deliver(outreach)
        except Exception:  # noqa: BLE001 - provider details stay private
            failed = True
        if (
            failed
            or type(receipt) is not MissionDeliveryReceipt
            or receipt.mission_id != snapshot.mission_id
            or receipt.participant_id != participant.participant_id
            or receipt.subject_id != participant.subject_id
            or receipt.route_id != route.route_id
        ):
            return MissionDispatchOutcome(
                code="delivery_state_unknown",
                occurred_at=occurred_at,
            )
        return MissionDispatchOutcome(
            code="delivered",
            delivery_id=receipt.delivery_id,
            route_id=receipt.route_id,
            occurred_at=receipt.delivered_at,
        )


__all__ = [
    "CaspianMissionTarget",
    "CaspianMissionTransport",
    "ConnectedMissionDispatcher",
    "MissionDeliveryReceipt",
    "MissionDispatchOutcome",
    "MissionInboundResponse",
    "MissionRoute",
    "MissionRouteRegistry",
    "MissionTransport",
    "MissionTransportUnavailable",
    "NoConfiguredMissionRoutes",
    "PreparedMissionOutreach",
]
