"""Deterministic coordinator for bounded Google ADK specialist agents."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from humanwire.domain import EngagementType
from humanwire.persona_runtime import PersonaProfile, SyntheticIntent


class GoogleSpecialist(StrEnum):
    PLANNING = "planning"
    OUTREACH = "outreach"
    EVIDENCE = "evidence"
    CONFLICT = "conflict"
    PROPOSAL = "proposal"
    AUTHORITY = "authority"
    SCHEDULING = "scheduling"


@dataclass(frozen=True, slots=True)
class GoogleSpecialistDefinition:
    specialist: GoogleSpecialist
    agent_name: str
    description: str
    instruction: str


def _definition(
    specialist: GoogleSpecialist,
    description: str,
    instruction: str,
) -> GoogleSpecialistDefinition:
    return GoogleSpecialistDefinition(
        specialist=specialist,
        agent_name=f"humanwire_{specialist.value}_specialist",
        description=description,
        instruction=(
            "You are a bounded HumanWire specialist. Return exactly one typed stakeholder "
            "decision using only the supplied role, conversation, and allowed intents. "
            "Never claim identity, evidence confirmation, approval, availability, delivery, "
            "or workflow state that the supplied assignment does not authorize. "
            + instruction
        ),
    )


_SPECIALISTS = (
    _definition(
        GoogleSpecialist.PLANNING,
        "Produces a bounded coordination recommendation.",
        "Recommend only the minimum necessary next coordination action.",
    ),
    _definition(
        GoogleSpecialist.OUTREACH,
        "Handles acknowledgements and concise role-appropriate outreach.",
        "Respond to the request without inventing facts or additional stakeholders.",
    ),
    _definition(
        GoogleSpecialist.EVIDENCE,
        "Interprets bounded answers and evidence contributions.",
        "Distinguish assertions from confirmable answers; never confirm evidence yourself.",
    ),
    _definition(
        GoogleSpecialist.CONFLICT,
        "Surfaces disagreement and answers targeted interview questions.",
        "Explain the issue precisely; do not treat a proposed change as approval.",
    ),
    _definition(
        GoogleSpecialist.PROPOSAL,
        "Reviews a proposal and responds within the supplied contract.",
        "Accept or request change only when that intent is explicitly allowed.",
    ),
    _definition(
        GoogleSpecialist.AUTHORITY,
        "Handles an explicit review-and-approval assignment.",
        "Record only the allowed approval or change response; never infer authority.",
    ),
    _definition(
        GoogleSpecialist.SCHEDULING,
        "Provides availability after the workflow requests it.",
        "Return only supplied availability; never declare the meeting scheduled.",
    ),
)
_BY_SPECIALIST = {item.specialist: item for item in _SPECIALISTS}


class GoogleAdkCoordinator:
    """Route an authoritative HumanWire assignment to one ADK specialist."""

    def catalog(self) -> tuple[GoogleSpecialistDefinition, ...]:
        return _SPECIALISTS

    def select(self, profile: PersonaProfile) -> GoogleSpecialistDefinition:
        profile = PersonaProfile.model_validate(profile)
        intents = frozenset(profile.allowed_intents)
        if profile.engagement_contract is EngagementType.AVAILABILITY:
            specialist = GoogleSpecialist.SCHEDULING
        elif profile.engagement_contract is EngagementType.REVIEW_APPROVAL:
            specialist = GoogleSpecialist.AUTHORITY
        elif profile.engagement_contract is EngagementType.STRUCTURED_INTERVIEW:
            specialist = GoogleSpecialist.CONFLICT
        elif intents & {
            SyntheticIntent.ACCEPT_PROPOSAL,
            SyntheticIntent.CHANGE_PROPOSAL,
        }:
            specialist = GoogleSpecialist.PROPOSAL
        elif profile.engagement_contract in {
            EngagementType.INFORM,
            EngagementType.ACKNOWLEDGE,
        }:
            specialist = GoogleSpecialist.OUTREACH
        else:
            specialist = GoogleSpecialist.EVIDENCE
        return _BY_SPECIALIST[specialist]
