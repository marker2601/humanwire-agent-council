import pytest

from humanwire.domain import Direction, EngagementType, PlannedStakeholder
from humanwire.engagement_policy import EngagementPolicy, EngagementPolicyError


@pytest.fixture
def policy() -> EngagementPolicy:
    return EngagementPolicy()


def _stakeholder(
    *,
    reason: str,
    required: bool,
    questions: list[str],
    engagement_type: EngagementType,
    response_required: bool,
) -> PlannedStakeholder:
    return PlannedStakeholder(
        person_ref="stakeholder",
        reason=reason,
        direction=Direction.LATERAL,
        required=required,
        engagement_type=engagement_type,
        response_required=response_required,
        questions=questions,
    )


@pytest.mark.parametrize(
    ("reason", "required", "questions", "expected"),
    [
        ("Keep Finance informed.", False, [], EngagementType.INFORM),
        ("Acknowledge sponsorship of the rollout.", True, [], EngagementType.ACKNOWLEDGE),
        (
            "Confirm the deployment date.",
            True,
            ["Which date is committed?"],
            EngagementType.QUICK_RESPONSE,
        ),
        (
            "Gather facts and constraints.",
            True,
            ["Fact?", "Constraint?", "Commitment?"],
            EngagementType.STRUCTURED_INTERVIEW,
        ),
        ("Approve the launch decision.", True, [], EngagementType.REVIEW_APPROVAL),
        ("Provide meeting availability.", True, [], EngagementType.AVAILABILITY),
    ],
)
def test_policy_selects_minimum_engagement(
    policy: EngagementPolicy,
    reason: str,
    required: bool,
    questions: list[str],
    expected: EngagementType,
) -> None:
    candidate = _stakeholder(
        reason=reason,
        required=required,
        questions=questions,
        engagement_type=expected,
        response_required=expected is not EngagementType.INFORM,
    )

    selected = policy.select(
        candidate,
        objective=reason,
        required_decisions=["Complete the stated mandate"],
    )

    assert selected.engagement_type is expected
    assert selected.response_required is (expected is not EngagementType.INFORM)


def test_policy_overrides_advisory_inform_for_required_approval(
    policy: EngagementPolicy,
) -> None:
    candidate = _stakeholder(
        reason="Decision owner must approve launch.",
        required=True,
        questions=[],
        engagement_type=EngagementType.INFORM,
        response_required=False,
    )

    selected = policy.select(
        candidate,
        objective="Launch the service",
        required_decisions=["Authorize launch"],
    )

    assert selected.engagement_type is EngagementType.REVIEW_APPROVAL
    assert selected.response_required is True
    assert selected.questions == []


def test_policy_overrides_advisory_acknowledgement_for_required_questions(
    policy: EngagementPolicy,
) -> None:
    candidate = PlannedStakeholder.model_construct(
        person_ref="stakeholder",
        reason="Provide the required facts.",
        direction=Direction.LATERAL,
        required=True,
        engagement_type=EngagementType.ACKNOWLEDGE,
        response_required=True,
        questions=["Which constraint applies?", "Which date is committed?"],
    )

    selected = policy.select(
        candidate,
        objective="Confirm operating constraints",
        required_decisions=["Complete the stated mandate"],
    )

    assert selected.engagement_type is EngagementType.QUICK_RESPONSE
    assert selected.questions == candidate.questions


def test_policy_keeps_optional_zero_question_notification_as_inform(
    policy: EngagementPolicy,
) -> None:
    candidate = _stakeholder(
        reason="Notify Finance for awareness.",
        required=False,
        questions=[],
        engagement_type=EngagementType.ACKNOWLEDGE,
        response_required=True,
    )

    selected = policy.select(
        candidate,
        objective="Share launch visibility",
        required_decisions=["Complete the stated mandate"],
    )

    assert selected.engagement_type is EngagementType.INFORM
    assert selected.response_required is False


