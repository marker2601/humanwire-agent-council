import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from secondsignal.config import Settings
from secondsignal.database import create_session_factory
from secondsignal.identities import IdentityRegistry
from secondsignal.repository import SqlAlchemyCaseRepository
from secondsignal.risk import (
    FeatherlessRiskAnalyzer,
    RiskAnalyzer,
    RuleBasedRiskAnalyzer,
)
from secondsignal.state_machine import CaseStateMachine
from secondsignal.workflow import VerificationWorkflow


@dataclass(frozen=True)
class ApplicationContainer:
    settings: Settings
    session_factory: sessionmaker[Session]
    repository: SqlAlchemyCaseRepository
    registry: IdentityRegistry
    analyzer: RiskAnalyzer
    state_machine: CaseStateMachine
    workflow: VerificationWorkflow

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> "ApplicationContainer":
        application_clock = clock or (lambda: datetime.now(UTC))
        session_factory = create_session_factory(settings.database_url)
        repository = SqlAlchemyCaseRepository(session_factory)
        registry = IdentityRegistry.load(settings.registry_path)
        rules = RuleBasedRiskAnalyzer()
        if settings.featherless_api_key is None:
            analyzer: RiskAnalyzer = rules
        else:
            analyzer = FeatherlessRiskAnalyzer(
                api_key=settings.featherless_api_key.get_secret_value(),
                model=settings.featherless_model,
                base_url=settings.featherless_base_url,
                fallback=rules,
            )
        state_machine = CaseStateMachine()
        workflow = VerificationWorkflow(
            registry=registry,
            analyzer=analyzer,
            repository=repository,
            state_machine=state_machine,
            clock=application_clock,
            timeout=timedelta(seconds=settings.case_timeout_seconds),
        )
        return cls(
            settings=settings,
            session_factory=session_factory,
            repository=repository,
            registry=registry,
            analyzer=analyzer,
            state_machine=state_machine,
            workflow=workflow,
        )


class ExpiryWorker:
    def __init__(
        self,
        workflow: VerificationWorkflow,
        gateway: Any,
        repository: SqlAlchemyCaseRepository,
        poll_seconds: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.workflow = workflow
        self.gateway = gateway
        self.repository = repository
        self.poll_seconds = poll_seconds
        self.clock = clock or (lambda: datetime.now(UTC))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> None:
        now = self.clock()
        self.repository.set_runtime_status("listener.heartbeat", "alive", now)
        result = self.workflow.expire_due(now)
        for delivery in result.deliveries:
            self.gateway.dispatch(delivery)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.run_once()
            self._stop_event.wait(self.poll_seconds)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="secondsignal-expiry",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(self.poll_seconds + 1, 2))
