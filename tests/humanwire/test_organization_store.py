from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from humanwire.decisionos_models import (
    DecisionOSPrincipal,
    DecisionOSRole,
    MembershipStatus,
)
from humanwire.decisionos_store import (
    DecisionOSAuthorizationDenied,
    InMemoryDecisionOSRepository,
    LastOwnerRequired,
    MembershipUnavailable,
    OrganizationUnavailable,
)
from humanwire.organization_models import (
    ImportDraft,
    OrganizationGraphCandidate,
    OrganizationSubject,
    OrganizationSubjectKind,
    OrganizationUnit,
    SourceRecord,
    SourceSnapshot,
    SubjectLifecycle,
)
from humanwire.organization_store import (
    GraphVersionConflict,
    ImportLineageConflict,
    ImportUnavailable,
    InMemoryOrganizationGraphRepository,
)

ORG_A = "org_01ARZ3NDEKTSV4RRFFQ69G5FAV"
ORG_B = "org_01ARZ3NDEKTSV4RRFFQ69G5FAW"
SUBJECT_A = "sub_01ARZ3NDEKTSV4RRFFQ69G5FAV"
SUBJECT_B = "sub_01ARZ3NDEKTSV4RRFFQ69G5FAW"
SNAPSHOT_A = "snap_01ARZ3NDEKTSV4RRFFQ69G5FAV"
IMPORT_A = "imp_01ARZ3NDEKTSV4RRFFQ69G5FAV"
IMPORT_B = "imp_01ARZ3NDEKTSV4RRFFQ69G5FAW"
DIGEST_A = "1" * 64
DIGEST_B = "2" * 64
NOW = datetime(2026, 8, 18, 15, 0, tzinfo=UTC)
PRIVATE_VALUE = "private-alice@example.invalid"


class SequenceIdentifiers:
    def __init__(self) -> None:
        self.organizations = iter((ORG_A, ORG_B))
        self.invitation_sequence = 0

    def organization_id(self) -> str:
        return next(self.organizations)

    def workspace_id(self) -> str:
        return "wrk_01ARZ3NDEKTSV4RRFFQ69G5FAV"

    def invitation_id(self) -> str:
        self.invitation_sequence += 1
        return f"inv_{self.invitation_sequence:026d}"

    def invitation_token(self) -> str:
        return f"opaque-invitation-token-{self.invitation_sequence:06d}"


def principal(uid: str) -> DecisionOSPrincipal:
    return DecisionOSPrincipal(
        uid=uid,
        email_verified=True,
        provider_ids=("google.com",),
    )


def import_draft(
    *,
    organization_id: str = ORG_A,
    import_id: str = IMPORT_A,
    digest: str = DIGEST_A,
    base_version: int = 0,
    include_person: bool = True,
) -> ImportDraft:
    records = (
        SourceRecord(
            record_id="rec_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            source_ordinal=1,
            source_identity="directory/alice",
            fields=(("email", PRIVATE_VALUE),),
        ),
    ) if include_person else ()
    subjects = (
        OrganizationSubject(
            subject_id=SUBJECT_A,
            organization_id=organization_id,
            kind=OrganizationSubjectKind.HUMAN,
            lifecycle=SubjectLifecycle.DRAFT_IMPORTED,
            display_name="Alice Example",
            source_identity="directory/alice",
        ),
    ) if include_person else ()
    snapshot = SourceSnapshot(
        snapshot_id=SNAPSHOT_A,
        organization_id=organization_id,
        source_kind="csv",
        captured_at=NOW,
        records=records,
        semantic_digest=digest,
    )
    return ImportDraft(
        import_id=import_id,
        organization_id=organization_id,
        source_snapshot=snapshot,
        candidate=OrganizationGraphCandidate(
            organization_id=organization_id,
            source_snapshot_id=SNAPSHOT_A,
            subjects=subjects,
        ),
        base_graph_version=base_version,
        semantic_digest=digest,
        created_at=NOW,
    )


def two_subject_draft() -> ImportDraft:
    first = import_draft()
    second_record = SourceRecord(
        record_id="rec_01ARZ3NDEKTSV4RRFFQ69G5FAW",
        source_ordinal=2,
        source_identity="directory/bob",
        fields=(("email", "bob@example.invalid"),),
    )
    second_subject = OrganizationSubject(
        subject_id=SUBJECT_B,
        organization_id=ORG_A,
        kind=OrganizationSubjectKind.HUMAN,
        lifecycle=SubjectLifecycle.DRAFT_IMPORTED,
        display_name="Bob Example",
        source_identity="directory/bob",
    )
    return first.model_copy(
        update={
            "source_snapshot": first.source_snapshot.model_copy(
                update={"records": (*first.source_snapshot.records, second_record)}
            ),
            "candidate": first.candidate.model_copy(
                update={"subjects": (*first.candidate.subjects, second_subject)}
            ),
        }
    )
@pytest.fixture
def setup_repositories():
    decisionos = InMemoryDecisionOSRepository(
        identifiers=SequenceIdentifiers(),
        clock=lambda: NOW,
    )
    owner = principal("firebase-owner-01")
    organization = decisionos.create_organization(owner, "Northstar Labs")
    owner_context = decisionos.load_context(owner, organization.organization_id)
    repository = InMemoryOrganizationGraphRepository(
        decisionos=decisionos,
        clock=lambda: NOW,
    )
    return repository, decisionos, owner_context


def make_member(decisionos, owner_context, *, uid: str, role: DecisionOSRole):
    invitee = principal(uid)
    invitation = decisionos.create_invitation(
        owner_context,
        role=DecisionOSRole.VIEWER,
        expires_in=timedelta(days=1),
    )
    decisionos.accept_invitation(invitee, invitation.token.get_secret_value())
    if role is not DecisionOSRole.VIEWER:
        decisionos.update_member_role(owner_context, uid, role)
    return decisionos.load_context(invitee, owner_context.organization_id)


def exception_graph_text(error: BaseException) -> str:
    pending = [error]
    seen = set()
    rendered = []
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


