from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from humanwire.organization_models import (
    AuthorityAssignment,
    AuthorityDecision,
    AuthorityFunction,
    AuthorityRequest,
    CommitImportRequest,
    ImportDraft,
    ImportReceipt,
    ImportReconciliation,
    OrganizationEdge,
    OrganizationEdgeKind,
    OrganizationGraph,
    OrganizationGraphCandidate,
    OrganizationProjection,
    OrganizationProjectionSubject,
    OrganizationSubject,
    OrganizationSubjectKind,
    OrganizationUnit,
    SourceRecord,
    SourceSnapshot,
    SubjectLifecycle,
)

ORG = "org_01K00000000000000000000000"
OTHER_ORG = "org_01K00000000000000000000001"
SUBJECT = "sub_01K00000000000000000000000"
SUBJECT_TWO = "sub_01K00000000000000000000001"
NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def human_subject(**changes: object) -> OrganizationSubject:
    values: dict[str, object] = {
        "subject_id": SUBJECT,
        "organization_id": ORG,
        "kind": OrganizationSubjectKind.HUMAN,
        "lifecycle": SubjectLifecycle.DIRECTORY_ONLY,
        "display_name": "Avery Morgan",
        "source_identity": "m365:user-42",
    }
    values.update(changes)
    return OrganizationSubject(**values)


def source_record(**changes: object) -> SourceRecord:
    values: dict[str, object] = {
        "record_id": "rec_01K00000000000000000000000",
        "source_ordinal": 1,
        "source_identity": "m365:user-42",
        "fields": (("display_name", "Avery Morgan"),),
    }
    values.update(changes)
    return SourceRecord(**values)


def reporting_edge(**changes: object) -> OrganizationEdge:
    values: dict[str, object] = {
        "edge_id": "edge_01K00000000000000000000000",
        "organization_id": ORG,
        "kind": OrganizationEdgeKind.REPORTS_TO,
        "source_subject_id": SUBJECT,
        "target_subject_id": SUBJECT_TWO,
    }
    values.update(changes)
    return OrganizationEdge(**values)


def organization_unit(**changes: object) -> OrganizationUnit:
    values: dict[str, object] = {
        "unit_id": "unit_01K00000000000000000000000",
        "organization_id": ORG,
        "name": "Product",
    }
    values.update(changes)
    return OrganizationUnit(**values)


def authority_assignment(**changes: object) -> AuthorityAssignment:
    values: dict[str, object] = {
        "assignment_id": "auth_01K00000000000000000000000",
        "organization_id": ORG,
        "subject_id": SUBJECT,
        "decision_type": "fundraising_readiness",
        "function": AuthorityFunction.APPROVER,
        "effective_from": NOW,
    }
    values.update(changes)
    return AuthorityAssignment(**values)


def source_snapshot(**changes: object) -> SourceSnapshot:
    values: dict[str, object] = {
        "snapshot_id": "snap_01K00000000000000000000000",
        "organization_id": ORG,
        "source_kind": "csv",
        "captured_at": NOW,
        "records": (source_record(),),
        "semantic_digest": "a" * 64,
    }
    values.update(changes)
    return SourceSnapshot(**values)


def graph_candidate(**changes: object) -> OrganizationGraphCandidate:
    values: dict[str, object] = {
        "organization_id": ORG,
        "source_snapshot_id": "snap_01K00000000000000000000000",
        "subjects": (human_subject(),),
    }
    values.update(changes)
    return OrganizationGraphCandidate(**values)


def import_draft(**changes: object) -> ImportDraft:
    values: dict[str, object] = {
        "import_id": "imp_01K00000000000000000000000",
        "organization_id": ORG,
        "source_snapshot": source_snapshot(),
        "candidate": graph_candidate(),
        "base_graph_version": 0,
        "semantic_digest": "b" * 64,
        "created_at": NOW,
    }
    values.update(changes)
    return ImportDraft(**values)


def import_reconciliation(**changes: object) -> ImportReconciliation:
    values: dict[str, object] = {
        "import_id": "imp_01K00000000000000000000000",
        "organization_id": ORG,
        "source_count": 1,
        "normalized_count": 1,
        "rejected_count": 0,
        "lifecycle_counts": ((SubjectLifecycle.DIRECTORY_ONLY, 1),),
    }
    values.update(changes)
    return ImportReconciliation(**values)


