from __future__ import annotations

import inspect
import multiprocessing
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from humanwire.decisionos_models import DecisionOSPrincipal, DecisionOSRole
from humanwire.decisionos_store import DecisionOSAuthorizationDenied, InMemoryDecisionOSRepository
from humanwire.organization_import import (
    ImportCorrectionKind,
    ImportCorrectionRequest,
    OrganizationImportReviewRequired,
    OrganizationImportService,
    OrganizationImportStale,
    RuleOrganizationMapper,
)
from humanwire.organization_models import (
    CommitImportRequest,
    OrganizationGraphCandidate,
    SourceRecord,
    SourceSnapshot,
    SubjectLifecycle,
)
from humanwire.organization_store import (
    GraphVersionConflict,
    ImportUnavailable,
    InMemoryOrganizationGraphRepository,
)

ORG_A = "org_01ARZ3NDEKTSV4RRFFQ69G5FAV"
ORG_B = "org_01ARZ3NDEKTSV4RRFFQ69G5FAW"
NOW = datetime(2026, 8, 18, 15, 0, tzinfo=UTC)


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


def source_record(
    ordinal: int,
    identity: str,
    fields: tuple[tuple[str, str], ...],
) -> SourceRecord:
    return SourceRecord(
        record_id=f"rec_{ordinal:026d}",
        source_ordinal=ordinal,
        source_identity=identity,
        fields=fields,
    )


def complete_snapshot(
    *,
    digest: str = "1" * 64,
    source_kind: str = "csv",
    reorder: bool = False,
) -> SourceSnapshot:
    records = (
        source_record(
            1,
            "directory/ada",
            (
                ("display_name", "Ada Lovelace"),
                ("kind", "human"),
                ("title", "Chief Executive"),
                ("unit_leader", "true"),
                ("unit_name", "Executive"),
            ),
        ),
        source_record(
            2,
            "directory/grace",
            (
                ("display_name", "Grace Hopper"),
                ("kind", "human"),
                ("manager_source_identity", "directory/ada"),
                ("title", "VP Engineering"),
                ("unit_leader", "true"),
                ("unit_name", "Engineering"),
                ("unit_parent_name", "Executive"),
            ),
        ),
        source_record(
            3,
            "directory/lin",
            (
                ("display_name", "Lin Chen"),
                ("kind", "human"),
                ("manager_source_identity", "directory/grace"),
                ("title", "Engineer"),
                ("unit_name", "Engineering"),
                ("unit_parent_name", "Executive"),
            ),
        ),
        source_record(
            4,
            "directory/sam",
            (
                ("display_name", "Sam Rivera"),
                ("kind", "human"),
                ("manager_source_identity", "directory/grace"),
                ("title", "Designer"),
                ("unit_name", "Engineering"),
                ("unit_parent_name", "Executive"),
            ),
        ),
    )
    if reorder:
        records = tuple(
            record.model_copy(update={"fields": tuple(reversed(record.fields))})
            for record in reversed(records)
        )
    return SourceSnapshot(
        snapshot_id="snap_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        organization_id=ORG_A,
        source_kind=source_kind,
        captured_at=NOW,
        records=records,
        semantic_digest=digest,
    )


@pytest.fixture
def service_setup():
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
    service = OrganizationImportService(repository=repository, clock=lambda: NOW)
    return service, repository, decisionos, owner_context


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


def test_draft_reconciles_every_source_row(service_setup) -> None:
    service, _repository, _decisionos, admin_context = service_setup
    draft = service.create_draft(admin_context, complete_snapshot())
    reconciliation = service.reconcile(admin_context, draft.import_id)
    assert reconciliation.source_count == 4
    assert reconciliation.normalized_count == 4
    assert reconciliation.rejected_count == 0
    assert sum(count for _lifecycle, count in reconciliation.lifecycle_counts) == 4
    assert reconciliation.blocking_codes == ()
    assert reconciliation.acknowledged_codes == ()


