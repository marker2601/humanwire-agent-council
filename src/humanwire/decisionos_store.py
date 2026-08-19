"""Organization authority and workspace repositories for DecisionOS."""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from humanwire.decisionos_models import (
    DecisionOrganization,
    DecisionOSContext,
    DecisionOSPrincipal,
    DecisionOSRole,
    DecisionWorkspace,
    MembershipStatus,
    OrganizationMembership,
    WorkspacePlaybook,
)

_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ORGANIZATION_ID = r"^org_[0-9A-HJKMNP-TV-Z]{26}$"
_WORKSPACE_ID = r"^wrk_[0-9A-HJKMNP-TV-Z]{26}$"
_INVITATION_ID = r"^inv_[0-9A-HJKMNP-TV-Z]{26}$"
_SUBJECT_ID = r"^sub_[0-9A-HJKMNP-TV-Z]{26}$"
_DELIVERY_ROUTE_ID = r"^[a-z][a-z0-9_]{0,63}$"
_AUDIT_ID = r"^audit_[0-9]{8,20}$"
_MAX_INVITATION_LIFETIME = timedelta(days=30)


class DecisionOSStoreError(RuntimeError):
    """A fixed safe repository failure."""


class OrganizationUnavailable(DecisionOSStoreError):
    def __init__(self) -> None:
        super().__init__("organization_unavailable")


class WorkspaceUnavailable(DecisionOSStoreError):
    def __init__(self) -> None:
        super().__init__("workspace_unavailable")


class InvitationUnavailable(DecisionOSStoreError):
    def __init__(self) -> None:
        super().__init__("invitation_unavailable")


class MembershipUnavailable(DecisionOSStoreError):
    def __init__(self) -> None:
        super().__init__("membership_unavailable")


class DecisionOSAuthorizationDenied(DecisionOSStoreError):
    def __init__(self) -> None:
        super().__init__("authorization_denied")


class LastOwnerRequired(DecisionOSStoreError):
    def __init__(self) -> None:
        super().__init__("last_owner_required")


class DecisionOSPermission(StrEnum):
    MANAGE_ORGANIZATION = "manage_organization"
    MANAGE_MEMBERS = "manage_members"
    CREATE_WORKSPACE = "create_workspace"
    READ_WORKSPACE = "read_workspace"
    CONTRIBUTE = "contribute"
    APPROVE = "approve"


_ROLE_PERMISSIONS = {
    DecisionOSRole.OWNER: frozenset(DecisionOSPermission),
    DecisionOSRole.ADMIN: frozenset(
        {
            DecisionOSPermission.MANAGE_ORGANIZATION,
            DecisionOSPermission.MANAGE_MEMBERS,
            DecisionOSPermission.CREATE_WORKSPACE,
            DecisionOSPermission.READ_WORKSPACE,
            DecisionOSPermission.CONTRIBUTE,
            DecisionOSPermission.APPROVE,
        }
    ),
    DecisionOSRole.DECISION_OWNER: frozenset(
        {
            DecisionOSPermission.CREATE_WORKSPACE,
            DecisionOSPermission.READ_WORKSPACE,
            DecisionOSPermission.CONTRIBUTE,
            DecisionOSPermission.APPROVE,
        }
    ),
    DecisionOSRole.CONTRIBUTOR: frozenset(
        {DecisionOSPermission.READ_WORKSPACE, DecisionOSPermission.CONTRIBUTE}
    ),
    DecisionOSRole.APPROVER: frozenset(
        {DecisionOSPermission.READ_WORKSPACE, DecisionOSPermission.APPROVE}
    ),
    DecisionOSRole.VIEWER: frozenset({DecisionOSPermission.READ_WORKSPACE}),
}


def require_permission(context: DecisionOSContext, permission: DecisionOSPermission) -> None:
    if permission not in _ROLE_PERMISSIONS[context.membership.role]:
        raise DecisionOSAuthorizationDenied()


class _StoreModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class InvitationGrant(_StoreModel):
    invitation_id: str = Field(pattern=_INVITATION_ID)
    organization_id: str
    role: DecisionOSRole = Field(strict=False)
    expires_at: datetime
    token: SecretStr

    @model_validator(mode="after")
    def expiry_is_aware(self) -> Self:
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("invitation expiry must be timezone-aware")
        return self


class SubjectInvitationDeliveryState(StrEnum):
    NOT_DELIVERED = "not_delivered"
    DELIVERY_PENDING = "delivery_pending"
    DELIVERED = "delivered"
    DELIVERY_FAILED = "delivery_failed"


class SubjectInvitationGrant(_StoreModel):
    invitation_id: str = Field(pattern=_INVITATION_ID)
    organization_id: str
    subject_id: str = Field(pattern=_SUBJECT_ID)
    role: DecisionOSRole = Field(strict=False)
    expires_at: datetime
    delivery_status: SubjectInvitationDeliveryState = Field(strict=False)
    delivery_route_id: str | None = Field(default=None, pattern=_DELIVERY_ROUTE_ID)
    retry_sequence: int = Field(ge=0, le=100)
    token: SecretStr | None = None

    @model_validator(mode="after")
    def is_a_bounded_subject_grant(self) -> Self:
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("invitation expiry must be timezone-aware")
        if (self.delivery_route_id is None) != (
            self.delivery_status is SubjectInvitationDeliveryState.NOT_DELIVERED
        ):
            raise ValueError("invitation delivery route is inconsistent")
        return self


class DecisionOSAuditEvent(_StoreModel):
    audit_id: str = Field(pattern=_AUDIT_ID)
    organization_id: str
    event_name: Literal[
        "organization_created",
        "membership_activated",
        "invitation_created",
        "invitation_accepted",
        "workspace_created",
        "member_role_changed",
        "member_suspended",
    ]
    actor_uid: str
    target_uid: str | None = None
    occurred_at: datetime


class DecisionOSIdentifierFactory(Protocol):
    def organization_id(self) -> str:
        raise NotImplementedError

    def workspace_id(self) -> str:
        raise NotImplementedError

    def invitation_id(self) -> str:
        raise NotImplementedError

    def invitation_token(self) -> str:
        raise NotImplementedError


def _random_ulid() -> str:
    value = secrets.randbits(128)
    encoded = "".join(
        _ULID_ALPHABET[(value >> (5 * index)) & 31]
        for index in range(25, -1, -1)
    )
    return encoded


class SecureDecisionOSIdentifiers:
    def organization_id(self) -> str:
        return f"org_{_random_ulid()}"

    def workspace_id(self) -> str:
        return f"wrk_{_random_ulid()}"

    def invitation_id(self) -> str:
        return f"inv_{_random_ulid()}"

    def invitation_token(self) -> str:
        return secrets.token_urlsafe(32)


@dataclass(frozen=True, slots=True)
class _InMemoryReferenceReplacement:
    target: object
    attribute: str
    prior: object
    replacement: object


@dataclass(frozen=True, slots=True)
class _InMemoryPreparedMutation:
    replacements: tuple[_InMemoryReferenceReplacement, ...]


def _publish_in_memory_replacements(
    prepared: _InMemoryPreparedMutation,
) -> None:
    """Publish prepared references with an exact unchecked rollback guard."""

    replacements = prepared.replacements
    if any(
        getattr(item.target, item.attribute) is not item.prior
        for item in replacements
    ):
        raise MembershipUnavailable()
    try:
        for item in replacements:
            setattr(item.target, item.attribute, item.replacement)
    except Exception:
        # Injected setters may reject publication, so compensation must bypass them.
        for item in replacements:
            object.__setattr__(item.target, item.attribute, item.prior)
        raise


class DecisionOSRepository(Protocol):
    def load_context(
        self,
        principal: DecisionOSPrincipal,
        organization_id: str,
    ) -> DecisionOSContext:
        raise NotImplementedError

    def create_workspace(
        self,
        context: DecisionOSContext,
        *,
        name: str,
        playbook: WorkspacePlaybook,
    ) -> DecisionWorkspace:
        raise NotImplementedError

    def authorize_context(
        self,
        context: DecisionOSContext,
        permission: DecisionOSPermission,
    ) -> DecisionOSContext:
        raise NotImplementedError

    def apply_organization_graph_membership_change(
        self,
        context: DecisionOSContext,
        *,
        carried_member_uids: tuple[str, ...],
        removed_member_uids: tuple[str, ...],
        mutation: Callable[[Any], _InMemoryPreparedMutation | None],
    ) -> None:
        raise NotImplementedError

    def create_subject_invitations(
        self,
        context: DecisionOSContext,
        *,
        subject_ids: tuple[str, ...],
        role: DecisionOSRole,
        expires_in: timedelta,
        delivery_route_id: str | None,
        mutation: Callable[[Any, DecisionOSContext, tuple[SubjectInvitationGrant, ...]], Any],
    ) -> tuple[SubjectInvitationGrant, ...]:
        raise NotImplementedError

    def record_subject_invitation_delivery(
        self,
        context: DecisionOSContext,
        grant: SubjectInvitationGrant,
        *,
        delivered: bool,
    ) -> SubjectInvitationGrant:
        raise NotImplementedError

    def accept_subject_invitation(
        self,
        principal: DecisionOSPrincipal,
        token: str,
        *,
        mutation: Callable[[Any, DecisionOSContext, str], Any],
    ) -> tuple[OrganizationMembership, Any]:
        raise NotImplementedError


