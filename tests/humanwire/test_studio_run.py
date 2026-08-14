import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from humanwire.pydantic_persona import PydanticAIPersonaDecisionEngineFactory
from humanwire.studio_run import (
    ActiveRunError,
    ModelModeUnavailable,
    StudioRunManager,
    UnknownRunError,
)
from tests.humanwire.studio_fixtures import conflict_request, launch_request


def exception_graph_text(error: BaseException) -> str:
    pending = [error]
    seen = set()
    rendered = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        rendered.extend((repr(current), str(current), repr(current.args)))
        if current.__context__ is not None:
            pending.append(current.__context__)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
    return "\n".join(rendered)


def manager_with_blocking_runner(tmp_path, release, aliases, *, started=None):
    alias_iter = iter(aliases)

    def runner(scenario, output_path, run_root, **kwargs):
        Path(run_root).mkdir()
        if started is not None:
            started.set()
        release.wait(2)

    return StudioRunManager(
        workspace_root=tmp_path,
        runner=runner,
        alias_factory=alias_iter.__next__,
    )


def completed_manager(tmp_path, *, model_factory_builder=None):
    def runner(scenario, output_path, run_root, **kwargs):
        Path(run_root).mkdir()

    return StudioRunManager(
        workspace_root=tmp_path,
        runner=runner,
        alias_factory=iter(["launch-001"]).__next__,
        model_factory_builder=model_factory_builder,
    )


def test_manager_does_not_start_until_create_run(tmp_path) -> None:
    calls = []
    manager = StudioRunManager(
        workspace_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs),
        alias_factory=iter(["launch-001"]).__next__,
    )

    assert manager.list_runs() == ()
    assert manager.active_alias is None
    assert calls == []


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("seed", -1, "seed must be between"),
        ("seed", 2_147_483_648, "seed must be between"),
        ("step_delay_ms", -1, "step delay must be between"),
        ("step_delay_ms", 3001, "step delay must be between"),
        ("max_decision_workers", 0, "max decision workers must be between"),
        ("max_decision_workers", 9, "max decision workers must be between"),
    ],
)
def test_invalid_execution_bounds_are_rejected_before_startup(
    tmp_path, option, value, message
) -> None:
    values = {option: value}

    with pytest.raises(ValueError, match=message):
        StudioRunManager(workspace_root=tmp_path, **values)

    assert not any(tmp_path.iterdir())


def test_manager_allows_exactly_one_active_run(tmp_path) -> None:
    release = threading.Event()
    started = threading.Event()
    manager = manager_with_blocking_runner(
        tmp_path,
        release,
        aliases=["launch-001"],
        started=started,
    )
    created = manager.create_run(launch_request())

    assert created.run_alias == "launch-001"
    assert created.workspace_url == "/runs/launch-001"
    with pytest.raises(ActiveRunError) as error:
        manager.create_run(conflict_request())
    assert error.value.run_alias == "launch-001"
    assert started.wait(2)
    assert len(list(tmp_path.iterdir())) == 1
    release.set()
    manager.join(created.run_alias, timeout=2)


def test_worker_start_failure_releases_atomic_ownership_without_private_text(
    tmp_path, monkeypatch
) -> None:
    def fail_start(self):
        raise RuntimeError("PRIVATE-THREAD-RUNTIME-PATH")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    manager = StudioRunManager(
        workspace_root=tmp_path,
        alias_factory=iter(["launch-001"]).__next__,
    )

    with pytest.raises(RuntimeError, match="coordination worker could not start") as error:
        manager.create_run(launch_request())

    assert "PRIVATE-THREAD" not in exception_graph_text(error.value)
    assert manager.active_alias is None
    assert manager.list_runs() == ()
    assert not any(tmp_path.iterdir())


