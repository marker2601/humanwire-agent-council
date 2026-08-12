"""Evidence-based alignment checks and bounded, human-authorized negotiation."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import IntegrityError

from humanwire.domain import (
    AlignmentIssue,
    AlignmentIssueType,
    DomainEvent,
    EngagementDecision,
    EngagementDecisionKind,
    EngagementType,
    EvidenceItem,
    EvidenceStatus,
    EvidenceType,
    EvidenceVisibility,
    Mandate,
    MandatePlan,
    Proposal,
    ProposalResponse,
    ProposalResponseKind,
    ProposalState,
    StakeholderAssignment,
    StakeholderState,
)
from humanwire.evidence import ShareableEvidence
from humanwire.model_client import JsonModelClient, ModelFailure
from humanwire.repository import SqlAlchemyHumanWireRepository

_HARD_CONSTRAINT = re.compile(r"\b(?:must|cannot|requires?|blocked)\b", re.IGNORECASE)
_DAY = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.IGNORECASE
)
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NEGATION = re.compile(r"\b(?:not|cannot|can't|never|won't)\b", re.IGNORECASE)
_NUMBER = re.compile(r"\b(\d+)\b")
_WHITESPACE = re.compile(r"\s+")
_MAX_PROPOSAL_TEXT = 600
_DRAFT_PREFIX = "HUMANWIRE DRAFT PROPOSAL\n"


class NegotiationLimitReached(ValueError):
    """Raised when a caller attempts a third (or invalid) negotiation round."""


class NegotiationOutcome(StrEnum):
    ALIGNED = "aligned"
    NEXT_ROUND = "next_round"
    MEETING_REQUIRED = "meeting_required"


class ContributionState(StrEnum):
    COMPLETE = "complete"
    MISSING_RESPONSE = "missing_response"
    MISSING_EVIDENCE = "missing_evidence"
    REJECTED = "rejected"
    CHANGE_REQUESTED = "change_requested"
    DELIVERY_FAILED = "delivery_failed"


class ContributionStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: ContributionState
    blocking: bool


def contribution_status(
    assignment: StakeholderAssignment,
    *,
    evidence: Sequence[EvidenceItem],
    decisions: Sequence[EngagementDecision],
    has_availability: bool,
) -> ContributionStatus:
    """Evaluate one contribution solely from exact persisted trusted facts."""
    if assignment.state in {
        StakeholderState.DELIVERY_FAILED,
        StakeholderState.UNREACHABLE,
    }:
        return ContributionStatus(
            state=ContributionState.DELIVERY_FAILED,
            blocking=assignment.required,
        )
    if assignment.state is not StakeholderState.COMPLETE:
        return ContributionStatus(
            state=ContributionState.MISSING_RESPONSE,
            blocking=assignment.required,
        )
    if assignment.engagement_type in {
        EngagementType.QUICK_RESPONSE,
        EngagementType.STRUCTURED_INTERVIEW,
    }:
        has_exact_evidence = any(
            item.mandate_id == assignment.mandate_id
            and item.assignment_id == assignment.assignment_id
            and item.stakeholder_id == assignment.person_id
            and item.status is EvidenceStatus.CONFIRMED
            and item.visibility
            in {EvidenceVisibility.SHAREABLE, EvidenceVisibility.PRIVATE}
            for item in evidence
        )
        if not has_exact_evidence:
            return ContributionStatus(
                state=ContributionState.MISSING_EVIDENCE,
                blocking=assignment.required,
            )
    elif assignment.engagement_type is EngagementType.REVIEW_APPROVAL:
        exact_decisions = [
            item
            for item in decisions
            if item.mandate_id == assignment.mandate_id
            and item.assignment_id == assignment.assignment_id
            and item.stakeholder_id == assignment.person_id
        ]
        if len(exact_decisions) != 1:
            return ContributionStatus(
                state=ContributionState.MISSING_RESPONSE,
                blocking=assignment.required,
            )
        response = exact_decisions[0].response
        if response is EngagementDecisionKind.REJECT:
            return ContributionStatus(
                state=ContributionState.REJECTED,
                blocking=True,
            )
        if response is EngagementDecisionKind.CHANGE:
            return ContributionStatus(
                state=ContributionState.CHANGE_REQUESTED,
                blocking=True,
            )
    elif (
        assignment.engagement_type is EngagementType.AVAILABILITY
        and not has_availability
    ):
        return ContributionStatus(
            state=ContributionState.MISSING_RESPONSE,
            blocking=assignment.required,
        )
    return ContributionStatus(state=ContributionState.COMPLETE, blocking=False)


class AlignmentReport(BaseModel):
    mandate_id: UUID
    agreements: list[str] = Field(default_factory=list)
    issues: list[AlignmentIssue] = Field(default_factory=list)
    covered_decisions: list[str] = Field(default_factory=list)
    private_blocker_count: int = 0
    is_aligned: bool

    @property
    def blocking_issue_count(self) -> int:
        return sum(issue.blocking for issue in self.issues)


class AlignmentEngine:
    """Conservative deterministic synthesis of public evidence only."""

    def __init__(self, mandate_id: UUID) -> None:
        self.mandate_id = mandate_id

    def analyze(
        self,
        plan: MandatePlan,
        evidence: Iterable[ShareableEvidence],
        assignments: Iterable[StakeholderAssignment],
        *,
        private_blocker_count: int = 0,
        contribution_evidence: Sequence[EvidenceItem] | None = None,
        decisions: Sequence[EngagementDecision] = (),
        availability_assignment_ids: set[UUID] | frozenset[UUID] = frozenset(),
    ) -> AlignmentReport:
        assignment_values = sorted(assignments, key=lambda item: str(item.assignment_id))
        public_evidence = self._public_evidence(evidence)
        required_assignments = [
            assignment for assignment in assignment_values if assignment.required
        ]
        statuses: dict[UUID, ContributionStatus] | None = None
        valid_assignment_ids: set[UUID] | None = None
        if contribution_evidence is not None:
            valid_assignment_ids = self._valid_assignment_ids(plan, assignment_values)
            statuses = {
                assignment.assignment_id: contribution_status(
                    assignment,
                    evidence=contribution_evidence,
                    decisions=decisions,
                    has_availability=(
                        assignment.assignment_id in availability_assignment_ids
                    ),
                )
                for assignment in assignment_values
            }
            eligible_evidence_ids = self._eligible_contribution_evidence_ids(
                assignment_values,
                contribution_evidence,
                valid_assignment_ids,
            )
            public_evidence = [
                item for item in public_evidence if item.evidence_id in eligible_evidence_ids
            ]
        issues = self._deterministic_issues(
            plan,
            public_evidence,
            required_assignments,
            assignments=assignment_values,
            contribution_statuses=statuses,
            valid_assignment_ids=valid_assignment_ids,
        )
        if private_blocker_count:
            issues.append(
                self._issue(
                    AlignmentIssueType.PRIVATE_BLOCKER,
                    "Private constraints require direct human resolution.",
                    blocking=True,
                )
            )

        covered = (
            self._covered_contribution_decisions(
                plan,
                assignment_values,
                contribution_evidence,
                decisions,
                statuses or {},
                valid_assignment_ids or set(),
            )
            if contribution_evidence is not None
            else self._covered_decisions(plan, public_evidence)
        )
        if contribution_evidence is not None:
            for decision in plan.required_decisions:
                if decision not in covered:
                    issues.append(
                        self._issue(
                            AlignmentIssueType.AUTHORITY_GAP,
                            "A required decision lacks authenticated evidence or approval authority.",
                            related_decision=decision,
                            blocking=True,
                        )
                    )
        planned_required_ids = {
            stakeholder.person_ref for stakeholder in plan.stakeholders if stakeholder.required
        }
        if contribution_evidence is None:
            completed_ids = {
                assignment.person_id
                for assignment in required_assignments
                if assignment.state is StakeholderState.COMPLETE
            }
            all_required_complete = bool(planned_required_ids) and planned_required_ids.issubset(
                completed_ids
            )
        else:
            all_required_complete = all(
                len(
                    [
                        assignment
                        for assignment in required_assignments
                        if assignment.person_id == person_id
                        and assignment.assignment_id in (valid_assignment_ids or set())
                        and statuses is not None
                        and statuses[assignment.assignment_id].state
                        is ContributionState.COMPLETE
                    ]
                )
                == 1
                for person_id in planned_required_ids
            )
        agreement_evidence = (
            [item for item in public_evidence if item.stakeholder_id is not None]
            if contribution_evidence is not None
            else public_evidence
        )
        agreements = self._agreements(agreement_evidence, issues)
        return AlignmentReport(
            mandate_id=self.mandate_id,
            agreements=agreements,
            issues=issues,
            covered_decisions=covered,
            private_blocker_count=private_blocker_count,
            is_aligned=(
                all_required_complete
                and set(plan.required_decisions).issubset(covered)
                and not any(issue.blocking for issue in issues)
            ),
        )

    def _deterministic_issues(
        self,
        plan: MandatePlan,
        evidence: list[ShareableEvidence],
        required_assignments: list[StakeholderAssignment],
        *,
        assignments: list[StakeholderAssignment] | None = None,
        contribution_statuses: dict[UUID, ContributionStatus] | None = None,
        valid_assignment_ids: set[UUID] | None = None,
    ) -> list[AlignmentIssue]:
        issues: list[AlignmentIssue] = []
        planned_required_ids = {
            stakeholder.person_ref for stakeholder in plan.stakeholders if stakeholder.required
        }
        accounted_for: set[str] = set()
        for person_id in sorted(planned_required_ids):
            matching = [
                assignment
                for assignment in required_assignments
                if assignment.person_id == person_id
            ]
            assignment = matching[0] if len(matching) == 1 else None
            accounted_for.add(person_id)
            if assignment is None:
                issues.append(
                    self._issue(
                        AlignmentIssueType.MISSING_EVIDENCE,
                        "A required stakeholder has no authenticated assignment.",
                        stakeholder_ids=[person_id],
                        blocking=True,
                    )
                )
            elif contribution_statuses is not None:
                if assignment.assignment_id not in (valid_assignment_ids or set()):
                    issues.append(
                        self._issue(
                            AlignmentIssueType.AUTHORITY_GAP,
                            "A required assignment does not match its trusted engagement plan.",
                            stakeholder_ids=[assignment.person_id],
                            blocking=True,
                        )
                    )
                else:
                    issue = self._contribution_issue(
                        assignment,
                        contribution_statuses[assignment.assignment_id],
                    )
                    if issue is not None:
                        issues.append(issue)
            elif assignment.state is not StakeholderState.COMPLETE:
                issues.append(
                    self._issue(
                        AlignmentIssueType.MISSING_EVIDENCE,
                        "A required stakeholder has not completed an authenticated response.",
                        stakeholder_ids=[assignment.person_id],
                        blocking=True,
                    )
                )
        for assignment in required_assignments:
            if assignment.person_id not in accounted_for and (
                contribution_statuses is not None
                or assignment.state is not StakeholderState.COMPLETE
            ):
                issues.append(
                    self._issue(
                        AlignmentIssueType.MISSING_EVIDENCE,
                        "A required stakeholder has not completed an authenticated response.",
                        stakeholder_ids=[assignment.person_id],
                        blocking=True,
                    )
                )
        if contribution_statuses is not None:
            for assignment in assignments or []:
                if assignment.required or assignment.assignment_id not in (
                    valid_assignment_ids or set()
                ):
                    continue
                issue = self._contribution_issue(
                    assignment,
                    contribution_statuses[assignment.assignment_id],
                )
                if issue is not None:
                    issues.append(issue)

        by_decision = self._by_decision(plan, evidence)
        for decision, candidates in by_decision.items():
            disputed = [item for item in candidates if item.status is EvidenceStatus.DISPUTED]
            if disputed:
                issues.append(
                    self._issue(
                        AlignmentIssueType.CONTRADICTION,
                        "Disputed evidence remains unresolved.",
                        evidence=disputed,
                        related_decision=decision,
                        blocking=True,
                    )
                )

            issues.extend(self._contradictions(candidates, decision))
            issues.extend(self._deadline_conflicts(candidates, decision))
            issues.extend(self._resource_conflicts(candidates, decision))
            issues.extend(self._hard_constraints(candidates, decision))
        return issues

    def _contribution_issue(
        self,
        assignment: StakeholderAssignment,
        status: ContributionStatus,
    ) -> AlignmentIssue | None:
        if not status.blocking or status.state is ContributionState.COMPLETE:
            return None
        if status.state in {
            ContributionState.REJECTED,
            ContributionState.CHANGE_REQUESTED,
        }:
            issue_type = AlignmentIssueType.HARD_CONSTRAINT
            summary = (
                "An authenticated approval response rejected the request."
                if status.state is ContributionState.REJECTED
                else "An authenticated approval response requested changes."
            )
        elif assignment.engagement_type is EngagementType.REVIEW_APPROVAL:
            issue_type = AlignmentIssueType.AUTHORITY_GAP
            summary = "Required authenticated approval is missing."
        elif status.state is ContributionState.MISSING_EVIDENCE:
            issue_type = AlignmentIssueType.MISSING_EVIDENCE
            summary = "A required response lacks confirmed assignment-bound evidence."
        elif status.state is ContributionState.DELIVERY_FAILED:
            issue_type = AlignmentIssueType.MISSING_EVIDENCE
            summary = "A required contribution could not be delivered or reached."
        else:
            issue_type = AlignmentIssueType.MISSING_EVIDENCE
            summary = "A required authenticated response is missing."
        return self._issue(
            issue_type,
            summary,
            stakeholder_ids=[assignment.person_id],
            blocking=True,
        )

    def _valid_assignment_ids(
        self,
        plan: MandatePlan,
        assignments: Sequence[StakeholderAssignment],
    ) -> set[UUID]:
        planned_by_person: dict[str, list] = {}
        for stakeholder in plan.stakeholders:
            planned_by_person.setdefault(stakeholder.person_ref, []).append(stakeholder)
        assignment_counts: dict[str, int] = {}
        for assignment in assignments:
            assignment_counts[assignment.person_id] = (
                assignment_counts.get(assignment.person_id, 0) + 1
            )
        return {
            assignment.assignment_id
            for assignment in assignments
            if assignment.mandate_id == self.mandate_id
            and assignment_counts[assignment.person_id] == 1
            and len(planned_by_person.get(assignment.person_id, [])) == 1
            and (
                stakeholder := planned_by_person[assignment.person_id][0]
            ).direction
            is assignment.direction
            and stakeholder.reason == assignment.reason
            and stakeholder.required is assignment.required
            and stakeholder.engagement_type is assignment.engagement_type
            and stakeholder.response_required is assignment.response_required
        }

    @staticmethod
    def _eligible_contribution_evidence_ids(
        assignments: Sequence[StakeholderAssignment],
        evidence: Sequence[EvidenceItem],
        valid_assignment_ids: set[UUID],
    ) -> set[UUID]:
        by_id = {item.assignment_id: item for item in assignments}
        return {
            item.evidence_id
            for item in evidence
            if item.assignment_id in valid_assignment_ids
            and (assignment := by_id.get(item.assignment_id)) is not None
            and assignment.engagement_type
            in {
                EngagementType.QUICK_RESPONSE,
                EngagementType.STRUCTURED_INTERVIEW,
            }
            and item.mandate_id == assignment.mandate_id
            and item.stakeholder_id == assignment.person_id
        }

    def _covered_contribution_decisions(
        self,
        plan: MandatePlan,
        assignments: Sequence[StakeholderAssignment],
        evidence: Sequence[EvidenceItem],
        decisions: Sequence[EngagementDecision],
        statuses: dict[UUID, ContributionStatus],
        valid_assignment_ids: set[UUID],
    ) -> list[str]:
        by_id = {assignment.assignment_id: assignment for assignment in assignments}
        approved = any(
            decision.response is EngagementDecisionKind.APPROVE
            and decision.assignment_id in valid_assignment_ids
            and (assignment := by_id.get(decision.assignment_id)) is not None
            and assignment.engagement_type is EngagementType.REVIEW_APPROVAL
            and statuses[assignment.assignment_id].state is ContributionState.COMPLETE
            and decision.mandate_id == assignment.mandate_id
            and decision.stakeholder_id == assignment.person_id
            for decision in decisions
        )
        covered: list[str] = []
        for required_decision in plan.required_decisions:
            evidence_covers = any(
                item.related_decision == required_decision
                and item.status is EvidenceStatus.CONFIRMED
                and item.visibility
                in {EvidenceVisibility.SHAREABLE, EvidenceVisibility.PRIVATE}
                and item.assignment_id in valid_assignment_ids
                and (assignment := by_id.get(item.assignment_id)) is not None
                and assignment.engagement_type
                in {
                    EngagementType.QUICK_RESPONSE,
                    EngagementType.STRUCTURED_INTERVIEW,
                }
                and statuses[assignment.assignment_id].state
                is ContributionState.COMPLETE
                and item.mandate_id == assignment.mandate_id
                and item.stakeholder_id == assignment.person_id
                for item in evidence
            )
            if approved or evidence_covers:
                covered.append(required_decision)
        return covered

    def _by_decision(
        self, plan: MandatePlan, evidence: list[ShareableEvidence]
    ) -> dict[str, list[ShareableEvidence]]:
        result = {decision: [] for decision in plan.required_decisions}
        for item in evidence:
            if item.related_decision in result:
                result[item.related_decision].append(item)
        return result

    def _contradictions(
        self, candidates: list[ShareableEvidence], decision: str
    ) -> list[AlignmentIssue]:
        facts = [item for item in candidates if item.evidence_type is EvidenceType.FACT]
        if self._facts_have_explicit_conflict(facts):
            return [
                self._issue(
                    AlignmentIssueType.CONTRADICTION,
                    "Public facts for this decision conflict and remain disputed.",
                    evidence=facts,
                    related_decision=decision,
                    blocking=True,
                )
            ]
        return []

    def _facts_have_explicit_conflict(self, facts: list[ShareableEvidence]) -> bool:
        if len(facts) < 2:
            return False
        dates = [self._date_value(item.statement) for item in facts]
        if all(value is not None for value in dates) and len(set(dates)) > 1:
            return True
        quantities = [self._quantity(item) for item in facts]
        if all(value is not None for value in quantities) and len(set(quantities)) > 1:
            return True
        normalized = [self._normalize(_NEGATION.sub("", item.statement)) for item in facts]
        polarity = [bool(_NEGATION.search(item.statement)) for item in facts]
        return len(set(normalized)) == 1 and len(set(polarity)) > 1

    @staticmethod
    def _date_value(statement: str) -> str | None:
        if match := _DATE.search(statement):
            return match.group(0)
        if match := _DAY.search(statement):
            return match.group(0).casefold()
        return None

    def _deadline_conflicts(
        self, candidates: list[ShareableEvidence], decision: str
    ) -> list[AlignmentIssue]:
        dated = [item for item in candidates if item.deadline is not None]
        if len({item.deadline for item in dated}) > 1:
            return [
                self._issue(
                    AlignmentIssueType.DEADLINE_CONFLICT,
                    "Explicit deadlines for this decision are incompatible.",
                    evidence=dated,
                    related_decision=decision,
                    blocking=True,
                )
            ]
        return []

    def _resource_conflicts(
        self, candidates: list[ShareableEvidence], decision: str
    ) -> list[AlignmentIssue]:
        constraints = [
            item
            for item in candidates
            if item.evidence_type is EvidenceType.CONSTRAINT and self._quantity(item) is not None
        ]
        requests = [
            item
            for item in candidates
            if item.evidence_type is EvidenceType.COMMITMENT and self._quantity(item) is not None
        ]
        if not constraints or not requests:
            return []
        asserted_limit = min(self._quantity(item) for item in constraints if self._quantity(item) is not None)
        requested = max(self._quantity(item) for item in requests if self._quantity(item) is not None)
        if requested > asserted_limit:
            return [
                self._issue(
                    AlignmentIssueType.RESOURCE_CONFLICT,
                    "A requested resource quantity exceeds an asserted public limit.",
                    evidence=[*constraints, *requests],
                    related_decision=decision,
                    blocking=True,
                )
            ]
        return []

    def _hard_constraints(
        self, candidates: list[ShareableEvidence], decision: str
    ) -> list[AlignmentIssue]:
        constraints = [
            item
            for item in candidates
            if item.evidence_type is EvidenceType.CONSTRAINT and _HARD_CONSTRAINT.search(item.statement)
        ]
        if not constraints:
            return []
        return [
            self._issue(
                AlignmentIssueType.HARD_CONSTRAINT,
                "A public hard constraint requires an explicit human resolution.",
                evidence=constraints,
                related_decision=decision,
                blocking=True,
            )
        ]

    def _covered_decisions(
        self, plan: MandatePlan, evidence: list[ShareableEvidence]
    ) -> list[str]:
        return [
            decision
            for decision in plan.required_decisions
            if any(
                item.related_decision == decision and item.status is EvidenceStatus.CONFIRMED
                for item in evidence
            )
        ]

    def _agreements(
        self, evidence: list[ShareableEvidence], issues: list[AlignmentIssue]
    ) -> list[str]:
        blocked_decisions = {issue.related_decision for issue in issues if issue.blocking}
        return [
            item.statement
            for item in evidence
            if item.evidence_type is EvidenceType.COMMITMENT
            and item.status is EvidenceStatus.CONFIRMED
            and item.related_decision not in blocked_decisions
        ]

    @staticmethod
    def _normalize(statement: str) -> str:
        return _WHITESPACE.sub(" ", statement.strip().casefold())

    @staticmethod
    def _public_evidence(evidence: Iterable[ShareableEvidence]) -> list[ShareableEvidence]:
        """Defend the shared boundary if a caller accidentally supplies raw evidence."""
        return [item for item in evidence if isinstance(item, ShareableEvidence)]

    @staticmethod
    def _quantity(item: ShareableEvidence) -> int | None:
        if item.resource and (match := _NUMBER.search(item.resource)):
            return int(match.group(1))
        if match := _NUMBER.search(item.statement):
            return int(match.group(1))
        return None

    def _issue(
        self,
        issue_type: AlignmentIssueType,
        summary: str,
        *,
        evidence: Iterable[ShareableEvidence] = (),
        stakeholder_ids: list[str] | None = None,
        related_decision: str | None = None,
        blocking: bool,
    ) -> AlignmentIssue:
        values = list(evidence)
        return AlignmentIssue(
            issue_id=uuid4(),
            mandate_id=self.mandate_id,
            issue_type=issue_type,
            evidence_ids=[item.evidence_id for item in values],
            stakeholder_ids=stakeholder_ids
            if stakeholder_ids is not None
            else sorted({item.stakeholder_id for item in values if item.stakeholder_id is not None}),
            related_decision=related_decision,
            summary=summary,
            blocking=blocking,
        )


class _ModelIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    issue_type: AlignmentIssueType
    related_decision: str | None = Field(default=None, max_length=240)


class _ModelAlignment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    issues: list[_ModelIssue] = Field(default_factory=list, max_length=10)


class _ModelProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    proposal: str = Field(min_length=1, max_length=_MAX_PROPOSAL_TEXT)


class HybridAlignmentEngine(AlignmentEngine):
    """Adds non-authoritative model suggestions to deterministic checks."""

    _SYSTEM_PROMPT = """Identify advisory issues from the supplied public evidence. Return exactly:
{"issues":[{"issue_type":string,"summary":string,"related_decision":string|null}]}
Do not infer acceptance, approval, authority, identities, private facts, or state changes.
Your output is advisory only and cannot resolve any issue."""

    def __init__(self, mandate_id: UUID, client: JsonModelClient) -> None:
        super().__init__(mandate_id)
        self._client = client
        self.last_fallback_reason: str | None = None

    def analyze(
        self,
        plan: MandatePlan,
        evidence: Iterable[ShareableEvidence],
        assignments: Iterable[StakeholderAssignment],
        *,
        private_blocker_count: int = 0,
        contribution_evidence: Sequence[EvidenceItem] | None = None,
        decisions: Sequence[EngagementDecision] = (),
        availability_assignment_ids: set[UUID] | frozenset[UUID] = frozenset(),
    ) -> AlignmentReport:
        assignment_values = list(assignments)
        public_evidence = self._public_evidence(evidence)
        if contribution_evidence is not None:
            valid_assignment_ids = self._valid_assignment_ids(plan, assignment_values)
            eligible_evidence_ids = self._eligible_contribution_evidence_ids(
                assignment_values,
                contribution_evidence,
                valid_assignment_ids,
            )
            public_evidence = [
                item
                for item in public_evidence
                if item.evidence_id in eligible_evidence_ids
            ]
        report = super().analyze(
            plan,
            public_evidence,
            assignment_values,
            private_blocker_count=private_blocker_count,
            contribution_evidence=contribution_evidence,
            decisions=decisions,
            availability_assignment_ids=availability_assignment_ids,
        )
        self.last_fallback_reason = None
        try:
            suggestion = _ModelAlignment.model_validate_json(
                json.dumps(
                    self._client.complete_json(
                        self._SYSTEM_PROMPT, self._safe_payload(plan, public_evidence)
                    )
                )
            )
        except ModelFailure as error:
            self.last_fallback_reason = error.reason
            return report
        except (TypeError, ValueError, ValidationError):
            self.last_fallback_reason = "invalid_schema"
            return report

        safe_issues = [
            self._issue(
                issue.issue_type,
                "An advisory human review item was identified.",
                related_decision=issue.related_decision,
                blocking=False,
            )
            for issue in suggestion.issues
            if (issue.related_decision is None or issue.related_decision in plan.required_decisions)
        ]
        return report.model_copy(update={"issues": [*report.issues, *safe_issues]})

    @staticmethod
    def _safe_payload(plan: MandatePlan, evidence: list[ShareableEvidence]) -> str:
        return json.dumps(
            {
                "required_decisions": plan.required_decisions,
                "evidence": [item.model_dump(mode="json") for item in evidence],
            },
            separators=(",", ":"),
        )


class NegotiationCoordinator:
    """Persists a maximum of two explicit, authenticated human response rounds."""

    _SYSTEM_PROMPT = """Suggest advisory wording for a draft proposal. Return exactly:
{"proposal":string}
Do not infer acceptance, approval, authority, identities, private facts, or state changes.
The suggestion is untrusted and cannot approve or mutate the mandate."""

    def __init__(
        self,
        repository: SqlAlchemyHumanWireRepository,
        client: JsonModelClient | None = None,
        response_window: timedelta = timedelta(days=1),
    ) -> None:
        self.repository = repository
        self._client = client
        self._response_window = response_window

    def create_proposal(
        self, mandate: Mandate, report: AlignmentReport, round_number: int, now: datetime
    ) -> Proposal:
        proposal = self.prepare_proposal(mandate, report, round_number, now)
        with self.repository.transaction() as unit:
            unit.add_proposal(proposal)
            unit.append_event(
                mandate.mandate_id,
                DomainEvent(
                    event_type="proposal.created",
                    created_at=now,
                    idempotency_key=f"proposal:create:{proposal.proposal_id}",
                    metadata={"proposal_id": str(proposal.proposal_id), "round_number": round_number},
                ),
            )
        return proposal

    def prepare_proposal(
        self, mandate: Mandate, report: AlignmentReport, round_number: int, now: datetime
    ) -> Proposal:
        """Build a proposal for callers that must persist it in a larger transaction."""
        if round_number not in (1, 2):
            raise NegotiationLimitReached("Negotiation is limited to exactly two rounds")
        return Proposal(
            proposal_id=uuid4(),
            mandate_id=mandate.mandate_id,
            round_number=round_number,
            text=self._draft_text(report),
            issue_ids=[issue.issue_id for issue in report.issues if issue.blocking],
            required_respondent_ids=[
                stakeholder.person_ref for stakeholder in mandate.plan.stakeholders if stakeholder.required
            ],
            created_at=now,
            expires_at=now + self._response_window,
        )

    def record_response(
        self,
        proposal: Proposal,
        stakeholder_id: str,
        kind: ProposalResponseKind,
        change_text: str | None,
        source_message_id: str,
        now: datetime,
    ) -> ProposalResponse:
        persisted = self.repository.get_proposal(proposal.proposal_id)
        if persisted is None:
            raise KeyError(str(proposal.proposal_id))
        if not source_message_id.strip():
            raise ValueError("source_message_id is required")
        key = self._response_key(persisted.proposal_id, source_message_id)
        existing = self._response_with_key(persisted.proposal_id, key)
        if existing is not None:
            return existing
        if persisted.state is not ProposalState.AWAITING_RESPONSES:
            raise ValueError("proposal is not awaiting responses")
        if now >= persisted.expires_at:
            raise ValueError("proposal has expired")
        if stakeholder_id not in persisted.required_respondent_ids:
            raise ValueError("stakeholder is not a required proposal respondent")
        cleaned_change = change_text.strip() if change_text else None
        if kind is ProposalResponseKind.CHANGE and not cleaned_change:
            raise ValueError("CHANGE requires requested change text")
        if kind is not ProposalResponseKind.CHANGE:
            cleaned_change = None
        proposal = persisted
        response = ProposalResponse(
            response_id=uuid4(),
            proposal_id=proposal.proposal_id,
            stakeholder_id=stakeholder_id,
            response=kind,
            change_text=cleaned_change,
            source_message_id=source_message_id,
            created_at=now,
            idempotency_key=key,
        )
        try:
            with self.repository.transaction() as unit:
                persisted_in_transaction = unit.get_proposal(proposal.proposal_id)
                if persisted_in_transaction is None:
                    raise KeyError(str(proposal.proposal_id))
                existing_in_transaction = unit.list_proposal_responses(proposal.proposal_id)
                duplicate = next(
                    (item for item in existing_in_transaction if item.idempotency_key == key), None
                )
                if duplicate is not None:
                    return duplicate
                if persisted_in_transaction.state is not ProposalState.AWAITING_RESPONSES:
                    raise ValueError("proposal is not awaiting responses")
                if now >= persisted_in_transaction.expires_at:
                    raise ValueError("proposal has expired")
                unit.add_proposal_response(response)
                all_responses = unit.list_proposal_responses(proposal.proposal_id)
                respondents = {item.stakeholder_id for item in all_responses}
                if set(proposal.required_respondent_ids).issubset(respondents):
                    outcome = self._outcome(proposal, all_responses)
                    state = (
                        ProposalState.ALIGNED
                        if outcome is NegotiationOutcome.ALIGNED
                        else ProposalState.UNRESOLVED
                    )
                    unit.save_proposal(persisted_in_transaction.model_copy(update={"state": state}))
                unit.append_event(
                    proposal.mandate_id,
                    DomainEvent(
                        event_type="proposal.response_recorded",
                        created_at=now,
                        idempotency_key=f"event:{key}",
                        actor_id=stakeholder_id,
                        metadata={"proposal_id": str(proposal.proposal_id), "status": kind.value},
                    ),
                )
        except IntegrityError:
            existing = self._response_with_key(proposal.proposal_id, key)
            if existing is not None:
                return existing
            raise
        return response

    def evaluate_round(self, proposal: Proposal) -> NegotiationOutcome:
        persisted = self.repository.get_proposal(proposal.proposal_id)
        if persisted is None:
            raise KeyError(str(proposal.proposal_id))
        if persisted.state is ProposalState.ALIGNED:
            return NegotiationOutcome.ALIGNED
        responses = self.repository.list_proposal_responses(persisted.proposal_id)
        return self._outcome(persisted, responses)

    def open_change_requests(self, proposal: Proposal) -> list[str]:
        return [
            response.change_text
            for response in self._latest_responses(
                self.repository.list_proposal_responses(proposal.proposal_id)
            ).values()
            if response.response is ProposalResponseKind.CHANGE and response.change_text is not None
        ]

    def _draft_text(self, report: AlignmentReport) -> str:
        fallback = self._fallback_draft(report)
        if self._client is None:
            return (_DRAFT_PREFIX + fallback)[:_MAX_PROPOSAL_TEXT]
        try:
            _ModelProposal.model_validate(
                self._client.complete_json(self._SYSTEM_PROMPT, self._safe_draft_payload(report))
            )
        except ModelFailure:
            return (_DRAFT_PREFIX + fallback)[:_MAX_PROPOSAL_TEXT]
        except (TypeError, ValueError, ValidationError):
            return (_DRAFT_PREFIX + fallback)[:_MAX_PROPOSAL_TEXT]
        advisory = "An advisory drafting suggestion is available for human review. "
        return (_DRAFT_PREFIX + advisory + fallback)[:_MAX_PROPOSAL_TEXT]

    @staticmethod
    def _safe_draft_payload(report: AlignmentReport) -> str:
        return json.dumps(
            {
                "issues": [
                    {
                        "issue_type": issue.issue_type.value,
                        "related_decision": issue.related_decision,
                    }
                    for issue in report.issues
                    if issue.blocking
                ]
            },
            separators=(",", ":"),
        )

    @staticmethod
    def _fallback_draft(report: AlignmentReport) -> str:
        issues = [issue.summary for issue in report.issues if issue.blocking]
        summary = "; ".join(issues) if issues else "Review the proposed decision."
        return f"{summary} Reply ACCEPT, REJECT, or CHANGE with a requested change."

    @staticmethod
    def _response_key(proposal_id: UUID, source_message_id: str) -> str:
        value = "|".join([str(proposal_id), source_message_id])
        return f"proposal-response:{hashlib.sha256(value.encode()).hexdigest()[:48]}"

    @staticmethod
    def _outcome(proposal: Proposal, responses: list[ProposalResponse]) -> NegotiationOutcome:
        by_stakeholder = NegotiationCoordinator._latest_responses(responses)
        aligned = (
            set(by_stakeholder) == set(proposal.required_respondent_ids)
            and all(response.response is ProposalResponseKind.ACCEPT for response in by_stakeholder.values())
        )
        if aligned:
            return NegotiationOutcome.ALIGNED
        if proposal.round_number == 1:
            return NegotiationOutcome.NEXT_ROUND
        return NegotiationOutcome.MEETING_REQUIRED

    @staticmethod
    def _latest_responses(responses: list[ProposalResponse]) -> dict[str, ProposalResponse]:
        return {
            response.stakeholder_id: response
            for response in responses
        }

    def _response_with_key(self, proposal_id: UUID, key: str) -> ProposalResponse | None:
        return next(
            (
                response
                for response in self.repository.list_proposal_responses(proposal_id)
                if response.idempotency_key == key
            ),
            None,
        )
