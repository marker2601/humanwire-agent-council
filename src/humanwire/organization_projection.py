"""Privacy-safe browser projections of authoritative organization graphs."""

from __future__ import annotations

import traceback as traceback_module
from datetime import UTC, datetime

from humanwire.organization_graph import validate_organization_graph
from humanwire.organization_models import (
    AuthorityAssignment,
    ImportReconciliation,
    OrganizationEdge,
    OrganizationGraph,
    OrganizationProjection,
    OrganizationProjectionSubject,
    OrganizationSubject,
    OrganizationUnit,
)


class OrganizationProjectionUnavailable(RuntimeError):
    """A fixed, content-free projection failure."""

    def __init__(self) -> None:
        super().__init__("organization_projection_unavailable")


def _subject(subject: OrganizationSubject) -> OrganizationProjectionSubject:
    if type(subject) is not OrganizationSubject:
        raise TypeError("organization subject is invalid")
    return OrganizationProjectionSubject(
        subject_id=subject.subject_id,
        kind=subject.kind,
        lifecycle=subject.lifecycle,
        display_name=subject.display_name,
        unit_id=subject.unit_id,
        title=subject.title,
    )


def _unit(unit: OrganizationUnit) -> OrganizationUnit:
    if type(unit) is not OrganizationUnit:
        raise TypeError("organization unit is invalid")
    return OrganizationUnit(
        unit_id=unit.unit_id,
        organization_id=unit.organization_id,
        name=unit.name,
        parent_unit_id=unit.parent_unit_id,
        leader_subject_id=unit.leader_subject_id,
    )


def _edge(edge: OrganizationEdge) -> OrganizationEdge:
    if type(edge) is not OrganizationEdge:
        raise TypeError("organization edge is invalid")
    return OrganizationEdge(
        edge_id=edge.edge_id,
        organization_id=edge.organization_id,
        kind=edge.kind,
        source_subject_id=edge.source_subject_id,
        target_subject_id=edge.target_subject_id,
        target_unit_id=edge.target_unit_id,
        is_primary=edge.is_primary,
    )


def _assignment(assignment: AuthorityAssignment) -> AuthorityAssignment:
    if type(assignment) is not AuthorityAssignment:
        raise TypeError("authority assignment is invalid")
    return AuthorityAssignment(
        assignment_id=assignment.assignment_id,
        organization_id=assignment.organization_id,
        subject_id=assignment.subject_id,
        workspace_id=assignment.workspace_id,
        decision_type=assignment.decision_type,
        function=assignment.function,
        effective_from=assignment.effective_from,
        effective_until=assignment.effective_until,
        policy_version=assignment.policy_version,
    )


def _reconciliation(value: ImportReconciliation | None) -> ImportReconciliation | None:
    if value is None:
        return None
    if type(value) is not ImportReconciliation:
        raise TypeError("import reconciliation is invalid")
    return ImportReconciliation(
        import_id=value.import_id,
        organization_id=value.organization_id,
        source_count=value.source_count,
        normalized_count=value.normalized_count,
        rejected_count=value.rejected_count,
        lifecycle_counts=tuple(value.lifecycle_counts),
        blocking_codes=tuple(value.blocking_codes),
        acknowledged_codes=tuple(value.acknowledged_codes),
    )


def _build_organization_projection(
    graph: OrganizationGraph,
    reconciliation: ImportReconciliation | None,
) -> OrganizationProjection:
    if type(graph) is not OrganizationGraph:
        raise TypeError("organization graph is invalid")
    if not validate_organization_graph(graph).committable:
        raise ValueError("organization graph is invalid")
    return OrganizationProjection(
        organization_id=graph.organization_id,
        graph_version=graph.version,
        subjects=tuple(
            _subject(item) for item in sorted(graph.subjects, key=lambda item: item.subject_id)
        ),
        units=tuple(_unit(item) for item in sorted(graph.units, key=lambda item: item.unit_id)),
        edges=tuple(_edge(item) for item in sorted(graph.edges, key=lambda item: item.edge_id)),
        authority_assignments=tuple(
            _assignment(item)
            for item in sorted(
                graph.authority_assignments,
                key=lambda item: item.assignment_id,
            )
        ),
        # The authoritative graph/reconciliation inputs do not carry source provenance.
        source_kind=None,
        synchronized_at=graph.created_at,
        reconciliation=_reconciliation(reconciliation),
        generated_at=datetime.now(UTC),
    )


def build_organization_projection(
    graph: OrganizationGraph,
    reconciliation: ImportReconciliation | None,
) -> OrganizationProjection:
    """Build an allowlisted projection while sealing source-bearing failures."""

    result: OrganizationProjection | None = None
    failed = False
    try:
        result = _build_organization_projection(graph, reconciliation)
    except Exception as error:  # noqa: BLE001 - projection failures remain fixed and private
        traceback_module.clear_frames(error.__traceback__)
        error.__traceback__ = None
        error.__cause__ = None
        error.__context__ = None
        failed = True
    if failed or result is None:
        graph = None  # type: ignore[assignment]  # erase source-bearing traceback local
        reconciliation = None
        result = None
        _raise_projection_unavailable()
    return result


def _raise_projection_unavailable() -> None:
    raise OrganizationProjectionUnavailable() from None


__all__ = [
    "OrganizationProjectionUnavailable",
    "build_organization_projection",
]
