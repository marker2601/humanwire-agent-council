"""Deterministic, offline connected-product proof for HumanWire operators."""

from __future__ import annotations

import argparse
import csv
import io
import logging
import re
import sys
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from pydantic import SecretStr

from humanwire.alignment import contribution_status
from humanwire.caspian_gateway import CaspianGateway
from humanwire.config import Settings
from humanwire.database import create_session_factory
from humanwire.demo import create_demo_app
from humanwire.directory import InitiatorPolicy, OrganizationDirectory, OrganizationDocument
from humanwire.domain import (
    Channel,
    ContactRoute,
    Direction,
    EngagementType,
    MandatePlan,
    MandateState,
    Person,
    PlannedStakeholder,
)
from humanwire.evidence import RuleBasedEvidenceExtractor
from humanwire.offline_caspian import (
    OfflineCaspianClient,
    email_envelope,
    telegram_envelope,
)
from humanwire.planning import ResolvedPlan
from humanwire.repository import SqlAlchemyHumanWireRepository
from humanwire.web import OUTREACH_HEADERS, create_app
from humanwire.workflow import HumanWireWorkflow

_NOW = datetime(2026, 8, 12, 15, 0, tzinfo=UTC)
_PRIVATE_EVIDENCE = "PRIVATE-EVIDENCE-SMOKE-SENTINEL"
_PRIVATE_CHANGE = "PRIVATE-CHANGE-SMOKE-SENTINEL"
_PROVIDER_BODY = "PRIVATE-PROVIDER-SMOKE-SENTINEL"
_FORMULA = "=PRIVATE-FORMULA-SMOKE-SENTINEL"
_CREDENTIAL = "smoke-caspian-credential-sentinel"
_ANALYTICS_TOKEN = "smoke-analytics-credential-sentinel"
_SUCCESS_LINES = (
    "PASS domain",
    "PASS adaptive-engagement",
    "PASS preview-override",
    "PASS cross-channel-interview",
    "PASS explicit-approval",
    "PASS negotiation-limit",
    "PASS meeting-package",
    "PASS decision-room",
    "PASS propagation-lanes",
    "PASS analytics-export",
    "PASS privacy-scan",
)
_LIVE_CHECKLIST = """HumanWire live proof — operator checklist only; this mode does not transmit.
1. manager mandate
2. preview, override, and release
3. delivery acknowledgement
4. quick and structured replies
5. cross-channel continuation
6. explicit decision
7. bounded proposal rounds
8. availability
9. meeting package
10. UI, API, and CSV inspection
11. privacy and log review
"""


@dataclass(frozen=True)
class OfflineProof:
    primary_state: str
    change_state: str
    engagement_types: set[str]
    inform_question_count: int
    ack_interview_count: int
    quick_question_counts: tuple[int, ...]
    structured_channels: tuple[str, ...]
    review_response: str
    required_contributions: int
    proposal_rounds: int
    meeting_package_count: int
    restart_verified: bool
    replay_verified: bool
    change_created_proposal: bool
    change_created_meeting_package: bool
    private_sentinel_absent: bool
    gateway_handler_count: int
    gateway_channels: tuple[str, ...]
    provider_callback_count: int
    provider_failure_safe: bool