def test_rule_mapping_is_byte_identical_under_record_and_field_reordering() -> None:
    def build():
        decisionos = InMemoryDecisionOSRepository(
            identifiers=SequenceIdentifiers(), clock=lambda: NOW
        )
        owner = principal("firebase-owner-01")
        organization = decisionos.create_organization(owner, "Northstar Labs")
        context = decisionos.load_context(owner, organization.organization_id)
        service = OrganizationImportService(
            repository=InMemoryOrganizationGraphRepository(
                decisionos=decisionos, clock=lambda: NOW
            ),
            clock=lambda: NOW,
        )
        return service, context

    first_service, first_context = build()
    second_service, second_context = build()
    first = first_service.create_draft(first_context, complete_snapshot())
    second = second_service.create_draft(second_context, complete_snapshot(reorder=True))
    assert first.model_dump_json() == second.model_dump_json()
    assert (
        first_service.reconcile(first_context, first.import_id).model_dump_json()
        == second_service.reconcile(second_context, second.import_id).model_dump_json()
    )


def test_rule_mapper_does_not_guess_email_identity_authority_or_manager() -> None:
    snapshot = SourceSnapshot(
        snapshot_id="snap_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        organization_id=ORG_A,
        source_kind="csv",
        captured_at=NOW,
        records=(
            source_record(
                1,
                "row/one",
                (
                    ("display_name", "Ada Lovelace"),
                    ("email", "ada@example.invalid"),
                    ("title", "Approving manager"),
                ),
            ),
        ),
        semantic_digest="2" * 64,
    )
    candidate = RuleOrganizationMapper().map(
        snapshot,
        OrganizationImportService.empty_graph(ORG_A, NOW),
    )
    assert len(candidate.subjects) == 1
    assert candidate.subjects[0].source_identity == "row/one"
    assert candidate.subjects[0].lifecycle is SubjectLifecycle.NEEDS_REVIEW
    assert candidate.edges == ()
    assert candidate.authority_assignments == ()


def test_manual_correction_binds_digest_and_record_and_supersedes_old_draft(
    service_setup,
) -> None:
    service, _repository, _decisionos, context = service_setup
    ambiguous = SourceSnapshot(
        snapshot_id="snap_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        organization_id=ORG_A,
        source_kind="csv",
        captured_at=NOW,
        records=(source_record(1, "row/one", (("title", "Founder"),)),),
        semantic_digest="3" * 64,
    )
    draft = service.create_draft(context, ambiguous)
    request = ImportCorrectionRequest(
        import_id=draft.import_id,
        reviewed_digest=draft.semantic_digest,
        kind=ImportCorrectionKind.CORRECT_RECORD,
        source_record_ids=(ambiguous.records[0].record_id,),
        replacement_fields=(
            ("display_name", "Ada Lovelace"),
            ("kind", "human"),
            ("unit_leader", "true"),
            ("unit_name", "Executive"),
        ),
    )
    corrected = service.apply_correction(context, request)
    assert corrected.semantic_digest != draft.semantic_digest
    assert corrected.import_id != draft.import_id
    assert corrected.candidate.subjects[0].display_name == "Ada Lovelace"
    assert service.reconcile(context, corrected.import_id).blocking_codes == ()
    with pytest.raises(OrganizationImportStale, match="organization_import_stale"):
        service.commit(
            context,
            CommitImportRequest(
                import_id=draft.import_id,
                reviewed_digest=draft.semantic_digest,
            ),
        )
    with pytest.raises(OrganizationImportStale, match="organization_import_stale"):
        service.apply_correction(context, request)


def test_correction_contract_rejects_unbound_or_noncanonical_operations() -> None:
    with pytest.raises(ValidationError):
        ImportCorrectionRequest(
            import_id="imp_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            reviewed_digest="1" * 64,
            kind=ImportCorrectionKind.CORRECT_RECORD,
            source_record_ids=(
                "rec_00000000000000000000000002",
                "rec_00000000000000000000000001",
            ),
            replacement_fields=(("display_name", "Ada"),),
        )
    with pytest.raises(ValidationError):
        ImportCorrectionRequest(
            import_id="imp_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            reviewed_digest="1" * 64,
            kind=ImportCorrectionKind.MERGE_DUPLICATES,
            source_record_ids=("rec_00000000000000000000000001",),
            replacement_fields=(("private_email", "private@example.invalid"),),
        )


