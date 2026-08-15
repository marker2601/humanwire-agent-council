from datetime import date

import pytest
from pydantic import ValidationError

import humanwire.synthetic as synthetic_module
from humanwire.domain import EngagementType
from humanwire.studio_models import (
    CoordinationRequest,
    RequesterRole,
    StudioAgentMode,
    TargetTiming,
    coordination_target_date,
    product_catalog,
)
from humanwire.synthetic import build_coordination_scenario
from tests.humanwire.studio_fixtures import launch_request


def test_product_catalog_uses_approved_names_templates_and_copy() -> None:
    catalog = product_catalog()
    assert [person.display_name for person in catalog.stakeholders] == [
        "Maya Chen",
        "Nora Jensen",
        "Priya Shah",
        "Marcus Reed",
        "Anika Rao",
        "Sofia Alvarez",
        "Daniel Brooks",
        "Elena Torres",
    ]
    assert [person.engagement_label for person in catalog.stakeholders] == [
        "Inform",
        "Acknowledge",
        "Quick response",
        "Quick response",
        "Structured interview",
        "Review and approval",
        "Availability",
        "Review and approval",
    ]
    launch = next(item for item in catalog.templates if item.template_id == "launch-decision")
    assert launch.objective == "Set up a decision meeting tomorrow to approve the launch plan."
    assert launch.requester_role is RequesterRole.MANAGER
    assert launch.target_timing is TargetTiming.TOMORROW
    assert launch.include_conflict is True


def test_coordination_dates_use_the_injected_local_reference_date() -> None:
    assert coordination_target_date(
        launch_request(target_timing="tomorrow"),
        reference_date=date(2026, 8, 14),
    ) == date(2026, 8, 15)
    assert coordination_target_date(
        launch_request(target_timing="next_business_day"),
        reference_date=date(2026, 8, 14),
    ) == date(2026, 8, 17)


def test_approval_change_catalog_label_matches_its_existing_contract() -> None:
    change_persona = next(
        item
        for item in synthetic_module.default_synthetic_scenario().personas
        if item.persona_id == "approval-change"
    )
    change_card = next(
        item for item in product_catalog().stakeholders if item.persona_id == "approval-change"
    )

    assert synthetic_module._contract_for(change_persona) is EngagementType.REVIEW_APPROVAL
    assert change_card.engagement_label == "Review and approval"


def test_coordination_request_is_strict_bounded_and_unique() -> None:
    valid = CoordinationRequest(
        template_id="launch-decision",
        objective="Set up a decision meeting tomorrow to approve the launch plan.",
        requester_name="Alex Morgan",
        requester_role="manager",
        participant_ids=[
            "inform",
            "ack",
            "quick-a",
            "quick-b",
            "structured",
            "approval",
            "availability",
        ],
        target_timing="tomorrow",
        include_conflict=True,
        agent_mode="standard",
    )
    assert valid.agent_mode is StudioAgentMode.STANDARD
    for mutation in (
        {**valid.model_dump(), "unknown": True},
        {**valid.model_dump(), "participant_ids": ["quick-a", "quick-a"]},
        {**valid.model_dump(), "objective": "x" * 1001},
        {**valid.model_dump(), "requester_name": "Someone Else"},
    ):
        with pytest.raises(ValidationError):
            CoordinationRequest.model_validate(mutation)


def test_build_coordination_scenario_is_single_story_with_product_identities() -> None:
    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="launch-run-001")
    assert scenario.scenario_id == "launch-run-001"
    assert [item.persona_id for item in scenario.personas] == [
        "synthetic-manager",
        "inform",
        "ack",
        "quick-a",
        "quick-b",
        "structured",
        "approval",
        "availability",
    ]
    assert scenario.personas[0].display_name == "Alex Morgan"
    assert scenario.personas[0].role == "Strategy manager"
    assert "approval-change" not in {item.persona_id for item in scenario.personas}
    assert scenario.personas[5].display_name == "Anika Rao"
    assert scenario.personas[5].role == "Risk & compliance lead"


def test_build_coordination_scenario_omits_change_persona_without_template_support() -> None:
    request = launch_request(
        participant_ids=(
            "inform",
            "ack",
            "quick-a",
            "quick-b",
            "structured",
            "approval",
            "availability",
            "approval-change",
        )
    )

    scenario = build_coordination_scenario(request, seed=7, scenario_id="launch-run-001")

    assert "approval-change" not in {item.persona_id for item in scenario.personas}