@dataclass
class _InvitationRecord:
    invitation_id: str
    organization_id: str
    role: DecisionOSRole
    token_digest: str
    expires_at: datetime
    status: str = "active"


@dataclass(frozen=True, slots=True)
class _SubjectInvitationRecord:
    invitation_id: str
    organization_id: str
    subject_id: str
    role: DecisionOSRole
    token_digest: str
    expires_at: datetime
    delivery_status: SubjectInvitationDeliveryState
    delivery_route_id: str | None
    retry_sequence: int
    retry_of_invitation_id: str | None = None
    status: Literal["active", "accepted", "revoked"] = "active"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return an aware datetime")
    return value.astimezone(UTC)


def _display_name(value: str) -> str:
    if type(value) is not str:
        raise TypeError("display name must be a string")
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("display name cannot be empty")
    return normalized


def _token_digest(token: str) -> str:
    if (
        type(token) is not str
        or not 16 <= len(token) <= 512
        or not token.isascii()
        or any(character.isspace() or ord(character) < 33 for character in token)
    ):
        raise InvitationUnavailable()
    return hashlib.sha256(token.encode()).hexdigest()


def _subject_invitation_inputs(
    subject_ids: tuple[str, ...],
    role: DecisionOSRole,
    expires_in: timedelta,
    delivery_route_id: str | None,
) -> None:
    if (
        type(subject_ids) is not tuple
        or not 1 <= len(subject_ids) <= 100
        or any(
            type(subject_id) is not str
            or re.fullmatch(_SUBJECT_ID, subject_id) is None
            for subject_id in subject_ids
        )
        or len(set(subject_ids)) != len(subject_ids)
    ):
        raise InvitationUnavailable()
    if type(role) is not DecisionOSRole:
        raise InvitationUnavailable()
    if role in {DecisionOSRole.OWNER, DecisionOSRole.ADMIN}:
        raise DecisionOSAuthorizationDenied()
    if (
        type(expires_in) is not timedelta
        or not timedelta(seconds=60) <= expires_in <= _MAX_INVITATION_LIFETIME
        or expires_in.total_seconds() != int(expires_in.total_seconds())
    ):
        raise InvitationUnavailable()
    if delivery_route_id is not None and (
        type(delivery_route_id) is not str
        or re.fullmatch(_DELIVERY_ROUTE_ID, delivery_route_id) is None
    ):
        raise InvitationUnavailable()


def _subject_grant(
    record: _SubjectInvitationRecord,
    *,
    token: str | None,
) -> SubjectInvitationGrant:
    return SubjectInvitationGrant(
        invitation_id=record.invitation_id,
        organization_id=record.organization_id,
        subject_id=record.subject_id,
        role=record.role,
        expires_at=record.expires_at,
        delivery_status=record.delivery_status,
        delivery_route_id=record.delivery_route_id,
        retry_sequence=record.retry_sequence,
        token=None if token is None else SecretStr(token),
    )


def _verified_principal(principal: object) -> DecisionOSPrincipal:
    if type(principal) is not DecisionOSPrincipal or principal.email_verified is not True:
        raise InvitationUnavailable()
    if (
        type(principal.uid) is not str
        or type(principal.provider_ids) is not tuple
        or any(type(provider_id) is not str for provider_id in principal.provider_ids)
    ):
        raise InvitationUnavailable()
    return principal


