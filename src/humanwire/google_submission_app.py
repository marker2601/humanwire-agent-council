"""Hardened durable public application for the Google Cloud HumanWire runtime."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from humanwire.cloud_contracts import RunDispatcher, RunRepository
from humanwire.cloud_dispatch import DispatchUnavailable
from humanwire.cloud_observability import CloudLogEvent, log_cloud_event
from humanwire.cloud_progress import bound_cloud_exports
from humanwire.cloud_store import (
    CloudActiveRunError,
    CloudDivergenceError,
    CloudRunState,
    CloudStoreError,
    CloudUnknownRunError,
)
from humanwire.studio_app import (
    _MAX_BODY_BYTES,
    _SAFE_HEADERS,
    _ascii_header,
    _coordination_request,
    _fixed_error,
    _raw_headers,
)
from humanwire.studio_models import StudioAgentMode, product_catalog
from humanwire.studio_projection import validate_product_safe_request

_PACKAGE_DIR = Path(__file__).resolve().parent
_PUBLIC_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_RUN_PATH = b"/api/runs"
logger = logging.getLogger("humanwire.cloud.web")


def _normalized_hosts(values: Iterable[str]) -> frozenset[str]:
    hosts: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise TypeError("Google submission hosts must be strings")
        host = value.strip().casefold()
        if _PUBLIC_HOST.fullmatch(host) is None or ".." in host:
            raise ValueError("Google submission host is invalid")
        hosts.add(host)
    if not hosts:
        raise ValueError("at least one Google submission host is required")
    return frozenset(hosts)


def _approved_host(value: str | None, allowed_hosts: frozenset[str]) -> bool:
    return value is not None and value.casefold() in allowed_hosts


def _approved_origin(
    value: str | None,
    request_host: str | None,
    allowed_hosts: frozenset[str],
) -> bool:
    if (
        value is None
        or request_host is None
        or not _approved_host(request_host, allowed_hosts)
    ):
        return False
    return hmac.compare_digest(value.casefold(), f"https://{request_host.casefold()}")


def _etag(serialized: str) -> str:
    return '"' + hashlib.sha256(serialized.encode("utf-8")).hexdigest() + '"'


def _attachment_name(run_alias: str, suffix: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", run_alias) is None:
        return f"humanwire-coordination-{suffix}"
    return f"{run_alias}-{suffix}"


def create_google_submission_app(
    repository: RunRepository,
    dispatcher: RunDispatcher,
    *,
    action_token: str,
    allowed_hosts: Iterable[str],
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    """Build the public durable polling surface without a local run manager."""
    if not isinstance(action_token, str) or not action_token:
        raise ValueError("Google submission action token must be non-empty")
    approved_hosts = _normalized_hosts(allowed_hosts)
    now = clock or (lambda: datetime.now(UTC))
    templates = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.repository = repository
    app.state.dispatcher = dispatcher
    app.state.service_role = "web"
    app.state.requires_platform_authentication = False
    app.state.runtime_credentials_allowed = False
    app.mount(
        "/studio-static",
        StaticFiles(directory=str(_PACKAGE_DIR / "studio_static")),
        name="studio-static",
    )

    @app.exception_handler(StarletteHTTPException)
    async def fixed_http_error(
        _request: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        if error.status_code == 404:
            return _fixed_error(404, "not_found")
        if error.status_code == 405:
            return _fixed_error(405, "method_not_allowed")
        return _fixed_error(error.status_code, "request_failed")

    @app.middleware("http")
    async def google_submission_boundary(request: Request, call_next):
        try:
            host = _ascii_header(_raw_headers(request, b"host"))
            raw_path = request.scope.get("raw_path")
            if not _approved_host(host, approved_hosts):
                response = _fixed_error(400, "invalid_host")
            elif request.method not in {"GET", "HEAD", "POST"} or request.method == "POST" and (
                raw_path != _RUN_PATH
                or request.scope.get("path") != "/api/runs"
                or bool(request.scope.get("query_string"))
            ):
                response = _fixed_error(405, "method_not_allowed")
            elif request.method != "POST":
                response = await call_next(request)
            else:
                length_text = _ascii_header(_raw_headers(request, b"content-length"))
                if (
                    length_text is None
                    or not length_text.isdecimal()
                    or len(length_text) > 4
                    or int(length_text) < 1
                ):
                    response = _fixed_error(400, "invalid_request")
                elif int(length_text) > _MAX_BODY_BYTES:
                    response = _fixed_error(413, "request_too_large")
                elif _raw_headers(request, b"transfer-encoding") or _raw_headers(
                    request,
                    b"content-encoding",
                ):
                    response = _fixed_error(400, "invalid_request")
                else:
                    action = _ascii_header(_raw_headers(request, b"x-humanwire-action"))
                    origin = _ascii_header(_raw_headers(request, b"origin"))
                    content_types = _raw_headers(request, b"content-type")
                    content_type = _ascii_header(content_types)
                    if action is None or not hmac.compare_digest(action, action_token):
                        response = _fixed_error(403, "action_forbidden")
                    elif not _approved_origin(origin, host, approved_hosts):
                        response = _fixed_error(403, "origin_forbidden")
                    elif len(content_types) != 1:
                        response = _fixed_error(400, "invalid_request")
                    elif content_type is None or content_type.split(";", 1)[0].strip().casefold() != (
                        "application/json"
                    ):
                        response = _fixed_error(415, "unsupported_media_type")
                    else:
                        response = await call_next(request)
        except Exception:  # noqa: BLE001 - public failures must not retain private details
            response = _fixed_error(500, "request_failed")
        for name, value in _SAFE_HEADERS.items():
            response.headers[name] = value
        return response

    def studio_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request=request,
            name="coordination_studio.html",
            context={"action_token": action_token, "delivery_mode": "cloud"},
        )

    @app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def home(request: Request) -> Response:
        return studio_page(request)

    @app.api_route(
        "/runs/{run_alias}",
        methods=["GET", "HEAD"],
        response_class=HTMLResponse,
    )
    def workspace(request: Request, run_alias: str) -> Response:
        try:
            repository.load_metadata(run_alias)
        except (CloudUnknownRunError, ValueError):
            return _fixed_error(404, "run_not_found")
        except Exception:  # noqa: BLE001 - repository details remain private
            return _fixed_error(500, "run_unavailable")
        return studio_page(request)

    @app.api_route("/api/catalog", methods=["GET", "HEAD"])
    def catalog() -> JSONResponse:
        return JSONResponse(content=product_catalog().model_dump(mode="json"))

    @app.api_route("/healthz", methods=["GET", "HEAD"])
    def health() -> JSONResponse:
        return JSONResponse(content={"service_role": "web", "status": "ok"})

    @app.post("/api/runs")
    async def create_run(request: Request) -> Response:
        length_text = _ascii_header(_raw_headers(request, b"content-length"))
        if length_text is None:
            return _fixed_error(400, "invalid_request")
        body = await request.body()
        if len(body) != int(length_text) or not 1 <= len(body) <= _MAX_BODY_BYTES:
            return _fixed_error(400, "invalid_request")
        coordination = _coordination_request(body)
        if coordination is None:
            return _fixed_error(400, "invalid_request")
        if coordination.agent_mode is not StudioAgentMode.GOOGLE_ADK:
            return _fixed_error(409, "google_runtime_required")
        try:
            coordination = validate_product_safe_request(coordination)
        except ValueError:
            return _fixed_error(400, "invalid_request")
        try:
            created = repository.create_run(coordination, now=now())
        except CloudActiveRunError:
            return _fixed_error(409, "active_run")
        except CloudStoreError:
            return _fixed_error(500, "run_unavailable")
        except Exception:  # noqa: BLE001 - repository details remain private
            return _fixed_error(500, "run_unavailable")
        try:
            dispatcher.dispatch(created.run_alias, created.idempotency_key)
        except DispatchUnavailable:
            try:
                repository.fail_queued_dispatch(
                    created.run_alias,
                    created.idempotency_key,
                    now=now(),
                )
            except Exception:  # noqa: BLE001 - cleanup details remain private
                return _fixed_error(500, "run_unavailable")
            return _fixed_error(503, "dispatch_unavailable")
        except Exception:  # noqa: BLE001 - dispatcher details remain private
            try:
                repository.fail_queued_dispatch(
                    created.run_alias,
                    created.idempotency_key,
                    now=now(),
                )
            except Exception:  # noqa: BLE001 - cleanup details remain private
                return _fixed_error(500, "run_unavailable")
            return _fixed_error(503, "dispatch_unavailable")
        log_cloud_event(
            CloudLogEvent.RUN_QUEUED,
            state="queued",
            service_role="web",
            logger=logger,
        )
        return JSONResponse(
            status_code=202,
            content={
                "run_alias": created.run_alias,
                "workspace_url": f"/runs/{created.run_alias}",
                "state": CloudRunState.QUEUED.value,
            },
        )

    @app.api_route("/api/runs/{run_alias}", methods=["GET", "HEAD"])
    def snapshot(request: Request, run_alias: str) -> Response:
        try:
            current = repository.load_snapshot(run_alias)
        except (CloudUnknownRunError, ValueError):
            return _fixed_error(404, "run_not_found")
        except Exception:  # noqa: BLE001 - repository details remain private
            return _fixed_error(500, "run_unavailable")
        serialized = current.model_dump_json()
        etag = _etag(serialized)
        response_headers = {
            "ETag": etag,
            "X-HumanWire-Saved-Ordinal": str(
                max(
                    (item.persisted_ordinal or 0 for item in current.events),
                    default=0,
                )
            ),
        }
        supplied = _ascii_header(_raw_headers(request, b"if-none-match"))
        if supplied is not None and hmac.compare_digest(supplied, etag):
            return Response(status_code=304, headers=response_headers)
        return Response(
            content=serialized,
            media_type="application/json",
            headers=response_headers,
        )

    def export(run_alias: str, *, csv: bool) -> Response:
        try:
            artifacts = bound_cloud_exports(repository, run_alias)
        except CloudUnknownRunError:
            return _fixed_error(404, "run_not_found")
        except CloudDivergenceError:
            return _fixed_error(409, "exports_not_ready")
        except ValueError:
            return _fixed_error(404, "run_not_found")
        except Exception:  # noqa: BLE001 - repository details remain private
            return _fixed_error(500, "run_unavailable")
        if csv:
            content = artifacts.csv_bytes
            suffix = "evidence.csv"
            media_type = "text/csv; charset=utf-8"
        else:
            content = artifacts.json_bytes
            suffix = "evidence.json"
            media_type = "application/json"
        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{_attachment_name(run_alias, suffix)}"'
                )
            },
        )

    @app.api_route(
        "/api/runs/{run_alias}/evidence.json",
        methods=["GET", "HEAD"],
    )
    def evidence(run_alias: str) -> Response:
        return export(run_alias, csv=False)

    @app.api_route(
        "/api/runs/{run_alias}/evidence.csv",
        methods=["GET", "HEAD"],
    )
    def events(run_alias: str) -> Response:
        return export(run_alias, csv=True)

    return app