def test_commit_requires_exact_reviewed_digest(setup_repositories) -> None:
    repository, _decisionos, owner_context = setup_repositories
    draft = repository.save_import_draft(owner_context, import_draft())

    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository.commit_graph(
            owner_context,
            draft_id=draft.import_id,
            reviewed_digest="0" * 64,
        )


def test_directory_subject_does_not_create_membership(setup_repositories) -> None:
    repository, _decisionos, owner_context = setup_repositories
    draft = repository.save_import_draft(owner_context, import_draft())

    receipt = repository.commit_graph(
        owner_context,
        draft_id=draft.import_id,
        reviewed_digest=draft.semantic_digest,
    )

    assert receipt.committed_subject_count == 1
    assert repository.load_graph(owner_context).subjects[0].lifecycle is SubjectLifecycle.DIRECTORY_ONLY
    with pytest.raises(OrganizationUnavailable, match="organization_unavailable"):
        repository.load_context(principal("firebase-imported-alice"), ORG_A)


def test_every_draft_key_is_bound_to_authenticated_tenant(setup_repositories) -> None:
    repository, decisionos, owner_context = setup_repositories
    saved = repository.save_import_draft(owner_context, import_draft())
    owner_b = principal("firebase-owner-02")
    org_b = decisionos.create_organization(owner_b, "Other Company")
    context_b = decisionos.load_context(owner_b, org_b.organization_id)

    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository.load_import_draft(context_b, saved.import_id)
    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository.commit_graph(
            context_b,
            draft_id=saved.import_id,
            reviewed_digest=saved.semantic_digest,
        )
    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository.save_import_draft(context_b, import_draft())


def test_viewer_reads_graph_but_only_admin_can_manage_imports(setup_repositories) -> None:
    repository, decisionos, owner_context = setup_repositories
    viewer_context = make_member(
        decisionos,
        owner_context,
        uid="firebase-viewer-01",
        role=DecisionOSRole.VIEWER,
    )
    admin_context = make_member(
        decisionos,
        owner_context,
        uid="firebase-admin-01",
        role=DecisionOSRole.ADMIN,
    )

    assert repository.load_graph(viewer_context).version == 0
    with pytest.raises(DecisionOSAuthorizationDenied, match="authorization_denied"):
        repository.save_import_draft(viewer_context, import_draft())
    assert repository.save_import_draft(admin_context, import_draft()).import_id == IMPORT_A


def test_exact_receipt_and_graph_version_reads_use_required_permissions(
    setup_repositories,
) -> None:
    repository, decisionos, owner_context = setup_repositories
    viewer_context = make_member(
        decisionos,
        owner_context,
        uid="firebase-viewer-01",
        role=DecisionOSRole.VIEWER,
    )
    saved = repository.save_import_draft(owner_context, import_draft())
    assert repository.load_import_receipt(owner_context, saved.import_id) is None
    assert repository.load_committed_import(viewer_context, 0) is None
    with pytest.raises(DecisionOSAuthorizationDenied, match="authorization_denied"):
        repository.load_import_receipt(viewer_context, saved.import_id)

    receipt = repository.commit_graph(
        owner_context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )

    assert repository.load_import_receipt(owner_context, saved.import_id) == receipt
    assert repository.load_committed_import(viewer_context, receipt.graph_version) == (
        saved,
        receipt,
    )
    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository.load_committed_import(viewer_context, receipt.graph_version + 1)


def test_graph_version_read_ignores_pending_latest_and_fails_on_corrupt_receipt(
    setup_repositories,
) -> None:
    repository, _decisionos, owner_context = setup_repositories
    committed_draft = repository.save_import_draft(owner_context, import_draft())
    receipt = repository.commit_graph(
        owner_context,
        draft_id=committed_draft.import_id,
        reviewed_digest=committed_draft.semantic_digest,
    )
    pending = import_draft(
        import_id=IMPORT_B,
        digest=DIGEST_B,
        base_version=receipt.graph_version,
    ).model_copy(update={"supersedes_import_id": committed_draft.import_id})
    repository.save_import_draft(owner_context, pending)

    assert repository.load_committed_import(owner_context, receipt.graph_version) == (
        committed_draft,
        receipt,
    )
    repository._imports[(ORG_A, committed_draft.import_id)].receipt = receipt.model_copy(
        update={"organization_id": ORG_B}
    )
    with pytest.raises(ImportUnavailable, match="import_unavailable") as captured:
        repository.load_committed_import(owner_context, receipt.graph_version)
    assert PRIVATE_VALUE not in exception_graph_text(captured.value)


def test_committed_import_carries_across_two_exact_member_binding_versions(
    setup_repositories,
) -> None:
    repository, decisionos, owner_context = setup_repositories
    first_member = make_member(
        decisionos,
        owner_context,
        uid="firebase-viewer-01",
        role=DecisionOSRole.VIEWER,
    )
    second_member = make_member(
        decisionos,
        owner_context,
        uid="firebase-viewer-02",
        role=DecisionOSRole.VIEWER,
    )
    draft = repository.save_import_draft(owner_context, two_subject_draft())
    receipt = repository.commit_graph(
        owner_context,
        draft_id=draft.import_id,
        reviewed_digest=draft.semantic_digest,
    )
    repository.bind_member(
        owner_context,
        subject_id=SUBJECT_A,
        member_uid=first_member.principal.uid,
    )
    repository.bind_member(
        owner_context,
        subject_id=SUBJECT_B,
        member_uid=second_member.principal.uid,
    )

    assert repository.load_committed_import(owner_context, 2) == (draft, receipt)
    assert repository.load_committed_import(owner_context, 3) == (draft, receipt)


