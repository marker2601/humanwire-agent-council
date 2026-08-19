from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from humanwire.decisionos_app import DecisionOSDependencies, create_decisionos_app
from humanwire.decisionos_auth import (
    AppCheckUnavailable,
    AuthenticationUnavailable,
    VerifiedAppCheck,
)
from humanwire.decisionos_models import (
    DecisionOSPrincipal,
    DecisionOSRole,
    MembershipStatus,
)
from humanwire.decisionos_store import (
    DecisionOSAuthorizationDenied,
    InMemoryDecisionOSRepository,
    InvitationUnavailable,
    OrganizationUnavailable,
)
from humanwire.organization_activation import (
    ActivatedOrganizationMembership,
    ActivationDeliveryStatus,
    ActivationService,
    BulkInvitationRequest,
)
from humanwire.organization_models import (
    ImportDraft,
    OrganizationGraphCandidate,
    OrganizationSubject,
    OrganizationSubjectKind,
    SourceRecord,
    SourceSnapshot,
    SubjectLifecycle,
)
from humanwire.organization_store import (
    ImportUnavailable,
    InMemoryOrganizationGraphRepository,
)

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
ORG_A = "org_01ARZ3NDEKTSV4RRFFQ69G5FAV"
ORG_B = "org_01ARZ3NDEKTSV4RRFFQ69G5FAW"
IMPORT_A = "imp_01ARZ3NDEKTSV4RRFFQ69G5FAV"
SNAPSHOT_A = "snap_01ARZ3NDEKTSV4RRFFQ69G5FAV"
ALICE = "sub_01ARZ3NDEKTSV4RRFFQ69G5FAV"
BOB = "sub_01ARZ3NDEKTSV4RRFFQ69G5FAW"
CAROL = "sub_01ARZ3NDEKTSV4RRFFQ69G5FAX"
AI = "sub_01ARZ3NDEKTSV4RRFFQ69G5FAY"
EXTERNAL = "sub_01ARZ3NDEKTSV4RRFFQ69G5FAZ"
REVIEW = "sub_01ARZ3NDEKTSV4RRFFQ69G5FB0"
SUSPENDED = "sub_01ARZ3NDEKTSV4RRFFQ69G5FB1"
PRIVATE_EMAIL = "alice.private@example.invalid"


class MutableClock:
    def __init__(self) -> None:
        self.current = NOW

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


class SequenceIdentifiers:
    def __init__(self) -> None:
        self.organization_sequence = iter((ORG_A, ORG_B))
        self.invitation_sequence = 0

    def organization_id(self) -> str:
        return next(self.organization_sequence)

    def workspace_id(self) -> str:
        return "wrk_01ARZ3NDEKTSV4RRFFQ69G5FAV"

    def invitation_id(self) -> str:
        self.invitation_sequence += 1
        return f"inv_{self.invitation_sequence:026d}"

    def invitation_token(self) -> str:
        return f"opaque-subject-invitation-token-{self.invitation_sequence:06d}"


class RecordingTransport:
    route_id = "consented_test_route"

    def __init__(self, *, fail_subject_ids: set[str] | None = None) -> None:
        self.fail_subject_ids = set() if fail_subject_ids is None else fail_subject_ids
        self.deliveries = []

    def deliver(self, grant) -> None:
        self.deliveries.append(grant)
        if grant.subject_id in self.fail_subject_ids:
            raise RuntimeError(f"private transport failure for {PRIVATE_EMAIL}")


class MissingRouteTransport:
    def deliver(self, grant) -> None:
        raise AssertionError(f"must not deliver {grant.invitation_id}")


class AlternateTransport(RecordingTransport):
    route_id = "other_consented_route"


class PrivateString(str):
    pass


class FixedActivationService:
    def __init__(self, *, receipt=None, accepted=None) -> None:
        self.receipt = receipt
        self.accepted = accepted

    def create_invitations(self, _context, _request):
        return self.receipt

    def accept(self, _principal, _token):
        return self.accepted


class FakeAuthenticator:
    def __init__(self) -> None:
        self.sessions = {
            "session-owner": principal("firebase-owner-01"),
            "session-invitee": principal("firebase-route-invitee"),
        }

    def verify_session_cookie(self, cookie: str, *, check_revoked: bool):
        if check_revoked is not True or cookie not in self.sessions:
            raise AuthenticationUnavailable()
        return self.sessions[cookie]


class FakeAppCheck:
    def verify(self, token: str) -> VerifiedAppCheck:
        if token != "valid-app-check":
            raise AppCheckUnavailable()
        return VerifiedAppCheck(app_id="humanwire-web")


def principal(uid: str, *, verified: bool = True) -> DecisionOSPrincipal:
    return DecisionOSPrincipal(
        uid=uid,
        email_verified=verified,
        provider_ids=("google.com",),
    )