class _AdaptivePlanner:
    def __init__(self, people: dict[str, Person]) -> None:
        self._people = people

    def plan(self, text: str, initiator: Person) -> ResolvedPlan:
        del initiator
        if "change safety" in text.casefold():
            stakeholder_specs = [
                (
                    "approval-owner",
                    "Approve adaptive launch coverage",
                    Direction.UPWARD,
                    True,
                    EngagementType.REVIEW_APPROVAL,
                    [],
                )
            ]
            objective = "Prove explicit change safety"
        else:
            stakeholder_specs = [
                (
                    "inform-owner",
                    "Receive the launch notice",
                    Direction.DOWNWARD,
                    False,
                    EngagementType.INFORM,
                    [],
                ),
                (
                    "override-owner",
                    "Receive an optional acknowledgement",
                    Direction.LATERAL,
                    False,
                    EngagementType.INFORM,
                    [],
                ),
                (
                    "ack-owner",
                    "Acknowledge launch coordination",
                    Direction.UPWARD,
                    True,
                    EngagementType.ACKNOWLEDGE,
                    [],
                ),
                (
                    "quick-a",
                    "Confirm the first launch fact",
                    Direction.DOWNWARD,
                    True,
                    EngagementType.QUICK_RESPONSE,
                    ["Which launch date is recorded?"],
                ),
                (
                    "quick-b",
                    "Confirm the second launch fact",
                    Direction.LATERAL,
                    True,
                    EngagementType.QUICK_RESPONSE,
                    ["Which launch date is recorded?"],
                ),
                (
                    "structured-owner",
                    "Confirm the launch constraint",
                    Direction.LATERAL,
                    True,
                    EngagementType.STRUCTURED_INTERVIEW,
                    [
                        "What private context should remain internal?",
                        "Which launch date is recorded?",
                        "What can the team support?",
                    ],
                ),
                (
                    "approval-owner",
                    "Approve adaptive launch coverage",
                    Direction.UPWARD,
                    True,
                    EngagementType.REVIEW_APPROVAL,
                    [],
                ),
                (
                    "availability-owner",
                    "Provide required availability",
                    Direction.LATERAL,
                    True,
                    EngagementType.AVAILABILITY,
                    [],
                ),
            ]
            objective = "Coordinate adaptive launch coverage"
        return ResolvedPlan(
            plan=MandatePlan(
                objective=objective,
                required_decisions=["Approve adaptive launch coverage"],
                stakeholders=[
                    PlannedStakeholder(
                        person_ref=person_id,
                        reason=reason,
                        direction=direction,
                        required=required,
                        engagement_type=engagement_type,
                        response_required=engagement_type is not EngagementType.INFORM,
                        questions=questions,
                    )
                    for (
                        person_id,
                        reason,
                        direction,
                        required,
                        engagement_type,
                        questions,
                    ) in stakeholder_specs
                ],
                completion_conditions=["Every required contribution is recorded"],
            ),
            people=[self._people[item[0]] for item in stakeholder_specs],
            planner="deterministic",
        )


class _SmokeEvidenceExtractor:
    """Deterministically link real recorded answers to the smoke decision."""

    def __init__(self) -> None:
        self._fallback = RuleBasedEvidenceExtractor()

    def extract(self, *args, **kwargs):
        return [
            item.model_copy(
                update={
                    "related_decision": (
                        "Approve adaptive launch coverage"
                        if "launch date" in item.statement.casefold()
                        else None
                    )
                }
            )
            for item in self._fallback.extract(*args, **kwargs)
        ]


