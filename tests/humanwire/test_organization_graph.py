from __future__ import annotations

from datetime import UTC, datetime, timedelta

from humanwire.organization_graph import (
    authority_for,
    project_team_graph,
    validate_organization_graph,
)
from humanwire.organization_models import (
    AuthorityAssignment,
    AuthorityFunction,
    AuthorityRequest,
    OrganizationEdge,
    OrganizationEdgeKind,
    OrganizationGraph,
    OrganizationSubject,
    OrganizationSubjectKind,
    OrganizationUnit,
    SubjectLifecycle,
)

ORG = "org_01K00000000000000000000000"
OTHER_ORG = "org_01K00000000000000000000001"
NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
AVERY = "sub_01K00000000000000000000000"
BLAIR = "sub_01K00000000000000000000001"
CASEY = "sub_01K00000000000000000000002"
AI = "sub_01K00000000000000000000003"
PRODUCT = "unit_01K00000000000000000000000"
PLATFORM = "unit_01K00000000000000000000001"


def subject(subject_id: str = AVERY, **changes: object) -> OrganizationSubject:
    values: dict[str, object] = {
        "subject_id": subject_id,
        "organization_id": ORG,
        "kind": OrganizationSubjectKind.HUMAN,
        "lifecycle": SubjectLifecycle.DIRECTORY_ONLY,
        "display_name": f"Subject {subject_id[-1]}",
    }
    values.update(changes)
    return OrganizationSubject(**values)


def unit(unit_id: str = PRODUCT, **changes: object) -> OrganizationUnit:
    values: dict[str, object] = {
        "unit_id": unit_id,
        "organization_id": ORG,
        "name": f"Unit {unit_id[-1]}",
    }
    values.update(changes)
    return OrganizationUnit(**values)


def edge(edge_id: str, **changes: object) -> OrganizationEdge:
    values: dict[str, object] = {
        "edge_id": edge_id,
        "organization_id": ORG,
        "kind": OrganizationEdgeKind.REPORTS_TO,
        "source_subject_id": AVERY,
        "target_subject_id": BLAIR,
    }
    values.update(changes)
    return OrganizationEdge(**values)


def assignment(assignment_id: str, **changes: object) -> AuthorityAssignment:
    values: dict[str, object] = {
        "assignment_id": assignment_id,
        "organization_id": ORG,
        "subject_id": AVERY,
        "decision_type": "fundraising_readiness",
        "function": AuthorityFunction.APPROVER,
        "effective_from": NOW - timedelta(days=1),
    }
    values.update(changes)
    return AuthorityAssignment(**values)


def graph(**changes: object) -> OrganizationGraph:
    values: dict[str, object] = {
        "organization_id": ORG,
        "version": 3,
        "subjects": (subject(), subject(BLAIR), subject(CASEY)),
        "units": (unit(),),
        "created_at": NOW,
    }
    values.update(changes)
    return OrganizationGraph(**values)


def invalid_graph(**changes: object) -> OrganizationGraph:
    """Construct invalid records deliberately because Task 1 rejects them at ingestion."""

    base = graph()
    values = {
        "organization_id": base.organization_id,
        "version": base.version,
        "subjects": base.subjects,
        "units": base.units,
        "edges": base.edges,
        "authority_assignments": base.authority_assignments,
        "created_at": base.created_at,
    }
    values.update(changes)
    return OrganizationGraph.model_construct(**values)


def test_reporting_cycle_is_blocking() -> None:
    result = validate_organization_graph(
        graph(
            edges=(
                edge("edge_01K00000000000000000000000"),
                edge(
                    "edge_01K00000000000000000000001",
                    source_subject_id=BLAIR,
                    target_subject_id=AVERY,
                ),
            )
        )
    )

    assert result.committable is False
    assert result.blocking_codes == ("reporting_cycle",)


def test_manager_without_authority_cannot_approve() -> None:
    decision = authority_for(
        AuthorityRequest(
            organization_id=ORG,
            subject_id=BLAIR,
            decision_type="fundraising_readiness",
            function=AuthorityFunction.APPROVER,
            occurred_at=NOW,
        ),
        assignments=(assignment("auth_01K00000000000000000000000"),),
    )

    assert decision.allowed is False
    assert decision.reason == "authority_missing"
    assert decision.assignment_id is None


def test_self_reporting_is_blocking() -> None:
    self_edge = edge("edge_01K00000000000000000000000").model_copy(
        update={"target_subject_id": AVERY}
    )

    result = validate_organization_graph(
        invalid_graph(edges=(self_edge,))
    )

    assert result.blocking_codes == ("self_reporting",)