def _draft() -> ImportDraft:
    specs = (
        (ALICE, "directory/alice", OrganizationSubjectKind.HUMAN, SubjectLifecycle.DRAFT_IMPORTED),
        (BOB, "directory/bob", OrganizationSubjectKind.HUMAN, SubjectLifecycle.DRAFT_IMPORTED),
        (CAROL, "directory/carol", OrganizationSubjectKind.HUMAN, SubjectLifecycle.DRAFT_IMPORTED),
        (AI, "directory/ai", OrganizationSubjectKind.AI_SPECIALIST, SubjectLifecycle.ACTIVE),
        (EXTERNAL, "directory/external", OrganizationSubjectKind.EXTERNAL, SubjectLifecycle.DIRECTORY_ONLY),
        (REVIEW, "directory/review", OrganizationSubjectKind.HUMAN, SubjectLifecycle.NEEDS_REVIEW),
        (SUSPENDED, "directory/suspended", OrganizationSubjectKind.HUMAN, SubjectLifecycle.SUSPENDED),
    )
    records = tuple(
        SourceRecord(
            record_id=f"rec_{index:026d}",
            source_ordinal=index,
            source_identity=source_identity,
            fields=(("email", PRIVATE_EMAIL if subject_id == ALICE else f"person{index}@example.invalid"),),
        )
        for index, (subject_id, source_identity, _kind, _lifecycle) in enumerate(specs, 1)
    )
    subjects = tuple(
        OrganizationSubject(
            subject_id=subject_id,
            organization_id=ORG_A,
            kind=kind,
            lifecycle=lifecycle,
            display_name=f"Subject {index}",
            source_identity=source_identity,
            specialist_key="test_specialist" if kind is OrganizationSubjectKind.AI_SPECIALIST else None,
        )
        for index, (subject_id, source_identity, kind, lifecycle) in enumerate(specs, 1)
    )
    snapshot = SourceSnapshot(
        snapshot_id=SNAPSHOT_A,
        organization_id=ORG_A,
        source_kind="csv",
        captured_at=NOW,
        records=records,
        semantic_digest="1" * 64,
    )
    return ImportDraft(
        import_id=IMPORT_A,
        organization_id=ORG_A,
        source_snapshot=snapshot,
        candidate=OrganizationGraphCandidate(
            organization_id=ORG_A,
            source_snapshot_id=SNAPSHOT_A,
            subjects=subjects,
        ),
        base_graph_version=0,
        semantic_digest="2" * 64,
        created_at=NOW,
    )


@pytest.fixture
def activation_setup():
    clock = MutableClock()
    decisionos = InMemoryDecisionOSRepository(
        identifiers=SequenceIdentifiers(),
        clock=clock,
    )
    owner = principal("firebase-owner-01")
    organization = decisionos.create_organization(owner, "Northstar Labs")
    owner_context = decisionos.load_context(owner, organization.organization_id)
    graph = InMemoryOrganizationGraphRepository(decisionos=decisionos, clock=clock)
    draft = graph.save_import_draft(owner_context, _draft())
    graph.commit_graph(
        owner_context,
        draft_id=draft.import_id,
        reviewed_digest=draft.semantic_digest,
    )
    return clock, decisionos, graph, owner_context


def _service(activation_setup, transport=None) -> ActivationService:
    _clock, decisionos, graph, _context = activation_setup
    return ActivationService(
        decisionos_repository=decisionos,
        graph_repository=graph,
        transport=transport,
    )


def _request(*subject_ids: str, role: DecisionOSRole = DecisionOSRole.CONTRIBUTOR):
    return BulkInvitationRequest(subject_ids=subject_ids, role=role)


def _exception_graph_text(error: BaseException) -> str:
    pending = [error]
    seen: set[int] = set()
    rendered: list[str] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        rendered.extend((str(current), repr(current), repr(current.args)))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return " ".join(rendered)


def _exception_reaches_secret(error: BaseException, secret: str) -> bool:
    pending: list[object] = [error]
    seen: set[int] = set()
    budget = 20_000
    while pending and budget:
        budget -= 1
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if type(value) is str:
            if value == secret:
                return True
            continue
        if value is None or type(value) in {int, float, bool, bytes, type}:
            continue
        if isinstance(value, BaseException):
            pending.extend(value.args)
            pending.extend((value.__cause__, value.__context__, value.__traceback__))
        if type(value).__name__ == "traceback":
            if value.tb_frame.f_code.co_filename != __file__:
                pending.extend(value.tb_frame.f_locals.values())
            pending.append(value.tb_next)
            continue
        if type(value) is dict:
            pending.extend(value.keys())
            pending.extend(value.values())
            continue
        if type(value) in {tuple, list, set, frozenset}:
            pending.extend(value)
            continue
        state = getattr(value, "__dict__", None)
        if type(state) is dict:
            pending.append(state)
    return False


def test_bulk_invite_only_targets_explicit_committed_human_subject_ids(
    activation_setup,
) -> None:
    _clock, _decisionos, graph, owner_context = activation_setup

    receipt = _service(activation_setup).create_invitations(
        owner_context,
        _request(ALICE, BOB),
    )

    assert receipt.requested_subject_ids == (ALICE, BOB)
    assert receipt.created_count == 2
    assert receipt.delivered_count == 0
    assert receipt.pending_count == 2
    assert [item.status for item in receipt.invitations] == [
        ActivationDeliveryStatus.NOT_DELIVERED,
        ActivationDeliveryStatus.NOT_DELIVERED,
    ]
    issued_graph = graph.load_graph(owner_context)
    assert issued_graph.version == 2
    by_id = {item.subject_id: item for item in issued_graph.subjects}
    assert by_id[ALICE].lifecycle is SubjectLifecycle.INVITED
    assert by_id[BOB].lifecycle is SubjectLifecycle.INVITED
    carol = by_id[CAROL]
    assert carol.lifecycle is SubjectLifecycle.DIRECTORY_ONLY
    assert graph.load_committed_import(owner_context, 2) == (
        graph.load_import_draft(owner_context, IMPORT_A),
        graph.load_import_receipt(owner_context, IMPORT_A),
    )


