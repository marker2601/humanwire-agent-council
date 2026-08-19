"""Explicit subject-bound activation without imported-identity account matching."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from humanwire.decisionos_models import (
    DecisionOSContext,
    DecisionOSPrincipal,
    DecisionOSRole,
    MembershipStatus,
)
from humanwire.decisionos_store import (
    DecisionOSAuthorizationDenied,
    DecisionOSRepository,
    InvitationUnavailable,
    SubjectInvitationDeliveryState,
    SubjectInvitationGrant,
)
from humanwire.organization_store import OrganizationGraphRepository

_SUBJECT_ID = r"^sub_[0-9A-HJKMNP-TV-Z]{26}$"
_ORGANIZATION_ID = r"^org_[0-9A-HJKMNP-TV-Z]{26}$"
_INVITATION_ID = r"^inv_[0-9A-HJKMNP-TV-Z]{26}$"
_FIREBASE_UID = r"^[A-Za-z0-9._:-]{1,128}$"
_DELIVERY_ROUTE_ID = r"^[a-z][a-z0-9_]{0,63}$"


class _ActivationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ActivationDeliveryStatus(StrEnum):
    NOT_DELIVERED = "not_delivered"
    DELIVERY_PENDING = "delivery_pending"
    DELIVERED = "delivered"
    DELIVERY_FAILED = "delivery_failed"
    DELIVERY_UNKNOWN = "delivery_unknown"


class BulkInvitationRequest(_ActivationModel):
    subject_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    role: DecisionOSRole
    expires_in_seconds: int = Field(default=604800, ge=60, le=2592000)

    @model_validator(mode="after")
    def has_exact_unique_subjects(self) -> Self:
        if (
            type(self.subject_ids) is not tuple
            or any(
                type(subject_id) is not str
                or re.fullmatch(_SUBJECT_ID, subject_id) is None
                for subject_id in self.subject_ids
            )
            or len(set(self.subject_ids)) != len(self.subject_ids)
            or type(self.role) is not DecisionOSRole
            or type(self.expires_in_seconds) is not int
        ):
            raise ValueError("bulk invitation request is invalid")
        return self


class SubjectInvitationReceipt(_ActivationModel):
    invitation_id: str = Field(pattern=_INVITATION_ID)
    subject_id: str = Field(pattern=_SUBJECT_ID)
    status: ActivationDeliveryStatus
    expires_at: datetime


class BulkInvitationReceipt(_ActivationModel):
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    requested_subject_ids: tuple[str, ...]
    invitations: tuple[SubjectInvitationReceipt, ...]
    created_count: int = Field(ge=0, le=100)
    delivered_count: int = Field(ge=0, le=100)
    pending_count: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def counts_match_subject_receipts(self) -> Self:
        if (
            type(self.requested_subject_ids) is not tuple
            or tuple(item.subject_id for item in self.invitations)
            != self.requested_subject_ids
            or self.created_count != len(self.invitations)
            or self.delivered_count
            != sum(item.status is ActivationDeliveryStatus.DELIVERED for item in self.invitations)
            or self.pending_count != self.created_count - self.delivered_count
        ):
            raise ValueError("bulk invitation receipt is inconsistent")
        return self


class ActivatedOrganizationMembership(_ActivationModel):
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    subject_id: str = Field(pattern=_SUBJECT_ID)
    uid: str = Field(pattern=_FIREBASE_UID)
    role: DecisionOSRole
    status: MembershipStatus

    @model_validator(mode="after")
    def is_active(self) -> Self:
        if self.status is not MembershipStatus.ACTIVE:
            raise ValueError("activated membership must be active")
        return self


class SubjectInvitationTransport(Protocol):
    route_id: str

    def deliver(self, grant: SubjectInvitationGrant) -> None: ...


_PUBLIC_STATUS = {
    SubjectInvitationDeliveryState.NOT_DELIVERED: ActivationDeliveryStatus.NOT_DELIVERED,
    SubjectInvitationDeliveryState.DELIVERED: ActivationDeliveryStatus.DELIVERED,
    SubjectInvitationDeliveryState.DELIVERY_FAILED: ActivationDeliveryStatus.DELIVERY_FAILED,
    SubjectInvitationDeliveryState.DELIVERY_PENDING: ActivationDeliveryStatus.DELIVERY_PENDING,
    SubjectInvitationDeliveryState.DELIVERY_SENDING: ActivationDeliveryStatus.DELIVERY_UNKNOWN,
}

_FailureMarker = Literal["authorization_denied", "invitation_unavailable"]


def _without_token(grant: SubjectInvitationGrant) -> SubjectInvitationGrant:
    return SubjectInvitationGrant(
        invitation_id=grant.invitation_id,
        organization_id=grant.organization_id,
        subject_id=grant.subject_id,
        role=grant.role,
        expires_at=grant.expires_at,
        delivery_status=grant.delivery_status,
        delivery_route_id=grant.delivery_route_id,
        retry_sequence=grant.retry_sequence,
        token=None,
    )


def _create_invitations_internal(
    graph: OrganizationGraphRepository,
    decisionos: DecisionOSRepository,
    transport: SubjectInvitationTransport | None,
    delivery_route_id: str | None,
    context: DecisionOSContext,
    request: BulkInvitationRequest,
) -> BulkInvitationReceipt | _FailureMarker:
    """Contain every token-bearing frame and return only token-free outcomes."""

    grants = None
    grant = None
    started = None
    try:
        grants = graph.create_subject_invitations(
            decisionos,
            context,
            subject_ids=request.subject_ids,
            role=request.role,
            expires_in=timedelta(seconds=request.expires_in_seconds),
            delivery_route_id=delivery_route_id,
        )
        if type(grants) is not tuple:
            return "invitation_unavailable"
        finalized: list[SubjectInvitationGrant] = []
        for grant in grants:
            if type(grant) is not SubjectInvitationGrant:
                return "invitation_unavailable"
            if grant.token is None or transport is None:
                finalized.append(_without_token(grant))
                continue
            started = decisionos.begin_subject_invitation_delivery(context, grant)
            if type(started) is not SubjectInvitationGrant or started.token is None:
                return "invitation_unavailable"
            try:
                transport.deliver(started)
            except Exception:  # noqa: BLE001 - sending is durably unknown
                finalized.append(_without_token(started))
                continue
            updated = decisionos.record_subject_invitation_delivery(
                context,
                started,
                delivered=True,
            )
            if type(updated) is not SubjectInvitationGrant or updated.token is not None:
                return "invitation_unavailable"
            finalized.append(updated)
        invitations = tuple(
            SubjectInvitationReceipt(
                invitation_id=item.invitation_id,
                subject_id=item.subject_id,
                status=_PUBLIC_STATUS[item.delivery_status],
                expires_at=item.expires_at,
            )
            for item in finalized
        )
        delivered_count = sum(
            item.status is ActivationDeliveryStatus.DELIVERED for item in invitations
        )
        return BulkInvitationReceipt(
            organization_id=context.organization_id,
            requested_subject_ids=request.subject_ids,
            invitations=invitations,
            created_count=len(invitations),
            delivered_count=delivered_count,
            pending_count=len(invitations) - delivered_count,
        )
    except DecisionOSAuthorizationDenied:
        return "authorization_denied"
    except Exception:  # noqa: BLE001 - every private seam failure is sealed here
        return "invitation_unavailable"
    finally:
        grant = None
        started = None
        grants = None


def _accept_invitation_internal(
    graph: OrganizationGraphRepository,
    decisionos: DecisionOSRepository,
    principal: DecisionOSPrincipal,
    raw_token: str,
) -> ActivatedOrganizationMembership | _FailureMarker:
    """Contain the raw bearer and provider objects below the public traceback."""

    accepted = None
    membership = None
    subject = None
    try:
        accepted = graph.accept_subject_invitation(decisionos, principal, raw_token)
        if type(accepted) is not tuple or len(accepted) != 2:
            return "invitation_unavailable"
        membership, subject = accepted
        return ActivatedOrganizationMembership(
            organization_id=membership.organization_id,
            subject_id=subject.subject_id,
            uid=membership.uid,
            role=membership.role,
            status=membership.status,
        )
    except Exception:  # noqa: BLE001 - all token failures are non-enumerating
        return "invitation_unavailable"
    finally:
        raw_token = ""
        accepted = None
        membership = None
        subject = None


class ActivationService:
    """Validate an exact committed selection and activate only token bearers."""

    def __init__(
        self,
        *,
        decisionos_repository: DecisionOSRepository,
        graph_repository: OrganizationGraphRepository,
        transport: SubjectInvitationTransport | None = None,
    ) -> None:
        try:
            route_id = (
                None
                if transport is None
                else object.__getattribute__(transport, "route_id")
            )
        except Exception:  # noqa: BLE001 - hostile descriptors are not trusted
            raise ValueError("invitation transport route is invalid") from None
        if transport is not None and (
            type(route_id) is not str or re.fullmatch(_DELIVERY_ROUTE_ID, route_id) is None
        ):
            raise ValueError("invitation transport route is invalid")
        self._decisionos = decisionos_repository
        self._graph = graph_repository
        self._transport = transport
        self._delivery_route_id = route_id

    def __repr__(self) -> str:
        return "ActivationService()"

    def create_invitations(
        self,
        context: DecisionOSContext,
        request: BulkInvitationRequest,
    ) -> BulkInvitationReceipt:
        if type(context) is not DecisionOSContext or type(request) is not BulkInvitationRequest:
            raise InvitationUnavailable()
        outcome = _create_invitations_internal(
            self._graph,
            self._decisionos,
            self._transport,
            self._delivery_route_id,
            context,
            request,
        )
        if outcome == "authorization_denied":
            del self
            raise DecisionOSAuthorizationDenied() from None
        if outcome == "invitation_unavailable":
            del self
            raise InvitationUnavailable() from None
        return outcome

    def accept(
        self,
        principal: DecisionOSPrincipal,
        token: str,
    ) -> ActivatedOrganizationMembership:
        outcome = _accept_invitation_internal(
            self._graph,
            self._decisionos,
            principal,
            token,
        )
        token = ""
        if outcome == "invitation_unavailable":
            del self
            raise InvitationUnavailable() from None
        return outcome


__all__ = [
    "ActivatedOrganizationMembership",
    "ActivationDeliveryStatus",
    "ActivationService",
    "BulkInvitationReceipt",
    "BulkInvitationRequest",
    "SubjectInvitationReceipt",
    "SubjectInvitationTransport",
]
