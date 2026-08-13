"""Read-only, privacy-safe HTTP projections of persisted HumanWire state."""

from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import html
import io
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from humanwire.alignment import AlignmentReport
from humanwire.config import Settings
from humanwire.domain import (
    AvailabilityWindow,
    Channel,
    Direction,
    EngagementDecisionKind,
    EngagementType,
    EvidenceStatus,
    EvidenceType,
    EvidenceVisibility,
    Mandate,
    MandateState,
    PlannedStakeholder,
    StakeholderAssignment,
    StakeholderState,
)
from humanwire.evidence import private_blocker_count, shareable_evidence
from humanwire.meetings import MeetingCoordinator, render_ics
from humanwire.redaction import redact_sensitive
from humanwire.repository import SqlAlchemyHumanWireRepository

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_PRIVATE_PROJECTION_KEYS = frozenset(
    {
        "connection_id",
        "conversation_id",
        "current_conversation_id",
        "current_route_id",
        "idempotency_key",
        "origin_conversation_id",
        "origin_message_id",
        "provider_body",
        "recipient",
        "route_id",
        "route_ids",
        "sender_address",
        "sender_id",
        "source_message_id",
        "assignment_id",
        "mandate_id",
    }
)
_IDENTIFIER_FIELDS = frozenset(
    {"actor_id", "evidence_id", "person_id", "stakeholder_id", "token"}
)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_FILENAME_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_DEPARTMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &'(),./_-]{0,127}$")
_SAFE_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{0,99}$")
_SAFE_PUBLIC_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ENUM_FIELDS = {
    "channel": {item.value for item in Channel},
    "direction": {item.value for item in Direction},
    "engagement_type": {item.value for item in EngagementType},
    "engagement_status": {
        "acknowledged",
        "approved",
        "awaiting acknowledgement",
        "awaiting confirmation",
        "awaiting response",
        "change requested",
        "complete",
        "declined",
        "delivered",
        "delivery failed",
        "in progress",
        "missing",
        "pending",
        "recorded",
        "rejected",
        "unreachable",
    },
    "evidence_type": {item.value for item in EvidenceType},
    "interview_status": {"complete", "in_progress", "not_started"},
    "state": {item.value for item in MandateState} | {item.value for item in StakeholderState},
    "status": {item.value for item in EvidenceStatus} | {"complete", "current", "pending"},
    "previous_state": {item.value for item in MandateState}
    | {item.value for item in StakeholderState},
    "new_state": {item.value for item in MandateState} | {item.value for item in StakeholderState},
    "phase_label": {item.value for item in MandateState} | {"coordinating"},
}
_PRIVATE_MARKER = "[PRIVATE]"
_CALENDAR_UID_NAMESPACE = b"humanwire:calendar:uid:v1:"
_PACKAGE_DIR = Path(__file__).resolve().parent
_ENGAGEMENT_LABELS = {
    "inform": "Inform only",
    "acknowledge": "Acknowledgement",
    "quick_response": "Quick response",
    "structured_interview": "Structured interview",
    "review_approval": "Approval review",
    "availability": "Availability",
}
_STATUS_LABELS = {
    "acknowledged": "Acknowledged",
    "approved": "Approved",
    "awaiting acknowledgement": "Awaiting acknowledgement",
    "awaiting confirmation": "Awaiting confirmation",
    "awaiting response": "Awaiting response",
    "change requested": "Change requested",
    "complete": "Complete",
    "declined": "Declined",
    "delivered": "Delivered",
    "delivery failed": "Delivery failed",
    "in progress": "In progress",
    "missing": "Missing",
    "pending": "Pending",
    "recorded": "Recorded",
    "rejected": "Rejected",
    "unreachable": "Unreachable",
}
_DASHBOARD_STATE_GROUPS = {
    "active": {
        "received",
        "planned",
        "interviewing",
        "synthesizing",
        "negotiating",
        "meeting_required",
        "scheduling",
    },
    "aligned": {"aligned"},
    "meeting-ready": {"meeting_ready"},
    "partial": {"partial"},
    "failed": {"expired", "cancelled", "delivery_failed"},
}
_WORKFLOW_STEPS = (
    ("received", "Received"),
    ("planned", "Planned"),
    ("coordinating", "Coordinating"),
    ("synthesizing", "Synthesizing"),
    ("negotiating", "Negotiating"),
    ("meeting-ready", "Meeting Ready"),
)
_ACTIONABLE_ASSIGNMENT_STATES = frozenset(
    {
        StakeholderState.CONTACT_QUEUED,
        StakeholderState.DELIVERED,
        StakeholderState.AWAITING_ACKNOWLEDGEMENT,
        StakeholderState.ACKNOWLEDGED,
        StakeholderState.INTERVIEWING,
        StakeholderState.FOLLOW_UP_DUE,
        StakeholderState.ALTERNATE_CHANNEL,
    }
)
OUTREACH_HEADERS = [
    "mandate_token",
    "timestamp",
    "initiator_id",
    "source_department",
    "target_person_id",
    "target_department",
    "direction",
    "channel",
    "engagement_type",
    "response_required",
    "engagement_status",
    "event_type",
    "previous_state",
    "new_state",
    "outcome",
    "response_latency_seconds",
]
_OUTREACH_FILTER_KEYS = (
    "engagement_type",
    "engagement_status",
    "department",
    "person_id",
    "channel",
    "direction",
    "event_type",
    "timestamp_from",
    "timestamp_to",
)
_OUTREACH_OUTCOMES = {
    "mandate.created": "mandate created",
    "engagement.plan_previewed": "plan previewed",
    "engagement.plan_released": "plan released",
    "mandate.interviewing": "coordination started",
    "engagement.quick_response_sent": "outreach sent",
    "engagement.structured_interview_sent": "outreach sent",
    "engagement.acknowledgement_sent": "outreach sent",
    "engagement.approval_pending": "decision pending",
    "engagement.inform_delivered": "delivered",
    "engagement.quick_response_completed": "response complete",
    "engagement.acknowledged": "acknowledged",
    "engagement.structured_interview_reminder": "reminder sent",
    "engagement.structured_interview_alternate_selected": "alternate selected",
    "engagement.structured_interview_progressed": "response in progress",
}


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _redact(value: str | None) -> str | None:
    return redact_sensitive(value) if value is not None else None


def _scrub_known_private(value: str, denied_values: frozenset[str]) -> str:
    if value in denied_values:
        return _PRIVATE_MARKER
    for private_value in sorted(denied_values, key=len, reverse=True):
        distinctive_embedded_value = (
            len(private_value) >= 24
            or (len(private_value) >= 16 and any(character.isspace() for character in private_value))
            or (
                len(private_value) >= 6
                and any(character.isdigit() for character in private_value)
                and not private_value.isalnum()
            )
        )
        if distinctive_embedded_value:
            start_boundary = r"(?<![A-Za-z0-9])" if private_value[0].isalnum() else ""
            end_boundary = r"(?![A-Za-z0-9])" if private_value[-1].isalnum() else ""
            value = re.sub(
                start_boundary + re.escape(private_value) + end_boundary,
                lambda _: _PRIVATE_MARKER,
                value,
            )
    return value


def _private_deny_values(repository: Any, mandate: Mandate) -> frozenset[str]:
    denied: set[str] = set()
    for item in repository.list_evidence(mandate.mandate_id):
        if item.visibility is not EvidenceVisibility.PRIVATE:
            continue
        denied.update(
            value
            for value in (
                item.statement,
                item.related_decision,
                item.resource,
                item.source_message_id,
            )
            if value
        )
    denied.update(
        decision.change_text
        for decision in repository.list_engagement_decisions(mandate.mandate_id)
        if decision.change_text
    )
    return frozenset(denied)


def _public_calendar_uid(meeting_id: str, denied_values: frozenset[str]) -> str:
    public_uid = f"{meeting_id}@humanwire.local"
    if _scrub_known_private(public_uid, denied_values) == public_uid:
        return public_uid
    digest = hashlib.sha256(_CALENDAR_UID_NAMESPACE + meeting_id.encode("ascii")).digest()
    token = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"{token}@humanwire.local"


