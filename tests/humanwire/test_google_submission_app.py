from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from humanwire.cloud_dispatch import DispatchUnavailable, InlineRunDispatcher
from humanwire.cloud_store import CloudRunState, InMemoryRunRepository
from humanwire.google_submission_app import create_google_submission_app
from humanwire.studio_models import StudioAgentMode
from tests.humanwire.studio_fixtures import launch_request

HOST = "humanwire-cloud.example.test"
TOKEN = "google-submission-action-token"
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


class RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def dispatch(self, run_alias: str, idempotency_key: str) -> None:
        self.calls.append((run_alias, idempotency_key))


class FailedDispatcher:
    def dispatch(self, _run_alias: str, _idempotency_key: str) -> None:
        raise DispatchUnavailable()


def cloud_request() -> dict[str, object]:
    return launch_request(agent_mode=StudioAgentMode.GOOGLE_ADK).model_dump(mode="json")


def headers(**updates: str) -> dict[str, str]:
    return {
        "Host": HOST,
        "Origin": f"https://{HOST}",
        "X-HumanWire-Action": TOKEN,
        **updates,
    }


def app(repository=None, dispatcher=None):
    repository = repository or InMemoryRunRepository()
    dispatcher = dispatcher or RecordingDispatcher()
    return create_google_submission_app(
        repository,
        dispatcher,
        action_token=TOKEN,
        allowed_hosts=(HOST,),
        clock=lambda: NOW,
    )


def raw_post_response(
    application,
    raw_headers: list[tuple[bytes, bytes]],
    body: bytes,
):
    messages: list[dict[str, object]] = []
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/api/runs",
        "raw_path": b"/api/runs",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", HOST.encode()), *raw_headers],
        "client": ("127.0.0.1", 10000),
        "server": (HOST, 443),
    }
    asyncio.run(application(scope, receive, send))
    started = next(item for item in messages if item["type"] == "http.response.start")
    content = b"".join(
        item.get("body", b"")
        for item in messages
        if item["type"] == "http.response.body"
    )
    return SimpleNamespace(
        status_code=started["status"],
        content=content,
        json=lambda: json.loads(content),
    )


def test_create_is_queued_once_and_returns_only_safe_public_fields() -> None:
    repository = InMemoryRunRepository()
    dispatcher = RecordingDispatcher()
    application = app(repository, dispatcher)

    with TestClient(application, base_url=f"https://{HOST}") as client:
        response = client.post("/api/runs", headers=headers(), json=cloud_request())

    assert response.status_code == 202
    assert set(response.json()) == {"run_alias", "workspace_url", "state"}
    assert response.json()["state"] == "queued"
    assert response.json()["workspace_url"] == f"/runs/{response.json()['run_alias']}"
    assert len(dispatcher.calls) == 1
    alias, private_key = dispatcher.calls[0]
    assert alias == response.json()["run_alias"]
    assert private_key not in response.text
    assert repository.load_metadata(alias).state is CloudRunState.QUEUED


def test_dispatch_failure_atomically_fails_and_releases_active_owner() -> None:
    repository = InMemoryRunRepository()
    application = app(repository, FailedDispatcher())

    with TestClient(application, base_url=f"https://{HOST}") as client:
        failed = client.post("/api/runs", headers=headers(), json=cloud_request())
        retry = client.post("/api/runs", headers=headers(), json=cloud_request())

    assert (failed.status_code, failed.json()) == (
        503,
        {"error": "dispatch_unavailable"},
    )
    assert (retry.status_code, retry.json()) == (
        503,
        {"error": "dispatch_unavailable"},
    )
    assert repository.active_run is None


def test_public_creation_requires_explicit_google_mode_before_repository() -> None:
    repository = InMemoryRunRepository()
    application = app(repository)
    request = cloud_request()
    request["agent_mode"] = "standard"

    with TestClient(application, base_url=f"https://{HOST}") as client:
        response = client.post("/api/runs", headers=headers(), json=request)

    assert (response.status_code, response.json()) == (
        409,
        {"error": "google_runtime_required"},
    )
    assert repository.active_run is None


@pytest.mark.parametrize(
    "origin",
    (
        "https://second.example.test",
        f"https://{HOST}:",
        f"https://{HOST}?",
        f"https://{HOST}#",
    ),
)
def test_origin_is_exactly_the_current_request_host(origin) -> None:
    repository = InMemoryRunRepository()
    application = create_google_submission_app(
        repository,
        RecordingDispatcher(),
        action_token=TOKEN,
        allowed_hosts=(HOST, "second.example.test"),
        clock=lambda: NOW,
    )
    with TestClient(application, base_url=f"https://{HOST}") as client:
        response = client.post(
            "/api/runs",
            headers=headers(Origin=origin),
            json=cloud_request(),
        )

    assert (response.status_code, response.json()) == (
        403,
        {"error": "origin_forbidden"},
    )
    assert repository.active_run is None


