from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

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

ORG_A = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
ORG_B = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AB"
WORKSPACE_A = "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
USER_A = "firebase-user-01"


def _principal(**updates: Any) -> DecisionOSPrincipal:
    values: dict[str, object] = {
        "uid": USER_A,
        "email_verified": True,
        "provider_ids": ("google.com",),
    }
    values.update(updates)
    return DecisionOSPrincipal.model_validate(values)


def _membership(**updates: Any) -> OrganizationMembership:
    values: dict[str, object] = {
        "organization_id": ORG_A,
        "uid": USER_A,
        "role": "decision_owner",
        "status": "active",
    }
    values.update(updates)
    return OrganizationMembership.model_validate(values)


def test_context_requires_matching_active_membership() -> None:
    principal = _principal()
    membership = _membership()

    context = DecisionOSContext(principal=principal, membership=membership)

    assert context.organization_id == ORG_A
    assert context.principal is principal
    assert context.membership.role is DecisionOSRole.DECISION_OWNER


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"uid": "different-user"}, "must match principal"),
        ({"status": "suspended"}, "must be active"),
        ({"status": "invited"}, "must be active"),
    ],
)
def test_context_rejects_wrong_or_inactive_membership(
    updates: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        DecisionOSContext(principal=_principal(), membership=_membership(**updates))


@pytest.mark.parametrize(
    "value",
    [
        "../org",
        "ORG SPACE",
        "",
        "a" * 129,
        "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AI",
        "person@example.com",
    ],
)
def test_organization_id_rejects_noncanonical_or_identifying_values(value: str) -> None:
    with pytest.raises(ValidationError):
        DecisionOrganization(
            organization_id=value,
            name="Northstar Labs",
            created_by_uid=USER_A,
        )


@pytest.mark.parametrize(
    "value",
    [
        "../workspace",
        "workspace name",
        "",
        "w" * 129,
        "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AI",
        "founder@example.com",
    ],
)
def test_workspace_id_rejects_noncanonical_or_identifying_values(value: str) -> None:
    with pytest.raises(ValidationError):
        DecisionWorkspace(
            workspace_id=value,
            organization_id=ORG_A,
            name="Launch decision",
            playbook="launch_decision",
            created_by_uid=USER_A,
        )


def test_workspace_parses_wire_playbook_and_keeps_tenant_binding() -> None:
    workspace = DecisionWorkspace(
        workspace_id=WORKSPACE_A,
        organization_id=ORG_A,
        name="Fundraising readiness",
        playbook="fundraising_readiness",
        created_by_uid=USER_A,
    )

    assert workspace.playbook is WorkspacePlaybook.FUNDRAISING_READINESS
    assert workspace.organization_id == ORG_A


@pytest.mark.parametrize(
    "values",
    [
        {"uid": "", "email_verified": True},
        {"uid": "user@example.com", "email_verified": True},
        {"uid": USER_A, "email_verified": True, "provider_ids": ("GOOGLE.COM",)},
        {
            "uid": USER_A,
            "email_verified": True,
            "provider_ids": ("google.com", "google.com"),
        },
        {"uid": USER_A, "email_verified": 1},
    ],
)
def test_principal_rejects_unsafe_or_ambiguous_identity(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DecisionOSPrincipal.model_validate(values)


def test_models_are_strict_frozen_and_forbid_extra_fields() -> None:
    membership = _membership()
    assert membership.status is MembershipStatus.ACTIVE

    with pytest.raises(ValidationError):
        OrganizationMembership.model_validate(
            {
                **membership.model_dump(),
                "role": "owner",
                "unexpected": True,
            }
        )
    with pytest.raises(ValidationError, match="frozen"):
        membership.role = DecisionOSRole.OWNER


def test_role_and_playbook_catalogs_are_exact_product_contracts() -> None:
    assert [item.value for item in DecisionOSRole] == [
        "owner",
        "admin",
        "decision_owner",
        "contributor",
        "approver",
        "viewer",
    ]
    assert [item.value for item in WorkspacePlaybook] == [
        "launch_decision",
        "fundraising_readiness",
    ]
    assert {item.value for item in MembershipStatus} == {
        "invited",
        "active",
        "suspended",
    }


def test_context_cannot_borrow_another_organization_membership() -> None:
    membership = _membership(organization_id=ORG_B)

    context = DecisionOSContext(principal=_principal(), membership=membership)

    assert context.organization_id == ORG_B
    assert context.organization_id != ORG_A