def test_no_configured_transport_has_no_delivery_side_effect(activation_setup) -> None:
    receipt = _service(activation_setup).create_invitations(
        activation_setup[3],
        _request(ALICE),
    )

    assert receipt.invitations[0].status is ActivationDeliveryStatus.NOT_DELIVERED
    assert receipt.pending_count == 1


def test_non_null_transport_requires_an_exact_server_route_id(activation_setup) -> None:
    with pytest.raises(ValueError, match="invitation transport route is invalid"):
        _service(activation_setup, MissingRouteTransport())


def test_route_less_invitation_can_be_reissued_once_for_a_configured_route(
    activation_setup,
) -> None:
    first = _service(activation_setup).create_invitations(
        activation_setup[3],
        _request(ALICE),
    )
    issued_version = activation_setup[2].load_graph(activation_setup[3]).version
    transport = RecordingTransport()

    recovered = _service(activation_setup, transport).create_invitations(
        activation_setup[3],
        _request(ALICE),
    )

    assert recovered.invitations[0].invitation_id != first.invitations[0].invitation_id
    assert recovered.invitations[0].status is ActivationDeliveryStatus.DELIVERED
    assert len(transport.deliveries) == 1
    assert activation_setup[2].load_graph(activation_setup[3]).version == issued_version


def test_non_null_delivery_route_mismatch_remains_fail_closed(activation_setup) -> None:
    first_transport = RecordingTransport()
    _service(activation_setup, first_transport).create_invitations(
        activation_setup[3],
        _request(ALICE),
    )
    alternate = AlternateTransport()

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        _service(activation_setup, alternate).create_invitations(
            activation_setup[3],
            _request(ALICE),
        )

    assert alternate.deliveries == []


def test_ineligible_subject_is_rejected_before_any_token_is_generated(
    activation_setup,
) -> None:
    identifiers = activation_setup[1]._identifiers
    before = identifiers.invitation_sequence

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        _service(activation_setup).create_invitations(
            activation_setup[3],
            _request(AI),
        )

    assert identifiers.invitation_sequence == before


def test_subject_invitation_retry_fails_closed_when_subject_state_is_missing(
    activation_setup,
) -> None:
    service = _service(activation_setup)
    service.create_invitations(activation_setup[3], _request(ALICE))
    activation_setup[1]._active_subject_invitations.pop((ORG_A, ALICE))

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        service.create_invitations(activation_setup[3], _request(ALICE))


def test_legacy_and_subject_invitations_share_id_and_digest_namespace(
    activation_setup,
) -> None:
    _service(activation_setup).create_invitations(
        activation_setup[3],
        _request(ALICE),
    )
    activation_setup[1]._identifiers.invitation_sequence = 0

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        activation_setup[1].create_invitation(
            activation_setup[3],
            role=DecisionOSRole.VIEWER,
            expires_in=timedelta(days=1),
        )


def test_legacy_acceptance_rejects_ambiguous_cross_kind_token_digest(
    activation_setup,
) -> None:
    decisionos = activation_setup[1]
    generic = decisionos.create_invitation(
        activation_setup[3],
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
    )
    _service(activation_setup).create_invitations(
        activation_setup[3],
        _request(ALICE),
    )
    subject_invitation_id = decisionos._active_subject_invitations[(ORG_A, ALICE)]
    subject_record = decisionos._subject_invitations[subject_invitation_id]
    object.__setattr__(
        subject_record,
        "token_digest",
        hashlib.sha256(generic.token.get_secret_value().encode()).hexdigest(),
    )

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        decisionos.accept_invitation(
            principal("firebase-ambiguous-token"),
            generic.token.get_secret_value(),
        )


def test_consented_transport_receives_one_opaque_grant_per_explicit_subject(
    activation_setup,
) -> None:
    transport = RecordingTransport()

    receipt = _service(activation_setup, transport).create_invitations(
        activation_setup[3],
        _request(ALICE, BOB, role=DecisionOSRole.APPROVER),
    )

    assert receipt.delivered_count == 2
    assert receipt.pending_count == 0
    assert [item.subject_id for item in transport.deliveries] == [ALICE, BOB]
    assert all(item.role is DecisionOSRole.APPROVER for item in transport.deliveries)
    assert all(
        item.token.get_secret_value().startswith("opaque-subject-invitation-token-")
        for item in transport.deliveries
    )


