"""Explicit subject-bound activation without imported-identity account matching."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol, Self

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
    DELIVERED = "delivered"
    DELIVERY_FAILED = "delivery_failed"


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
}


class ActivationService:
    """Validate an exact committed selection and activate only token bearers."""

    def __init__(
        self,
        *,
        decisionos_repository: DecisionOSRepository,
        graph_repository: OrganizationGraphRepository,
        transport: SubjectInvitationTransport | None = None,
    ) -> None:
        route_id = None if transport is None else getattr(transport, "route_id", None)
        if route_id is not None and (
            type(route_id) is not str
            or re.fullmatch(_DELIVERY_ROUTE_ID, route_id) is None
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
        failed = False
        grants = None
        try:
            grants = self._graph.create_subject_invitations(
                self._decisionos,
                context,
                subject_ids=request.subject_ids,
                role=request.role,
                expires_in=timedelta(seconds=request.expires_in_seconds),
                delivery_route_id=self._delivery_route_id,
            )
        except (DecisionOSAuthorizationDenied, InvitationUnavailable):
            raise
        except Exception:  # noqa: BLE001 - hostile seam details are sealed
            failed = True
        if failed or type(grants) is not tuple:
            raise InvitationUnavailable() from None
        finalized: list[SubjectInvitationGrant] = []
        for grant in grants:
            if grant.token is None or self._transport is None:
                finalized.append(grant)
                continue
            delivered = False
            try:
                self._transport.deliver(grant)
                delivered = True
            except Exception:  # noqa: BLE001 - provider details never cross the boundary
                delivered = False
            delivery_update_failed = False
            updated = None
            try:
                updated = self._decisionos.record_subject_invitation_delivery(
                    context,
                    grant,
                    delivered=delivered,
                )
            except (DecisionOSAuthorizationDenied, InvitationUnavailable):
                raise
            except Exception:  # noqa: BLE001 - provider/storage details are sealed
                delivery_update_failed = True
            if delivery_update_failed or updated is None:
                raise InvitationUnavailable() from None
            finalized.append(updated)
        receipt_failed = False
        receipt = None
        try:
            invitations = tuple(
                SubjectInvitationReceipt(
                    invitation_id=grant.invitation_id,
                    subject_id=grant.subject_id,
                    status=_PUBLIC_STATUS[grant.delivery_status],
                    expires_at=grant.expires_at,
                )
                for grant in finalized
            )
            delivered_count = sum(
                item.status is ActivationDeliveryStatus.DELIVERED for item in invitations
            )
            receipt = BulkInvitationReceipt(
                organization_id=context.organization_id,
                requested_subject_ids=request.subject_ids,
                invitations=invitations,
                created_count=len(invitations),
                delivered_count=delivered_count,
                pending_count=len(invitations) - delivered_count,
            )
        except Exception:  # noqa: BLE001 - hostile seam values are fixed-safe
            receipt_failed = True
        if receipt_failed or receipt is None:
            raise InvitationUnavailable() from None
        return receipt

    def accept(
        self,
        principal: DecisionOSPrincipal,
        token: str,
    ) -> ActivatedOrganizationMembership:
        failed = False
        accepted = None
        try:
            accepted = self._graph.accept_subject_invitation(
                self._decisionos,
                principal,
                token,
            )
        except Exception:  # noqa: BLE001 - every token failure is non-enumerating
            failed = True
        if failed or accepted is None:
            raise InvitationUnavailable() from None
        membership, subject = accepted
        result_failed = False
        result = None
        try:
            result = ActivatedOrganizationMembership(
                organization_id=membership.organization_id,
                subject_id=subject.subject_id,
                uid=membership.uid,
                role=membership.role,
                status=membership.status,
            )
        except Exception:  # noqa: BLE001 - hostile seam values are fixed-safe
            result_failed = True
        if result_failed or result is None:
            raise InvitationUnavailable() from None
        return result


__all__ = [
    "ActivatedOrganizationMembership",
    "ActivationDeliveryStatus",
    "ActivationService",
    "BulkInvitationReceipt",
    "BulkInvitationRequest",
    "SubjectInvitationReceipt",
    "SubjectInvitationTransport",
]