class InMemoryDecisionOSRepository:
    """Locked semantic reference used by tests and local development."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        identifiers: DecisionOSIdentifierFactory | None = None,
    ) -> None:
        self._clock = (lambda: datetime.now(UTC)) if clock is None else clock
        self._identifiers = SecureDecisionOSIdentifiers() if identifiers is None else identifiers
        self._lock = threading.RLock()
        self._organizations: dict[str, DecisionOrganization] = {}
        self._memberships: dict[tuple[str, str], OrganizationMembership] = {}
        self._workspaces: dict[tuple[str, str], DecisionWorkspace] = {}
        self._invitations: dict[str, _InvitationRecord] = {}
        self._subject_invitations: dict[str, _SubjectInvitationRecord] = {}
        self._active_subject_invitations: dict[tuple[str, str], str] = {}
        self._audit: dict[str, list[DecisionOSAuditEvent]] = {}
        self._audit_sequence = 0

    def create_organization(
        self,
        principal: DecisionOSPrincipal,
        name: str,
    ) -> DecisionOrganization:
        with self._lock:
            organization = DecisionOrganization(
                organization_id=self._identifiers.organization_id(),
                name=_display_name(name),
                created_by_uid=principal.uid,
            )
            if organization.organization_id in self._organizations:
                raise OrganizationUnavailable()
            membership = OrganizationMembership(
                organization_id=organization.organization_id,
                uid=principal.uid,
                role=DecisionOSRole.OWNER,
                status=MembershipStatus.ACTIVE,
            )
            self._organizations[organization.organization_id] = organization
            self._memberships[(organization.organization_id, principal.uid)] = membership
            self._append_audit(
                organization.organization_id,
                "organization_created",
                principal.uid,
            )
            self._append_audit(
                organization.organization_id,
                "membership_activated",
                principal.uid,
                target_uid=principal.uid,
            )
            return organization

    def list_organizations(
        self,
        principal: DecisionOSPrincipal,
    ) -> tuple[DecisionOrganization, ...]:
        with self._lock:
            organization_ids = {
                organization_id
                for (organization_id, uid), membership in self._memberships.items()
                if uid == principal.uid and membership.status is MembershipStatus.ACTIVE
            }
            return tuple(
                self._organizations[item]
                for item in sorted(organization_ids)
                if item in self._organizations
            )

    def load_context(
        self,
        principal: DecisionOSPrincipal,
        organization_id: str,
    ) -> DecisionOSContext:
        with self._lock:
            membership = self._memberships.get((organization_id, principal.uid))
            if (
                organization_id not in self._organizations
                or membership is None
                or membership.status is not MembershipStatus.ACTIVE
            ):
                raise OrganizationUnavailable()
            return DecisionOSContext(principal=principal, membership=membership)

    def create_invitation(
        self,
        context: DecisionOSContext,
        *,
        role: DecisionOSRole,
        expires_in: timedelta,
    ) -> InvitationGrant:
        with self._lock:
            current = self._authorize(context, DecisionOSPermission.MANAGE_MEMBERS)
            if role in {DecisionOSRole.OWNER, DecisionOSRole.ADMIN}:
                raise DecisionOSAuthorizationDenied()
            if not timedelta(0) < expires_in <= _MAX_INVITATION_LIFETIME:
                raise ValueError("invitation lifetime must be between zero and thirty days")
            invitation_id = self._identifiers.invitation_id()
            token = self._identifiers.invitation_token()
            digest = _token_digest(token)
            if invitation_id in self._invitations or any(
                item.token_digest == digest for item in self._invitations.values()
            ):
                raise InvitationUnavailable()
            expires_at = _aware(self._clock()) + expires_in
            self._invitations[invitation_id] = _InvitationRecord(
                invitation_id=invitation_id,
                organization_id=current.organization_id,
                role=role,
                token_digest=digest,
                expires_at=expires_at,
            )
            self._append_audit(
                current.organization_id,
                "invitation_created",
                current.principal.uid,
            )
            return InvitationGrant(
                invitation_id=invitation_id,
                organization_id=current.organization_id,
                role=role,
                expires_at=expires_at,
                token=SecretStr(token),
            )

    def accept_invitation(
        self,
        principal: DecisionOSPrincipal,
        token: str,
    ) -> OrganizationMembership:
        digest = _token_digest(token)
        with self._lock:
            record = next(
                (
                    item
                    for item in self._invitations.values()
                    if hmac_compare(item.token_digest, digest)
                ),
                None,
            )
            now = _aware(self._clock())
            if record is None or record.status != "active" or record.expires_at <= now:
                raise InvitationUnavailable()
            key = (record.organization_id, principal.uid)
            existing = self._memberships.get(key)
            if existing is not None and existing.status is MembershipStatus.ACTIVE:
                raise InvitationUnavailable()
            membership = OrganizationMembership(
                organization_id=record.organization_id,
                uid=principal.uid,
                role=record.role,
                status=MembershipStatus.ACTIVE,
            )
            record.status = "accepted"
            self._memberships[key] = membership
            self._append_audit(
                record.organization_id,
                "invitation_accepted",
                principal.uid,
                target_uid=principal.uid,
            )
            self._append_audit(
                record.organization_id,
                "membership_activated",
                principal.uid,
                target_uid=principal.uid,
            )
            return membership

    def create_subject_invitations(
        self,
        context: DecisionOSContext,
        *,
        subject_ids: tuple[str, ...],
        role: DecisionOSRole,
        expires_in: timedelta,
        delivery_route_id: str | None,
        mutation: Callable[[Any, DecisionOSContext, tuple[SubjectInvitationGrant, ...]], Any],
    ) -> tuple[SubjectInvitationGrant, ...]:
        _subject_invitation_inputs(subject_ids, role, expires_in, delivery_route_id)
        with self._lock:
            current = self._authorize(context, DecisionOSPermission.MANAGE_MEMBERS)
            now = _aware(self._clock())
            replacement_records = dict(self._subject_invitations)
            replacement_active = dict(self._active_subject_invitations)
            grants: list[SubjectInvitationGrant] = []
            created_records: list[_SubjectInvitationRecord] = []
            known_digests = {
                item.token_digest for item in self._invitations.values()
            } | {item.token_digest for item in replacement_records.values()}

            for subject_id in subject_ids:
                key = (current.organization_id, subject_id)
                existing_id = replacement_active.get(key)
                existing = (
                    replacement_records.get(existing_id)
                    if type(existing_id) is str
                    else None
                )
                if existing is not None:
                    if (
                        existing.status != "active"
                        or existing.organization_id != current.organization_id
                        or existing.subject_id != subject_id
                        or existing.role is not role
                        or existing.delivery_route_id != delivery_route_id
                    ):
                        raise InvitationUnavailable()
                    if (
                        existing.expires_at > now
                        and existing.delivery_status
                        in {
                            SubjectInvitationDeliveryState.DELIVERED,
                            SubjectInvitationDeliveryState.NOT_DELIVERED,
                        }
                    ):
                        grants.append(_subject_grant(existing, token=None))
                        continue
                    replacement_records[existing.invitation_id] = replace(
                        existing,
                        status="revoked",
                    )
                    replacement_active.pop(key, None)

                invitation_id = self._identifiers.invitation_id()
                token = self._identifiers.invitation_token()
                digest = _token_digest(token)
                if (
                    invitation_id in self._invitations
                    or invitation_id in replacement_records
                    or digest in known_digests
                ):
                    raise InvitationUnavailable()
                known_digests.add(digest)
                record = _SubjectInvitationRecord(
                    invitation_id=invitation_id,
                    organization_id=current.organization_id,
                    subject_id=subject_id,
                    role=role,
                    token_digest=digest,
                    expires_at=now + expires_in,
                    delivery_status=(
                        SubjectInvitationDeliveryState.NOT_DELIVERED
                        if delivery_route_id is None
                        else SubjectInvitationDeliveryState.DELIVERY_PENDING
                    ),
                    delivery_route_id=delivery_route_id,
                    retry_sequence=0 if existing is None else existing.retry_sequence + 1,
                    retry_of_invitation_id=(
                        None if existing is None else existing.invitation_id
                    ),
                )
                replacement_records[invitation_id] = record
                replacement_active[key] = invitation_id
                created_records.append(record)
                grants.append(_subject_grant(record, token=token))

            organization_mutation = mutation(None, current, tuple(grants))
            if not isinstance(organization_mutation, _InMemoryPreparedMutation):
                raise InvitationUnavailable()
            next_sequence = self._audit_sequence
            audit_events = list(self._audit.get(current.organization_id, ()))
            for _record in created_records:
                next_sequence += 1
                audit_events.append(
                    DecisionOSAuditEvent(
                        audit_id=f"audit_{next_sequence:08d}",
                        organization_id=current.organization_id,
                        event_name="invitation_created",
                        actor_uid=current.principal.uid,
                        occurred_at=now,
                    )
                )
            replacement_audit = {
                organization_id: list(events)
                for organization_id, events in self._audit.items()
            }
            replacement_audit[current.organization_id] = audit_events
            prepared = _InMemoryPreparedMutation(
                replacements=(
                    *organization_mutation.replacements,
                    _InMemoryReferenceReplacement(
                        self,
                        "_subject_invitations",
                        self._subject_invitations,
                        replacement_records,
                    ),
                    _InMemoryReferenceReplacement(
                        self,
                        "_active_subject_invitations",
                        self._active_subject_invitations,
                        replacement_active,
                    ),
                    _InMemoryReferenceReplacement(
                        self,
                        "_audit",
                        self._audit,
                        replacement_audit,
                    ),
                    _InMemoryReferenceReplacement(
                        self,
                        "_audit_sequence",
                        self._audit_sequence,
                        next_sequence,
                    ),
                )
            )
            _publish_in_memory_replacements(prepared)
            return tuple(grants)

    def record_subject_invitation_delivery(
        self,
        context: DecisionOSContext,
        grant: SubjectInvitationGrant,
        *,
        delivered: bool,
    ) -> SubjectInvitationGrant:
        with self._lock:
            current = self._authorize(context, DecisionOSPermission.MANAGE_MEMBERS)
            if type(grant) is not SubjectInvitationGrant or type(delivered) is not bool:
                raise InvitationUnavailable()
            token = grant.token.get_secret_value() if grant.token is not None else None
            digest = _token_digest(token) if token is not None else None
            record = self._subject_invitations.get(grant.invitation_id)
            if (
                record is None
                or record.status != "active"
                or record.organization_id != current.organization_id
                or record.subject_id != grant.subject_id
                or record.role is not grant.role
                or record.delivery_route_id != grant.delivery_route_id
                or record.retry_sequence != grant.retry_sequence
                or record.delivery_status
                is not SubjectInvitationDeliveryState.DELIVERY_PENDING
                or digest is None
                or not hmac_compare(record.token_digest, digest)
            ):
                raise InvitationUnavailable()
            updated = replace(
                record,
                delivery_status=(
                    SubjectInvitationDeliveryState.DELIVERED
                    if delivered
                    else SubjectInvitationDeliveryState.DELIVERY_FAILED
                ),
            )
            replacement = dict(self._subject_invitations)
            replacement[record.invitation_id] = updated
            self._subject_invitations = replacement
            return _subject_grant(updated, token=None)

    def accept_subject_invitation(
        self,
        principal: DecisionOSPrincipal,
        token: str,
        *,
        mutation: Callable[[Any, DecisionOSContext, str], Any],
    ) -> tuple[OrganizationMembership, Any]:
        verified = _verified_principal(principal)
        digest = _token_digest(token)
        with self._lock:
            matches = [
                item
                for item in self._subject_invitations.values()
                if hmac_compare(item.token_digest, digest)
            ]
            now = _aware(self._clock())
            if len(matches) != 1:
                raise InvitationUnavailable()
            record = matches[0]
            if (
                record.status != "active"
                or record.delivery_status is not SubjectInvitationDeliveryState.DELIVERED
                or record.expires_at <= now
                or record.role in {DecisionOSRole.OWNER, DecisionOSRole.ADMIN}
                or self._active_subject_invitations.get(
                    (record.organization_id, record.subject_id)
                )
                != record.invitation_id
                or (record.organization_id, verified.uid) in self._memberships
            ):
                raise InvitationUnavailable()
            membership = OrganizationMembership(
                organization_id=record.organization_id,
                uid=verified.uid,
                role=record.role,
                status=MembershipStatus.ACTIVE,
            )
            current = DecisionOSContext(principal=verified, membership=membership)
            organization_mutation, result = mutation(
                None,
                current,
                record.subject_id,
            )
            if not isinstance(organization_mutation, _InMemoryPreparedMutation):
                raise InvitationUnavailable()

            replacement_records = dict(self._subject_invitations)
            replacement_records[record.invitation_id] = replace(
                record,
                status="accepted",
            )
            replacement_active = dict(self._active_subject_invitations)
            replacement_active.pop((record.organization_id, record.subject_id), None)
            replacement_memberships = dict(self._memberships)
            replacement_memberships[(record.organization_id, verified.uid)] = membership
            replacement_audit = {
                organization_id: list(events)
                for organization_id, events in self._audit.items()
            }
            next_sequence = self._audit_sequence
            events = list(replacement_audit.get(record.organization_id, ()))
            for event_name in ("invitation_accepted", "membership_activated"):
                next_sequence += 1
                events.append(
                    DecisionOSAuditEvent(
                        audit_id=f"audit_{next_sequence:08d}",
                        organization_id=record.organization_id,
                        event_name=event_name,
                        actor_uid=verified.uid,
                        target_uid=verified.uid,
                        occurred_at=now,
                    )
                )
            replacement_audit[record.organization_id] = events
            prepared = _InMemoryPreparedMutation(
                replacements=(
                    *organization_mutation.replacements,
                    _InMemoryReferenceReplacement(
                        self,
                        "_subject_invitations",
                        self._subject_invitations,
                        replacement_records,
                    ),
                    _InMemoryReferenceReplacement(
                        self,
                        "_active_subject_invitations",
                        self._active_subject_invitations,
                        replacement_active,
                    ),
                    _InMemoryReferenceReplacement(
                        self,
                        "_memberships",
                        self._memberships,
                        replacement_memberships,
                    ),
                    _InMemoryReferenceReplacement(
                        self,
                        "_audit",
                        self._audit,
                        replacement_audit,
                    ),
                    _InMemoryReferenceReplacement(
                        self,
                        "_audit_sequence",
                        self._audit_sequence,
                        next_sequence,
                    ),
                )
            )
            _publish_in_memory_replacements(prepared)
            return membership, result

    def create_workspace(
        self,
        context: DecisionOSContext,
        *,
        name: str,
        playbook: WorkspacePlaybook,
    ) -> DecisionWorkspace:
        with self._lock:
            current = self._authorize(context, DecisionOSPermission.CREATE_WORKSPACE)
            workspace = DecisionWorkspace(
                workspace_id=self._identifiers.workspace_id(),
                organization_id=current.organization_id,
                name=_display_name(name),
                playbook=playbook,
                created_by_uid=current.principal.uid,
            )
            key = (current.organization_id, workspace.workspace_id)
            if key in self._workspaces:
                raise WorkspaceUnavailable()
            self._workspaces[key] = workspace
            self._append_audit(
                current.organization_id,
                "workspace_created",
                current.principal.uid,
            )
            return workspace

    def authorize_context(
        self,
        context: DecisionOSContext,
        permission: DecisionOSPermission,
    ) -> DecisionOSContext:
        """Reload membership before granting another repository authority."""

        with self._lock:
            return self._authorize(context, permission)

    def apply_organization_graph_membership_change(
        self,
        context: DecisionOSContext,
        *,
        carried_member_uids: tuple[str, ...],
        removed_member_uids: tuple[str, ...],
        mutation: Callable[[Any], _InMemoryPreparedMutation | None],
    ) -> None:
        """Validate carried bindings and suspend removed members under one lock."""

        with self._lock:
            current = self._authorize(context, DecisionOSPermission.MANAGE_MEMBERS)
            carried = tuple(dict.fromkeys(carried_member_uids))
            removed = tuple(dict.fromkeys(removed_member_uids))
            if set(carried) & set(removed):
                raise MembershipUnavailable()
            for uid in carried:
                self._member_for_management(current, uid)
            active_removed = tuple(
                member
                for uid in removed
                if (member := self._memberships.get((current.organization_id, uid))) is not None
                and member.status is MembershipStatus.ACTIVE
            )
            if current.membership.role is DecisionOSRole.ADMIN and any(
                member.role is DecisionOSRole.OWNER for member in active_removed
            ):
                raise DecisionOSAuthorizationDenied()
            removed_uids = {member.uid for member in active_removed}
            if any(member.role is DecisionOSRole.OWNER for member in active_removed) and not any(
                organization_id == current.organization_id
                and uid not in removed_uids
                and membership.status is MembershipStatus.ACTIVE
                and membership.role is DecisionOSRole.OWNER
                for (organization_id, uid), membership in self._memberships.items()
            ):
                raise LastOwnerRequired()
            next_audit_sequence = self._audit_sequence
            audit_events = []
            updated_memberships = []
            membership_snapshot = {}
            for member in active_removed:
                key = (current.organization_id, member.uid)
                membership_snapshot[key] = member
                updated_memberships.append(
                    (key, member.model_copy(update={"status": MembershipStatus.SUSPENDED}))
                )
                next_audit_sequence += 1
                audit_events.append(
                    DecisionOSAuditEvent(
                        audit_id=f"audit_{next_audit_sequence:08d}",
                        organization_id=current.organization_id,
                        event_name="member_suspended",
                        actor_uid=current.principal.uid,
                        target_uid=member.uid,
                        occurred_at=_aware(self._clock()),
                    )
                )
            if any(
                self._memberships.get(key) != value for key, value in membership_snapshot.items()
            ):
                raise MembershipUnavailable()
            replacement_memberships = dict(self._memberships)
            for key, updated in updated_memberships:
                replacement_memberships[key] = updated
            replacement_audit = {
                organization_id: list(events)
                for organization_id, events in self._audit.items()
            }
            replacement_audit[current.organization_id] = [
                *replacement_audit.get(current.organization_id, ()),
                *audit_events,
            ]
            prior_memberships = self._memberships
            prior_audit = self._audit
            prior_audit_sequence = self._audit_sequence
            organization_mutation = mutation(None)
            if not isinstance(organization_mutation, _InMemoryPreparedMutation):
                raise MembershipUnavailable()
            prepared = _InMemoryPreparedMutation(
                replacements=(
                    *organization_mutation.replacements,
                    _InMemoryReferenceReplacement(
                        self,
                        "_memberships",
                        prior_memberships,
                        replacement_memberships,
                    ),
                    _InMemoryReferenceReplacement(
                        self,
                        "_audit",
                        prior_audit,
                        replacement_audit,
                    ),
                    _InMemoryReferenceReplacement(
                        self,
                        "_audit_sequence",
                        prior_audit_sequence,
                        next_audit_sequence,
                    ),
                )
            )
            _publish_in_memory_replacements(prepared)

    def load_workspace(
        self,
        context: DecisionOSContext,
        workspace_id: str,
    ) -> DecisionWorkspace:
        with self._lock:
            current = self._authorize(context, DecisionOSPermission.READ_WORKSPACE)
            workspace = self._workspaces.get((current.organization_id, workspace_id))
            if workspace is None:
                raise WorkspaceUnavailable()
            return workspace

    def list_workspaces(
        self,
        context: DecisionOSContext,
    ) -> tuple[DecisionWorkspace, ...]:
        with self._lock:
            current = self._authorize(context, DecisionOSPermission.READ_WORKSPACE)
            return tuple(
                workspace
                for (organization_id, _workspace_id), workspace in sorted(
                    self._workspaces.items()
                )
                if organization_id == current.organization_id
            )

    def update_member_role(
        self,
        context: DecisionOSContext,
        uid: str,
        role: DecisionOSRole,
    ) -> OrganizationMembership:
        with self._lock:
            current = self._authorize(context, DecisionOSPermission.MANAGE_MEMBERS)
            if current.membership.role is DecisionOSRole.ADMIN and role in {
                DecisionOSRole.OWNER,
                DecisionOSRole.ADMIN,
            }:
                raise DecisionOSAuthorizationDenied()
            member = self._member_for_management(current, uid)
            if current.membership.role is DecisionOSRole.ADMIN and member.role is DecisionOSRole.OWNER:
                raise DecisionOSAuthorizationDenied()
            if member.role is DecisionOSRole.OWNER and role is not DecisionOSRole.OWNER:
                self._require_another_owner(current.organization_id, uid)
            updated = member.model_copy(update={"role": role})
            self._memberships[(current.organization_id, uid)] = updated
            self._append_audit(
                current.organization_id,
                "member_role_changed",
                current.principal.uid,
                target_uid=uid,
            )
            return updated

    def suspend_member(
        self,
        context: DecisionOSContext,
        uid: str,
    ) -> OrganizationMembership:
        return self._suspend_member(context, uid)

    def remove_member(
        self,
        context: DecisionOSContext,
        uid: str,
    ) -> OrganizationMembership:
        return self._suspend_member(context, uid)

    def list_audit(
        self,
        context: DecisionOSContext,
    ) -> tuple[DecisionOSAuditEvent, ...]:
        with self._lock:
            current = self._authorize(context, DecisionOSPermission.READ_WORKSPACE)
            return tuple(self._audit.get(current.organization_id, ()))

    def _authorize(
        self,
        context: DecisionOSContext,
        permission: DecisionOSPermission,
    ) -> DecisionOSContext:
        membership = self._memberships.get(
            (context.membership.organization_id, context.principal.uid)
        )
        if membership is None or membership.status is not MembershipStatus.ACTIVE:
            raise DecisionOSAuthorizationDenied()
        current = DecisionOSContext(principal=context.principal, membership=membership)
        require_permission(current, permission)
        return current

    def _member_for_management(
        self,
        context: DecisionOSContext,
        uid: str,
    ) -> OrganizationMembership:
        member = self._memberships.get((context.organization_id, uid))
        if member is None or member.status is not MembershipStatus.ACTIVE:
            raise MembershipUnavailable()
        return member

    def _require_another_owner(self, organization_id: str, excluded_uid: str) -> None:
        if not any(
            candidate_org == organization_id
            and candidate_uid != excluded_uid
            and membership.status is MembershipStatus.ACTIVE
            and membership.role is DecisionOSRole.OWNER
            for (candidate_org, candidate_uid), membership in self._memberships.items()
        ):
            raise LastOwnerRequired()

    def _suspend_member(
        self,
        context: DecisionOSContext,
        uid: str,
    ) -> OrganizationMembership:
        with self._lock:
            current = self._authorize(context, DecisionOSPermission.MANAGE_MEMBERS)
            member = self._member_for_management(current, uid)
            if current.membership.role is DecisionOSRole.ADMIN and member.role is DecisionOSRole.OWNER:
                raise DecisionOSAuthorizationDenied()
            if member.role is DecisionOSRole.OWNER:
                self._require_another_owner(current.organization_id, uid)
            updated = member.model_copy(update={"status": MembershipStatus.SUSPENDED})
            self._memberships[(current.organization_id, uid)] = updated
            self._append_audit(
                current.organization_id,
                "member_suspended",
                current.principal.uid,
                target_uid=uid,
            )
            return updated

    def _append_audit(
        self,
        organization_id: str,
        event_name: str,
        actor_uid: str,
        *,
        target_uid: str | None = None,
    ) -> None:
        self._audit_sequence += 1
        event = DecisionOSAuditEvent(
            audit_id=f"audit_{self._audit_sequence:08d}",
            organization_id=organization_id,
            event_name=event_name,
            actor_uid=actor_uid,
            target_uid=target_uid,
            occurred_at=_aware(self._clock()),
        )
        self._audit.setdefault(organization_id, []).append(event)


def _collection_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", value):
        raise ValueError("DecisionOS collection name is invalid")
    return value


def _snapshot_model(model_type, snapshot):
    if not snapshot.exists:
        return None
    return model_type.model_validate(snapshot.to_dict())


_SUBJECT_INVITATION_FIELDS = frozenset(
    {
        "schema_version",
        "invitation_kind",
        "invitation_id",
        "organization_id",
        "subject_id",
        "role",
        "expires_at",
        "delivery_status",
        "delivery_route_id",
        "retry_sequence",
        "retry_of_invitation_id",
        "status",
    }
)


def _firestore_subject_invitation(
    value: object,
    *,
    token_digest: str,
) -> _SubjectInvitationRecord:
    if type(value) is not dict or set(value) != _SUBJECT_INVITATION_FIELDS:
        raise InvitationUnavailable()
    failed = False
    record = None
    try:
        role = DecisionOSRole(value["role"])
        delivery_status = SubjectInvitationDeliveryState(value["delivery_status"])
        record = _SubjectInvitationRecord(
            invitation_id=value["invitation_id"],
            organization_id=value["organization_id"],
            subject_id=value["subject_id"],
            role=role,
            token_digest=token_digest,
            expires_at=_aware(value["expires_at"]),
            delivery_status=delivery_status,
            delivery_route_id=value["delivery_route_id"],
            retry_sequence=value["retry_sequence"],
            retry_of_invitation_id=value["retry_of_invitation_id"],
            status=value["status"],
        )
    except Exception:  # noqa: BLE001 - stored corruption details are sealed
        failed = True
    if failed or record is None:
        raise InvitationUnavailable() from None
    if (
        value["schema_version"] != 1
        or value["invitation_kind"] != "organization_subject"
        or type(record.invitation_id) is not str
        or re.fullmatch(_INVITATION_ID, record.invitation_id) is None
        or type(record.organization_id) is not str
        or re.fullmatch(_ORGANIZATION_ID, record.organization_id) is None
        or type(record.subject_id) is not str
        or re.fullmatch(_SUBJECT_ID, record.subject_id) is None
        or type(record.retry_sequence) is not int
        or not 0 <= record.retry_sequence <= 100
        or record.status not in {"active", "accepted", "revoked"}
        or (record.delivery_route_id is None)
        != (record.delivery_status is SubjectInvitationDeliveryState.NOT_DELIVERED)
    ):
        raise InvitationUnavailable()
    return record


def _subject_invitation_payload(record: _SubjectInvitationRecord) -> dict[str, object]:
    return {
        "schema_version": 1,
        "invitation_kind": "organization_subject",
        "invitation_id": record.invitation_id,
        "organization_id": record.organization_id,
        "subject_id": record.subject_id,
        "role": record.role.value,
        "expires_at": record.expires_at,
        "delivery_status": record.delivery_status.value,
        "delivery_route_id": record.delivery_route_id,
        "retry_sequence": record.retry_sequence,
        "retry_of_invitation_id": record.retry_of_invitation_id,
        "status": record.status,
    }


class FirestoreDecisionOSRepository:
    """Transactional Firestore repository with the in-memory authority semantics."""

    def __init__(
        self,
        client: Any,
        *,
        clock: Callable[[], datetime] | None = None,
        identifiers: DecisionOSIdentifierFactory | None = None,
        organization_collection: str = "organizations",
        invitation_index_collection: str = "humanwire_invitation_tokens",
    ) -> None:
        self._client = client
        self._clock = (lambda: datetime.now(UTC)) if clock is None else clock
        self._identifiers = SecureDecisionOSIdentifiers() if identifiers is None else identifiers
        self._organization_collection = _collection_name(organization_collection)
        self._invitation_index_collection = _collection_name(invitation_index_collection)

    def _organization_ref(self, organization_id: str):
        if re.fullmatch(_ORGANIZATION_ID, organization_id) is None:
            raise OrganizationUnavailable()
        return self._client.collection(self._organization_collection).document(organization_id)

    def _member_ref(self, organization_id: str, uid: str):
        return self._organization_ref(organization_id).collection("members").document(uid)

    def _workspace_ref(self, organization_id: str, workspace_id: str):
        if re.fullmatch(_WORKSPACE_ID, workspace_id) is None:
            raise WorkspaceUnavailable()
        return self._organization_ref(organization_id).collection("workspaces").document(
            workspace_id
        )

    def _invitation_ref(self, organization_id: str, invitation_id: str):
        if re.fullmatch(_INVITATION_ID, invitation_id) is None:
            raise InvitationUnavailable()
        return self._organization_ref(organization_id).collection("invitations").document(
            invitation_id
        )

    def _invitation_index_ref(self, token_digest: str):
        return self._client.collection(self._invitation_index_collection).document(token_digest)

    def _subject_invitation_state_ref(self, organization_id: str, subject_id: str):
        if re.fullmatch(_SUBJECT_ID, subject_id) is None:
            raise InvitationUnavailable()
        return (
            self._organization_ref(organization_id)
            .collection("subject_invitation_state")
            .document(subject_id)
        )

    def create_organization(
        self,
        principal: DecisionOSPrincipal,
        name: str,
    ) -> DecisionOrganization:
        from google.cloud import firestore

        organization = DecisionOrganization(
            organization_id=self._identifiers.organization_id(),
            name=_display_name(name),
            created_by_uid=principal.uid,
        )
        membership = OrganizationMembership(
            organization_id=organization.organization_id,
            uid=principal.uid,
            role=DecisionOSRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )
        organization_ref = self._organization_ref(organization.organization_id)
        member_ref = self._member_ref(organization.organization_id, principal.uid)
        at = _aware(self._clock())

        @firestore.transactional
        def create(transaction):
            if organization_ref.get(transaction=transaction).exists:
                raise OrganizationUnavailable()
            transaction.create(organization_ref, organization.model_dump(mode="python"))
            transaction.create(member_ref, membership.model_dump(mode="python"))
            self._append_audit_transaction(
                transaction,
                organization.organization_id,
                "organization_created",
                principal.uid,
                occurred_at=at,
            )
            self._append_audit_transaction(
                transaction,
                organization.organization_id,
                "membership_activated",
                principal.uid,
                target_uid=principal.uid,
                occurred_at=at,
            )

        create(self._client.transaction())
        return organization

    def list_organizations(
        self,
        principal: DecisionOSPrincipal,
    ) -> tuple[DecisionOrganization, ...]:
        from google.cloud.firestore_v1.base_query import FieldFilter

        rows = self._client.collection_group("members").where(
            filter=FieldFilter("uid", "==", principal.uid)
        ).stream()
        organizations: dict[str, DecisionOrganization] = {}
        for row in rows:
            membership = OrganizationMembership.model_validate(row.to_dict())
            if membership.status is not MembershipStatus.ACTIVE:
                continue
            organization = _snapshot_model(
                DecisionOrganization,
                self._organization_ref(membership.organization_id).get(),
            )
            if organization is not None:
                organizations[organization.organization_id] = organization
        return tuple(organizations[key] for key in sorted(organizations))

    def load_context(
        self,
        principal: DecisionOSPrincipal,
        organization_id: str,
    ) -> DecisionOSContext:
        organization = self._organization_ref(organization_id).get()
        membership = _snapshot_model(
            OrganizationMembership,
            self._member_ref(organization_id, principal.uid).get(),
        )
        if (
            not organization.exists
            or membership is None
            or membership.status is not MembershipStatus.ACTIVE
        ):
            raise OrganizationUnavailable()
        return DecisionOSContext(principal=principal, membership=membership)

    def create_invitation(
        self,
        context: DecisionOSContext,
        *,
        role: DecisionOSRole,
        expires_in: timedelta,
    ) -> InvitationGrant:
        from google.cloud import firestore

        if role in {DecisionOSRole.OWNER, DecisionOSRole.ADMIN}:
            raise DecisionOSAuthorizationDenied()
        if not timedelta(0) < expires_in <= _MAX_INVITATION_LIFETIME:
            raise ValueError("invitation lifetime must be between zero and thirty days")
        invitation_id = self._identifiers.invitation_id()
        token = self._identifiers.invitation_token()
        digest = _token_digest(token)
        expires_at = _aware(self._clock()) + expires_in
        invitation_ref = self._invitation_ref(context.organization_id, invitation_id)
        index_ref = self._invitation_index_ref(digest)

        @firestore.transactional
        def create(transaction):
            current = self._authorize_transaction(
                transaction,
                context,
                DecisionOSPermission.MANAGE_MEMBERS,
            )
            if invitation_ref.get(transaction=transaction).exists or index_ref.get(
                transaction=transaction
            ).exists:
                raise InvitationUnavailable()
            transaction.create(
                invitation_ref,
                {
                    "invitation_id": invitation_id,
                    "organization_id": current.organization_id,
                    "role": role.value,
                    "expires_at": expires_at,
                    "status": "active",
                },
            )
            transaction.create(
                index_ref,
                {
                    "invitation_id": invitation_id,
                    "organization_id": current.organization_id,
                    "status": "active",
                },
            )
            self._append_audit_transaction(
                transaction,
                current.organization_id,
                "invitation_created",
                current.principal.uid,
                occurred_at=_aware(self._clock()),
            )

        create(self._client.transaction())
        return InvitationGrant(
            invitation_id=invitation_id,
            organization_id=context.organization_id,
            role=role,
            expires_at=expires_at,
            token=SecretStr(token),
        )

    def accept_invitation(
        self,
        principal: DecisionOSPrincipal,
        token: str,
    ) -> OrganizationMembership:
        from google.cloud import firestore

        digest = _token_digest(token)
        index_ref = self._invitation_index_ref(digest)
        now = _aware(self._clock())

        @firestore.transactional
        def accept(transaction):
            index_row = index_ref.get(transaction=transaction)
            if not index_row.exists:
                raise InvitationUnavailable()
            index = index_row.to_dict()
            if (
                index.get("status") != "active"
                or index.get("invitation_kind") is not None
            ):
                raise InvitationUnavailable()
            organization_id = index.get("organization_id")
            invitation_id = index.get("invitation_id")
            if type(organization_id) is not str or type(invitation_id) is not str:
                raise InvitationUnavailable()
            invitation_ref = self._invitation_ref(organization_id, invitation_id)
            invitation_row = invitation_ref.get(transaction=transaction)
            if not invitation_row.exists:
                raise InvitationUnavailable()
            invitation = invitation_row.to_dict()
            expires_at = invitation.get("expires_at")
            if (
                invitation.get("status") != "active"
                or invitation.get("invitation_kind") is not None
                or not isinstance(expires_at, datetime)
                or _aware(expires_at) <= now
            ):
                raise InvitationUnavailable()
            member_ref = self._member_ref(organization_id, principal.uid)
            existing = member_ref.get(transaction=transaction)
            if existing.exists:
                saved = OrganizationMembership.model_validate(existing.to_dict())
                if saved.status is MembershipStatus.ACTIVE:
                    raise InvitationUnavailable()
            membership = OrganizationMembership(
                organization_id=organization_id,
                uid=principal.uid,
                role=invitation.get("role"),
                status=MembershipStatus.ACTIVE,
            )
            transaction.set(member_ref, membership.model_dump(mode="python"))
            transaction.update(invitation_ref, {"status": "accepted"})
            transaction.update(index_ref, {"status": "accepted"})
            self._append_audit_transaction(
                transaction,
                organization_id,
                "invitation_accepted",
                principal.uid,
                target_uid=principal.uid,
                occurred_at=now,
            )
            self._append_audit_transaction(
                transaction,
                organization_id,
                "membership_activated",
                principal.uid,
                target_uid=principal.uid,
                occurred_at=now,
            )
            return membership

        return accept(self._client.transaction())

    def create_subject_invitations(
        self,
        context: DecisionOSContext,
        *,
        subject_ids: tuple[str, ...],
        role: DecisionOSRole,
        expires_in: timedelta,
        delivery_route_id: str | None,
        mutation: Callable[[Any, DecisionOSContext, tuple[SubjectInvitationGrant, ...]], Any],
    ) -> tuple[SubjectInvitationGrant, ...]:
        from google.cloud import firestore

        _subject_invitation_inputs(subject_ids, role, expires_in, delivery_route_id)
        candidates = tuple(
            (
                self._identifiers.invitation_id(),
                self._identifiers.invitation_token(),
            )
            for _subject_id in subject_ids
        )
        now = _aware(self._clock())

        @firestore.transactional
        def create(transaction):
            current = self._authorize_transaction(
                transaction,
                context,
                DecisionOSPermission.MANAGE_MEMBERS,
            )
            grants: list[SubjectInvitationGrant] = []
            writes: list[tuple[object, object, object, _SubjectInvitationRecord]] = []
            revocations: list[tuple[object, object, _SubjectInvitationRecord]] = []
            for subject_id, (invitation_id, token) in zip(
                subject_ids,
                candidates,
                strict=True,
            ):
                state_ref = self._subject_invitation_state_ref(
                    current.organization_id,
                    subject_id,
                )
                state_row = state_ref.get(transaction=transaction)
                existing = None
                existing_index_ref = None
                existing_invitation_ref = None
                if state_row.exists:
                    state = state_row.to_dict()
                    expected_state_fields = {
                        "schema_version",
                        "organization_id",
                        "subject_id",
                        "invitation_id",
                        "token_digest",
                        "status",
                    }
                    if type(state) is not dict or set(state) != expected_state_fields:
                        raise InvitationUnavailable()
                    existing_digest = state.get("token_digest")
                    existing_id = state.get("invitation_id")
                    if (
                        state.get("schema_version") != 1
                        or state.get("organization_id") != current.organization_id
                        or state.get("subject_id") != subject_id
                        or type(existing_digest) is not str
                        or re.fullmatch(r"[0-9a-f]{64}", existing_digest) is None
                        or type(existing_id) is not str
                        or re.fullmatch(_INVITATION_ID, existing_id) is None
                        or state.get("status") != "active"
                    ):
                        raise InvitationUnavailable()
                    existing_invitation_ref = self._invitation_ref(
                        current.organization_id,
                        existing_id,
                    )
                    existing_row = existing_invitation_ref.get(transaction=transaction)
                    if not existing_row.exists:
                        raise InvitationUnavailable()
                    existing = _firestore_subject_invitation(
                        existing_row.to_dict(),
                        token_digest=existing_digest,
                    )
                    if (
                        existing.organization_id != current.organization_id
                        or existing.subject_id != subject_id
                        or existing.role is not role
                        or existing.delivery_route_id != delivery_route_id
                        or existing.status != "active"
                    ):
                        raise InvitationUnavailable()
                    if (
                        existing.expires_at > now
                        and existing.delivery_status
                        in {
                            SubjectInvitationDeliveryState.DELIVERED,
                            SubjectInvitationDeliveryState.NOT_DELIVERED,
                        }
                    ):
                        grants.append(_subject_grant(existing, token=None))
                        continue
                    existing_index_ref = self._invitation_index_ref(existing_digest)
                    index_row = existing_index_ref.get(transaction=transaction)
                    if not index_row.exists:
                        raise InvitationUnavailable()
                    revocations.append(
                        (existing_invitation_ref, existing_index_ref, existing)
                    )

                digest = _token_digest(token)
                invitation_ref = self._invitation_ref(current.organization_id, invitation_id)
                index_ref = self._invitation_index_ref(digest)
                if invitation_ref.get(transaction=transaction).exists or index_ref.get(
                    transaction=transaction
                ).exists:
                    raise InvitationUnavailable()
                record = _SubjectInvitationRecord(
                    invitation_id=invitation_id,
                    organization_id=current.organization_id,
                    subject_id=subject_id,
                    role=role,
                    token_digest=digest,
                    expires_at=now + expires_in,
                    delivery_status=(
                        SubjectInvitationDeliveryState.NOT_DELIVERED
                        if delivery_route_id is None
                        else SubjectInvitationDeliveryState.DELIVERY_PENDING
                    ),
                    delivery_route_id=delivery_route_id,
                    retry_sequence=0 if existing is None else existing.retry_sequence + 1,
                    retry_of_invitation_id=(
                        None if existing is None else existing.invitation_id
                    ),
                )
                writes.append((invitation_ref, index_ref, state_ref, record))
                grants.append(_subject_grant(record, token=token))

            mutation(transaction, current, tuple(grants))
            if (4 * len(writes)) + (2 * len(revocations)) > 440:
                raise InvitationUnavailable()
            for invitation_ref, index_ref, _existing in revocations:
                transaction.update(invitation_ref, {"status": "revoked"})
                transaction.update(index_ref, {"status": "revoked"})
            for invitation_ref, index_ref, state_ref, record in writes:
                transaction.create(invitation_ref, _subject_invitation_payload(record))
                transaction.create(
                    index_ref,
                    {
                        "invitation_kind": "organization_subject",
                        "invitation_id": record.invitation_id,
                        "organization_id": record.organization_id,
                        "subject_id": record.subject_id,
                        "status": "active",
                    },
                )
                transaction.set(
                    state_ref,
                    {
                        "schema_version": 1,
                        "organization_id": record.organization_id,
                        "subject_id": record.subject_id,
                        "invitation_id": record.invitation_id,
                        "token_digest": record.token_digest,
                        "status": "active",
                    },
                )
                self._append_audit_transaction(
                    transaction,
                    current.organization_id,
                    "invitation_created",
                    current.principal.uid,
                    occurred_at=now,
                )
            return tuple(grants)

        return create(self._client.transaction())

    def record_subject_invitation_delivery(
        self,
        context: DecisionOSContext,
        grant: SubjectInvitationGrant,
        *,
        delivered: bool,
    ) -> SubjectInvitationGrant:
        from google.cloud import firestore

        if (
            type(grant) is not SubjectInvitationGrant
            or type(delivered) is not bool
            or grant.token is None
        ):
            raise InvitationUnavailable()
        digest = _token_digest(grant.token.get_secret_value())
        invitation_ref = self._invitation_ref(
            context.organization_id,
            grant.invitation_id,
        )
        index_ref = self._invitation_index_ref(digest)

        @firestore.transactional
        def update(transaction):
            current = self._authorize_transaction(
                transaction,
                context,
                DecisionOSPermission.MANAGE_MEMBERS,
            )
            row = invitation_ref.get(transaction=transaction)
            index = index_ref.get(transaction=transaction)
            if not row.exists or not index.exists:
                raise InvitationUnavailable()
            record = _firestore_subject_invitation(
                row.to_dict(),
                token_digest=digest,
            )
            index_value = index.to_dict()
            if (
                record.organization_id != current.organization_id
                or record.invitation_id != grant.invitation_id
                or record.subject_id != grant.subject_id
                or record.role is not grant.role
                or record.delivery_route_id != grant.delivery_route_id
                or record.retry_sequence != grant.retry_sequence
                or record.status != "active"
                or record.delivery_status
                is not SubjectInvitationDeliveryState.DELIVERY_PENDING
                or type(index_value) is not dict
                or index_value.get("invitation_kind") != "organization_subject"
                or index_value.get("invitation_id") != record.invitation_id
                or index_value.get("organization_id") != record.organization_id
                or index_value.get("subject_id") != record.subject_id
                or index_value.get("status") != "active"
            ):
                raise InvitationUnavailable()
            updated = replace(
                record,
                delivery_status=(
                    SubjectInvitationDeliveryState.DELIVERED
                    if delivered
                    else SubjectInvitationDeliveryState.DELIVERY_FAILED
                ),
            )
            transaction.set(invitation_ref, _subject_invitation_payload(updated))
            return _subject_grant(updated, token=None)

        return update(self._client.transaction())

    def accept_subject_invitation(
        self,
        principal: DecisionOSPrincipal,
        token: str,
        *,
        mutation: Callable[[Any, DecisionOSContext, str], Any],
    ) -> tuple[OrganizationMembership, Any]:
        from google.cloud import firestore

        verified = _verified_principal(principal)
        digest = _token_digest(token)
        index_ref = self._invitation_index_ref(digest)
        now = _aware(self._clock())

        @firestore.transactional
        def accept(transaction):
            index_row = index_ref.get(transaction=transaction)
            if not index_row.exists:
                raise InvitationUnavailable()
            index = index_row.to_dict()
            if type(index) is not dict or set(index) != {
                "invitation_kind",
                "invitation_id",
                "organization_id",
                "subject_id",
                "status",
            }:
                raise InvitationUnavailable()
            organization_id = index.get("organization_id")
            invitation_id = index.get("invitation_id")
            subject_id = index.get("subject_id")
            if (
                index.get("invitation_kind") != "organization_subject"
                or index.get("status") != "active"
                or type(organization_id) is not str
                or type(invitation_id) is not str
                or type(subject_id) is not str
            ):
                raise InvitationUnavailable()
            invitation_ref = self._invitation_ref(organization_id, invitation_id)
            invitation_row = invitation_ref.get(transaction=transaction)
            if not invitation_row.exists:
                raise InvitationUnavailable()
            record = _firestore_subject_invitation(
                invitation_row.to_dict(),
                token_digest=digest,
            )
            state_ref = self._subject_invitation_state_ref(
                organization_id,
                subject_id,
            )
            state_row = state_ref.get(transaction=transaction)
            state = state_row.to_dict() if state_row.exists else None
            state_digest = state.get("token_digest") if type(state) is dict else None
            member_ref = self._member_ref(organization_id, verified.uid)
            if (
                record.organization_id != organization_id
                or record.invitation_id != invitation_id
                or record.subject_id != subject_id
                or record.status != "active"
                or record.delivery_status is not SubjectInvitationDeliveryState.DELIVERED
                or record.expires_at <= now
                or record.role in {DecisionOSRole.OWNER, DecisionOSRole.ADMIN}
                or type(state) is not dict
                or state.get("schema_version") != 1
                or state.get("organization_id") != organization_id
                or state.get("subject_id") != subject_id
                or state.get("invitation_id") != invitation_id
                or type(state_digest) is not str
                or not hmac_compare(state_digest, digest)
                or state.get("status") != "active"
                or member_ref.get(transaction=transaction).exists
            ):
                raise InvitationUnavailable()
            membership = OrganizationMembership(
                organization_id=organization_id,
                uid=verified.uid,
                role=record.role,
                status=MembershipStatus.ACTIVE,
            )
            current = DecisionOSContext(principal=verified, membership=membership)
            result = mutation(transaction, current, subject_id)
            transaction.create(member_ref, membership.model_dump(mode="python"))
            transaction.update(invitation_ref, {"status": "accepted"})
            transaction.update(index_ref, {"status": "accepted"})
            transaction.update(state_ref, {"status": "accepted"})
            for event_name in ("invitation_accepted", "membership_activated"):
                self._append_audit_transaction(
                    transaction,
                    organization_id,
                    event_name,
                    verified.uid,
                    target_uid=verified.uid,
                    occurred_at=now,
                )
            return membership, result

        return accept(self._client.transaction())

    def create_workspace(
        self,
        context: DecisionOSContext,
        *,
        name: str,
        playbook: WorkspacePlaybook,
    ) -> DecisionWorkspace:
        from google.cloud import firestore

        workspace_id = self._identifiers.workspace_id()
        workspace_ref = self._workspace_ref(context.organization_id, workspace_id)

        @firestore.transactional
        def create(transaction):
            current = self._authorize_transaction(
                transaction,
                context,
                DecisionOSPermission.CREATE_WORKSPACE,
            )
            if workspace_ref.get(transaction=transaction).exists:
                raise WorkspaceUnavailable()
            workspace = DecisionWorkspace(
                workspace_id=workspace_id,
                organization_id=current.organization_id,
                name=_display_name(name),
                playbook=playbook,
                created_by_uid=current.principal.uid,
            )
            transaction.create(workspace_ref, workspace.model_dump(mode="python"))
            self._append_audit_transaction(
                transaction,
                current.organization_id,
                "workspace_created",
                current.principal.uid,
                occurred_at=_aware(self._clock()),
            )
            return workspace

        return create(self._client.transaction())

    def authorize_context(
        self,
        context: DecisionOSContext,
        permission: DecisionOSPermission,
    ) -> DecisionOSContext:
        """Reload membership before granting another repository authority."""

        current = self.load_context(context.principal, context.organization_id)
        require_permission(current, permission)
        return current

    def apply_organization_graph_membership_change(
        self,
        context: DecisionOSContext,
        *,
        carried_member_uids: tuple[str, ...],
        removed_member_uids: tuple[str, ...],
        mutation: Callable[[Any], _InMemoryPreparedMutation | None],
    ) -> None:
        """Apply a membership-only graph change in one DecisionOS transaction."""

        from google.cloud import firestore
        from google.cloud.firestore_v1.base_query import FieldFilter

        carried = tuple(dict.fromkeys(carried_member_uids))
        removed = tuple(dict.fromkeys(removed_member_uids))

        @firestore.transactional
        def apply(transaction):
            current = self._authorize_transaction(
                transaction,
                context,
                DecisionOSPermission.MANAGE_MEMBERS,
            )
            if set(carried) & set(removed):
                raise MembershipUnavailable()
            for uid in carried:
                self._member_transaction(transaction, current, uid)
            active_removed = []
            for uid in removed:
                row = self._member_ref(current.organization_id, uid).get(
                    transaction=transaction
                )
                member = _snapshot_model(OrganizationMembership, row)
                if member is not None and member.status is MembershipStatus.ACTIVE:
                    active_removed.append(member)
            if current.membership.role is DecisionOSRole.ADMIN and any(
                member.role is DecisionOSRole.OWNER for member in active_removed
            ):
                raise DecisionOSAuthorizationDenied()
            removed_uids = {member.uid for member in active_removed}
            if any(member.role is DecisionOSRole.OWNER for member in active_removed):
                owner_rows = (
                    self._organization_ref(current.organization_id)
                    .collection("members")
                    .where(filter=FieldFilter("role", "==", DecisionOSRole.OWNER.value))
                    .stream(transaction=transaction)
                )
                if not any(
                    row.id not in removed_uids
                    and OrganizationMembership.model_validate(row.to_dict()).status
                    is MembershipStatus.ACTIVE
                    for row in owner_rows
                ):
                    raise LastOwnerRequired()
            now = _aware(self._clock())
            for member in active_removed:
                updated = member.model_copy(update={"status": MembershipStatus.SUSPENDED})
                transaction.set(
                    self._member_ref(current.organization_id, member.uid),
                    updated.model_dump(mode="python"),
                )
                self._append_audit_transaction(
                    transaction,
                    current.organization_id,
                    "member_suspended",
                    current.principal.uid,
                    target_uid=member.uid,
                    occurred_at=now,
                )
            mutation(transaction)

        apply(self._client.transaction())

    def load_workspace(
        self,
        context: DecisionOSContext,
        workspace_id: str,
    ) -> DecisionWorkspace:
        current = self.load_context(context.principal, context.organization_id)
        require_permission(current, DecisionOSPermission.READ_WORKSPACE)
        workspace = _snapshot_model(
            DecisionWorkspace,
            self._workspace_ref(current.organization_id, workspace_id).get(),
        )
        if workspace is None:
            raise WorkspaceUnavailable()
        return workspace

    def list_workspaces(
        self,
        context: DecisionOSContext,
    ) -> tuple[DecisionWorkspace, ...]:
        current = self.load_context(context.principal, context.organization_id)
        require_permission(current, DecisionOSPermission.READ_WORKSPACE)
        rows = self._organization_ref(current.organization_id).collection("workspaces").stream()
        return tuple(
            sorted(
                (DecisionWorkspace.model_validate(row.to_dict()) for row in rows),
                key=lambda item: item.workspace_id,
            )
        )

    def update_member_role(
        self,
        context: DecisionOSContext,
        uid: str,
        role: DecisionOSRole,
    ) -> OrganizationMembership:
        from google.cloud import firestore

        member_ref = self._member_ref(context.organization_id, uid)

        @firestore.transactional
        def update(transaction):
            current = self._authorize_transaction(
                transaction,
                context,
                DecisionOSPermission.MANAGE_MEMBERS,
            )
            if current.membership.role is DecisionOSRole.ADMIN and role in {
                DecisionOSRole.OWNER,
                DecisionOSRole.ADMIN,
            }:
                raise DecisionOSAuthorizationDenied()
            member = self._member_transaction(transaction, current, uid)
            if current.membership.role is DecisionOSRole.ADMIN and member.role is DecisionOSRole.OWNER:
                raise DecisionOSAuthorizationDenied()
            if member.role is DecisionOSRole.OWNER and role is not DecisionOSRole.OWNER:
                self._require_another_owner_transaction(transaction, current.organization_id, uid)
            updated = member.model_copy(update={"role": role})
            transaction.set(member_ref, updated.model_dump(mode="python"))
            self._append_audit_transaction(
                transaction,
                current.organization_id,
                "member_role_changed",
                current.principal.uid,
                target_uid=uid,
                occurred_at=_aware(self._clock()),
            )
            return updated

        return update(self._client.transaction())

    def suspend_member(
        self,
        context: DecisionOSContext,
        uid: str,
    ) -> OrganizationMembership:
        return self._suspend_member(context, uid)

    def remove_member(
        self,
        context: DecisionOSContext,
        uid: str,
    ) -> OrganizationMembership:
        return self._suspend_member(context, uid)

    def list_audit(
        self,
        context: DecisionOSContext,
    ) -> tuple[DecisionOSAuditEvent, ...]:
        current = self.load_context(context.principal, context.organization_id)
        require_permission(current, DecisionOSPermission.READ_WORKSPACE)
        rows = (
            self._organization_ref(current.organization_id)
            .collection("audit")
            .order_by("occurred_at")
            .stream()
        )
        return tuple(DecisionOSAuditEvent.model_validate(row.to_dict()) for row in rows)

    def _authorize_transaction(
        self,
        transaction,
        context: DecisionOSContext,
        permission: DecisionOSPermission,
    ) -> DecisionOSContext:
        organization_row = self._organization_ref(context.organization_id).get(
            transaction=transaction
        )
        member_row = self._member_ref(context.organization_id, context.principal.uid).get(
            transaction=transaction
        )
        membership = _snapshot_model(OrganizationMembership, member_row)
        if (
            not organization_row.exists
            or membership is None
            or membership.status is not MembershipStatus.ACTIVE
        ):
            raise DecisionOSAuthorizationDenied()
        current = DecisionOSContext(principal=context.principal, membership=membership)
        require_permission(current, permission)
        return current

    def _member_transaction(
        self,
        transaction,
        context: DecisionOSContext,
        uid: str,
    ) -> OrganizationMembership:
        member = _snapshot_model(
            OrganizationMembership,
            self._member_ref(context.organization_id, uid).get(transaction=transaction),
        )
        if member is None or member.status is not MembershipStatus.ACTIVE:
            raise MembershipUnavailable()
        return member

    def _require_another_owner_transaction(
        self,
        transaction,
        organization_id: str,
        excluded_uid: str,
    ) -> None:
        from google.cloud.firestore_v1.base_query import FieldFilter

        query = (
            self._organization_ref(organization_id)
            .collection("members")
            .where(filter=FieldFilter("role", "==", DecisionOSRole.OWNER.value))
        )
        rows = query.stream(transaction=transaction)
        if not any(
            row.id != excluded_uid
            and OrganizationMembership.model_validate(row.to_dict()).status
            is MembershipStatus.ACTIVE
            for row in rows
        ):
            raise LastOwnerRequired()

    def _suspend_member(
        self,
        context: DecisionOSContext,
        uid: str,
    ) -> OrganizationMembership:
        from google.cloud import firestore

        member_ref = self._member_ref(context.organization_id, uid)

        @firestore.transactional
        def suspend(transaction):
            current = self._authorize_transaction(
                transaction,
                context,
                DecisionOSPermission.MANAGE_MEMBERS,
            )
            member = self._member_transaction(transaction, current, uid)
            if current.membership.role is DecisionOSRole.ADMIN and member.role is DecisionOSRole.OWNER:
                raise DecisionOSAuthorizationDenied()
            if member.role is DecisionOSRole.OWNER:
                self._require_another_owner_transaction(transaction, current.organization_id, uid)
            updated = member.model_copy(update={"status": MembershipStatus.SUSPENDED})
            transaction.set(member_ref, updated.model_dump(mode="python"))
            self._append_audit_transaction(
                transaction,
                current.organization_id,
                "member_suspended",
                current.principal.uid,
                target_uid=uid,
                occurred_at=_aware(self._clock()),
            )
            return updated

        return suspend(self._client.transaction())

    def _append_audit_transaction(
        self,
        transaction,
        organization_id: str,
        event_name: str,
        actor_uid: str,
        *,
        occurred_at: datetime,
        target_uid: str | None = None,
    ) -> None:
        audit_id = f"audit_{secrets.randbelow(10**20):020d}"
        event = DecisionOSAuditEvent(
            audit_id=audit_id,
            organization_id=organization_id,
            event_name=event_name,
            actor_uid=actor_uid,
            target_uid=target_uid,
            occurred_at=occurred_at,
        )
        audit_ref = self._organization_ref(organization_id).collection("audit").document(
            audit_id
        )
        transaction.create(audit_ref, event.model_dump(mode="python"))


def hmac_compare(first: str, second: str) -> bool:
    return secrets.compare_digest(first, second)