def test_delivery_exception_is_durable_unknown_and_never_auto_redelivered(
    activation_setup,
) -> None:
    transport = RecordingTransport(fail_subject_ids={BOB})
    service = _service(activation_setup, transport)
    first = service.create_invitations(activation_setup[3], _request(ALICE, BOB))
    first_bob_token = transport.deliveries[1].token.get_secret_value()
    issued_version = activation_setup[2].load_graph(activation_setup[3]).version

    assert [item.status for item in first.invitations] == [
        ActivationDeliveryStatus.DELIVERED,
        ActivationDeliveryStatus.DELIVERY_UNKNOWN,
    ]
    assert (first.delivered_count, first.pending_count) == (1, 1)

    transport.fail_subject_ids.clear()
    second = service.create_invitations(activation_setup[3], _request(ALICE, BOB))

    assert len(transport.deliveries) == 2
    assert second.delivered_count == 1
    assert second.pending_count == 1
    assert second.invitations[0].invitation_id == first.invitations[0].invitation_id
    assert second.invitations[1].invitation_id == first.invitations[1].invitation_id
    assert activation_setup[2].load_graph(activation_setup[3]).version == issued_version
    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        service.accept(principal("firebase-bob-old-token"), first_bob_token)

    third = service.create_invitations(activation_setup[3], _request(ALICE, BOB))
    assert third == second
    assert len(transport.deliveries) == 2


def test_unknown_delivery_remains_token_free_and_never_resends_after_expiry(
    activation_setup,
) -> None:
    clock, decisionos, graph, owner_context = activation_setup
    transport = RecordingTransport(fail_subject_ids={ALICE})
    request = BulkInvitationRequest(
        subject_ids=(ALICE,),
        role=DecisionOSRole.CONTRIBUTOR,
        expires_in_seconds=60,
    )
    first = _service(activation_setup, transport).create_invitations(
        owner_context,
        request,
    )
    token = transport.deliveries[0].token.get_secret_value()
    digest = hashlib.sha256(token.encode()).hexdigest()
    invitation_id = first.invitations[0].invitation_id
    issued_version = graph.load_graph(owner_context).version
    records_before = dict(decisionos._subject_invitations)
    active_before = dict(decisionos._active_subject_invitations)
    index_before = dict(decisionos._invitation_token_index)
    audit_before = {
        organization_id: tuple(events)
        for organization_id, events in decisionos._audit.items()
    }
    identifier_sequence = decisionos._identifiers.invitation_sequence

    clock.advance(timedelta(seconds=61))
    transport.fail_subject_ids.clear()
    restarted = _service(activation_setup, transport)
    retried = restarted.create_invitations(owner_context, request)

    assert retried.invitations[0].invitation_id == invitation_id
    assert retried.invitations[0].status is ActivationDeliveryStatus.DELIVERY_UNKNOWN
    assert (retried.delivered_count, retried.pending_count) == (0, 1)
    assert len(transport.deliveries) == 1
    assert decisionos._identifiers.invitation_sequence == identifier_sequence
    assert decisionos._subject_invitations == records_before
    assert decisionos._active_subject_invitations == active_before
    assert decisionos._invitation_token_index == index_before
    assert {
        organization_id: tuple(events)
        for organization_id, events in decisionos._audit.items()
    } == audit_before
    assert graph.load_graph(owner_context).version == issued_version
    rendered = f"{retried!r} {retried.model_dump_json()}"
    assert token not in rendered
    assert digest not in rendered
    assert PRIVATE_EMAIL not in rendered


def test_pre_provider_failure_reissues_pending_grant_without_token_traceback(
    activation_setup,
    monkeypatch,
) -> None:
    transport = RecordingTransport()
    first_service = _service(activation_setup, transport)
    decisionos = activation_setup[1]
    original_begin = decisionos.begin_subject_invitation_delivery
    first_token = "opaque-subject-invitation-token-000001"

    def fail_before_provider(_context, grant):
        raise RuntimeError(grant.token.get_secret_value())

    monkeypatch.setattr(
        decisionos,
        "begin_subject_invitation_delivery",
        fail_before_provider,
    )
    with pytest.raises(InvitationUnavailable, match="invitation_unavailable") as caught:
        first_service.create_invitations(activation_setup[3], _request(ALICE))

    assert transport.deliveries == []
    assert not _exception_reaches_secret(caught.value, first_token)
    issued_version = activation_setup[2].load_graph(activation_setup[3]).version

    monkeypatch.setattr(
        decisionos,
        "begin_subject_invitation_delivery",
        original_begin,
    )
    restarted = _service(activation_setup, transport)
    recovered = restarted.create_invitations(activation_setup[3], _request(ALICE))

    assert recovered.invitations[0].status is ActivationDeliveryStatus.DELIVERED
    assert recovered.invitations[0].invitation_id == "inv_00000000000000000000000002"
    assert len(transport.deliveries) == 1
    assert activation_setup[2].load_graph(activation_setup[3]).version == issued_version
    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        restarted.accept(principal("firebase-revoked-pending"), first_token)


@pytest.mark.parametrize("subject_id", [AI, EXTERNAL, REVIEW, SUSPENDED])
def test_non_directory_human_targets_are_rejected(activation_setup, subject_id) -> None:
    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        _service(activation_setup).create_invitations(
            activation_setup[3],
            _request(subject_id),
        )


def test_duplicate_or_cross_tenant_subject_selection_is_rejected(activation_setup) -> None:
    with pytest.raises(ValidationError):
        _request(ALICE, ALICE)
    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        _service(activation_setup).create_invitations(
            activation_setup[3],
            _request("sub_00000000000000000000000000"),
        )


