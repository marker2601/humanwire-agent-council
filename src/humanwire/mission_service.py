"""Durable coordinator for Demo run and Connected organization missions."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from humanwire.council_runtime import CouncilRunOutput
from humanwire.decisionos_models import DecisionOSContext, DecisionWorkspace
from humanwire.google_council import CouncilExecutionEvent, CouncilExecutionStatus
from humanwire.mission_models import (
    MissionActorType,
    MissionBlockedReason,
    MissionEvent,
    MissionMode,
    MissionParticipant,
    MissionRequest,
    MissionSnapshot,
    MissionState,
)
from humanwire.mission_store import MissionRepository
from humanwire.mission_transport import (
    MissionDispatchOutcome,
    MissionInboundResponse,
)

_READINESS_REASONS = {
    "no_eligible_participant": MissionBlockedReason.NO_ELIGIBLE_PARTICIPANT,
    "no_consented_route": MissionBlockedReason.NO_CONSENTED_ROUTE,
    "provider_not_configured": MissionBlockedReason.PROVIDER_NOT_CONFIGURED,
    "delivery_state_unknown": MissionBlockedReason.DELIVERY_STATE_UNKNOWN,
}


class MissionServiceUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("mission_unavailable")


_DEMO_CONTRIBUTIONS = (
    ("executive sponsor", "confirmed executive sponsorship and the accountable decision owner"),
    ("communications", "confirmed the communication constraint and stakeholder narrative"),
    ("product", "defined the scope, success criteria, and product dependency"),
    ("engineering", "confirmed engineering ownership, rollback, and operational dependencies"),
    ("risk", "recorded the risk gate that must close before approval"),
    ("approval", "reserved approval until the evidence and risk gates are satisfied"),
    ("operations", "confirmed operational readiness and post-approval availability"),
    ("business", "confirmed the business priority and measurable outcome"),
    ("decision owner", "reserved approval until the evidence and risk gates are satisfied"),
)


def _demo_contribution(participant: MissionParticipant) -> str:
    role = participant.role.casefold()
    detail = next(
        (copy for marker, copy in _DEMO_CONTRIBUTIONS if marker in role),
        "provided the requested decision input",
    )
    return f"{participant.display_name} · {participant.role} {detail}."


class MissionResolver(Protocol):
    def resolve(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        request: MissionRequest,
    ) -> tuple[MissionParticipant, ...]: ...


class MissionCouncil(Protocol):
    def run(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        objective: str,
        *,
        cancellation: threading.Event,
        on_event: Callable[[CouncilExecutionEvent], None],
    ) -> CouncilRunOutput: ...


class MissionDispatcher(Protocol):
    def check_readiness(
        self,
        context: DecisionOSContext,
        snapshot: MissionSnapshot,
        participant: MissionParticipant,
    ) -> str | None: ...

    def dispatch(
        self,
        context: DecisionOSContext,
        snapshot: MissionSnapshot,
        participant: MissionParticipant,
    ) -> MissionDispatchOutcome: ...


def _clock_value(clock: Callable[[], datetime]) -> datetime:
    failed = False
    value = None
    try:
        value = clock()
    except Exception:  # noqa: BLE001 - private clock details stay private
        failed = True
    if (
        failed
        or type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise MissionServiceUnavailable()
    return value.astimezone(UTC)


class MissionService:
    def __init__(
        self,
        *,
        repository: MissionRepository,
        resolver: MissionResolver,
        council: MissionCouncil,
        dispatcher: MissionDispatcher,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._resolver = resolver
        self._council = council
        self._dispatcher = dispatcher
        self._clock = clock

    def _load_bound(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        mission_id: str,
    ) -> MissionSnapshot:
        if (
            type(context) is not DecisionOSContext
            or type(workspace) is not DecisionWorkspace
            or type(mission_id) is not str
        ):
            raise MissionServiceUnavailable()
        try:
            snapshot = self._repository.load_bound(context, workspace, mission_id)
        except Exception:  # noqa: BLE001 - repository details stay private
            raise MissionServiceUnavailable() from None
        if (
            type(snapshot) is not MissionSnapshot
            or type(workspace) is not DecisionWorkspace
            or snapshot.organization_id != context.organization_id
            or snapshot.workspace_id != workspace.workspace_id
            or workspace.organization_id != context.organization_id
        ):
            raise MissionServiceUnavailable()
        return snapshot

    def _publish(
        self,
        context: DecisionOSContext,
        snapshot: MissionSnapshot,
        *,
        kind: str,
        stage: str,
        summary: str,
        participant_id: str | None = None,
        state: MissionState | None = None,
        blocked_reason: MissionBlockedReason | None = None,
        occurred_at: datetime | None = None,
        on_event: Callable[[MissionEvent], None] | None = None,
    ) -> MissionSnapshot:
        event = MissionEvent(
            ordinal=len(snapshot.events) + 1,
            kind=kind,
            stage=stage,
            summary=summary,
            participant_id=participant_id,
            created_at=occurred_at or _clock_value(self._clock),
        )
        proposed = snapshot.model_copy(
            update={
                "events": (*snapshot.events, event),
                "state": state or snapshot.state,
                "blocked_reason": blocked_reason,
            }
        )
        try:
            updated = self._repository.update(
                context,
                proposed,
                expected_version=snapshot.version,
            )
        except Exception:  # noqa: BLE001 - repository details stay private
            raise MissionServiceUnavailable() from None
        if on_event is not None:
            observer_failed = False
            try:
                on_event(event)
            except Exception:  # noqa: BLE001 - observers cannot stop a mission
                observer_failed = True
            if observer_failed:
                return updated
        return updated

    def create(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        request: MissionRequest,
    ) -> MissionSnapshot:
        if (
            type(context) is not DecisionOSContext
            or type(workspace) is not DecisionWorkspace
            or type(request) is not MissionRequest
        ):
            raise MissionServiceUnavailable()
        try:
            participants = self._resolver.resolve(context, workspace, request)
            if type(participants) is not tuple or any(
                type(item) is not MissionParticipant for item in participants
            ):
                raise ValueError
            snapshot = self._repository.create(context, workspace, request)
            proposed = snapshot.model_copy(update={"participants": participants})
            return self._repository.update(
                context,
                proposed,
                expected_version=snapshot.version,
            )
        except Exception:  # noqa: BLE001 - dependency details stay private
            raise MissionServiceUnavailable() from None

    def load(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        mission_id: str,
    ) -> MissionSnapshot:
        return self._load_bound(context, workspace, mission_id)

    def _block(
        self,
        context: DecisionOSContext,
        snapshot: MissionSnapshot,
        reason: MissionBlockedReason,
        on_event: Callable[[MissionEvent], None] | None,
    ) -> MissionSnapshot:
        return self._publish(
            context,
            snapshot,
            kind="outreach.blocked",
            stage="outreach",
            summary="Connected outreach is not ready.",
            state=MissionState.BLOCKED,
            blocked_reason=reason,
            on_event=on_event,
        )

    def run(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        mission_id: str,
        *,
        cancellation: threading.Event,
        on_event: Callable[[MissionEvent], None] | None = None,
    ) -> MissionSnapshot:
        if type(cancellation) is not threading.Event:
            raise MissionServiceUnavailable()
        current = self._load_bound(context, workspace, mission_id)
        if current.state is not MissionState.READY:
            raise MissionServiceUnavailable()
        required = tuple(
            item
            for item in current.participants
            if item.response_required
            and item.actor_type is MissionActorType.HUMAN_MEMBER
        )
        if current.mode is MissionMode.CONNECTED_ORGANIZATION:
            if not required:
                return self._block(
                    context,
                    current,
                    MissionBlockedReason.NO_ELIGIBLE_PARTICIPANT,
                    on_event,
                )
            for participant in required:
                try:
                    code = self._dispatcher.check_readiness(
                        context,
                        current,
                        participant,
                    )
                except Exception:  # noqa: BLE001 - transport details stay private
                    code = "delivery_state_unknown"
                if type(code) is not str and code is not None:
                    code = "delivery_state_unknown"
                reason = _READINESS_REASONS.get(code) if code is not None else None
                if reason is not None:
                    return self._block(context, current, reason, on_event)
        current = self._publish(
            context,
            current,
            kind="mission.started",
            stage="outreach",
            summary="Mission started.",
            state=MissionState.RUNNING,
            on_event=on_event,
        )

        def council_event(value: CouncilExecutionEvent) -> None:
            nonlocal current
            if type(value) is not CouncilExecutionEvent:
                raise MissionServiceUnavailable()
            participant_id = f"ai-{value.specialist_id.replace('_', '-')}"
            if participant_id not in {
                item.participant_id for item in current.participants
            }:
                raise MissionServiceUnavailable()
            suffix = {
                CouncilExecutionStatus.STARTED: ("started", "started analysis"),
                CouncilExecutionStatus.COMPLETED: ("completed", "completed analysis"),
                CouncilExecutionStatus.FAILED: ("failed", "stopped analysis"),
            }[value.status]
            current = self._publish(
                context,
                current,
                kind=f"council.specialist_{suffix[0]}",
                stage="analysis",
                summary=f"{value.display_name} {suffix[1]}.",
                participant_id=participant_id,
                on_event=on_event,
            )

        try:
            output = self._council.run(
                context,
                workspace,
                current.objective,
                cancellation=cancellation,
                on_event=council_event,
            )
        except Exception:  # noqa: BLE001 - model/provider details stay private
            return self._publish(
                context,
                current,
                kind="mission.failed",
                stage="analysis",
                summary="Mission stopped before the decision brief was ready.",
                state=MissionState.FAILED,
                on_event=on_event,
            )
        if type(output) is not CouncilRunOutput:
            raise MissionServiceUnavailable()
        recommendation = output.projection.recommendation_summary
        if type(recommendation) is not str or not recommendation:
            recommendation = "Council recommendation is ready."
        current = self._publish(
            context,
            current,
            kind="council.completed",
            stage="synthesis",
            summary=recommendation[:240],
            on_event=on_event,
        )
        if current.mode is MissionMode.DEMO_RUN:
            for item in current.participants:
                if (
                    item.actor_type is MissionActorType.DEMO_STAKEHOLDER
                    and item.response_required
                ):
                    current = self._publish(
                        context,
                        current,
                        kind="stakeholder.response_recorded",
                        stage="evidence",
                        summary=_demo_contribution(item),
                        participant_id=item.participant_id,
                        on_event=on_event,
                    )
            return self._publish(
                context,
                current,
                kind="decision_brief.ready",
                stage="decision",
                summary="Decision brief ready.",
                state=MissionState.COMPLETE,
                on_event=on_event,
            )
        for participant in required:
            try:
                outcome = self._dispatcher.dispatch(context, current, participant)
            except Exception:  # noqa: BLE001 - provider details stay private
                outcome = None
            if type(outcome) is not MissionDispatchOutcome or outcome.code != "delivered":
                reason = (
                    _READINESS_REASONS.get(outcome.code)
                    if type(outcome) is MissionDispatchOutcome
                    else None
                ) or MissionBlockedReason.DELIVERY_STATE_UNKNOWN
                return self._block(context, current, reason, on_event)
            current = self._publish(
                context,
                current,
                kind="outreach.sent",
                stage="outreach",
                summary="Outreach sent through a consented route.",
                participant_id=participant.participant_id,
                occurred_at=outcome.occurred_at,
                on_event=on_event,
            )
        proposed = current.model_copy(update={"state": MissionState.AWAITING_RESPONSE})
        try:
            return self._repository.update(
                context,
                proposed,
                expected_version=current.version,
            )
        except Exception:  # noqa: BLE001 - repository details stay private
            raise MissionServiceUnavailable() from None

    def record_response(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        response: MissionInboundResponse,
    ) -> MissionSnapshot:
        if type(response) is not MissionInboundResponse:
            raise MissionServiceUnavailable()
        current = self._load_bound(context, workspace, response.mission_id)
        if (
            current.mode is not MissionMode.CONNECTED_ORGANIZATION
            or current.state is not MissionState.AWAITING_RESPONSE
        ):
            raise MissionServiceUnavailable()
        participant = next(
            (
                item
                for item in current.participants
                if item.participant_id == response.participant_id
            ),
            None,
        )
        sent = any(
            item.kind == "outreach.sent"
            and item.participant_id == response.participant_id
            for item in current.events
        )
        answered = any(
            item.kind == "response.recorded"
            and item.participant_id == response.participant_id
            for item in current.events
        )
        if (
            participant is None
            or participant.actor_type is not MissionActorType.HUMAN_MEMBER
            or not participant.response_required
            or not sent
            or answered
        ):
            raise MissionServiceUnavailable()
        current = self._publish(
            context,
            current,
            kind="response.recorded",
            stage="evidence",
            summary=response.safe_summary,
            participant_id=response.participant_id,
            occurred_at=response.received_at,
        )
        required_ids = {
            item.participant_id
            for item in current.participants
            if item.actor_type is MissionActorType.HUMAN_MEMBER
            and item.response_required
        }
        answered_ids = {
            item.participant_id
            for item in current.events
            if item.kind == "response.recorded" and item.participant_id is not None
        }
        if required_ids <= answered_ids:
            return self._publish(
                context,
                current,
                kind="decision_brief.ready",
                stage="decision",
                summary="Decision brief ready with the organization response.",
                state=MissionState.COMPLETE,
            )
        return current


__all__ = ["MissionService", "MissionServiceUnavailable"]
