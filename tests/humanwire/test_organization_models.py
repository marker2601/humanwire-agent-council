from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from humanwire.organization_models import (
    AuthorityAssignment,
    AuthorityFunction,
    OrganizationEdge,
    OrganizationEdgeKind,
    OrganizationGraph,
    OrganizationProjection,
    OrganizationSubject,
    OrganizationSubjectKind,
    SourceRecord,
    SourceSnapshot,
    SubjectLifecycle,
)

ORG = "org_01K00000000000000000000000"
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
        OrganizationEdge(
            edge_id="edge_01K00000000000000000000000",
            organization_id=ORG,
            kind=OrganizationEdgeKind.REPORTS_TO,
            source_subject_id=SUBJECT,
            target_subject_id=SUBJECT_TWO,
            decision_function=AuthorityFunction.APPROVER,
        )


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
