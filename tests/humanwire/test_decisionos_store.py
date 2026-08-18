from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from humanwire.decisionos_models import (
    DecisionOSPrincipal,
    DecisionOSRole,
    MembershipStatus,
    WorkspacePlaybook,
)
from humanwire.decisionos_store import (
    DecisionOSAuthorizationDenied,
    DecisionOSPermission,
    FirestoreDecisionOSRepository,
    InMemoryDecisionOSRepository,
    InvitationUnavailable,
    LastOwnerRequired,
    MembershipUnavailable,
    OrganizationUnavailable,
    WorkspaceUnavailable,
    require_permission,
)

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
ORG_A = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
ORG_B = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AB"
WORKSPACE_A = "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
WORKSPACE_B = "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AB"
INVITE_A = "inv_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
INVITE_B = "inv_01HQ7XK9WPH4Y8ZQK3R2N1M6AB"


class SequenceIdentifiers:
    def __init__(self) -> None:
        self.organizations = [ORG_A, ORG_B]
        self.workspaces = [WORKSPACE_A, WORKSPACE_B]
        self.invitations = [INVITE_A, INVITE_B]
        self.tokens = ["invite-secret-token-01", "invite-secret-token-02"]

    def organization_id(self) -> str:
        return self.organizations.pop(0)

    def workspace_id(self) -> str:
        return self.workspaces.pop(0)

    def invitation_id(self) -> str:
        return self.invitations.pop(0)

    def invitation_token(self) -> str:
        return self.tokens.pop(0)


class MutableClock:
    def __init__(self) -> None:
        self.current = NOW

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


@pytest.fixture
def identifiers() -> SequenceIdentifiers:
    return SequenceIdentifiers()


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def repository(identifiers, clock) -> InMemoryDecisionOSRepository:
    return InMemoryDecisionOSRepository(clock=clock, identifiers=identifiers)


def _principal(uid: str) -> DecisionOSPrincipal:
    return DecisionOSPrincipal(
        uid=uid,
        email_verified=True,
        provider_ids=("google.com",),
    )


@pytest.fixture
def owner() -> DecisionOSPrincipal:
    return _principal("firebase-owner-01")


@pytest.fixture
def invitee() -> DecisionOSPrincipal:
    return _principal("firebase-approver-01")


def _create_owner_context(repository, owner):
    organization = repository.create_organization(owner, "  Northstar Labs  ")
    return repository.load_context(owner, organization.organization_id)


def test_create_organization_atomically_makes_creator_owner(repository, owner) -> None:
    organization = repository.create_organization(owner, "  Northstar Labs  ")

    context = repository.load_context(owner, organization.organization_id)
    organizations = repository.list_organizations(owner)
    audit = repository.list_audit(context)

    assert organization.organization_id == ORG_A
    assert organization.name == "Northstar Labs"
    assert context.membership.role is DecisionOSRole.OWNER
    assert context.membership.status is MembershipStatus.ACTIVE
    assert organizations == (organization,)
    assert [(item.event_name, item.actor_uid) for item in audit] == [
        ("organization_created", owner.uid),
        ("membership_activated", owner.uid),
    ]


def test_unknown_or_nonmember_organization_is_one_fixed_failure(repository, owner) -> None:
    repository.create_organization(owner, "Northstar Labs")
    outsider = _principal("firebase-outsider-01")

    for organization_id in (ORG_A, ORG_B, "../org"):
        with pytest.raises(OrganizationUnavailable, match="organization_unavailable"):
            repository.load_context(outsider, organization_id)


def test_invitation_is_one_time_role_bounded_and_not_retained_in_repr(
    repository,
    owner,
    invitee,
) -> None:
    context = _create_owner_context(repository, owner)
    invitation = repository.create_invitation(
        context,
        role=DecisionOSRole.APPROVER,
        expires_in=timedelta(days=7),
    )
    raw_token = invitation.token.get_secret_value()

    membership = repository.accept_invitation(invitee, raw_token)

    assert invitation.invitation_id == INVITE_A
    assert membership.organization_id == ORG_A
    assert membership.uid == invitee.uid
    assert membership.role is DecisionOSRole.APPROVER
    assert membership.status is MembershipStatus.ACTIVE
    assert raw_token not in repr(repository)
    assert raw_token not in " ".join(
        item.model_dump_json() for item in repository.list_audit(context)
    )
    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        repository.accept_invitation(invitee, raw_token)


def test_two_thread_invitation_race_has_one_member_and_one_fixed_failure(
    repository,
    owner,
    invitee,
) -> None:
    context = _create_owner_context(repository, owner)
    invitation = repository.create_invitation(
        context,
        role=DecisionOSRole.CONTRIBUTOR,
        expires_in=timedelta(days=7),
    )
    token = invitation.token.get_secret_value()

    def accept():
        try:
            return repository.accept_invitation(invitee, token).role.value
        except InvitationUnavailable as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = sorted(executor.map(lambda _index: accept(), range(2)))

    assert results == ["contributor", "invitation_unavailable"]
    assert repository.load_context(invitee, ORG_A).membership.role is DecisionOSRole.CONTRIBUTOR


