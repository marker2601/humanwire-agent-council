"""Strict identity, tenancy, and workspace contracts for DecisionOS."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_ULID = r"[0-9A-HJKMNP-TV-Z]{26}"
_ORGANIZATION_ID = rf"^org_{_ULID}$"
_WORKSPACE_ID = rf"^wrk_{_ULID}$"
_FIREBASE_UID = r"^[A-Za-z0-9._:-]{1,128}$"
_PROVIDER_ID = r"^[a-z0-9][a-z0-9.-]{0,63}$"


class _DecisionOSModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DecisionOSRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    DECISION_OWNER = "decision_owner"
    CONTRIBUTOR = "contributor"
    APPROVER = "approver"
    VIEWER = "viewer"


class MembershipStatus(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class WorkspacePlaybook(StrEnum):
    LAUNCH_DECISION = "launch_decision"
    FUNDRAISING_READINESS = "fundraising_readiness"


class DecisionOSPrincipal(_DecisionOSModel):
    uid: str = Field(pattern=_FIREBASE_UID)
    email_verified: bool
    provider_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def has_canonical_unique_providers(self) -> Self:
        if len(set(self.provider_ids)) != len(self.provider_ids):
            raise ValueError("provider IDs must be unique")
        for provider_id in self.provider_ids:
            if not provider_id or len(provider_id) > 64:
                raise ValueError("provider ID is invalid")
            if provider_id.casefold() != provider_id:
                raise ValueError("provider ID must be canonical lowercase")
            if re.fullmatch(_PROVIDER_ID, provider_id) is None:
                raise ValueError("provider ID is invalid")
        return self


class DecisionOrganization(_DecisionOSModel):
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    name: str = Field(min_length=1, max_length=120)
    created_by_uid: str = Field(pattern=_FIREBASE_UID)


class OrganizationMembership(_DecisionOSModel):
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    uid: str = Field(pattern=_FIREBASE_UID)
    role: DecisionOSRole = Field(strict=False)
    status: MembershipStatus = Field(strict=False)


class DecisionWorkspace(_DecisionOSModel):
    workspace_id: str = Field(pattern=_WORKSPACE_ID)
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    name: str = Field(min_length=1, max_length=120)
    playbook: WorkspacePlaybook = Field(strict=False)
    created_by_uid: str = Field(pattern=_FIREBASE_UID)


class DecisionOSContext(_DecisionOSModel):
    principal: DecisionOSPrincipal
    membership: OrganizationMembership

    @model_validator(mode="after")
    def membership_is_active_and_matches_principal(self) -> Self:
        if self.membership.uid != self.principal.uid:
            raise ValueError("membership UID must match principal")
        if self.membership.status is not MembershipStatus.ACTIVE:
            raise ValueError("membership must be active")
        return self

    @property
    def organization_id(self) -> str:
        return self.membership.organization_id
