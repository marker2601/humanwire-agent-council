"""Read-only, privacy-safe HTTP projections of persisted HumanWire state."""

from __future__ import annotations

import hmac
import html
import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from humanwire.alignment import AlignmentReport
from humanwire.config import Settings
from humanwire.domain import (
    Channel,
    Direction,
    EvidenceStatus,
    EvidenceType,
    EvidenceVisibility,
    Mandate,
    MandateState,
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
    }
)
_IDENTIFIER_FIELDS = frozenset(
    {"actor_id", "evidence_id", "person_id", "stakeholder_id", "token"}
)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{0,99}$")
_SAFE_PUBLIC_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ENUM_FIELDS = {
    "channel": {item.value for item in Channel},
    "direction": {item.value for item in Direction},
    "evidence_type": {item.value for item in EvidenceType},
    "interview_status": {"complete", "in_progress", "not_started"},
    "state": {item.value for item in MandateState} | {item.value for item in StakeholderState},
    "status": {item.value for item in EvidenceStatus},
    "previous_state": {item.value for item in MandateState}
    | {item.value for item in StakeholderState},
    "new_state": {item.value for item in MandateState} | {item.value for item in StakeholderState},
}
_PRIVATE_MARKER = "[PRIVATE]"


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
    return frozenset(denied)


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
) -> dict[str, Any]:
    interview = interviews.get(assignment.interview_id)
    if interview is None:
        interview_status = "not_started"
        question = None
    elif interview.completed_at is not None:
        interview_status = "complete"
        question = None
    else:
        interview_status = "in_progress"
        question = interview.current_question_index + 1
    return {
        **_person(repository, assignment.person_id),
        "department": redact_sensitive(assignment.department),
        "direction": assignment.direction.value,
        "reason": redact_sensitive(assignment.reason),
        "required": assignment.required,
        "state": assignment.state.value,
        "attempt_count": assignment.attempt_count,
        "channel": interview.current_channel.value if interview and interview.current_channel else None,
        "interview_status": interview_status,
        "current_question": question,
        "first_contact_at": _iso(assignment.first_contact_at),
        "last_delivery_at": _iso(assignment.last_delivery_at),
        "next_action_at": _iso(assignment.next_action_at),
        "acknowledged_at": _iso(assignment.acknowledged_at),
        "completed_at": _iso(assignment.completed_at),
    }


def _next_action(assignments: list[StakeholderAssignment]) -> dict[str, Any] | None:
    scheduled = [assignment for assignment in assignments if assignment.next_action_at is not None]
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


def _stakeholders(repository: Any, mandate: Mandate) -> list[dict[str, Any]]:
    interviews = {
        interview.session_id: interview
        for interview in repository.list_interviews(mandate.mandate_id)
    }
    assignments = repository.list_assignments(mandate.mandate_id)
    return [
        _assignment_projection(repository, assignment, interviews)
        for assignment in sorted(
            assignments,
            key=lambda item: (item.direction.value, item.person_id, str(item.assignment_id)),
        )
    ]


def _events(repository: Any, mandate: Mandate) -> list[dict[str, Any]]:
    return [
        {
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
        for event in repository.list_events(mandate.mandate_id)
    ]


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
    calendar = render_ics(rebuilt, coordinator).decode("utf-8")
    return "\r\n".join(
        _scrub_known_private(line, denied_values) for line in calendar.split("\r\n")
    ).encode("utf-8")


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
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.repository = repository
    app.state.settings = settings
    app.state.demo_mode = demo_mode

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
        return await call_next(request)

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
    def home():
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
            return rows, frozenset().union(*corpora.values()) if corpora else frozenset()

        mandates, denied_values = safe_projection(project)
        return _html_page("HumanWire", mandates, denied_values)

    @app.get("/mandates/{token}", response_class=HTMLResponse)
    def decision_room(token: str):
        mandate = load_mandate(token)
        denied_values = _private_deny_values(repository, mandate)
        public_token = _scrub_known_private(mandate.token, denied_values)
        return _html_page(
            f"HumanWire {public_token}",
            safe_projection(lambda: _mandate_detail(repository, mandate), mandate),
            denied_values,
        )

    @app.get("/mandates/{token}/reach", response_class=HTMLResponse)
    def reach(token: str):
        mandate = load_mandate(token)
        denied_values = _private_deny_values(repository, mandate)
        rows = safe_projection(lambda: _stakeholders(repository, mandate), mandate)
        lanes = {
            direction: [row for row in rows if row["direction"] == direction]
            for direction in ("downward", "lateral", "upward", "external")
        }
        public_token = _scrub_known_private(mandate.token, denied_values)
        return _html_page(f"HumanWire {public_token} Reach", lanes, denied_values)

    @app.get("/mandates/{token}/data", response_class=HTMLResponse)
    def data(token: str):
        mandate = load_mandate(token)
        denied_values = _private_deny_values(repository, mandate)
        public_token = _scrub_known_private(mandate.token, denied_values)
        return _html_page(
            f"HumanWire {public_token} Data",
            safe_projection(lambda: _events(repository, mandate), mandate),
            denied_values,
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
    def mandate_events(token: str):
        mandate = load_mandate(token)
        return safe_projection(lambda: _events(repository, mandate), mandate)

    @app.get("/api/v1/mandates/{token}/evidence-summary")
    def mandate_evidence(token: str):
        mandate = load_mandate(token)
        return safe_projection(lambda: _evidence_summary(repository, mandate), mandate)

    return app
