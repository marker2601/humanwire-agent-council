"""Pure deterministic selection and override policy for stakeholder engagements."""

from __future__ import annotations

import re
from collections.abc import Iterable

from humanwire.domain import EngagementType, PlannedStakeholder


class EngagementPolicyError(ValueError):
    """Raised when an engagement cannot be selected or overridden safely."""


_APPROVAL_TERMS = ("approve", "approval", "authorize", "sign off", "decision owner")
_SCHEDULING_TERMS = ("availability", "schedule", "time window")
_RECEIPT_TERMS = ("acknowledge", "acknowledgement", "receipt", "sponsor", "sponsorship")
_AWARENESS_TERMS = ("inform", "informed", "notify", "notification", "awareness", "visibility")


def _contains_term(text: str, terms: Iterable[str]) -> bool:
    for term in terms:
        escaped_term = re.escape(term).replace(r"\ ", r"\s+")
        if re.search(rf"(?<!\w){escaped_term}(?!\w)", text, flags=re.IGNORECASE):
            return True
    return False


class EngagementPolicy:
    """Chooses the minimum safe engagement without consulting external state."""

    def select(
        self,
        stakeholder: PlannedStakeholder,
        *,
        objective: str,
        required_decisions: list[str],
    ) -> PlannedStakeholder:
        text = "\n".join(
            [objective, *required_decisions, stakeholder.reason, *stakeholder.questions]
        )
        question_count = len(stakeholder.questions)
        if question_count > 5:
            raise EngagementPolicyError("Stakeholder question count exceeds five")

        has_approval = _contains_term(text, _APPROVAL_TERMS)
        has_scheduling = _contains_term(text, _SCHEDULING_TERMS)
        has_receipt = _contains_term(text, _RECEIPT_TERMS)
        has_awareness = _contains_term(text, _AWARENESS_TERMS)

        if has_approval:
            if question_count:
                raise EngagementPolicyError(
                    "Approval work with substantive questions is contradictory"
                )
            if not stakeholder.required:
                raise EngagementPolicyError("Optional approval work is ambiguous")
            return self._selected(stakeholder, EngagementType.REVIEW_APPROVAL, True)

        if has_scheduling:
            if question_count:
                raise EngagementPolicyError(
                    "Availability work with substantive questions is contradictory"
                )
            if not stakeholder.required:
                raise EngagementPolicyError("Optional availability work is ambiguous")
            return self._selected(stakeholder, EngagementType.AVAILABILITY, True)

        if question_count:
            if not stakeholder.required:
                raise EngagementPolicyError("Optional substantive questions are ambiguous")
            if question_count <= 2:
                return self._selected(stakeholder, EngagementType.QUICK_RESPONSE, True)
            return self._selected(stakeholder, EngagementType.STRUCTURED_INTERVIEW, True)

        if has_receipt and has_awareness:
            raise EngagementPolicyError("Receipt and awareness work is ambiguous")
        if has_receipt:
            if not stakeholder.required:
                raise EngagementPolicyError("Optional receipt work is ambiguous")
            return self._selected(stakeholder, EngagementType.ACKNOWLEDGE, True)
        if has_awareness:
            if stakeholder.required:
                raise EngagementPolicyError("Required awareness work is ambiguous")
            return self._selected(stakeholder, EngagementType.INFORM, False)
        raise EngagementPolicyError("Stakeholder contribution is ambiguous")

    def validate_override(
        self,
        current: PlannedStakeholder,
        requested: EngagementType,
    ) -> EngagementType:
        question_count = len(current.questions)
        if requested in {
            EngagementType.INFORM,
            EngagementType.ACKNOWLEDGE,
            EngagementType.REVIEW_APPROVAL,
            EngagementType.AVAILABILITY,
        } and question_count:
            raise EngagementPolicyError("Requested engagement cannot discard saved questions")
        if requested is EngagementType.QUICK_RESPONSE and question_count not in range(1, 3):
            raise EngagementPolicyError("Quick response requires one or two saved questions")
        if requested is EngagementType.STRUCTURED_INTERVIEW and question_count not in range(3, 6):
            raise EngagementPolicyError("Structured interview requires three to five questions")

        if current.engagement_type in {
            EngagementType.REVIEW_APPROVAL,
            EngagementType.AVAILABILITY,
        }:
            if requested is current.engagement_type:
                return requested
            raise EngagementPolicyError("Required authority or availability type cannot change")

        if current.engagement_type in {
            EngagementType.QUICK_RESPONSE,
            EngagementType.STRUCTURED_INTERVIEW,
        }:
            if requested is current.engagement_type:
                return requested
            if (
                current.engagement_type is EngagementType.STRUCTURED_INTERVIEW
                and requested is EngagementType.QUICK_RESPONSE
                and question_count <= 2
            ):
                return requested
            raise EngagementPolicyError("Required evidence work cannot be weakened")

        if current.engagement_type in {EngagementType.INFORM, EngagementType.ACKNOWLEDGE}:
            if current.engagement_type is EngagementType.INFORM and current.required:
                raise EngagementPolicyError("Required work cannot use inform")
            if requested is current.engagement_type:
                return requested
            if (
                not current.required
                and question_count == 0
                and requested in {EngagementType.INFORM, EngagementType.ACKNOWLEDGE}
            ):
                return requested

        raise EngagementPolicyError("Requested engagement override is not allowed")

    @staticmethod
    def _selected(
        stakeholder: PlannedStakeholder,
        engagement_type: EngagementType,
        response_required: bool,
    ) -> PlannedStakeholder:
        return stakeholder.model_copy(
            update={
                "engagement_type": engagement_type,
                "response_required": response_required,
            }
        )
