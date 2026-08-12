"""Evidence-based alignment checks and bounded, human-authorized negotiation."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import IntegrityError

from humanwire.domain import (
    AlignmentIssue,
    AlignmentIssueType,
    DomainEvent,
    EvidenceStatus,
    EvidenceType,
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
_AUTHORITY_CLAIM = re.compile(r"\b(?:accept(?:ed|ance)?|approv(?:e|ed|al)|aligned)\b", re.IGNORECASE)
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
    ) -> AlignmentReport:
        public_evidence = self._public_evidence(evidence)
        required_assignments = [assignment for assignment in assignments if assignment.required]
        issues = self._deterministic_issues(plan, public_evidence, required_assignments)
        if private_blocker_count:
            issues.append(
                self._issue(
                    AlignmentIssueType.PRIVATE_BLOCKER,
                    "Private constraints require direct human resolution.",
                    blocking=True,
                )
            )

        covered = self._covered_decisions(plan, public_evidence)
        planned_required_ids = {
            stakeholder.person_ref for stakeholder in plan.stakeholders if stakeholder.required
        }
        completed_ids = {
            assignment.person_id
            for assignment in required_assignments
            if assignment.state is StakeholderState.COMPLETE
        }
        all_required_complete = bool(planned_required_ids) and planned_required_ids.issubset(
            completed_ids
        )
        agreements = self._agreements(public_evidence, issues)
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
    ) -> list[AlignmentIssue]:
        issues: list[AlignmentIssue] = []
        for assignment in required_assignments:
            if assignment.state is not StakeholderState.COMPLETE:
                issues.append(
                    self._issue(
                        AlignmentIssueType.MISSING_EVIDENCE,
                        "A required stakeholder has not completed an authenticated response.",
                        stakeholder_ids=[assignment.person_id],
                        blocking=True,
                    )
                )

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
        normalized = {self._normalize(item.statement) for item in facts}
        if len(facts) > 1 and len(normalized) > 1:
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
    summary: str = Field(min_length=1, max_length=300)
    related_decision: str | None = Field(default=None, max_length=240)


class _ModelAlignment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    agreements: list[str] = Field(default_factory=list, max_length=10)
    issues: list[_ModelIssue] = Field(default_factory=list, max_length=10)


class HybridAlignmentEngine(AlignmentEngine):
    """Adds non-authoritative model suggestions to deterministic checks."""

    _SYSTEM_PROMPT = """Summarize only the supplied public evidence. Return exactly:
{"agreements":[string],"issues":[{"issue_type":string,"summary":string,"related_decision":string|null}]}
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
    ) -> AlignmentReport:
        public_evidence = self._public_evidence(evidence)
        report = super().analyze(
            plan, public_evidence, assignments, private_blocker_count=private_blocker_count
        )
        self.last_fallback_reason = None
        try:
            suggestion = _ModelAlignment.model_validate(
                self._client.complete_json(self._SYSTEM_PROMPT, self._safe_payload(plan, public_evidence))
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
                issue.summary,
                related_decision=issue.related_decision,
                blocking=False,
            )
            for issue in suggestion.issues
            if issue.related_decision is None or issue.related_decision in plan.required_decisions
        ]
        safe_agreements = [
            f"Suggested: {text.strip()}"
            for text in suggestion.agreements
            if text.strip() and not _AUTHORITY_CLAIM.search(text)
        ][:10]
        return report.model_copy(
            update={"agreements": [*report.agreements, *safe_agreements], "issues": [*report.issues, *safe_issues]}
        )

    @staticmethod
    def _safe_payload(plan: MandatePlan, evidence: list[ShareableEvidence]) -> str:
        return json.dumps(
            {
                "required_decisions": plan.required_decisions,
                "evidence": [item.model_dump(mode="json") for item in evidence],
            },
            separators=(",", ":"),
        )


class _ModelProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    proposal: str = Field(min_length=1, max_length=_MAX_PROPOSAL_TEXT)


