from __future__ import annotations

import csv
import io
import json
import re
import subprocess
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from humanwire.demo import create_demo_app
from humanwire.synthetic import (
    SyntheticTranscript,
    default_synthetic_scenario,
    generate_scenario,
    load_transcript,
)
from humanwire.synthetic_progress import (
    RepositoryProgressObserver,
    SyntheticAggregateCounts,
    SyntheticProgressEvent,
    SyntheticProgressStore,
    SyntheticRunState,
    SyntheticRuntimeStatus,
    initial_progress,
)
from humanwire.synthetic_viewer import (
    create_synthetic_viewer_app,
    validate_viewer_host,
)


def _event(
    ordinal: int,
    *,
    source: str = "HumanWire",
    destination: str = "Decision Room",
    data_point: str = "Mandate created",
    highlight_target: str = "origin",
    channel: str | None = None,
    direction: str | None = None,
) -> SyntheticProgressEvent:
    return SyntheticProgressEvent(
        timeline_ordinal=ordinal,
        persisted_ordinal=ordinal,
        created_at=datetime(2026, 8, 13, 12, ordinal, tzinfo=UTC),
        story="primary",
        effect="persisted",
        stage="Mandate",
        source=source,
        destination=destination,
        data_point=data_point,
        description=f"Mandate: {data_point}",
        highlight_target=highlight_target,
        channel=channel,
        direction=direction,
    )


@pytest.fixture
def running_viewer_client(tmp_path: Path) -> TestClient:
    scenario = default_synthetic_scenario(seed=31)
    initial = initial_progress(scenario)
    events = (_event(1),)
    running = initial.model_copy(
        update={
            "run_state": SyntheticRunState.RUNNING,
            "runtime_status": SyntheticRuntimeStatus.PERSISTED,
            "saved_event_count": 1,
            "timeline_event_count": 1,
            "current_timeline_ordinal": 1,
            "current_persisted_ordinal": 1,
            "events": events,
            "aggregate_counts": SyntheticAggregateCounts(
                personas=len(initial.personas),
                persisted_events=1,
                inert_attempts=0,
                complete_assignments=0,
                pending_assignments=0,
                terminal_mandates=0,
            ),
        }
    )
    return TestClient(
        create_synthetic_viewer_app(
            SyntheticProgressStore(running), tmp_path / "unfinished-transcript.json"
        )
    )


@pytest.fixture(scope="module")
def completed_viewer_material(tmp_path_factory):
    run_root = tmp_path_factory.mktemp("completed-viewer") / "run"
    transcript_path = run_root / "transcript.json"
    scenario = default_synthetic_scenario(seed=37)
    store = SyntheticProgressStore(initial_progress(scenario))
    observer = RepositoryProgressObserver(store)
    generate_scenario(
        scenario,
        transcript_path,
        run_root,
        progress_observer=observer,
    )
    return store, transcript_path


@pytest.fixture
def completed_viewer_client(completed_viewer_material) -> TestClient:
    store, transcript_path = completed_viewer_material
    return TestClient(create_synthetic_viewer_app(store, transcript_path))


def test_viewer_is_get_only_and_progress_is_no_store(running_viewer_client) -> None:
    progress = running_viewer_client.get("/progress.json")

    assert progress.status_code == 200
    assert progress.headers["cache-control"] == "no-store"
    assert progress.json()["run_state"] == "running"
    for method in ("post", "put", "patch", "delete", "options"):
        response = getattr(running_viewer_client, method)("/")
        assert response.status_code == 405
        assert response.json() == {"detail": "Method not allowed"}
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "client_fixture",
    ("running_viewer_client", "completed_viewer_client"),
)
def test_get_and_head_responses_have_safe_headers_and_head_has_no_body(
    client_fixture: str,
    request: pytest.FixtureRequest,
) -> None:
    client = request.getfixturevalue(client_fixture)
    for path in (
        "/",
        "/progress.json",
        "/evidence.json",
        "/events.csv",
        "/static/styles.css",
        "/viewer-static/synthetic-progress.js",
    ):
        response = client.get(path)
        head = client.head(path)
        expected_status = (
            409
            if client_fixture == "running_viewer_client"
            and path in {"/evidence.json", "/events.csv"}
            else 200
        )

        assert response.status_code == expected_status
        assert head.status_code == expected_status
        assert head.content == b""
        for candidate in (response, head):
            assert candidate.headers["cache-control"] == "no-store"
            assert candidate.headers["x-content-type-options"] == "nosniff"
            assert candidate.headers["referrer-policy"] == "no-referrer"
            assert candidate.headers["permissions-policy"] == (
                "camera=(), microphone=(), geolocation=()"
            )
            assert candidate.headers["content-security-policy"] == (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; object-src 'none'; base-uri 'none'; "
                "frame-ancestors 'none'; form-action 'none'"
            )


