"""Deterministic, isolated, fictional public HumanWire fixture."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import FastAPI

from humanwire.alignment import AlignmentReport
from humanwire.config import Settings
from humanwire.database import create_session_factory
from humanwire.domain import (
    AlignmentIssue,
    AlignmentIssueType,
    AvailabilityWindow,
    Channel,
    Direction,
    DomainEvent,
    EngagementDecision,
    EngagementDecisionKind,
    EngagementType,
    EvidenceItem,
    EvidenceStatus,
    EvidenceType,
    EvidenceVisibility,
    InterviewSession,
    Mandate,
    MandatePlan,
    MandateState,
    PlannedStakeholder,
    StakeholderAssignment,
    StakeholderState,
)
from humanwire.evidence import shareable_evidence
from humanwire.meetings import MeetingCoordinator
from humanwire.repository import SqlAlchemyHumanWireRepository
from humanwire.web import create_app
from humanwire.workflow import json_windows

DEMO_NOW = datetime(2026, 8, 11, 18, 0, tzinfo=UTC)


def _id(kind: str, value: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"humanwire:demo:{kind}:{value}")


def _plan(objective: str, stakeholders: list[PlannedStakeholder]) -> MandatePlan:
    return MandatePlan(
        objective=objective,
        required_decisions=["Approve the smallest safe coverage plan"],
        stakeholders=stakeholders,
        deadline=DEMO_NOW + timedelta(days=3),
        completion_conditions=["Required stakeholders respond", "Decision owner reviews"],
    )


def _mandate(
    token: str,
    initiator_id: str,
    state: MandateState,
    objective: str,
    plan: MandatePlan,
    created_at: datetime,
) -> Mandate:
    terminal = state in {MandateState.ALIGNED, MandateState.MEETING_READY}
    return Mandate(
        mandate_id=_id("mandate", token),
        token=token,
        initiator_id=initiator_id,
        origin_channel=Channel.TELEGRAM,
        origin_conversation_id=f"demo-origin-{token.lower()}",
        origin_message_id=f"demo-message-{token.lower()}",
        redacted_request=objective,
        objective=objective,
        plan=plan,
        state=state,
        created_at=created_at,
        updated_at=created_at + timedelta(minutes=45),
        expires_at=created_at + timedelta(days=1),
        completed_at=created_at + timedelta(minutes=45) if terminal else None,
        idempotency_key=f"demo-mandate-{token.lower()}",
    )


def _assignment(
    mandate: Mandate,
    person_id: str,
    department: str,
    direction: Direction,
    state: StakeholderState,
    reason: str,
    offset: int,
    *,
    engagement_type: EngagementType,
    required: bool = True,
    interview: bool,
    active_route_index: int = 0,
) -> StakeholderAssignment:
    assignment_id = _id("assignment", f"{mandate.token}:{person_id}")
    started = mandate.created_at + timedelta(minutes=offset)
    complete = state is StakeholderState.COMPLETE
    return StakeholderAssignment(
        assignment_id=assignment_id,
        mandate_id=mandate.mandate_id,
        person_id=person_id,
        department=department,
        direction=direction,
        reason=reason,
        required=required,
        engagement_type=engagement_type,
        response_required=engagement_type is not EngagementType.INFORM,
        state=state,
        route_ids=[f"demo-route-{person_id}-primary", f"demo-route-{person_id}-alternate"],
        active_route_index=active_route_index,
        attempt_count=2 if active_route_index else 1,
        interview_id=_id("interview", f"{mandate.token}:{person_id}") if interview else None,
        first_contact_at=started,
        last_delivery_at=started + timedelta(minutes=2),
        next_action_at=(
            DEMO_NOW + timedelta(minutes=30)
            if state is StakeholderState.ALTERNATE_CHANNEL
            else None
        ),
        acknowledged_at=(
            started + timedelta(minutes=3)
            if engagement_type is not EngagementType.INFORM
            and state
            in {
                StakeholderState.ACKNOWLEDGED,
                StakeholderState.INTERVIEWING,
                StakeholderState.COMPLETE,
            }
            else None
        ),
        completed_at=started + timedelta(minutes=8) if complete else None,
    )


def _interview(
    mandate: Mandate,
    assignment: StakeholderAssignment,
    questions: list[str],
    *,
    current_question_index: int | None = None,
    current_channel: Channel = Channel.EMAIL,
) -> InterviewSession:
    completed = assignment.state is StakeholderState.COMPLETE
    index = (
        len(questions)
        if completed
        else (current_question_index if current_question_index is not None else 0)
    )
    return InterviewSession(
        session_id=assignment.interview_id,
        mandate_id=mandate.mandate_id,
        assignment_id=assignment.assignment_id,
        questions=questions,
        current_question_index=index,
        current_channel=current_channel,
        current_route_id=assignment.route_ids[assignment.active_route_index],
        current_conversation_id=f"demo-conversation-{assignment.person_id}",
        channel_history=(
            [Channel.EMAIL, Channel.TELEGRAM]
            if assignment.active_route_index
            else [current_channel]
        ),
        acknowledged_at=assignment.acknowledged_at,
        started_at=assignment.first_contact_at,
        updated_at=assignment.last_delivery_at,
        completed_at=assignment.completed_at,
    )


def _event(
    mandate: Mandate,
    index: int,
    event_type: str,
    *,
    person_id: str | None = None,
    department: str | None = None,
    direction: Direction | None = None,
    channel: Channel | None = None,
    previous_state: str | None = None,
    new_state: str | None = None,
    actor_id: str | None = None,
    metadata: dict[str, str] | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_type=event_type,
        created_at=mandate.created_at + timedelta(minutes=index),
        idempotency_key=f"demo-event-{mandate.token.lower()}-{index:02d}",
        actor_id=mandate.initiator_id if index == 0 else actor_id,
        person_id=person_id,
        department=department,
        direction=direction,
        channel=channel,
        previous_state=previous_state,
        new_state=new_state,
        metadata=metadata or {},
    )


def _seed_people(repository: SqlAlchemyHumanWireRepository) -> None:
    people = {
        "arun-patel": ("Arun Patel", "Support Manager"),
        "eli-torres": ("Eli Torres", "US Team Lead"),
        "sora-kim": ("Sora Kim", "APAC Team Lead"),
        "priya-shah": ("Priya Shah", "People Partner"),
        "nora-chen": ("Nora Chen", "VP Support"),
        "maya-brooks": ("Maya Brooks", "COO"),
        "inez-ward": ("Inez Ward", "Launch Observer"),
        "nora-okafor": ("Nora Okafor", "VP Support"),
        "maya-chen": ("Maya Chen", "COO"),
        "lena-ortiz": ("Lena Ortiz", "Operations Manager"),
    }
    for person_id, (name, role) in people.items():
        repository.set_runtime_status(
            f"public.person:{person_id}",
            json.dumps({"name": name, "role": role}, sort_keys=True),
            DEMO_NOW,
        )


def _seed_primary(repository: SqlAlchemyHumanWireRepository) -> None:
    planned = [
        PlannedStakeholder(
            person_ref="eli-torres",
            reason="Confirm US launch coverage",
            direction=Direction.DOWNWARD,
            engagement_type=EngagementType.QUICK_RESPONSE,
            questions=["Can the US team cover the launch window?"],
        ),
        PlannedStakeholder(
            person_ref="sora-kim",
            reason="Confirm APAC launch coverage",
            direction=Direction.DOWNWARD,
            engagement_type=EngagementType.QUICK_RESPONSE,
            questions=["Can the APAC team cover the launch window?"],
        ),
        PlannedStakeholder(
            person_ref="priya-shah",
            reason="Confirm staffing policy constraints",
            direction=Direction.LATERAL,
            engagement_type=EngagementType.STRUCTURED_INTERVIEW,
            questions=[
                "Which staffing rule applies?",
                "What constraint affects coverage?",
                "What safe option can you support?",
            ],
        ),
        PlannedStakeholder(
            person_ref="nora-chen",
            reason="Acknowledge executive sponsorship receipt",
            direction=Direction.UPWARD,
            engagement_type=EngagementType.ACKNOWLEDGE,
            questions=[],
        ),
        PlannedStakeholder(
            person_ref="maya-brooks",
            reason="Review the approval request",
            direction=Direction.UPWARD,
            engagement_type=EngagementType.REVIEW_APPROVAL,
            questions=[],
        ),
        PlannedStakeholder(
            person_ref="inez-ward",
            reason="Receive the launch coordination update",
            direction=Direction.DOWNWARD,
            required=False,
            engagement_type=EngagementType.INFORM,
            response_required=False,
            questions=[],
        ),
    ]
    mandate = _mandate(
        "HW-2411",
        "arun-patel",
        MandateState.INTERVIEWING,
        "Prepare approved weekend launch coverage",
        _plan("Prepare approved weekend launch coverage", planned),
        DEMO_NOW - timedelta(hours=3),
    )
    repository.add_mandate(mandate)
    assignments = [
        _assignment(
            mandate,
            "eli-torres",
            "US Support",
            Direction.DOWNWARD,
            StakeholderState.COMPLETE,
            "Confirm US launch coverage",
            4,
            engagement_type=EngagementType.QUICK_RESPONSE,
            interview=True,
        ),
        _assignment(
            mandate,
            "sora-kim",
            "APAC Support",
            Direction.DOWNWARD,
            StakeholderState.COMPLETE,
            "Confirm APAC launch coverage",
            5,
            engagement_type=EngagementType.QUICK_RESPONSE,
            interview=True,
        ),
        _assignment(
            mandate,
            "priya-shah",
            "People",
            Direction.LATERAL,
            StakeholderState.INTERVIEWING,
            "Confirm staffing policy constraints",
            6,
            engagement_type=EngagementType.STRUCTURED_INTERVIEW,
            interview=True,
            active_route_index=1,
        ),
        _assignment(
            mandate,
            "nora-chen",
            "Support Leadership",
            Direction.UPWARD,
            StakeholderState.COMPLETE,
            "Acknowledge executive sponsorship receipt",
            7,
            engagement_type=EngagementType.ACKNOWLEDGE,
            interview=False,
        ),
        _assignment(
            mandate,
            "maya-brooks",
            "Executive",
            Direction.UPWARD,
            StakeholderState.AWAITING_ACKNOWLEDGEMENT,
            "Review the approval request",
            8,
            engagement_type=EngagementType.REVIEW_APPROVAL,
            interview=False,
        ),
        _assignment(
            mandate,
            "inez-ward",
            "Observers",
            Direction.DOWNWARD,
            StakeholderState.COMPLETE,
            "Receive the launch coordination update",
            9,
            engagement_type=EngagementType.INFORM,
            required=False,
            interview=False,
        ),
    ]
    planned_by_person = {item.person_ref: item for item in planned}
    for assignment in assignments:
        repository.add_assignment(assignment)
        if assignment.interview_id is not None:
            repository.add_interview(
                _interview(
                    mandate,
                    assignment,
                    planned_by_person[assignment.person_id].questions,
                    current_question_index=(
                        1 if assignment.person_id == "priya-shah" else None
                    ),
                    current_channel=(
                        Channel.TELEGRAM
                        if assignment.person_id in {"sora-kim", "priya-shah"}
                        else Channel.EMAIL
                    ),
                )
            )

    events = [
        (0, "mandate.created", None, None, None, None, "received", "planned"),
        (1, "engagement.plan_previewed", None, None, None, None, "planned", "planned"),
        (2, "engagement.plan_released", None, None, None, None, "planned", "interviewing"),
        (3, "mandate.interviewing", None, None, None, None, "planned", "interviewing"),
        (4, "engagement.quick_response_sent", "eli-torres", "US Support", Direction.DOWNWARD, Channel.EMAIL, None, "awaiting_acknowledgement"),
        (5, "engagement.quick_response_sent", "sora-kim", "APAC Support", Direction.DOWNWARD, Channel.TELEGRAM, None, "awaiting_acknowledgement"),
        (6, "engagement.structured_interview_sent", "priya-shah", "People", Direction.LATERAL, Channel.EMAIL, None, "awaiting_acknowledgement"),
        (7, "engagement.acknowledgement_sent", "nora-chen", "Support Leadership", Direction.UPWARD, Channel.EMAIL, None, "awaiting_acknowledgement"),
        (8, "engagement.approval_pending", "maya-brooks", "Executive", Direction.UPWARD, Channel.EMAIL, None, "awaiting_acknowledgement"),
        (9, "engagement.inform_delivered", "inez-ward", "Observers", Direction.DOWNWARD, Channel.EMAIL, "delivered", "complete"),
        (10, "engagement.quick_response_completed", "eli-torres", "US Support", Direction.DOWNWARD, Channel.EMAIL, "interviewing", "complete"),
        (11, "engagement.quick_response_completed", "sora-kim", "APAC Support", Direction.DOWNWARD, Channel.TELEGRAM, "interviewing", "complete"),
        (12, "engagement.acknowledged", "nora-chen", "Support Leadership", Direction.UPWARD, Channel.EMAIL, "awaiting_acknowledgement", "complete"),
        (13, "engagement.structured_interview_reminder", "priya-shah", "People", Direction.LATERAL, Channel.EMAIL, "awaiting_acknowledgement", "follow_up_due"),
        (14, "engagement.structured_interview_alternate_selected", "priya-shah", "People", Direction.LATERAL, Channel.TELEGRAM, "follow_up_due", "alternate_channel"),
        (15, "engagement.structured_interview_progressed", "priya-shah", "People", Direction.LATERAL, Channel.TELEGRAM, "alternate_channel", "interviewing"),
    ]
    for values in events:
        index, event_type, person_id, department, direction, channel, previous, new = values
        repository.append_event(
            mandate.mandate_id,
            _event(
                mandate,
                index,
                event_type,
                person_id=person_id,
                department=department,
                direction=direction,
                channel=channel,
                previous_state=previous,
                new_state=new,
            ),
        )

    evidence_specs = [
        ("us", "eli-torres", EvidenceVisibility.SHAREABLE, EvidenceType.COMMITMENT, "The US lead can staff one voluntary on-call shift."),
        ("policy", "priya-shah", EvidenceVisibility.ANONYMOUS, EvidenceType.CONSTRAINT, "Coverage requires a documented handoff."),
        ("apac", "sora-kim", EvidenceVisibility.SHAREABLE, EvidenceType.COMMITMENT, "The APAC lead confirmed the launch handoff."),
        ("private", "priya-shah", EvidenceVisibility.PRIVATE, EvidenceType.CONSTRAINT, "Private medical leave details must remain confidential."),
    ]
    by_person = {item.person_id: item for item in assignments}
    for offset, (label, person_id, visibility, evidence_type, statement) in enumerate(
        evidence_specs, start=20
    ):
        assignment = by_person[person_id]
        repository.add_evidence(
            EvidenceItem(
                evidence_id=_id("evidence", f"{mandate.token}:{label}"),
                mandate_id=mandate.mandate_id,
                assignment_id=assignment.assignment_id,
                stakeholder_id=person_id,
                evidence_type=evidence_type,
                statement=statement,
                visibility=visibility,
                status=EvidenceStatus.CONFIRMED,
                source_message_id=f"demo-source-{label}",
                channel=Channel.EMAIL,
                created_at=mandate.created_at + timedelta(minutes=offset),
            )
        )


def _seed_aligned(repository: SqlAlchemyHumanWireRepository) -> None:
    plan = _plan(
        "Confirm incident review ownership",
        [
            PlannedStakeholder(
                person_ref="nora-okafor",
                reason="Confirm ownership",
                direction=Direction.UPWARD,
                engagement_type=EngagementType.REVIEW_APPROVAL,
                questions=[],
            )
        ],
    )
    mandate = _mandate(
        "HW-2412",
        "arun-patel",
        MandateState.ALIGNED,
        "Confirm incident review ownership",
        plan,
        DEMO_NOW - timedelta(hours=2),
    )
    repository.add_mandate(mandate)
    assignment = _assignment(
        mandate,
        "nora-okafor",
        "Support Leadership",
        Direction.UPWARD,
        StakeholderState.COMPLETE,
        "Confirm incident review ownership",
        2,
        engagement_type=EngagementType.REVIEW_APPROVAL,
        interview=False,
    )
    repository.add_assignment(assignment)
    repository.add_engagement_decision(
        EngagementDecision(
            decision_id=_id("decision", f"{mandate.token}:approve"),
            mandate_id=mandate.mandate_id,
            assignment_id=assignment.assignment_id,
            stakeholder_id=assignment.person_id,
            response=EngagementDecisionKind.APPROVE,
            source_message_id="demo-source-aligned-approval",
            created_at=mandate.created_at + timedelta(minutes=3),
            idempotency_key=f"demo-decision-{mandate.token.lower()}",
        )
    )
    repository.append_event(mandate.mandate_id, _event(mandate, 0, "mandate.created"))
    repository.append_event(mandate.mandate_id, _event(mandate, 3, "mandate.aligned"))


def _seed_meeting_ready(repository: SqlAlchemyHumanWireRepository) -> None:
    plan = _plan(
        "Resolve the launch approval decision",
        [
            PlannedStakeholder(
                person_ref="maya-chen",
                reason="Make the approval decision",
                direction=Direction.UPWARD,
                engagement_type=EngagementType.REVIEW_APPROVAL,
                questions=[],
            )
        ],
    )
    mandate = _mandate(
        "HW-2413",
        "lena-ortiz",
        MandateState.MEETING_READY,
        "Resolve the launch approval decision",
        plan,
        DEMO_NOW - timedelta(hours=1),
    )
    repository.add_mandate(mandate)
    assignment = _assignment(
        mandate,
        "maya-chen",
        "Executive",
        Direction.UPWARD,
        StakeholderState.COMPLETE,
        "Make the approval decision",
        2,
        engagement_type=EngagementType.REVIEW_APPROVAL,
        interview=False,
    )
    repository.add_assignment(assignment)
    repository.add_engagement_decision(
        EngagementDecision(
            decision_id=_id("decision", f"{mandate.token}:change"),
            mandate_id=mandate.mandate_id,
            assignment_id=assignment.assignment_id,
            stakeholder_id=assignment.person_id,
            response=EngagementDecisionKind.CHANGE,
            change_text="Use a narrower approval scope before proceeding.",
            source_message_id="demo-source-meeting-change",
            created_at=mandate.created_at + timedelta(minutes=3),
            idempotency_key=f"demo-decision-{mandate.token.lower()}",
        )
    )
    issue = AlignmentIssue(
        issue_id=_id("issue", mandate.token),
        mandate_id=mandate.mandate_id,
        issue_type=AlignmentIssueType.HARD_CONSTRAINT,
        stakeholder_ids=["maya-chen"],
        related_decision="Approve the launch plan",
        summary="The accountable owner requested a narrower approval scope.",
        blocking=True,
    )
    repository.add_issue(issue)
    evidence = EvidenceItem(
        evidence_id=_id("evidence", f"{mandate.token}:fact"),
        mandate_id=mandate.mandate_id,
        assignment_id=assignment.assignment_id,
        stakeholder_id="maya-chen",
        evidence_type=EvidenceType.FACT,
        statement="The decision package is ready for review.",
        visibility=EvidenceVisibility.SHAREABLE,
        status=EvidenceStatus.CONFIRMED,
        source_message_id="demo-source-meeting-fact",
        channel=Channel.EMAIL,
        created_at=mandate.created_at + timedelta(minutes=4),
    )
    repository.add_evidence(evidence)
    report = AlignmentReport(mandate_id=mandate.mandate_id, issues=[issue], is_aligned=False)
    coordinator = MeetingCoordinator(mandate.initiator_id)
    attendees = coordinator.required_attendees(report, [assignment], mandate.initiator_id)
    package_created_at = mandate.created_at + timedelta(minutes=30)
    window = AvailabilityWindow(
        start=datetime(2026, 8, 14, 20, 0, tzinfo=UTC),
        end=datetime(2026, 8, 14, 21, 0, tzinfo=UTC),
    )
    for attendee_id in attendees:
        coordinator.record_availability(attendee_id, [window])
        repository.set_runtime_status(
            f"availability:{mandate.mandate_id}:{attendee_id}",
            json_windows(type("Command", (), {"windows": [window]})()),
            package_created_at,
        )
    slot = coordinator.find_overlap()
    assert slot is not None
    package = coordinator.build_package(
        mandate.plan,
        report,
        [assignment],
        mandate.initiator_id,
        shareable_evidence([evidence]),
        proposed_slot=slot,
        created_at=package_created_at,
    )
    repository.save_meeting_package(package)
    repository.append_event(mandate.mandate_id, _event(mandate, 0, "mandate.created"))
    repository.append_event(
        mandate.mandate_id,
        _event(
            mandate,
            30,
            "meeting.package_created",
            person_id="maya-chen",
            department="Executive",
            direction=Direction.UPWARD,
            previous_state="scheduling",
            new_state="meeting_ready",
            actor_id=mandate.initiator_id,
            metadata={"meeting_id": str(package.meeting_id)},
        ),
    )


def create_demo_app() -> FastAPI:
    """Construct a fresh in-memory app without environment, files, or network access."""
    defaults = {
        name: field.get_default(call_default_factory=True)
        for name, field in Settings.model_fields.items()
    }
    settings = Settings.model_validate(
        defaults
        | {
            "database_url": "sqlite://",
            "organization_path": Path("demo-organization-not-loaded.json"),
            "public_demo": True,
        }
    )
    repository = SqlAlchemyHumanWireRepository(create_session_factory("sqlite://"))
    _seed_people(repository)
    _seed_primary(repository)
    _seed_aligned(repository)
    _seed_meeting_ready(repository)
    return create_app(repository, settings, clock=lambda: DEMO_NOW, demo_mode=True)