def test_worker_that_starts_then_raises_retains_ownership_until_it_finishes(
    tmp_path, monkeypatch
) -> None:
    release = threading.Event()
    runner_started = threading.Event()
    captured = {}

    def runner(scenario, output_path, run_root, **kwargs):
        captured["thread"] = threading.current_thread()
        Path(run_root).mkdir()
        runner_started.set()
        assert release.wait(2)

    real_start = threading.Thread.start

    def start_then_raise(worker):
        real_start(worker)
        captured["started_worker"] = worker
        raise RuntimeError("PRIVATE-PARTIAL-START-RUNTIME")

    monkeypatch.setattr(threading.Thread, "start", start_then_raise)
    manager = StudioRunManager(
        workspace_root=tmp_path,
        runner=runner,
        alias_factory=iter(["launch-001", "conflict-002"]).__next__,
    )

    created = manager.create_run(launch_request())

    assert created.run_alias == "launch-001"
    assert runner_started.wait(2)
    worker = captured["started_worker"]
    assert worker is captured["thread"]
    assert worker.ident is not None
    assert worker.is_alive()
    assert manager.active_alias == "launch-001"
    assert manager.list_runs() == ("launch-001",)
    with pytest.raises(ActiveRunError) as error:
        manager.create_run(conflict_request())
    assert error.value.run_alias == "launch-001"

    release.set()
    manager.join("launch-001", timeout=2)
    assert manager.active_alias is None


def test_worker_is_non_daemon_and_receives_exact_run_contract(tmp_path) -> None:
    captured = {}

    def runner(scenario, output_path, run_root, **kwargs):
        captured.update(
            scenario=scenario,
            output_path=output_path,
            run_root=run_root,
            kwargs=kwargs,
            thread=threading.current_thread(),
        )
        Path(run_root).mkdir()

    manager = StudioRunManager(
        workspace_root=tmp_path,
        seed=19,
        step_delay_ms=0,
        max_decision_workers=3,
        runner=runner,
        alias_factory=iter(["launch-001"]).__next__,
    )
    created = manager.create_run(launch_request())
    manager.join(created.run_alias, timeout=2)

    assert captured["thread"].daemon is False
    assert captured["thread"].name == "humanwire-studio-launch-001"
    assert captured["scenario"].scenario_id == "launch-001"
    assert captured["scenario"].identity_seed == 19
    assert captured["output_path"] == tmp_path / "launch-001" / "transcript.json"
    assert captured["run_root"] == tmp_path / "launch-001"
    assert captured["kwargs"]["decision_engine"] is None
    assert captured["kwargs"]["max_decision_workers"] == 3
    assert captured["kwargs"]["progress_observer"] is captured["kwargs"][
        "presentation_observer"
    ]
    assert captured["kwargs"]["mandate_request"] == launch_request().objective
    assert captured["kwargs"]["include_change_story"] is False


def test_model_assisted_request_requires_explicit_model_factory(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "humanwire.studio_run.Settings",
        lambda: SimpleNamespace(featherless_api_key=None),
    )
    manager = StudioRunManager(workspace_root=tmp_path)

    with pytest.raises(ModelModeUnavailable, match="model_credentials_missing"):
        manager.create_run(launch_request(agent_mode="model_assisted"))

    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []
    assert manager.active_alias is None
    assert manager.list_runs() == ()


def test_standard_request_never_reads_settings_or_builds_model_factory(
    tmp_path, monkeypatch
) -> None:
    built = []

    def forbidden_settings():
        raise AssertionError("standard mode read model environment")

    monkeypatch.setattr("humanwire.studio_run.Settings", forbidden_settings)
    manager = completed_manager(
        tmp_path,
        model_factory_builder=lambda: built.append(True),
    )
    manager.create_run(launch_request(agent_mode="standard"))
    manager.join("launch-001", timeout=3)

    assert built == []


