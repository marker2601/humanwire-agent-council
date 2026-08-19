"""Organization authority and workspace repositories for DecisionOS."""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
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
        mutation: Callable[[Any], None],
    ) -> None:
        raise NotImplementedError


@dataclass
class _InvitationRecord:
    invitation_id: str
    organization_id: str
    role: DecisionOSRole
    token_digest: str
    expires_at: datetime
    status: str = "active"


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
        mutation: Callable[[Any], None],
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
            mutation(None)
            for key, updated in updated_memberships:
                self._memberships[key] = updated
            self._audit_sequence = next_audit_sequence
            self._audit.setdefault(current.organization_id, []).extend(audit_events)

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
            if index.get("status") != "active":
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
        mutation: Callable[[Any], None],
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