@pytest.mark.parametrize("role", [DecisionOSRole.OWNER, DecisionOSRole.ADMIN])
def test_subject_invitation_cannot_escalate_to_owner_or_admin(
    activation_setup,
    role,
) -> None:
    with pytest.raises(DecisionOSAuthorizationDenied, match="authorization_denied"):
        _service(activation_setup).create_invitations(
            activation_setup[3],
            _request(ALICE, role=role),
        )


def test_create_requires_fresh_owner_or_admin_manage_members_authority(
    activation_setup,
) -> None:
    _clock, decisionos, _graph, owner_context = activation_setup
    viewer = principal("firebase-viewer-01")
    invite = decisionos.create_invitation(
        owner_context,
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
    )
    decisionos.accept_invitation(viewer, invite.token.get_secret_value())
    viewer_context = decisionos.load_context(viewer, ORG_A)

    with pytest.raises(DecisionOSAuthorizationDenied, match="authorization_denied"):
        _service(activation_setup).create_invitations(viewer_context, _request(ALICE))


def test_already_bound_subject_is_rejected(activation_setup) -> None:
    _clock, decisionos, graph, owner_context = activation_setup
    member = principal("firebase-existing-member")
    invitation = decisionos.create_invitation(
        owner_context,
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
    )
    decisionos.accept_invitation(member, invitation.token.get_secret_value())
    graph.bind_member(owner_context, subject_id=ALICE, member_uid=member.uid)

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        _service(activation_setup).create_invitations(owner_context, _request(ALICE))


def test_acceptance_creates_membership_and_binds_verified_uid_once(
    activation_setup,
) -> None:
    transport = RecordingTransport()
    service = _service(activation_setup, transport)
    service.create_invitations(activation_setup[3], _request(ALICE))
    token = transport.deliveries[0].token.get_secret_value()
    invitee = principal("firebase-alice-uid")

    activated = service.accept(invitee, token)

    assert activated.uid == invitee.uid
    assert activated.subject_id == ALICE
    assert activated.organization_id == ORG_A
    assert activated.role is DecisionOSRole.CONTRIBUTOR
    assert activated.status is MembershipStatus.ACTIVE
    assert activation_setup[2].load_graph(activation_setup[3]).version == 3
    bound = next(
        item
        for item in activation_setup[2].load_graph(activation_setup[3]).subjects
        if item.subject_id == ALICE
    )
    assert bound.member_uid == invitee.uid
    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        service.accept(principal("firebase-other-uid"), token)


def test_imported_email_is_never_used_as_an_authenticated_claim_match(
    activation_setup,
) -> None:
    transport = RecordingTransport()
    service = _service(activation_setup, transport)
    service.create_invitations(activation_setup[3], _request(ALICE))

    activated = service.accept(
        principal("uid-not-derived-from-imported-email"),
        transport.deliveries[0].token.get_secret_value(),
    )

    assert activated.uid == "uid-not-derived-from-imported-email"


def test_unverified_existing_member_and_malformed_tokens_share_one_fixed_failure(
    activation_setup,
) -> None:
    transport = RecordingTransport()
    service = _service(activation_setup, transport)
    service.create_invitations(activation_setup[3], _request(ALICE))
    token = transport.deliveries[0].token.get_secret_value()
    _clock, decisionos, _graph, owner_context = activation_setup
    existing = principal("firebase-existing-unbound")
    generic = decisionos.create_invitation(
        owner_context,
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
    )
    decisionos.accept_invitation(existing, generic.token.get_secret_value())

    for bad_principal, bad_token in (
        (principal("firebase-unverified", verified=False), token),
        (existing, token),
        (principal("firebase-malformed"), "not a token"),
        (principal("firebase-unknown"), "x" * 40),
    ):
        with pytest.raises(InvitationUnavailable, match="invitation_unavailable") as caught:
            service.accept(bad_principal, bad_token)
        assert str(caught.value) == "invitation_unavailable"


def test_expired_invitation_is_inert(activation_setup) -> None:
    clock, _decisionos, _graph, owner_context = activation_setup
    transport = RecordingTransport()
    service = _service(activation_setup, transport)
    service.create_invitations(
        owner_context,
        BulkInvitationRequest(
            subject_ids=(ALICE,),
            role=DecisionOSRole.VIEWER,
            expires_in_seconds=60,
        ),
    )
    clock.advance(timedelta(seconds=61))

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable"):
        service.accept(
            principal("firebase-expired"),
            transport.deliveries[0].token.get_secret_value(),
        )
    with pytest.raises(OrganizationUnavailable):
        activation_setup[1].load_context(principal("firebase-expired"), ORG_A)


def test_two_principal_acceptance_race_has_one_atomic_winner(activation_setup) -> None:
    transport = RecordingTransport()
    service = _service(activation_setup, transport)
    service.create_invitations(activation_setup[3], _request(ALICE))
    token = transport.deliveries[0].token.get_secret_value()

    def accept(index: int) -> str:
        try:
            return service.accept(principal(f"firebase-racer-{index}"), token).uid
        except InvitationUnavailable as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(accept, range(2)))

    assert outcomes.count("invitation_unavailable") == 1
    winner = next(item for item in outcomes if item != "invitation_unavailable")
    assert activation_setup[1].load_context(principal(winner), ORG_A).membership.uid == winner
    graph = activation_setup[2].load_graph(activation_setup[3])
    assert next(item for item in graph.subjects if item.subject_id == ALICE).member_uid == winner