@pytest.mark.parametrize(
    "path",
    (
        "/",
        "/progress.json",
        "/evidence.json",
        "/events.csv",
        "/static/styles.css",
        "/viewer-static/synthetic-progress.js",
    ),
)
@pytest.mark.parametrize(
    "client_fixture",
    ("running_viewer_client", "completed_viewer_client"),
)
def test_every_viewer_route_rejects_every_mutation_method(
    path: str,
    client_fixture: str,
    request: pytest.FixtureRequest,
) -> None:
    client = request.getfixturevalue(client_fixture)
    for method in ("post", "put", "patch", "delete", "options"):
        response = getattr(client, method)(path)

        assert response.status_code == 405
        assert response.json() == {"detail": "Method not allowed"}
        assert response.headers["cache-control"] == "no-store"


def test_final_downloads_are_unavailable_until_completion(running_viewer_client) -> None:
    for path in ("/evidence.json", "/events.csv"):
        response = running_viewer_client.get(path)
        head = running_viewer_client.head(path)

        assert response.status_code == 409
        assert response.json() == {"detail": "Final evidence unavailable"}
        assert "content-disposition" not in response.headers
        assert head.status_code == 409
        assert head.content == b""
        assert "content-disposition" not in head.headers


def test_completed_json_and_csv_are_attachments(completed_viewer_client) -> None:
    json_response = completed_viewer_client.get("/evidence.json")
    csv_response = completed_viewer_client.get("/events.csv")

    assert json_response.status_code == 200
    assert csv_response.status_code == 200
    assert json_response.headers["content-disposition"] == (
        'attachment; filename="humanwire-synthetic-evidence.json"'
    )
    assert csv_response.headers["content-disposition"] == (
        'attachment; filename="humanwire-synthetic-events.csv"'
    )
    assert json_response.headers["content-type"].startswith("application/json")
    assert csv_response.headers["content-type"].startswith("text/csv")
    evidence = json.loads(json_response.content)
    assert evidence["provenance"]["transport"] == "fake_caspian"
    assert evidence["schema_version"] == "humanwire.synthetic-evidence/v1"
    assert "actions" not in evidence
    assert "outbound_digests" not in evidence
    assert csv_response.text.startswith(
        "timeline_ordinal,persisted_ordinal,effect,created_at,story,stage,source,"
        "destination,channel,direction,data_point"
    )
    for path in ("/evidence.json", "/events.csv"):
        head = completed_viewer_client.head(path)
        assert head.status_code == 200
        assert head.content == b""
        assert head.headers["content-disposition"].startswith("attachment; filename=")


def test_private_transcript_binding_never_reaches_any_http_surface(
    completed_viewer_material,
) -> None:
    store, transcript_path = completed_viewer_material
    transcript_sha256 = load_transcript(transcript_path).digest
    client = TestClient(create_synthetic_viewer_app(store, transcript_path))

    responses = [
        client.get(path)
        for path in (
            "/",
            "/progress.json",
            "/evidence.json",
            "/events.csv",
            "/missing",
        )
    ]
    public_surface = "".join(
        response.text + json.dumps(dict(response.headers)) for response in responses
    )

    assert transcript_sha256 not in public_surface
    assert "transcript_sha256" not in public_surface