def test_multi_node_reporting_cycle_is_reported_once() -> None:
    result = validate_organization_graph(
        graph(
            edges=(
                edge("edge_01K00000000000000000000000"),
                edge(
                    "edge_01K00000000000000000000001",
                    source_subject_id=BLAIR,
                    target_subject_id=CASEY,
                ),
                edge(
                    "edge_01K00000000000000000000002",
                    source_subject_id=CASEY,
                    target_subject_id=AVERY,
                ),
            )
        )
    )

    assert result.blocking_codes == ("reporting_cycle",)


def test_orphan_unit_reference_is_blocking() -> None:
    orphan = unit(PLATFORM, parent_unit_id="unit_01K00000000000000000000002")

    result = validate_organization_graph(graph(units=(unit(), orphan)))

    assert result.blocking_codes == ("orphan_unit",)


def test_cross_organization_edge_is_blocking() -> None:
    foreign_edge = edge("edge_01K00000000000000000000000").model_copy(
        update={"organization_id": OTHER_ORG}
    )

    result = validate_organization_graph(invalid_graph(edges=(foreign_edge,)))

    assert result.blocking_codes == ("cross_organization_edge",)


def test_duplicate_primary_manager_is_blocking() -> None:
    result = validate_organization_graph(
        graph(
            edges=(
                edge("edge_01K00000000000000000000000", is_primary=True),
                edge(
                    "edge_01K00000000000000000000001",
                    target_subject_id=CASEY,
                    is_primary=True,
                ),
            )
        )
    )

    assert result.blocking_codes == ("duplicate_primary_manager",)


def test_expired_authority_is_not_allowed() -> None:
    decision = authority_for(
        AuthorityRequest(
            organization_id=ORG,
            subject_id=AVERY,
            decision_type="fundraising_readiness",
            function=AuthorityFunction.APPROVER,
            occurred_at=NOW,
        ),
        assignments=(
            assignment(
                "auth_01K00000000000000000000000",
                effective_until=NOW - timedelta(seconds=1),
            ),
        ),
    )

    assert decision.allowed is False
    assert decision.reason == "authority_missing"


def test_authority_is_scoped_to_its_decision_type() -> None:
    decision = authority_for(
        AuthorityRequest(
            organization_id=ORG,
            subject_id=AVERY,
            decision_type="hiring_plan",
            function=AuthorityFunction.APPROVER,
            occurred_at=NOW,
        ),
        assignments=(assignment("auth_01K00000000000000000000000"),),
    )

    assert decision.allowed is False
    assert decision.reason == "authority_missing"


def test_overlapping_authority_policies_are_blocking() -> None:
    result = validate_organization_graph(
        graph(
            authority_assignments=(
                assignment("auth_01K00000000000000000000000"),
                assignment(
                    "auth_01K00000000000000000000001",
                    policy_version=2,
                ),
            )
        )
    )

    assert result.blocking_codes == ("authority_conflict",)


def test_adjacent_authority_policies_do_not_conflict() -> None:
    result = validate_organization_graph(
        graph(
            authority_assignments=(
                assignment(
                    "auth_01K00000000000000000000000",
                    effective_until=NOW,
                ),
                assignment(
                    "auth_01K00000000000000000000001",
                    effective_from=NOW,
                    policy_version=2,
                ),
            )
        )
    )

    assert result.committable is True
    assert result.blocking_codes == ()


def test_suspended_subject_cannot_hold_authority() -> None:
    result = validate_organization_graph(
        graph(
            subjects=(subject(lifecycle=SubjectLifecycle.SUSPENDED), subject(BLAIR)),
            authority_assignments=(assignment("auth_01K00000000000000000000000"),),
        )
    )

    assert result.blocking_codes == ("suspended_authority",)


def test_ai_specialist_cannot_be_an_approver() -> None:
    result = validate_organization_graph(
        graph(
            subjects=(
                subject(
                    AI,
                    kind=OrganizationSubjectKind.AI_SPECIALIST,
                    lifecycle=SubjectLifecycle.ACTIVE,
                    display_name="Risk Specialist",
                    specialist_key="risk_specialist",
                ),
            ),
            authority_assignments=(
                assignment("auth_01K00000000000000000000000", subject_id=AI),
            ),
        )
    )

    assert result.blocking_codes == ("ai_approver_authority",)


