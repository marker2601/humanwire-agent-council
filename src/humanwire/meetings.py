"""Deterministic, privacy-safe preparation of meeting packages."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from humanwire.alignment import AlignmentReport
from humanwire.domain import (
    AlignmentIssue,
    AlignmentIssueType,
    AvailabilityWindow,
    EvidenceItem,
    EvidenceStatus,
    MandatePlan,
    MeetingPackage,
    StakeholderAssignment,
)
from humanwire.evidence import ShareableEvidence
from humanwire.redaction import redact_sensitive

_SLOT_DURATION = timedelta(minutes=30)
_AGENDA = [
    "1. Confirm agreed facts",
    "2. Resolve each open decision in severity order",
    "3. Assign owner and deadline for each commitment",
    "4. Confirm the final decision record",
]
_ISSUE_SEVERITY = {
    AlignmentIssueType.HARD_CONSTRAINT: 0,
    AlignmentIssueType.AUTHORITY_GAP: 1,
    AlignmentIssueType.RESOURCE_CONFLICT: 2,
    AlignmentIssueType.DEADLINE_CONFLICT: 3,
    AlignmentIssueType.CONTRADICTION: 4,
    AlignmentIssueType.MISSING_EVIDENCE: 5,
    AlignmentIssueType.PRIVATE_BLOCKER: 6,
    AlignmentIssueType.AGREEMENT: 7,
}


@dataclass(frozen=True)
class VerifiedOverlap:
    """A coordinator-issued result; validity is retained privately by its coordinator."""

    start: datetime
    end: datetime
    required_attendee_ids: tuple[str, ...]


class MeetingCoordinator:
    """Keeps confirmed availability and constructs local calendar-ready metadata."""

    def __init__(self, initiator_id: str) -> None:
        self._initiator_id = initiator_id
        self._availability: dict[str, tuple[AvailabilityWindow, ...]] = {}
        self._required_attendee_ids: set[str] = set()
        self._issued_overlaps: dict[int, VerifiedOverlap] = {}
        self._issued_packages: dict[int, tuple[MeetingPackage, str]] = {}
        self.availability_retry: str | None = None

    def required_attendees(
        self,
        report: AlignmentReport,
        assignments: Iterable[StakeholderAssignment],
        decision_owner_id: str,
    ) -> set[str]:
        """Return the smallest accountable set; optional assignments are never selected."""
        required_ids = {
            assignment.person_id
            for assignment in assignments
            if assignment.required
        }
        attendees = {self._initiator_id, decision_owner_id}
        for issue in self._unresolved_blocking_issues(report):
            owners = sorted(set(issue.stakeholder_ids).intersection(required_ids))
            if owners:
                attendees.add(owners[0])
        self._required_attendee_ids = attendees
        return attendees

    def record_availability(
        self, person_id: str, windows: Iterable[AvailabilityWindow]
    ) -> tuple[AvailabilityWindow, ...]:
        """Store only timezone-normalized, validated availability supplied by one attendee."""
        normalized = tuple(
            sorted((self._utc_window(window) for window in windows), key=lambda window: window.start)
        )
        if not normalized:
            raise ValueError("at least one availability window is required")
        self._availability[person_id] = normalized
        return normalized

    def find_overlap(
        self,
        availability: Mapping[str, Iterable[AvailabilityWindow]] | None = None,
        *,
        required_attendee_ids: Iterable[str] | None = None,
        duration: timedelta = _SLOT_DURATION,
    ) -> VerifiedOverlap | None:
        """Find the earliest fully-confirmed UTC slot on a 30-minute boundary."""
        if duration <= timedelta(0) or duration % _SLOT_DURATION:
            raise ValueError("meeting duration must be a positive 30-minute increment")

        source = self._availability if availability is None else availability
        attendee_ids = sorted(
            required_attendee_ids if required_attendee_ids is not None else self._required_attendee_ids
        )
        if not attendee_ids:
            self.availability_retry = "Required attendee set is not established."
            return None
        normalized = {
            attendee_id: tuple(
                sorted(
                    (self._utc_window(window) for window in source.get(attendee_id, ())),
                    key=lambda window: window.start,
                )
            )
            for attendee_id in attendee_ids
        }
        missing = [attendee_id for attendee_id, windows in normalized.items() if not windows]
        if missing:
            self.availability_retry = (
                f"Awaiting confirmed availability from: {', '.join(missing)}"
            )
            return None

        earliest = min(window.start for windows in normalized.values() for window in windows)
        latest = max(window.end for windows in normalized.values() for window in windows)
        candidate = self._round_up_to_slot(earliest)
        while candidate + duration <= latest:
            candidate_end = candidate + duration
            if all(
                any(window.start <= candidate and candidate_end <= window.end for window in windows)
                for windows in normalized.values()
            ):
                self.availability_retry = None
                result = VerifiedOverlap(
                    start=candidate,
                    end=candidate_end,
                    required_attendee_ids=tuple(attendee_ids),
                )
                self._issued_overlaps[id(result)] = result
                return result
            candidate += _SLOT_DURATION

        self.availability_retry = "No shared availability; request another availability window."
        return None

    def build_package(
        self,
        plan: MandatePlan,
        report: AlignmentReport,
        assignments: Iterable[StakeholderAssignment],
        decision_owner_id: str,
        evidence: Iterable[ShareableEvidence | EvidenceItem],
        *,
        proposed_slot: VerifiedOverlap | None,
        created_at: datetime,
    ) -> MeetingPackage:
        """Build a local-only package from public evidence projections and confirmed slots."""
        attendees = sorted(self.required_attendees(report, assignments, decision_owner_id))
        self._verify_slot(proposed_slot, attendees)
        shared_evidence = self._confirmed_shareable_evidence(evidence)
        issues = self._unresolved_blocking_issues(report)
        open_decisions = self._open_decisions(issues)
        package = MeetingPackage(
            meeting_id=uuid5(NAMESPACE_URL, f"humanwire:meeting:{report.mandate_id}"),
            mandate_id=report.mandate_id,
            purpose=redact_sensitive(plan.objective),
            decision_owner_id=decision_owner_id,
            required_attendee_ids=attendees,
            proposed_start=proposed_slot.start if proposed_slot is not None else None,
            proposed_end=proposed_slot.end if proposed_slot is not None else None,
            timezone="UTC",
            agreed_facts=sorted({redact_sensitive(fact) for fact in report.agreements}),
            open_decisions=open_decisions,
            agenda=list(_AGENDA),
            pre_read_evidence_ids=sorted((item.evidence_id for item in shared_evidence), key=str),
            calendar_written=False,
            created_at=created_at.astimezone(UTC),
        )
        if proposed_slot is not None:
            self._issued_packages[id(package)] = (package, package.model_dump_json())
        return package

    @staticmethod
    def _utc_window(window: AvailabilityWindow) -> AvailabilityWindow:
        return AvailabilityWindow(start=window.start.astimezone(UTC), end=window.end.astimezone(UTC))

    @staticmethod
    def _round_up_to_slot(value: datetime) -> datetime:
        value = value.astimezone(UTC).replace(second=0, microsecond=0)
        remainder = value.minute % 30
        if remainder:
            value += timedelta(minutes=30 - remainder)
        return value

    def _verify_slot(self, slot: VerifiedOverlap | None, attendees: list[str]) -> None:
        if slot is None:
            return
        if self._issued_overlaps.get(id(slot)) is not slot:
            raise ValueError("proposed slot must be a verified overlap")
        if slot.required_attendee_ids != tuple(attendees):
            raise ValueError("verified overlap does not match the required attendee set")
        duration = slot.end - slot.start
        current = self._current_overlap(attendees, duration)
        if current is None or current.start != slot.start or current.end != slot.end:
            raise ValueError("verified overlap no longer matches current confirmed availability")

    def has_current_verified_package(self, package: MeetingPackage) -> bool:
        """Return whether this exact package instance still matches current availability."""
        issued = self._issued_packages.get(id(package))
        if issued is None or issued[0] is not package or issued[1] != package.model_dump_json():
            return False
        if package.proposed_start is None or package.proposed_end is None:
            return False
        attendees = tuple(sorted(self._required_attendee_ids))
        if tuple(package.required_attendee_ids) != attendees:
            return False
        current = self._current_overlap(list(attendees), package.proposed_end - package.proposed_start)
        return (
            current is not None
            and current.start == package.proposed_start
            and current.end == package.proposed_end
        )

    def _current_overlap(
        self, attendee_ids: list[str], duration: timedelta
    ) -> AvailabilityWindow | None:
        if duration <= timedelta(0) or duration % _SLOT_DURATION:
            return None
        normalized = {
            attendee_id: tuple(
                sorted(
                    (self._utc_window(window) for window in self._availability.get(attendee_id, ())),
                    key=lambda window: window.start,
                )
            )
            for attendee_id in attendee_ids
        }
        if not normalized or any(not windows for windows in normalized.values()):
            return None
        earliest = min(window.start for windows in normalized.values() for window in windows)
        latest = max(window.end for windows in normalized.values() for window in windows)
        candidate = self._round_up_to_slot(earliest)
        while candidate + duration <= latest:
            candidate_end = candidate + duration
            if all(
                any(window.start <= candidate and candidate_end <= window.end for window in windows)
                for windows in normalized.values()
            ):
                return AvailabilityWindow(start=candidate, end=candidate_end)
            candidate += _SLOT_DURATION
        return None

    @staticmethod
    def _unresolved_blocking_issues(report: AlignmentReport) -> list[AlignmentIssue]:
        return sorted(
            (
                issue
                for issue in report.issues
                if issue.blocking and issue.resolution is None
            ),
            key=lambda issue: (_ISSUE_SEVERITY[issue.issue_type], issue.related_decision or issue.summary),
        )

    @staticmethod
    def _confirmed_shareable_evidence(
        evidence: Iterable[ShareableEvidence | EvidenceItem],
    ) -> list[ShareableEvidence]:
        return sorted(
            (
                item
                for item in evidence
                if isinstance(item, ShareableEvidence) and item.status is EvidenceStatus.CONFIRMED
            ),
            key=lambda item: str(item.evidence_id),
        )

    @staticmethod
    def _open_decisions(issues: Iterable[AlignmentIssue]) -> list[str]:
        decisions: list[str] = []
        for issue in issues:
            decision = redact_sensitive(issue.related_decision or issue.summary)
            if decision not in decisions:
                decisions.append(decision)
        return decisions


def render_ics(
    package: MeetingPackage,
    coordinator: MeetingCoordinator,
    *,
    public_uid: str | None = None,
    public_summary: str | None = None,
) -> bytes:
    """Create a local RFC 5545-style calendar download; it never writes to a calendar."""
    if not coordinator.has_current_verified_package(package):
        raise ValueError("a verified proposed slot is required to render calendar content")
    start = package.proposed_start.astimezone(UTC)
    end = package.proposed_end.astimezone(UTC)
    created = package.created_at.astimezone(UTC)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//HumanWire//Meeting Package//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{_ics_escape(public_uid or f'{package.meeting_id}@humanwire.local')}",
        f"DTSTAMP:{_ics_time(created)}",
        f"DTSTART:{_ics_time(start)}",
        f"DTEND:{_ics_time(end)}",
        f"SUMMARY:{_ics_escape(public_summary or redact_sensitive(package.purpose))}",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ]
    return "\r\n".join(lines).encode("utf-8")


def _ics_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _ics_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
