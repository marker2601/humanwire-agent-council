from humanwire.studio_models import CoordinationRequest

_PRIMARY_PARTICIPANTS = (
    "inform",
    "ack",
    "quick-a",
    "quick-b",
    "structured",
    "approval",
    "availability",
)


def launch_request(**updates: object) -> CoordinationRequest:
    values: dict[str, object] = {
        "template_id": "launch-decision",
        "objective": "Set up a decision meeting tomorrow to approve the launch plan.",
        "requester_name": "Alex Morgan",
        "requester_role": "manager",
        "participant_ids": _PRIMARY_PARTICIPANTS,
        "target_timing": "tomorrow",
        "custom_date": None,
        "include_conflict": True,
        "agent_mode": "standard",
    }
    values.update(updates)
    return CoordinationRequest.model_validate(values)


def conflict_request(**updates: object) -> CoordinationRequest:
    values: dict[str, object] = {
        **launch_request().model_dump(mode="python"),
        "template_id": "cross-team-conflict",
        "objective": (
            "Resolve the launch-readiness disagreement between Product, "
            "Engineering, and Risk."
        ),
        "requester_role": "program_lead",
        "participant_ids": ("quick-a", "quick-b", "structured", "approval"),
    }
    values.update(updates)
    return CoordinationRequest.model_validate(values)