@pytest.mark.parametrize(
    ("question_count", "expected"),
    [
        (1, EngagementType.QUICK_RESPONSE),
        (2, EngagementType.QUICK_RESPONSE),
        (3, EngagementType.STRUCTURED_INTERVIEW),
        (5, EngagementType.STRUCTURED_INTERVIEW),
    ],
)
def test_policy_enforces_question_count_boundaries(
    policy: EngagementPolicy,
    question_count: int,
    expected: EngagementType,
) -> None:
    candidate = _stakeholder(
        reason="Gather required facts.",
        required=True,
        questions=[f"Question {index}?" for index in range(question_count)],
        engagement_type=expected,
        response_required=True,
    )

    selected = policy.select(
        candidate,
        objective="Gather facts",
        required_decisions=["Complete the stated mandate"],
    )

    assert selected.engagement_type is expected


def test_policy_rejects_six_questions(policy: EngagementPolicy) -> None:
    candidate = PlannedStakeholder.model_construct(
        person_ref="stakeholder",
        reason="Gather required facts.",
        direction=Direction.LATERAL,
        required=True,
        engagement_type=EngagementType.STRUCTURED_INTERVIEW,
        response_required=True,
        questions=[f"Question {index}?" for index in range(6)],
    )

    with pytest.raises(EngagementPolicyError, match="question"):
        policy.select(
            candidate,
            objective="Gather facts",
            required_decisions=["Complete the stated mandate"],
        )


@pytest.mark.parametrize("authority_text", ["Approve", "APPROVAL", "authorize", "sign off", "decision owner"])
def test_approval_terms_take_precedence_case_insensitively(
    policy: EngagementPolicy, authority_text: str
) -> None:
    candidate = _stakeholder(
        reason=f"{authority_text} the launch.",
        required=True,
        questions=[],
        engagement_type=EngagementType.INFORM,
        response_required=False,
    )

    selected = policy.select(
        candidate,
        objective="Launch",
        required_decisions=["Complete the stated mandate"],
    )

    assert selected.engagement_type is EngagementType.REVIEW_APPROVAL


@pytest.mark.parametrize("schedule_text", ["availability", "SCHEDULE", "time window"])
def test_scheduling_terms_take_precedence_case_insensitively(
    policy: EngagementPolicy, schedule_text: str
) -> None:
    candidate = _stakeholder(
        reason=f"Provide a {schedule_text}.",
        required=True,
        questions=[],
        engagement_type=EngagementType.INFORM,
        response_required=False,
    )

    selected = policy.select(
        candidate,
        objective="Arrange the meeting",
        required_decisions=["Complete the stated mandate"],
    )

    assert selected.engagement_type is EngagementType.AVAILABILITY


@pytest.mark.parametrize("reason", ["Approve launch.", "Provide meeting availability."])
def test_policy_rejects_authority_or_scheduling_with_substantive_questions(
    policy: EngagementPolicy, reason: str
) -> None:
    candidate = _stakeholder(
        reason=reason,
        required=True,
        questions=["What do you recommend?"],
        engagement_type=EngagementType.QUICK_RESPONSE,
        response_required=True,
    )

    with pytest.raises(EngagementPolicyError, match="contradictory"):
        policy.select(
            candidate,
            objective="Complete the mandate",
            required_decisions=["Complete the stated mandate"],
        )


@pytest.mark.parametrize(
    ("reason", "required"),
    [
        ("Acknowledge receipt.", False),
        ("Notify Finance for awareness.", True),
        ("Provide an update.", True),
        ("Provide an update.", False),
    ],
)
def test_policy_rejects_required_or_optional_ambiguity(
    policy: EngagementPolicy, reason: str, required: bool
) -> None:
    candidate = _stakeholder(
        reason=reason,
        required=required,
        questions=[],
        engagement_type=EngagementType.INFORM,
        response_required=False,
    )

    with pytest.raises(EngagementPolicyError, match="ambiguous"):
        policy.select(
            candidate,
            objective="Complete the mandate",
            required_decisions=["Complete the stated mandate"],
        )