def test_duplicate_merge_requires_explicit_reviewed_operation(service_setup) -> None:
    service, _repository, _decisionos, context = service_setup
    duplicate = SourceSnapshot(
        snapshot_id="snap_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        organization_id=ORG_A,
        source_kind="csv",
        captured_at=NOW,
        records=(
            source_record(
                1,
                "row/ada-primary",
                (
                    ("display_name", "Ada Lovelace"),
                    ("duplicate_of", "row/ada-secondary"),
                    ("kind", "human"),
                ),
            ),
            source_record(
                2,
                "row/ada-secondary",
                (
                    ("display_name", "Ada L."),
                    ("duplicate_of", "row/ada-primary"),
                    ("kind", "human"),
                ),
            ),
        ),
        semantic_digest="4" * 64,
    )
    draft = service.create_draft(context, duplicate)
    assert service.reconcile(context, draft.import_id).blocking_codes == (
        "duplicate_identity",
        "needs_review",
        "unresolved_duplicate",
    )
    merged = service.apply_correction(
        context,
        ImportCorrectionRequest(
            import_id=draft.import_id,
            reviewed_digest=draft.semantic_digest,
            kind=ImportCorrectionKind.MERGE_DUPLICATES,
            source_record_ids=tuple(sorted(record.record_id for record in duplicate.records)),
            replacement_fields=(
                ("display_name", "Ada Lovelace"),
                ("kind", "human"),
            ),
        ),
    )
    reconciliation = service.reconcile(context, merged.import_id)
    assert reconciliation.source_count == 2
    assert reconciliation.normalized_count == 1
    assert reconciliation.rejected_count == 1
    assert reconciliation.blocking_codes == ()
    assert len(merged.candidate.subjects) == 1


def test_nonblocking_gaps_require_exact_acknowledgement_in_receipt(service_setup) -> None:
    service, _repository, _decisionos, context = service_setup
    leaderless = SourceSnapshot(
        snapshot_id="snap_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        organization_id=ORG_A,
        source_kind="csv",
        captured_at=NOW,
        records=(
            source_record(
                1,
                "row/one",
                (
                    ("display_name", "Ada Lovelace"),
                    ("kind", "human"),
                    ("unit_name", "Research"),
                ),
            ),
        ),
        semantic_digest="5" * 64,
    )
    draft = service.create_draft(context, leaderless)
    reconciliation = service.reconcile(context, draft.import_id)
    assert reconciliation.blocking_codes == ()
    assert reconciliation.acknowledged_codes == ("leaderless_team",)
    with pytest.raises(OrganizationImportReviewRequired):
        service.commit(
            context,
            CommitImportRequest(import_id=draft.import_id, reviewed_digest=draft.semantic_digest),
        )
    receipt = service.commit(
        context,
        CommitImportRequest(
            import_id=draft.import_id,
            reviewed_digest=draft.semantic_digest,
            acknowledged_codes=("leaderless_team",),
        ),
    )
    assert receipt.acknowledged_codes == ("leaderless_team",)


def test_missing_explicit_authority_blocks_commit(service_setup) -> None:
    service, _repository, _decisionos, context = service_setup
    snapshot = complete_snapshot()
    first = snapshot.records[0].model_copy(
        update={"fields": (*snapshot.records[0].fields, ("authority_required", "true"))}
    )
    snapshot = snapshot.model_copy(update={"records": (first, *snapshot.records[1:])})
    draft = service.create_draft(context, snapshot)
    assert service.reconcile(context, draft.import_id).blocking_codes == (
        "missing_authority",
    )
    with pytest.raises(OrganizationImportReviewRequired):
        service.commit(
            context,
            CommitImportRequest(import_id=draft.import_id, reviewed_digest=draft.semantic_digest),
        )


def test_explicit_authority_columns_are_mapped_without_reporting_inference() -> None:
    snapshot = complete_snapshot()
    first = snapshot.records[0].model_copy(
        update={
            "fields": (
                *snapshot.records[0].fields,
                ("authority_function", "approver"),
                ("authority_required", "true"),
                ("decision_type", "organization_import"),
            )
        }
    )
    snapshot = snapshot.model_copy(update={"records": (first, *snapshot.records[1:])})
    candidate = RuleOrganizationMapper().map(
        snapshot,
        OrganizationImportService.empty_graph(ORG_A, NOW),
    )
    assert len(candidate.authority_assignments) == 1
    ada = next(
        subject for subject in candidate.subjects if subject.source_identity == "directory/ada"
    )
    assert candidate.authority_assignments[0].subject_id == ada.subject_id


