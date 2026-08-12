"""Dependency composition and due-action worker for HumanWire."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from humanwire.alignment import (
    AlignmentEngine,
    HybridAlignmentEngine,
    NegotiationCoordinator,
)
from humanwire.config import Settings
from humanwire.database import create_session_factory
from humanwire.directory import OrganizationDirectory
from humanwire.engagements import EngagementCoordinator
from humanwire.evidence import (
    EvidenceExtractor,
    FeatherlessEvidenceExtractor,
    RuleBasedEvidenceExtractor,
)
from humanwire.interviews import InterviewCoordinator
from humanwire.meetings import MeetingCoordinator
from humanwire.model_client import FeatherlessJsonClient, JsonModelClient
from humanwire.planning import (
    FeatherlessMandatePlanner,
    MandatePlanner,
    RuleBasedMandatePlanner,
)
from humanwire.repository import SqlAlchemyHumanWireRepository
from humanwire.services import SynthesisService
from humanwire.state_machine import MandateStateMachine, StakeholderStateMachine
from humanwire.workflow import HumanWireWorkflow

logger = logging.getLogger("humanwire.container")


@dataclass(frozen=True)
class ApplicationContainer:
    settings: Settings
    session_factory: sessionmaker[Session]
    repository: SqlAlchemyHumanWireRepository
    directory: OrganizationDirectory
    rule_planner: RuleBasedMandatePlanner
    rule_evidence_extractor: RuleBasedEvidenceExtractor
    model_client: JsonModelClient | None
    planner: MandatePlanner
    evidence_extractor: EvidenceExtractor
    mandate_state_machine: MandateStateMachine
    stakeholder_state_machine: StakeholderStateMachine
    engagement_coordinator: EngagementCoordinator
    interview_coordinator: InterviewCoordinator
    synthesis_service: SynthesisService
    negotiation_coordinator: NegotiationCoordinator
    alignment_engine_factory: Callable[[UUID], AlignmentEngine]
    meeting_coordinator_factory: Callable[[str], MeetingCoordinator]
    workflow: HumanWireWorkflow

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> ApplicationContainer:
        del clock  # The current workflow accepts message/due timestamps at its entrypoints.
        session_factory = create_session_factory(settings.database_url)
        repository = SqlAlchemyHumanWireRepository(session_factory)
        directory = OrganizationDirectory.load(settings.organization_path)
        rule_planner = RuleBasedMandatePlanner(directory)
        rule_evidence_extractor = RuleBasedEvidenceExtractor()

        model_client: JsonModelClient | None = None
        planner: MandatePlanner = rule_planner
        evidence_extractor: EvidenceExtractor = rule_evidence_extractor
        if settings.featherless_api_key is not None:
            model_client = FeatherlessJsonClient(
                api_key=settings.featherless_api_key.get_secret_value(),
                model=settings.featherless_model,
                base_url=settings.featherless_base_url,
            )
            planner = FeatherlessMandatePlanner(model_client, directory, fallback=rule_planner)
            evidence_extractor = FeatherlessEvidenceExtractor(
                model_client, fallback=rule_evidence_extractor
            )

        if model_client is None:
            alignment_engine_factory: Callable[[UUID], AlignmentEngine] = AlignmentEngine
        else:
            alignment_engine_factory = lambda mandate_id: HybridAlignmentEngine(
                mandate_id, model_client
            )
        negotiation_coordinator = NegotiationCoordinator(repository, model_client)
        meeting_coordinator_factory = MeetingCoordinator
        workflow = HumanWireWorkflow(
            directory=directory,
            repository=repository,
            planner=planner,
            evidence_extractor=evidence_extractor,
            settings=settings,
            alignment_engine_factory=alignment_engine_factory,
            negotiation_coordinator=negotiation_coordinator,
            meeting_coordinator_factory=meeting_coordinator_factory,
        )

        return cls(
            settings=settings,
            session_factory=session_factory,
            repository=repository,
            directory=directory,
            rule_planner=rule_planner,
            rule_evidence_extractor=rule_evidence_extractor,
            model_client=model_client,
            planner=planner,
            evidence_extractor=evidence_extractor,
            mandate_state_machine=workflow.mandates.state_machine,
            stakeholder_state_machine=workflow.mandates.interviews.state_machine,
            engagement_coordinator=workflow.engagements,
            interview_coordinator=workflow.mandates.interviews,
            synthesis_service=workflow.synthesis,
            negotiation_coordinator=negotiation_coordinator,
            alignment_engine_factory=alignment_engine_factory,
            meeting_coordinator_factory=meeting_coordinator_factory,
            workflow=workflow,
        )


class DueActionWorker:
    def __init__(
        self,
        workflow: HumanWireWorkflow,
        gateway: Any,
        repository: SqlAlchemyHumanWireRepository,
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

    @property
    def thread(self) -> threading.Thread | None:
        return self._thread

    def run_once(self) -> None:
        now = self.clock()
        self.repository.set_runtime_status("listener.heartbeat", "alive", now)
        result = self.workflow.process_due(now)
        for delivery in result.deliveries:
            try:
                self.gateway.dispatch(delivery)
            except Exception:  # noqa: BLE001 - one failed delivery must not starve the batch
                logger.warning(
                    "due_delivery_failed",
                    extra={
                        "mandate_token": delivery.mandate_token,
                        "event_type": "delivery.failed",
                        "reason": "worker_dispatch_error",
                    },
                )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - transient scans must not kill the worker
                logger.warning(
                    "due_action_failed",
                    extra={"event_type": "worker.failed", "reason": "worker_error"},
                )
            self._stop_event.wait(self.poll_seconds)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="humanwire-due-actions",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join()