def test_snapshot_poll_has_etag_saved_ordinal_and_cold_workspace() -> None:
    repository = InMemoryRunRepository()
    application = app(repository)
    with TestClient(application, base_url=f"https://{HOST}") as client:
        created = client.post("/api/runs", headers=headers(), json=cloud_request()).json()
        page = client.get(created["workspace_url"])
        first = client.get(f"/api/runs/{created['run_alias']}")
        unchanged = client.get(
            f"/api/runs/{created['run_alias']}",
            headers={"If-None-Match": first.headers["etag"]},
        )

    assert page.status_code == 200
    assert '<meta name="humanwire-delivery-mode" content="cloud">' in page.text
    assert first.status_code == 200
    assert first.json()["run_state"] == "starting"
    assert first.headers["x-humanwire-saved-ordinal"] == "0"
    assert first.headers["etag"].startswith('"')
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert unchanged.headers["etag"] == first.headers["etag"]


def test_gets_never_create_runs_and_unknown_workspace_is_not_a_shell() -> None:
    repository = InMemoryRunRepository()
    application = app(repository)
    with TestClient(application, base_url=f"https://{HOST}") as client:
        home = client.get("/")
        unknown = client.get("/runs/coordination-not-present")
        malformed = client.get("/runs/not%2Fsafe")

    assert home.status_code == 200
    assert repository.active_run is None
    assert (unknown.status_code, unknown.json()) == (
        404,
        {"error": "run_not_found"},
    )
    assert malformed.status_code == 404


def test_simultaneous_starts_have_one_safe_winner_and_one_dispatch() -> None:
    repository = InMemoryRunRepository()
    dispatcher = RecordingDispatcher()
    application = app(repository, dispatcher)

    def start() -> tuple[int, dict[str, object]]:
        with TestClient(application, base_url=f"https://{HOST}") as client:
            response = client.post("/api/runs", headers=headers(), json=cloud_request())
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [item.result() for item in (pool.submit(start), pool.submit(start))]

    assert sorted(status for status, _body in results) == [202, 409]
    conflict = next(body for status, body in results if status == 409)
    assert conflict == {"error": "active_run"}
    assert len(dispatcher.calls) == 1


def test_catalog_health_head_and_private_worker_separation() -> None:
    application = app()
    with TestClient(application, base_url=f"https://{HOST}") as client:
        page = client.get("/")
        catalog = client.get("/api/catalog")
        catalog_head = client.head("/api/catalog")
        health = client.get("/healthz")
        docs = client.get("/docs")
        worker = client.post("/internal/pubsub/runs", headers=headers(), json={})

    assert '<meta name="humanwire-delivery-mode" content="cloud">' in page.text
    assert "Google ADK agents" in page.text
    assert "Gemini 3.5 Flash" in page.text
    assert "HumanWire authority gates" in page.text
    assert "No external stakeholder messages" in page.text
    assert 'name="agent_mode" value="standard"' not in page.text
    assert 'name="agent_mode" value="model_assisted"' not in page.text
    assert catalog.status_code == 200
    assert catalog_head.status_code == 200 and catalog_head.content == b""
    assert health.json() == {"service_role": "web", "status": "ok"}
    assert docs.status_code == 404
    assert worker.status_code == 405