def test_newer_committed_import_supersedes_prior_receipt_predecessor(
    setup_repositories,
) -> None:
    repository, decisionos, owner_context = setup_repositories
    member = make_member(
        decisionos,
        owner_context,
        uid="firebase-viewer-01",
        role=DecisionOSRole.VIEWER,
    )
    first = repository.save_import_draft(owner_context, import_draft())
    repository.commit_graph(
        owner_context,
        draft_id=first.import_id,
        reviewed_digest=first.semantic_digest,
    )
    repository.bind_member(
        owner_context,
        subject_id=SUBJECT_A,
        member_uid=member.principal.uid,
    )
    second = repository.save_import_draft(
        owner_context,
        import_draft(import_id=IMPORT_B, digest=DIGEST_B, base_version=2),
    )
    second_receipt = repository.commit_graph(
        owner_context,
        draft_id=second.import_id,
        reviewed_digest=second.semantic_digest,
    )

    assert second_receipt.graph_version == 3
    assert repository.load_committed_import(owner_context, 3) == (
        second,
        second_receipt,
    )


@pytest.mark.parametrize("corruption", ["subject", "lifecycle", "structure", "delta"])
def test_committed_import_predecessor_rejects_non_binding_graph_delta(
    setup_repositories,
    corruption,
) -> None:
    repository, decisionos, owner_context = setup_repositories
    member = make_member(
        decisionos,
        owner_context,
        uid="firebase-viewer-01",
        role=DecisionOSRole.VIEWER,
    )
    draft = repository.save_import_draft(owner_context, import_draft())
    repository.commit_graph(
        owner_context,
        draft_id=draft.import_id,
        reviewed_digest=draft.semantic_digest,
    )
    repository.bind_member(
        owner_context,
        subject_id=SUBJECT_A,
        member_uid=member.principal.uid,
    )
    target = repository._graphs[(ORG_A, 2)]
    if corruption == "subject":
        target = target.model_copy(
            update={
                "subjects": (
                    target.subjects[0].model_copy(update={"title": "PRIVATE-CHANGED"}),
                )
            }
        )
    elif corruption == "lifecycle":
        prior = repository._graphs[(ORG_A, 1)]
        target = prior.model_copy(
            update={
                "version": 2,
                "subjects": (
                    prior.subjects[0].model_copy(
                        update={"lifecycle": SubjectLifecycle.SUSPENDED}
                    ),
                ),
            }
        )
    elif corruption == "structure":
        target = target.model_copy(
            update={
                "units": (
                    OrganizationUnit(
                        unit_id="unit_01ARZ3NDEKTSV4RRFFQ69G5FAV",
                        organization_id=ORG_A,
                        name="PRIVATE-UNIT",
                    ),
                )
            }
        )
    else:
        target = target.model_copy(update={"version": 3})
        repository._graphs[(ORG_A, 3)] = target

    repository._graphs[(ORG_A, target.version)] = target
    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository.load_committed_import(owner_context, target.version)


def test_stale_draft_conflicts_and_duplicate_commit_returns_same_receipt(
    setup_repositories,
) -> None:
    repository, _decisionos, owner_context = setup_repositories
    first = repository.save_import_draft(owner_context, import_draft())
    receipt = repository.commit_graph(
        owner_context,
        draft_id=first.import_id,
        reviewed_digest=first.semantic_digest,
    )
    duplicate = repository.commit_graph(
        owner_context,
        draft_id=first.import_id,
        reviewed_digest=first.semantic_digest,
    )

    assert duplicate == receipt
    stale = repository.save_import_draft(
        owner_context,
        import_draft(import_id=IMPORT_B, digest=DIGEST_B),
    )
    with pytest.raises(GraphVersionConflict, match="graph_version_conflict"):
        repository.commit_graph(
            owner_context,
            draft_id=stale.import_id,
            reviewed_digest=stale.semantic_digest,
        )


def test_new_source_draft_durably_supersedes_uncommitted_latest(
    setup_repositories,
) -> None:
    repository, _decisionos, owner_context = setup_repositories
    first = repository.save_import_draft(owner_context, import_draft())
    second = repository.save_import_draft(
        owner_context,
        import_draft(import_id=IMPORT_B, digest=DIGEST_B),
    )

    with pytest.raises(ImportUnavailable, match="^import_lineage_conflict$"):
        repository.commit_graph(
            owner_context,
            draft_id=first.import_id,
            reviewed_digest=first.semantic_digest,
        )
    assert repository.commit_graph(
        owner_context,
        draft_id=second.import_id,
        reviewed_digest=second.semantic_digest,
    ).import_id == second.import_id


def test_committed_exact_retry_precedes_newer_source_freshness(
    setup_repositories,
) -> None:
    repository, _decisionos, owner_context = setup_repositories
    first = repository.save_import_draft(owner_context, import_draft())
    receipt = repository.commit_graph(
        owner_context,
        draft_id=first.import_id,
        reviewed_digest=first.semantic_digest,
        acknowledged_codes=("leaderless_team",),
    )
    repository.save_import_draft(
        owner_context,
        import_draft(import_id=IMPORT_B, digest=DIGEST_B, base_version=1),
    )

    assert repository.commit_graph(
        owner_context,
        draft_id=first.import_id,
        reviewed_digest=first.semantic_digest,
        acknowledged_codes=("leaderless_team",),
    ) == receipt
    with pytest.raises(ImportUnavailable, match="^import_unavailable$"):
        repository.commit_graph(
            owner_context,
            draft_id=first.import_id,
            reviewed_digest=first.semantic_digest,
            acknowledged_codes=(),
        )


def test_correction_must_supersede_exact_durable_latest(setup_repositories) -> None:
    repository, _decisionos, owner_context = setup_repositories
    first = repository.save_import_draft(owner_context, import_draft())
    second = repository.save_import_draft(
        owner_context,
        import_draft(import_id=IMPORT_B, digest=DIGEST_B),
    )
    stale_correction = import_draft(
        import_id="imp_01ARZ3NDEKTSV4RRFFQ69G5FAX",
        digest="3" * 64,
    ).model_copy(update={"supersedes_import_id": first.import_id})

    with pytest.raises(ImportUnavailable, match="^import_lineage_conflict$"):
        repository.save_import_draft(owner_context, stale_correction)

    exact_correction = stale_correction.model_copy(
        update={
            "import_id": "imp_01ARZ3NDEKTSV4RRFFQ69G5FAY",
            "supersedes_import_id": second.import_id,
        }
    )
    assert repository.save_import_draft(
        owner_context,
        exact_correction,
    ).supersedes_import_id == second.import_id


