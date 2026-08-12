import json
import sys
import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from humanwire import __main__ as cli
from humanwire.config import Settings
from humanwire.container import ApplicationContainer, DueActionWorker
from humanwire.domain import DeliveryInstruction, DeliveryKind, WorkflowResult
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
                    }
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


def test_cli_parser_exposes_required_commands_and_description() -> None:
    parser = cli.build_parser()

    assert parser.description == "AI chief of staff that interviews the organization"
    for command in ("init-db", "listen", "web", "smoke"):
        assert parser.parse_args([command]).command == command


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
    monkeypatch.setattr(cli, "_now", lambda: NOW)

    with pytest.raises(KeyboardInterrupt):
        cli.run_listener(settings)

    assert events == ["connect", "start", "listen", "stop"]
    assert repository.statuses == [
        ("channel.email", "stopped", NOW),
        ("channel.telegram", "stopped", NOW),
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


def test_smoke_command_delegates_to_smoke_script(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("scripts.smoke_check.main", lambda: calls.append("smoke"))

    cli.run_smoke()

    assert calls == ["smoke"]