def test_completed_csv_has_row_for_row_timeline_and_provenance_parity(
    completed_viewer_client,
) -> None:
    json_response = completed_viewer_client.get("/evidence.json")
    csv_response = completed_viewer_client.get("/events.csv")
    evidence = json_response.json()
    reader = csv.DictReader(io.StringIO(csv_response.text))
    rows = list(reader)

    assert json_response.status_code == csv_response.status_code == 200
    assert reader.fieldnames == [
        "timeline_ordinal",
        "persisted_ordinal",
        "effect",
        "created_at",
        "story",
        "stage",
        "source",
        "destination",
        "channel",
        "direction",
        "data_point",
        "proof_class",
        "actor_type",
        "identity_source",
        "transport",
        "human_attested",
        "live_provider_verified",
    ]
    assert len(rows) == len(evidence["events"])
    assert any(event["effect"] == "inert_attempt" for event in evidence["events"])
    assert [int(row["timeline_ordinal"]) for row in rows] == list(
        range(1, len(rows) + 1)
    )
    for event, row in zip(evidence["events"], rows, strict=True):
        assert row["timeline_ordinal"] == str(event["timeline_ordinal"])
        assert row["persisted_ordinal"] == (
            "" if event["persisted_ordinal"] is None else str(event["persisted_ordinal"])
        )
        assert row["effect"] == event["effect"]
        assert row["created_at"] == event["created_at"]
        assert row["story"] == event["story"]
        assert row["stage"] == event["stage"]
        assert row["source"] == event["source"]
        assert row["destination"] == event["destination"]
        assert row["channel"] == (event["channel"] or "")
        assert row["direction"] == (event["direction"] or "")
        assert row["data_point"] == event["data_point"]
        assert {key: row[key] for key in reader.fieldnames[11:]} == {
            "proof_class": "synthetic_multi_persona",
            "actor_type": "simulated_persona",
            "identity_source": "synthetic_fixture",
            "transport": "fake_caspian",
            "human_attested": "false",
            "live_provider_verified": "false",
        }


def test_progress_exposes_only_allowlisted_route_metadata(completed_viewer_client) -> None:
    """Break caught: the visual route has to guess channel or direction from prose."""
    events = completed_viewer_client.get("/progress.json").json()["events"]
    persisted = [event for event in events if event["effect"] == "persisted"]

    channels = {event["channel"] for event in persisted if event["channel"]}
    assert {"Email", "Telegram"} <= channels
    assert channels <= {"Email", "Telegram", "Internal"}
    assert {event["direction"] for event in persisted if event["direction"]} <= {
        "Upward",
        "Downward",
        "Lateral",
        "External",
    }
    assert any(event["direction"] for event in persisted)


def test_downloads_fail_closed_for_invalid_or_mismatched_transcript(
    completed_viewer_material, tmp_path: Path
) -> None:
    store, transcript_path = completed_viewer_material
    invalid = tmp_path / "private-path" / "transcript.json"
    invalid.parent.mkdir()
    invalid.write_text('{"secret":"provider-body"}', encoding="utf-8")
    invalid_client = TestClient(create_synthetic_viewer_app(store, invalid))
    mismatch = store.snapshot()
    mismatch._identity_seed = 99
    mismatch_client = TestClient(
        create_synthetic_viewer_app(SyntheticProgressStore(mismatch), transcript_path)
    )

    for client in (invalid_client, mismatch_client):
        for path in ("/evidence.json", "/events.csv"):
            response = client.get(path)
            assert response.status_code == 409
            assert response.json() == {"detail": "Final evidence unavailable"}
            assert "content-disposition" not in response.headers
            assert "provider-body" not in response.text
            assert "private-path" not in response.text


def test_downloads_reject_a_different_valid_same_seed_transcript(
    completed_viewer_material, tmp_path: Path
) -> None:
    store, transcript_path = completed_viewer_material
    transcript = load_transcript(transcript_path)
    altered_action = transcript.actions[0].model_copy(
        update={"content": f"{transcript.actions[0].content} altered"}
    )
    altered = SyntheticTranscript.create(
        scenario=transcript.scenario,
        outbound_digests=transcript.outbound_digests,
        actions=[altered_action, *transcript.actions[1:]],
    )
    assert altered.digest != transcript.digest
    altered_path = tmp_path / "same-seed-valid-transcript.json"
    altered_path.write_text(altered.model_dump_json(), encoding="utf-8")
    client = TestClient(create_synthetic_viewer_app(store, altered_path))

    for path in ("/evidence.json", "/events.csv"):
        response = client.get(path)
        assert response.status_code == 409
        assert response.json() == {"detail": "Final evidence unavailable"}
        assert "content-disposition" not in response.headers
        assert altered.digest not in response.text


