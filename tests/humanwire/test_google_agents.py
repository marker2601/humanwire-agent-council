from __future__ import annotations

import pytest

from humanwire.domain import EngagementType
from humanwire.google_agents import GoogleAdkCoordinator, GoogleSpecialist
from humanwire.persona_runtime import PersonaProfile, SyntheticIntent


@pytest.mark.parametrize(
    ("contract", "intents", "expected"),
    [
        (EngagementType.INFORM, (SyntheticIntent.ACKNOWLEDGE,), GoogleSpecialist.OUTREACH),
        (
            EngagementType.ACKNOWLEDGE,
            (SyntheticIntent.ACKNOWLEDGE,),
            GoogleSpecialist.OUTREACH,
        ),
        (
            EngagementType.QUICK_RESPONSE,
            (SyntheticIntent.ANSWER,),
            GoogleSpecialist.EVIDENCE,
        ),
        (
            EngagementType.STRUCTURED_INTERVIEW,
            (SyntheticIntent.INTERVIEW_RESPONSE,),
            GoogleSpecialist.CONFLICT,
        ),
        (
            EngagementType.REVIEW_APPROVAL,
            (SyntheticIntent.APPROVE, SyntheticIntent.CHANGE),
            GoogleSpecialist.AUTHORITY,
        ),
        (
            EngagementType.AVAILABILITY,
            (SyntheticIntent.AVAILABILITY,),
            GoogleSpecialist.SCHEDULING,
        ),
        (
            EngagementType.QUICK_RESPONSE,
            (SyntheticIntent.ACCEPT_PROPOSAL, SyntheticIntent.CHANGE_PROPOSAL),
            GoogleSpecialist.PROPOSAL,
        ),
        (
            EngagementType.QUICK_RESPONSE,
            (SyntheticIntent.CONFIRM_EVIDENCE,),
            GoogleSpecialist.EVIDENCE,
        ),
    ],
)
def test_coordinator_routes_each_assignment_to_the_bounded_specialist(
    contract: EngagementType,
    intents: tuple[SyntheticIntent, ...],
    expected: GoogleSpecialist,
) -> None:
    profile = PersonaProfile(
        role="Bounded stakeholder",
        private_facts=(),
        allowed_intents=intents,
        engagement_contract=contract,
    )

    selected = GoogleAdkCoordinator().select(profile)

    assert selected.specialist is expected
    assert selected.agent_name == f"humanwire_{expected.value}_specialist"


def test_specialist_catalog_includes_every_approved_responsibility() -> None:
    assert {item.specialist for item in GoogleAdkCoordinator().catalog()} == set(
        GoogleSpecialist
    )
