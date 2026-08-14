"""Private literal-loopback API for the HumanWire coordination studio."""

from __future__ import annotations

import hmac
import json
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from humanwire.studio_exports import product_events_csv, product_evidence
from humanwire.studio_models import CoordinationRequest, product_catalog
from humanwire.studio_run import (
    ActiveRunError,
    ModelModeUnavailable,
    StudioRunManager,
    UnknownRunError,
    validate_run_alias,
)

_PACKAGE_DIR = Path(__file__).resolve().parent
_MAX_BODY_BYTES = 8192
_HOST = re.compile(r"^127\.0\.0\.1(?::([0-9]+))?$")
_ORIGIN = re.compile(r"^http://127\.0\.0\.1(?::([0-9]+))?$")
_SAFE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "X-Frame-Options": "DENY",
}


@dataclass(frozen=True)
class StudioOptions:
    workspace_root: Path
    port: int = 8766
    seed: int = 0
    step_delay_ms: int = 350
    max_decision_workers: int = 4

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace_root", Path(self.workspace_root).resolve())
        if not 1024 <= self.port <= 65535:
            raise ValueError("port must be between 1024 and 65535")
        if not 0 <= self.seed <= 2_147_483_647:
            raise ValueError("seed must be between 0 and 2147483647")
        if not 0 <= self.step_delay_ms <= 3000:
            raise ValueError("step delay must be between 0 and 3000 milliseconds")
        if not 1 <= self.max_decision_workers <= 8:
            raise ValueError("max decision workers must be between 1 and 8")


def validate_studio_host(host: str) -> str:
    """Accept only the single literal interface authorized for the studio."""
    if host != "127.0.0.1":
        raise ValueError("coordination studio host must be 127.0.0.1")
    return host


