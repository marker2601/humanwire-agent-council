import asyncio
import csv
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import humanwire.__main__ as cli
from humanwire.demo import create_demo_app
from humanwire.studio_app import (
    StudioOptions,
    create_coordination_studio_app,
    run_coordination_studio,
    validate_studio_host,
)
from humanwire.studio_exports import _csv_cell
from humanwire.studio_run import ModelModeUnavailable, StudioRunManager
from humanwire.synthetic import build_coordination_scenario
from tests.humanwire.studio_fixtures import launch_request

ACTION_HEADERS = {
    "Content-Type": "application/json",
    "X-HumanWire-Action": "test-action-token",
}


def studio_client(tmp_path) -> tuple[TestClient, StudioRunManager]:
    manager = StudioRunManager(
        workspace_root=tmp_path,
        alias_factory=iter(["launch-001"]).__next__,
        step_delay_ms=0,
    )
    app = create_coordination_studio_app(manager, action_token="test-action-token")
    return TestClient(app, base_url="http://127.0.0.1"), manager


def request_body(**updates: object) -> str:
    body = launch_request().model_dump(mode="json")
    body.update(updates)
    return json.dumps(body)


def raw_post_response(
    app,
    headers: list[tuple[bytes, bytes]],
    body: bytes,
    *,
    path: str = "/api/runs",
    raw_path: bytes = b"/api/runs",
    query_string: bytes = b"",
):
    messages = []
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
        "scheme": "http",
        "path": path,
        "raw_path": raw_path,
        "query_string": query_string,
        "root_path": "",
        "headers": [(b"host", b"127.0.0.1"), *headers],
        "client": ("127.0.0.1", 10000),
        "server": ("127.0.0.1", 80),
    }
    asyncio.run(app(scope, receive, send))
    started = next(item for item in messages if item["type"] == "http.response.start")
    content = b"".join(
        item.get("body", b"") for item in messages if item["type"] == "http.response.body"
    )
    return SimpleNamespace(
        status_code=started["status"],
        headers={name.decode("ascii"): value.decode("ascii") for name, value in started["headers"]},
        content=content,
        text=content.decode("utf-8"),
    )


def test_studio_home_is_idle_product_and_has_no_started_run(tmp_path) -> None:
    client, manager = studio_client(tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    assert "Start a coordination" in response.text
    assert "Synthetic HumanWire progress" not in response.text
    assert "test-action-token" in response.text
    assert manager.list_runs() == ()


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 403),
        ({"X-HumanWire-Action": "wrong"}, 403),
        ({"X-HumanWire-Action": "test-action-token", "Content-Type": "text/plain"}, 415),
        (
            {
                "X-HumanWire-Action": "test-action-token",
                "Content-Type": "application/json",
                "Origin": "https://attacker.example",
            },
            403,
        ),
    ],
)
def test_create_run_requires_loopback_action_boundary(tmp_path, headers, status) -> None:
    client, _manager = studio_client(tmp_path)

    response = client.post(
        "/api/runs",
        headers=headers,
        content=request_body(),
    )

    assert response.status_code == status
    assert "objective" not in response.text
    assert "content-disposition" not in response.headers


@pytest.mark.parametrize(
    ("host", "status"),
    [
        ("127.0.0.1", 200),
        ("127.0.0.1:8766", 200),
        ("localhost", 400),
        ("::1", 400),
        ("attacker.example", 400),
        ("127.0.0.1:not-a-port", 400),
        ("127.0.0.1:65536", 400),
    ],
)
def test_host_boundary_accepts_only_literal_ipv4_loopback(tmp_path, host, status) -> None:
    client, _manager = studio_client(tmp_path)

    response = client.get("/", headers={"Host": host})

    assert response.status_code == status


def test_duplicate_host_is_rejected(tmp_path) -> None:
    client, _manager = studio_client(tmp_path)

    response = client.get(
        "/",
        headers=[("Host", "127.0.0.1"), ("Host", "attacker.example")],
    )

    assert response.status_code == 400


def test_pathological_numeric_host_and_length_headers_fail_closed(tmp_path) -> None:
    client, _manager = studio_client(tmp_path)
    digits = "1" * 5000

    invalid_host = client.get("/", headers={"Host": f"127.0.0.1:{digits}"})
    invalid_length = client.post(
        "/api/runs",
        headers={**ACTION_HEADERS, "Content-Length": digits},
        content=request_body(),
    )

    assert invalid_host.status_code == 400
    assert invalid_length.status_code == 400


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "OPTIONS"])
def test_mutating_and_preflight_methods_are_rejected(tmp_path, method) -> None:
    client, _manager = studio_client(tmp_path)

    response = client.request(method, "/")

    assert response.status_code == 405
    assert response.json() == {"error": "method_not_allowed"}


