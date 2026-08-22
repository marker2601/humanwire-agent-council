"""Private Pub/Sub worker ingress and claimed HumanWire cloud execution."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol, Self

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from humanwire.cloud_dispatch import RunDispatchMessage
from humanwire.cloud_observability import CloudLogEvent, log_cloud_event
from humanwire.cloud_progress import CloudProgressPublisher
from humanwire.cloud_store import (
    CloudClaimStatus,
    CloudDivergenceError,
    CloudExpiredClaimError,
    CloudRunMetadata,
    CloudStoreError,
    CloudTimelineRecord,
    CloudUnknownRunError,
)
from humanwire.studio_app import _SAFE_HEADERS, _ascii_header, _raw_headers
from humanwire.studio_projection import (
    StudioDataPoint,
    StudioLifecycle,
    StudioOutcome,
    StudioTimelineEvent,
    StudioTransition,
    StudioWorkspaceSnapshot,
    create_studio_progress,
)
from humanwire.synthetic import build_coordination_scenario, generate_scenario

_WORKER_PATH = b"/internal/pubsub/runs"
_HEALTH_PATH = b"/healthz"
_MAX_PUSH_BYTES = 8192
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_SUBSCRIPTION = re.compile(
    r"^projects/[a-z][a-z0-9-]{4,62}/subscriptions/[A-Za-z][A-Za-z0-9._~-]{2,254}$"
)
_MESSAGE_ID = r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,255}$"
logger = logging.getLogger("humanwire.cloud.worker")


class _RunRepository(Protocol):
    def claim_run(self, *args, **kwargs): ...

    def renew_claim(self, *args, **kwargs) -> bool: ...

    def load_metadata(self, run_alias: str) -> CloudRunMetadata: ...

    def load_snapshot(self, run_alias: str) -> StudioWorkspaceSnapshot: ...

    def append_timeline(self, *args, **kwargs) -> bool: ...

    def finish_run(self, *args, **kwargs) -> bool: ...


class WorkerDisposition(StrEnum):
    ACCEPTED = "accepted"
    CONFLICT = "conflict"
    INVALID = "invalid"
    RETRY = "retry"


class _PushModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        populate_by_name=True,
    )


class PubSubPushMessage(_PushModel):
    data: str = Field(min_length=4, max_length=4096)
    message_id: str = Field(alias="messageId", pattern=_MESSAGE_ID)
    publish_time: datetime | None = Field(default=None, alias="publishTime")
    attributes: dict[str, str] = Field(default_factory=dict)
    ordering_key: str | None = Field(default=None, alias="orderingKey", max_length=256)

    @model_validator(mode="after")
    def has_bounded_safe_attributes(self) -> Self:
        if len(self.attributes) > 16 or any(
            not 1 <= len(key) <= 64 or len(value) > 256
            for key, value in self.attributes.items()
        ):
            raise ValueError("Pub/Sub attributes are invalid")
        return self


class PubSubPushEnvelope(_PushModel):
    message: PubSubPushMessage
    subscription: str = Field(pattern=_SUBSCRIPTION.pattern)


def _rejecting_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def decode_pubsub_push(body: bytes) -> RunDispatchMessage:
    """Fail closed on every layer of the Pub/Sub push envelope."""
    invalid = not isinstance(body, bytes) or not 2 <= len(body) <= _MAX_PUSH_BYTES
    dispatch: RunDispatchMessage | None = None
    if not invalid:
        try:
            decoded = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=_rejecting_object,
                parse_constant=_reject_constant,
            )
            envelope = PubSubPushEnvelope.model_validate_json(
                json.dumps(decoded, separators=(",", ":"))
            )
            payload = base64.b64decode(envelope.message.data.encode("ascii"), validate=True)
            dispatch = RunDispatchMessage.from_bytes(payload)
        except (UnicodeError, ValueError, TypeError, ValidationError):
            invalid = True
    if invalid or dispatch is None:
        raise ValueError("invalid_envelope") from None
    return dispatch


def _safe_seed(run_alias: str) -> int:
    return int.from_bytes(hashlib.sha256(run_alias.encode("ascii")).digest()[:4], "big") & (
        2**31 - 1
    )


def _failed_snapshot(snapshot: StudioWorkspaceSnapshot) -> StudioWorkspaceSnapshot:
    failed = snapshot.model_copy(
        update={
            "run_state": "failed",
            "downloads_ready": False,
            "outcome": StudioOutcome(
                state="failed",
                headline="Coordination stopped",
                summary="The saved workspace remains available for review.",
            ),
        }
    )
    failed._final_trace_sha256 = None
    failed._transcript_sha256 = None
    return failed


def _recovery_record(metadata: CloudRunMetadata, now: datetime) -> CloudTimelineRecord:
    ordinal = metadata.timeline_count + 1
    transition = StudioTransition(
        source="humanwire",
        destination="caspian-gateway",
        source_label="HumanWire",
        destination_label="Caspian Gateway",
        generated_label="Worker recovery started",
    )
    event = StudioTimelineEvent(
        timeline_ordinal=ordinal,
        persisted_ordinal=None,
        created_at=now,
        stage=metadata.lifecycle_stage,
        effect="inert",
        active_transition=transition,
        live_copy="Worker recovery started.",
    )
    stages = tuple(type(metadata.lifecycle_stage))
    return CloudTimelineRecord.create(
        event=event,
        conversations=(),
        data_point=StudioDataPoint(
            event_ordinal=ordinal,
            label="Worker recovery started",
            summary="No state change",
            effect="inert",
        ),
        lifecycle=StudioLifecycle(
            current=metadata.lifecycle_stage,
            stages=stages,
            completed=stages[: stages.index(metadata.lifecycle_stage)],
        ),
    )


class CloudRunWorker:
    """Claim, execute, clean up, and only then bind one cloud run."""

    def __init__(
        self,
        repository: _RunRepository,
        *,
        decision_factory_builder: Callable[[], object],
        runner: Callable[..., object] = generate_scenario,
        claim_owner_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        lease_seconds: int = 300,
        heartbeat_seconds: float = 30,
    ) -> None:
        if not 30 <= lease_seconds <= 900:
            raise ValueError("worker lease must be between 30 and 900 seconds")
        if not 0.1 <= heartbeat_seconds < lease_seconds:
            raise ValueError("worker heartbeat must be positive and shorter than its lease")
        self._repository = repository
        self._decision_factory_builder = decision_factory_builder
        self._runner = runner
        self._claim_owner_factory = claim_owner_factory or (
            lambda: "worker-" + secrets.token_hex(16)
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds

    def handle(self, dispatch: RunDispatchMessage) -> WorkerDisposition:
        dispatch = RunDispatchMessage.model_validate(dispatch)
        owner = self._claim_owner_factory()
        try:
            claim = self._repository.claim_run(
                dispatch.run_alias,
                dispatch.idempotency_key,
                owner,
                now=self._clock(),
                lease_seconds=self._lease_seconds,
            )
        except CloudExpiredClaimError:
            try:
                metadata = self._repository.load_metadata(dispatch.run_alias)
                now = self._clock()
                claim = self._repository.claim_run(
                    dispatch.run_alias,
                    dispatch.idempotency_key,
                    owner,
                    now=now,
                    lease_seconds=self._lease_seconds,
                    recovery_record=_recovery_record(metadata, now),
                )
            except (CloudDivergenceError, CloudUnknownRunError):
                return WorkerDisposition.INVALID
            except Exception:  # noqa: BLE001 - storage recovery remains retry-only
                return WorkerDisposition.RETRY
        except (CloudDivergenceError, CloudUnknownRunError):
            return WorkerDisposition.INVALID
        except Exception:  # noqa: BLE001 - storage failures are fixed retry signals
            return WorkerDisposition.RETRY
        if claim.status in {CloudClaimStatus.DUPLICATE, CloudClaimStatus.TERMINAL}:
            return WorkerDisposition.ACCEPTED
        if claim.status is CloudClaimStatus.CONFLICT:
            return WorkerDisposition.CONFLICT
        if claim.status is CloudClaimStatus.RECOVERED:
            try:
                publisher = CloudProgressPublisher(
                    self._repository,
                    run_alias=dispatch.run_alias,
                    claim_owner=owner,
                    clock=self._clock,
                )
                publisher.bind_failure(
                    _failed_snapshot(self._repository.load_snapshot(dispatch.run_alias))
                )
            except Exception:  # noqa: BLE001 - recovery finalization is retry-only
                return WorkerDisposition.RETRY
            log_cloud_event(
                CloudLogEvent.RUN_RECOVERED,
                state="failed",
                service_role="worker",
                logger=logger,
            )
            return WorkerDisposition.ACCEPTED

        log_cloud_event(
            CloudLogEvent.RUN_CLAIMED,
            state="running",
            service_role="worker",
            logger=logger,
        )

        try:
            metadata = self._repository.load_metadata(dispatch.run_alias)
            scenario = build_coordination_scenario(
                metadata.request,
                seed=_safe_seed(dispatch.run_alias),
                scenario_id=dispatch.run_alias,
            )
            publisher = CloudProgressPublisher(
                self._repository,
                run_alias=dispatch.run_alias,
                claim_owner=owner,
                clock=self._clock,
            )
            store, observer = create_studio_progress(
                metadata.request,
                scenario,
                publisher=publisher,
            )
        except Exception:  # noqa: BLE001 - safe retry leaves the lease recoverable
            return WorkerDisposition.RETRY

        heartbeat_stop = threading.Event()
        claim_lost = threading.Event()

        def renew_claim() -> None:
            while not heartbeat_stop.wait(self._heartbeat_seconds):
                try:
                    renewed = self._repository.renew_claim(
                        dispatch.run_alias,
                        owner,
                        now=self._clock(),
                        lease_seconds=self._lease_seconds,
                    )
                except Exception:  # noqa: BLE001,S112 - retry-only heartbeat probe
                    # A transient storage collision does not disprove ownership; the
                    # durable lease and all later owner-checked writes remain authoritative.
                    continue
                if not renewed:
                    claim_lost.set()
                    return

        heartbeat = threading.Thread(
            target=renew_claim,
            name="humanwire-cloud-claim",
            daemon=False,
        )
        heartbeat.start()
        terminal_failure = False
        retryable_failure = False
        completed_snapshot: StudioWorkspaceSnapshot | None = None
        try:
            factory = self._decision_factory_builder()
            if getattr(factory, "model_identifier", None) != metadata.model_id:
                raise ValueError("worker model does not match its durable run")
            with TemporaryDirectory(prefix="humanwire-cloud-run-") as temporary:
                run_root = Path(temporary) / "run"
                self._runner(
                    scenario,
                    run_root / "transcript.json",
                    run_root,
                    decision_engine=factory,
                    max_decision_workers=4,
                    model_decision_timeout_seconds=60.0,
                    progress_observer=observer,
                    presentation_observer=observer,
                    mandate_request=metadata.request.objective,
                    include_change_story=False,
                    availability_date=metadata.target_date,
                    defer_authority_until_ready=True,
                    include_conflict=metadata.request.include_conflict,
                )
                completed_snapshot = store.snapshot()
                if completed_snapshot.run_state != "complete":
                    terminal_failure = True
        except ValueError:
            terminal_failure = True
        except Exception:  # noqa: BLE001 - unexpected execution details remain private
            retryable_failure = True
        finally:
            heartbeat_stop.set()
            heartbeat.join()

        if claim_lost.is_set() or retryable_failure:
            return WorkerDisposition.RETRY
        try:
            if terminal_failure or completed_snapshot is None:
                store.publish_failed()
                publisher.bind_failure(store.snapshot())
                terminal_event = CloudLogEvent.RUN_FAILED
                terminal_state = "failed"
            else:
                publisher.bind_completion(completed_snapshot)
                terminal_event = CloudLogEvent.RUN_COMPLETED
                terminal_state = "complete"
        except (CloudStoreError, ValueError):
            return WorkerDisposition.RETRY
        except Exception:  # noqa: BLE001 - provider/storage details remain fixed
            return WorkerDisposition.RETRY
        log_cloud_event(
            terminal_event,
            state=terminal_state,
            service_role="worker",
            logger=logger,
        )
        return WorkerDisposition.ACCEPTED


def _normalized_hosts(values: set[str] | frozenset[str]) -> frozenset[str]:
    hosts = set()
    for value in values:
        if not isinstance(value, str):
            raise TypeError("worker hosts must be strings")
        host = value.strip().casefold()
        if _HOST.fullmatch(host) is None or ".." in host:
            raise ValueError("worker host is invalid")
        hosts.add(host)
    if not hosts:
        raise ValueError("worker requires at least one host")
    return frozenset(hosts)


def _fixed(status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code})


def create_cloud_worker_app(
    worker: CloudRunWorker | Any,
    *,
    allowed_hosts: set[str] | frozenset[str],
) -> FastAPI:
    """Create the IAM-private worker route with a strict machine boundary."""
    hosts = _normalized_hosts(allowed_hosts)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.requires_platform_authentication = True
    app.state.service_role = "worker"
    app.state.browser_invocation_allowed = False

    @app.middleware("http")
    async def worker_boundary(request: Request, call_next):
        try:
            host = _ascii_header(_raw_headers(request, b"host"))
            raw_path = request.scope.get("raw_path")
            query = request.scope.get("query_string")
            if host is None or host.casefold() not in hosts:
                response = _fixed(400, "invalid_host")
            elif raw_path == _HEALTH_PATH and request.method in {"GET", "HEAD"}:
                response = (
                    _fixed(405, "method_not_allowed")
                    if query
                    else await call_next(request)
                )
            elif raw_path != _WORKER_PATH or request.method != "POST" or query:
                response = _fixed(405, "method_not_allowed")
            elif _raw_headers(request, b"origin"):
                response = _fixed(403, "origin_forbidden")
            else:
                length_text = _ascii_header(_raw_headers(request, b"content-length"))
                content_type = _ascii_header(_raw_headers(request, b"content-type"))
                if (
                    length_text is None
                    or not length_text.isdecimal()
                    or len(length_text) > 4
                    or not 1 <= int(length_text) <= _MAX_PUSH_BYTES
                    or _raw_headers(request, b"transfer-encoding")
                    or _raw_headers(request, b"content-encoding")
                ):
                    response = _fixed(400, "invalid_request")
                elif content_type is None or content_type.casefold() != "application/json":
                    response = _fixed(415, "unsupported_media_type")
                else:
                    response = await call_next(request)
        except Exception:  # noqa: BLE001 - all boundary errors are fixed and private
            response = _fixed(500, "request_failed")
        for name, value in _SAFE_HEADERS.items():
            response.headers[name] = value
        return response

    @app.api_route("/healthz", methods=["GET", "HEAD"])
    def health() -> JSONResponse:
        return JSONResponse(content={"service": "worker", "ready": True})

    @app.post("/internal/pubsub/runs")
    async def execute(request: Request) -> Response:
        length_text = _ascii_header(_raw_headers(request, b"content-length"))
        if length_text is None:
            return _fixed(400, "invalid_request")
        body = await request.body()
        if len(body) != int(length_text):
            return _fixed(400, "invalid_request")
        try:
            dispatch = decode_pubsub_push(body)
        except ValueError:
            return _fixed(400, "invalid_envelope")
        disposition = worker.handle(dispatch)
        if disposition is WorkerDisposition.ACCEPTED:
            return Response(status_code=204)
        if disposition is WorkerDisposition.CONFLICT:
            return _fixed(409, "claim_conflict")
        if disposition is WorkerDisposition.INVALID:
            return _fixed(400, "invalid_dispatch")
        return _fixed(503, "retry_later")

    return app