def test_service_restart_preserves_subject_invitation_state(activation_setup) -> None:
    transport = RecordingTransport()
    first = _service(activation_setup, transport)
    first.create_invitations(activation_setup[3], _request(ALICE))
    token = transport.deliveries[0].token.get_secret_value()

    restarted = _service(activation_setup, RecordingTransport())
    activated = restarted.accept(principal("firebase-after-restart"), token)

    assert activated.subject_id == ALICE
    assert activation_setup[2].load_committed_import(activation_setup[3], 3) == (
        activation_setup[2].load_import_draft(activation_setup[3], IMPORT_A),
        activation_setup[2].load_import_receipt(activation_setup[3], IMPORT_A),
    )


def test_committed_import_carry_fails_closed_on_corrupt_activation_transition(
    activation_setup,
) -> None:
    service = _service(activation_setup)
    service.create_invitations(activation_setup[3], _request(ALICE))
    graph = activation_setup[2]
    transition = graph._activation_transitions[(ORG_A, 2)]
    graph._activation_transitions[(ORG_A, 2)] = transition.model_copy(
        update={"subject_ids": (BOB,)}
    )

    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        graph.load_committed_import(activation_setup[3], 2)


def test_graph_failure_rolls_back_membership_token_and_graph_before_safe_retry(
    activation_setup,
    monkeypatch,
) -> None:
    transport = RecordingTransport()
    service = _service(activation_setup, transport)
    service.create_invitations(activation_setup[3], _request(ALICE))
    token = transport.deliveries[0].token.get_secret_value()
    invitee = principal("firebase-rollback")
    graph = activation_setup[2]
    original = graph._prepare_subject_invitation_acceptance

    def explode(*_args, **_kwargs):
        raise RuntimeError(f"private rollback sentinel {PRIVATE_EMAIL}")

    monkeypatch.setattr(graph, "_prepare_subject_invitation_acceptance", explode)
    with pytest.raises(InvitationUnavailable, match="invitation_unavailable") as caught:
        service.accept(invitee, token)
    assert PRIVATE_EMAIL not in _exception_graph_text(caught.value)
    with pytest.raises(OrganizationUnavailable):
        activation_setup[1].load_context(invitee, ORG_A)
    subject = next(
        item for item in graph.load_graph(activation_setup[3]).subjects if item.subject_id == ALICE
    )
    assert subject.member_uid is None

    monkeypatch.setattr(graph, "_prepare_subject_invitation_acceptance", original)
    assert service.accept(invitee, token).subject_id == ALICE


def test_token_is_not_reachable_from_create_persistence_failure_traceback(
    activation_setup,
    monkeypatch,
) -> None:
    transport = RecordingTransport()
    service = _service(activation_setup, transport)

    def fail_persistence(*_args, **_kwargs):
        raise RuntimeError("private delivery persistence failure")

    monkeypatch.setattr(
        activation_setup[1],
        "record_subject_invitation_delivery",
        fail_persistence,
    )
    with pytest.raises(InvitationUnavailable, match="invitation_unavailable") as caught:
        service.create_invitations(activation_setup[3], _request(ALICE))

    token = transport.deliveries[0].token.get_secret_value()
    assert not _exception_reaches_secret(caught.value, token)


def test_raw_token_is_not_reachable_from_invalid_replay_or_provider_tracebacks(
    activation_setup,
    monkeypatch,
) -> None:
    invalid = "opaque-invalid-invitation-token-000001"
    service = _service(activation_setup)
    with pytest.raises(InvitationUnavailable, match="invitation_unavailable") as caught:
        service.accept(principal("firebase-invalid-token"), invalid)
    assert not _exception_reaches_secret(caught.value, invalid)

    transport = RecordingTransport()
    service = _service(activation_setup, transport)
    service.create_invitations(activation_setup[3], _request(ALICE))
    token = transport.deliveries[0].token.get_secret_value()
    service.accept(principal("firebase-first-accept"), token)
    with pytest.raises(InvitationUnavailable, match="invitation_unavailable") as replay:
        service.accept(principal("firebase-replay"), token)
    assert not _exception_reaches_secret(replay.value, token)

    provider_token = "opaque-provider-failure-token-000001"

    def fail_provider(*_args, **_kwargs):
        raise RuntimeError(provider_token)

    monkeypatch.setattr(
        activation_setup[2],
        "accept_subject_invitation",
        fail_provider,
    )
    with pytest.raises(InvitationUnavailable, match="invitation_unavailable") as provider:
        service.accept(principal("firebase-provider-failure"), provider_token)
    assert not _exception_reaches_secret(provider.value, provider_token)


def test_tokens_digests_and_email_never_appear_in_receipts_repr_or_fixed_errors(
    activation_setup,
) -> None:
    transport = RecordingTransport(fail_subject_ids={ALICE})
    service = _service(activation_setup, transport)
    receipt = service.create_invitations(activation_setup[3], _request(ALICE))
    token = transport.deliveries[0].token.get_secret_value()
    digest = hashlib.sha256(token.encode()).hexdigest()
    rendered = f"{receipt!r} {receipt.model_dump_json()} {service!r}"
    assert token not in rendered
    assert digest not in rendered
    assert PRIVATE_EMAIL not in rendered

    with pytest.raises(InvitationUnavailable) as caught:
        service.accept(principal("firebase-failed-delivery"), token)
    error_graph = _exception_graph_text(caught.value)
    assert token not in error_graph
    assert digest not in error_graph
    assert PRIVATE_EMAIL not in error_graph


