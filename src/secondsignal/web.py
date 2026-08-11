from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import SQLAlchemyError

from secondsignal.config import Settings
from secondsignal.domain import CaseState, VerificationCase
from secondsignal.repository import SqlAlchemyCaseRepository

PACKAGE_DIR = Path(__file__).resolve().parent
UNVERIFIED_STATES = {
    CaseState.UNVERIFIED,
    CaseState.EXPIRED,
    CaseState.CANCELLED,
    CaseState.DELIVERY_FAILED,
}

STATE_LABELS = {
    CaseState.RECEIVED: "Received",
    CaseState.ANALYZED: "Analyzed",
    CaseState.AWAITING_VERIFICATION: "Awaiting",
    CaseState.VERIFIED: "Verified",
    CaseState.DENIED: "Denied",
    CaseState.UNVERIFIED: "Unverified",
    CaseState.EXPIRED: "Unverified",
    CaseState.CANCELLED: "Cancelled",
    CaseState.DELIVERY_FAILED: "Delivery failed",
}

EVENT_LABELS = {
    "case.created": "Received",
    "case.analyzed": "AI analyzed risk",
    "case.awaiting_verification": "Awaiting verification",
    "verification.requested": "Verification requested",
    "case.verified": "Human responded YES — verdict verified",
    "case.denied": "Human responded NO — verdict denied",
    "case.unverified": "Unable to verify",
    "case.expired": "Verification expired",
    "case.cancelled": "Reporter cancelled case",
    "case.delivery_failed": "Verification delivery failed",
    "model.fallback": "AI fallback analyzer used",
    "verification.invalid_response": "Invalid response rejected",
}


def _channel_label(value: str) -> str:
    return value.capitalize()


def _age_label(created_at: datetime, now: datetime) -> str:
    seconds = max(int((now - created_at).total_seconds()), 0)
    if seconds < 60:
        return "<1 min"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hr"
    return f"{hours // 24} d"


def _human_response(case: VerificationCase) -> str:
    if case.state is CaseState.VERIFIED:
        return "YES — I sent this request"
    if case.state is CaseState.DENIED:
        return "NO — I did not send this request"
    if case.state is CaseState.AWAITING_VERIFICATION:
        return "Awaiting a registered human response"
    return "No conclusive human response"


def _case_view(case: VerificationCase, now: datetime) -> dict[str, Any]:
    verification_channel = (
        case.verification_route.channel.value if case.verification_route else None
    )
    channel_path = _channel_label(case.origin_channel.value)
    if verification_channel:
        channel_path += f" → {_channel_label(verification_channel)}"
    else:
        channel_path += " → No independent route"
    return {
        "token": case.token,
        "claimed_identity": case.claimed_identity_name,
        "channel_path": channel_path,
        "origin_channel": _channel_label(case.origin_channel.value),
        "verification_channel": (
            _channel_label(verification_channel) if verification_channel else "Unavailable"
        ),
        "state": STATE_LABELS[case.state],
        "state_key": case.state.value,
        "state_class": ("unverified" if case.state in UNVERIFIED_STATES else case.state.value),
        "age": _age_label(case.created_at, now),
        "safe_summary": case.risk.safe_summary,
        "risk_signals": case.risk.risk_signals,
        "human_response": _human_response(case),
        "created_at": case.created_at,
        "resolved_at": case.resolved_at,
        "reason": case.reason,
    }


def create_app(
    repository: SqlAlchemyCaseRepository | Any,
    settings: Settings,
    *,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    application_clock = clock or (lambda: datetime.now(UTC))
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
    app = FastAPI(title="SecondSignal", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    @app.get("/")
    def dashboard(request: Request):
        now = application_clock()
        cases = repository.list_recent(limit=30)
        counts = {
            "awaiting": sum(case.state is CaseState.AWAITING_VERIFICATION for case in cases),
            "verified": sum(case.state is CaseState.VERIFIED for case in cases),
            "denied": sum(case.state is CaseState.DENIED for case in cases),
            "unverified": sum(case.state in UNVERIFIED_STATES for case in cases),
        }
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "cases": [_case_view(case, now) for case in cases],
                "counts": counts,
            },
        )

    @app.get("/cases/{token}")
    def case_detail(request: Request, token: str):
        case = repository.get_by_token(token.upper())
        if case is None:
            raise HTTPException(status_code=404, detail="Case not found")
        events = repository.list_events(case.case_id)
        event_views = [
            {
                "label": EVENT_LABELS.get(event.event_type, event.event_type.replace(".", " ")),
                "created_at": event.created_at,
            }
            for event in events
        ]
        return templates.TemplateResponse(
            request=request,
            name="case_detail.html",
            context={
                "case": _case_view(case, application_clock()),
                "events": event_views,
            },
        )

    @app.get("/health/live")
    def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def readiness():
        try:
            email = repository.get_runtime_status("channel.email")
            telegram = repository.get_runtime_status("channel.telegram")
            heartbeat = repository.get_runtime_status("listener.heartbeat")
        except (SQLAlchemyError, RuntimeError):
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "database_unavailable"},
            )
        if not email or not telegram or email[0] != "ready" or telegram[0] != "ready":
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "channels_not_ready"},
            )
        if not heartbeat:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "missing_heartbeat"},
            )
        freshness_seconds = max(settings.expiry_poll_seconds * 3, 20)
        if (application_clock() - heartbeat[1]).total_seconds() > freshness_seconds:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "stale_heartbeat"},
            )
        return {"status": "ready"}

    return app