def test_model_factory_is_built_once_before_child_root_exists(tmp_path) -> None:
    checks = []
    factory = SimpleNamespace(model_identifier="fixture/model-v1")

    def builder():
        checks.append(not any(tmp_path.iterdir()))
        return factory

    captured = {}

    def runner(scenario, output_path, run_root, **kwargs):
        captured.update(kwargs)
        Path(run_root).mkdir()

    manager = StudioRunManager(
        workspace_root=tmp_path,
        runner=runner,
        alias_factory=iter(["model-001"]).__next__,
        model_factory_builder=builder,
    )
    created = manager.create_run(launch_request(agent_mode="model_assisted"))
    manager.join(created.run_alias, timeout=2)

    assert checks == [True]
    assert captured["decision_engine"] is factory


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("PRIVATE-MODEL-KEY C:/provider/config"),
        ModelModeUnavailable("PRIVATE-MODEL-READINESS-PATH"),
    ],
)
def test_model_builder_failure_is_translated_without_private_exception_or_root(
    tmp_path, failure
) -> None:
    def fail_builder():
        raise failure

    manager = StudioRunManager(
        workspace_root=tmp_path,
        alias_factory=iter(["model-001"]).__next__,
        model_factory_builder=fail_builder,
    )

    with pytest.raises(ModelModeUnavailable) as error:
        manager.create_run(launch_request(agent_mode="model_assisted"))

    assert error.value.reason == "model_runtime_unavailable"
    assert "PRIVATE-MODEL" not in exception_graph_text(error.value)
    assert not any(tmp_path.iterdir())
    assert manager.list_runs() == ()


def test_caller_model_error_is_rebuilt_without_its_private_exception_graph(
    tmp_path,
) -> None:
    def fail_builder():
        try:
            raise RuntimeError("PRIVATE-CALLER-MODEL-CAUSE")
        except RuntimeError as private_error:
            raise ModelModeUnavailable("PRIVATE-CALLER-REASON") from private_error

    manager = StudioRunManager(
        workspace_root=tmp_path,
        alias_factory=iter(["model-001"]).__next__,
        model_factory_builder=fail_builder,
    )

    with pytest.raises(ModelModeUnavailable) as error:
        manager.create_run(launch_request(agent_mode="model_assisted"))

    assert error.value.reason == "model_runtime_unavailable"
    assert "PRIVATE-CALLER" not in exception_graph_text(error.value)
    assert error.value.__context__ is None
    assert error.value.__cause__ is None
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize("key", [None, SecretStr(""), SecretStr("   ")])
def test_default_model_builder_rejects_missing_or_blank_key(
    tmp_path, monkeypatch, key
) -> None:
    settings = SimpleNamespace(
        featherless_api_key=key,
        featherless_model="fixture/model-v1",
        featherless_base_url="https://models.example.test/v1",
    )
    monkeypatch.setattr("humanwire.studio_run.Settings", lambda: settings)
    manager = StudioRunManager(
        workspace_root=tmp_path,
        alias_factory=iter(["model-001"]).__next__,
    )

    with pytest.raises(ModelModeUnavailable) as error:
        manager.create_run(launch_request(agent_mode="model_assisted"))

    assert error.value.reason == "model_credentials_missing"
    assert not any(tmp_path.iterdir())


def test_default_model_builder_uses_explicit_pydantic_ai_factory_without_leaking_key(
    tmp_path, monkeypatch
) -> None:
    key = "PRIVATE-STUDIO-MODEL-KEY"
    settings = SimpleNamespace(
        featherless_api_key=SecretStr(key),
        featherless_model="fixture/model-v1",
        featherless_base_url="https://models.example.test/v1",
    )
    monkeypatch.setattr("humanwire.studio_run.Settings", lambda: settings)
    captured = {}

    def runner(scenario, output_path, run_root, **kwargs):
        captured.update(kwargs)
        Path(run_root).mkdir()

    manager = StudioRunManager(
        workspace_root=tmp_path,
        runner=runner,
        alias_factory=iter(["model-001"]).__next__,
    )
    created = manager.create_run(launch_request(agent_mode="model_assisted"))
    manager.join(created.run_alias, timeout=2)

    factory = captured["decision_engine"]
    assert isinstance(factory, PydanticAIPersonaDecisionEngineFactory)
    assert factory.model_identifier == "fixture/model-v1"
    assert factory.base_url == "https://models.example.test/v1"
    assert key not in repr(factory)
    assert key not in json.dumps(manager.snapshot(created.run_alias).model_dump(mode="json"))