def _fixed_error(status_code: int, code: str, **safe: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": code, **safe})


def _raw_headers(request: Request, name: bytes) -> tuple[bytes, ...]:
    lowered = name.lower()
    return tuple(value for key, value in request.scope.get("headers", ()) if key.lower() == lowered)


def _ascii_header(values: tuple[bytes, ...]) -> str | None:
    if len(values) != 1:
        return None
    try:
        return values[0].decode("ascii")
    except UnicodeDecodeError:
        return None


def _literal_loopback(value: str | None, pattern: re.Pattern[str]) -> bool:
    if value is None:
        return False
    matched = pattern.fullmatch(value)
    if matched is None:
        return False
    port_text = matched.group(1)
    return port_text is None or (
        len(port_text) <= 5 and 1 <= int(port_text) <= 65535
    )


def _rejecting_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _coordination_request(body: bytes) -> CoordinationRequest | None:
    try:
        text = body.decode("utf-8")
        decoded = json.loads(
            text,
            object_pairs_hook=_rejecting_object,
            parse_constant=_reject_constant,
        )
        if not isinstance(decoded, dict):
            return None
        return CoordinationRequest.model_validate(decoded)
    except (UnicodeError, ValueError, TypeError, ValidationError):
        return None


def _safe_attachment_name(run_alias: str, suffix: str) -> str:
    try:
        alias = validate_run_alias(run_alias)
    except ValueError:
        alias = "humanwire-coordination"
    return f"{alias}-{suffix}"


def create_coordination_studio_app(
    manager: StudioRunManager,
    action_token: str,
) -> FastAPI:
    """Build the private studio without sharing any route with the public demo."""
    if not isinstance(action_token, str) or not action_token:
        raise ValueError("studio action token must be non-empty")
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
        _request: Request, error: StarletteHTTPException
    ) -> JSONResponse:
        if error.status_code == 404:
            return _fixed_error(404, "not_found")
        if error.status_code == 405:
            return _fixed_error(405, "method_not_allowed")
        return _fixed_error(error.status_code, "request_failed")

    @app.middleware("http")
    async def private_loopback_boundary(request: Request, call_next):
        try:
            host = _ascii_header(_raw_headers(request, b"host"))
            if not _literal_loopback(host, _HOST):
                response = _fixed_error(400, "invalid_host")
            elif request.method not in {
                "GET",
                "HEAD",
                "POST",
            } or request.method == "POST" and (
                request.scope.get("path") != "/api/runs"
                or request.scope.get("raw_path") != b"/api/runs"
                or bool(request.scope.get("query_string"))
            ):
                response = _fixed_error(405, "method_not_allowed")
            elif request.method == "POST":
                lengths = _raw_headers(request, b"content-length")
                length_text = _ascii_header(lengths)
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
                    request, b"content-encoding"
                ):
                    response = _fixed_error(400, "invalid_request")
                else:
                    actions = _raw_headers(request, b"x-humanwire-action")
                    supplied_action = _ascii_header(actions)
                    if supplied_action is None or not hmac.compare_digest(
                        supplied_action, action_token
                    ):
                        response = _fixed_error(403, "action_forbidden")
                    else:
                        origins = _raw_headers(request, b"origin")
                        origin = _ascii_header(origins) if origins else None
                        if origins and (
                            len(origins) != 1
                            or origin is None
                            or not _literal_loopback(origin, _ORIGIN)
                        ):
                            response = _fixed_error(403, "origin_forbidden")
                        else:
                            types = _raw_headers(request, b"content-type")
                            content_type = _ascii_header(types)
                            if len(types) != 1:
                                response = _fixed_error(400, "invalid_request")
                            elif content_type is None or content_type.split(";", 1)[
                                0
                            ].strip().casefold() != "application/json":
                                response = _fixed_error(415, "unsupported_media_type")
                            else:
                                response = await call_next(request)
            else:
                response = await call_next(request)
        except Exception:  # noqa: BLE001 - boundary failures must not retain private details
            response = _fixed_error(500, "request_failed")
        for name, value in _SAFE_HEADERS.items():
            response.headers[name] = value
        return response

    def studio_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request=request,
            name="coordination_studio.html",
            context={"action_token": action_token},
        )

    @app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def home(request: Request) -> Response:
        return studio_page(request)

    @app.api_route("/runs/{run_alias}", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def workspace(request: Request, run_alias: str) -> Response:
        try:
            manager.snapshot(run_alias)
        except (UnknownRunError, ValueError):
            return _fixed_error(404, "not_found")
        except Exception:  # noqa: BLE001 - manager details stay inside loopback boundary
            return _fixed_error(503, "run_unavailable")
        return studio_page(request)

    @app.api_route("/api/catalog", methods=["GET", "HEAD"])
    def catalog() -> JSONResponse:
        return JSONResponse(content=product_catalog().model_dump(mode="json"))

    @app.post("/api/runs")
    async def create_run(request: Request) -> JSONResponse:
        length_text = _ascii_header(_raw_headers(request, b"content-length"))
        if length_text is None:
            return _fixed_error(400, "invalid_request")
        body = await request.body()
        if len(body) != int(length_text) or not 1 <= len(body) <= _MAX_BODY_BYTES:
            return _fixed_error(400, "invalid_request")
        coordination = _coordination_request(body)
        if coordination is None:
            return _fixed_error(400, "invalid_request")
        try:
            created = manager.create_run(coordination)
        except ActiveRunError as error:
            return _fixed_error(409, "active_run", run_alias=error.run_alias)
        except ModelModeUnavailable:
            return _fixed_error(409, "model_unavailable")
        except Exception:  # noqa: BLE001 - manager details stay inside loopback boundary
            return _fixed_error(500, "run_unavailable")
        return JSONResponse(
            status_code=201,
            content={
                "run_alias": created.run_alias,
                "workspace_url": f"/runs/{created.run_alias}",
            },
        )

    @app.api_route("/api/runs/{run_alias}", methods=["GET", "HEAD"])
    def run_snapshot(run_alias: str) -> JSONResponse:
        try:
            snapshot = manager.snapshot(run_alias)
        except (UnknownRunError, ValueError):
            return _fixed_error(404, "not_found")
        except Exception:  # noqa: BLE001 - manager details stay inside loopback boundary
            return _fixed_error(503, "run_unavailable")
        return JSONResponse(content=snapshot.model_dump(mode="json"))

    def final_export(run_alias: str):
        try:
            binding = manager.final_binding(run_alias)
        except (UnknownRunError, ValueError):
            return None, _fixed_error(404, "not_found")
        except Exception:  # noqa: BLE001 - binding details stay private
            return None, _fixed_error(503, "run_unavailable")
        if binding is None:
            return None, _fixed_error(409, "final_evidence_unavailable")
        try:
            evidence = product_evidence(binding)
        except ValueError:
            return None, _fixed_error(409, "final_evidence_unavailable")
        return evidence, None

    @app.api_route(
        "/api/runs/{run_alias}/evidence.json",
        methods=["GET", "HEAD"],
    )
    def evidence_json(run_alias: str) -> Response:
        evidence, error = final_export(run_alias)
        if error is not None:
            return error
        assert evidence is not None
        return JSONResponse(
            content=evidence.model_dump(mode="json"),
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{_safe_attachment_name(run_alias, "evidence.json")}"'
                )
            },
        )

    @app.api_route(
        "/api/runs/{run_alias}/events.csv",
        methods=["GET", "HEAD"],
    )
    def events_csv(run_alias: str) -> Response:
        evidence, error = final_export(run_alias)
        if error is not None:
            return error
        assert evidence is not None
        return Response(
            content=product_events_csv(evidence),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{_safe_attachment_name(run_alias, "events.csv")}"'
                )
            },
        )

    return app


def run_coordination_studio(options: StudioOptions) -> int:
    """Construct an idle studio manager/app and bind it only to literal loopback."""
    options = StudioOptions(**options.__dict__)
    manager = StudioRunManager(
        workspace_root=options.workspace_root,
        seed=options.seed,
        step_delay_ms=options.step_delay_ms,
        max_decision_workers=options.max_decision_workers,
    )
    app = create_coordination_studio_app(manager, action_token=secrets.token_urlsafe())
    print(f"studio_url=http://127.0.0.1:{options.port}")
    uvicorn.run(app, host=validate_studio_host("127.0.0.1"), port=options.port)
    return 0
