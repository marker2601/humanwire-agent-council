from datetime import UTC, datetime
from uuid import uuid4

import pytest

from humanwire.alignment import AlignmentReport
from humanwire.domain import (
    AlignmentIssue,
    AlignmentIssueType,
    AvailabilityWindow,
    Channel,
    Direction,
    EvidenceItem,
    EvidenceStatus,
    EvidenceType,
    EvidenceVisibility,
    MandatePlan,
    StakeholderAssignment,
    StakeholderState,
)
from humanwire.evidence import ShareableEvidence
from humanwire.meetings import MeetingCoordinator, render_ics
from humanwire.messages import (
    render_availability_request,
    render_meeting_confirmation,
    render_meeting_reminder,
)


@pytest.fixture
def coordinator() -> MeetingCoordinator:
    return MeetingCoordinator(initiator_id="manager")


def sample_plan() -> MandatePlan:
    return MandatePlan(
        objective="Resolve the customer, launch; review\nplan",
        required_decisions=["Approve the launch plan"],
        stakeholders=[
            {
                "person_ref": "manager",
                "reason": "Owns operations",
                "direction": Direction.UPWARD,
                "questions": ["What is the operating plan?"],
            }
        ],
        completion_conditions=["A decision record is confirmed"],
    )


def assignments() -> list[StakeholderAssignment]:
    mandate_id = uuid4()
    return [
        StakeholderAssignment(
            assignment_id=uuid4(),
            mandate_id=mandate_id,
            person_id="manager",
            department="Operations",
            direction=Direction.UPWARD,
            reason="Owns operations",
            required=True,
            state=StakeholderState.COMPLETE,
            route_ids=["private@example.test"],
        ),
        StakeholderAssignment(
            assignment_id=uuid4(),
            mandate_id=mandate_id,
            person_id="vp-people",
            department="People",
            direction=Direction.LATERAL,
            reason="Owns people policy",
            required=True,
            state=StakeholderState.COMPLETE,
            route_ids=["vp-people-private@example.test"],
        ),
        StakeholderAssignment(
            assignment_id=uuid4(),
            mandate_id=mandate_id,
            person_id="observer",
            department="Legal",
            direction=Direction.LATERAL,
            reason="Optional observer",
            required=False,
            state=StakeholderState.COMPLETE,
            route_ids=["observer-private@example.test"],
        ),
    ]


def sample_report() -> AlignmentReport:
    mandate_id = uuid4()
    return AlignmentReport(
        mandate_id=mandate_id,
        agreements=["Capacity is limited to two engineers."],
        issues=[
            AlignmentIssue(
                issue_id=uuid4(),
                mandate_id=mandate_id,
                issue_type=AlignmentIssueType.AUTHORITY_GAP,
                stakeholder_ids=["vp-people", "observer"],
                related_decision="Approve the launch plan",
                summary="The decision owner must resolve the authority gap.",
                blocking=True,
            )
        ],
        covered_decisions=[],
        is_aligned=False,
    )


def windows_in_chicago_and_london() -> dict[str, list[AvailabilityWindow]]:
    return {
        "manager": [
            AvailabilityWindow(
                start=datetime.fromisoformat("2026-08-14T15:00:00-05:00"),
                end=datetime.fromisoformat("2026-08-14T16:30:00-05:00"),
            )
        ],
        "vp-people": [
            AvailabilityWindow(
                start=datetime.fromisoformat("2026-08-14T20:00:00+01:00"),
                end=datetime.fromisoformat("2026-08-14T21:30:00+01:00"),
            )
        ],
    }


def test_smallest_attendee_set_includes_issue_owners_and_decision_owner(
    coordinator: MeetingCoordinator,
) -> None:
    attendees = coordinator.required_attendees(sample_report(), assignments(), decision_owner_id="coo")

    assert attendees == {"manager", "vp-people", "coo"}


def test_overlap_is_calculated_in_utc(coordinator: MeetingCoordinator) -> None:
    slot = coordinator.find_overlap(windows_in_chicago_and_london())

    assert slot is not None
    assert slot.start.isoformat() == "2026-08-14T20:00:00+00:00"
    assert slot.end.isoformat() == "2026-08-14T20:30:00+00:00"


def test_missing_required_availability_is_a_blocker_and_never_invents_a_slot(
    coordinator: MeetingCoordinator,
) -> None:
    coordinator.required_attendees(sample_report(), assignments(), decision_owner_id="coo")
    coordinator.record_availability(
        "manager",
        [
            AvailabilityWindow(
                start=datetime(2026, 8, 14, 14, 0, tzinfo=UTC),
                end=datetime(2026, 8, 14, 15, 0, tzinfo=UTC),
            )
        ],
    )

    result = coordinator.find_overlap()

    assert result is None
    assert coordinator.availability_retry == "Awaiting confirmed availability from: coo, vp-people"


def test_non_overlapping_windows_return_one_explicit_retry_result(
    coordinator: MeetingCoordinator,
) -> None:
    result = coordinator.find_overlap(
        {
            "manager": [
                AvailabilityWindow(
                    start=datetime(2026, 8, 14, 14, 0, tzinfo=UTC),
                    end=datetime(2026, 8, 14, 14, 30, tzinfo=UTC),
                )
            ],
            "vp-people": [
                AvailabilityWindow(
                    start=datetime(2026, 8, 14, 15, 0, tzinfo=UTC),
                    end=datetime(2026, 8, 14, 15, 30, tzinfo=UTC),
                )
            ],
        }
    )

    assert result is None
    assert coordinator.availability_retry == "No shared availability; request another availability window."