@pytest.mark.parametrize(
    ("path", "request_headers", "expected"),
    [
        ("/api%2Fruns", {}, 405),
        ("/%61pi/runs", {}, 405),
        ("/api/runs?extra=1", {}, 405),
        ("/api/runs", {"Host": "elsewhere.example.test"}, 400),
        ("/api/runs", {"Origin": "https://elsewhere.example.test"}, 403),
        ("/api/runs", {"X-HumanWire-Action": "wrong"}, 403),
        ("/api/runs", {"Content-Type": "text/plain"}, 415),
        ("/api/runs", {"Content-Encoding": "gzip"}, 400),
        ("/api/runs", {"Transfer-Encoding": "chunked"}, 400),
    ],
)
def test_post_boundary_is_exact_and_fail_closed(path, request_headers, expected) -> None:
    application = app()
    with TestClient(application, base_url=f"https://{HOST}") as client:
        response = client.post(
            path,
            headers=headers(**request_headers),
            content=json.dumps(cloud_request()).encode(),
        )

    assert response.status_code == expected
    assert set(response.json()) == {"error"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize(
    ("duplicated", "expected"),
    [
        ("host", 400),
        ("origin", 403),
        ("x-humanwire-action", 403),
        ("content-type", 400),
        ("content-length", 400),
    ],
)
def test_security_sensitive_duplicate_headers_are_rejected(duplicated, expected) -> None:
    body = json.dumps(cloud_request()).encode()
    values = [
        ("Host", HOST),
        ("Origin", f"https://{HOST}"),
        ("X-HumanWire-Action", TOKEN),
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
    ]
    original = next(value for name, value in values if name.casefold() == duplicated)
    values.append((duplicated, original))

    with TestClient(app(), base_url=f"https://{HOST}") as client:
        response = client.post("/api/runs", headers=values, content=body)

    assert response.status_code == expected
    assert set(response.json()) == {"error"}


@pytest.mark.parametrize(
    ("include_length", "header_updates", "expected"),
    [
        (False, {}, 400),
        (True, {b"origin": b"https://humanwire-cloud.example.test:\xff"}, 403),
        (True, {b"content-length": b"1"}, 400),
    ],
)
def test_raw_boundary_rejects_missing_length_non_ascii_origin_and_mismatch(
    include_length,
    header_updates,
    expected,
) -> None:
    body = json.dumps(cloud_request()).encode()
    raw = {
        b"content-type": b"application/json",
        b"origin": f"https://{HOST}".encode(),
        b"x-humanwire-action": TOKEN.encode(),
    }
    if include_length:
        raw[b"content-length"] = str(len(body)).encode()
    if header_updates:
        raw.update(header_updates)
    response = raw_post_response(app(), list(raw.items()), body)

    assert response.status_code == expected
    assert set(response.json()) == {"error"}


def test_unknown_alias_and_unbound_exports_are_fixed() -> None:
    repository = InMemoryRunRepository()
    application = app(repository)
    with TestClient(application, base_url=f"https://{HOST}") as client:
        unknown = client.get("/api/runs/coordination-unknown")
        created = client.post("/api/runs", headers=headers(), json=cloud_request()).json()
        pending_json = client.get(f"/api/runs/{created['run_alias']}/evidence.json")
        pending_csv = client.get(f"/api/runs/{created['run_alias']}/evidence.csv")

    assert (unknown.status_code, unknown.json()) == (404, {"error": "run_not_found"})
    assert (pending_json.status_code, pending_json.json()) == (
        409,
        {"error": "exports_not_ready"},
    )
    assert (pending_csv.status_code, pending_csv.json()) == (
        409,
        {"error": "exports_not_ready"},
    )


def test_downstream_exceptions_use_fixed_safe_envelope() -> None:
    application = app()

    @application.get("/private-failure")
    def private_failure():
        raise RuntimeError("PRIVATE-PATH/API-KEY")

    with TestClient(application, base_url=f"https://{HOST}", raise_server_exceptions=False) as client:
        response = client.get("/private-failure")

    assert (response.status_code, response.json()) == (
        500,
        {"error": "request_failed"},
    )
    assert "PRIVATE" not in response.text


def test_private_repository_and_dispatcher_failures_never_cross_response() -> None:
    class PrivateRepositoryFailure(InMemoryRunRepository):
        def create_run(self, *args, **kwargs):
            raise RuntimeError("PRIVATE-FIRESTORE-PATH/API-KEY")

    class PrivateDispatcherFailure:
        def dispatch(self, _run_alias, _idempotency_key):
            raise RuntimeError("PRIVATE-PUBSUB-BODY/API-KEY")

    applications = (
        app(PrivateRepositoryFailure(), RecordingDispatcher()),
        app(InMemoryRunRepository(), PrivateDispatcherFailure()),
    )
    responses = []
    for application in applications:
        with TestClient(application, base_url=f"https://{HOST}") as client:
            responses.append(
                client.post("/api/runs", headers=headers(), json=cloud_request())
            )

    assert [(item.status_code, item.json()) for item in responses] == [
        (500, {"error": "run_unavailable"}),
        (503, {"error": "dispatch_unavailable"}),
    ]
    assert all("PRIVATE" not in item.text for item in responses)


def test_inline_dispatch_can_finish_and_cold_exports_are_downloadable(tmp_path) -> None:
    from humanwire.cloud_worker import CloudRunWorker
    from humanwire.synthetic import generate_scenario

    repository = InMemoryRunRepository()

    class DecisionFactory:
        model_identifier = "gemini-3.5-flash"

    def deterministic_runner(scenario, output_path, run_root, **kwargs):
        return generate_scenario(
            scenario,
            output_path,
            run_root,
            decision_engine=None,
            max_decision_workers=1,
            progress_observer=kwargs["progress_observer"],
            presentation_observer=kwargs["presentation_observer"],
            mandate_request=kwargs["mandate_request"],
            include_change_story=False,
            availability_date=kwargs["availability_date"],
            defer_authority_until_ready=True,
            include_conflict=kwargs["include_conflict"],
        )

    worker = CloudRunWorker(
        repository,
        decision_factory_builder=DecisionFactory,
        runner=deterministic_runner,
        clock=lambda: NOW,
        claim_owner_factory=lambda: "worker-owner-000000000000006",
    )
    application = app(repository, InlineRunDispatcher(worker.handle))

    with TestClient(application, base_url=f"https://{HOST}") as client:
        created = client.post("/api/runs", headers=headers(), json=cloud_request()).json()
        snapshot = client.get(f"/api/runs/{created['run_alias']}")
        evidence = client.get(f"/api/runs/{created['run_alias']}/evidence.json")
        events = client.get(f"/api/runs/{created['run_alias']}/evidence.csv")

    assert snapshot.json()["run_state"] == "complete"
    assert evidence.status_code == 200
    assert evidence.headers["content-type"].startswith("application/json")
    assert evidence.headers["content-disposition"].endswith('evidence.json"')
    assert events.status_code == 200
    assert events.text.startswith("timeline_ordinal,persisted_ordinal,effect,")
    assert events.headers["content-disposition"].endswith('evidence.csv"')