def test_concurrent_source_saves_publish_one_latest_lineage(setup_repositories) -> None:
    repository, _decisionos, owner_context = setup_repositories
    drafts = (
        import_draft(import_id=IMPORT_A, digest=DIGEST_A),
        import_draft(import_id=IMPORT_B, digest=DIGEST_B),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        saved = tuple(executor.map(lambda item: repository.save_import_draft(owner_context, item), drafts))

    receipts = []
    failures = []
    for draft in saved:
        try:
            receipts.append(
                repository.commit_graph(
                    owner_context,
                    draft_id=draft.import_id,
                    reviewed_digest=draft.semantic_digest,
                )
            )
        except ImportUnavailable as error:
            failures.append(error)

    assert len(receipts) == 1
    assert len(failures) == 1
    assert str(failures[0]) == "import_lineage_conflict"
    assert repository.load_graph(owner_context).version == 1


def test_commit_persists_exact_acknowledgements_and_retry_must_match(
    setup_repositories,
) -> None:
    repository, _decisionos, owner_context = setup_repositories
    draft = repository.save_import_draft(owner_context, import_draft())

    receipt = repository.commit_graph(
        owner_context,
        draft_id=draft.import_id,
        reviewed_digest=draft.semantic_digest,
        acknowledged_codes=("leaderless_team",),
    )

    assert receipt.acknowledged_codes == ("leaderless_team",)
    assert repository.commit_graph(
        owner_context,
        draft_id=draft.import_id,
        reviewed_digest=draft.semantic_digest,
        acknowledged_codes=("leaderless_team",),
    ) == receipt
    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository.commit_graph(
            owner_context,
            draft_id=draft.import_id,
            reviewed_digest=draft.semantic_digest,
            acknowledged_codes=(),
        )


def test_invalid_acknowledgement_shape_is_fixed_safe(setup_repositories) -> None:
    repository, _decisionos, owner_context = setup_repositories
    draft = repository.save_import_draft(owner_context, import_draft())

    with pytest.raises(ImportUnavailable, match="^import_unavailable$") as captured:
        repository.commit_graph(
            owner_context,
            draft_id=draft.import_id,
            reviewed_digest=draft.semantic_digest,
            acknowledged_codes=(["PRIVATE-ACKNOWLEDGEMENT"],),  # type: ignore[arg-type,list-item]
        )

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_concurrent_duplicate_commit_is_one_idempotent_result(setup_repositories) -> None:
    repository, _decisionos, owner_context = setup_repositories
    draft = repository.save_import_draft(owner_context, import_draft())

    def commit():
        return repository.commit_graph(
            owner_context,
            draft_id=draft.import_id,
            reviewed_digest=draft.semantic_digest,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = tuple(executor.map(lambda _index: commit(), range(2)))

    assert receipts[0] == receipts[1]
    assert repository.load_graph(owner_context).version == 1


def test_commit_appends_immutable_content_free_audit(setup_repositories) -> None:
    repository, _decisionos, owner_context = setup_repositories
    draft = repository.save_import_draft(owner_context, import_draft())
    receipt = repository.commit_graph(
        owner_context,
        draft_id=draft.import_id,
        reviewed_digest=draft.semantic_digest,
    )

    audit = repository.list_audit(owner_context)

    assert len(audit) == 1
    assert audit[0].actor_uid == owner_context.principal.uid
    assert audit[0].prior_graph_version == 0
    assert audit[0].new_graph_version == 1
    assert audit[0].source_snapshot_digest == DIGEST_A
    assert audit[0].receipt == receipt
    assert PRIVATE_VALUE not in repr(repository)
    assert PRIVATE_VALUE not in audit[0].model_dump_json()


def test_next_complete_source_suspends_missing_subject_instead_of_deleting_it(
    setup_repositories,
) -> None:
    repository, _decisionos, owner_context = setup_repositories
    first = repository.save_import_draft(owner_context, import_draft())
    repository.commit_graph(
        owner_context,
        draft_id=first.import_id,
        reviewed_digest=first.semantic_digest,
    )
    removal = repository.save_import_draft(
        owner_context,
        import_draft(
            import_id=IMPORT_B,
            digest=DIGEST_B,
            base_version=1,
            include_person=False,
        ),
    )

    repository.commit_graph(
        owner_context,
        draft_id=removal.import_id,
        reviewed_digest=removal.semantic_digest,
    )

    graph = repository.load_graph(owner_context)
    assert len(graph.subjects) == 1
    assert graph.subjects[0].subject_id == SUBJECT_A
    assert graph.subjects[0].lifecycle is SubjectLifecycle.SUSPENDED
    assert repository.list_audit(owner_context)[0].receipt.import_id == IMPORT_A


def test_bind_member_requires_an_existing_active_same_tenant_membership(
    setup_repositories,
) -> None:
    repository, decisionos, owner_context = setup_repositories
    draft = repository.save_import_draft(owner_context, import_draft())
    repository.commit_graph(
        owner_context,
        draft_id=draft.import_id,
        reviewed_digest=draft.semantic_digest,
    )

    with pytest.raises(OrganizationUnavailable, match="organization_unavailable"):
        repository.bind_member(
            owner_context,
            subject_id=SUBJECT_A,
            member_uid="firebase-imported-alice",
        )

    member_context = make_member(
        decisionos,
        owner_context,
        uid="firebase-imported-alice",
        role=DecisionOSRole.VIEWER,
    )
    bound = repository.bind_member(
        owner_context,
        subject_id=SUBJECT_A,
        member_uid=member_context.principal.uid,
    )

    assert bound.member_uid == member_context.principal.uid
    assert bound.lifecycle is SubjectLifecycle.ACTIVE
    assert repository.load_graph(owner_context).version == 2


def test_binding_and_source_removal_do_not_bypass_last_owner_protection(
    setup_repositories,
) -> None:
    repository, decisionos, owner_context = setup_repositories
    draft = repository.save_import_draft(owner_context, import_draft())
    repository.commit_graph(
        owner_context,
        draft_id=draft.import_id,
        reviewed_digest=draft.semantic_digest,
    )
    repository.bind_member(
        owner_context,
        subject_id=SUBJECT_A,
        member_uid=owner_context.principal.uid,
    )

    with pytest.raises(LastOwnerRequired, match="last_owner_required"):
        decisionos.remove_member(owner_context, owner_context.principal.uid)
    assert repository.load_context(owner_context.principal, ORG_A) == owner_context


def test_fixed_failures_do_not_retain_private_source_values_in_exception_graph(
    setup_repositories,
) -> None:
    repository, _decisionos, owner_context = setup_repositories
    repository.save_import_draft(owner_context, import_draft())

    try:
        repository.save_import_draft(owner_context, import_draft())
    except ImportUnavailable as error:
        nodes: list[BaseException] = [error]
        seen: set[int] = set()
        rendered: list[str] = []
        while nodes:
            node = nodes.pop()
            if id(node) in seen:
                continue
            seen.add(id(node))
            rendered.extend((str(node), repr(node), repr(node.args)))
            if node.__cause__ is not None:
                nodes.append(node.__cause__)
            if node.__context__ is not None:
                nodes.append(node.__context__)
        assert PRIVATE_VALUE not in " ".join(rendered)
    else:
        pytest.fail("duplicate import should fail closed")


def test_protocol_draft_load_list_and_reconciliation_are_tenant_bound(
    setup_repositories,
) -> None:
    repository, _decisionos, owner_context = setup_repositories
    saved = repository.save_import_draft(owner_context, import_draft())

    assert repository.load_import_draft(owner_context, saved.import_id) == saved
    assert repository.list_imports(owner_context) == (saved,)
    reconciliation = repository.reconcile_import(owner_context, saved.import_id)
    assert reconciliation.import_id == saved.import_id
    assert reconciliation.source_count == 1
    assert reconciliation.normalized_count == 1
    assert reconciliation.rejected_count == 0
    assert reconciliation.committable is True


def test_stale_admin_role_cannot_commit_a_saved_draft(setup_repositories) -> None:
    repository, decisionos, owner_context = setup_repositories
    admin_context = make_member(
        decisionos,
        owner_context,
        uid="firebase-admin-01",
        role=DecisionOSRole.ADMIN,
    )
    saved = repository.save_import_draft(admin_context, import_draft())
    decisionos.update_member_role(owner_context, admin_context.principal.uid, DecisionOSRole.VIEWER)

    with pytest.raises(DecisionOSAuthorizationDenied, match="authorization_denied"):
        repository.commit_graph(
            admin_context,
            draft_id=saved.import_id,
            reviewed_digest=saved.semantic_digest,
        )


def test_other_tenant_cannot_read_graph_audit_or_bind_subject(setup_repositories) -> None:
    repository, decisionos, owner_context = setup_repositories
    saved = repository.save_import_draft(owner_context, import_draft())
    repository.commit_graph(
        owner_context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    owner_b = principal("firebase-owner-02")
    org_b = decisionos.create_organization(owner_b, "Other Company")
    context_b = decisionos.load_context(owner_b, org_b.organization_id)

    assert repository.load_graph(context_b).version == 0
    assert repository.list_audit(context_b) == ()
    with pytest.raises(OrganizationUnavailable, match="organization_unavailable"):
        repository.bind_member(
            context_b,
            subject_id=SUBJECT_A,
            member_uid=context_b.principal.uid,
        )


def test_two_different_concurrent_drafts_serialize_one_winner(setup_repositories) -> None:
    repository, _decisionos, owner_context = setup_repositories
    drafts = (
        repository.save_import_draft(owner_context, import_draft()),
        repository.save_import_draft(
            owner_context,
            import_draft(import_id=IMPORT_B, digest=DIGEST_B),
        ),
    )

    def commit(draft):
        try:
            return repository.commit_graph(
                owner_context,
                draft_id=draft.import_id,
                reviewed_digest=draft.semantic_digest,
            )
        except ImportLineageConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(commit, drafts))

    assert sum(not isinstance(item, ImportLineageConflict) for item in results) == 1
    assert sum(isinstance(item, ImportLineageConflict) for item in results) == 1
    assert repository.load_graph(owner_context).version == 1


def _commit_and_bind(repository, decisionos, owner_context, *, uid: str):
    saved = repository.save_import_draft(owner_context, import_draft())
    repository.commit_graph(
        owner_context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    member_context = make_member(
        decisionos,
        owner_context,
        uid=uid,
        role=DecisionOSRole.VIEWER,
    )
    repository.bind_member(
        owner_context,
        subject_id=SUBJECT_A,
        member_uid=member_context.principal.uid,
    )
    return member_context


def test_source_removal_suspends_bound_non_owner_product_membership(
    setup_repositories,
) -> None:
    repository, decisionos, owner_context = setup_repositories
    member_context = _commit_and_bind(
        repository,
        decisionos,
        owner_context,
        uid="firebase-alice-01",
    )
    removal = repository.save_import_draft(
        owner_context,
        import_draft(
            import_id=IMPORT_B,
            digest=DIGEST_B,
            base_version=2,
            include_person=False,
        ),
    )

    repository.commit_graph(
        owner_context,
        draft_id=removal.import_id,
        reviewed_digest=removal.semantic_digest,
    )

    assert repository.load_graph(owner_context).subjects[0].lifecycle is SubjectLifecycle.SUSPENDED
    with pytest.raises(OrganizationUnavailable, match="organization_unavailable"):
        decisionos.load_context(member_context.principal, ORG_A)


def test_source_removal_rejects_suspending_last_owner_atomically(setup_repositories) -> None:
    repository, decisionos, owner_context = setup_repositories
    saved = repository.save_import_draft(owner_context, import_draft())
    repository.commit_graph(
        owner_context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    repository.bind_member(
        owner_context,
        subject_id=SUBJECT_A,
        member_uid=owner_context.principal.uid,
    )
    removal = repository.save_import_draft(
        owner_context,
        import_draft(
            import_id=IMPORT_B,
            digest=DIGEST_B,
            base_version=2,
            include_person=False,
        ),
    )

    with pytest.raises(LastOwnerRequired, match="last_owner_required"):
        repository.commit_graph(
            owner_context,
            draft_id=removal.import_id,
            reviewed_digest=removal.semantic_digest,
        )

    assert repository.load_graph(owner_context).version == 2
    assert decisionos.load_context(owner_context.principal, ORG_A) == owner_context


def test_source_removal_serializes_against_concurrent_membership_change(
    setup_repositories,
) -> None:
    repository, decisionos, owner_context = setup_repositories
    member_context = _commit_and_bind(
        repository,
        decisionos,
        owner_context,
        uid="firebase-alice-01",
    )
    removal = repository.save_import_draft(
        owner_context,
        import_draft(
            import_id=IMPORT_B,
            digest=DIGEST_B,
            base_version=2,
            include_person=False,
        ),
    )

    def remove_source():
        return repository.commit_graph(
            owner_context,
            draft_id=removal.import_id,
            reviewed_digest=removal.semantic_digest,
        )

    def change_role():
        try:
            return decisionos.update_member_role(
                owner_context,
                member_context.principal.uid,
                DecisionOSRole.CONTRIBUTOR,
            )
        except MembershipUnavailable as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        removed, role_result = tuple(executor.map(lambda fn: fn(), (remove_source, change_role)))

    assert removed.graph_version == 3
    assert isinstance(role_result, MembershipUnavailable) or (
        role_result.role is DecisionOSRole.CONTRIBUTOR
    )
    with pytest.raises(OrganizationUnavailable):
        decisionos.load_context(member_context.principal, ORG_A)


def test_commit_rejects_candidate_introduced_active_member_binding(
    setup_repositories,
) -> None:
    repository, decisionos, owner_context = setup_repositories
    member_context = make_member(
        decisionos,
        owner_context,
        uid="firebase-alice-01",
        role=DecisionOSRole.VIEWER,
    )
    forged_subject = import_draft().candidate.subjects[0].model_copy(
        update={
            "lifecycle": SubjectLifecycle.ACTIVE,
            "member_uid": member_context.principal.uid,
        }
    )
    forged = import_draft().model_copy(
        update={
            "candidate": import_draft().candidate.model_copy(
                update={"subjects": (forged_subject,)}
            )
        }
    )
    saved = repository.save_import_draft(owner_context, forged)

    with pytest.raises(ImportUnavailable):
        repository.commit_graph(
            owner_context,
            draft_id=saved.import_id,
            reviewed_digest=saved.semantic_digest,
        )


def test_commit_accepts_active_ai_specialist_without_membership(
    setup_repositories,
) -> None:
    repository, _decisionos, owner_context = setup_repositories
    original = import_draft()
    specialist = OrganizationSubject(
        subject_id=SUBJECT_A,
        organization_id=ORG_A,
        kind=OrganizationSubjectKind.AI_SPECIALIST,
        lifecycle=SubjectLifecycle.ACTIVE,
        display_name="Finance specialist",
        source_identity="directory/alice",
        specialist_key="finance",
    )
    candidate = original.candidate.model_copy(update={"subjects": (specialist,)})
    saved = repository.save_import_draft(
        owner_context,
        original.model_copy(update={"candidate": candidate}),
    )

    receipt = repository.commit_graph(
        owner_context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )

    committed = repository.load_graph(owner_context).subjects[0]
    assert receipt.committed_subject_count == 1
    assert committed.kind is OrganizationSubjectKind.AI_SPECIALIST
    assert committed.lifecycle is SubjectLifecycle.ACTIVE
    assert committed.member_uid is None


@pytest.mark.parametrize(
    "update",
    (
        {"member_uid": "firebase-hostile-ai"},
        {"lifecycle": SubjectLifecycle.DIRECTORY_ONLY},
    ),
)
def test_import_rejects_hostile_ai_membership_or_lifecycle(
    setup_repositories,
    update,
) -> None:
    repository, _decisionos, owner_context = setup_repositories
    original = import_draft()
    specialist = OrganizationSubject(
        subject_id=SUBJECT_A,
        organization_id=ORG_A,
        kind=OrganizationSubjectKind.AI_SPECIALIST,
        lifecycle=SubjectLifecycle.ACTIVE,
        display_name="Finance specialist",
        source_identity="directory/alice",
        specialist_key="finance",
    ).model_copy(update=update)
    forged = original.model_copy(
        update={"candidate": original.candidate.model_copy(update={"subjects": (specialist,)})}
    )

    with pytest.raises(ImportUnavailable, match="import_unavailable"):
        repository.save_import_draft(owner_context, forged)


def test_removal_clock_failure_rolls_back_graph_membership_and_retry(
    setup_repositories,
) -> None:
    repository, decisionos, owner_context = setup_repositories
    member_context = _commit_and_bind(
        repository,
        decisionos,
        owner_context,
        uid="firebase-alice-01",
    )
    removal = repository.save_import_draft(
        owner_context,
        import_draft(
            import_id=IMPORT_B,
            digest=DIGEST_B,
            base_version=2,
            include_person=False,
        ),
    )
    graph_before = repository.load_graph(owner_context)
    org_audit_before = repository.list_audit(owner_context)
    member_audit_before = decisionos.list_audit(owner_context)

    def failing_clock() -> datetime:
        raise RuntimeError(PRIVATE_VALUE)

    decisionos._clock = failing_clock
    with pytest.raises(ImportUnavailable) as captured:
        repository.commit_graph(
            owner_context,
            draft_id=removal.import_id,
            reviewed_digest=removal.semantic_digest,
        )

    assert PRIVATE_VALUE not in exception_graph_text(captured.value)
    assert repository.load_graph(owner_context) == graph_before
    assert repository.list_audit(owner_context) == org_audit_before
    assert decisionos.list_audit(owner_context) == member_audit_before
    assert decisionos.load_context(member_context.principal, ORG_A) == member_context

    decisionos._clock = lambda: NOW
    receipt = repository.commit_graph(
        owner_context,
        draft_id=removal.import_id,
        reviewed_digest=removal.semantic_digest,
    )

    assert receipt.graph_version == 3
    assert repository.load_graph(owner_context).version == 3
    assert len(repository.list_audit(owner_context)) == len(org_audit_before) + 1
    suspended = tuple(
        event
        for event in decisionos.list_audit(owner_context)
        if event.event_name == "member_suspended"
        and event.target_uid == member_context.principal.uid
    )
    assert len(suspended) == 1


def test_graph_audit_container_failure_rolls_back_both_repositories_and_retry(
    setup_repositories,
) -> None:
    repository, decisionos, owner_context = setup_repositories
    member_context = _commit_and_bind(
        repository,
        decisionos,
        owner_context,
        uid="firebase-alice-01",
    )
    removal = repository.save_import_draft(
        owner_context,
        import_draft(
            import_id=IMPORT_B,
            digest=DIGEST_B,
            base_version=2,
            include_person=False,
        ),
    )
    graph_before = repository.load_graph(owner_context)
    org_audit_before = repository.list_audit(owner_context)
    member_audit_before = decisionos.list_audit(owner_context)

    class FailingAuditContainer(dict):
        def items(self):
            raise RuntimeError(PRIVATE_VALUE)

        def setdefault(self, _key, _default=None):
            raise RuntimeError(PRIVATE_VALUE)

    repository._audit = FailingAuditContainer(repository._audit)
    with pytest.raises(ImportUnavailable) as captured:
        repository.commit_graph(
            owner_context,
            draft_id=removal.import_id,
            reviewed_digest=removal.semantic_digest,
        )

    assert PRIVATE_VALUE not in exception_graph_text(captured.value)
    assert repository.load_graph(owner_context) == graph_before
    assert repository.list_audit(owner_context) == org_audit_before
    assert decisionos.list_audit(owner_context) == member_audit_before
    assert decisionos.load_context(member_context.principal, ORG_A) == member_context

    repository._audit = {key: list(value) for key, value in dict(repository._audit).items()}
    receipt = repository.commit_graph(
        owner_context,
        draft_id=removal.import_id,
        reviewed_digest=removal.semantic_digest,
    )

    assert receipt.graph_version == 3
    assert repository.load_graph(owner_context).version == 3
    assert len(repository.list_audit(owner_context)) == len(org_audit_before) + 1
    with pytest.raises(OrganizationUnavailable):
        decisionos.load_context(member_context.principal, ORG_A)
    assert len(
        tuple(
            event
            for event in decisionos.list_audit(owner_context)
            if event.event_name == "member_suspended"
            and event.target_uid == member_context.principal.uid
        )
    ) == 1


def test_persistent_decisionos_audit_assignment_failure_restores_exact_state_and_retry(
    setup_repositories,
    monkeypatch,
) -> None:
    repository, decisionos, owner_context = setup_repositories
    member_context = _commit_and_bind(
        repository,
        decisionos,
        owner_context,
        uid="firebase-alice-01",
    )
    removal = repository.save_import_draft(
        owner_context,
        import_draft(
            import_id=IMPORT_B,
            digest=DIGEST_B,
            base_version=2,
            include_person=False,
        ),
    )
    graph_before = repository.load_graph(owner_context)
    graph_bytes_before = graph_before.model_dump_json()
    org_audit_before = repository.list_audit(owner_context)
    member_audit_before = decisionos.list_audit(owner_context)
    state_references_before = (
        repository._graphs,
        repository._current_versions,
        repository._imports,
        repository._audit,
        decisionos._memberships,
        decisionos._audit,
    )
    state_before = (
        dict(repository._graphs),
        dict(repository._current_versions),
        {
            key: (saved.draft, saved.receipt)
            for key, saved in repository._imports.items()
        },
        {key: tuple(events) for key, events in repository._audit.items()},
        dict(decisionos._memberships),
        {key: tuple(events) for key, events in decisionos._audit.items()},
        decisionos._audit_sequence,
    )
    original_setattr = InMemoryDecisionOSRepository.__setattr__
    audit_assignments = []

    def fail_persistently_on_audit_assignment(instance, name, value):
        if instance is decisionos and name == "_audit":
            audit_assignments.append(value)
            raise RuntimeError(PRIVATE_VALUE)
        original_setattr(instance, name, value)

    with monkeypatch.context() as injection:
        injection.setattr(
            InMemoryDecisionOSRepository,
            "__setattr__",
            fail_persistently_on_audit_assignment,
        )
        with pytest.raises(ImportUnavailable) as captured:
            repository.commit_graph(
                owner_context,
                draft_id=removal.import_id,
                reviewed_digest=removal.semantic_digest,
            )

        assert len(audit_assignments) == 1
        assert audit_assignments[0] is not state_references_before[5]
        assert PRIVATE_VALUE not in exception_graph_text(captured.value)
        assert all(
            actual is expected
            for actual, expected in zip(
                (
                    repository._graphs,
                    repository._current_versions,
                    repository._imports,
                    repository._audit,
                    decisionos._memberships,
                    decisionos._audit,
                ),
                state_references_before,
                strict=True,
            )
        )
        assert (
            dict(repository._graphs),
            dict(repository._current_versions),
            {
                key: (saved.draft, saved.receipt)
                for key, saved in repository._imports.items()
            },
            {key: tuple(events) for key, events in repository._audit.items()},
            dict(decisionos._memberships),
            {key: tuple(events) for key, events in decisionos._audit.items()},
            decisionos._audit_sequence,
        ) == state_before
        assert repository.load_graph(owner_context).model_dump_json() == graph_bytes_before
        assert repository.list_audit(owner_context) == org_audit_before
        assert decisionos.list_audit(owner_context) == member_audit_before
        assert decisionos.load_context(member_context.principal, ORG_A) == member_context

    receipt = repository.commit_graph(
        owner_context,
        draft_id=removal.import_id,
        reviewed_digest=removal.semantic_digest,
    )

    assert receipt.graph_version == graph_before.version + 1
    assert repository.load_graph(owner_context).version == receipt.graph_version
    assert len(repository._graphs) == len(state_before[0]) + 1
    assert repository._current_versions[ORG_A] == receipt.graph_version
    assert repository._imports[(ORG_A, IMPORT_B)].receipt == receipt
    assert sum(saved.receipt is not None for saved in repository._imports.values()) == (
        sum(saved_receipt is not None for _draft, saved_receipt in state_before[2].values()) + 1
    )
    org_audit_after = repository.list_audit(owner_context)
    assert org_audit_after[:-1] == org_audit_before
    assert org_audit_after[-1].receipt == receipt
    assert len(org_audit_after) == len(org_audit_before) + 1
    changed_memberships = {
        key
        for key, membership in decisionos._memberships.items()
        if state_before[4].get(key) != membership
    }
    assert changed_memberships == {(ORG_A, member_context.principal.uid)}
    assert (
        decisionos._memberships[(ORG_A, member_context.principal.uid)].status
        is MembershipStatus.SUSPENDED
    )
    member_audit_after = decisionos.list_audit(owner_context)
    assert member_audit_after[:-1] == member_audit_before
    assert member_audit_after[-1].event_name == "member_suspended"
    assert member_audit_after[-1].target_uid == member_context.principal.uid
    assert len(member_audit_after) == len(member_audit_before) + 1


def test_removal_serializes_membership_version_until_no_fail_publish(
    setup_repositories,
) -> None:
    repository, decisionos, owner_context = setup_repositories
    member_context = _commit_and_bind(
        repository,
        decisionos,
        owner_context,
        uid="firebase-alice-01",
    )
    removal = repository.save_import_draft(
        owner_context,
        import_draft(
            import_id=IMPORT_B,
            digest=DIGEST_B,
            base_version=2,
            include_person=False,
        ),
    )
    clock_entered = Event()
    release_clock = Event()

    def blocking_clock() -> datetime:
        clock_entered.set()
        assert release_clock.wait(timeout=5)
        return NOW

    decisionos._clock = blocking_clock
    with ThreadPoolExecutor(max_workers=2) as executor:
        removal_future = executor.submit(
            repository.commit_graph,
            owner_context,
            draft_id=removal.import_id,
            reviewed_digest=removal.semantic_digest,
        )
        assert clock_entered.wait(timeout=5)
        role_future = executor.submit(
            decisionos.update_member_role,
            owner_context,
            member_context.principal.uid,
            DecisionOSRole.CONTRIBUTOR,
        )
        assert not role_future.done()
        release_clock.set()
        receipt = removal_future.result(timeout=5)
        with pytest.raises(MembershipUnavailable):
            role_future.result(timeout=5)

    assert receipt.graph_version == 3
    with pytest.raises(OrganizationUnavailable):
        decisionos.load_context(member_context.principal, ORG_A)


def test_commit_carries_only_a_fresh_active_trusted_binding(setup_repositories) -> None:
    repository, decisionos, owner_context = setup_repositories
    member_context = _commit_and_bind(
        repository,
        decisionos,
        owner_context,
        uid="firebase-alice-01",
    )
    next_draft = repository.save_import_draft(
        owner_context,
        import_draft(import_id=IMPORT_B, digest=DIGEST_B, base_version=2),
    )

    repository.commit_graph(
        owner_context,
        draft_id=next_draft.import_id,
        reviewed_digest=next_draft.semantic_digest,
    )

    carried = repository.load_graph(owner_context).subjects[0]
    assert carried.lifecycle is SubjectLifecycle.ACTIVE
    assert carried.member_uid == member_context.principal.uid


def test_commit_rejects_a_stale_carried_member_binding(setup_repositories) -> None:
    repository, decisionos, owner_context = setup_repositories
    member_context = _commit_and_bind(
        repository,
        decisionos,
        owner_context,
        uid="firebase-alice-01",
    )
    next_draft = repository.save_import_draft(
        owner_context,
        import_draft(import_id=IMPORT_B, digest=DIGEST_B, base_version=2),
    )
    decisionos.suspend_member(owner_context, member_context.principal.uid)

    with pytest.raises(ImportUnavailable):
        repository.commit_graph(
            owner_context,
            draft_id=next_draft.import_id,
            reviewed_digest=next_draft.semantic_digest,
        )
    assert repository.load_graph(owner_context).version == 2


def test_one_uid_cannot_bind_two_subjects_and_prior_graph_is_immutable(
    setup_repositories,
) -> None:
    repository, decisionos, owner_context = setup_repositories
    second = OrganizationSubject(
        subject_id=SUBJECT_B,
        organization_id=ORG_A,
        kind=OrganizationSubjectKind.HUMAN,
        lifecycle=SubjectLifecycle.DRAFT_IMPORTED,
        display_name="Bob Example",
        source_identity="directory/bob",
    )
    base = import_draft()
    two_people = base.model_copy(
        update={
            "candidate": base.candidate.model_copy(
                update={"subjects": (*base.candidate.subjects, second)}
            )
        }
    )
    saved = repository.save_import_draft(owner_context, two_people)
    repository.commit_graph(
        owner_context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )
    version_one = repository.load_graph(owner_context)
    member_context = make_member(
        decisionos,
        owner_context,
        uid="firebase-alice-01",
        role=DecisionOSRole.VIEWER,
    )
    repository.bind_member(
        owner_context,
        subject_id=SUBJECT_A,
        member_uid=member_context.principal.uid,
    )

    with pytest.raises(OrganizationUnavailable):
        repository.bind_member(
            owner_context,
            subject_id=SUBJECT_B,
            member_uid=member_context.principal.uid,
        )
    assert version_one.version == 1
    assert all(subject.member_uid is None for subject in version_one.subjects)