def test_expired_invitation_is_inert(repository, owner, invitee, clock) -> None:
    context = _create_owner_context(repository, owner)
    invitation = repository.create_invitation(
        context,
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=7),
    )
    clock.advance(timedelta(days=8))

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        repository.accept_invitation(invitee, invitation.token.get_secret_value())
    with pytest.raises(OrganizationUnavailable):
        repository.load_context(invitee, ORG_A)


@pytest.mark.parametrize("role", [DecisionOSRole.OWNER, DecisionOSRole.ADMIN])
def test_invitation_cannot_delegate_organization_ownership(
    repository,
    owner,
    role,
) -> None:
    context = _create_owner_context(repository, owner)

    with pytest.raises(DecisionOSAuthorizationDenied, match="authorization_denied"):
        repository.create_invitation(context, role=role, expires_in=timedelta(days=7))


def test_workspace_load_requires_matching_tenant_context(repository, owner) -> None:
    context_a = _create_owner_context(repository, owner)
    workspace_a = repository.create_workspace(
        context_a,
        name="  Fundraising readiness  ",
        playbook=WorkspacePlaybook.FUNDRAISING_READINESS,
    )
    owner_b = _principal("firebase-owner-02")
    organization_b = repository.create_organization(owner_b, "Other Company")
    context_b = repository.load_context(owner_b, organization_b.organization_id)

    assert workspace_a.name == "Fundraising readiness"
    assert repository.load_workspace(context_a, WORKSPACE_A) == workspace_a
    assert repository.list_workspaces(context_a) == (workspace_a,)
    with pytest.raises(WorkspaceUnavailable, match="workspace_unavailable"):
        repository.load_workspace(context_b, WORKSPACE_A)


def test_viewer_can_read_but_cannot_create_workspace(repository, owner, invitee) -> None:
    owner_context = _create_owner_context(repository, owner)
    invitation = repository.create_invitation(
        owner_context,
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=7),
    )
    repository.accept_invitation(invitee, invitation.token.get_secret_value())
    viewer_context = repository.load_context(invitee, ORG_A)

    require_permission(viewer_context, DecisionOSPermission.READ_WORKSPACE)
    with pytest.raises(DecisionOSAuthorizationDenied, match="authorization_denied"):
        repository.create_workspace(
            viewer_context,
            name="Forbidden workspace",
            playbook=WorkspacePlaybook.LAUNCH_DECISION,
        )


def test_suspended_membership_loses_access_immediately(repository, owner, invitee) -> None:
    owner_context = _create_owner_context(repository, owner)
    invitation = repository.create_invitation(
        owner_context,
        role=DecisionOSRole.CONTRIBUTOR,
        expires_in=timedelta(days=7),
    )
    repository.accept_invitation(invitee, invitation.token.get_secret_value())

    suspended = repository.suspend_member(owner_context, invitee.uid)

    assert suspended.status is MembershipStatus.SUSPENDED
    with pytest.raises(OrganizationUnavailable, match="organization_unavailable"):
        repository.load_context(invitee, ORG_A)


def test_stale_context_loses_mutation_authority_after_suspension(
    repository,
    owner,
    invitee,
) -> None:
    owner_context = _create_owner_context(repository, owner)
    invitation = repository.create_invitation(
        owner_context,
        role=DecisionOSRole.DECISION_OWNER,
        expires_in=timedelta(days=7),
    )
    repository.accept_invitation(invitee, invitation.token.get_secret_value())
    stale_context = repository.load_context(invitee, ORG_A)
    repository.suspend_member(owner_context, invitee.uid)

    with pytest.raises(DecisionOSAuthorizationDenied, match="authorization_denied"):
        repository.create_workspace(
            stale_context,
            name="Stale authority",
            playbook=WorkspacePlaybook.LAUNCH_DECISION,
        )


def test_caller_constructed_role_does_not_replace_repository_authority(
    repository,
    owner,
    invitee,
) -> None:
    owner_context = _create_owner_context(repository, owner)
    invitation = repository.create_invitation(
        owner_context,
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=7),
    )
    repository.accept_invitation(invitee, invitation.token.get_secret_value())
    viewer_context = repository.load_context(invitee, ORG_A)
    forged = viewer_context.model_copy(
        update={
            "membership": viewer_context.membership.model_copy(
                update={"role": DecisionOSRole.OWNER}
            )
        }
    )

    with pytest.raises(DecisionOSAuthorizationDenied, match="authorization_denied"):
        repository.create_invitation(
            forged,
            role=DecisionOSRole.APPROVER,
            expires_in=timedelta(days=7),
        )


def test_final_owner_cannot_be_demoted_or_removed(repository, owner) -> None:
    context = _create_owner_context(repository, owner)

    with pytest.raises(LastOwnerRequired, match="last_owner_required"):
        repository.update_member_role(context, owner.uid, DecisionOSRole.ADMIN)
    with pytest.raises(LastOwnerRequired, match="last_owner_required"):
        repository.remove_member(context, owner.uid)


