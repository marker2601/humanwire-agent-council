"""Loopback-only orchestration for watching a synthetic HumanWire run."""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import uvicorn

from humanwire.config import Settings
from humanwire.model_client import FeatherlessJsonClient
from humanwire.persona_runtime import (
    FeatherlessPersonaDecisionEngine,
    PersonaDecisionEngine,
    SyntheticGenerationMode,
)
from humanwire.synthetic import default_synthetic_scenario, generate_scenario
from humanwire.synthetic_progress import (
    RepositoryProgressObserver,
    SyntheticProgressStore,
    SyntheticRunState,
    SyntheticRuntimeStatus,
    initial_progress,
)
from humanwire.synthetic_viewer import create_synthetic_viewer_app, validate_viewer_host

AgentMode = Literal["deterministic", "featherless"]


class ModelRuntimeUnavailable(RuntimeError):
    """A model setup failure carrying only a fixed safe reason."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class SyntheticWatchOptions:
    output: Path
    run_root: Path
    seed: int = 0
    agent_mode: AgentMode = "deterministic"
    port: int = 8766
    step_delay_ms: int = 350
    max_decision_workers: int = 4

    def __post_init__(self) -> None:
        output = Path(self.output).absolute()
        run_root = Path(self.run_root).absolute()
        object.__setattr__(self, "output", output)
        object.__setattr__(self, "run_root", run_root)
        if not 0 <= self.seed <= 2_147_483_647:
            raise ValueError("seed must be between 0 and 2147483647")
        if self.agent_mode not in {"deterministic", "featherless"}:
            raise ValueError("agent mode must be deterministic or featherless")
        if not 1024 <= self.port <= 65535:
            raise ValueError("port must be between 1024 and 65535")
        if not 0 <= self.step_delay_ms <= 3000:
            raise ValueError("step delay must be between 0 and 3000 milliseconds")
        if not 1 <= self.max_decision_workers <= 8:
            raise ValueError("max decision workers must be between 1 and 8")
        try:
            output.relative_to(run_root)
        except ValueError as error:
            raise ValueError("synthetic output path must be inside run root") from error
        if output == run_root:
            raise ValueError("synthetic output path must be inside run root")


class _PacedProgressObserver:
    """Delay presentation only after publishing newly persisted progress."""

    def __init__(self, delegate: RepositoryProgressObserver, step_delay_seconds: float) -> None:
        self._delegate = delegate
        self._step_delay_seconds = step_delay_seconds

    def capture(self, repository, scenario, **state: object) -> None:
        before = self._delegate.snapshot().saved_event_count
        self._delegate.capture(repository, scenario, **state)
        after = self._delegate.snapshot().saved_event_count
        if after > before and self._step_delay_seconds:
            time.sleep(self._step_delay_seconds)

    def mark_unavailable(self) -> None:
        self._delegate.mark_unavailable()

    def record_inert_attempt(self, **attempt: object) -> None:
        self._delegate.record_inert_attempt(**attempt)


def _decision_engine(mode: AgentMode) -> PersonaDecisionEngine | None:
    if mode == "deterministic":
        return None
    settings = Settings()
    if settings.featherless_api_key is None:
        raise ModelRuntimeUnavailable("model_credentials_missing")
    api_key = settings.featherless_api_key.get_secret_value()
    if not api_key.strip():
        raise ModelRuntimeUnavailable("model_credentials_missing")
    client = FeatherlessJsonClient(
        api_key=api_key,
        model=settings.featherless_model,
        base_url=settings.featherless_base_url,
    )
    return FeatherlessPersonaDecisionEngine(client, settings.featherless_model)


def _publish_terminal_failure(store: SyntheticProgressStore) -> None:
    try:
        current = store.snapshot()
        store.publish(
            current.model_copy(
                update={
                    "run_state": SyntheticRunState.FAILED,
                    "runtime_status": SyntheticRuntimeStatus.TERMINAL_FAILURE,
                    "active_persona_label": None,
                    "active_contract": None,
                    "final_trace_sha256": None,
                }
            )
        )
    except Exception:  # noqa: BLE001, S110 - presentation failure remains non-authoritative
        pass


def _run_generation_safely(
    *,
    scenario,
    output_path: Path,
    run_root: Path,
    decision_engine: PersonaDecisionEngine | None,
    max_decision_workers: int,
    progress_observer: _PacedProgressObserver,
    store: SyntheticProgressStore,
) -> None:
    try:
        generate_scenario(
            scenario,
            output_path,
            run_root,
            decision_engine=decision_engine,
            max_decision_workers=max_decision_workers,
            progress_observer=progress_observer,
        )
    except Exception:  # noqa: BLE001 - never expose provider or private runtime text
        _publish_terminal_failure(store)


def run_synthetic_watch(options: SyntheticWatchOptions) -> int:
    """Run generation to completion behind a loopback-only progress viewer."""
    options = SyntheticWatchOptions(**options.__dict__)
    if options.run_root.exists():
        raise FileExistsError("synthetic proof requires a fresh run root")

    scenario = default_synthetic_scenario(seed=options.seed)
    initial = initial_progress(scenario)
    if options.agent_mode == "featherless":
        initial = initial.model_copy(update={"mode": SyntheticGenerationMode.MODEL_ASSISTED})
    store = SyntheticProgressStore(initial)
    observer = _PacedProgressObserver(
        RepositoryProgressObserver(store),
        options.step_delay_ms / 1000,
    )
    app = create_synthetic_viewer_app(store, options.output)
    try:
        decision_engine = _decision_engine(options.agent_mode)
    except ModelRuntimeUnavailable as error:
        print("synthetic_status=failed", file=sys.stderr)
        print(f"failure_reason={error.reason}", file=sys.stderr)
        return 2

    worker = threading.Thread(
        target=_run_generation_safely,
        kwargs={
            "scenario": scenario,
            "output_path": options.output,
            "run_root": options.run_root,
            "decision_engine": decision_engine,
            "max_decision_workers": options.max_decision_workers,
            "progress_observer": observer,
            "store": store,
        },
        name="humanwire-synthetic-generation",
        daemon=False,
    )
    worker.start()
    print(f"viewer_url=http://127.0.0.1:{options.port}")
    try:
        uvicorn.run(
            app,
            host=validate_viewer_host("127.0.0.1"),
            port=options.port,
            log_level="warning",
        )
    finally:
        worker.join()
    return 0