def test_completed_run_allows_new_isolated_coordination(tmp_path) -> None:
    aliases = iter(["launch-001", "conflict-002"])
    manager = StudioRunManager(
        workspace_root=tmp_path,
        seed=7,
        step_delay_ms=0,
        alias_factory=aliases.__next__,
    )
    first = manager.create_run(launch_request())
    manager.join(first.run_alias, timeout=10)
    first_root = tmp_path / first.run_alias
    first_transcript = first_root / "transcript.json"
    first_bytes = first_transcript.read_bytes()
    second = manager.create_run(conflict_request())
    manager.join(second.run_alias, timeout=10)
    second_root = tmp_path / second.run_alias

    assert first_root != second_root
    assert first_transcript.read_bytes() == first_bytes
    assert (second_root / "transcript.json").exists()
    assert manager.list_runs() == ("launch-001", "conflict-002")
    assert manager.snapshot(first.run_alias).run_state == "complete"
    assert manager.snapshot(second.run_alias).run_state in {"complete", "failed"}
    assert manager.final_binding(first.run_alias) is not None


def test_worker_failure_publishes_only_fixed_failed_snapshot(
    tmp_path, capsys
) -> None:
    def runner(scenario, output_path, run_root, **kwargs):
        Path(run_root).mkdir()
        raise RuntimeError("PRIVATE-PROVIDER-PATH-C:/secret/key")

    manager = StudioRunManager(
        workspace_root=tmp_path,
        runner=runner,
        alias_factory=iter(["failed-001"]).__next__,
    )
    created = manager.create_run(launch_request())
    manager.join(created.run_alias, timeout=2)
    snapshot = manager.snapshot(created.run_alias)
    output = capsys.readouterr()

    assert snapshot.run_state == "failed"
    assert snapshot.downloads_ready is False
    assert snapshot.outcome.state == "failed"
    assert "PRIVATE-PROVIDER" not in output.out + output.err
    assert "PRIVATE-PROVIDER" not in snapshot.model_dump_json()
    assert manager.active_alias is None
    assert manager.final_binding(created.run_alias) is None


def test_join_timeout_keeps_active_run_owned_by_worker(tmp_path) -> None:
    release = threading.Event()
    manager = manager_with_blocking_runner(tmp_path, release, aliases=["launch-001"])
    created = manager.create_run(launch_request())

    with pytest.raises(TimeoutError, match="coordination worker did not finish"):
        manager.join(created.run_alias, timeout=0.01)
    assert manager.active_alias == created.run_alias
    release.set()
    manager.join(created.run_alias, timeout=2)
    assert manager.active_alias is None


@pytest.mark.parametrize("alias", ["../other", "..", "a/b", "a\\b", " C-drive"])
def test_path_traversal_alias_is_rejected_before_any_child_root(
    tmp_path, alias
) -> None:
    manager = StudioRunManager(
        workspace_root=tmp_path,
        alias_factory=iter([alias]).__next__,
    )

    with pytest.raises(ValueError, match="coordination run alias"):
        manager.create_run(launch_request())

    assert not any(tmp_path.iterdir())
    assert manager.list_runs() == ()


def test_duplicate_alias_and_preexisting_child_root_are_never_overwritten(tmp_path) -> None:
    existing = tmp_path / "occupied-001"
    existing.mkdir()
    marker = existing / "operator-note.txt"
    marker.write_bytes(b"preserve me")
    manager = StudioRunManager(
        workspace_root=tmp_path,
        alias_factory=iter(["occupied-001"]).__next__,
    )

    with pytest.raises(FileExistsError, match="run root already exists"):
        manager.create_run(launch_request())

    assert marker.read_bytes() == b"preserve me"
    assert manager.list_runs() == ()


