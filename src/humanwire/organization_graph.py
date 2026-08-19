"""Pure organization graph validation, authority evaluation, and team projections."""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from humanwire.organization_models import (
    AuthorityAssignment,
    AuthorityDecision,
    AuthorityFunction,
    AuthorityRequest,
    OrganizationEdge,
    OrganizationEdgeKind,
    OrganizationGraph,
    OrganizationSubject,
    OrganizationSubjectKind,
    SubjectLifecycle,
)


class GraphValidation(BaseModel):
    """Canonical, fail-closed result for organization graph validation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    committable: bool
    blocking_codes: tuple[str, ...] = ()

    @field_validator("blocking_codes")
    @classmethod
    def blocking_codes_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("blocking codes must be unique")
        if value != tuple(sorted(value)):
            raise ValueError("blocking codes must be sorted")
        return value

    @model_validator(mode="after")
    def committable_matches_blocking_codes(self) -> GraphValidation:
        if self.committable != (not self.blocking_codes):
            raise ValueError("committable must match blocking codes")
        return self


def authority_for(
    request: AuthorityRequest,
    assignments: tuple[AuthorityAssignment, ...],
) -> AuthorityDecision:
    """Resolve one explicit authority assignment without inferring it from reporting."""

    matches = tuple(
        item
        for item in assignments
        if item.organization_id == request.organization_id
        and item.subject_id == request.subject_id
        and item.decision_type == request.decision_type
        and item.function is request.function
        and item.effective_from <= request.occurred_at
        and (item.effective_until is None or request.occurred_at < item.effective_until)
    )
    return AuthorityDecision(
        allowed=len(matches) == 1,
        reason=None if len(matches) == 1 else "authority_missing",
        assignment_id=matches[0].assignment_id if len(matches) == 1 else None,
    )


def validate_organization_graph(graph: OrganizationGraph) -> GraphValidation:
    """Return stable blocking diagnostics for graph records that cannot be committed."""

    codes: set[str] = set()
    subjects = {subject.subject_id: subject for subject in graph.subjects}
    units = {unit.unit_id: unit for unit in graph.units}

    _validate_tenant_binding(graph, codes)
    _validate_unit_references(graph, subjects, units, codes)
    _validate_edges(graph.edges, subjects, units, codes)
    _validate_reporting(graph.edges, codes)
    _validate_authority_assignments(graph.authority_assignments, subjects, codes)

    blocking_codes = tuple(sorted(codes))
    return GraphValidation(
        committable=not blocking_codes,
        blocking_codes=blocking_codes,
    )


def project_team_graph(graph: OrganizationGraph, team_id: str) -> OrganizationGraph:
    """Return the selected unit subtree with only its subjects and internal relations."""

    team_unit_ids = _team_subtree_ids(graph, team_id)
    included_subject_ids = {
        subject.subject_id
        for subject in graph.subjects
        if subject.unit_id in team_unit_ids
    }
    included_subject_ids.update(
        edge.source_subject_id
        for edge in graph.edges
        if (
            edge.kind is OrganizationEdgeKind.MEMBER_OF
            and edge.target_unit_id in team_unit_ids
        )
    )
    included_subject_ids.update(
        unit.leader_subject_id
        for unit in graph.units
        if unit.unit_id in team_unit_ids and unit.leader_subject_id is not None
    )
    subjects = tuple(
        sorted(
            (
                subject
                for subject in graph.subjects
                if subject.subject_id in included_subject_ids
            ),
            key=lambda item: item.subject_id,
        )
    )
    subject_ids = {subject.subject_id for subject in subjects}
    units = tuple(
        sorted(
            (unit for unit in graph.units if unit.unit_id in team_unit_ids),
            key=lambda item: item.unit_id,
        )
    )
    edges = tuple(
        sorted(
            (
                edge
                for edge in graph.edges
                if _is_internal_team_edge(edge, subject_ids, team_unit_ids)
            ),
            key=lambda item: item.edge_id,
        )
    )
    assignments = tuple(
        sorted(
            (
                assignment
                for assignment in graph.authority_assignments
                if assignment.subject_id in subject_ids
            ),
            key=lambda item: item.assignment_id,
        )
    )
    return OrganizationGraph(
        organization_id=graph.organization_id,
        version=graph.version,
        subjects=subjects,
        units=units,
        edges=edges,
        authority_assignments=assignments,
        created_at=graph.created_at,
    )


def _validate_tenant_binding(graph: OrganizationGraph, codes: set[str]) -> None:
    for subject in graph.subjects:
        if subject.organization_id != graph.organization_id:
            codes.add("cross_organization_subject")
    for unit in graph.units:
        if unit.organization_id != graph.organization_id:
            codes.add("cross_organization_unit")
    for edge in graph.edges:
        if edge.organization_id != graph.organization_id:
            codes.add("cross_organization_edge")
    for assignment in graph.authority_assignments:
        if assignment.organization_id != graph.organization_id:
            codes.add("cross_organization_authority")


def _validate_unit_references(
    graph: OrganizationGraph,
    subjects: dict[str, OrganizationSubject],
    units: dict[str, object],
    codes: set[str],
) -> None:
    for subject in graph.subjects:
        if subject.unit_id is not None and subject.unit_id not in units:
            codes.add("orphan_unit")
    for unit in graph.units:
        if unit.parent_unit_id is not None and unit.parent_unit_id not in units:
            codes.add("orphan_unit")
        if unit.leader_subject_id is not None and unit.leader_subject_id not in subjects:
            codes.add("unknown_unit_leader")


def _validate_edges(
    edges: tuple[OrganizationEdge, ...],
    subjects: dict[str, OrganizationSubject],
    units: dict[str, object],
    codes: set[str],
) -> None:
    for edge in edges:
        if edge.source_subject_id not in subjects:
            codes.add("unknown_edge_subject")
        if edge.kind is OrganizationEdgeKind.MEMBER_OF:
            if edge.target_unit_id not in units:
                codes.add("unknown_edge_unit")
        elif edge.target_subject_id not in subjects:
            codes.add("unknown_edge_subject")


def _validate_reporting(edges: tuple[OrganizationEdge, ...], codes: set[str]) -> None:
    reporting_edges = tuple(
        edge for edge in edges if edge.kind is OrganizationEdgeKind.REPORTS_TO
    )
    primary_manager_counts: dict[str, int] = defaultdict(int)
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in reporting_edges:
        if edge.source_subject_id == edge.target_subject_id:
            codes.add("self_reporting")
            continue
        if edge.target_subject_id is not None:
            adjacency[edge.source_subject_id].add(edge.target_subject_id)
        if edge.is_primary:
            primary_manager_counts[edge.source_subject_id] += 1
    if any(count > 1 for count in primary_manager_counts.values()):
        codes.add("duplicate_primary_manager")
    if _has_reporting_cycle(adjacency):
        codes.add("reporting_cycle")


def _has_reporting_cycle(adjacency: dict[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(subject_id: str) -> bool:
        if subject_id in visiting:
            return True
        if subject_id in visited:
            return False
        visiting.add(subject_id)
        for manager_id in sorted(adjacency.get(subject_id, ())):
            if visit(manager_id):
                return True
        visiting.remove(subject_id)
        visited.add(subject_id)
        return False

    return any(visit(subject_id) for subject_id in sorted(adjacency))


def _validate_authority_assignments(
    assignments: tuple[AuthorityAssignment, ...],
    subjects: dict[str, OrganizationSubject],
    codes: set[str],
) -> None:
    for assignment in assignments:
        subject = subjects.get(assignment.subject_id)
        if subject is None:
            codes.add("unknown_authority_subject")
            continue
        if subject.lifecycle is SubjectLifecycle.SUSPENDED:
            codes.add("suspended_authority")
        if (
            subject.kind is OrganizationSubjectKind.AI_SPECIALIST
            and assignment.function is AuthorityFunction.APPROVER
        ):
            codes.add("ai_approver_authority")
        if (
            assignment.effective_until is not None
            and assignment.effective_until <= assignment.effective_from
        ):
            codes.add("invalid_authority_interval")
    if _has_authority_conflict(assignments):
        codes.add("authority_conflict")


def _has_authority_conflict(assignments: tuple[AuthorityAssignment, ...]) -> bool:
    groups: dict[tuple[str, str | None, str, AuthorityFunction], list[AuthorityAssignment]] = (
        defaultdict(list)
    )
    for assignment in assignments:
        groups[
            (
                assignment.subject_id,
                assignment.workspace_id,
                assignment.decision_type,
                assignment.function,
            )
        ].append(assignment)
    for group in groups.values():
        ordered = sorted(
            group,
            key=lambda item: (item.effective_from, item.assignment_id),
        )
        latest_until = ordered[0].effective_until
        for assignment in ordered[1:]:
            if latest_until is None:
                return True
            if assignment.effective_from < latest_until:
                return True
            if assignment.effective_until is None:
                latest_until = None
            elif assignment.effective_until > latest_until:
                latest_until = assignment.effective_until
    return False


def _team_subtree_ids(graph: OrganizationGraph, team_id: str) -> set[str]:
    child_units: dict[str, set[str]] = defaultdict(set)
    for unit in graph.units:
        if unit.parent_unit_id is not None:
            child_units[unit.parent_unit_id].add(unit.unit_id)
    included = {team_id} if any(unit.unit_id == team_id for unit in graph.units) else set()
    pending = list(included)
    while pending:
        parent_id = pending.pop()
        for child_id in sorted(child_units.get(parent_id, ())):
            if child_id not in included:
                included.add(child_id)
                pending.append(child_id)
    return included


def _is_internal_team_edge(
    edge: OrganizationEdge,
    subject_ids: set[str],
    unit_ids: set[str],
) -> bool:
    if edge.source_subject_id not in subject_ids:
        return False
    if edge.kind is OrganizationEdgeKind.MEMBER_OF:
        return edge.target_unit_id in unit_ids
    return edge.target_subject_id in subject_ids
