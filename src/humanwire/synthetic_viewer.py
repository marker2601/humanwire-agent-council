"""Loopback-only read surface for safe synthetic progress and final evidence."""

from __future__ import annotations

import csv
import io
import ipaddress
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from humanwire.synthetic import load_transcript
from humanwire.synthetic_progress import SyntheticEvidenceBundle, SyntheticProgressStore

_PACKAGE_DIR = Path(__file__).resolve().parent
_SAFE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
_CSV_FIELDS = (
    "ordinal",
    "created_at",
    "story",
    "stage",
    "source",
    "destination",
    "data_point",
)
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def validate_viewer_host(host: str) -> str:
    """Accept an explicit loopback IP and reject names or network interfaces."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise ValueError("synthetic viewer host must be a loopback IP") from error
    if not address.is_loopback:
        raise ValueError("synthetic viewer host must be loopback")
    return host


def _unavailable() -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"detail": "Final evidence unavailable"},
    )


def _validated_final_evidence(
    store: SyntheticProgressStore,
    transcript_path: Path,
) -> SyntheticEvidenceBundle | None:
    bundle = store.evidence_bundle()
    if bundle is None:
        return None
    try:
        transcript = load_transcript(transcript_path)
    except (OSError, UnicodeError, ValidationError, ValueError):
        return None
    if (
        transcript.scenario.identity_seed != bundle.identity_seed
        or transcript.scenario.provenance != bundle.provenance
    ):
        return None
    return bundle


def _csv_cell(value: object) -> str:
    rendered = str(value)
    if rendered.startswith(_FORMULA_PREFIXES):
        return f"'{rendered}"
    return rendered


def _events_csv(bundle: SyntheticEvidenceBundle) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for event in bundle.events:
        row = {
            "ordinal": event.persisted_ordinal or event.timeline_ordinal,
            "created_at": event.created_at.isoformat(),
            "story": event.story,
            "stage": event.stage,
            "source": event.source,
            "destination": event.destination,
            "data_point": event.data_point,
        }
        writer.writerow({key: _csv_cell(value) for key, value in row.items()})
    return output.getvalue()


def create_synthetic_viewer_app(
    store: SyntheticProgressStore,
    transcript_path: Path,
) -> FastAPI:
    """Build a separate read-only viewer over Task 4's safe immutable models."""
    templates = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount(
        "/static",
        StaticFiles(directory=str(_PACKAGE_DIR / "static")),
        name="static",
    )

    @app.middleware("http")
    async def safe_local_surface(request: Request, call_next):
        if request.method not in {"GET", "HEAD"}:
            response = JSONResponse(
                status_code=405,
                content={"detail": "Method not allowed"},
            )
        else:
            response = await call_next(request)
        for name, value in _SAFE_HEADERS.items():
            response.headers[name] = value
        return response

    @app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def home(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="synthetic_progress.html",
            context={},
        )

    @app.api_route("/progress.json", methods=["GET", "HEAD"])
    def progress() -> JSONResponse:
        return JSONResponse(content=store.snapshot().model_dump(mode="json"))

    @app.api_route("/evidence.json", methods=["GET", "HEAD"])
    def evidence() -> Response:
        bundle = _validated_final_evidence(store, transcript_path)
        if bundle is None:
            return _unavailable()
        return JSONResponse(
            content=bundle.model_dump(mode="json"),
            headers={
                "Content-Disposition": ('attachment; filename="humanwire-synthetic-evidence.json"')
            },
        )

    @app.api_route("/events.csv", methods=["GET", "HEAD"])
    def events_csv() -> Response:
        bundle = _validated_final_evidence(store, transcript_path)
        if bundle is None:
            return _unavailable()
        return Response(
            content=_events_csv(bundle),
            media_type="text/csv",
            headers={
                "Content-Disposition": ('attachment; filename="humanwire-synthetic-events.csv"')
            },
        )

    return app


def run_synthetic_viewer(
    app: FastAPI,
    *,
    host: str = "127.0.0.1",
    port: int = 8766,
) -> None:
    """Run the viewer only on a validated loopback address."""
    uvicorn.run(app, host=validate_viewer_host(host), port=port)
