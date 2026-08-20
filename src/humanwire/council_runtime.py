"""Tenant-bound execution and durable projection storage for DecisionOS councils."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from humanwire.council_gateway import CouncilGateway, CouncilGatewayResult
from humanwire.council_models import CouncilRunRequest
from humanwire.council_projection import CouncilProjection, build_council_projection
from humanwire.council_tools import (
    CouncilEvidenceRecord,
    CouncilPriorDecision,
    CouncilToolContext,
    list_evidence,
)
from humanwire.decisionos_models import DecisionOSContext, DecisionWorkspace
from humanwire.decisionos_store import DecisionOSPermission, require_permission
from humanwire.google_council import (
    CouncilExecutionEvent,
    CouncilExecutionFailure,
    GoogleCouncilRunner,
)


class CouncilRuntimeUnavailable(RuntimeError):
    def __init__(self, code: str = "council_unavailable") -> None:
        super().__init__(code)


class _RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CouncilRunOutput(_RuntimeModel):
    run_id: str
    projection: CouncilProjection
    gateway: CouncilGatewayResult


class CouncilRunStore(Protocol):
    def start(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        run_id: str,
        objective: str,
        at: datetime,
    ) -> None: ...

    def append_event(
        self,
        context: DecisionOSContext,
        workspace_id: str,
        run_id: str,
        event: CouncilExecutionEvent,
    ) -> None: ...

    def finish(
        self,
        context: DecisionOSContext,
        workspace_id: str,
        output: CouncilRunOutput,
        at: datetime,
    ) -> None: ...

    def fail(
        self,
        context: DecisionOSContext,
        workspace_id: str,
        run_id: str,
        events: tuple[CouncilExecutionEvent, ...],
        at: datetime,
    ) -> None: ...

    def load_latest(
        self,
        context: DecisionOSContext,
        workspace_id: str,
    ) -> CouncilProjection | None: ...


class InMemoryCouncilRunStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[tuple[str, str, str], dict[str, object]] = {}
        self._latest: dict[tuple[str, str], str] = {}

    def start(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        run_id: str,
        objective: str,
        at: datetime,
    ) -> None:
        key = (context.organization_id, workspace.workspace_id, run_id)
        with self._lock:
            if key in self._runs:
                raise CouncilRuntimeUnavailable()
            active_id = self._latest.get(key[:2])
            active = (
                None
                if active_id is None
                else self._runs.get((key[0], key[1], active_id))
            )
            if active is not None and active.get("state") == "running":
                raise CouncilRuntimeUnavailable("council_active")
            self._runs[key] = {
                "objective": objective,
                "at": at,
                "events": [],
                "projection": None,
                "state": "running",
            }
            self._latest[key[:2]] = run_id

    def append_event(
        self,
        context: DecisionOSContext,
        workspace_id: str,
        run_id: str,
        event: CouncilExecutionEvent,
    ) -> None:
        key = (context.organization_id, workspace_id, run_id)
        with self._lock:
            row = self._runs.get(key)
            if row is None:
                raise CouncilRuntimeUnavailable()
            events = row["events"]
            if type(events) is not list:
                raise CouncilRuntimeUnavailable()
            events.append(event)

    def finish(
        self,
        context: DecisionOSContext,
        workspace_id: str,
        output: CouncilRunOutput,
        at: datetime,
    ) -> None:
        del at
        key = (context.organization_id, workspace_id, output.run_id)
        with self._lock:
            row = self._runs.get(key)
            if row is None:
                raise CouncilRuntimeUnavailable()
            row["projection"] = output.projection
            row["state"] = output.projection.state

    def fail(
        self,
        context: DecisionOSContext,
        workspace_id: str,
        run_id: str,
        events: tuple[CouncilExecutionEvent, ...],
        at: datetime,
    ) -> None:
        del at
        key = (context.organization_id, workspace_id, run_id)
        with self._lock:
            row = self._runs.get(key)
            if row is None:
                return
            row["projection"] = build_council_projection(
                run_id=run_id,
                objective=str(row["objective"]),
                events=events,
                failed=True,
            )
            row["state"] = "failed"

    def load_latest(
        self,
        context: DecisionOSContext,
        workspace_id: str,
    ) -> CouncilProjection | None:
        with self._lock:
            run_id = self._latest.get((context.organization_id, workspace_id))
            row = (
                None
                if run_id is None
                else self._runs.get((context.organization_id, workspace_id, run_id))
            )
            projection = None if row is None else row.get("projection")
            return projection if type(projection) is CouncilProjection else None


class FirestoreCouncilRunStore:
    """Private nested Firestore storage; only safe projections are read back."""

    def __init__(self, client: object) -> None:
        if not callable(getattr(client, "collection", None)):
            raise TypeError("Firestore client is invalid")
        self._client = client

    def _workspace_ref(self, organization_id: str, workspace_id: str):
        return (
            self._client.collection("decisionos_organizations")
            .document(organization_id)
            .collection("workspaces")
            .document(workspace_id)
        )

    def _run_ref(self, organization_id: str, workspace_id: str, run_id: str):
        return self._workspace_ref(organization_id, workspace_id).collection(
            "council_runs"
        ).document(run_id)

    def start(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        run_id: str,
        objective: str,
        at: datetime,
    ) -> None:
        run_ref = self._run_ref(context.organization_id, workspace.workspace_id, run_id)
        latest_ref = self._workspace_ref(
            context.organization_id, workspace.workspace_id
        ).collection("council_state").document("latest")
        transaction = self._client.transaction()

        def publish(transaction):
            if run_ref.get(transaction=transaction).exists:
                raise CouncilRuntimeUnavailable()
            latest = latest_ref.get(transaction=transaction)
            if latest.exists:
                latest_row = latest.to_dict()
                active_id = (
                    latest_row.get("run_id") if type(latest_row) is dict else None
                )
                if type(active_id) is not str:
                    raise CouncilRuntimeUnavailable()
                active = self._run_ref(
                    context.organization_id, workspace.workspace_id, active_id
                ).get(transaction=transaction)
                active_row = active.to_dict() if active.exists else None
                active_state = (
                    active_row.get("state") if type(active_row) is dict else None
                )
                active_started = (
                    active_row.get("started_at") if type(active_row) is dict else None
                )
                if (
                    active_state == "running"
                    and isinstance(active_started, datetime)
                    and active_started >= at - timedelta(minutes=10)
                ):
                    raise CouncilRuntimeUnavailable("council_active")
            transaction.create(
                run_ref,
                {
                    "schema_version": 1,
                    "organization_id": context.organization_id,
                    "workspace_id": workspace.workspace_id,
                    "run_id": run_id,
                    "objective": objective,
                    "state": "running",
                    "started_at": at,
                },
            )
            transaction.set(
                latest_ref,
                {
                    "schema_version": 1,
                    "organization_id": context.organization_id,
                    "workspace_id": workspace.workspace_id,
                    "run_id": run_id,
                    "state": "running",
                    "started_at": at,
                },
            )

        from google.cloud import firestore

        firestore.transactional(publish)(transaction)

    def append_event(
        self,
        context: DecisionOSContext,
        workspace_id: str,
        run_id: str,
        event: CouncilExecutionEvent,
    ) -> None:
        self._run_ref(context.organization_id, workspace_id, run_id).collection(
            "events"
        ).document(f"{event.ordinal:03d}").create(event.model_dump(mode="json"))

    def finish(
        self,
        context: DecisionOSContext,
        workspace_id: str,
        output: CouncilRunOutput,
        at: datetime,
    ) -> None:
        self._run_ref(context.organization_id, workspace_id, output.run_id).update(
            {
                "state": output.projection.state,
                "finished_at": at,
                "projection": output.projection.model_dump(mode="json"),
                "gateway": output.gateway.model_dump(mode="json"),
            }
        )
        self._workspace_ref(context.organization_id, workspace_id).collection(
            "council_state"
        ).document("latest").update({"state": output.projection.state})

    def fail(
        self,
        context: DecisionOSContext,
        workspace_id: str,
        run_id: str,
        events: tuple[CouncilExecutionEvent, ...],
        at: datetime,
    ) -> None:
        ref = self._run_ref(context.organization_id, workspace_id, run_id)
        snapshot = ref.get()
        if not snapshot.exists:
            return
        row = snapshot.to_dict()
        objective = row.get("objective") if type(row) is dict else None
        if type(objective) is not str:
            return
        projection = build_council_projection(
            run_id=run_id,
            objective=objective,
            events=events,
            failed=True,
        )
        ref.update(
            {
                "state": "failed",
                "finished_at": at,
                "projection": projection.model_dump(mode="json"),
            }
        )
        self._workspace_ref(context.organization_id, workspace_id).collection(
            "council_state"
        ).document("latest").update({"state": "failed"})

    def load_latest(
        self,
        context: DecisionOSContext,
        workspace_id: str,
    ) -> CouncilProjection | None:
        latest_ref = self._workspace_ref(
            context.organization_id, workspace_id
        ).collection("council_state").document("latest")
        latest = latest_ref.get()
        if not latest.exists:
            return None
        latest_row = latest.to_dict()
        run_id = latest_row.get("run_id") if type(latest_row) is dict else None
        if type(run_id) is not str:
            raise CouncilRuntimeUnavailable()
        run = self._run_ref(context.organization_id, workspace_id, run_id).get()
        row = run.to_dict() if run.exists else None
        projection = row.get("projection") if type(row) is dict else None
        if type(projection) is not dict:
            return None
        try:
            return CouncilProjection.model_validate(projection)
        except Exception:  # noqa: BLE001 - corrupt private state fails closed
            raise CouncilRuntimeUnavailable() from None


class FirestoreCouncilEvidenceRegistry:
    """Read only the sanitized evidence projection for one tenant workspace."""

    def __init__(self, client: object) -> None:
        self._client = client

    def _collection(self, organization_id: str, workspace_id: str):
        return (
            self._client.collection("decisionos_organizations")
            .document(organization_id)
            .collection("workspaces")
            .document(workspace_id)
            .collection("evidence")
        )

    def list_evidence(
        self, organization_id: str, workspace_id: str
    ) -> tuple[CouncilEvidenceRecord, ...]:
        rows = self._collection(organization_id, workspace_id).limit(100).stream()
        return tuple(CouncilEvidenceRecord.model_validate(row.to_dict()) for row in rows)

    def load_evidence(
        self, organization_id: str, workspace_id: str, evidence_id: str
    ) -> CouncilEvidenceRecord | None:
        row = self._collection(organization_id, workspace_id).document(evidence_id).get()
        return CouncilEvidenceRecord.model_validate(row.to_dict()) if row.exists else None

    def load_prior_decision(
        self, organization_id: str, workspace_id: str, decision_id: str
    ) -> CouncilPriorDecision | None:
        row = (
            self._client.collection("decisionos_organizations")
            .document(organization_id)
            .collection("workspaces")
            .document(workspace_id)
            .collection("decisions")
            .document(decision_id)
            .get()
        )
        return CouncilPriorDecision.model_validate(row.to_dict()) if row.exists else None


RunnerFactory = Callable[[CouncilToolContext], GoogleCouncilRunner]


class DecisionOSCouncilRuntime:
    def __init__(
        self,
        *,
        store: CouncilRunStore,
        evidence_registry: object,
        model_identifier: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        runner_factory: RunnerFactory | None = None,
        timeout_seconds: float = 180.0,
    ) -> None:
        if not 10 <= timeout_seconds <= 300:
            raise ValueError("council timeout is invalid")
        self._store = store
        self._evidence_registry = evidence_registry
        self._model_identifier = model_identifier
        self._clock = clock
        self._runner_factory = runner_factory
        self._timeout_seconds = timeout_seconds

    def run(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        objective: str,
        *,
        cancellation: threading.Event,
        on_event: Callable[[CouncilExecutionEvent], None] | None = None,
    ) -> CouncilRunOutput:
        canonical_context = DecisionOSContext.model_validate(context)
        canonical_workspace = DecisionWorkspace.model_validate(workspace)
        require_permission(canonical_context, DecisionOSPermission.CONTRIBUTE)
        if canonical_workspace.organization_id != canonical_context.organization_id:
            raise CouncilRuntimeUnavailable()
        run_id = f"council_run_{secrets.token_hex(12)}"
        started = self._clock()
        self._store.start(
            canonical_context, canonical_workspace, run_id, objective, started
        )
        tool_context = CouncilToolContext(
            context=canonical_context,
            workspace_id=canonical_workspace.workspace_id,
            registry=self._evidence_registry,
        )
        catalog = list_evidence(tool_context)
        evidence_ids = tuple(item.evidence_id for item in catalog.items)
        request = CouncilRunRequest(
            context=canonical_context,
            workspace_id=canonical_workspace.workspace_id,
            decision_id=f"decision_{run_id.removeprefix('council_run_')}",
            playbook_id=canonical_workspace.playbook,
            objective=objective,
            evidence_ids=evidence_ids,
            policy_version="council-v1",
        )
        events: list[CouncilExecutionEvent] = []

        def publish(event: CouncilExecutionEvent) -> None:
            events.append(event)
            self._store.append_event(
                canonical_context, canonical_workspace.workspace_id, run_id, event
            )
            if on_event is not None:
                on_event(event)

        runner = (
            self._runner_factory(tool_context)
            if self._runner_factory is not None
            else GoogleCouncilRunner(
                model_identifier=self._model_identifier,
                tool_context=tool_context,
            )
        )
        try:
            result = runner.run(
                request,
                deadline=time.monotonic() + self._timeout_seconds,
                cancellation=cancellation,
                on_event=publish,
            )
        except CouncilExecutionFailure:
            self._store.fail(
                canonical_context,
                canonical_workspace.workspace_id,
                run_id,
                tuple(events),
                self._clock(),
            )
            raise CouncilRuntimeUnavailable() from None
        gateway = CouncilGateway(nonce_factory=lambda: secrets.token_urlsafe(24)).evaluate(
            result.recommendation,
            confirmed_evidence_ids=evidence_ids,
        )
        projection = build_council_projection(
            run_id=run_id,
            objective=objective,
            result=result,
        )
        if not gateway.accepted:
            projection = projection.model_copy(
                update={
                    "state": "blocked",
                    "required_human_action": (
                        "Resolve the evidence or challenge gate before approval."
                    ),
                }
            )
        output = CouncilRunOutput(
            run_id=run_id,
            projection=projection,
            gateway=gateway,
        )
        self._store.finish(
            canonical_context,
            canonical_workspace.workspace_id,
            output,
            self._clock(),
        )
        return output

    def load_latest(
        self,
        context: DecisionOSContext,
        workspace_id: str,
    ) -> CouncilProjection | None:
        canonical = DecisionOSContext.model_validate(context)
        require_permission(canonical, DecisionOSPermission.READ_WORKSPACE)
        return self._store.load_latest(canonical, workspace_id)
