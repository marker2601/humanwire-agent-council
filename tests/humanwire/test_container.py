import json
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from caspian_sdk import CommError

from humanwire import __main__ as cli
from humanwire.config import Settings
from humanwire.container import ApplicationContainer, DueActionWorker
from humanwire.domain import (
    Channel,
    DeliveryInstruction,
    DeliveryKind,
    Direction,
    EngagementType,
    EvidenceItem,
    EvidenceStatus,
    EvidenceType,
    EvidenceVisibility,
    IncomingMessage,
    Mandate,
    MandatePlan,
    MandateState,
    PlannedStakeholder,
    StakeholderAssignment,
    StakeholderState,
    WorkflowResult,
)
from humanwire.engagements import EngagementCoordinator
from humanwire.evidence import FeatherlessEvidenceExtractor, RuleBasedEvidenceExtractor
from humanwire.model_client import FeatherlessJsonClient
from humanwire.planning import FeatherlessMandatePlanner, RuleBasedMandatePlanner

NOW = datetime(2026, 8, 11, 16, 0, tzinfo=UTC)


def write_organization(path) -> None:
    path.write_text(
        json.dumps(
            {
                "people": [
                    {
                        "person_id": "manager",
                        "display_name": "Morgan Lee",
                        "role": "Operations Manager",
                        "department": "Operations",
                        "timezone": "America/Chicago",
                        "routes": [
                            {
                                "route_id": "manager-telegram",
                                "channel": "telegram",
                                "sender_address": "manager-chat",
                                "conversation_id": "manager-conversation",
                            }
                        ],
                    },
                    {
                        "person_id": "team-lead",
                        "display_name": "Riley Chen",
                        "role": "Team Lead",
                        "department": "Operations",
                        "timezone": "America/Chicago",
                        "manager_id": "manager",
                        "routes": [
                            {
                                "route_id": "team-lead-email",
                                "channel": "email",
                                "sender_address": "team-lead@example.test",
                                "recipient": "team-lead@example.test",
                            }
                        ],
                    },
                ],
                "initiator_policies": [
                    {
                        "person_id": "manager",
                        "allowed_directions": ["downward", "lateral", "upward", "external"],
                        "allowed_departments": ["Operations"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def make_settings(tmp_path, **overrides) -> Settings:
    organization_path = tmp_path / "organization.json"
    write_organization(organization_path)
    values = {
        "_env_file": None,
        "database_url": "sqlite://",
        "organization_path": organization_path,
    }
    values.update(overrides)
    return Settings(**values)


def seed_complete_mandate(container, *, suffix: str) -> Mandate:
    mandate_id = UUID(f"00000000-0000-0000-0000-{int(suffix):012d}")
    assignment_id = UUID(f"10000000-0000-0000-0000-{int(suffix):012d}")
    evidence_id = UUID(f"20000000-0000-0000-0000-{int(suffix):012d}")
    mandate = Mandate(
        mandate_id=mandate_id,
        token=f"HW-MODEL{suffix}",
        initiator_id="manager",
        origin_channel=Channel.TELEGRAM,
        origin_conversation_id="manager-conversation",
        origin_message_id=f"origin-{suffix}",
        redacted_request="Coordinate an operations decision.",
        objective="Coordinate an operations decision.",
        plan=MandatePlan(
            objective="Coordinate an operations decision.",
            required_decisions=["Choose the operating date"],
            stakeholders=[
                PlannedStakeholder(
                    person_ref="team-lead",
                    reason="Owns the operating plan.",
                    direction=Direction.DOWNWARD,
                    questions=["Which date is viable?"],
                )
            ],
            completion_conditions=["The required stakeholder responds."],
        ),
        state=MandateState.INTERVIEWING,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(days=1),
        idempotency_key=f"seed:{suffix}",
    )
    assignment = StakeholderAssignment(
        assignment_id=assignment_id,
        mandate_id=mandate_id,
        person_id="team-lead",
        department="Operations",
        direction=Direction.DOWNWARD,
        reason="Owns the operating plan.",
        required=True,
        engagement_type=EngagementType.QUICK_RESPONSE,
        state=StakeholderState.COMPLETE,
        route_ids=["team-lead-email"],
    )
    with container.repository.transaction() as unit:
        unit.add_mandate(mandate)
        unit.add_assignment(assignment)
    container.repository.add_evidence(
        EvidenceItem(
            evidence_id=evidence_id,
            mandate_id=mandate_id,
            assignment_id=assignment_id,
            stakeholder_id="team-lead",
            evidence_type=EvidenceType.FACT,
            statement="The authenticated contribution is confirmed.",
            visibility=EvidenceVisibility.SHAREABLE,
            status=EvidenceStatus.CONFIRMED,
            source_message_id=f"confirmed-{suffix}",
            channel=Channel.EMAIL,
            created_at=NOW,
        )
    )
    return mandate


def test_container_builds_offline_rule_fallbacks_without_creating_comm_client(
    tmp_path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)

    def reject_channel_creation(*args, **kwargs):
        del args, kwargs
        raise AssertionError("ApplicationContainer.build opened a channel")

    monkeypatch.setattr("caspian_sdk.CommClient", reject_channel_creation)

    container = ApplicationContainer.build(settings)

    assert isinstance(container.rule_planner, RuleBasedMandatePlanner)
    assert isinstance(container.planner, RuleBasedMandatePlanner)
    assert isinstance(container.rule_evidence_extractor, RuleBasedEvidenceExtractor)
    assert isinstance(container.evidence_extractor, RuleBasedEvidenceExtractor)
    assert container.model_client is None
    assert container.repository.list_recent_mandates() == []


def test_container_exposes_one_shared_engagement_coordinator(tmp_path) -> None:
    container = ApplicationContainer.build(make_settings(tmp_path))

    assert isinstance(container.engagement_coordinator, EngagementCoordinator)
    assert container.workflow.engagements is container.engagement_coordinator
    assert container.workflow.mandates.engagements is container.engagement_coordinator
    assert container.workflow.mandates.interviews is container.interview_coordinator
    assert container.engagement_coordinator.interviews is container.interview_coordinator


def test_configured_featherless_selects_real_json_adapters_without_opening_channels(
    tmp_path, monkeypatch
) -> None:
    settings = make_settings(tmp_path, featherless_api_key="featherless-test-key")

    def reject_channel_creation(*args, **kwargs):
        del args, kwargs
        raise AssertionError("ApplicationContainer.build opened a channel")

    monkeypatch.setattr("caspian_sdk.CommClient", reject_channel_creation)

    container = ApplicationContainer.build(settings)

    assert isinstance(container.model_client, FeatherlessJsonClient)
    assert isinstance(container.planner, FeatherlessMandatePlanner)
    assert isinstance(container.evidence_extractor, FeatherlessEvidenceExtractor)
    assert container.workflow.mandates.planner is container.planner
    assert container.workflow.mandates.interviews.evidence_extractor is container.evidence_extractor


def test_configured_model_is_invoked_by_container_workflow_synthesis_and_proposal(
    tmp_path, monkeypatch
) -> None:
    container = ApplicationContainer.build(
        make_settings(tmp_path, featherless_api_key="featherless-test-key")
    )
    mandate = seed_complete_mandate(container, suffix="1")
    calls: list[tuple[str, str]] = []

    def complete_json(system: str, user: str) -> dict:
        calls.append((system, user))
        if "Identify advisory issues" in system:
            return {"issues": []}
        return {"proposal": "Consider an alternate staffing handoff."}

    monkeypatch.setattr(container.model_client, "complete_json", complete_json)

    container.workflow.synthesis.run(mandate.mandate_id, NOW)

    proposal = container.repository.get_active_proposal(mandate.mandate_id)
    assert proposal is not None
    assert "advisory drafting suggestion" in proposal.text
    assert len(calls) == 2
    assert all("private" not in user.casefold() for _, user in calls)


def test_offline_container_workflow_uses_exact_deterministic_proposal_fallback(tmp_path) -> None:
    container = ApplicationContainer.build(make_settings(tmp_path))
    mandate = seed_complete_mandate(container, suffix="2")

    container.workflow.synthesis.run(mandate.mandate_id, NOW)

    proposal = container.repository.get_active_proposal(mandate.mandate_id)
    assert proposal is not None
    assert proposal.text == (
        "HUMANWIRE DRAFT PROPOSAL\n"
        "A required decision lacks authenticated evidence or approval authority. "
        "Reply ACCEPT, REJECT, or CHANGE with a requested change."
    )


def test_container_meeting_factory_is_used_by_public_availability_workflow(
    tmp_path, monkeypatch
) -> None:
    from humanwire.meetings import MeetingCoordinator

    factory_calls: list[str] = []

    def meeting_factory(initiator_id: str) -> MeetingCoordinator:
        factory_calls.append(initiator_id)
        return MeetingCoordinator(initiator_id)

    monkeypatch.setattr("humanwire.container.MeetingCoordinator", meeting_factory)
    container = ApplicationContainer.build(make_settings(tmp_path))
    mandate = seed_complete_mandate(container, suffix="3")
    with container.repository.transaction() as unit:
        unit.save_mandate(mandate.model_copy(update={"state": MandateState.SCHEDULING}))
    message = IncomingMessage(
        message_id="availability-3",
        conversation_id="manager-conversation",
        connection_id="telegram-connection",
        channel=Channel.TELEGRAM,
        sender_address="manager-chat",
        text="AVAILABLE HW-MODEL3 2026-08-12T09:00:00-05:00/2026-08-12T10:00:00-05:00",
        received_at=NOW,
    )

    container.workflow.handle(message)

    assert factory_calls == ["manager", "manager"]


class RecordingRepository:
    def __init__(self) -> None:
        self.statuses: list[tuple[str, str, datetime]] = []

    def set_runtime_status(self, key: str, value: str, updated_at: datetime) -> None:
        self.statuses.append((key, value, updated_at))


class DueWorkflow:
    def __init__(self, result: WorkflowResult) -> None:
        self.result = result
        self.calls: list[datetime] = []
        self.ran = threading.Event()

    def process_due(self, now: datetime) -> WorkflowResult:
        self.calls.append(now)
        self.ran.set()
        return self.result


class RecordingGateway:
    def __init__(self) -> None:
        self.calls: list[DeliveryInstruction] = []

    def dispatch(self, instruction: DeliveryInstruction) -> None:
        self.calls.append(instruction)


def due_deliveries() -> list[DeliveryInstruction]:
    return [
        DeliveryInstruction(
            kind=DeliveryKind.REPLY_TO_MESSAGE,
            text="first",
            mandate_token="HW-DUE",
            assignment_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            message_id="message-1",
        ),
        DeliveryInstruction(
            kind=DeliveryKind.SEND_TO_CONVERSATION,
            text="second",
            mandate_token="HW-DUE",
            assignment_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            conversation_id="directory-conversation",
        ),
    ]


def test_due_worker_writes_heartbeat_and_dispatches_each_due_result_once() -> None:
    deliveries = due_deliveries()
    workflow = DueWorkflow(WorkflowResult(deliveries=deliveries))
    gateway = RecordingGateway()
    repository = RecordingRepository()
    worker = DueActionWorker(workflow, gateway, repository, poll_seconds=60, clock=lambda: NOW)

    worker.run_once()

    assert repository.statuses == [("listener.heartbeat", "alive", NOW)]
    assert workflow.calls == [NOW]
    assert gateway.calls == deliveries


def test_due_worker_attempts_remaining_deliveries_after_one_dispatch_crashes() -> None:
    deliveries = due_deliveries()
    workflow = DueWorkflow(WorkflowResult(deliveries=deliveries))
    repository = RecordingRepository()

    class FlakyGateway(RecordingGateway):
        def dispatch(self, instruction: DeliveryInstruction) -> None:
            self.calls.append(instruction)
            if len(self.calls) == 1:
                raise RuntimeError("PRIVATE provider body")

    gateway = FlakyGateway()
    worker = DueActionWorker(workflow, gateway, repository, poll_seconds=60, clock=lambda: NOW)

    worker.run_once()

    assert gateway.calls == deliveries


def test_due_worker_thread_survives_a_transient_due_scan_failure() -> None:
    class FlakyWorkflow(DueWorkflow):
        def process_due(self, now: datetime) -> WorkflowResult:
            self.calls.append(now)
            if len(self.calls) == 1:
                raise RuntimeError("PRIVATE database body")
            self.ran.set()
            return self.result

    workflow = FlakyWorkflow(WorkflowResult())
    worker = DueActionWorker(
        workflow,
        RecordingGateway(),
        RecordingRepository(),
        poll_seconds=0.01,
        clock=lambda: NOW,
    )

    worker.start()
    survived = workflow.ran.wait(timeout=1)
    worker.stop()

    assert survived
    assert len(workflow.calls) >= 2


def test_due_worker_uses_named_thread_and_stops_cleanly() -> None:
    workflow = DueWorkflow(WorkflowResult())
    worker = DueActionWorker(
        workflow,
        RecordingGateway(),
        RecordingRepository(),
        poll_seconds=60,
        clock=lambda: NOW,
    )

    worker.start()
    assert workflow.ran.wait(timeout=1)
    thread = worker.thread
    worker.start()
    worker.stop()

    assert thread is not None
    assert thread.name == "humanwire-due-actions"
    assert worker.thread is thread
    assert not thread.is_alive()


def test_due_worker_stop_does_not_return_while_dispatch_is_still_in_flight() -> None:
    entered_dispatch = threading.Event()
    release_dispatch = threading.Event()
    stop_returned = threading.Event()

    class BlockingGateway(RecordingGateway):
        def dispatch(self, instruction: DeliveryInstruction) -> None:
            self.calls.append(instruction)
            entered_dispatch.set()
            release_dispatch.wait()

    worker = DueActionWorker(
        DueWorkflow(WorkflowResult(deliveries=due_deliveries()[:1])),
        BlockingGateway(),
        RecordingRepository(),
        poll_seconds=0.01,
        clock=lambda: NOW,
    )
    worker.start()
    assert entered_dispatch.wait(timeout=1)
    stopper = threading.Thread(target=lambda: (worker.stop(), stop_returned.set()))
    stopper.start()

    returned_before_dispatch = stop_returned.wait(timeout=2.2)
    release_dispatch.set()
    stopper.join(timeout=1)

    assert not returned_before_dispatch
    assert stop_returned.is_set()
    assert worker.thread is not None and not worker.thread.is_alive()


def test_cli_parser_exposes_required_commands_and_description() -> None:
    parser = cli.build_parser()

    assert parser.description == "AI chief of staff for adaptive human coordination"
    for command in ("init-db", "listen", "web", "smoke"):
        assert parser.parse_args([command]).command == command
    assert parser.parse_args(["studio", "--workspace-root", "work"]).command == "studio"


def test_init_database_redacts_password(tmp_path, capsys) -> None:
    database_path = (tmp_path / "humanwire.db").as_posix()
    settings = Settings(
        _env_file=None,
        database_url="postgresql://operator:private-password@db.example.test/humanwire",
    )
    monkey_session = object()

    # Avoid requiring a Postgres driver: this assertion is about CLI output, not SQLAlchemy.
    original = cli.create_session_factory
    cli.create_session_factory = lambda database_url: monkey_session
    try:
        cli.init_database(settings)
    finally:
        cli.create_session_factory = original

    output = capsys.readouterr().out
    assert "HumanWire database initialized" in output
    assert "private-password" not in output
    assert "***" in output
    assert database_path not in output


def test_listener_opens_channels_then_always_stops_worker_and_marks_channels_stopped(
    tmp_path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    events: list[str] = []
    repository = RecordingRepository()
    container = SimpleNamespace(workflow=object(), repository=repository)

    class ListenerGateway:
        def __init__(self, **kwargs) -> None:
            assert kwargs == {
                "settings": settings,
                "workflow": container.workflow,
                "repository": repository,
            }

        def connect(self) -> None:
            events.append("connect")

        def listen(self) -> None:
            events.append("listen")
            raise KeyboardInterrupt

        def close(self) -> None:
            events.append("close")
            repository.set_runtime_status("channel.email", "stopped", NOW)
            repository.set_runtime_status("channel.telegram", "stopped", NOW)

    class ListenerWorker:
        def __init__(self, **kwargs) -> None:
            assert kwargs["workflow"] is container.workflow
            assert kwargs["repository"] is repository
            assert kwargs["poll_seconds"] == settings.due_action_poll_seconds

        def start(self) -> None:
            events.append("start")

        def stop(self) -> None:
            events.append("stop")

    monkeypatch.setattr(cli.ApplicationContainer, "build", lambda selected: container)
    monkeypatch.setattr(cli, "CaspianGateway", ListenerGateway)
    monkeypatch.setattr(cli, "DueActionWorker", ListenerWorker)
    with pytest.raises(KeyboardInterrupt):
        cli.run_listener(settings)

    assert events == ["connect", "start", "listen", "stop", "close"]
    assert repository.statuses == [
        ("channel.email", "stopped", NOW),
        ("channel.telegram", "stopped", NOW),
    ]


def test_listener_closes_partial_gateway_when_second_channel_connect_fails(
    tmp_path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    events: list[str] = []
    repository = RecordingRepository()
    container = SimpleNamespace(workflow=object(), repository=repository)

    class PartialGateway:
        def __init__(self, **kwargs) -> None:
            del kwargs

        def connect(self) -> None:
            events.append("connect")
            repository.set_runtime_status("channel.email", "ready", NOW)
            repository.set_runtime_status("channel.telegram", "error", NOW)
            raise CommError(401, "PRIVATE Telegram provider response")

        def close(self) -> None:
            events.append("close")
            repository.set_runtime_status("channel.email", "stopped", NOW)

    class IdleWorker:
        def __init__(self, **kwargs) -> None:
            del kwargs

        def start(self) -> None:
            events.append("start")

        def stop(self) -> None:
            events.append("stop")

    monkeypatch.setattr(cli.ApplicationContainer, "build", lambda selected: container)
    monkeypatch.setattr(cli, "CaspianGateway", PartialGateway)
    monkeypatch.setattr(cli, "DueActionWorker", IdleWorker)

    with pytest.raises(CommError):
        cli.run_listener(settings)

    assert events == ["connect", "stop", "close"]
    assert repository.statuses == [
        ("channel.email", "ready", NOW),
        ("channel.telegram", "error", NOW),
        ("channel.email", "stopped", NOW),
    ]


def test_web_command_builds_container_and_starts_fastapi(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path, dashboard_host="127.0.0.7", dashboard_port=8765)
    repository = object()
    app = object()
    calls: list[tuple[object, str, int]] = []
    monkeypatch.setattr(
        cli.ApplicationContainer,
        "build",
        lambda selected: SimpleNamespace(repository=repository),
    )
    monkeypatch.setitem(
        sys.modules,
        "humanwire.web",
        SimpleNamespace(create_app=lambda selected_repository, selected_settings: app),
    )
    monkeypatch.setattr(
        cli.uvicorn,
        "run",
        lambda selected_app, *, host, port: calls.append((selected_app, host, port)),
    )

    cli.run_web(settings)

    assert calls == [(app, "127.0.0.7", 8765)]


def test_smoke_command_delegates_to_installed_smoke_module(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "humanwire.smoke.main", lambda argv: calls.append(f"smoke:{argv}")
    )

    cli.run_smoke()

    assert calls == ["smoke:[]"]


def test_installed_smoke_command_runs_outside_repository(tmp_path) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-m", "humanwire", "smoke"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
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
    ]
    assert result.stderr == ""


def test_smoke_command_forwards_only_explicit_live_flags(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        cli,
        "run_smoke",
        lambda argv: calls.append(list(argv)) or 0,
    )

    assert cli.main(["smoke", "--live", "--confirm-live"]) == 0

    assert calls == [["--live", "--confirm-live"]]