def _public_projection(
    value: Any,
    field: str | None = None,
    denied_values: frozenset[str] = frozenset(),
) -> Any:
    """Apply one recursive privacy boundary immediately before public serialization."""
    if isinstance(value, Mapping):
        return {
            str(key): _public_projection(item, str(key), denied_values)
            for key, item in value.items()
            if str(key) not in _PRIVATE_PROJECTION_KEYS
            and _SAFE_PUBLIC_KEY.fullmatch(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_public_projection(item, field, denied_values) for item in value]
    if not isinstance(value, str):
        return value
    value = _scrub_known_private(value, denied_values)
    if value == "":
        return ""
    if value == _PRIVATE_MARKER:
        return value
    if field in _IDENTIFIER_FIELDS or (field is not None and field.endswith("_ids")):
        return value if _SAFE_IDENTIFIER.fullmatch(value) else "[REDACTED]"
    if field == "event_type":
        return value if _SAFE_EVENT_TYPE.fullmatch(value) else "[REDACTED]"
    allowed = _ENUM_FIELDS.get(field)
    if allowed is not None:
        return value if value in allowed else "[REDACTED]"
    return redact_sensitive(value)


def _name(person_id: str) -> str:
    return " ".join(part.capitalize() for part in person_id.replace("_", "-").split("-"))


def _person(repository: Any, person_id: str) -> dict[str, Any]:
    projection: dict[str, Any] = {"person_id": person_id, "name": _name(person_id)}
    stored = repository.get_runtime_status(f"public.person:{person_id}")
    if stored is not None:
        try:
            public = json.loads(stored[0])
        except (TypeError, ValueError):
            public = {}
        if isinstance(public, dict):
            if isinstance(public.get("name"), str):
                projection["name"] = redact_sensitive(public["name"])
            if isinstance(public.get("role"), str):
                projection["role"] = redact_sensitive(public["role"])
    return projection


def _assignment_projection(
    repository: Any,
    assignment: StakeholderAssignment,
    interviews: dict[Any, Any],
    planned: PlannedStakeholder | None,
    decisions: dict[Any, Any],
    event_channel: Channel | None = None,
    evidence: tuple[Any, ...] | list[Any] = (),
    events: tuple[Any, ...] | list[Any] = (),
) -> dict[str, Any]:
    interview = interviews.get(assignment.interview_id)
    if not (
        assignment.engagement_type
        in {
            EngagementType.QUICK_RESPONSE,
            EngagementType.STRUCTURED_INTERVIEW,
        }
        and interview is not None
        and interview.mandate_id == assignment.mandate_id
        and interview.assignment_id == assignment.assignment_id
    ):
        interview = None
    decision = decisions.get(assignment.assignment_id)
    if not (
        assignment.engagement_type is EngagementType.REVIEW_APPROVAL
        and decision is not None
        and decision.mandate_id == assignment.mandate_id
        and decision.assignment_id == assignment.assignment_id
        and decision.stakeholder_id == assignment.person_id
    ):
        decision = None
    if interview is None:
        interview_status = "not_started"
        question = None
    elif interview.completed_at is not None:
        interview_status = "complete"
        question = None
    else:
        interview_status = "in_progress"
        question = interview.current_question_index + 1
    engagement_status, progress_current, progress_total = _engagement_progress(
        repository,
        assignment,
        interview,
        planned,
        decision,
    )
    answer_prefix = f"interview:{assignment.assignment_id}:answer:"
    answer_sources = {
        event.idempotency_key.removeprefix(answer_prefix)
        for event in events
        if event.event_type == "interview.answer_recorded"
        and event.assignment_id == assignment.assignment_id
        and event.person_id == assignment.person_id
        and event.idempotency_key.startswith(answer_prefix)
    }
    confirmation_proven = any(
        event.event_type == "interview.evidence_confirmed"
        and event.assignment_id == assignment.assignment_id
        and event.person_id == assignment.person_id
        and interview is not None
        and event.channel is interview.current_channel
        and event.metadata.get("evidence_count", 0) > 0
        for event in events
    )
    answer_evidence = [
        item
        for item in evidence
        if item.mandate_id == assignment.mandate_id
        and item.assignment_id == assignment.assignment_id
        and item.stakeholder_id == assignment.person_id
        and item.source_message_id in answer_sources
    ]
    evidence_confirmed = confirmation_proven and any(
        item.status is EvidenceStatus.CONFIRMED for item in answer_evidence
    ) and not any(item.status is EvidenceStatus.ASSERTED for item in answer_evidence)
    if (
        assignment.engagement_type
        in {EngagementType.QUICK_RESPONSE, EngagementType.STRUCTURED_INTERVIEW}
        and assignment.state is StakeholderState.COMPLETE
        and not evidence_confirmed
    ):
        engagement_status = "awaiting confirmation"
    return {
        **_person(repository, assignment.person_id),
        "department": redact_sensitive(assignment.department),
        "direction": assignment.direction.value,
        "reason": redact_sensitive(assignment.reason),
        "required": assignment.required,
        "engagement_type": assignment.engagement_type.value,
        "response_required": assignment.response_required,
        "engagement_status": engagement_status,
        "evidence_confirmed": evidence_confirmed,
        "progress_current": progress_current,
        "progress_total": progress_total,
        "state": assignment.state.value,
        "attempt_count": assignment.attempt_count,
        "channel": (
            interview.current_channel.value
            if interview and interview.current_channel
            else event_channel.value if event_channel else None
        ),
        "channel_is_alternate": assignment.active_route_index > 0,
        "interview_status": interview_status,
        "current_question": question,
        "first_contact_at": _iso(assignment.first_contact_at),
        "last_delivery_at": _iso(assignment.last_delivery_at),
        "next_action_at": _iso(assignment.next_action_at),
        "acknowledged_at": _iso(assignment.acknowledged_at),
        "completed_at": _iso(assignment.completed_at),
    }


def _engagement_progress(
    repository: Any,
    assignment: StakeholderAssignment,
    interview: Any,
    planned: PlannedStakeholder | None,
    decision: Any,
) -> tuple[str, int, int]:
    engagement_type = assignment.engagement_type
    question_engagement = engagement_type in {
        EngagementType.QUICK_RESPONSE,
        EngagementType.STRUCTURED_INTERVIEW,
    }
    question_total = (
        len(interview.questions)
        if interview is not None
        else len(planned.questions if planned else [])
    )
    question_current = (
        min(max(interview.current_question_index, 0), question_total)
        if interview is not None
        else 0
    )
    terminal_labels = {
        StakeholderState.DELIVERY_FAILED: "delivery failed",
        StakeholderState.UNREACHABLE: "unreachable",
        StakeholderState.DECLINED: "declined",
    }
    if assignment.state in terminal_labels:
        return (
            terminal_labels[assignment.state],
            question_current if question_engagement else 0,
            question_total if question_engagement else 1,
        )

    if engagement_type is EngagementType.INFORM:
        delivery_confirmed = (
            assignment.state is StakeholderState.COMPLETE
            and assignment.completed_at is not None
            and assignment.last_delivery_at is not None
        )
        return ("delivered" if delivery_confirmed else "pending"), int(delivery_confirmed), 1
    if engagement_type is EngagementType.ACKNOWLEDGE:
        complete = assignment.state is StakeholderState.COMPLETE
        return ("acknowledged" if complete else "awaiting acknowledgement"), int(complete), 1
    if engagement_type in {
        EngagementType.QUICK_RESPONSE,
        EngagementType.STRUCTURED_INTERVIEW,
    }:
        if assignment.state is StakeholderState.COMPLETE:
            return "complete", question_total, question_total
        status = (
            "in progress"
            if interview is not None and question_current > 0
            else "awaiting response"
        )
        return status, question_current, question_total
    if engagement_type is EngagementType.REVIEW_APPROVAL:
        labels = {
            EngagementDecisionKind.APPROVE: "approved",
            EngagementDecisionKind.REJECT: "rejected",
            EngagementDecisionKind.CHANGE: "change requested",
        }
        return (labels[decision.response], 1, 1) if decision is not None else ("pending", 0, 1)

    recorded = _has_exact_availability(repository, assignment)
    return ("recorded" if recorded else "missing"), int(recorded), 1


def _has_exact_availability(repository: Any, assignment: StakeholderAssignment) -> bool:
    stored = repository.get_runtime_status(
        f"availability:{assignment.mandate_id}:{assignment.person_id}"
    )
    if (
        stored is None
        or assignment.state is not StakeholderState.COMPLETE
        or assignment.completed_at is None
        or stored[1] != assignment.completed_at
    ):
        return False
    raw_windows = stored[0].split("|")
    if not raw_windows or any(not raw for raw in raw_windows):
        return False
    try:
        windows = [
            AvailabilityWindow(
                start=datetime.fromisoformat(raw.split("/", 1)[0]),
                end=datetime.fromisoformat(raw.split("/", 1)[1]),
            )
            for raw in raw_windows
        ]
    except (IndexError, ValueError):
        return False
    return bool(windows)


def _next_action(assignments: list[StakeholderAssignment]) -> dict[str, Any] | None:
    scheduled = [
        assignment
        for assignment in assignments
        if assignment.next_action_at is not None
        and assignment.state in _ACTIONABLE_ASSIGNMENT_STATES
    ]
    if not scheduled:
        return None
    assignment = min(scheduled, key=lambda item: (item.next_action_at, str(item.assignment_id)))
    event_type = (
        "outreach.alternate_send"
        if assignment.state.value == "alternate_channel"
        else "outreach.follow_up"
    )
    return {
        "event_type": event_type,
        "person_id": assignment.person_id,
        "scheduled_at": _iso(assignment.next_action_at),
    }


def _mandate_summary(repository: Any, mandate: Mandate) -> dict[str, Any]:
    assignments = repository.list_assignments(mandate.mandate_id)
    return {
        "token": mandate.token,
        "objective": redact_sensitive(mandate.objective),
        "state": mandate.state.value,
        "phase_label": (
            "coordinating"
            if mandate.state is MandateState.INTERVIEWING
            else mandate.state.value
        ),
        "initiator": _person(repository, mandate.initiator_id),
        "created_at": _iso(mandate.created_at),
        "updated_at": _iso(mandate.updated_at),
        "completed_at": _iso(mandate.completed_at),
        "stakeholder_count": len(assignments),
        "complete_count": sum(item.state.value == "complete" for item in assignments),
    }


def _mandate_detail(repository: Any, mandate: Mandate) -> dict[str, Any]:
    assignments = repository.list_assignments(mandate.mandate_id)
    issues = repository.list_issues(mandate.mandate_id)
    package = repository.get_meeting_package(mandate.mandate_id)
    detail = {
        **_mandate_summary(repository, mandate),
        "required_decisions": [redact_sensitive(item) for item in mandate.plan.required_decisions],
        "completion_conditions": [
            redact_sensitive(item) for item in mandate.plan.completion_conditions
        ],
        "deadline": _iso(mandate.plan.deadline),
        "next_action": _next_action(assignments),
        "open_issue_count": sum(issue.blocking and issue.resolution is None for issue in issues),
    }
    if package is not None:
        detail["meeting"] = _meeting_projection(repository, package)
    return detail


def _stakeholders(
    repository: Any,
    mandate: Mandate,
    *,
    include_reach_identity: bool = False,
) -> list[dict[str, Any]]:
    interviews = {
        interview.session_id: interview
        for interview in repository.list_interviews(mandate.mandate_id)
    }
    assignments = repository.list_assignments(mandate.mandate_id)
    planned = {item.person_ref: item for item in mandate.plan.stakeholders}
    decisions = {
        item.assignment_id: item
        for item in repository.list_engagement_decisions(mandate.mandate_id)
    }
    evidence = repository.list_evidence(mandate.mandate_id)
    events = repository.list_events(mandate.mandate_id)
    person_counts = Counter(item.person_id for item in assignments)
    assignment_counts = Counter(str(item.assignment_id) for item in assignments)
    exact_assignments = {
        (str(item.mandate_id), str(item.assignment_id), item.person_id): item
        for item in assignments
        if item.mandate_id == mandate.mandate_id
        and person_counts[item.person_id] == 1
        and assignment_counts[str(item.assignment_id)] == 1
    }
    event_channels: dict[Any, Channel] = {}
    for event in events:
        identity = (
            str(mandate.mandate_id),
            str(event.assignment_id) if event.assignment_id is not None else "",
            event.person_id or "",
        )
        assignment = exact_assignments.get(identity)
        if assignment is not None and event.channel is not None:
            event_channels[assignment.assignment_id] = event.channel
    plan_order = {
        planned.person_ref: index
        for index, planned in enumerate(mandate.plan.stakeholders)
    }
    rows = []
    for assignment in sorted(
        assignments,
        key=lambda item: (
            plan_order.get(item.person_id, len(plan_order)),
            item.person_id,
            str(item.assignment_id),
        ),
    ):
        row = _assignment_projection(
            repository,
            assignment,
            interviews,
            planned.get(assignment.person_id),
            decisions,
            event_channels.get(assignment.assignment_id),
            evidence,
            events,
        )
        if include_reach_identity:
            row["_reach_mandate_id"] = str(assignment.mandate_id)
            row["_reach_assignment_id"] = str(assignment.assignment_id)
        rows.append(row)
    return rows


def _events(
    repository: Any,
    mandate: Mandate,
    *,
    include_reach_identity: bool = False,
) -> list[dict[str, Any]]:
    rows = []
    for event in repository.list_events(mandate.mandate_id):
        row = {
            "event_type": event.event_type,
            "created_at": _iso(event.created_at),
            "actor": _person(repository, event.actor_id) if event.actor_id else None,
            "person": _person(repository, event.person_id) if event.person_id else None,
            "department": _redact(event.department),
            "direction": event.direction.value if event.direction else None,
            "channel": event.channel.value if event.channel else None,
            "previous_state": event.previous_state,
            "new_state": event.new_state,
            "metadata": event.metadata,
        }
        if include_reach_identity:
            row["_reach_mandate_id"] = str(mandate.mandate_id)
            row["_reach_assignment_id"] = (
                str(event.assignment_id) if event.assignment_id is not None else None
            )
        rows.append(row)
    return rows


def _safe_identifier(value: Any) -> str:
    text = str(value or "")
    return text if text.isascii() and _SAFE_IDENTIFIER.fullmatch(text) else ""


def _safe_department(value: Any) -> str:
    text = str(value or "")
    return text if text.isascii() and _SAFE_DEPARTMENT.fullmatch(text) else ""


def _safe_enum_value(value: Any, allowed: set[str]) -> str:
    text = str(getattr(value, "value", value) or "")
    return text if text in allowed else ""


def _safe_event_type(value: Any) -> str:
    text = str(value or "")
    return text if text.isascii() and _SAFE_EVENT_TYPE.fullmatch(text) else ""


def _analytics_timestamp(value: Any) -> str:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        return ""
    return value.astimezone(UTC).isoformat()


def _parse_analytics_timestamp(value: str) -> datetime | None:
    if not value or not value.isascii():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _validated_outreach_filters(request: Request) -> dict[str, str]:
    supplied: dict[str, str] = {}
    allowed_keys = set(_OUTREACH_FILTER_KEYS)
    for key, value in request.query_params.multi_items():
        if key not in allowed_keys or key in supplied:
            raise HTTPException(status_code=400, detail="Invalid filters")
        supplied[key] = value

    validators: dict[str, Callable[[str], str]] = {
        "engagement_type": lambda value: (
            value if value in _ENUM_FIELDS["engagement_type"] else ""
        ),
        "engagement_status": lambda value: (
            value if value in _ENUM_FIELDS["engagement_status"] else ""
        ),
        "department": _safe_department,
        "person_id": _safe_identifier,
        "channel": lambda value: value if value in _ENUM_FIELDS["channel"] else "",
        "direction": lambda value: value if value in _ENUM_FIELDS["direction"] else "",
        "event_type": _safe_event_type,
    }
    normalized: dict[str, str] = {}
    for key in _OUTREACH_FILTER_KEYS:
        if key not in supplied:
            continue
        value = supplied[key]
        if value == "":
            continue
        if key in {"timestamp_from", "timestamp_to"}:
            parsed = _parse_analytics_timestamp(value)
            normalized_value = _analytics_timestamp(parsed)
        else:
            normalized_value = validators[key](value)
        if not normalized_value:
            raise HTTPException(status_code=400, detail="Invalid filters")
        normalized[key] = normalized_value
    if (
        normalized.get("timestamp_from")
        and normalized.get("timestamp_to")
        and normalized["timestamp_from"] > normalized["timestamp_to"]
    ):
        raise HTTPException(status_code=400, detail="Invalid filters")
    return normalized


def _exact_interview(
    interviews: Mapping[Any, Any], assignment: StakeholderAssignment
) -> Any | None:
    interview = interviews.get(assignment.interview_id)
    if (
        assignment.engagement_type
        not in {EngagementType.QUICK_RESPONSE, EngagementType.STRUCTURED_INTERVIEW}
        or interview is None
        or interview.mandate_id != assignment.mandate_id
        or interview.assignment_id != assignment.assignment_id
    ):
        return None
    return interview


def _exact_decision(
    decisions: Mapping[Any, Any], assignment: StakeholderAssignment
) -> Any | None:
    decision = decisions.get(assignment.assignment_id)
    if (
        assignment.engagement_type is not EngagementType.REVIEW_APPROVAL
        or decision is None
        or decision.mandate_id != assignment.mandate_id
        or decision.assignment_id != assignment.assignment_id
        or decision.stakeholder_id != assignment.person_id
    ):
        return None
    return decision


def _response_latency(
    repository: Any,
    assignment: StakeholderAssignment,
    engagement_status: str,
    interviews: Mapping[Any, Any],
    decisions: Mapping[Any, Any],
) -> int | str:
    if assignment.engagement_type is EngagementType.INFORM:
        return ""
    response_at: datetime | None = None
    if assignment.engagement_type is EngagementType.ACKNOWLEDGE:
        if engagement_status == "acknowledged":
            response_at = assignment.acknowledged_at
    elif assignment.engagement_type in {
        EngagementType.QUICK_RESPONSE,
        EngagementType.STRUCTURED_INTERVIEW,
    }:
        interview = _exact_interview(interviews, assignment)
        response_at = interview.acknowledged_at if interview is not None else None
    elif assignment.engagement_type is EngagementType.REVIEW_APPROVAL:
        decision = _exact_decision(decisions, assignment)
        response_at = decision.created_at if decision is not None else None
    elif (
        assignment.engagement_type is EngagementType.AVAILABILITY
        and _has_exact_availability(repository, assignment)
    ):
        response_at = assignment.completed_at
    started_at = assignment.first_contact_at
    if (
        started_at is None
        or response_at is None
        or started_at.utcoffset() is None
        or response_at.utcoffset() is None
        or response_at < started_at
    ):
        return ""
    return int((response_at - started_at).total_seconds())


def _outreach_outcome(
    event_type: str,
    event: Any,
    assignment: StakeholderAssignment | None,
    engagement_status: str,
) -> str:
    if not event_type:
        return ""
    if assignment is None:
        if (
            getattr(event, "assignment_id", None) is None
            and getattr(event, "person_id", None) is None
            and event_type in _MANDATE_LEVEL_EVENT_TYPES
        ):
            return _OUTREACH_OUTCOMES.get(event_type, "")
        return ""
    outcome = _OUTREACH_OUTCOMES.get(event_type, "")
    if event_type == "engagement.inform_delivered" and engagement_status != "delivered":
        return ""
    if (
        event_type == "engagement.quick_response_completed"
        and engagement_status != "complete"
    ):
        return ""
    if event_type == "engagement.acknowledged" and engagement_status != "acknowledged":
        return ""
    return outcome


def _outreach_rows(
    repository: Any,
    mandate: Mandate,
    filters: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Project one canonical, read-only analytics row per persisted event."""
    assignments = repository.list_assignments(mandate.mandate_id)
    person_counts = Counter(item.person_id for item in assignments)
    assignment_counts = Counter(str(item.assignment_id) for item in assignments)
    identity_counts = Counter(
        (str(item.mandate_id), str(item.assignment_id), item.person_id)
        for item in assignments
    )
    exact_assignments = {
        identity: assignment
        for assignment in assignments
        if (
            identity := (
                str(assignment.mandate_id),
                str(assignment.assignment_id),
                assignment.person_id,
            )
        )[0]
        == str(mandate.mandate_id)
        and person_counts[identity[2]] == 1
        and assignment_counts[identity[1]] == 1
        and identity_counts[identity] == 1
    }
    initiator_matches = [
        assignment
        for identity, assignment in exact_assignments.items()
        if identity[2] == mandate.initiator_id
    ]
    source_department = (
        _safe_department(initiator_matches[0].department)
        if len(initiator_matches) == 1
        else ""
    )
    interview_values = repository.list_interviews(mandate.mandate_id)
    interview_counts = Counter(str(item.session_id) for item in interview_values)
    interviews = {
        item.session_id: item
        for item in interview_values
        if interview_counts[str(item.session_id)] == 1
    }
    decision_values = repository.list_engagement_decisions(mandate.mandate_id)
    decision_counts = Counter(str(item.assignment_id) for item in decision_values)
    decisions = {
        item.assignment_id: item
        for item in decision_values
        if decision_counts[str(item.assignment_id)] == 1
    }
    planned = {item.person_ref: item for item in mandate.plan.stakeholders}
    evidence = repository.list_evidence(mandate.mandate_id)
    events = repository.list_events(mandate.mandate_id)
    state_values = _ENUM_FIELDS["previous_state"]
    channel_values = _ENUM_FIELDS["channel"]
    rows: list[dict[str, Any]] = []
    for event in events:
        identity = (
            str(mandate.mandate_id),
            str(event.assignment_id) if event.assignment_id is not None else "",
            event.person_id or "",
        )
        assignment = exact_assignments.get(identity)
        engagement_status = ""
        if assignment is not None:
            projection = _assignment_projection(
                repository,
                assignment,
                interviews,
                planned.get(assignment.person_id),
                decisions,
                evidence=evidence,
                events=events,
            )
            engagement_status = _safe_enum_value(
                projection.get("engagement_status"),
                _ENUM_FIELDS["engagement_status"],
            )
        event_type = _safe_event_type(event.event_type)
        row = {
            "mandate_token": _safe_identifier(mandate.token),
            "timestamp": _analytics_timestamp(event.created_at),
            "initiator_id": _safe_identifier(mandate.initiator_id),
            "source_department": source_department,
            "target_person_id": (
                _safe_identifier(assignment.person_id) if assignment is not None else ""
            ),
            "target_department": (
                _safe_department(assignment.department) if assignment is not None else ""
            ),
            "direction": (
                _safe_enum_value(assignment.direction, _ENUM_FIELDS["direction"])
                if assignment is not None
                else ""
            ),
            "channel": (
                _safe_enum_value(event.channel, channel_values)
                if assignment is not None
                else ""
            ),
            "engagement_type": (
                _safe_enum_value(
                    assignment.engagement_type,
                    _ENUM_FIELDS["engagement_type"],
                )
                if assignment is not None
                else ""
            ),
            "response_required": (
                assignment.response_required if assignment is not None else ""
            ),
            "engagement_status": engagement_status,
            "event_type": event_type,
            "previous_state": _safe_enum_value(event.previous_state, state_values),
            "new_state": _safe_enum_value(event.new_state, state_values),
            "outcome": _outreach_outcome(
                event_type, event, assignment, engagement_status
            ),
            "response_latency_seconds": (
                _response_latency(
                    repository,
                    assignment,
                    engagement_status,
                    interviews,
                    decisions,
                )
                if assignment is not None
                else ""
            ),
        }
        if not row["timestamp"]:
            row["timestamp"] = ""
        if filters.get("engagement_type") != row["engagement_type"] and filters.get(
            "engagement_type"
        ):
            continue
        if filters.get("engagement_status") != row["engagement_status"] and filters.get(
            "engagement_status"
        ):
            continue
        if filters.get("department") != row["target_department"] and filters.get(
            "department"
        ):
            continue
        if filters.get("person_id") != row["target_person_id"] and filters.get(
            "person_id"
        ):
            continue
        if filters.get("channel") != row["channel"] and filters.get("channel"):
            continue
        if filters.get("direction") != row["direction"] and filters.get("direction"):
            continue
        if filters.get("event_type") != row["event_type"] and filters.get(
            "event_type"
        ):
            continue
        if filters.get("timestamp_from") and row["timestamp"] < filters["timestamp_from"]:
            continue
        if filters.get("timestamp_to") and row["timestamp"] > filters["timestamp_to"]:
            continue
        rows.append({header: row[header] for header in OUTREACH_HEADERS})
    return rows


def _csv_cell(value: Any) -> str:
    if isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value if value is not None else "")
    formula_leading = text.startswith(("=", "+", "-", "@", "\t", "\r"))
    text = text.replace("\r", " ").replace("\n", " ")
    if formula_leading:
        return f"'{text}"
    return text


def _outreach_csv(rows: list[dict[str, Any]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=OUTREACH_HEADERS, lineterminator="\r\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({header: _csv_cell(row[header]) for header in OUTREACH_HEADERS})
    return stream.getvalue().encode("utf-8")


def _outreach_filename(token: str, denied_values: frozenset[str]) -> str:
    candidate = f"{token}-outreach-events.csv"
    if (
        token.isascii()
        and _SAFE_FILENAME_TOKEN.fullmatch(token)
        and _scrub_known_private(token, denied_values) == token
        and _scrub_known_private(candidate, denied_values) == candidate
    ):
        return candidate
    return "humanwire-outreach-events.csv"


def _evidence_summary(repository: Any, mandate: Mandate) -> dict[str, Any]:
    persisted = repository.list_evidence(mandate.mandate_id)
    public = shareable_evidence(persisted)
    return {
        "counts": {
            "shareable": sum(item.stakeholder_id is not None for item in public),
            "anonymous": sum(item.stakeholder_id is None for item in public),
            "private_blockers": private_blocker_count(persisted),
        },
        "items": [
            {
                "evidence_id": str(item.evidence_id),
                "evidence_type": item.evidence_type.value,
                "statement": redact_sensitive(item.statement),
                "stakeholder_id": item.stakeholder_id,
                "status": item.status.value,
                "related_decision": _redact(item.related_decision),
                "deadline": _iso(item.deadline),
                "resource": _redact(item.resource),
            }
            for item in public
        ],
    }


def _short_datetime(value: str | None) -> str:
    if not value:
        return "—"
    try:
        moment = datetime.fromisoformat(value).astimezone(UTC)
    except (TypeError, ValueError):
        return "—"
    return f"{moment.strftime('%b')} {moment.day}, {moment.strftime('%H:%M')}"


def _long_date(value: str | None) -> str:
    if not value:
        return "—"
    try:
        moment = datetime.fromisoformat(value).astimezone(UTC)
    except (TypeError, ValueError):
        return "—"
    return f"{moment.strftime('%b')} {moment.day}, {moment.year}"


def _relative_deadline(value: str | None, current_time: datetime) -> str:
    if not value:
        return ""
    try:
        deadline = datetime.fromisoformat(value).astimezone(UTC)
    except (TypeError, ValueError):
        return ""
    seconds = int((deadline - current_time.astimezone(UTC)).total_seconds())
    if seconds <= 0:
        return "due"
    days = seconds // 86_400
    if days:
        return f"in {days} day{'s' if days != 1 else ''}"
    hours = max(seconds // 3_600, 1)
    return f"in {hours} hour{'s' if hours != 1 else ''}"


def _countdown(value: str | None, current_time: datetime) -> str:
    if not value:
        return "--:--:--"
    try:
        deadline = datetime.fromisoformat(value).astimezone(UTC)
    except (TypeError, ValueError):
        return "--:--:--"
    remaining = max(int((deadline - current_time.astimezone(UTC)).total_seconds()), 0)
    hours, remainder = divmod(remaining, 3_600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _public_label(value: Any) -> str:
    return str(value or "").replace("_", " ").capitalize()


def _engagement_ladder(row: Mapping[str, Any]) -> list[dict[str, str]]:
    """Build one type-specific ladder strictly from safe persisted projection fields."""
    engagement_type = str(row.get("engagement_type") or "")
    engagement_status = str(row.get("engagement_status") or "")
    state = str(row.get("state") or "")
    attempts = max(int(row.get("attempt_count") or 0), 0)
    first_contact = bool(row.get("first_contact_at")) or attempts > 0
    acknowledged = bool(row.get("acknowledged_at"))
    completed = bool(row.get("completed_at")) or state == "complete"
    progressed = int(row.get("progress_current") or 0) > 0
    alternate = bool(row.get("channel_is_alternate"))
    channel = str(row.get("channel") or "").capitalize()
    terminal_failure = state in {"declined", "unreachable", "delivery_failed"}
    steps: list[dict[str, str]] = []

    def add(label: str, status: str, detail: str = "") -> None:
        steps.append({"label": label, "status": status, "detail": detail})

    def finish() -> list[dict[str, str]]:
        current_seen = False
        for step in steps:
            if step["status"] != "current":
                continue
            if current_seen:
                step["status"] = "pending"
            else:
                current_seen = True
        return steps

    later_outreach = attempts > 1 or alternate or acknowledged or progressed or completed
    add(
        "Primary",
        "complete" if first_contact else "current",
        _short_datetime(row.get("first_contact_at")),
    )
    if attempts > 1:
        reminder_done = alternate or acknowledged or progressed or completed
        add(
            "Reminder",
            "complete" if reminder_done else "current",
            _short_datetime(row.get("last_delivery_at")),
        )
    if alternate:
        alternate_done = acknowledged or progressed or completed
        add(
            f"Alternate {channel}" if channel else "Alternate",
            "complete" if alternate_done else "current",
            _short_datetime(row.get("last_delivery_at")),
        )

    outreach_waiting = (
        (not first_contact)
        or (attempts > 1 and not later_outreach)
        or (alternate and not (acknowledged or progressed or completed))
    )
    if engagement_type == "inform":
        delivery_confirmed = (
            state == "complete"
            and bool(row.get("completed_at"))
            and engagement_status == "delivered"
        )
        delivery_in_progress = state == "delivered" and not terminal_failure
        add(
            "Delivered",
            (
                "complete"
                if delivery_confirmed
                else "current" if delivery_in_progress else "pending"
            ),
            _short_datetime(row.get("completed_at")) if delivery_confirmed else "Pending",
        )
        return finish()

    if engagement_type == "acknowledge":
        add(
            "Acknowledged",
            "complete"
            if acknowledged or completed or engagement_status == "acknowledged"
            else "pending" if outreach_waiting or terminal_failure else "current",
            _short_datetime(row.get("acknowledged_at")),
        )
        return finish()

    if engagement_type in {"quick_response", "structured_interview"}:
        add(
            "Acknowledged",
            "complete"
            if acknowledged or progressed or completed
            else "pending" if outreach_waiting or terminal_failure else "current",
            _short_datetime(row.get("acknowledged_at")),
        )
        response_label = (
            "Quick response" if engagement_type == "quick_response" else "Interview"
        )
        response_complete = completed or engagement_status == "complete"
        response_current = progressed and not response_complete and not terminal_failure
        add(
            response_label,
            "complete" if response_complete else "current" if response_current else "pending",
            (
                f"{row.get('progress_current', 0)} of {row.get('progress_total', 0)}"
                if progressed or response_complete
                else "Pending"
            ),
        )
        confirmed = bool(row.get("evidence_confirmed"))
        add(
            "Confirmation",
            "complete" if confirmed else "current" if response_complete else "pending",
            (
                _short_datetime(row.get("completed_at"))
                if confirmed
                else "Awaiting CONFIRM" if response_complete else "Pending"
            ),
        )
        return finish()

    if engagement_type == "review_approval":
        decided = engagement_status in {"approved", "rejected", "change requested"}
        add(
            "Decision",
            "complete"
            if decided
            else "pending" if outreach_waiting or terminal_failure else "current",
            _STATUS_LABELS.get(engagement_status, "Pending"),
        )
        return finish()

    if engagement_type == "availability":
        recorded = engagement_status == "recorded"
        add(
            "Availability",
            "complete"
            if recorded
            else "pending" if outreach_waiting or terminal_failure else "current",
            "Recorded" if recorded else "Missing",
        )
        return finish()

    return finish()


def _filter_groups(row: Mapping[str, Any]) -> str:
    status = str(row.get("engagement_status") or "")
    groups: list[str] = []
    if status in {
        "in progress",
        "awaiting response",
        "awaiting acknowledgement",
        "awaiting confirmation",
    }:
        groups.append("in-progress")
    if status in {"complete", "acknowledged", "approved", "recorded", "delivered"}:
        groups.append("completed")
    if status in {"pending", "missing"}:
        groups.append("pending")
    if status == "delivered":
        groups.append("delivered")
    if status in {"delivery failed", "unreachable"}:
        groups.append("unreachable")
    return " ".join(groups)


def _present_stakeholder(row: Mapping[str, Any]) -> dict[str, Any]:
    channel = str(row.get("channel") or "")
    channel_label = channel.capitalize() if channel else "Not available"
    if channel and row.get("channel_is_alternate"):
        channel_label += " (alternate)"
    engagement_type = str(row.get("engagement_type") or "")
    status = str(row.get("engagement_status") or "")
    direction_labels = {
        "downward": "Gather input",
        "lateral": "Coordinate policy",
        "upward": "Get approval",
        "external": "External",
    }
    presented = dict(row)
    presented.update(
        {
            "engagement_label": _ENGAGEMENT_LABELS.get(
                engagement_type, engagement_type.replace("_", " ").capitalize()
            ),
            "status_label": _STATUS_LABELS.get(status, status.capitalize()),
            "channel_label": channel_label,
            "direction_label": direction_labels.get(
                str(row.get("direction") or ""), "External"
            ),
            "last_contact_display": _short_datetime(
                row.get("last_delivery_at") or row.get("first_contact_at")
            ),
            "filter_groups": _filter_groups(row),
            "ladder": _engagement_ladder(row),
        }
    )
    return presented


def _selected_stakeholder(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    active_states = {"acknowledged", "alternate_channel", "follow_up_due", "interviewing"}
    terminal_statuses = {
        "acknowledged",
        "approved",
        "change requested",
        "complete",
        "declined",
        "delivered",
        "delivery failed",
        "recorded",
        "rejected",
        "unreachable",
    }

    def priority(indexed: tuple[int, dict[str, Any]]) -> tuple[int, int]:
        index, row = indexed
        if row.get("required") and row.get("state") in active_states:
            return 0, index
        if row.get("required") and row.get("engagement_status") not in terminal_statuses:
            return 1, index
        return 2, index

    return min(enumerate(rows), key=priority)[1]


def _actionable_stakeholder(
    mandate_state: str,
    next_action: Mapping[str, Any] | None,
    rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if mandate_state != MandateState.INTERVIEWING.value or not next_action:
        return None
    person_id = next_action.get("person_id")
    scheduled_at = next_action.get("scheduled_at")
    if not person_id or not scheduled_at:
        return None
    matches = [
        row
        for row in rows
        if row.get("person_id") == person_id
        and row.get("next_action_at") == scheduled_at
        and row.get("state") in {state.value for state in _ACTIONABLE_ASSIGNMENT_STATES}
        and row.get("channel") in {channel.value for channel in Channel}
    ]
    return matches[0] if len(matches) == 1 else None


def _workflow_view(mandate: Mapping[str, Any]) -> list[dict[str, str]]:
    state = str(mandate.get("state") or "")
    current_index = {
        "received": 0,
        "planned": 1,
        "interviewing": 2,
        "synthesizing": 3,
        "negotiating": 4,
        "aligned": 4,
        "meeting_required": 5,
        "scheduling": 5,
        "meeting_ready": 5,
    }.get(state, 2)
    created = _short_datetime(mandate.get("created_at"))
    updated = _short_datetime(mandate.get("updated_at"))
    steps = []
    for index, (key, label) in enumerate(_WORKFLOW_STEPS):
        if index < current_index:
            status = "complete"
        elif index == current_index:
            status = "current"
        else:
            status = "pending"
        if index < 2:
            detail = created.split(",", 1)[0]
        elif index == current_index:
            detail = updated
        else:
            detail = "Pending"
        steps.append({"key": key, "label": label, "status": status, "detail": detail})
    return steps


def _event_description(event: Mapping[str, Any]) -> str:
    event_type = str(event.get("event_type") or "")
    person = event.get("person") or {}
    name = person.get("name") if isinstance(person, Mapping) else None
    subject = str(name or "Mandate")
    labels = {
        "mandate.created": "Mandate received",
        "engagement.plan_previewed": "Engagement plan previewed",
        "engagement.plan_released": "Engagement plan released",
        "mandate.interviewing": "Coordination started",
        "engagement.quick_response_sent": f"Quick response sent to {subject}",
        "engagement.structured_interview_sent": f"Structured interview sent to {subject}",
        "engagement.acknowledgement_sent": f"Acknowledgement sent to {subject}",
        "engagement.approval_pending": f"{subject} approval review is pending",
        "engagement.inform_delivered": f"{subject} received the coordination update",
        "engagement.quick_response_completed": f"{subject} completed a quick response",
        "engagement.acknowledged": f"{subject} acknowledged the request",
        "engagement.structured_interview_reminder": f"Reminder sent to {subject}",
        "engagement.structured_interview_alternate_selected": (
            f"Alternate channel selected for {subject}"
        ),
        "engagement.structured_interview_progressed": (
            f"{subject} structured interview progressed"
        ),
    }
    return labels.get(
        event_type,
        "Saved engagement event" if name else "Saved mandate event",
    )


_REACH_FILTERS = frozenset({"all", "in-progress", "completed", "pending", "unreachable"})
_REACH_LANES = (
    ("downward", "Gather input", "Downward"),
    ("lateral", "Coordinate policy", "Lateral"),
    ("upward", "Get approval", "Upward"),
)
_MANDATE_LEVEL_EVENT_TYPES = frozenset(
    {
        "alignment.brief_persisted",
        "engagement.plan_previewed",
        "engagement.plan_released",
        "mandate.aligned",
        "mandate.cancelled",
        "mandate.created",
        "mandate.expired",
        "mandate.interviewing",
        "mandate.meeting_ready",
        "mandate.meeting_required",
        "mandate.negotiating",
        "mandate.partial",
        "mandate.planned",
        "mandate.received",
        "mandate.scheduling",
        "mandate.synthesizing",
        "meeting.package_created",
        "model.fallback",
        "proposal.created",
        "proposal.response_recorded",
    }
)

_REPLAY_EVENT_EXPLANATIONS = {
    "mandate.created": ("Mandate", "Mandate created"),
    "mandate.received": ("Mandate", "Mandate received"),
    "engagement.plan_previewed": ("Plan", "Plan previewed"),
    "engagement.plan_released": ("Plan", "Plan released"),
    "mandate.planned": ("Plan", "Plan prepared"),
    "mandate.interviewing": ("Outreach", "Coordination started"),
    "engagement.quick_response_sent": ("Outreach", "Outreach sent"),
    "engagement.structured_interview_sent": ("Outreach", "Interview requested"),
    "engagement.acknowledgement_sent": ("Outreach", "Acknowledgement requested"),
    "engagement.inform_delivered": ("Outreach", "Update delivered"),
    "engagement.structured_interview_reminder": ("Outreach", "Reminder sent"),
    "engagement.structured_interview_alternate_selected": (
        "Outreach",
        "Alternate channel selected",
    ),
    "engagement.quick_response_completed": ("Response", "Response completed"),
    "engagement.acknowledged": ("Response", "Acknowledgement received"),
    "engagement.structured_interview_progressed": ("Response", "Interview progressed"),
    "interview.answer_recorded": ("Response", "Answer recorded"),
    "interview.evidence_confirmed": ("Evidence", "Evidence confirmed"),
    "engagement.approval_pending": ("Decision", "Decision requested"),
    "engagement.override_recorded": ("Decision", "Decision updated"),
    "proposal.response_recorded": ("Decision", "Proposal response recorded"),
    "proposal.created": ("Proposal", "Proposal prepared"),
    "mandate.negotiating": ("Proposal", "Proposal review started"),
    "mandate.meeting_required": ("Scheduling", "Meeting required"),
    "mandate.scheduling": ("Scheduling", "Scheduling started"),
    "availability.recorded": ("Scheduling", "Availability recorded"),
    "meeting.package_created": ("Scheduling", "Meeting prepared"),
    "mandate.meeting_ready": ("Scheduling", "Meeting ready"),
    "mandate.aligned": ("Outcome", "Outcome recorded"),
    "mandate.partial": ("Outcome", "Partial outcome recorded"),
    "mandate.cancelled": ("Outcome", "Mandate cancelled"),
    "mandate.expired": ("Outcome", "Mandate expired"),
}

_MANDATE_SCOPED_REPLAY_EVENT_TYPES = frozenset(
    {
        "mandate.created",
        "mandate.received",
        "engagement.plan_previewed",
        "engagement.plan_released",
        "mandate.planned",
        "mandate.interviewing",
        "proposal.response_recorded",
        "proposal.created",
        "mandate.negotiating",
        "mandate.meeting_required",
        "mandate.scheduling",
        "availability.recorded",
        "meeting.package_created",
        "mandate.meeting_ready",
        "mandate.aligned",
        "mandate.partial",
        "mandate.cancelled",
        "mandate.expired",
    }
)


def _replay_explanation(
    event_type: Any, bound_row: Mapping[str, Any] | None
) -> dict[str, str]:
    """Return public replay labels using only allowlisted event types and bindings."""
    explanation = _REPLAY_EVENT_EXPLANATIONS.get(str(event_type or ""))
    if explanation is None:
        return {
            "stage_label": "Saved event",
            "source_label": "HumanWire",
            "destination_label": "Decision Room",
            "data_point_label": "No public data point",
        }
    destination = (
        str(bound_row.get("name") or "").strip()
        if isinstance(bound_row, Mapping)
        else ""
    )
    return {
        "stage_label": explanation[0],
        "source_label": "HumanWire",
        "destination_label": destination or "Decision Room",
        "data_point_label": explanation[1],
    }


def _reach_result(row: Mapping[str, Any]) -> str:
    """Return deterministic result copy from the typed public engagement contract."""
    engagement_type = str(row.get("engagement_type") or "")
    status = str(row.get("engagement_status") or "")
    terminal = {
        "delivery failed": "Delivery failed",
        "unreachable": "Unreachable",
        "declined": "Declined",
    }
    if status in terminal:
        return terminal[status]
    if engagement_type in {"quick_response", "structured_interview"}:
        if status == "complete":
            return "Response complete"
        if status == "in progress":
            return "Response in progress"
        return "Awaiting response"
    if engagement_type == "acknowledge":
        return (
            "Receipt confirmed"
            if status == "acknowledged"
            else "Awaiting acknowledgement"
        )
    if engagement_type == "review_approval":
        return {
            "approved": "Decision approved",
            "rejected": "Decision rejected",
            "change requested": "Change requested",
        }.get(status, "Decision pending")
    if engagement_type == "inform":
        return "Update delivered" if status == "delivered" else "Delivery pending"
    if engagement_type == "availability":
        return (
            "Availability recorded"
            if status == "recorded"
            else "Availability missing"
        )
    return "Engagement pending"


def _safe_technical_path(token: Any, person_id: Any) -> str:
    safe_token = str(token or "")
    safe_person_id = str(person_id or "")
    if not _SAFE_IDENTIFIER.fullmatch(safe_token):
        return "/"
    base = f"/mandates/{safe_token}/data"
    if not _SAFE_IDENTIFIER.fullmatch(safe_person_id):
        return base
    return f"{base}?person_id={safe_person_id}"


def _reach_page_view(
    detail: Mapping[str, Any],
    stakeholder_rows: list[dict[str, Any]],
    saved_events: list[dict[str, Any]],
    requested_status: str | None = None,
    requested_person_id: str | None = None,
    *,
    expected_mandate_id: str,
) -> dict[str, Any]:
    """Compose the Reach presentation using only existing public projections."""
    plan_rows = list(stakeholder_rows)
    plan_order = {id(row): index for index, row in enumerate(plan_rows)}
    globally_ordered = sorted(
        plan_rows,
        key=lambda row: (
            row.get("first_contact_at") is None,
            str(row.get("first_contact_at") or ""),
            plan_order[id(row)],
            str(row.get("person_id") or ""),
        ),
    )
    person_ids = [str(row.get("person_id") or "") for row in globally_ordered]
    exact_public_person_ids = {
        person_id
        for person_id in person_ids
        if person_ids.count(person_id) == 1 and _SAFE_IDENTIFIER.fullmatch(person_id)
    }
    assignment_ids = [
        str(row.get("_reach_assignment_id") or "") for row in globally_ordered
    ]
    binding_person_counts = Counter(person_ids)
    binding_assignment_counts = Counter(assignment_ids)
    binding_identity_counts = Counter(
        (
            str(row.get("_reach_mandate_id") or ""),
            str(row.get("_reach_assignment_id") or ""),
            str(row.get("person_id") or ""),
        )
        for row in globally_ordered
    )
    exact_rows_by_identity = {
        identity: row
        for row in globally_ordered
        if (
            identity := (
                str(row.get("_reach_mandate_id") or ""),
                str(row.get("_reach_assignment_id") or ""),
                str(row.get("person_id") or ""),
            )
        )[0]
        == expected_mandate_id
        and identity[1]
        and identity[2] in exact_public_person_ids
        and binding_person_counts[identity[2]] == 1
        and binding_assignment_counts[identity[1]] == 1
        and binding_identity_counts[identity] == 1
    }

    replay: list[dict[str, Any]] = []
    indexed_events = list(enumerate(saved_events))
    indexed_events.sort(
        key=lambda item: (str(item[1].get("created_at") or ""), item[0])
    )
    for event_index, (_saved_order, event) in enumerate(indexed_events, start=1):
        person = event.get("person")
        person_id = (
            str(person.get("person_id") or "")
            if isinstance(person, Mapping)
            else ""
        )
        assignment_id = str(event.get("_reach_assignment_id") or "")
        event_mandate_id = str(event.get("_reach_mandate_id") or "")
        identity = (event_mandate_id, assignment_id, person_id)
        bound_row = exact_rows_by_identity.get(identity)
        event_type = str(event.get("event_type") or "")
        has_exact_binding = bound_row is not None
        has_exact_mandate_identity = (
            not assignment_id
            and not person_id
            and event_mandate_id == expected_mandate_id
        )
        can_explain = event_type in _REPLAY_EVENT_EXPLANATIONS and (
            has_exact_binding
            or (
                has_exact_mandate_identity
                and event_type in _MANDATE_SCOPED_REPLAY_EVENT_TYPES
            )
        )
        explanation = _replay_explanation(
            event_type if can_explain else "", bound_row if has_exact_binding else None
        )
        bound_person_id = (
            str(bound_row.get("person_id") or "")
            if has_exact_binding and can_explain
            else ""
        )
        if bound_person_id:
            highlight = bound_person_id
        elif (
            can_explain
            and
            not assignment_id
            and not person_id
            and event_mandate_id == expected_mandate_id
            and str(event.get("event_type") or "") in _MANDATE_LEVEL_EVENT_TYPES
        ):
            highlight = "origin"
        else:
            highlight = "none"
        context = " · ".join(
            value
            for value in (
                _public_label(event.get("channel")) if event.get("channel") else "",
                _public_label(event.get("direction")) if event.get("direction") else "",
            )
            if value
        )
        replay.append(
            {
                "index": event_index,
                "created_at": event.get("created_at"),
                "created_display": _short_datetime(event.get("created_at")),
                "description": _event_description(event),
                "context_label": context,
                "channel_label": (
                    _public_label(event.get("channel")) if event.get("channel") else ""
                ),
                "direction_label": (
                    _public_label(event.get("direction"))
                    if event.get("direction")
                    else ""
                ),
                **explanation,
                "highlight": highlight,
                "person_id": bound_person_id or None,
            }
        )

    default_row = _selected_stakeholder(plan_rows)
    selected_filter = requested_status if requested_status in _REACH_FILTERS else "all"
    prepared_rows: list[dict[str, Any]] = []
    for sequence, row in enumerate(globally_ordered, start=1):
        person_id = str(row.get("person_id") or "")
        engagement_type = str(row.get("engagement_type") or "")
        status = str(row.get("engagement_status") or "")
        channel = str(row.get("channel") or "")
        filter_groups = _filter_groups(row)
        prepared_rows.append(
            {
                "sequence": sequence,
                "person_id": person_id,
                "name": row.get("name") or "Stakeholder",
                "role": row.get("role") or "Role not listed",
                "department": row.get("department") or "Department not listed",
                "direction": row.get("direction"),
                "direction_label": _public_label(row.get("direction")),
                "engagement_type": engagement_type,
                "engagement_label": _ENGAGEMENT_LABELS.get(
                    engagement_type,
                    "Engagement",
                ),
                "channel_label": (
                    f"{channel.capitalize()} (alternate)"
                    if channel and row.get("channel_is_alternate")
                    else channel.capitalize() if channel else "Not available"
                ),
                "progress_current": row.get("progress_current", 0),
                "progress_total": row.get("progress_total", 0),
                "engagement_status": status,
                "status_label": _STATUS_LABELS.get(status, "Pending"),
                "status_key": status.replace(" ", "-"),
                "filter_groups": filter_groups,
                "first_contact_at": row.get("first_contact_at"),
                "first_contact_display": _short_datetime(row.get("first_contact_at")),
                "last_contact_at": row.get("last_delivery_at")
                or row.get("first_contact_at"),
                "last_contact_display": _short_datetime(
                    row.get("last_delivery_at") or row.get("first_contact_at")
                ),
                "result": _reach_result(row),
                "technical_path": _safe_technical_path(detail.get("token"), person_id),
                "ladder": _engagement_ladder(row),
                "history": [
                    event for event in replay if event.get("person_id") == person_id
                ],
                "is_default": row is default_row,
            }
        )

    visible_rows = [
        row
        for row in prepared_rows
        if selected_filter == "all"
        or selected_filter in str(row.get("filter_groups") or "").split()
    ]
    if not visible_rows:
        selected_filter = "all"
        visible_rows = list(prepared_rows)
    requested_matches = [
        row
        for row in visible_rows
        if requested_person_id
        and row.get("person_id") == requested_person_id
        and requested_person_id in exact_public_person_ids
    ]
    selected_row = (
        requested_matches[0]
        if len(requested_matches) == 1
        else next((row for row in visible_rows if row.get("is_default")), visible_rows[0] if visible_rows else None)
    )
    selected_sequence = selected_row.get("sequence") if selected_row else None
    for row in prepared_rows:
        row["selected"] = row.get("sequence") == selected_sequence
        row["initially_hidden"] = row not in visible_rows
        row.pop("is_default", None)

    lanes = []
    for order, (key, label, direction_label) in enumerate(_REACH_LANES, start=1):
        if key == "downward":
            lane_rows = [
                row
                for row in prepared_rows
                if row.get("direction") in {"downward", "external"}
            ]
        else:
            lane_rows = [row for row in prepared_rows if row.get("direction") == key]
        lanes.append(
            {
                "order": order,
                "key": key,
                "label": label,
                "direction_label": direction_label,
                "count": len(lane_rows),
                "count_label": f"{len(lane_rows)} {'person' if len(lane_rows) == 1 else 'people'}",
                "empty_label": f"No saved {direction_label.lower()} engagements",
                "rows": lane_rows,
            }
        )

    filter_counts = {
        "all": len(prepared_rows),
        "in_progress": sum(
            "in-progress" in str(row.get("filter_groups") or "").split()
            for row in prepared_rows
        ),
        "completed": sum(
            "completed" in str(row.get("filter_groups") or "").split()
            for row in prepared_rows
        ),
        "pending": sum(
            "pending" in str(row.get("filter_groups") or "").split()
            for row in prepared_rows
        ),
        "unreachable": sum(
            "unreachable" in str(row.get("filter_groups") or "").split()
            for row in prepared_rows
        ),
    }
    first_event = replay[0] if replay else None
    updated_display = _short_datetime(detail.get("updated_at"))
    return {
        "mandate": {
            "token": detail.get("token"),
            "objective": detail.get("objective"),
            "state_label": _public_label(detail.get("phase_label")),
            "initiator": detail.get("initiator"),
            "created_at": detail.get("created_at"),
            "created_display": _short_datetime(detail.get("created_at")),
            "updated_at": detail.get("updated_at"),
            "updated_display": updated_display,
            "updated_time": updated_display.rsplit(" ", 1)[-1] if detail.get("updated_at") else "—",
        },
        "lanes": lanes,
        "selected": selected_row,
        "selected_filter": selected_filter,
        "filter_counts": filter_counts,
        "replay": replay,
        "event_count": len(replay),
        "first_event": first_event,
    }


def _reach_view(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    definitions = (
        (
            "gather",
            "Gather input",
            {"quick_response", "structured_interview"},
            lambda row: bool(row.get("required")),
        ),
        ("coordinate", "Coordinate policy", {"acknowledge", "availability"}, lambda row: True),
        ("approval", "Get approval", {"review_approval"}, lambda row: True),
        ("optional", "Optional", {"inform"}, lambda row: not bool(row.get("required"))),
    )
    complete_statuses = {"acknowledged", "approved", "complete", "delivered", "recorded"}
    lanes: list[dict[str, Any]] = []
    for key, label, types, predicate in definitions:
        lane_rows = [
            row
            for row in rows
            if row.get("engagement_type") in types and predicate(row)
        ]
        if not lane_rows:
            continue
        complete = sum(row.get("engagement_status") in complete_statuses for row in lane_rows)
        pending = any(row.get("engagement_status") in {"pending", "missing"} for row in lane_rows)
        status = "pending" if pending else "complete" if complete == len(lane_rows) else "in progress"
        lanes.append(
            {
                "key": key,
                "label": label,
                "count": len(lane_rows),
                "complete": complete,
                "state_label": status,
            }
        )
    return lanes


def _decision_room_view(
    repository: Any, mandate: Mandate, current_time: datetime
) -> dict[str, Any]:
    detail = _mandate_detail(repository, mandate)
    safe_rows = _stakeholders(repository, mandate)
    plan_order = {
        planned.person_ref: index
        for index, planned in enumerate(mandate.plan.stakeholders)
    }
    safe_rows.sort(
        key=lambda row: plan_order.get(str(row.get("person_id")), len(plan_order))
    )
    selected = _selected_stakeholder(safe_rows)
    presented_rows = [_present_stakeholder(row) for row in safe_rows]
    selected_id = selected.get("person_id") if selected is not None else None
    presented_rows.sort(
        key=lambda row: (
            row.get("person_id") != selected_id,
            plan_order.get(str(row.get("person_id")), len(plan_order)),
        )
    )
    selected_view = next(
        (
            row
            for row in presented_rows
            if selected is not None and row.get("person_id") == selected.get("person_id")
        ),
        None,
    )
    action_view = _actionable_stakeholder(
        str(detail.get("state") or ""),
        detail.get("next_action"),
        presented_rows,
    )
    evidence = _evidence_summary(repository, mandate)
    public_evidence_count = evidence["counts"]["shareable"] + evidence["counts"]["anonymous"]
    missing_responses = sum(
        bool(row.get("required"))
        and row.get("engagement_status")
        not in {
            "acknowledged",
            "approved",
            "change requested",
            "complete",
            "declined",
            "delivered",
            "delivery failed",
            "recorded",
            "rejected",
            "unreachable",
        }
        for row in safe_rows
    )
    proposal = repository.get_active_proposal(mandate.mandate_id)
    ai_draft = {
        "count": 1 if proposal is not None else 0,
        "readiness": "Ready for human review" if proposal is not None else "Not ready",
        "assumptions": 0,
        "open_questions": len(proposal.issue_ids) if proposal is not None else 0,
    }
    events = _events(repository, mandate)
    timeline = [
        {
            "created_at": event.get("created_at"),
            "created_display": _short_datetime(event.get("created_at")),
            "description": _event_description(event),
            "context_label": " · ".join(
                value
                for value in (
                    str(event.get("channel") or "").capitalize(),
                    str(event.get("direction") or "").replace("_", " ").capitalize(),
                )
                if value
            ),
        }
        for event in reversed(events)
    ]
    if action_view is not None:
        action_name = str(action_view.get("name") or "Stakeholder")
        first_name = action_name.split()[0]
        channel_name = _public_label(action_view.get("channel"))
        next_action = {
            "is_actionable": True,
            "label": f"Contact {first_name} through registered {channel_name}",
            "why": str(action_view.get("reason") or ""),
            "due_at": action_view.get("next_action_at"),
            "countdown": _countdown(action_view.get("next_action_at"), current_time),
        }
    else:
        next_action = {
            "is_actionable": False,
            "label": "No pending action",
            "why": None,
            "due_at": None,
            "countdown": None,
        }
    filter_counts = {
        "all": len(presented_rows),
        "in_progress": sum("in-progress" in row["filter_groups"] for row in presented_rows),
        "completed": sum("completed" in row["filter_groups"] for row in presented_rows),
        "pending": sum("pending" in row["filter_groups"] for row in presented_rows),
        "delivered": sum("delivered" in row["filter_groups"] for row in presented_rows),
    }
    return {
        "mandate": {
            **detail,
            "state_label": _public_label(detail.get("phase_label")),
            "deadline_display": _long_date(detail.get("deadline")),
            "deadline_relative": _relative_deadline(detail.get("deadline"), current_time),
            "updated_display": _short_datetime(detail.get("updated_at")),
            "updated_time": (
                _short_datetime(detail.get("updated_at")).rsplit(" ", 1)[-1]
                if detail.get("updated_at")
                else "—"
            ),
            "progress_label": (
                f"{detail.get('complete_count', 0)} of {detail.get('stakeholder_count', 0)} "
                "engagements in progress"
            ),
        },
        "workflow": _workflow_view(detail),
        "stakeholders": presented_rows,
        "selected": selected_view,
        "filter_counts": filter_counts,
        "next_action": next_action,
        "human_evidence": {
            "saved": public_evidence_count,
            "unresolved": detail.get("open_issue_count", 0),
            "missing": missing_responses,
        },
        "ai_draft": ai_draft,
        "timeline": timeline,
        "reach": _reach_view(presented_rows),
    }


def _dashboard_view(rows: list[dict[str, Any]], state_filter: str | None) -> dict[str, Any]:
    selected_filter = state_filter if state_filter in _DASHBOARD_STATE_GROUPS else "all"
    counts = {
        key.replace("-", "_"): sum(row.get("state") in states for row in rows)
        for key, states in _DASHBOARD_STATE_GROUPS.items()
    }
    visible = (
        rows
        if selected_filter == "all"
        else [row for row in rows if row.get("state") in _DASHBOARD_STATE_GROUPS[selected_filter]]
    )
    presented = [
        {
            **row,
            "phase_display": str(row.get("phase_label") or "").replace("_", " ").capitalize(),
            "updated_display": _short_datetime(row.get("updated_at")),
        }
        for row in visible
    ]
    return {
        "mandates": presented,
        "filter": selected_filter,
        "counts": {"all": len(rows), **counts},
    }


def _meeting_projection(repository: Any, package: Any) -> dict[str, Any]:
    return {
        "purpose": redact_sensitive(package.purpose),
        "decision_owner": _person(repository, package.decision_owner_id),
        "required_attendees": [
            _person(repository, person_id) for person_id in package.required_attendee_ids
        ],
        "optional_attendees": [
            _person(repository, person_id) for person_id in package.optional_attendee_ids
        ],
        "proposed_start": _iso(package.proposed_start),
        "proposed_end": _iso(package.proposed_end),
        "timezone": package.timezone,
        "agreed_facts": [redact_sensitive(item) for item in package.agreed_facts],
        "open_decisions": [redact_sensitive(item) for item in package.open_decisions],
        "agenda": [redact_sensitive(item) for item in package.agenda],
        "calendar_written": package.calendar_written,
    }


def _verified_calendar(repository: Any, mandate: Mandate, current_time: datetime) -> bytes | None:
    if mandate.state is not MandateState.MEETING_READY:
        return None
    package = repository.get_meeting_package(mandate.mandate_id)
    if package is None or package.proposed_start is None or package.proposed_end is None:
        return None
    creation_events = [
        event
        for event in repository.list_events(mandate.mandate_id)
        if event.event_type == "meeting.package_created"
    ]
    if len(creation_events) != 1:
        return None
    creation_event = creation_events[0]
    if (
        creation_event.actor_id != mandate.initiator_id
        or creation_event.metadata != {"meeting_id": str(package.meeting_id)}
        or creation_event.created_at != package.created_at
        or creation_event.created_at < mandate.created_at
        or creation_event.created_at > mandate.updated_at
        or creation_event.created_at > current_time.astimezone(UTC)
    ):
        return None
    assignments = repository.list_assignments(mandate.mandate_id)
    report = AlignmentReport(
        mandate_id=mandate.mandate_id,
        issues=repository.list_issues(mandate.mandate_id),
        is_aligned=False,
    )
    coordinator = MeetingCoordinator(mandate.initiator_id)
    decision_owner_id = mandate.initiator_id
    attendee_ids = coordinator.required_attendees(report, assignments, decision_owner_id)
    try:
        for attendee_id in attendee_ids:
            stored = repository.get_runtime_status(
                f"availability:{mandate.mandate_id}:{attendee_id}"
            )
            if stored is None or stored[1] > package.created_at:
                return None
            windows = []
            for raw in stored[0].split("|"):
                start, end = raw.split("/", 1)
                from humanwire.domain import AvailabilityWindow

                windows.append(
                    AvailabilityWindow(
                        start=datetime.fromisoformat(start), end=datetime.fromisoformat(end)
                    )
                )
            coordinator.record_availability(attendee_id, windows)
        slot = coordinator.find_overlap()
        if slot is None:
            return None
        evidence = repository.list_evidence(mandate.mandate_id)
        by_evidence_id = {item.evidence_id: item for item in evidence}
        if any(
            evidence_id not in by_evidence_id
            or by_evidence_id[evidence_id].created_at > package.created_at
            for evidence_id in package.pre_read_evidence_ids
        ):
            return None
        rebuilt = coordinator.build_package(
            mandate.plan,
            report,
            assignments,
            decision_owner_id,
            shareable_evidence(evidence),
            proposed_slot=slot,
            created_at=package.created_at,
        )
    except (AttributeError, TypeError, ValueError):
        return None
    if rebuilt.model_dump(mode="json") != package.model_dump(mode="json"):
        return None
    denied_values = _private_deny_values(repository, mandate)
    public_objective = _scrub_known_private(mandate.plan.objective, denied_values)
    if public_objective != mandate.plan.objective:
        rebuilt = coordinator.build_package(
            mandate.plan.model_copy(update={"objective": public_objective}),
            report,
            assignments,
            decision_owner_id,
            shareable_evidence(evidence),
            proposed_slot=slot,
            created_at=creation_event.created_at,
        )
    public_uid = _public_calendar_uid(str(rebuilt.meeting_id), denied_values)
    public_summary = _scrub_known_private(rebuilt.purpose, denied_values)
    return render_ics(
        rebuilt,
        coordinator,
        public_uid=public_uid,
        public_summary=public_summary,
    )


def _html_page(
    title: str,
    payload: Any,
    denied_values: frozenset[str] = frozenset(),
) -> HTMLResponse:
    safe_title = html.escape(_public_projection(title, denied_values=denied_values))
    safe_payload = html.escape(
        json.dumps(
            _public_projection(payload, denied_values=denied_values),
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return HTMLResponse(
        "<!doctype html><html><head>"
        f"<title>{safe_title}</title></head><body><main><h1>{safe_title}</h1>"
        f"<pre>{safe_payload}</pre></main></body></html>"
    )


def _calendar_filename(token: str, denied_values: frozenset[str]) -> str:
    candidate = f"{token}-meeting.ics"
    if (
        _SAFE_IDENTIFIER.fullmatch(token)
        and _scrub_known_private(token, denied_values) == token
        and _scrub_known_private(candidate, denied_values) == candidate
    ):
        return candidate
    return "humanwire-meeting.ics"


def create_app(
    repository: SqlAlchemyHumanWireRepository,
    settings: Settings,
    clock: Callable[[], datetime] | None = None,
    demo_mode: bool = False,
) -> FastAPI:
    """Build the read-only web surface over an injected source-of-truth repository."""
    now = clock or (lambda: datetime.now(UTC))
    templates = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount(
        "/static",
        StaticFiles(directory=str(_PACKAGE_DIR / "static")),
        name="static",
    )
    app.state.repository = repository
    app.state.settings = settings
    app.state.demo_mode = demo_mode

    @app.exception_handler(404)
    async def bodyless_not_found(_request: Request, _error: HTTPException):
        return Response(status_code=404)

    @app.middleware("http")
    async def reject_mutations(request: Request, call_next):
        if request.method in _MUTATING_METHODS:
            return JSONResponse(status_code=405, content={"detail": "Method not allowed"})
        if not demo_mode and request.url.path.startswith("/api/v1/"):
            configured = settings.analytics_read_token
            supplied = request.headers.get("authorization", "")
            expected = (
                f"Bearer {configured.get_secret_value()}" if configured is not None else ""
            )
            if not expected or not hmac.compare_digest(supplied, expected):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unauthorized"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
        response = await call_next(request)
        data_form_action = bool(
            re.fullmatch(r"/mandates/[^/]+/data", request.url.path)
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action "
            + ("'self'" if data_form_action else "'none'")
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    def load_mandate(token: str) -> Mandate:
        try:
            mandate = repository.get_mandate_by_token(token)
        except Exception as error:
            raise HTTPException(status_code=503, detail="Service unavailable") from error
        if mandate is None:
            raise HTTPException(status_code=404, detail="Not found")
        return mandate

    def safe_projection(operation: Callable[[], Any], mandate: Mandate | None = None) -> Any:
        try:
            denied_values = (
                _private_deny_values(repository, mandate)
                if mandate is not None
                else frozenset()
            )
            return _public_projection(operation(), denied_values=denied_values)
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(status_code=503, detail="Service unavailable") from error

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, state: str | None = Query(default=None)):
        def project():
            mandates = repository.list_recent_mandates()
            corpora = {
                mandate.mandate_id: _private_deny_values(repository, mandate)
                for mandate in mandates
            }
            rows = [
                _public_projection(
                    _mandate_summary(repository, item),
                    denied_values=corpora[item.mandate_id],
                )
                for item in mandates
            ]
            denied_values = frozenset().union(*corpora.values()) if corpora else frozenset()
            return _public_projection(
                _dashboard_view(rows, state),
                denied_values=denied_values,
            )

        dashboard = safe_projection(project)
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "dashboard": dashboard,
                "demo_mode": demo_mode,
                "current_nav": "mandates",
                "nav_token": None,
            },
        )

    @app.get("/mandates/{token}", response_class=HTMLResponse)
    def decision_room(request: Request, token: str):
        mandate = load_mandate(token)
        room = safe_projection(
            lambda: _decision_room_view(repository, mandate, now()),
            mandate,
        )
        return templates.TemplateResponse(
            request=request,
            name="mandate.html",
            context={
                "room": room,
                "demo_mode": demo_mode,
                "current_nav": "mandates",
                "nav_token": room["mandate"]["token"],
            },
        )

    @app.get("/mandates/{token}/reach", response_class=HTMLResponse)
    def reach(request: Request, token: str):
        mandate = load_mandate(token)
        status_values = request.query_params.getlist("status")
        person_values = request.query_params.getlist("person_id")
        requested_status = status_values[0] if len(status_values) == 1 else None
        requested_person_id = person_values[0] if len(person_values) == 1 else None
        view = safe_projection(
            lambda: _reach_page_view(
                _mandate_detail(repository, mandate),
                _stakeholders(repository, mandate, include_reach_identity=True),
                _events(repository, mandate, include_reach_identity=True),
                requested_status,
                requested_person_id,
                expected_mandate_id=str(mandate.mandate_id),
            ),
            mandate,
        )
        return templates.TemplateResponse(
            request=request,
            name="reach.html",
            context={
                "reach": view,
                "demo_mode": demo_mode,
                "current_nav": "reach",
                "nav_token": view["mandate"]["token"],
            },
        )

    @app.get("/mandates/{token}/data", response_class=HTMLResponse)
    def data(request: Request, token: str):
        mandate = load_mandate(token)
        filters = _validated_outreach_filters(request)

        def project():
            rows = _outreach_rows(repository, mandate, filters)
            last_updated = rows[-1]["timestamp"] if rows else ""
            return {
                "mandate": {
                    "token": _safe_identifier(mandate.token),
                    "updated_at": last_updated,
                    "updated_display": _short_datetime(last_updated),
                },
                "headers": OUTREACH_HEADERS,
                "rows": rows,
                "row_count": len(rows),
                "row_count_label": (
                    f"{len(rows)} saved event{'s' if len(rows) != 1 else ''}"
                ),
                "filters": filters,
                "export_query": urlencode(list(filters.items())),
                "empty_label": (
                    "No outreach events match these filters"
                    if filters
                    else "No saved events"
                ),
                "engagement_types": sorted(_ENUM_FIELDS["engagement_type"]),
                "engagement_statuses": sorted(_ENUM_FIELDS["engagement_status"]),
                "channels": sorted(_ENUM_FIELDS["channel"]),
                "directions": sorted(_ENUM_FIELDS["direction"]),
            }

        view = safe_projection(project, mandate)
        return templates.TemplateResponse(
            request=request,
            name="data.html",
            context={
                "data": view,
                "demo_mode": demo_mode,
                "current_nav": "data",
                "nav_token": view["mandate"]["token"],
            },
        )

    @app.get("/mandates/{token}/meeting.ics")
    def meeting_ics(token: str):
        mandate = load_mandate(token)
        denied_values = _private_deny_values(repository, mandate)
        content = safe_projection(lambda: _verified_calendar(repository, mandate, now()), mandate)
        if content is None:
            raise HTTPException(status_code=404, detail="Not found")
        return Response(
            content=content,
            media_type="text/calendar; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{_calendar_filename(mandate.token, denied_values)}"'
                )
            },
        )

    @app.get("/health/live")
    def health_live():
        return {"status": "live"}

    @app.get("/health/ready")
    def health_ready():
        try:
            repository.list_recent_mandates(limit=1)
        except Exception:  # noqa: BLE001 - readiness must fail closed for any DB adapter error
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "database_unavailable"},
            )
        if demo_mode:
            return {"status": "ready", "mode": "demo"}
        try:
            settings.require_listener_credentials()
        except ValueError:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "configuration_unavailable"},
            )
        try:
            channels = [
                repository.get_runtime_status("channel.email"),
                repository.get_runtime_status("channel.telegram"),
            ]
            heartbeat = repository.get_runtime_status("listener.heartbeat")
        except Exception:  # noqa: BLE001 - readiness must fail closed for any DB adapter error
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "database_unavailable"},
            )
        freshness = timedelta(seconds=max(30, settings.due_action_poll_seconds * 3))
        current = now().astimezone(UTC)
        heartbeat_ready = (
            heartbeat is not None
            and heartbeat[0] == "alive"
            and current - heartbeat[1].astimezone(UTC) <= freshness
            and heartbeat[1].astimezone(UTC) <= current + timedelta(seconds=1)
        )
        if not heartbeat_ready or any(status is None or status[0] != "ready" for status in channels):
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "listener_unavailable"},
            )
        return {"status": "ready"}

    @app.get("/api/v1/mandates")
    def mandate_list(state: str | None = Query(default=None)):
        def project():
            mandates = repository.list_recent_mandates()
            if state is not None:
                mandates = [mandate for mandate in mandates if mandate.state.value == state]
            return [
                _public_projection(
                    _mandate_summary(repository, mandate),
                    denied_values=_private_deny_values(repository, mandate),
                )
                for mandate in mandates
            ]

        return safe_projection(project)

    @app.get("/api/v1/mandates/{token}")
    def mandate_detail(token: str):
        mandate = load_mandate(token)
        return safe_projection(lambda: _mandate_detail(repository, mandate), mandate)

    @app.get("/api/v1/mandates/{token}/stakeholders")
    def mandate_stakeholders(token: str):
        mandate = load_mandate(token)
        return safe_projection(lambda: _stakeholders(repository, mandate), mandate)

    @app.get("/api/v1/mandates/{token}/outreach-events")
    def mandate_events(request: Request, token: str):
        mandate = load_mandate(token)
        filters = _validated_outreach_filters(request)
        return safe_projection(lambda: _outreach_rows(repository, mandate, filters), mandate)

    @app.get("/api/v1/mandates/{token}/outreach-events.csv")
    def mandate_events_csv(request: Request, token: str):
        mandate = load_mandate(token)
        filters = _validated_outreach_filters(request)
        export = safe_projection(
            lambda: {
                "rows": _outreach_rows(repository, mandate, filters),
                "filename": _outreach_filename(
                    mandate.token, _private_deny_values(repository, mandate)
                ),
            },
            mandate,
        )
        return Response(
            content=_outreach_csv(export["rows"]),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{export["filename"]}"'
                )
            },
        )

    @app.get("/api/v1/mandates/{token}/evidence-summary")
    def mandate_evidence(token: str):
        mandate = load_mandate(token)
        return safe_projection(lambda: _evidence_summary(repository, mandate), mandate)

    return app