def import_receipt(**changes: object) -> ImportReceipt:
    values: dict[str, object] = {
        "receipt_id": "rcp_01K00000000000000000000000",
        "import_id": "imp_01K00000000000000000000000",
        "organization_id": ORG,
        "source_snapshot_id": "snap_01K00000000000000000000000",
        "source_snapshot_digest": "a" * 64,
        "graph_version": 1,
        "committed_subject_count": 1,
        "committed_at": NOW,
        "committed_by_uid": "firebase-user",
    }
    values.update(changes)
    return ImportReceipt(**values)


def test_imported_human_is_not_a_membership() -> None:
    subject = human_subject()

    assert subject.member_uid is None
    assert not hasattr(subject, "role")


def test_ai_specialist_rejects_human_lifecycle() -> None:
    with pytest.raises(ValidationError):
        human_subject(
            kind=OrganizationSubjectKind.AI_SPECIALIST,
            lifecycle=SubjectLifecycle.INVITED,
            display_name="Risk Challenger",
            source_identity=None,
            specialist_key="risk_challenger",
        )


def test_reporting_edge_cannot_encode_approval() -> None:
    with pytest.raises(ValidationError):
        reporting_edge(decision_function=AuthorityFunction.APPROVER)

    edge = reporting_edge()
    assert not hasattr(edge, "decision_function")
    assert "decision_function" not in edge.model_dump(mode="json")