def control_ambiguity_snapshot(*, reorder: bool = False) -> SourceSnapshot:
    records = (
        source_record(
            1,
            "row/one",
            (
                ("authority_function", "approver"),
                ("display_name", "Ada One"),
                ("kind", "human"),
                ("unit_leader", "true"),
                ("unit_name", "Platform"),
                ("unit_parent_name", "Product"),
            ),
        ),
        source_record(
            2,
            "row/two",
            (
                ("display_name", "Ada Two"),
                ("kind", "human"),
                ("unit_leader", "true"),
                ("unit_name", "Platform"),
                ("unit_parent_name", "Engineering"),
            ),
        ),
        source_record(
            3,
            "row/three",
            (
                ("display_name", "Ada Three"),
                ("kind", "human"),
                ("unit_leader", "yes"),
                ("unit_name", "Platform"),
            ),
        ),
        source_record(
            4,
            "row/four",
            (
                ("authority_required", "sometimes"),
                ("display_name", "Ada Four"),
                ("kind", "human"),
                ("unit_name", "Operations"),
            ),
        ),
    )
    if reorder:
        records = tuple(
            record.model_copy(update={"fields": tuple(reversed(record.fields))})
            for record in reversed(records)
        )
    return SourceSnapshot(
        snapshot_id="snap_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        organization_id=ORG_A,
        source_kind="csv",
        captured_at=NOW,
        records=records,
        semantic_digest="6" * 64,
    )


def test_control_ambiguity_marks_all_participants_and_selects_no_winner(
    service_setup,
) -> None:
    service, _repository, _decisionos, context = service_setup
    draft = service.create_draft(context, control_ambiguity_snapshot())
    reconciliation = service.reconcile(context, draft.import_id)

    assert reconciliation.blocking_codes == (
        "conflicting_unit_parent",
        "incomplete_authority",
        "invalid_control_value",
        "multiple_unit_leaders",
        "needs_review",
    )
    assert all(
        subject.lifecycle is SubjectLifecycle.NEEDS_REVIEW
        for subject in draft.candidate.subjects
    )
    platform = next(unit for unit in draft.candidate.units if unit.name == "Platform")
    assert platform.parent_unit_id is None
    assert platform.leader_subject_id is None


def test_control_ambiguity_is_byte_identical_when_inputs_are_shuffled() -> None:
    def build(snapshot):
        decisionos = InMemoryDecisionOSRepository(
            identifiers=SequenceIdentifiers(), clock=lambda: NOW
        )
        owner = principal("firebase-owner-01")
        organization = decisionos.create_organization(owner, "Northstar Labs")
        context = decisionos.load_context(owner, organization.organization_id)
        service = OrganizationImportService(
            repository=InMemoryOrganizationGraphRepository(
                decisionos=decisionos, clock=lambda: NOW
            ),
            clock=lambda: NOW,
        )
        draft = service.create_draft(context, snapshot)
        return draft, service.reconcile(context, draft.import_id)

    first = build(control_ambiguity_snapshot())
    second = build(control_ambiguity_snapshot(reorder=True))

    assert first[0].model_dump_json() == second[0].model_dump_json()
    assert first[1].model_dump_json() == second[1].model_dump_json()


def test_stale_source_and_stale_graph_fail_closed(service_setup) -> None:
    service, _repository, _decisionos, context = service_setup
    stale_source = service.create_draft(context, complete_snapshot(digest="1" * 64))
    service.create_draft(context, complete_snapshot(digest="2" * 64))
    with pytest.raises(OrganizationImportStale):
        service.commit(
            context,
            CommitImportRequest(
                import_id=stale_source.import_id,
                reviewed_digest=stale_source.semantic_digest,
            ),
        )
    first = service.create_draft(
        context,
        complete_snapshot(digest="3" * 64, source_kind="json"),
    )
    second = service.create_draft(
        context,
        complete_snapshot(digest="4" * 64, source_kind="xlsx"),
    )
    service.commit(
        context,
        CommitImportRequest(import_id=first.import_id, reviewed_digest=first.semantic_digest),
    )
    with pytest.raises(GraphVersionConflict):
        service.commit(
            context,
            CommitImportRequest(import_id=second.import_id, reviewed_digest=second.semantic_digest),
        )


