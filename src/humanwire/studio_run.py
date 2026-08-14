"""Thread-safe ownership and lifecycle for product coordination runs."""

from __future__ import annotations

import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from humanwire.config import Settings
from humanwire.persona_runtime import PersonaDecisionEngineFactory
from humanwire.pydantic_persona import PydanticAIPersonaDecisionEngineFactory
from humanwire.studio_models import CoordinationRequest, StudioAgentMode
from humanwire.studio_projection import (
    StudioProgressObserver,
    StudioProgressStore,
    StudioWorkspaceSnapshot,
    create_studio_progress,
)
from humanwire.synthetic import (
    SyntheticRunResult,
    build_coordination_scenario,
    generate_scenario,
)
from humanwire.synthetic_progress import SyntheticEvidenceBundle

_SAFE_RUN_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MODEL_UNAVAILABLE_REASONS = frozenset(
    {"model_credentials_missing", "model_runtime_unavailable"}
)


class ActiveRunError(RuntimeError):
    """A safe signal that one coordination worker still owns the manager."""

    def __init__(self, run_alias: str) -> None:
        self.run_alias = run_alias
        super().__init__("a coordination run is already active")


class UnknownRunError(LookupError):
    """A safe signal that a validated presentation alias is not registered."""

    def __init__(self, run_alias: str) -> None:
        self.run_alias = run_alias
        super().__init__("coordination run was not found")


class ModelModeUnavailable(RuntimeError):
    """A model readiness failure carrying only an allowlisted reason."""

    def __init__(self, reason: str) -> None:
        if reason not in _MODEL_UNAVAILABLE_REASONS:
            reason = "model_runtime_unavailable"
        self.reason = reason
        super().__init__(reason)


@dataclass
class StudioRunRecord:
    run_alias: str
    request: CoordinationRequest
    run_root: Path
    transcript_path: Path
    store: StudioProgressStore
    observer: StudioProgressObserver
    worker: threading.Thread | None = None


@dataclass(frozen=True)
class RunCreationResult:
    run_alias: str
    workspace_url: str


@dataclass(frozen=True)
class StudioFinalBinding:
    snapshot: StudioWorkspaceSnapshot
    evidence: SyntheticEvidenceBundle
    transcript_path: Path


def safe_run_alias() -> str:
    """Return a bounded presentation alias without filesystem separators."""
    return "coordination-" + secrets.token_hex(8)


def validate_run_alias(run_alias: str) -> str:
    """Validate one alias before it reaches either manager state or a path."""
    if not isinstance(run_alias, str) or _SAFE_RUN_ALIAS.fullmatch(run_alias) is None:
        raise ValueError("coordination run alias is invalid")
    return run_alias


class _PacedStudioObserver:
    """Delay presentation only after publishing a new persisted product ordinal."""

    def __init__(self, delegate: StudioProgressObserver, delay_seconds: float) -> None:
        self._delegate = delegate
        self._delay_seconds = delay_seconds

    def _persisted_count(self) -> int:
        return sum(
            event.persisted_ordinal is not None
            for event in self._delegate.snapshot().events
        )

    def capture(self, repository, scenario, **state: object) -> None:
        before = self._persisted_count()
        self._delegate.capture(repository, scenario, **state)
        after = self._persisted_count()
        if after > before and self._delay_seconds:
            time.sleep(self._delay_seconds)

    def mark_unavailable(self) -> None:
        self._delegate.mark_unavailable()

    def record_inert_attempt(self, **attempt: object) -> None:
        self._delegate.record_inert_attempt(**attempt)

    def record_outbound(self, **presentation: object) -> None:
        self._delegate.record_outbound(**presentation)

    def record_decision(self, **presentation: object) -> None:
        self._delegate.record_decision(**presentation)