@pytest.mark.parametrize(
    "headers",
    [
        [
            ("Content-Length", "-1"),
            ("Content-Type", "application/json"),
            ("X-HumanWire-Action", "test-action-token"),
        ],
        [
            ("Content-Length", "word"),
            ("Content-Type", "application/json"),
            ("X-HumanWire-Action", "test-action-token"),
        ],
        [
            ("Content-Length", "10"),
            ("Content-Length", "11"),
            ("Content-Type", "application/json"),
            ("X-HumanWire-Action", "test-action-token"),
        ],
        [
            ("Content-Length", "8193"),
            ("Content-Type", "application/json"),
            ("X-HumanWire-Action", "test-action-token"),
        ],
    ],
)
def test_post_rejects_missing_ambiguous_or_out_of_bounds_length(tmp_path, headers) -> None:
    client, _manager = studio_client(tmp_path)

    response = client.post("/api/runs", headers=headers, content=request_body())

    assert response.status_code in {400, 413}
    assert response.json()["error"] in {"invalid_request", "request_too_large"}
    assert "content-disposition" not in response.headers


def test_post_rejects_truly_missing_content_length_at_the_asgi_boundary(tmp_path) -> None:
    manager = StudioRunManager(workspace_root=tmp_path)
    app = create_coordination_studio_app(manager, action_token="test-action-token")

    response = raw_post_response(
        app,
        [
            (b"content-type", b"application/json"),
            (b"x-humanwire-action", b"test-action-token"),
        ],
        request_body().encode(),
    )

    assert response.status_code == 400
    assert manager.list_runs() == ()


def test_post_rejects_present_non_ascii_origin_instead_of_treating_it_as_absent(
    tmp_path,
) -> None:
    manager = StudioRunManager(
        workspace_root=tmp_path,
        alias_factory=iter(["launch-001"]).__next__,
        step_delay_ms=0,
    )
    app = create_coordination_studio_app(manager, action_token="test-action-token")
    body = request_body().encode()

    response = raw_post_response(
        app,
        [
            (b"content-length", str(len(body)).encode()),
            (b"content-type", b"application/json"),
            (b"x-humanwire-action", b"test-action-token"),
            (b"origin", b"http://127.0.0.1:\xff"),
        ],
        body,
    )
    for run_alias in manager.list_runs():
        manager.join(run_alias, timeout=20)

    assert response.status_code == 403
    assert json.loads(response.text) == {"error": "origin_forbidden"}
    assert manager.list_runs() == ()


@pytest.mark.parametrize("raw_path", [b"/api%2Fruns", b"/%61pi/runs"])
def test_post_rejects_encoded_raw_path_aliases(tmp_path, raw_path) -> None:
    manager = StudioRunManager(
        workspace_root=tmp_path,
        alias_factory=iter(["launch-001"]).__next__,
        step_delay_ms=0,
    )
    app = create_coordination_studio_app(manager, action_token="test-action-token")
    body = request_body().encode()

    response = raw_post_response(
        app,
        [
            (b"content-length", str(len(body)).encode()),
            (b"content-type", b"application/json"),
            (b"x-humanwire-action", b"test-action-token"),
        ],
        body,
        path="/api/runs",
        raw_path=raw_path,
    )
    for run_alias in manager.list_runs():
        manager.join(run_alias, timeout=20)

    assert response.status_code == 405
    assert json.loads(response.text) == {"error": "method_not_allowed"}
    assert manager.list_runs() == ()


@pytest.mark.parametrize("path", ["/api/run", "/api/runs/"])
def test_post_rejects_wrong_path_and_query_string(tmp_path, path) -> None:
    client, manager = studio_client(tmp_path)

    wrong_path = client.post(path, headers=ACTION_HEADERS, content=request_body())
    query = client.post("/api/runs?start=true", headers=ACTION_HEADERS, content=request_body())

    assert wrong_path.status_code == 405
    assert query.status_code == 405
    assert manager.list_runs() == ()


@pytest.mark.parametrize("encoding_header", [b"transfer-encoding", b"content-encoding"])
def test_post_rejects_transfer_and_content_encodings(tmp_path, encoding_header) -> None:
    manager = StudioRunManager(workspace_root=tmp_path)
    app = create_coordination_studio_app(manager, action_token="test-action-token")
    body = request_body().encode()

    response = raw_post_response(
        app,
        [
            (b"content-length", str(len(body)).encode()),
            (b"content-type", b"application/json"),
            (b"x-humanwire-action", b"test-action-token"),
            (encoding_header, b"identity"),
        ],
        body,
    )

    assert response.status_code == 400
    assert json.loads(response.text) == {"error": "invalid_request"}
    assert manager.list_runs() == ()