def test_two_service_instances_share_durable_source_lineage(service_setup) -> None:
    first_service, repository, _decisionos, context = service_setup
    second_service = OrganizationImportService(repository=repository, clock=lambda: NOW)
    first = first_service.create_draft(context, complete_snapshot(digest="1" * 64))
    second_service.create_draft(context, complete_snapshot(digest="2" * 64))

    with pytest.raises(OrganizationImportStale, match="organization_import_stale"):
        first_service.commit(
            context,
            CommitImportRequest(
                import_id=first.import_id,
                reviewed_digest=first.semantic_digest,
            ),
        )


def test_old_service_cannot_correct_chain_advanced_by_new_service(service_setup) -> None:
    first_service, repository, _decisionos, context = service_setup
    second_service = OrganizationImportService(repository=repository, clock=lambda: NOW)
    first = first_service.create_draft(context, complete_snapshot(digest="1" * 64))
    second_service.create_draft(context, complete_snapshot(digest="2" * 64))

    with pytest.raises(OrganizationImportStale, match="organization_import_stale"):
        first_service.apply_correction(
            context,
            ImportCorrectionRequest(
                import_id=first.import_id,
                reviewed_digest=first.semantic_digest,
                kind=ImportCorrectionKind.CORRECT_RECORD,
                source_record_ids=(first.source_snapshot.records[0].record_id,),
                replacement_fields=(("display_name", "Ada Corrected"),),
            ),
        )


def test_exact_committed_retry_survives_newer_source_in_same_service(
    service_setup,
) -> None:
    service, _repository, _decisionos, context = service_setup
    first = service.create_draft(context, complete_snapshot(digest="1" * 64))
    request = CommitImportRequest(
        import_id=first.import_id,
        reviewed_digest=first.semantic_digest,
    )
    receipt = service.commit(context, request)
    service.create_draft(context, complete_snapshot(digest="2" * 64))

    assert service.commit(context, request) == receipt


def test_wrong_tenant_and_permission_fail_closed(service_setup) -> None:
    service, _repository, decisionos, owner_context = service_setup
    viewer_context = make_member(
        decisionos,
        owner_context,
        uid="firebase-viewer-01",
        role=DecisionOSRole.VIEWER,
    )
    with pytest.raises(DecisionOSAuthorizationDenied):
        service.create_draft(viewer_context, complete_snapshot())
    owner_b = principal("firebase-owner-02")
    org_b = decisionos.create_organization(owner_b, "Other Company")
    context_b = decisionos.load_context(owner_b, org_b.organization_id)
    with pytest.raises(ImportUnavailable):
        service.create_draft(context_b, complete_snapshot())


class TimeoutMapper:
    def __init__(self) -> None:
        self.provider_state: list[str] = []

    def map(self, snapshot, current_graph):
        raise TimeoutError("PRIVATE-MAPPER-TIMEOUT")


class InvalidMapper:
    def map(self, snapshot, current_graph):
        return {"private": "PRIVATE-INVALID-MAPPER"}


class PrivateFailureMapper:
    def map(self, snapshot, current_graph):
        private_value = "PRIVATE-MAPPER-TRACE-SENTINEL"
        raise RuntimeError(private_value)


class SleepingMapper:
    def map(self, snapshot, current_graph):
        time.sleep(30)
        return RuleOrganizationMapper().map(snapshot, current_graph)


class NonPicklableMapper:
    def __init__(self) -> None:
        self.callback = lambda: None

    def map(self, snapshot, current_graph):
        return RuleOrganizationMapper().map(snapshot, current_graph)


class EmptyMapper:
    def map(self, snapshot, current_graph):
        candidate = RuleOrganizationMapper().map(snapshot, current_graph)
        return candidate.model_copy(
            update={"subjects": (), "units": (), "edges": (), "authority_assignments": ()}
        )