def test_owner_can_transfer_ownership_before_removing_self(repository, owner, invitee) -> None:
    owner_context = _create_owner_context(repository, owner)
    invitation = repository.create_invitation(
        owner_context,
        role=DecisionOSRole.APPROVER,
        expires_in=timedelta(days=7),
    )
    repository.accept_invitation(invitee, invitation.token.get_secret_value())

    promoted = repository.update_member_role(
        owner_context,
        invitee.uid,
        DecisionOSRole.OWNER,
    )
    removed = repository.remove_member(owner_context, owner.uid)

    assert promoted.role is DecisionOSRole.OWNER
    assert removed.status is MembershipStatus.SUSPENDED
    assert repository.load_context(invitee, ORG_A).membership.role is DecisionOSRole.OWNER
    with pytest.raises(OrganizationUnavailable):
        repository.load_context(owner, ORG_A)


def test_admin_cannot_promote_owner_or_modify_owner(repository, owner, invitee) -> None:
    owner_context = _create_owner_context(repository, owner)
    admin = _principal("firebase-admin-01")
    invitation = repository.create_invitation(
        owner_context,
        role=DecisionOSRole.CONTRIBUTOR,
        expires_in=timedelta(days=7),
    )
    repository.accept_invitation(admin, invitation.token.get_secret_value())
    repository.update_member_role(owner_context, admin.uid, DecisionOSRole.ADMIN)
    admin_context = repository.load_context(admin, ORG_A)

    with pytest.raises(DecisionOSAuthorizationDenied, match="authorization_denied"):
        repository.update_member_role(admin_context, invitee.uid, DecisionOSRole.OWNER)
    with pytest.raises(DecisionOSAuthorizationDenied, match="authorization_denied"):
        repository.suspend_member(admin_context, owner.uid)


def test_member_lookup_uses_fixed_failure_for_unknown_uid(repository, owner) -> None:
    context = _create_owner_context(repository, owner)

    with pytest.raises(MembershipUnavailable, match="membership_unavailable"):
        repository.update_member_role(
            context,
            "firebase-unknown-01",
            DecisionOSRole.VIEWER,
        )


def test_firestore_rejects_invalid_organization_id_before_client_access(owner) -> None:
    class NoAccessClient:
        def collection(self, _name):
            raise AssertionError("Firestore must not receive an invalid tenant path")

    repository = FirestoreDecisionOSRepository(NoAccessClient(), clock=lambda: NOW)

    with pytest.raises(OrganizationUnavailable, match="organization_unavailable"):
        repository.load_context(owner, "../org")


@pytest.mark.parametrize(
    ("operation", "expected_error"),
    [
        (lambda repository: repository._workspace_ref(ORG_A, "../workspace"), WorkspaceUnavailable),
        (
            lambda repository: repository._invitation_ref(ORG_A, "../invitation"),
            InvitationUnavailable,
        ),
    ],
)
def test_firestore_rejects_invalid_nested_ids_before_client_access(
    operation,
    expected_error,
) -> None:
    class NoAccessClient:
        def collection(self, _name):
            raise AssertionError("Firestore must not receive an invalid nested path")

    repository = FirestoreDecisionOSRepository(NoAccessClient(), clock=lambda: NOW)

    with pytest.raises(expected_error):
        operation(repository)


@pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="requires explicit Firestore emulator",
)
@pytest.mark.firestore_emulator
def test_firestore_emulator_preserves_tenant_invitation_and_workspace_semantics() -> None:
    from google.cloud import firestore

    suffix = uuid4().hex
    identifiers = SequenceIdentifiers()
    client = firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT", "humanwire-test"))
    repository = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=identifiers,
        organization_collection=f"humanwire_test_organizations_{suffix}",
        invitation_index_collection=f"humanwire_test_invites_{suffix}",
    )
    owner = _principal("firebase-owner-01")
    invitee = _principal("firebase-approver-01")
    organization = repository.create_organization(owner, "Northstar Labs")
    owner_context = repository.load_context(owner, organization.organization_id)
    invitation = repository.create_invitation(
        owner_context,
        role=DecisionOSRole.APPROVER,
        expires_in=timedelta(days=7),
    )

    def accept():
        try:
            return repository.accept_invitation(
                invitee,
                invitation.token.get_secret_value(),
            ).role.value
        except InvitationUnavailable as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = sorted(executor.map(lambda _index: accept(), range(2)))

    assert results == ["approver", "invitation_unavailable"]
    invitee_context = repository.load_context(invitee, organization.organization_id)
    workspace = repository.create_workspace(
        owner_context,
        name="Fundraising readiness",
        playbook=WorkspacePlaybook.FUNDRAISING_READINESS,
    )
    assert repository.load_workspace(invitee_context, workspace.workspace_id) == workspace