def test_post_rejects_actual_body_length_mismatch(tmp_path) -> None:
    manager = StudioRunManager(workspace_root=tmp_path)
    app = create_coordination_studio_app(manager, action_token="test-action-token")
    body = request_body().encode()

    response = raw_post_response(
        app,
        [
            (b"content-length", str(len(body) + 1).encode()),
            (b"content-type", b"application/json"),
            (b"x-humanwire-action", b"test-action-token"),
        ],
        body,
    )

    assert response.status_code == 400
    assert json.loads(response.text) == {"error": "invalid_request"}
    assert manager.list_runs() == ()


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        (
            [
                ("Content-Type", "application/json"),
                ("Content-Type", "application/json"),
                ("X-HumanWire-Action", "test-action-token"),
            ],
            400,
        ),
        (
            [
                ("Content-Type", "application/json"),
                ("X-HumanWire-Action", "test-action-token"),
                ("X-HumanWire-Action", "test-action-token"),
            ],
            403,
        ),
        (
            [
                ("Content-Type", "application/json"),
                ("X-HumanWire-Action", "test-action-token"),
                ("Origin", "http://127.0.0.1"),
                ("Origin", "http://127.0.0.1"),
            ],
            403,
        ),
    ],
)
def test_post_rejects_duplicate_security_headers(tmp_path, headers, status) -> None:
    client, _manager = studio_client(tmp_path)

    response = client.post("/api/runs", headers=headers, content=request_body())

    assert response.status_code == status


@pytest.mark.parametrize("origin", ["http://127.0.0.1", "http://127.0.0.1:8766"])
def test_post_accepts_optional_literal_loopback_origin(tmp_path, origin) -> None:
    client, manager = studio_client(tmp_path)

    response = client.post(
        "/api/runs",
        headers={**ACTION_HEADERS, "Origin": origin},
        content=request_body(),
    )

    assert response.status_code == 201
    manager.join("launch-001", timeout=20)


@pytest.mark.parametrize(
    "body",
    [
        "{",
        '{"objective":"first objective long enough","objective":"second objective long enough"}',
        request_body(unexpected="field"),
        request_body(participant_ids=["inform", "ack", "unknown"]),
        request_body(objective="too short"),
        request_body(objective="x" * 1001),
    ],
)
def test_post_rejects_invalid_or_ambiguous_json_without_reflection(tmp_path, body) -> None:
    client, _manager = studio_client(tmp_path)

    response = client.post("/api/runs", headers=ACTION_HEADERS, content=body)

    assert response.status_code == 400
    assert response.json() == {"error": "invalid_request"}
    assert "first objective" not in response.text
    assert "unknown" not in response.text


def test_run_routes_publish_catalog_snapshot_and_safe_active_conflict(tmp_path) -> None:
    client, manager = studio_client(tmp_path)

    catalog = client.get("/api/catalog")
    created = client.post("/api/runs", headers=ACTION_HEADERS, content=request_body())
    conflict = client.post("/api/runs", headers=ACTION_HEADERS, content=request_body())
    snapshot = client.get("/api/runs/launch-001")

    assert catalog.status_code == 200
    assert len(catalog.json()["stakeholders"]) == 8
    assert created.status_code == 201
    assert created.json() == {
        "run_alias": "launch-001",
        "workspace_url": "/runs/launch-001",
    }
    assert conflict.status_code == 409
    assert conflict.json() == {"error": "active_run", "run_alias": "launch-001"}
    assert snapshot.status_code == 200
    assert snapshot.json()["run_alias"] == "launch-001"
    manager.join("launch-001", timeout=20)


def test_security_headers_and_disabled_docs_apply_to_every_response(tmp_path) -> None:
    client, _manager = studio_client(tmp_path)

    for response in (client.get("/"), client.get("/openapi.json"), client.delete("/")):
        assert response.headers["cache-control"] == "no-store"
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["permissions-policy"]
        assert response.headers["x-frame-options"] == "DENY"

    assert client.get("/openapi.json").json() == {"error": "not_found"}
    assert client.get("/api/runs").json() == {"error": "method_not_allowed"}