@pytest.mark.parametrize(
    "mutation",
    [
        lambda record: replace(record, role=DecisionOSRole.OWNER),
        lambda record: replace(record, organization_id=ORG_B),
        lambda record: replace(record, status="revoked"),
    ],
)
def test_subject_invitation_state_corruption_is_one_fixed_private_failure(
    activation_setup,
    mutation,
) -> None:
    transport = RecordingTransport()
    service = _service(activation_setup, transport)
    service.create_invitations(activation_setup[3], _request(ALICE))
    token = transport.deliveries[0].token.get_secret_value()
    decisionos = activation_setup[1]
    invitation_id, record = next(iter(decisionos._subject_invitations.items()))
    decisionos._subject_invitations[invitation_id] = mutation(record)

    with pytest.raises(InvitationUnavailable, match="invitation_unavailable") as caught:
        service.accept(principal("firebase-corrupt-state"), token)

    assert PRIVATE_EMAIL not in _exception_graph_text(caught.value)


def test_bulk_request_is_strict_frozen_and_bounded() -> None:
    request = _request(ALICE)
    with pytest.raises(ValidationError):
        BulkInvitationRequest(subject_ids=[ALICE], role=DecisionOSRole.VIEWER)
    with pytest.raises(ValidationError):
        BulkInvitationRequest(subject_ids=(ALICE,), role="viewer")
    with pytest.raises(ValidationError):
        BulkInvitationRequest(
            subject_ids=(ALICE,),
            role=DecisionOSRole.VIEWER,
            expires_in_seconds=True,
        )
    with pytest.raises(ValidationError):
        BulkInvitationRequest(
            subject_ids=(ALICE,),
            role=DecisionOSRole.VIEWER,
            expires_in_seconds=59,
        )
    with pytest.raises(ValidationError):
        request.subject_ids = (BOB,)


def _route_client(activation_setup, service, *, session: str | None = None) -> TestClient:
    _clock, decisionos, graph, _context = activation_setup
    dependencies = DecisionOSDependencies(
        authenticator=FakeAuthenticator(),
        app_check=FakeAppCheck(),
        repository=decisionos,
        allowed_hosts=frozenset({"decisionos.test"}),
        csrf_token_factory=lambda: "unused",
        organization_features_enabled=True,
        organization_source_parser=lambda _request: None,
        organization_import_service=object(),
        organization_graph_repository=graph,
        organization_projection_builder=lambda _graph, _reconciliation: None,
        organization_activation_service=service,
    )
    client = TestClient(
        create_decisionos_app(dependencies),
        base_url="https://decisionos.test",
        raise_server_exceptions=False,
    )
    if session is not None:
        client.cookies.set("__session", session)
        client.cookies.set(
            "__Host-humanwire-csrf",
            hashlib.sha256(session.encode()).hexdigest(),
        )
    return client


def _route_headers(client: TestClient) -> dict[str, str]:
    return {
        "Origin": "https://decisionos.test",
        "X-Firebase-AppCheck": "valid-app-check",
        "X-HumanWire-CSRF": client.cookies["__Host-humanwire-csrf"],
    }


def test_subject_invitation_routes_return_only_the_frozen_safe_contract(
    activation_setup,
) -> None:
    transport = RecordingTransport()
    service = _service(activation_setup, transport)
    owner_client = _route_client(activation_setup, service, session="session-owner")

    created = owner_client.post(
        f"/api/organizations/{ORG_A}/subject-invitations",
        headers=_route_headers(owner_client),
        json={
            "subject_ids": [ALICE],
            "role": "contributor",
            "expires_in_seconds": 3600,
        },
    )

    assert created.status_code == 201
    payload = created.json()
    assert payload == {
        "organization_id": ORG_A,
        "requested_subject_ids": [ALICE],
        "invitations": [
            {
                "invitation_id": payload["invitations"][0]["invitation_id"],
                "subject_id": ALICE,
                "status": "delivered",
                "expires_at": "2026-08-19T13:00:00Z",
            }
        ],
        "created_count": 1,
        "delivered_count": 1,
        "pending_count": 0,
    }
    token = transport.deliveries[0].token.get_secret_value()
    digest = hashlib.sha256(token.encode()).hexdigest()
    assert token not in created.text
    assert digest not in created.text
    assert PRIVATE_EMAIL not in created.text

    invitee_client = _route_client(
        activation_setup,
        service,
        session="session-invitee",
    )
    accepted = invitee_client.post(
        "/api/subject-invitations/accept",
        headers=_route_headers(invitee_client),
        json={"invitation_token": token},
    )

    assert accepted.status_code == 200
    assert accepted.json() == {
        "status": "active",
        "organization_id": ORG_A,
        "subject_id": ALICE,
        "role": "contributor",
    }
    assert "firebase-route-invitee" not in accepted.text