def test_csv_neutralizes_formula_cells_and_exports_only_allowlisted_fields(
    completed_viewer_material,
) -> None:
    store, transcript_path = completed_viewer_material
    completed = store.snapshot()
    first = completed.events[0].model_copy(
        update={"source": "=2+2", "destination": "+2", "data_point": "-2"}
    )
    second = completed.events[1].model_copy(
        update={"source": "@SUM(A1)", "destination": "\tprivate", "data_point": "\rprivate"}
    )
    third = completed.events[2].model_copy(update={"source": "\nprivate"})
    changed = completed.model_copy(update={"events": (first, second, third, *completed.events[3:])})
    changed._identity_seed = completed._identity_seed
    client = TestClient(
        create_synthetic_viewer_app(SyntheticProgressStore(changed), transcript_path)
    )

    response = client.get("/events.csv")
    rows = list(csv.DictReader(io.StringIO(response.text)))

    assert response.status_code == 200
    assert rows[0]["source"] == "'=2+2"
    assert rows[0]["destination"] == "'+2"
    assert rows[0]["data_point"] == "'-2"
    assert rows[1]["source"] == "'@SUM(A1)"
    assert rows[1]["destination"] == "'\tprivate"
    assert rows[1]["data_point"] == "'\rprivate"
    assert rows[2]["source"] == "'\nprivate"
    assert set(rows[0]) == {
        "timeline_ordinal",
        "persisted_ordinal",
        "effect",
        "created_at",
        "story",
        "stage",
        "source",
        "destination",
        "channel",
        "direction",
        "data_point",
        "proof_class",
        "actor_type",
        "identity_source",
        "transport",
        "human_attested",
        "live_provider_verified",
    }


def test_viewer_accepts_only_the_literal_ipv4_loopback_host() -> None:
    assert validate_viewer_host("127.0.0.1") == "127.0.0.1"


@pytest.mark.parametrize(
    "host",
    [
        "::1",
        "localhost",
        "127.0.0.01",
        "127.1",
        "2130706433",
        "0x7f000001",
        "0.0.0.0",
        "192.0.2.10",
        "example.test",
    ],
)
def test_viewer_rejects_every_nonliteral_host_binding(host: str) -> None:
    with pytest.raises(ValueError) as caught:
        validate_viewer_host(host)
    assert str(caught.value) == "synthetic viewer host must be 127.0.0.1"


def test_public_demo_has_no_local_progress_surface() -> None:
    web_client = TestClient(create_demo_app())

    assert web_client.get("/progress.json").status_code == 404
    assert web_client.get("/evidence.json").status_code == 404
    assert web_client.get("/events.csv").status_code == 404
    assert web_client.get("/static/synthetic-progress.js").status_code == 404
    assert web_client.get("/viewer-static/synthetic-progress.js").status_code == 404