class NegotiationCoordinator:
    """Persists a maximum of two explicit, authenticated human response rounds."""

    _SYSTEM_PROMPT = """Draft a concise proposal based only on the supplied safe issue summaries.
Return exactly {"proposal": string}. The proposal must be no more than 600 characters.
Do not claim anyone accepted, approved, has authority, or changed system state."""

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
        if round_number not in (1, 2):
            raise NegotiationLimitReached("Negotiation is limited to exactly two rounds")
        proposal = Proposal(
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

    def record_response(
        self,
        proposal: Proposal,
        stakeholder_id: str,
        kind: ProposalResponseKind,
        change_text: str | None,
        now: datetime,
    ) -> ProposalResponse:
        if stakeholder_id not in proposal.required_respondent_ids:
            raise ValueError("stakeholder is not a required proposal respondent")
        cleaned_change = change_text.strip() if change_text else None
        if kind is ProposalResponseKind.CHANGE and not cleaned_change:
            raise ValueError("CHANGE requires requested change text")
        if kind is not ProposalResponseKind.CHANGE:
            cleaned_change = None
        key = self._response_key(proposal.proposal_id, stakeholder_id, kind, cleaned_change)
        existing = self._response_with_key(proposal.proposal_id, key)
        if existing is not None:
            return existing
        response = ProposalResponse(
            response_id=uuid4(),
            proposal_id=proposal.proposal_id,
            stakeholder_id=stakeholder_id,
            response=kind,
            change_text=cleaned_change,
            source_message_id=f"proposal-response:{response_key_fragment(key)}",
            created_at=now,
            idempotency_key=key,
        )
        try:
            with self.repository.transaction() as unit:
                existing_in_transaction = unit.list_proposal_responses(proposal.proposal_id)
                duplicate = next(
                    (item for item in existing_in_transaction if item.idempotency_key == key), None
                )
                if duplicate is not None:
                    return duplicate
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
                    unit.save_proposal(proposal.model_copy(update={"state": state}))
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
        responses = self.repository.list_proposal_responses(proposal.proposal_id)
        return self._outcome(proposal, responses)

    def open_change_requests(self, proposal: Proposal) -> list[str]:
        return [
            response.change_text
            for response in self.repository.list_proposal_responses(proposal.proposal_id)
            if response.response is ProposalResponseKind.CHANGE and response.change_text is not None
        ]

    def _draft_text(self, report: AlignmentReport) -> str:
        body = self._fallback_draft(report)
        if self._client is not None:
            try:
                payload = {
                    "issues": [
                        {"summary": issue.summary, "related_decision": issue.related_decision}
                        for issue in report.issues
                    ],
                    "private_blocker_count": report.private_blocker_count,
                }
                candidate = _ModelProposal.model_validate(
                    self._client.complete_json(self._SYSTEM_PROMPT, json.dumps(payload, separators=(",", ":")))
                ).proposal.strip()
                if candidate and not _AUTHORITY_CLAIM.search(candidate):
                    body = candidate
            except (ModelFailure, TypeError, ValueError, ValidationError):
                pass
        return (_DRAFT_PREFIX + body)[:_MAX_PROPOSAL_TEXT]

    @staticmethod
    def _fallback_draft(report: AlignmentReport) -> str:
        issues = [issue.summary for issue in report.issues if issue.blocking]
        summary = "; ".join(issues) if issues else "Review the proposed decision."
        return f"{summary} Reply ACCEPT, REJECT, or CHANGE with a requested change."

    @staticmethod
    def _response_key(
        proposal_id: UUID, stakeholder_id: str, kind: ProposalResponseKind, change_text: str | None
    ) -> str:
        value = "|".join([str(proposal_id), stakeholder_id, kind.value, change_text or ""])
        return f"proposal-response:{hashlib.sha256(value.encode()).hexdigest()[:48]}"

    @staticmethod
    def _outcome(proposal: Proposal, responses: list[ProposalResponse]) -> NegotiationOutcome:
        by_stakeholder = {
            response.stakeholder_id: response
            for response in sorted(responses, key=lambda item: (item.created_at, str(item.response_id)))
        }
        aligned = (
            set(by_stakeholder) == set(proposal.required_respondent_ids)
            and all(response.response is ProposalResponseKind.ACCEPT for response in by_stakeholder.values())
        )
        if aligned:
            return NegotiationOutcome.ALIGNED
        if proposal.round_number == 1:
            return NegotiationOutcome.NEXT_ROUND
        return NegotiationOutcome.MEETING_REQUIRED

    def _response_with_key(self, proposal_id: UUID, key: str) -> ProposalResponse | None:
        return next(
            (
                response
                for response in self.repository.list_proposal_responses(proposal_id)
                if response.idempotency_key == key
            ),
            None,
        )


def response_key_fragment(key: str) -> str:
    """Keep source correlation opaque and bounded without repeating content."""
    return key.rsplit(":", maxsplit=1)[-1]