def test_diagnostics_are_canonical_under_shuffled_input() -> None:
    foreign_edge = edge("edge_01K00000000000000000000000").model_copy(
        update={"organization_id": OTHER_ORG}
    )
    orphan = unit(PLATFORM, parent_unit_id="unit_01K00000000000000000000002")
    assignments = (
        assignment("auth_01K00000000000000000000000"),
        assignment("auth_01K00000000000000000000001", policy_version=2),
    )

    first = validate_organization_graph(
        invalid_graph(units=(unit(), orphan), edges=(foreign_edge,), authority_assignments=assignments)
    )
    second = validate_organization_graph(
        invalid_graph(
            units=(orphan, unit()),
            edges=(foreign_edge,),
            authority_assignments=tuple(reversed(assignments)),
        )
    )

    assert first.blocking_codes == (
        "authority_conflict",
        "cross_organization_edge",
        "orphan_unit",
    )
    assert second == first


def test_project_team_graph_keeps_the_selected_unit_descendants_and_internal_relations() -> None:
    product = unit(leader_subject_id=AVERY)
    platform = unit(PLATFORM, parent_unit_id=PRODUCT, leader_subject_id=BLAIR)
    graph_with_teams = graph(
        subjects=(
            subject(unit_id=PRODUCT),
            subject(BLAIR, unit_id=PLATFORM),
            subject(CASEY),
        ),
        units=(product, platform),
        edges=(
            edge("edge_01K00000000000000000000000"),
            edge(
                "edge_01K00000000000000000000001",
                source_subject_id=CASEY,
                target_subject_id=AVERY,
            ),
        ),
        authority_assignments=(assignment("auth_01K00000000000000000000000"),),
    )

    projection = project_team_graph(graph_with_teams, PRODUCT)

    assert tuple(item.unit_id for item in projection.units) == (PRODUCT, PLATFORM)
    assert tuple(item.subject_id for item in projection.subjects) == (AVERY, BLAIR)
    assert tuple(item.edge_id for item in projection.edges) == (
        "edge_01K00000000000000000000000",
    )
    assert tuple(item.assignment_id for item in projection.authority_assignments) == (
        "auth_01K00000000000000000000000",
    )


def test_project_team_graph_includes_subjects_linked_by_membership_edges() -> None:
    membership = OrganizationEdge(
        edge_id="edge_01K00000000000000000000000",
        organization_id=ORG,
        kind=OrganizationEdgeKind.MEMBER_OF,
        source_subject_id=AVERY,
        target_unit_id=PRODUCT,
    )

    projection = project_team_graph(
        graph(subjects=(subject(),), edges=(membership,)),
        PRODUCT,
    )

    assert tuple(item.subject_id for item in projection.subjects) == (AVERY,)
    assert projection.edges == (membership,)


def test_projecting_a_child_team_rebases_its_root_parent() -> None:
    source = graph(
        subjects=(subject(BLAIR, unit_id=PLATFORM),),
        units=(unit(), unit(PLATFORM, parent_unit_id=PRODUCT)),
    )

    projection = project_team_graph(source, PLATFORM)

    assert validate_organization_graph(source).committable is True
    assert validate_organization_graph(projection).committable is True
    assert projection.units[0].parent_unit_id is None
    assert source.units[1].parent_unit_id == PRODUCT


def test_projecting_an_external_member_normalizes_the_subject_unit() -> None:
    membership = OrganizationEdge(
        edge_id="edge_01K00000000000000000000000",
        organization_id=ORG,
        kind=OrganizationEdgeKind.MEMBER_OF,
        source_subject_id=AVERY,
        target_unit_id=PLATFORM,
    )
    source = graph(
        subjects=(subject(unit_id=PRODUCT),),
        units=(unit(), unit(PLATFORM, parent_unit_id=PRODUCT)),
        edges=(membership,),
    )

    projection = project_team_graph(source, PLATFORM)

    assert validate_organization_graph(source).committable is True
    assert validate_organization_graph(projection).committable is True
    assert projection.subjects[0].unit_id == PLATFORM
    assert source.subjects[0].unit_id == PRODUCT


def test_projecting_an_external_leader_normalizes_the_subject_unit() -> None:
    source = graph(
        subjects=(subject(unit_id=PRODUCT),),
        units=(
            unit(),
            unit(PLATFORM, parent_unit_id=PRODUCT, leader_subject_id=AVERY),
        ),
    )

    projection = project_team_graph(source, PLATFORM)

    assert validate_organization_graph(source).committable is True
    assert validate_organization_graph(projection).committable is True
    assert projection.subjects[0].unit_id == PLATFORM
    assert source.subjects[0].unit_id == PRODUCT