@pytest.mark.parametrize(
    "changes",
    [
        {"unexpected": "field"},
        {"kind": "HUMAN"},
        {"display_name": "Avery\nMorgan"},
        {"display_name": "Avery\u00a0Morgan"},
        {"subject_id": "subject_01K00000000000000000000000"},
    ],
)
def test_subject_fails_closed_for_untrusted_or_noncanonical_values(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        human_subject(**changes)


def test_ai_and_service_records_cannot_bind_a_firebase_member() -> None:
    with pytest.raises(ValidationError):
        human_subject(
            kind=OrganizationSubjectKind.SERVICE,
            lifecycle=SubjectLifecycle.DIRECTORY_ONLY,
            source_identity=None,
            member_uid="firebase-user",
        )


def test_snapshot_rejects_duplicate_source_identities() -> None:
    with pytest.raises(ValidationError):
        SourceSnapshot(
            snapshot_id="snap_01K00000000000000000000000",
            organization_id=ORG,
            source_kind="csv",
            captured_at=NOW,
            records=(source_record(), source_record(record_id="rec_01K00000000000000000000001")),
            semantic_digest="a" * 64,
        )


def test_source_record_normalizes_canonically_equivalent_values_for_json() -> None:
    record = source_record(fields=(("display_name", "Cafe\u0301"),))

    assert record.fields == (("display_name", "Café"),)
    assert record.model_dump(mode="json")["fields"] == [["display_name", "Café"]]


@pytest.mark.parametrize(
    ("lifecycle", "member_uid", "valid"),
    [
        (SubjectLifecycle.DRAFT_IMPORTED, None, True),
        (SubjectLifecycle.DRAFT_IMPORTED, "firebase-user", False),
        (SubjectLifecycle.DIRECTORY_ONLY, None, True),
        (SubjectLifecycle.DIRECTORY_ONLY, "firebase-user", False),
        (SubjectLifecycle.INVITED, None, True),
        (SubjectLifecycle.INVITED, "firebase-user", False),
        (SubjectLifecycle.ACTIVE, None, False),
        (SubjectLifecycle.ACTIVE, "firebase-user", True),
        (SubjectLifecycle.SUSPENDED, None, True),
        (SubjectLifecycle.SUSPENDED, "firebase-user", True),
        (SubjectLifecycle.NEEDS_REVIEW, None, True),
        (SubjectLifecycle.NEEDS_REVIEW, "firebase-user", False),
    ],
)
def test_human_lifecycle_controls_member_uid_binding(
    lifecycle: SubjectLifecycle,
    member_uid: str | None,
    valid: bool,
) -> None:
    if valid:
        subject = human_subject(lifecycle=lifecycle, member_uid=member_uid)
        assert subject.member_uid == member_uid
    else:
        with pytest.raises(ValidationError):
            human_subject(lifecycle=lifecycle, member_uid=member_uid)


def test_authority_assignment_requires_aware_effective_time() -> None:
    with pytest.raises(ValidationError):
        AuthorityAssignment(
            assignment_id="auth_01K00000000000000000000000",
            organization_id=ORG,
            subject_id=SUBJECT,
            decision_type="fundraising_readiness",
            function=AuthorityFunction.APPROVER,
            effective_from=datetime(2026, 8, 18, 12, 0),  # noqa: DTZ001
        )


def test_graph_rejects_duplicate_subject_identities() -> None:
    with pytest.raises(ValidationError):
        OrganizationGraph(
            organization_id=ORG,
            version=1,
            subjects=(human_subject(), human_subject(subject_id=SUBJECT_TWO)),
            units=(),
            edges=(),
            authority_assignments=(),
            created_at=NOW,
        )


def test_projection_rejects_member_and_source_identity_leakage() -> None:
    with pytest.raises(ValidationError):
        OrganizationProjection(
            organization_id=ORG,
            graph_version=1,
            subjects=(human_subject(),),
            generated_at=NOW,
        )


def test_projection_uses_a_redacted_subject_representation() -> None:
    subject = OrganizationProjectionSubject(
        subject_id=SUBJECT,
        kind=OrganizationSubjectKind.HUMAN,
        lifecycle=SubjectLifecycle.ACTIVE,
        display_name="Avery Morgan",
    )
    projection = OrganizationProjection(
        organization_id=ORG,
        graph_version=1,
        subjects=(subject,),
        generated_at=NOW,
    )

    assert projection.model_dump(mode="json")["subjects"] == [
        {
            "subject_id": SUBJECT,
            "kind": "human",
            "lifecycle": "active",
            "display_name": "Avery Morgan",
            "unit_id": None,
            "title": None,
        }
    ]
    with pytest.raises(ValidationError):
        OrganizationProjectionSubject(
            subject_id=SUBJECT,
            kind=OrganizationSubjectKind.HUMAN,
            lifecycle=SubjectLifecycle.ACTIVE,
            display_name="Avery Morgan",
            member_uid="firebase-user",
        )


def test_remaining_contracts_are_immutable_with_stable_json_dumps() -> None:
    snapshot = source_snapshot()
    candidate = graph_candidate()
    contracts = (
        organization_unit(),
        AuthorityRequest(
            organization_id=ORG,
            subject_id=SUBJECT,
            decision_type="fundraising_readiness",
            function=AuthorityFunction.APPROVER,
            occurred_at=NOW,
        ),
        AuthorityDecision(
            allowed=False,
            reason="authority_missing",
        ),
        candidate,
        import_draft(source_snapshot=snapshot, candidate=candidate),
        import_reconciliation(),
        CommitImportRequest(
            import_id="imp_01K00000000000000000000000",
            reviewed_digest="b" * 64,
        ),
        import_receipt(),
    )
    json_contracts = tuple(contract.model_dump(mode="json") for contract in contracts)

    assert [
        json_contracts[0]["unit_id"],
        json_contracts[1]["occurred_at"],
        json_contracts[2],
        json_contracts[3]["source_snapshot_id"],
        json_contracts[4]["import_id"],
        json_contracts[5]["lifecycle_counts"],
        json_contracts[6]["reviewed_digest"],
        json_contracts[7]["receipt_id"],
    ] == [
        "unit_01K00000000000000000000000",
        "2026-08-18T12:00:00Z",
        {"allowed": False, "reason": "authority_missing", "assignment_id": None},
        "snap_01K00000000000000000000000",
        "imp_01K00000000000000000000000",
        [["directory_only", 1]],
        "b" * 64,
        "rcp_01K00000000000000000000000",
    ]
    assert json_contracts == tuple(
        contract.model_dump(mode="json") for contract in contracts
    )
    for contract in contracts:
        with pytest.raises(ValidationError):
            setattr(contract, "unexpected", "field")  # noqa: B010


@pytest.mark.parametrize(
    "builder",
    [
        lambda: organization_unit(unit_id="unit_invalid"),
        lambda: reporting_edge(edge_id="edge_invalid"),
        lambda: authority_assignment(assignment_id="auth_invalid"),
        lambda: source_record(record_id="rec_invalid"),
        lambda: source_snapshot(snapshot_id="snap_invalid"),
        lambda: graph_candidate(source_snapshot_id="snap_invalid"),
        lambda: import_draft(import_id="imp_invalid"),
        lambda: import_reconciliation(import_id="imp_invalid"),
        lambda: CommitImportRequest(import_id="imp_invalid", reviewed_digest="b" * 64),
        lambda: import_receipt(receipt_id="rcp_invalid"),
        lambda: AuthorityDecision(
            allowed=True,
            assignment_id="auth_invalid",
        ),
        lambda: AuthorityRequest(
            organization_id="org_invalid",
            subject_id=SUBJECT,
            decision_type="fundraising_readiness",
            function=AuthorityFunction.APPROVER,
            occurred_at=NOW,
        ),
        lambda: OrganizationGraph(
            organization_id="org_invalid",
            version=1,
            created_at=NOW,
        ),
        lambda: OrganizationProjectionSubject(
            subject_id="sub_invalid",
            kind=OrganizationSubjectKind.HUMAN,
            lifecycle=SubjectLifecycle.ACTIVE,
            display_name="Avery Morgan",
        ),
        lambda: OrganizationProjection(
            organization_id="org_invalid",
            graph_version=1,
            generated_at=NOW,
        ),
    ],
)
def test_remaining_contracts_reject_invalid_id_patterns(builder: object) -> None:
    with pytest.raises(ValidationError):
        builder()  # type: ignore[operator]


@pytest.mark.parametrize(
    "builder",
    [
        lambda: SourceSnapshot(
            snapshot_id="snap_01K00000000000000000000000",
            organization_id=ORG,
            source_kind="csv",
            captured_at=datetime(2026, 8, 18, 12, 0),  # noqa: DTZ001
            records=(source_record(),),
            semantic_digest="a" * 64,
        ),
        lambda: OrganizationGraph(
            organization_id=ORG,
            version=1,
            created_at=datetime(2026, 8, 18, 12, 0),  # noqa: DTZ001
        ),
        lambda: import_draft(created_at=datetime(2026, 8, 18, 12, 0)),  # noqa: DTZ001
        lambda: AuthorityRequest(
            organization_id=ORG,
            subject_id=SUBJECT,
            decision_type="fundraising_readiness",
            function=AuthorityFunction.APPROVER,
            occurred_at=datetime(2026, 8, 18, 12, 0),  # noqa: DTZ001
        ),
        lambda: import_receipt(committed_at=datetime(2026, 8, 18, 12, 0)),  # noqa: DTZ001
        lambda: OrganizationProjection(
            organization_id=ORG,
            graph_version=1,
            generated_at=datetime(2026, 8, 18, 12, 0),  # noqa: DTZ001
        ),
    ],
)
def test_every_datetime_contract_rejects_naive_values(builder: object) -> None:
    with pytest.raises(ValidationError):
        builder()  # type: ignore[operator]


def test_nested_import_records_reject_cross_tenant_and_duplicate_tuples() -> None:
    with pytest.raises(ValidationError):
        import_draft(source_snapshot=source_snapshot(organization_id=OTHER_ORG))
    with pytest.raises(ValidationError):
        graph_candidate(subjects=(human_subject(organization_id=OTHER_ORG),))
    with pytest.raises(ValidationError):
        source_record(
            fields=(("display_name", "Avery Morgan"), ("display_name", "Avery M.")),
        )
    with pytest.raises(ValidationError):
        import_reconciliation(
            lifecycle_counts=(
                (SubjectLifecycle.DIRECTORY_ONLY, 1),
                (SubjectLifecycle.DIRECTORY_ONLY, 0),
            ),
        )
    with pytest.raises(ValidationError):
        CommitImportRequest(
            import_id="imp_01K00000000000000000000000",
            reviewed_digest="b" * 64,
            acknowledged_codes=("leaderless_team", "leaderless_team"),
        )


def test_import_receipt_defaults_to_no_acknowledgements_and_requires_canonical_codes() -> None:
    assert import_receipt().acknowledged_codes == ()
    assert import_receipt(
        acknowledged_codes=("leaderless_team", "unassigned_subject"),
    ).acknowledged_codes == ("leaderless_team", "unassigned_subject")

    with pytest.raises(ValidationError):
        import_receipt(acknowledged_codes=("unassigned_subject", "leaderless_team"))
    with pytest.raises(ValidationError):
        import_receipt(acknowledged_codes=("leaderless_team", "leaderless_team"))


def test_import_draft_lineage_is_optional_but_cannot_supersede_itself() -> None:
    assert import_draft().supersedes_import_id is None
    prior = "imp_01K00000000000000000000001"
    assert import_draft(supersedes_import_id=prior).supersedes_import_id == prior

    with pytest.raises(ValidationError):
        import_draft(
            supersedes_import_id="imp_01K00000000000000000000000",
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"units": (organization_unit(), organization_unit())},
        {"edges": (reporting_edge(), reporting_edge())},
        {
            "authority_assignments": (
                authority_assignment(),
                authority_assignment(),
            )
        },
    ],
)
def test_projection_rejects_duplicate_nested_record_ids(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        OrganizationProjection(
            organization_id=ORG,
            graph_version=1,
            generated_at=NOW,
            **changes,
        )


def test_redacted_projection_subject_rejects_ai_invitation_lifecycle() -> None:
    with pytest.raises(ValidationError):
        OrganizationProjectionSubject(
            subject_id=SUBJECT,
            kind=OrganizationSubjectKind.AI_SPECIALIST,
            lifecycle=SubjectLifecycle.INVITED,
            display_name="Risk Challenger",
        )