def test_subject_invitation_create_route_rejects_shadow_serializer_without_leak(
    activation_setup,
) -> None:
    receipt = _service(activation_setup).create_invitations(
        activation_setup[3],
        _request(ALICE),
    )
    hostile = receipt.model_copy(
        update={
            "model_dump": lambda **_kwargs: {
                "organization_id": ORG_A,
                "private": PRIVATE_EMAIL,
            }
        }
    )
    client = _route_client(
        activation_setup,
        FixedActivationService(receipt=hostile),
        session="session-owner",
    )

    response = client.post(
        f"/api/organizations/{ORG_A}/subject-invitations",
        headers=_route_headers(client),
        json={"subject_ids": [ALICE], "role": "contributor"},
    )

    assert response.status_code == 400
    assert response.json() == {"error": "invitation_unavailable"}
    assert PRIVATE_EMAIL not in response.text


@pytest.mark.parametrize("corruption", ["scalar_subclass", "private_extra"])
def test_subject_invitation_accept_route_deep_canonicalizes_result(
    activation_setup,
    corruption,
) -> None:
    accepted = ActivatedOrganizationMembership(
        organization_id=ORG_A,
        subject_id=ALICE,
        uid="firebase-hostile-result",
        role=DecisionOSRole.CONTRIBUTOR,
        status=MembershipStatus.ACTIVE,
    )
    if corruption == "scalar_subclass":
        accepted = accepted.model_copy(
            update={"organization_id": PrivateString(PRIVATE_EMAIL)}
        )
    else:
        accepted = accepted.model_copy(update={"private": PRIVATE_EMAIL})
    client = _route_client(
        activation_setup,
        FixedActivationService(accepted=accepted),
        session="session-invitee",
    )

    response = client.post(
        "/api/subject-invitations/accept",
        headers=_route_headers(client),
        json={"invitation_token": "opaque-route-token-000001"},
    )

    assert response.status_code == 400
    assert response.json() == {"error": "invitation_unavailable"}
    assert PRIVATE_EMAIL not in response.text


@pytest.mark.parametrize(
    ("headers", "session", "status", "code"),
    [
        ({}, None, 403, "origin_forbidden"),
        ({"Origin": "https://decisionos.test"}, None, 403, "app_check_failed"),
        (
            {
                "Origin": "https://decisionos.test",
                "X-Firebase-AppCheck": "valid-app-check",
            },
            None,
            401,
            "authentication_required",
        ),
        (
            {
                "Origin": "https://decisionos.test",
                "X-Firebase-AppCheck": "valid-app-check",
                "X-HumanWire-CSRF": "wrong",
            },
            "session-owner",
            403,
            "csrf_failed",
        ),
    ],
)
def test_subject_invitation_create_requires_exact_security_boundary(
    activation_setup,
    headers,
    session,
    status,
    code,
) -> None:
    client = _route_client(
        activation_setup,
        _service(activation_setup),
        session=session,
    )
    response = client.post(
        f"/api/organizations/{ORG_A}/subject-invitations",
        headers=headers,
        json={"subject_ids": [ALICE], "role": "viewer"},
    )

    assert (response.status_code, response.json()) == (status, {"error": code})


def test_subject_invitation_routes_reject_aliases_queries_and_non_exact_json(
    activation_setup,
) -> None:
    client = _route_client(
        activation_setup,
        _service(activation_setup),
        session="session-owner",
    )
    headers = _route_headers(client)

    for path in (
        f"/api/organizations/{ORG_A}/subject-invitations/",
        f"/api/organizations/{ORG_A}/subject-invitations?retry=true",
        "/api/subject-invitations/accept/",
    ):
        response = client.post(
            path,
            headers=headers,
            json={"subject_ids": [ALICE], "role": "viewer"},
            follow_redirects=False,
        )
        assert response.status_code == 405
        assert response.json() == {"error": "method_not_allowed"}

    for body in (
        {"subject_ids": [ALICE], "role": "viewer", "delivery_route": "email"},
        {"subject_ids": [ALICE], "role": "viewer", "expires_in_seconds": True},
        {"subject_ids": [ALICE], "role": "viewer", "expires_in_seconds": 59},
    ):
        response = client.post(
            f"/api/organizations/{ORG_A}/subject-invitations",
            headers=headers,
            json=body,
        )
        assert response.status_code == 400
        assert response.json() == {"error": "invalid_request"}


def test_subject_acceptance_never_enumerates_token_state_over_http(
    activation_setup,
) -> None:
    client = _route_client(
        activation_setup,
        _service(activation_setup),
        session="session-invitee",
    )

    for token in ("bad", "x" * 40, "private@example.invalid"):
        response = client.post(
            "/api/subject-invitations/accept",
            headers=_route_headers(client),
            json={"invitation_token": token},
        )
        assert response.status_code == 400
        assert response.json() == {"error": "invitation_unavailable"}
        assert token not in response.text


def test_legacy_generic_invitation_contract_is_unchanged_with_activation_routes(
    activation_setup,
) -> None:
    client = _route_client(
        activation_setup,
        _service(activation_setup),
        session="session-owner",
    )
    response = client.post(
        f"/api/organizations/{ORG_A}/invitations",
        headers=_route_headers(client),
        json={"role": "viewer"},
    )

    assert response.status_code == 201
    assert set(response.json()) == {
        "invitation_id",
        "role",
        "expires_at",
        "invitation_token",
    }