class PartialMapper:
    def map(self, snapshot, current_graph):
        candidate = RuleOrganizationMapper().map(snapshot, current_graph)
        return candidate.model_copy(
            update={
                "subjects": candidate.subjects[:1],
                "units": (),
                "edges": (),
                "authority_assignments": (),
            }
        )


class ExtraSubjectMapper:
    def map(self, snapshot, current_graph):
        candidate = RuleOrganizationMapper().map(snapshot, current_graph)
        extra = candidate.subjects[0].model_copy(
            update={
                "subject_id": "sub_01ARZ3NDEKTSV4RRFFQ69G5FZZ",
                "source_identity": "not/in/source",
            }
        )
        return candidate.model_copy(update={"subjects": (*candidate.subjects, extra)})


class DuplicateSourceMapper:
    def map(self, snapshot, current_graph):
        candidate = RuleOrganizationMapper().map(snapshot, current_graph)
        duplicate = candidate.subjects[0].model_copy(
            update={"subject_id": "sub_01ARZ3NDEKTSV4RRFFQ69G5FZZ"}
        )
        return candidate.model_copy(update={"subjects": (*candidate.subjects, duplicate)})


class ExplodingCandidate(OrganizationGraphCandidate):
    def model_dump(self, *args, **kwargs):
        raise RuntimeError("PRIVATE-CANDIDATE-SERIALIZATION")


class HostileCandidateMapper:
    def map(self, snapshot, current_graph):
        candidate = RuleOrganizationMapper().map(snapshot, current_graph)
        return ExplodingCandidate.model_construct(**candidate.__dict__)


@pytest.mark.parametrize(
    "mapper",
    [TimeoutMapper(), InvalidMapper(), PrivateFailureMapper(), HostileCandidateMapper()],
)
def test_mapper_timeout_failure_and_invalid_output_use_rule_fallback_without_leak(
    service_setup,
    mapper,
) -> None:
    _service, repository, _decisionos, context = service_setup
    service = OrganizationImportService(
        repository=repository,
        mapper=mapper,
        clock=lambda: NOW,
    )
    draft = service.create_draft(context, complete_snapshot())
    assert len(draft.candidate.subjects) == 4
    assert service.reconcile(context, draft.import_id).blocking_codes == ()
    assert "PRIVATE" not in repr(service)
    if isinstance(mapper, TimeoutMapper):
        assert mapper.provider_state == []


def test_uncooperative_mapper_has_hard_deadline_and_leaves_no_worker(service_setup) -> None:
    _service, repository, _decisionos, context = service_setup
    before_processes = {process.pid for process in multiprocessing.active_children()}
    before_threads = {thread.ident for thread in threading.enumerate()}
    service = OrganizationImportService(
        repository=repository,
        mapper=SleepingMapper(),
        mapper_timeout_seconds=0.2,
        clock=lambda: NOW,
    )

    started = time.perf_counter()
    draft = service.create_draft(context, complete_snapshot())
    elapsed = time.perf_counter() - started

    assert elapsed < 2
    assert len(draft.candidate.subjects) == 4
    assert {
        process.pid for process in multiprocessing.active_children()
    } <= before_processes
    assert {thread.ident for thread in threading.enumerate()} <= before_threads


def test_non_picklable_mapper_configuration_falls_back_fixed_safe(service_setup) -> None:
    _service, repository, _decisionos, context = service_setup
    service = OrganizationImportService(
        repository=repository,
        mapper=NonPicklableMapper(),
        mapper_timeout_seconds=0.2,
        clock=lambda: NOW,
    )

    draft = service.create_draft(context, complete_snapshot())

    assert len(draft.candidate.subjects) == 4


@pytest.mark.parametrize("mapper", [EmptyMapper(), PartialMapper()])
def test_mapper_must_cover_every_active_row_or_rule_fallback_preserves_all(
    service_setup,
    mapper,
) -> None:
    _service, repository, _decisionos, context = service_setup
    service = OrganizationImportService(
        repository=repository,
        mapper=mapper,
        mapper_timeout_seconds=2,
        clock=lambda: NOW,
    )

    draft = service.create_draft(context, complete_snapshot())
    reconciliation = service.reconcile(context, draft.import_id)
    receipt = service.commit(
        context,
        CommitImportRequest(
            import_id=draft.import_id,
            reviewed_digest=draft.semantic_digest,
        ),
    )

    assert reconciliation.normalized_count == 4
    assert reconciliation.rejected_count == 0
    assert receipt.committed_subject_count == 4
    assert len(repository.load_graph(context).subjects) == 4