def _directory() -> tuple[OrganizationDirectory, dict[str, Person]]:
    manager = Person(
        person_id="manager",
        display_name="Morgan Reed",
        role="Operations Manager",
        department="Operations",
        timezone="UTC",
        routes=[
            ContactRoute(
                route_id="manager-telegram",
                channel=Channel.TELEGRAM,
                sender_address="manager-offline-chat",
                conversation_id="manager-offline-conversation",
                preferred=True,
            )
        ],
    )
    specs = (
        ("inform-owner", "Inez Ward", "Delivery"),
        ("override-owner", "Owen Bell", "Program"),
        ("ack-owner", "Noah Price", "Executive"),
        ("quick-a", "Quinn Stone", "Delivery"),
        ("quick-b", "Sam Lee", "Program"),
        ("structured-owner", "Priya Raman", "People"),
        ("approval-owner", "Maya Brooks", "Executive"),
        ("availability-owner", "Ari Lane", "Operations"),
    )
    people: dict[str, Person] = {manager.person_id: manager}
    for person_id, name, department in specs:
        people[person_id] = Person(
            person_id=person_id,
            display_name=name,
            role=f"{department} owner",
            department=department,
            timezone="UTC",
            routes=[
                ContactRoute(
                    route_id=f"{person_id}-email",
                    channel=Channel.EMAIL,
                    sender_address=f"{person_id}@private.example.test",
                    recipient=f"{person_id}@private.example.test",
                    preferred=True,
                ),
                ContactRoute(
                    route_id=f"{person_id}-telegram",
                    channel=Channel.TELEGRAM,
                    sender_address=f"{person_id}-private-chat",
                    conversation_id=f"{person_id}-private-conversation",
                ),
            ],
        )
    departments = {person.department for person in people.values() if person is not manager}
    return (
        OrganizationDirectory(
            OrganizationDocument(
                people=list(people.values()),
                initiator_policies=[
                    InitiatorPolicy(
                        person_id="manager",
                        allowed_directions={
                            Direction.DOWNWARD,
                            Direction.LATERAL,
                            Direction.UPWARD,
                        },
                        allowed_departments=departments,
                    )
                ],
            )
        ),
        people,
    )


def _settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        organization_path=Path("offline-directory-not-loaded.json"),
        caspian_api_key=SecretStr(_CREDENTIAL),
        telegram_bot_token=SecretStr("smoke-telegram-credential-sentinel"),
        analytics_read_token=SecretStr(_ANALYTICS_TOKEN),
        engagement_preview_seconds=15,
        engagement_require_go=False,
        acknowledgement_seconds=10,
        reminder_seconds=10,
    )


def _workflow(
    directory: OrganizationDirectory,
    people: dict[str, Person],
    repository: SqlAlchemyHumanWireRepository,
    settings: Settings,
) -> HumanWireWorkflow:
    return HumanWireWorkflow(
        directory,
        repository,
        _AdaptivePlanner(people),
        _SmokeEvidenceExtractor(),
        settings,
    )


def _provider_message(
    person: Person,
    text: str,
    message_id: str,
    *,
    channel: Channel | None = None,
) -> SimpleNamespace:
    chosen = channel or Channel.EMAIL
    route = next(route for route in person.routes if route.channel is chosen)
    envelope = {
        "message_id": message_id,
        "conversation_id": route.conversation_id or f"{person.person_id}-email-thread",
        "connection_id": f"offline-{chosen.value}-connection",
        "sender_address": route.sender_address,
        "sender_name": person.display_name,
        "text": text,
    }
    if chosen is Channel.EMAIL:
        return email_envelope(**envelope)
    return telegram_envelope(**envelope)


def _csv_matches_json(json_rows: list[dict], csv_body: str) -> bool:
    reader = csv.DictReader(io.StringIO(csv_body))
    if reader.fieldnames != OUTREACH_HEADERS:
        return False
    expected = [
        {
            key: (
                "true"
                if value is True
                else "false"
                if value is False
                else str(value if value is not None else "")
            )
            for key, value in row.items()
        }
        for row in json_rows
    ]
    return list(reader) == expected