def test_overlap_rounds_up_to_a_30_minute_boundary_across_dst_offsets(
    coordinator: MeetingCoordinator,
) -> None:
    slot = coordinator.find_overlap(
        {
            "chicago": [
                AvailabilityWindow(
                    start=datetime.fromisoformat("2026-11-01T01:15:00-05:00"),
                    end=datetime.fromisoformat("2026-11-01T02:15:00-06:00"),
                )
            ],
            "london": [
                AvailabilityWindow(
                    start=datetime.fromisoformat("2026-11-01T06:00:00+00:00"),
                    end=datetime.fromisoformat("2026-11-01T08:00:00+00:00"),
                )
            ],
        }
    )

    assert slot == AvailabilityWindow(
        start=datetime(2026, 11, 1, 6, 30, tzinfo=UTC),
        end=datetime(2026, 11, 1, 7, 0, tzinfo=UTC),
    )


def test_overlap_rejects_non_30_minute_duration(coordinator: MeetingCoordinator) -> None:
    with pytest.raises(ValueError, match="30-minute"):
        coordinator.find_overlap(windows_in_chicago_and_london(), duration=datetime.resolution)


def test_package_and_ics_are_deterministic_and_exclude_private_evidence(
    coordinator: MeetingCoordinator,
) -> None:
    report = sample_report()
    public_evidence = ShareableEvidence(
        evidence_id=uuid4(),
        evidence_type=EvidenceType.FACT,
        statement="Capacity is limited to two engineers.",
        stakeholder_id="manager",
        status=EvidenceStatus.CONFIRMED,
        related_decision="Approve the launch plan",
        deadline=None,
        resource=None,
    )
    private_evidence = EvidenceItem(
        evidence_id=uuid4(),
        mandate_id=report.mandate_id,
        assignment_id=uuid4(),
        stakeholder_id="manager",
        evidence_type=EvidenceType.CONSTRAINT,
        statement="Private: the employee medical leave details are confidential.",
        visibility=EvidenceVisibility.PRIVATE,
        status=EvidenceStatus.CONFIRMED,
        source_message_id="private-message",
        channel=Channel.EMAIL,
        created_at=datetime(2026, 8, 11, 12, 0, tzinfo=UTC),
    )
    slot = AvailabilityWindow(
        start=datetime(2026, 8, 14, 20, 0, tzinfo=UTC),
        end=datetime(2026, 8, 14, 20, 30, tzinfo=UTC),
    )

    package = coordinator.build_package(
        sample_plan(),
        report,
        assignments(),
        decision_owner_id="coo",
        evidence=[public_evidence, private_evidence],
        proposed_slot=slot,
        created_at=datetime(2026, 8, 11, 12, 0, tzinfo=UTC),
    )
    ics = render_ics(package).decode("utf-8")

    assert package.purpose == "Resolve the customer, launch; review\nplan"
    assert package.decision_owner_id == "coo"
    assert package.required_attendee_ids == ["coo", "manager", "vp-people"]
    assert package.agreed_facts == ["Capacity is limited to two engineers."]
    assert package.open_decisions == ["Approve the launch plan"]
    assert package.agenda == [
        "1. Confirm agreed facts",
        "2. Resolve each open decision in severity order",
        "3. Assign owner and deadline for each commitment",
        "4. Confirm the final decision record",
    ]
    assert package.pre_read_evidence_ids == [public_evidence.evidence_id]
    assert package.proposed_start == slot.start
    assert package.proposed_end == slot.end
    assert package.calendar_written is False
    assert "BEGIN:VCALENDAR" in ics
    assert "DTSTART:20260814T200000Z" in ics
    assert "DTEND:20260814T203000Z" in ics
    assert "SUMMARY:Resolve the customer\\, launch\\; review\\nplan" in ics
    assert "employee medical leave details" not in ics
    assert "private@example.test" not in ics


def test_meeting_messages_do_not_disclose_private_evidence_or_destinations(
    coordinator: MeetingCoordinator,
) -> None:
    package = coordinator.build_package(
        sample_plan(),
        sample_report(),
        assignments(),
        decision_owner_id="coo",
        evidence=[],
        proposed_slot=None,
        created_at=datetime(2026, 8, 11, 12, 0, tzinfo=UTC),
    )

    availability = render_availability_request("HW-AB12", "Review private@example.test")
    confirmation = render_meeting_confirmation("HW-AB12", package)
    reminder = render_meeting_reminder("HW-AB12", package)

    assert "private@example.test" not in availability
    assert "PROPOSED MEETING" in confirmation
    assert "private@example.test" not in confirmation
    assert "private@example.test" not in reminder


def test_meeting_confirmation_stays_proposed_until_every_required_attendee_acknowledges(
    coordinator: MeetingCoordinator,
) -> None:
    package = coordinator.build_package(
        sample_plan(),
        sample_report(),
        assignments(),
        decision_owner_id="coo",
        evidence=[],
        proposed_slot=None,
        created_at=datetime(2026, 8, 11, 12, 0, tzinfo=UTC),
    )

    partial = render_meeting_confirmation(
        "HW-AB12", package, acknowledged_attendee_ids={"coo", "manager"}
    )
    complete = render_meeting_confirmation(
        "HW-AB12", package, acknowledged_attendee_ids={"coo", "manager", "vp-people"}
    )

    assert "PROPOSED MEETING" in partial
    assert "MEETING CONFIRMED" in complete