def test_head_is_supported_for_pages_api_static_and_final_attachments(tmp_path) -> None:
    client, manager = studio_client(tmp_path)

    assert client.head("/").status_code == 200
    assert client.head("/api/catalog").status_code == 200
    assert client.head("/studio-static/coordination-studio.js").status_code == 200
    created = client.post("/api/runs", headers=ACTION_HEADERS, content=request_body())
    manager.join(created.json()["run_alias"], timeout=20)

    for path in (
        "/runs/launch-001",
        "/api/runs/launch-001",
        "/api/runs/launch-001/evidence.json",
        "/api/runs/launch-001/events.csv",
    ):
        response = client.head(path)
        assert response.status_code == 200
        assert response.content == b""


@pytest.mark.parametrize("failure_source", ["route", "render"])
def test_outer_exception_envelope_is_fixed_non_reflective_and_keeps_safe_headers(
    tmp_path, monkeypatch, caplog, failure_source
) -> None:
    manager = StudioRunManager(workspace_root=tmp_path)
    app = create_coordination_studio_app(manager, action_token="test-action-token")

    def fail_route():
        raise RuntimeError("PRIVATE route credential and request body")

    def fail_render(*_args, **_kwargs):
        raise RuntimeError("PRIVATE render credential and request body")

    if failure_source == "route":
        app.add_api_route("/explode", fail_route, methods=["GET"])
        path = "/explode"
    else:
        monkeypatch.setattr(
            "humanwire.studio_app.Jinja2Templates.TemplateResponse", fail_render
        )
        path = "/"
    client = TestClient(
        app,
        base_url="http://127.0.0.1",
        raise_server_exceptions=False,
    )

    response = client.get(path)

    assert response.status_code == 500
    assert response.json() == {"error": "request_failed"}
    assert "PRIVATE" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["permissions-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert "content-disposition" not in response.headers
    assert "PRIVATE" not in caplog.text


def test_public_demo_has_no_studio_api_or_controller() -> None:
    client = TestClient(create_demo_app())

    assert client.get("/api/runs").status_code == 404
    assert client.get("/studio-static/coordination-studio.js").status_code == 404


def test_local_studio_serves_only_its_controller_and_stylesheet(tmp_path) -> None:
    client, _manager = studio_client(tmp_path)

    assert client.get("/studio-static/coordination-studio.js").status_code == 200
    assert client.get("/studio-static/coordination-studio.css").status_code == 200


def test_completed_json_and_csv_are_attachments_with_event_parity(tmp_path) -> None:
    client, manager = studio_client(tmp_path)
    created = client.post("/api/runs", headers=ACTION_HEADERS, content=request_body())
    run_alias = created.json()["run_alias"]
    manager.join(run_alias, timeout=20)

    json_response = client.get(f"/api/runs/{run_alias}/evidence.json")
    csv_response = client.get(f"/api/runs/{run_alias}/events.csv")

    assert json_response.status_code == csv_response.status_code == 200
    assert json_response.headers["content-disposition"] == (
        'attachment; filename="launch-001-evidence.json"'
    )
    assert csv_response.headers["content-disposition"] == (
        'attachment; filename="launch-001-events.csv"'
    )
    json_events = json_response.json()["events"]
    csv_events = list(csv.DictReader(io.StringIO(csv_response.text)))
    assert len(json_events) == len(csv_events)
    assert [str(item["timeline_ordinal"]) for item in json_events] == [
        item["timeline_ordinal"] for item in csv_events
    ]
    assert [item["effect"] for item in json_events] == [item["effect"] for item in csv_events]
    for row in csv_events:
        assert all("\r" not in value and "\n" not in value and "\t" not in value for value in row.values())

    exported = json_response.text + csv_response.text
    scenario = build_coordination_scenario(
        launch_request(), seed=0, scenario_id="launch-001"
    )
    for persona in scenario.personas:
        assert persona.email not in exported
        assert all(fact not in exported for fact in persona.private_facts)
    for forbidden in (
        "sender_address",
        "route_id",
        "conversation_id",
        "message_id",
        "assignment_id",
        "identity_seed",
        "proof_class",
        "actor_type",
        "identity_source",
        "transport",
        "human_attested",
        "live_provider_verified",
        "trace_sha256",
    ):
        assert forbidden not in exported
    assert re.search(r"\bHW-[A-F0-9]{8}\b", exported, re.IGNORECASE) is None
    assert re.search(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        exported,
        re.IGNORECASE,
    ) is None


def test_attachments_require_final_binding_and_never_reflect_alias(tmp_path) -> None:
    client, manager = studio_client(tmp_path)
    created = client.post("/api/runs", headers=ACTION_HEADERS, content=request_body())
    alias = created.json()["run_alias"]

    pending = client.get(f"/api/runs/{alias}/evidence.json")
    unknown = client.get("/api/runs/not-present/events.csv")

    assert pending.status_code == 409
    assert pending.json() == {"error": "final_evidence_unavailable"}
    assert unknown.status_code == 404
    assert unknown.json() == {"error": "not_found"}
    assert all("content-disposition" not in item.headers for item in (pending, unknown))
    manager.join(alias, timeout=20)


def test_validate_studio_host_is_literal_only() -> None:
    assert validate_studio_host("127.0.0.1") == "127.0.0.1"
    for value in ("localhost", "::1", "127.0.0.1:8766"):
        with pytest.raises(ValueError):
            validate_studio_host(value)


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r", "\n"])
def test_every_csv_cell_neutralizes_formula_and_row_controls(prefix) -> None:
    rendered = _csv_cell(f"{prefix}SUM(A1:A2)\r\nnext\tcolumn")

    assert rendered.startswith("'")
    assert "\r" not in rendered
    assert "\n" not in rendered
    assert "\t" not in rendered


def test_model_unavailable_error_contains_no_configuration_values(tmp_path) -> None:
    def unavailable():
        raise ModelModeUnavailable("model_runtime_unavailable")

    manager = StudioRunManager(
        workspace_root=tmp_path,
        alias_factory=iter(["launch-001"]).__next__,
        model_factory_builder=unavailable,
    )
    client = TestClient(
        create_coordination_studio_app(manager, action_token="test-action-token"),
        base_url="http://127.0.0.1",
    )

    response = client.post(
        "/api/runs",
        headers=ACTION_HEADERS,
        content=request_body(agent_mode="model_assisted"),
    )

    assert response.status_code == 409
    assert response.json() == {"error": "model_unavailable"}
    assert "model_runtime" not in response.text
    assert manager.list_runs() == ()


def test_unexpected_manager_failure_is_fixed_and_non_reflective(tmp_path, monkeypatch) -> None:
    client, manager = studio_client(tmp_path)

    class PrivateManagerFailure(Exception):
        pass

    def fail(_request):
        raise PrivateManagerFailure("PRIVATE api-key and request objective")

    monkeypatch.setattr(manager, "create_run", fail)

    response = client.post("/api/runs", headers=ACTION_HEADERS, content=request_body())

    assert response.status_code == 500
    assert response.json() == {"error": "run_unavailable"}
    assert "PRIVATE" not in response.text
    assert "objective" not in response.text


def test_studio_cli_builds_idle_manager_and_prints_only_loopback_url(
    tmp_path, monkeypatch, capsys
) -> None:
    built = []
    app = object()
    calls = []
    monkeypatch.setattr("humanwire.studio_app.StudioRunManager", lambda **kwargs: built.append(kwargs) or SimpleNamespace(list_runs=lambda: ()))
    monkeypatch.setattr("humanwire.studio_app.create_coordination_studio_app", lambda manager, action_token: app)
    monkeypatch.setattr("humanwire.studio_app.secrets.token_urlsafe", lambda: "private-action-token")
    monkeypatch.setattr("humanwire.studio_app.uvicorn.run", lambda selected, *, host, port: calls.append((selected, host, port)))

    result = run_coordination_studio(
        StudioOptions(workspace_root=tmp_path, port=8877, seed=4, step_delay_ms=0)
    )

    assert result == 0
    assert built == [
        {
            "workspace_root": Path(tmp_path).resolve(),
            "seed": 4,
            "step_delay_ms": 0,
            "max_decision_workers": 4,
        }
    ]
    assert calls == [(app, "127.0.0.1", 8877)]
    assert capsys.readouterr().out.splitlines() == ["studio_url=http://127.0.0.1:8877"]


def test_main_delegates_studio_without_loading_settings(tmp_path, monkeypatch) -> None:
    options = []
    monkeypatch.setattr(cli, "Settings", lambda: pytest.fail("studio must not load Settings"))
    monkeypatch.setattr(
        "humanwire.studio_app.run_coordination_studio",
        lambda selected: options.append(selected) or 0,
    )

    result = cli.main(
        [
            "studio",
            "--workspace-root",
            str(tmp_path),
            "--port",
            "8878",
            "--seed",
            "9",
            "--step-delay-ms",
            "12",
            "--max-decision-workers",
            "2",
        ]
    )

    assert result == 0
    assert options == [
        StudioOptions(
            workspace_root=tmp_path,
            port=8878,
            seed=9,
            step_delay_ms=12,
            max_decision_workers=2,
        )
    ]