class StudioRunManager:
    """Own exactly one active worker while retaining completed run snapshots."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        seed: int = 0,
        step_delay_ms: int = 350,
        max_decision_workers: int = 4,
        model_factory_builder: Callable[[], PersonaDecisionEngineFactory] | None = None,
        alias_factory: Callable[[], str] = safe_run_alias,
        runner: Callable[..., SyntheticRunResult] = generate_scenario,
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        if not 0 <= seed <= 2_147_483_647:
            raise ValueError("seed must be between 0 and 2147483647")
        if not 0 <= step_delay_ms <= 3000:
            raise ValueError("step delay must be between 0 and 3000 milliseconds")
        if not 1 <= max_decision_workers <= 8:
            raise ValueError("max decision workers must be between 1 and 8")
        self._seed = seed
        self._step_delay_ms = step_delay_ms
        self._max_decision_workers = max_decision_workers
        self._model_factory_builder = model_factory_builder
        self._alias_factory = alias_factory
        self._runner = runner
        self._lock = threading.RLock()
        self._records: dict[str, StudioRunRecord] = {}
        self._active_alias: str | None = None

    @property
    def active_alias(self) -> str | None:
        with self._lock:
            return self._active_alias

    def create_run(self, request: CoordinationRequest) -> RunCreationResult:
        request = CoordinationRequest.model_validate(request)
        with self._lock:
            if self._active_alias is not None:
                raise ActiveRunError(self._active_alias)
            alias = validate_run_alias(self._alias_factory())
            if alias in self._records:
                raise FileExistsError("coordination run alias already exists")
            run_root = self._workspace_root / alias
            if run_root.resolve().parent != self._workspace_root:
                raise ValueError("coordination run alias is invalid")
            if run_root.exists():
                raise FileExistsError("coordination run root already exists")
            scenario = build_coordination_scenario(
                request,
                seed=self._seed,
                scenario_id=alias,
            )
            store, observer = create_studio_progress(request, scenario)
            decision_engine = self._decision_engine_for(request)
            record = StudioRunRecord(
                run_alias=alias,
                request=request,
                run_root=run_root,
                transcript_path=run_root / "transcript.json",
                store=store,
                observer=observer,
            )
            worker = threading.Thread(
                target=self._run_one,
                args=(record, scenario, decision_engine),
                name="humanwire-studio-" + alias,
                daemon=False,
            )
            record.worker = worker
            self._records[alias] = record
            self._active_alias = alias
            failed_before_start = False
            try:
                worker.start()
            except Exception:  # noqa: BLE001 - thread runtime details stay private
                failed_before_start = worker.ident is None and not worker.is_alive()
            if failed_before_start:
                self._records.pop(alias, None)
                if self._active_alias == alias:
                    self._active_alias = None
                raise RuntimeError("coordination worker could not start") from None
            return RunCreationResult(alias, "/runs/" + alias)

    def snapshot(self, run_alias: str) -> StudioWorkspaceSnapshot:
        with self._lock:
            record = self._record(run_alias)
        return record.store.snapshot()

    def final_binding(self, run_alias: str) -> StudioFinalBinding | None:
        with self._lock:
            record = self._record(run_alias)
        evidence = record.observer.evidence_bundle()
        snapshot = record.store.snapshot()
        if evidence is None or not snapshot.downloads_ready:
            return None
        return StudioFinalBinding(snapshot, evidence, record.transcript_path)

    def list_runs(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._records)

    def join(self, run_alias: str, timeout: float | None = None) -> None:
        with self._lock:
            worker = self._record(run_alias).worker
        if worker is None:
            raise RuntimeError("coordination worker was not started")
        worker.join(timeout)
        if worker.is_alive():
            raise TimeoutError("coordination worker did not finish")

    def _record(self, run_alias: str) -> StudioRunRecord:
        alias = validate_run_alias(run_alias)
        try:
            return self._records[alias]
        except KeyError as error:
            raise UnknownRunError(alias) from error

    def _decision_engine_for(
        self,
        request: CoordinationRequest,
    ) -> PersonaDecisionEngineFactory | None:
        if request.agent_mode is StudioAgentMode.STANDARD:
            return None
        factory: PersonaDecisionEngineFactory | None = None
        failure_reason: str | None = None
        try:
            if self._model_factory_builder is not None:
                factory = self._model_factory_builder()
            else:
                settings = Settings()
                if settings.featherless_api_key is None:
                    failure_reason = "model_credentials_missing"
                else:
                    api_key = settings.featherless_api_key.get_secret_value()
                    if not api_key.strip():
                        failure_reason = "model_credentials_missing"
                    else:
                        factory = PydanticAIPersonaDecisionEngineFactory(
                            api_key=api_key,
                            model_identifier=settings.featherless_model,
                            base_url=settings.featherless_base_url,
                        )
        except ModelModeUnavailable as error:
            failure_reason = (
                error.reason
                if error.reason in _MODEL_UNAVAILABLE_REASONS
                else "model_runtime_unavailable"
            )
        except Exception:  # noqa: BLE001 - provider/configuration details stay private
            failure_reason = "model_runtime_unavailable"
        if factory is None:
            raise ModelModeUnavailable(
                failure_reason or "model_runtime_unavailable"
            ) from None
        return factory

    def _run_one(
        self,
        record: StudioRunRecord,
        scenario: object,
        decision_engine: PersonaDecisionEngineFactory | None,
    ) -> None:
        observer: StudioProgressObserver | _PacedStudioObserver = record.observer
        if self._step_delay_ms:
            observer = _PacedStudioObserver(record.observer, self._step_delay_ms / 1000)
        try:
            self._runner(
                scenario,
                record.transcript_path,
                record.run_root,
                decision_engine=decision_engine,
                max_decision_workers=self._max_decision_workers,
                progress_observer=observer,
                presentation_observer=observer,
                mandate_request=record.request.objective,
                include_change_story=False,
            )
        except Exception:  # noqa: BLE001 - private worker failures stay inside this boundary
            record.store.publish_failed()
        finally:
            with self._lock:
                if self._active_alias == record.run_alias:
                    self._active_alias = None