def run_offline_proof(workdir: Path) -> OfflineProof:
    """Run the connected proof against a private file database and fake provider."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"Using `httpx` with `starlette\.testclient` is deprecated.*",
        )
        test_client = import_module("fastapi.testclient").TestClient
    workdir.mkdir(parents=True, exist_ok=True)
    database_url = f"sqlite:///{(workdir / 'humanwire-smoke.sqlite3').as_posix()}"
    settings = _settings(database_url)
    directory, people = _directory()
    session_factories = [create_session_factory(database_url)]
    repository = SqlAlchemyHumanWireRepository(session_factories[-1])
    workflow = _workflow(directory, people, repository, settings)
    clock = [_NOW]
    client = OfflineCaspianClient()
    gateway = CaspianGateway(
        settings=settings,
        workflow=workflow,
        repository=repository,
        client=client,
        clock=lambda: clock[0],
    )
    gateway.connect()

    logger = logging.getLogger("humanwire.caspian_gateway")
    captured_logs: list[str] = []

    class _SafeLogHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured_logs.append(
                "|".join(
                    str(value)
                    for value in (
                        record.getMessage(),
                        getattr(record, "event_type", ""),
                        getattr(record, "channel", ""),
                        getattr(record, "reason", ""),
                    )
                )
            )

    handler = _SafeLogHandler()
    previous_propagate = logger.propagate
    logger.addHandler(handler)
    logger.propagate = False
    try:
        client.emit_inbound(
            _provider_message(
                people["manager"],
                "/mandate\nCoordinate the primary adaptive product flow",
                "primary-create",
                channel=Channel.TELEGRAM,
            )
        )
        primary = repository.list_recent_mandates(1)[0]
        preview_assignments = repository.list_assignments(primary.mandate_id)
        preview_types = {item.engagement_type for item in preview_assignments}
        assert primary.state is MandateState.PLANNED
        assert preview_types == set(EngagementType)

        before_unauthorized = preview_assignments
        client.emit_inbound(
            _provider_message(
                people["quick-a"],
                f"ENGAGE {primary.token} override-owner ACKNOWLEDGE",
                "unauthorized-override",
            )
        )
        assert repository.list_assignments(primary.mandate_id) == before_unauthorized

        client.emit_inbound(
            _provider_message(
                people["manager"],
                f"ENGAGE {primary.token} override-owner ACKNOWLEDGE",
                "authorized-override",
                channel=Channel.TELEGRAM,
            )
        )
        overridden = {
            item.person_id: item
            for item in repository.list_assignments(primary.mandate_id)
        }
        assert overridden["override-owner"].engagement_type is EngagementType.ACKNOWLEDGE
        assert sum(
                event.event_type == "engagement.override_recorded"
            for event in repository.list_events(primary.mandate_id)
        ) == 1

        release_at = primary.created_at + timedelta(seconds=15)
        released = workflow.process_due(release_at)
        release_replay = workflow.process_due(release_at)
        assert len(released.deliveries) == len(overridden)
        assert release_replay.deliveries == []
        released_ids = {item.message_id for item in released.deliveries}
        assert released_ids == {
            item.outbox_id
            for item in repository.list_release_outbox(primary.mandate_id)
        }

        session_factories.append(create_session_factory(database_url))
        repository = SqlAlchemyHumanWireRepository(session_factories[-1])
        workflow = _workflow(directory, people, repository, settings)
        client = OfflineCaspianClient()
        gateway = CaspianGateway(
            settings=settings,
            workflow=workflow,
            repository=repository,
            client=client,
            clock=lambda: clock[0],
        )
        gateway.connect()
        clock[0] = release_at + timedelta(seconds=31)
        recovered = workflow.process_due(clock[0])
        assert {item.message_id for item in recovered.deliveries} == released_ids
        assert all(item.dispatch_claim_id for item in recovered.deliveries)
        restart_verified = (
            {item.message_id for item in recovered.deliveries} == released_ids
            and all(item.dispatch_claim_id for item in recovered.deliveries)
        )

        by_assignment = {
            item.assignment_id: item
            for item in repository.list_assignments(primary.mandate_id)
        }
        for delivery in recovered.deliveries:
            assignment = by_assignment[delivery.assignment_id]
            if assignment.person_id == "override-owner":
                client.configure_provider_failure(
                    people["override-owner"].routes[0].recipient,
                    body=_PROVIDER_BODY,
                )
            gateway.dispatch(delivery)
        provider_calls_after_callbacks = (
            len(client.initiated),
            len(client.sent),
            len(client.replies),
        )
        for delivery in recovered.deliveries:
            gateway.dispatch(delivery)
        assert (
            len(client.initiated),
            len(client.sent),
            len(client.replies),
        ) == provider_calls_after_callbacks

        def emit(
            person_id: str,
            text: str,
            message_id: str,
            *,
            channel: Channel = Channel.EMAIL,
            seconds: int,
        ) -> None:
            clock[0] = release_at + timedelta(seconds=seconds)
            client.emit_inbound(
                _provider_message(
                    people[person_id],
                    text,
                    message_id,
                    channel=channel,
                )
            )

        emit("ack-owner", f"ACK {primary.token}", "ack-owner-ack", seconds=32)
        emit(
            "override-owner",
            f"ACK {primary.token}",
            "override-owner-ack",
            channel=Channel.TELEGRAM,
            seconds=32,
        )
        for person_id, date, second in (
            ("quick-a", "2026-09-01", 33),
            ("quick-b", "2026-09-02", 36),
        ):
            emit(person_id, f"ACK {primary.token}", f"{person_id}-ack", seconds=second)
            emit(
                person_id,
                f"Launch date is {date}.",
                f"{person_id}-answer",
                seconds=second + 1,
            )
            emit(
                person_id,
                f"CONFIRM {primary.token}",
                f"{person_id}-confirm",
                seconds=second + 2,
            )
        quick_a_snapshot = (
            repository.get_assignment(
                next(
                    item.assignment_id
                    for item in repository.list_assignments(primary.mandate_id)
                    if item.person_id == "quick-a"
                )
            ),
            repository.list_evidence(primary.mandate_id),
            repository.list_events(primary.mandate_id),
        )
        emit("quick-a", f"ACK {primary.token}", "quick-a-ack", seconds=33)
        emit(
            "quick-a",
            "Launch date is 2026-09-01.",
            "quick-a-answer",
            seconds=34,
        )
        assert (
            repository.get_assignment(quick_a_snapshot[0].assignment_id),
            repository.list_evidence(primary.mandate_id),
            repository.list_events(primary.mandate_id),
        ) == quick_a_snapshot

        window = "2026-08-13T15:00:00+00:00/2026-08-13T16:00:00+00:00"
        emit(
            "approval-owner",
            f"DECIDE {primary.token} APPROVE",
            "approval-approve",
            seconds=35,
        )
        emit(
            "availability-owner",
            f"AVAILABLE {primary.token} {window}",
            "required-availability",
            seconds=35,
        )
        clock[0] = release_at + timedelta(seconds=40)
        reminder = workflow.process_due(clock[0])
        structured_id = next(
            item.assignment_id
            for item in repository.list_assignments(primary.mandate_id)
            if item.person_id == "structured-owner"
        )
        structured_reminders = [
            item for item in reminder.deliveries if item.assignment_id == structured_id
        ]
        assert len(structured_reminders) == 1
        gateway.dispatch_all(reminder)
        clock[0] = release_at + timedelta(seconds=51)
        alternate = workflow.process_due(clock[0])
        structured_alternates = [
            item for item in alternate.deliveries if item.assignment_id == structured_id
        ]
        assert len(structured_alternates) == 1
        assert structured_alternates[0].conversation_id == (
            "structured-owner-private-conversation"
        )
        gateway.dispatch_all(alternate)
        emit(
            "structured-owner",
            f"ACK {primary.token}",
            "structured-ack",
            channel=Channel.TELEGRAM,
            seconds=52,
        )
        for offset, answer in enumerate(
            (
                f"PRIVATE: {_PRIVATE_EVIDENCE} {_FORMULA}",
                "Launch date is 2026-09-03.",
            ),
            start=53,
        ):
            emit(
                "structured-owner",
                answer,
                f"structured-answer-{offset}",
                channel=Channel.TELEGRAM,
                seconds=offset,
            )
        emit(
            "structured-owner",
            "The team can support a reviewed launch.",
            "structured-answer-55",
            channel=Channel.TELEGRAM,
            seconds=55,
        )
        emit(
            "structured-owner",
            f"CONFIRM {primary.token}",
            "structured-confirm",
            channel=Channel.TELEGRAM,
            seconds=56,
        )
        primary = repository.get_mandate_by_token(primary.token)
        assert primary is not None
        assert primary.state is MandateState.NEGOTIATING, (
            primary.state,
            {item.person_id: item.state for item in repository.list_assignments(primary.mandate_id)},
            [
                (item.person_id, item.engagement_type, contribution_status(
                    item,
                    evidence=repository.list_evidence(primary.mandate_id),
                    decisions=repository.list_engagement_decisions(primary.mandate_id),
                    has_availability=repository.get_runtime_status(
                        f"availability:{primary.mandate_id}:{item.person_id}"
                    ) is not None,
                ))
                for item in repository.list_assignments(primary.mandate_id)
            ],
        )
        round_one = repository.get_active_proposal(primary.mandate_id)
        assert round_one is not None and round_one.round_number == 1

        required_ids = list(round_one.required_respondent_ids)
        for index, person_id in enumerate(required_ids[:-1]):
            emit(
                person_id,
                f"ACCEPT {primary.token}",
                f"round-one-{person_id}",
                seconds=56 + index,
            )
            assert repository.get_active_proposal(primary.mandate_id).proposal_id == (
                round_one.proposal_id
            )
        last_round_one = required_ids[-1]
        emit(
            last_round_one,
            f"CHANGE {primary.token} Adjust the reviewed launch plan",
            f"round-one-{last_round_one}",
            seconds=56 + len(required_ids),
        )
        round_two = repository.get_active_proposal(primary.mandate_id)
        assert round_two is not None and round_two.round_number == 2
        for index, person_id in enumerate(round_two.required_respondent_ids[:-1]):
            emit(
                person_id,
                f"ACCEPT {primary.token}",
                f"round-two-{person_id}",
                seconds=64 + index,
            )
            assert repository.get_active_proposal(primary.mandate_id).proposal_id == (
                round_two.proposal_id
            )
        last_round_two = round_two.required_respondent_ids[-1]
        emit(
            last_round_two,
            f"REJECT {primary.token}",
            f"round-two-{last_round_two}",
            seconds=64 + len(round_two.required_respondent_ids),
        )
        primary = repository.get_mandate_by_token(primary.token)
        assert primary is not None and primary.state is MandateState.SCHEDULING
        assert repository.get_active_proposal(primary.mandate_id) is None
        assert {
            proposal.round_number
            for proposal in (round_one, round_two)
        } == {1, 2}

        meeting_attendees = workflow._meeting_attendees(primary)
        for index, person_id in enumerate(meeting_attendees):
            emit(
                person_id,
                f"AVAILABLE {primary.token} {window}",
                f"meeting-availability-{person_id}",
                channel=people[person_id].routes[0].channel,
                seconds=72 + index,
            )
        primary = repository.get_mandate_by_token(primary.token)
        package = repository.get_meeting_package(primary.mandate_id)
        assert primary is not None and primary.state is MandateState.MEETING_READY
        assert package is not None

        event_count = len(repository.list_events(primary.mandate_id))
        evidence_count = len(repository.list_evidence(primary.mandate_id))
        response_count = sum(
            len(repository.list_proposal_responses(item.proposal_id))
            for item in (round_one, round_two)
        )
        emit(
            last_round_two,
            f"REJECT {primary.token}",
            f"round-two-{last_round_two}",
            seconds=90,
        )
        for person_id in meeting_attendees:
            emit(
                person_id,
                f"AVAILABLE {primary.token} {window}",
                f"meeting-availability-{person_id}",
                channel=people[person_id].routes[0].channel,
                seconds=91,
            )
        workflow.synthesis.run(primary.mandate_id, release_at + timedelta(seconds=92))
        workflow.process_due(release_at + timedelta(seconds=92))
        replay_verified = (
            len(repository.list_events(primary.mandate_id)) == event_count
            and len(repository.list_evidence(primary.mandate_id)) == evidence_count
            and sum(
                len(repository.list_proposal_responses(item.proposal_id))
                for item in (round_one, round_two)
            )
            == response_count
            and repository.get_meeting_package(primary.mandate_id) == package
        )

        clock[0] = release_at + timedelta(seconds=100)
        client.emit_inbound(
            _provider_message(
                people["manager"],
                "/mandate\nProve explicit CHANGE safety",
                "change-create",
                channel=Channel.TELEGRAM,
            )
        )
        change_mandate = repository.list_recent_mandates(1)[0]
        emit(
            "manager",
            f"GO {change_mandate.token}",
            "change-go",
            channel=Channel.TELEGRAM,
            seconds=101,
        )
        emit(
            "approval-owner",
            f"DECIDE {change_mandate.token} CHANGE {_PRIVATE_CHANGE}",
            "change-decision",
            seconds=102,
        )
        change_mandate = repository.get_mandate_by_token(change_mandate.token)
        assert change_mandate is not None and change_mandate.state is MandateState.PARTIAL

        app = create_app(
            repository,
            settings,
            clock=lambda: release_at + timedelta(seconds=103),
        )
        web = test_client(app)
        headers = {"Authorization": f"Bearer {_ANALYTICS_TOKEN}"}
        protected_denied = web.get(
            f"/api/v1/mandates/{primary.token}/outreach-events"
        )
        decision_room = web.get(f"/mandates/{primary.token}")
        reach = web.get(f"/mandates/{primary.token}/reach")
        data_page = web.get(f"/mandates/{primary.token}/data")
        data_json = web.get(
            f"/api/v1/mandates/{primary.token}/outreach-events", headers=headers
        )
        data_csv = web.get(
            f"/api/v1/mandates/{primary.token}/outreach-events.csv", headers=headers
        )
        calendar = web.get(f"/mandates/{primary.token}/meeting.ics")
        assert protected_denied.status_code == 401
        assert [
            decision_room.status_code,
            reach.status_code,
            data_page.status_code,
            data_json.status_code,
            data_csv.status_code,
            calendar.status_code,
        ] == [200] * 6
        assert _csv_matches_json(data_json.json(), data_csv.text)

        demo = test_client(create_demo_app())
        demo_responses = [
            demo.get("/mandates/HW-2411"),
            demo.get("/mandates/HW-2411/reach"),
            demo.get("/mandates/HW-2411/data"),
            demo.get("/api/v1/mandates/HW-2411/outreach-events"),
            demo.get("/api/v1/mandates/HW-2411/outreach-events.csv"),
            demo.get("/mandates/HW-2413/meeting.ics"),
        ]
        assert all(response.status_code == 200 for response in demo_responses)

        public_outputs = "\n".join(
            [
                decision_room.text,
                reach.text,
                data_page.text,
                data_json.text,
                data_csv.text,
                calendar.text,
                *[response.text for response in demo_responses],
                *captured_logs,
            ]
        )
        forbidden = (
            _PRIVATE_EVIDENCE,
            _PRIVATE_CHANGE,
            _PROVIDER_BODY,
            _FORMULA,
            _CREDENTIAL,
            _ANALYTICS_TOKEN,
            "@private.example.test",
            "private-conversation",
            "private-chat",
        )
        private_sentinel_absent = all(value not in public_outputs for value in forbidden)
        assert private_sentinel_absent
        assert re.search(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
            data_json.text + data_csv.text,
            re.IGNORECASE,
        ) is None

        assignments = {
            item.person_id: item
            for item in repository.list_assignments(primary.mandate_id)
        }
        interviews = repository.list_interviews(primary.mandate_id)
        by_assignment = {item.assignment_id: item for item in interviews}
        decisions = repository.list_engagement_decisions(primary.mandate_id)
        evidence = repository.list_evidence(primary.mandate_id)
        required_contributions = sum(
            not contribution_status(
                assignment,
                evidence=evidence,
                decisions=decisions,
                has_availability=(
                    repository.get_runtime_status(
                        f"availability:{primary.mandate_id}:{assignment.person_id}"
                    )
                    is not None
                ),
            ).blocking
            for assignment in assignments.values()
            if assignment.required
        )
        inform = assignments["inform-owner"]
        ack = assignments["ack-owner"]
        quick_counts = tuple(
            len(by_assignment[assignments[person_id].assignment_id].questions)
            for person_id in ("quick-a", "quick-b")
        )
        structured = by_assignment[assignments["structured-owner"].assignment_id]
        approval = next(
            item
            for item in decisions
            if item.assignment_id == assignments["approval-owner"].assignment_id
        )
        callback_events = [
            event
            for event in repository.list_events(primary.mandate_id)
            if event.event_type in {
                "outreach.delivery_confirmed",
                "outreach.delivery_failed",
            }
        ]
        return OfflineProof(
            primary_state=primary.state.value,
            change_state=change_mandate.state.value,
            engagement_types={item.engagement_type.value for item in assignments.values()},
            inform_question_count=(
                len(by_assignment[inform.assignment_id].questions)
                if inform.assignment_id in by_assignment
                else 0
            ),
            ack_interview_count=int(ack.assignment_id in by_assignment),
            quick_question_counts=quick_counts,
            structured_channels=tuple(
                channel.value for channel in structured.channel_history
            ),
            review_response=approval.response.value,
            required_contributions=required_contributions,
            proposal_rounds=2,
            meeting_package_count=1,
            restart_verified=restart_verified,
            replay_verified=replay_verified,
            change_created_proposal=(
                repository.get_active_proposal(change_mandate.mandate_id) is not None
            ),
            change_created_meeting_package=(
                repository.get_meeting_package(change_mandate.mandate_id) is not None
            ),
            private_sentinel_absent=private_sentinel_absent,
            gateway_handler_count=len(client.handlers),
            gateway_channels=tuple(sorted(set(client.inbound_channels))),
            provider_callback_count=len(callback_events),
            provider_failure_safe=(
                any("delivery_failed" in line for line in captured_logs)
                and _PROVIDER_BODY not in "\n".join(captured_logs)
            ),
        )
    finally:
        logger.removeHandler(handler)
        logger.propagate = previous_propagate
        for session_factory in session_factories:
            session_factory.kw["bind"].dispose()


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _parse_args(argv: Sequence[str]) -> tuple[bool, bool]:
    if len(argv) != len(set(argv)):
        raise ValueError("flags may be supplied only once")
    parser = _SafeArgumentParser(prog="humanwire smoke", add_help=False)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args(list(argv))
    return args.live, args.confirm_live


def main(argv: Sequence[str] | None = None) -> int:
    """Run the offline proof, or render the explicitly confirmed manual checklist."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        live, confirm_live = _parse_args(arguments)
    except ValueError as error:
        print(f"usage: humanwire smoke [--live --confirm-live]\nerror: {error}", file=sys.stderr)
        return 2
    if confirm_live and not live:
        print("usage: --confirm-live requires --live", file=sys.stderr)
        return 2
    if live and not confirm_live:
        print("Refusing live checklist: rerun with --live --confirm-live.", file=sys.stderr)
        return 2
    if live:
        print(_LIVE_CHECKLIST, end="")
        return 0

    with TemporaryDirectory(prefix="humanwire-smoke-") as temporary:
        proof = run_offline_proof(Path(temporary))
    assert proof.primary_state == MandateState.MEETING_READY.value
    assert proof.change_state == MandateState.PARTIAL.value
    assert proof.private_sentinel_absent
    for line in _SUCCESS_LINES:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