def test_policy_uses_whole_words_for_authority_terms(policy: EngagementPolicy) -> None:
    candidate = _stakeholder(
        reason="Notify Finance that a draft was disapproved.",
        required=False,
        questions=[],
        engagement_type=EngagementType.INFORM,
        response_required=False,
    )

    selected = policy.select(
        candidate,
        objective="Awareness only",
        required_decisions=["Complete the stated mandate"],
    )

    assert selected.engagement_type is EngagementType.INFORM


@pytest.mark.parametrize(
    ("current", "requested"),
    [
        (
            _stakeholder(
                reason="Notify Finance.",
                required=False,
                questions=[],
                engagement_type=EngagementType.INFORM,
                response_required=False,
            ),
            EngagementType.ACKNOWLEDGE,
        ),
        (
            _stakeholder(
                reason="Optional acknowledgement.",
                required=False,
                questions=[],
                engagement_type=EngagementType.ACKNOWLEDGE,
                response_required=True,
            ),
            EngagementType.INFORM,
        ),
        (
            PlannedStakeholder.model_construct(
                person_ref="stakeholder",
                reason="Two saved questions.",
                direction=Direction.LATERAL,
                required=True,
                engagement_type=EngagementType.STRUCTURED_INTERVIEW,
                response_required=True,
                questions=["Fact?", "Constraint?"],
            ),
            EngagementType.QUICK_RESPONSE,
        ),
    ],
)
def test_override_allows_only_safe_changes_without_mutating_current(
    policy: EngagementPolicy,
    current: PlannedStakeholder,
    requested: EngagementType,
) -> None:
    original = current.model_copy(deep=True)

    assert policy.validate_override(current, requested) is requested
    assert current == original
    assert current.questions == original.questions


@pytest.mark.parametrize(
    ("current", "requested"),
    [
        (
            _stakeholder(
                reason="Approve launch.",
                required=True,
                questions=[],
                engagement_type=EngagementType.REVIEW_APPROVAL,
                response_required=True,
            ),
            EngagementType.INFORM,
        ),
        (
            _stakeholder(
                reason="Provide availability.",
                required=True,
                questions=[],
                engagement_type=EngagementType.AVAILABILITY,
                response_required=True,
            ),
            EngagementType.ACKNOWLEDGE,
        ),
        (
            _stakeholder(
                reason="Provide a fact.",
                required=True,
                questions=["Fact?"],
                engagement_type=EngagementType.QUICK_RESPONSE,
                response_required=True,
            ),
            EngagementType.ACKNOWLEDGE,
        ),
        (
            _stakeholder(
                reason="Provide facts.",
                required=True,
                questions=["One?", "Two?", "Three?"],
                engagement_type=EngagementType.STRUCTURED_INTERVIEW,
                response_required=True,
            ),
            EngagementType.QUICK_RESPONSE,
        ),
        (
            _stakeholder(
                reason="Notify Finance.",
                required=False,
                questions=[],
                engagement_type=EngagementType.INFORM,
                response_required=False,
            ),
            EngagementType.QUICK_RESPONSE,
        ),
        (
            _stakeholder(
                reason="Provide a fact.",
                required=True,
                questions=["Fact?"],
                engagement_type=EngagementType.QUICK_RESPONSE,
                response_required=True,
            ),
            EngagementType.INFORM,
        ),
        (
            _stakeholder(
                reason="Invalid required notification.",
                required=True,
                questions=[],
                engagement_type=EngagementType.INFORM,
                response_required=False,
            ),
            EngagementType.INFORM,
        ),
    ],
)
def test_override_rejects_unsafe_changes(
    policy: EngagementPolicy,
    current: PlannedStakeholder,
    requested: EngagementType,
) -> None:
    with pytest.raises(EngagementPolicyError):
        policy.validate_override(current, requested)