def test_unknown_aliases_and_unknown_traversal_are_safe(tmp_path) -> None:
    manager = StudioRunManager(workspace_root=tmp_path)

    with pytest.raises(UnknownRunError) as error:
        manager.snapshot("missing-001")
    assert error.value.run_alias == "missing-001"
    with pytest.raises(UnknownRunError):
        manager.join("missing-001", timeout=0)
    with pytest.raises(ValueError, match="coordination run alias"):
        manager.snapshot("../missing")


def test_concurrent_create_run_has_exactly_one_winner(tmp_path) -> None:
    caller_barrier = threading.Barrier(3)
    release = threading.Event()
    started = threading.Event()
    manager = manager_with_blocking_runner(
        tmp_path,
        release,
        aliases=["launch-001", "conflict-002"],
        started=started,
    )
    results = []
    failures = []

    def create(request):
        caller_barrier.wait()
        try:
            results.append(manager.create_run(request))
        except ActiveRunError as error:
            failures.append(error)

    first = threading.Thread(target=create, args=(launch_request(),))
    second = threading.Thread(target=create, args=(conflict_request(),))
    first.start()
    second.start()
    caller_barrier.wait()
    first.join(2)
    second.join(2)

    assert len(results) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], ActiveRunError)
    assert failures[0].run_alias == results[0].run_alias
    assert manager.list_runs() == (results[0].run_alias,)
    assert started.wait(2)
    assert len(list(tmp_path.iterdir())) == 1
    release.set()
    manager.join(results[0].run_alias, timeout=2)


def test_pacing_sleeps_only_after_a_new_persisted_product_ordinal(
    tmp_path, monkeypatch
) -> None:
    events = []
    slept_after = []

    class FakeStore:
        def snapshot(self):
            return SimpleNamespace(events=tuple(events), downloads_ready=False)

        def publish_failed(self):
            raise AssertionError("successful pacing run was marked failed")

    class FakeObserver:
        def snapshot(self):
            return FakeStore().snapshot()

        def evidence_bundle(self):
            return None

        def capture(self, *args, **kwargs):
            events.append(SimpleNamespace(persisted_ordinal=kwargs.pop("persisted_ordinal")))

        def mark_unavailable(self):
            raise AssertionError("successful pacing run was marked unavailable")

        def record_inert_attempt(self, **kwargs):
            events.append(SimpleNamespace(persisted_ordinal=None))

        def record_outbound(self, **kwargs):
            return None

        def record_decision(self, **kwargs):
            return None

    store = FakeStore()
    observer = FakeObserver()
    monkeypatch.setattr(
        "humanwire.studio_run.create_studio_progress",
        lambda request, scenario: (store, observer),
    )

    def observe_sleep(seconds):
        assert seconds == 0.001
        slept_after.append(sum(item.persisted_ordinal is not None for item in events))

    monkeypatch.setattr("humanwire.studio_run.time.sleep", observe_sleep)

    def runner(scenario, output_path, run_root, **kwargs):
        assert kwargs["progress_observer"] is kwargs["presentation_observer"]
        paced = kwargs["progress_observer"]
        paced.capture(None, scenario, persisted_ordinal=1)
        paced.record_inert_attempt()
        paced.capture(None, scenario, persisted_ordinal=2)
        paced.record_outbound()
        paced.record_decision()

    manager = StudioRunManager(
        workspace_root=tmp_path,
        step_delay_ms=1,
        runner=runner,
        alias_factory=iter(["paced-001"]).__next__,
    )
    created = manager.create_run(launch_request())
    manager.join(created.run_alias, timeout=2)

    assert slept_after == [1, 2]
