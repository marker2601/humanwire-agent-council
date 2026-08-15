"""Public streaming product surface for the HumanWire submission."""

from __future__ import annotations

import asyncio
import hmac
import json
import re
from collections.abc import AsyncIterator, Iterable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from humanwire.studio_app import (
    _MAX_BODY_BYTES,
    _SAFE_HEADERS,
    _ascii_header,
    _coordination_request,
    _fixed_error,
    _raw_headers,
)
from humanwire.studio_exports import product_events_csv, product_evidence
from humanwire.studio_models import StudioAgentMode, product_catalog
from humanwire.studio_run import (
    ActiveRunError,
    ModelModeUnavailable,
    StudioRunManager,
)

_PACKAGE_DIR = Path(__file__).resolve().parent
_PUBLIC_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_RUN_PATH = b"/api/runs"
_RUN_JOIN_TIMEOUT_SECONDS = 10.0


def _normalized_hosts(values: Iterable[str]) -> frozenset[str]:
    hosts: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise TypeError("submission hosts must be strings")
        host = value.strip().casefold()
        if not _PUBLIC_HOST.fullmatch(host) or ".." in host:
            raise ValueError("submission host is invalid")
        hosts.add(host)
    if not hosts:
        raise ValueError("at least one submission host is required")
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


async def _run_stream(
    manager: StudioRunManager,
    run_alias: str,
    poll_interval_seconds: float,
) -> AsyncIterator[bytes]:
    last_snapshot = ""
    try:
        while True:
            snapshot = manager.snapshot(run_alias)
            serialized = snapshot.model_dump_json()
            terminal = snapshot.run_state in {"complete", "failed"}
            binding = None
            if terminal and snapshot.downloads_ready:
                binding = manager.final_binding(run_alias)
                if binding is None:
                    await asyncio.sleep(poll_interval_seconds)
                    continue
            if terminal:
                await asyncio.to_thread(
                    manager.join,
                    run_alias,
                    _RUN_JOIN_TIMEOUT_SECONDS,
                )
            if serialized != last_snapshot:
                envelope: dict[str, object] = {
                    "type": "snapshot",
                    "snapshot": json.loads(serialized),
                }
                if binding is not None:
                    evidence = product_evidence(binding)
                    envelope["evidence"] = evidence.model_dump(mode="json")
                    envelope["events_csv"] = product_events_csv(evidence)
                yield (json.dumps(envelope, separators=(",", ":")) + "\n").encode()
                last_snapshot = serialized
            if terminal:
                return
            await asyncio.sleep(poll_interval_seconds)
    except Exception:  # noqa: BLE001 - stream errors must remain fixed and private
        yield b'{"type":"error","error":"run_unavailable"}\n'


def create_submission_app(
    manager: StudioRunManager,
    *,
    action_token: str,
    allowed_hosts: Iterable[str],
    poll_interval_seconds: float = 0.05,
) -> FastAPI:
    """Build the public same-origin stream used by the submission product."""
    if not isinstance(action_token, str) or not action_token:
        raise ValueError("submission action token must be non-empty")
    if not 0.001 <= poll_interval_seconds <= 1:
        raise ValueError("submission poll interval must be between 0.001 and 1 second")
    approved_hosts = _normalized_hosts(allowed_hosts)
    templates = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.manager = manager
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
    async def public_submission_boundary(request: Request, call_next):
        try:
            host = _ascii_header(_raw_headers(request, b"host"))
            raw_path = request.scope.get("raw_path")
            is_run_post = raw_path == _RUN_PATH
            if not _approved_host(host, approved_hosts):
                response = _fixed_error(400, "invalid_host")
            elif request.method not in {"GET", "HEAD", "POST"} or request.method == "POST" and (
                raw_path is None
                or request.scope.get("path") != raw_path.decode("ascii", errors="ignore")
                or not is_run_post
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
                    supplied_action = _ascii_header(
                        _raw_headers(request, b"x-humanwire-action")
                    )
                    origin = _ascii_header(_raw_headers(request, b"origin"))
                    content_types = _raw_headers(request, b"content-type")
                    content_type = _ascii_header(content_types)
                    if (
                        supplied_action is None
                        or not hmac.compare_digest(supplied_action, action_token)
                    ):
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
        except Exception:  # noqa: BLE001 - boundary errors must not retain request details
            response = _fixed_error(500, "request_failed")
        for name, value in _SAFE_HEADERS.items():
            response.headers[name] = value
        return response

    def studio_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request=request,
            name="coordination_studio.html",
            context={"action_token": action_token, "delivery_mode": "stream"},
        )

    @app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def home(request: Request) -> Response:
        return studio_page(request)

    @app.api_route("/runs/{_run_alias}", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def workspace(request: Request, _run_alias: str) -> Response:
        return studio_page(request)

    @app.api_route("/api/catalog", methods=["GET", "HEAD"])
    def catalog() -> JSONResponse:
        return JSONResponse(content=product_catalog().model_dump(mode="json"))

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
        if coordination.agent_mode is not StudioAgentMode.STANDARD:
            return _fixed_error(409, "model_unavailable")
        try:
            created = manager.create_run(coordination)
        except ActiveRunError:
            return _fixed_error(409, "active_run")
        except ModelModeUnavailable:
            return _fixed_error(409, "model_unavailable")
        except Exception:  # noqa: BLE001 - manager details remain private
            return _fixed_error(500, "run_unavailable")
        return StreamingResponse(
            _run_stream(manager, created.run_alias, poll_interval_seconds),
            status_code=201,
            media_type="application/x-ndjson",
            headers={
                "X-HumanWire-Run-Alias": created.run_alias,
                "X-HumanWire-Workspace-Url": "/",
            },
        )

    return app