def test_progress_controller_is_served_only_by_the_local_viewer(
    completed_viewer_client,
) -> None:
    """Break caught: the public demo exposes the local progress controller asset."""
    response = completed_viewer_client.get("/viewer-static/synthetic-progress.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert "data-synthetic-viewer" in response.text


def _css_rule_selectors(source: str) -> list[str]:
    def split_selector_list(header: str) -> list[str]:
        selectors: list[str] = []
        token_start = 0
        parenthesis_depth = 0
        for index, character in enumerate(header):
            if character == "(":
                parenthesis_depth += 1
            elif character == ")":
                parenthesis_depth -= 1
            elif character == "," and parenthesis_depth == 0:
                selectors.append(header[token_start:index].strip())
                token_start = index + 1
        selectors.append(header[token_start:].strip())
        return [selector for selector in selectors if selector]

    selectors: list[str] = []
    token_start = 0
    for index, character in enumerate(source):
        if character == ";" or character == "}":
            token_start = index + 1
        elif character == "{":
            header = source[token_start:index].strip()
            if header and not header.startswith("@"):
                selectors.extend(split_selector_list(header))
            token_start = index + 1
    return selectors


class _FocusableControlParser(HTMLParser):
    """Collect controls that can participate in the viewer's focus order."""

    def __init__(self) -> None:
        super().__init__()
        self.controls: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if (
            (tag == "a" and "href" in attributes)
            or tag in {"button", "input", "select", "textarea"}
            or "tabindex" in attributes
        ):
            self.controls.append((tag, attributes))


def test_viewer_css_rules_cannot_match_the_public_reach_page() -> None:
    public_client = TestClient(create_demo_app())
    reach = public_client.get("/mandates/HW-2411/reach")
    styles = public_client.get("/static/styles.css").text
    viewer_styles = styles.split("/* Loopback-only synthetic progress viewer */", 1)[1]

    assert 'class="reach-page' in reach.text
    assert "synthetic-progress-page" not in reach.text
    assert "synthetic-viewer-body" not in reach.text
    selectors = _css_rule_selectors(viewer_styles)
    assert selectors
    assert all(
        ".synthetic-progress-page" in selector
        or ".synthetic-viewer-body" in selector
        for selector in selectors
    )


def test_viewer_template_is_truthful_accessible_and_download_first(
    running_viewer_client,
) -> None:
    response = running_viewer_client.get("/")
    html = response.text

    assert response.status_code == 200
    assert "Synthetic HumanWire progress" in html
    for label in (
        "proof_class=synthetic_multi_persona",
        "actor_type=simulated_persona",
        "identity_source=synthetic_fixture",
        "transport=fake_caspian",
        "human_attested=false",
        "live_provider_verified=false",
    ):
        assert label in html
    for marker in (
        "data-synthetic-viewer",
        "data-run-mode",
        "data-run-state",
        "data-runtime-status",
        "data-persona-list",
        "data-saved-event-count",
        "data-follow-live",
        "data-replay-list",
        "data-replay-source",
        "data-replay-channel",
        "data-replay-direction",
        "data-replay-destination",
        "data-replay-data-point",
        "data-replay-route",
        "data-flow-journey",
        "data-replay-stage",
        "data-replay-story",
        "data-replay-previous",
        "data-replay-play",
        "data-replay-next",
        'aria-live="polite"',
    ):
        assert marker in html
    assert re.search(r'<a[^>]+href="/evidence.json"[^>]+download[^>]+aria-disabled="true"', html)
    assert re.search(r'<a[^>]+href="/events.csv"[^>]+download[^>]+aria-disabled="true"', html)
    assert '<ol class="replay-events" data-replay-list hidden' in html
    assert 'aria-label="Event route visualization"' in html
    assert '<script src="/viewer-static/synthetic-progress.js" defer></script>' in html


def test_viewer_styles_have_accessible_controls_and_responsive_no_overflow_layout(
    running_viewer_client,
) -> None:
    css = running_viewer_client.get("/static/styles.css").text

    assert re.search(
        r"\.synthetic-progress-page\s*\{[^}]*font-size:\s*14px",
        css,
        re.DOTALL,
    )
    assert re.search(
        r"\.synthetic-personas\s*\{[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)",
        css,
        re.DOTALL,
    )
    assert re.search(
        r"\.synthetic-progress-page (?:button|\.synthetic-download)\b[^}]*min-height:\s*44px",
        css,
        re.DOTALL,
    )
    assert ".synthetic-progress-page :focus-visible" in css
    assert re.search(
        r"\.synthetic-progress-page \.replay-events\[hidden\]\s*\{[^}]*display:\s*none",
        css,
        re.DOTALL,
    )
    mobile_sections = re.findall(r"@media \(max-width: 759px\)(.*?)(?=@media|\Z)", css, re.DOTALL)
    mobile = "\n".join(mobile_sections)
    assert re.search(
        r"\.synthetic-personas\s*\{[^}]*grid-template-columns:\s*1fr", mobile, re.DOTALL
    )
    assert re.search(r"\.replay-route\s*\{[^}]*grid-template-columns:\s*1fr", mobile, re.DOTALL)
    assert ".synthetic-progress-page .replay-route-token" in css
    assert "@keyframes synthetic-route-pulse" in css
    assert ".synthetic-progress-page .replay-journey" in css
    assert re.search(r"\.replay-journey\s*\{[^}]*repeat\(10,", css, re.DOTALL)
    assert ".replay-connector:nth-child(4) .replay-route-token" in css
    assert ".replay-connector:nth-child(6) .replay-route-token" in css
    reduced = re.search(
        r"@media \(prefers-reduced-motion: reduce\)(.*?)(?=@media|\Z)", css, re.DOTALL
    )
    assert reduced is not None
    assert "transition-duration: 0.01ms !important" in reduced.group(1)
    assert "animation-duration: 0.01ms !important" in reduced.group(1)


def test_every_focusable_viewer_control_has_a_44px_target_contract(
    running_viewer_client,
) -> None:
    """Break caught: a focusable link falls below the 44 by 44 target floor."""
    html = running_viewer_client.get("/").text
    css = running_viewer_client.get("/static/styles.css").text
    parser = _FocusableControlParser()
    parser.feed(html)

    assert parser.controls
    assert any(
        tag == "a" and "skip-link" in (attributes.get("class") or "").split()
        for tag, attributes in parser.controls
    )
    rule = re.search(
        r"\.synthetic-viewer-body\s+:is\(([^)]*)\)\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert rule is not None
    selector_list, declarations = rule.groups()
    focusable_kinds = {
        "a[href]"
        if tag == "a" and "href" in attributes
        else tag
        if tag in {"button", "input", "select", "textarea"}
        else "[tabindex]"
        for tag, attributes in parser.controls
    }
    required_focusable_kinds = {
        "a[href]",
        "button",
        "input",
        "select",
        "textarea",
        "[tabindex]",
    }
    assert focusable_kinds <= required_focusable_kinds
    assert all(kind in selector_list for kind in required_focusable_kinds)
    assert re.search(r"\bdisplay:\s*inline-flex\b", declarations)
    assert re.search(r"\bmin-width:\s*44px\b", declarations)
    assert re.search(r"\bmin-height:\s*44px\b", declarations)


def test_progress_controller_exercises_live_manual_playback_and_download_states() -> None:
    script_path = (
        Path(__file__).resolve().parents[2]
        / "src/humanwire/viewer_static/synthetic-progress.js"
    )
    harness = r"""
const fs = require("fs");
const scriptPath = process.argv[1];
const mode = process.argv[2];
const reduced = mode === "reduced";

function expect(actual, expected, message) {
  if (actual !== expected) throw new Error(`${message}: expected ${expected}, got ${actual}`);
}
class ClassList {
  constructor() { this.values = new Set(); }
  add(...values) { values.forEach((value) => this.values.add(value)); }
  remove(...values) { values.forEach((value) => this.values.delete(value)); }
  toggle(value, force) { const next = force === undefined ? !this.values.has(value) : force; next ? this.values.add(value) : this.values.delete(value); return next; }
  contains(value) { return this.values.has(value); }
}
class Element {
  constructor(tag = "div") { this.tagName = tag.toUpperCase(); this.attributes = {}; this.dataset = {}; this.classList = new ClassList(); this.children = []; this.listeners = {}; this._text = ""; }
  get textContent() { return this._text; }
  set textContent(value) { this._text = String(value); this.children = []; }
  set innerHTML(_) { throw new Error("innerHTML must not be used"); }
  setAttribute(name, value) { this.attributes[name] = String(value); if (name.startsWith("data-")) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  getAttribute(name) { return this.attributes[name]; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(name, listener) { (this.listeners[name] ||= []).push(listener); }
  click() { (this.listeners.click || []).forEach((listener) => listener({ preventDefault() { this.prevented = true; } })); }
  scrollIntoView() {}
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    const matches = (node) => selector === "[data-highlight-target]" ? node.attributes["data-highlight-target"] !== undefined : false;
    const found = [];
    const visit = (node) => { if (matches(node)) found.push(node); node.children.forEach(visit); };
    this.children.forEach(visit); return found;
  }
}
const selectors = ["[data-synthetic-viewer]", "[data-run-status]", "[data-run-mode]", "[data-run-state]", "[data-runtime-status]", "[data-active-persona]", "[data-saved-event-count]", "[data-persona-list]", "[data-replay-list]", "[data-follow-live]", "[data-replay-previous]", "[data-replay-play]", "[data-replay-next]", "[data-replay-progress]", "[data-replay-route]", "[data-flow-journey]", "[data-replay-source]", "[data-replay-channel]", "[data-replay-direction]", "[data-replay-destination]", "[data-replay-data-point]", "[data-replay-stage]", "[data-replay-story]", "[data-replay-description]", "[data-replay-time]", "[data-replay-live]", "[data-evidence-json]", "[data-evidence-csv]"];
const nodes = Object.fromEntries(selectors.map((selector) => [selector, new Element(selector.includes("evidence") ? "a" : "div")]));
const root = nodes["[data-synthetic-viewer]"];
const origin = new Element(); origin.setAttribute("data-highlight-target", "origin"); root.append(origin, nodes["[data-persona-list]"]);
nodes["[data-follow-live]"].setAttribute("aria-pressed", "true");
nodes["[data-replay-play]"].setAttribute("aria-pressed", "false");
nodes["[data-evidence-json]"].setAttribute("aria-disabled", "true");
nodes["[data-evidence-csv]"].setAttribute("aria-disabled", "true");
const documentListeners = {};
global.document = {
  visibilityState: "visible",
  querySelector(selector) { return nodes[selector] || null; },
  querySelectorAll(selector) { return root.querySelectorAll(selector); },
  createElement(tag) { return new Element(tag); },
  addEventListener(name, listener) { (documentListeners[name] ||= []).push(listener); },
};
const intervals = new Map(); let intervalId = 0;
const snapshots = [
  { schema_version: "humanwire.synthetic-progress/v1", mode: "deterministic", run_state: "running", runtime_status: "persisted", active_persona_label: "Ada Stone", active_contract: "quick_response", saved_event_count: 1, events: [{ timeline_ordinal: 1, persisted_ordinal: 1, effect: "persisted", created_at: "2026-08-13T12:01:00Z", story: "primary", stage: "Mandate", source: "HumanWire", destination: "Decision Room", channel: null, direction: null, data_point: "Mandate created", description: "First saved event", highlight_target: "origin" }], personas: [{ ordinal: 1, display_name: "Ada Stone", role: "Program owner", contract: "quick_response", status: "pending", progress_current: 0, progress_total: 1 }] },
  { schema_version: "humanwire.synthetic-progress/v1", mode: "model_assisted", run_state: "complete", runtime_status: "persisted", active_persona_label: null, active_contract: null, saved_event_count: 2, final_trace_sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", events: [{ timeline_ordinal: 1, persisted_ordinal: 1, effect: "persisted", created_at: "2026-08-13T12:01:00Z", story: "primary", stage: "Mandate", source: "HumanWire", destination: "Decision Room", channel: null, direction: null, data_point: "Mandate created", description: "First saved event", highlight_target: "origin" }, { timeline_ordinal: 2, persisted_ordinal: 2, effect: "persisted", created_at: "2026-08-13T12:02:00Z", story: "primary", stage: "Outreach", source: "HumanWire", destination: "Ada Stone", channel: "Email", direction: "Downward", data_point: "Outreach sent", description: "Second saved event", highlight_target: "persona-1" }], personas: [{ ordinal: 1, display_name: "Ada Stone", role: "Program owner", contract: "quick_response", status: "complete", progress_current: 1, progress_total: 1 }] },
  { schema_version: "humanwire.synthetic-progress/v1", mode: "model_assisted", run_state: "complete", runtime_status: "persisted", active_persona_label: null, active_contract: null, saved_event_count: 3, final_trace_sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", events: [{ timeline_ordinal: 1, persisted_ordinal: 1, effect: "persisted", created_at: "2026-08-13T12:01:00Z", story: "primary", stage: "Mandate", source: "HumanWire", destination: "Decision Room", channel: null, direction: null, data_point: "Mandate created", description: "First saved event", highlight_target: "origin" }, { timeline_ordinal: 2, persisted_ordinal: 2, effect: "persisted", created_at: "2026-08-13T12:02:00Z", story: "primary", stage: "Outreach", source: "HumanWire", destination: "Ada Stone", channel: "Email", direction: "Downward", data_point: "Outreach sent", description: "Second saved event", highlight_target: "persona-1" }, { timeline_ordinal: 3, persisted_ordinal: 3, effect: "persisted", created_at: "2026-08-13T12:03:00Z", story: "primary", stage: "Saved event", source: "HumanWire", destination: "Decision Room", channel: null, direction: null, data_point: "No public data point", description: "Third saved event", highlight_target: "none" }], personas: [{ ordinal: 1, display_name: "Ada Stone", role: "Program owner", contract: "quick_response", status: "complete", progress_current: 1, progress_total: 1 }] }
];
let fetchCount = 0;
global.fetch = async (url, options) => { expect(url, "/progress.json", "poll URL"); expect(options.cache, "no-store", "poll cache"); const value = snapshots[Math.min(fetchCount, snapshots.length - 1)]; fetchCount += 1; return { ok: true, json: async () => value }; };
global.window = {
  matchMedia() { return { matches: reduced }; },
  setInterval(listener) { const id = ++intervalId; intervals.set(id, listener); return id; },
  clearInterval(id) { intervals.delete(id); },
  setTimeout(listener) { listener(); return 1; },
  clearTimeout() {},
};
const dispatch = (name) => (documentListeners[name] || []).forEach((listener) => listener());
const flush = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); };

(async () => {
  eval(fs.readFileSync(scriptPath, "utf8"));
  dispatch("DOMContentLoaded"); await flush();
  expect(fetchCount, 1, "initial visible poll");
  expect(nodes["[data-run-status]"].textContent, "Running · persisted · 1 saved event", "progress copy");
  expect(nodes["[data-replay-source]"].textContent, "HumanWire", "first From");
  expect(nodes["[data-replay-channel]"].textContent, "Not recorded", "missing channel stays unknown");
  expect(nodes["[data-replay-destination]"].textContent, "Decision Room", "first To");
  expect(nodes["[data-replay-data-point]"].textContent, "Mandate created", "first Generated");
  expect(nodes["[data-replay-stage]"].textContent, "Mandate", "first stage");
  expect(nodes["[data-replay-story]"].textContent, "Primary story", "first story");
  expect(nodes["[data-flow-journey]"].children.length, 10, "lifecycle plus neutral saved stage");
  expect(nodes["[data-flow-journey]"].children.filter((node) => node.classList.contains("is-current")).length, 1, "one current stage");
  expect(nodes["[data-replay-route]"].classList.contains(reduced ? "is-static" : "is-animated"), true, "route motion mode");
  expect(nodes["[data-evidence-json]"].getAttribute("aria-disabled"), "true", "JSON disabled while running");
  expect(root.querySelectorAll("[data-highlight-target]").filter((node) => node.classList.contains("is-replay-current")).length, 1, "one initial highlight");
  const poll = [...intervals.values()][0]; await poll(); await flush();
  expect(nodes["[data-run-status]"].textContent, "Complete · persisted · 2 saved events", "completed progress copy");
  expect(nodes["[data-replay-destination]"].textContent, "Ada Stone", "Follow Live newest To");
  expect(nodes["[data-replay-channel]"].textContent, "Email", "Follow Live channel");
  expect(nodes["[data-replay-route]"].dataset.direction, "downward", "safe route direction");
  expect(nodes["[data-replay-data-point]"].textContent, "Outreach sent", "Follow Live newest Generated");
  expect(nodes["[data-flow-journey]"].children.filter((node) => node.classList.contains("is-current"))[0].dataset.flowStage, "Outreach", "journey follows selected event");
  expect(nodes["[data-evidence-json]"].getAttribute("aria-disabled"), undefined, "JSON enabled after completion");
  expect(nodes["[data-evidence-csv]"].getAttribute("aria-disabled"), undefined, "CSV enabled after completion");
  await poll(); await flush();
  expect(nodes["[data-replay-progress]"].textContent, "Event 3 of 3", "Follow Live receives neutral saved event");
  expect(nodes["[data-replay-channel]"].textContent, "Not recorded", "neutral event does not invent an internal route");
  expect(nodes["[data-flow-journey]"].children.filter((node) => node.classList.contains("is-current"))[0].dataset.flowStage, "Saved event", "neutral saved event has a current journey marker");
  nodes["[data-replay-previous]"].click();
  expect(nodes["[data-follow-live]"].getAttribute("aria-pressed"), "false", "manual Previous disables Follow Live");
  expect(nodes["[data-replay-destination]"].textContent, "Ada Stone", "Previous To");
  nodes["[data-replay-next]"].click();
  expect(nodes["[data-replay-live]"].textContent, "Event 3 of 3: HumanWire via Not recorded to Decision Room; saved No public data point at Saved event stage. Third saved event", "polite replay announcement");
  expect(root.querySelectorAll("[data-highlight-target]").filter((node) => node.classList.contains("is-replay-current")).length, 0, "neutral event invents no persona highlight");
  nodes["[data-follow-live]"].click();
  expect(nodes["[data-follow-live]"].getAttribute("aria-pressed"), "true", "Follow Live can resume");
  nodes["[data-replay-previous]"].click();
  nodes["[data-replay-previous]"].click();
  nodes["[data-replay-play]"].click();
  expect(nodes["[data-replay-play]"].getAttribute("aria-pressed"), "true", "Play advances events in every motion mode");
  expect(nodes["[data-replay-route]"].classList.contains("is-playing"), true, "route enters playing state");
  const playback = [...intervals.values()][intervals.size - 1]; playback();
  expect(nodes["[data-replay-data-point]"].textContent, "Outreach sent", "playback advances the visible route");
  nodes["[data-replay-play]"].click();
  expect(nodes["[data-replay-play]"].getAttribute("aria-pressed"), "false", "Pause stops playback");
  expect(nodes["[data-replay-route]"].classList.contains("is-playing"), false, "Pause stops route playback state");
  expect(nodes["[data-replay-route]"].classList.contains("is-traversing"), false, "Pause clears the route pulse");
  await poll(); await flush();
  expect(nodes["[data-replay-route]"].classList.contains("is-traversing"), false, "an unchanged poll cannot restart a paused pulse");
  nodes["[data-replay-play]"].click();
  const visibleFetches = fetchCount;
  document.visibilityState = "hidden"; dispatch("visibilitychange");
  expect(nodes["[data-replay-play]"].getAttribute("aria-pressed"), "false", "hidden page pauses playback");
  expect(intervals.size, 0, "hidden page clears polling and playback");
  await flush(); expect(fetchCount, visibleFetches, "hidden page does not poll");
})().catch((error) => { console.error(error.stack); process.exitCode = 1; });
"""

    for mode in ("normal", "reduced"):
        result = subprocess.run(
            ["node", "-e", harness, str(script_path), mode],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