class CyclicMapper:
    def map(self, snapshot, current_graph):
        return RuleOrganizationMapper().map(snapshot, current_graph)


def test_cyclic_mapper_report_is_reviewable_but_not_committable(service_setup) -> None:
    _service, repository, _decisionos, context = service_setup
    service = OrganizationImportService(
        repository=repository,
        mapper=CyclicMapper(),
        clock=lambda: NOW,
    )
    snapshot = complete_snapshot()
    ada = snapshot.records[0].model_copy(
        update={
            "fields": (
                *snapshot.records[0].fields,
                ("manager_source_identity", "directory/grace"),
            )
        }
    )
    snapshot = snapshot.model_copy(update={"records": (ada, *snapshot.records[1:])})
    draft = service.create_draft(context, snapshot)
    assert service.reconcile(context, draft.import_id).blocking_codes == (
        "reporting_cycle",
    )
    with pytest.raises(OrganizationImportReviewRequired):
        service.commit(
            context,
            CommitImportRequest(import_id=draft.import_id, reviewed_digest=draft.semantic_digest),
        )


def test_exact_request_retry_and_concurrent_admin_commit_are_idempotent(service_setup) -> None:
    service, repository, _decisionos, context = service_setup
    draft = service.create_draft(context, complete_snapshot())
    request = CommitImportRequest(
        import_id=draft.import_id,
        reviewed_digest=draft.semantic_digest,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = tuple(executor.map(lambda _index: service.commit(context, request), range(2)))
    assert receipts[0] == receipts[1]
    assert service.commit(context, request) == receipts[0]
    assert repository.load_graph(context).version == 1


def test_import_commit_has_no_reachable_invitation_transport(
    service_setup,
    monkeypatch,
) -> None:
    service, _repository, decisionos, context = service_setup

    def fail_invitation(*_args, **_kwargs):
        raise AssertionError("import reached invitation transport")

    monkeypatch.setattr(decisionos, "create_invitation", fail_invitation)
    assert "invitation" not in inspect.signature(OrganizationImportService).parameters
    draft = service.create_draft(context, complete_snapshot())
    service.commit(
        context,
        CommitImportRequest(import_id=draft.import_id, reviewed_digest=draft.semantic_digest),
    )


def test_mapper_exception_trace_and_source_values_do_not_enter_public_errors(
    service_setup,
) -> None:
    _service, repository, _decisionos, context = service_setup
    service = OrganizationImportService(
        repository=repository,
        mapper=PrivateFailureMapper(),
        clock=lambda: NOW,
    )
    snapshot = complete_snapshot()
    private_source = "private-person@example.invalid"
    first = snapshot.records[0].model_copy(
        update={"fields": (*snapshot.records[0].fields, ("email", private_source))}
    )
    snapshot = snapshot.model_copy(update={"records": (first, *snapshot.records[1:])})
    draft = service.create_draft(context, snapshot)
    with pytest.raises(OrganizationImportReviewRequired) as captured:
        service.commit(
            context,
            CommitImportRequest(
                import_id=draft.import_id,
                reviewed_digest="0" * 64,
            ),
        )
    rendered = exception_graph_text(captured.value)
    assert private_source not in rendered
    assert "PRIVATE-MAPPER-TRACE-SENTINEL" not in rendered
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize("mapper", [ExtraSubjectMapper(), DuplicateSourceMapper()])
def test_external_mapper_candidate_must_cover_only_exact_snapshot_rows(
    service_setup,
    mapper,
) -> None:
    _service, repository, _decisionos, context = service_setup
    service = OrganizationImportService(
        repository=repository,
        mapper=mapper,
        clock=lambda: NOW,
    )
    draft = service.create_draft(context, complete_snapshot())
    assert len(draft.candidate.subjects) == 4
    source_identities = tuple(
        subject.source_identity for subject in draft.candidate.subjects
    )
    assert "not/in/source" not in source_identities
    assert len(source_identities) == len(set(source_identities))
