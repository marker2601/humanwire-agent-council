import html
import json
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from humanwire.studio_app import create_coordination_studio_app
from humanwire.studio_projection import create_studio_progress
from humanwire.studio_run import StudioRunManager
from humanwire.synthetic import build_coordination_scenario, generate_scenario

from .studio_fixtures import launch_request

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "src/humanwire/templates/coordination_studio.html"
CSS = ROOT / "src/humanwire/studio_static/coordination-studio.css"
SCRIPT = ROOT / "src/humanwire/studio_static/coordination-studio.js"
FIXTURES = ROOT / "tests/humanwire/fixtures/studio-snapshots.json"
HOSTILE_HARNESS = ROOT / "tests/humanwire/studio_frontend_hostile_harness.js"


def chromium_executable() -> Path | None:
    candidates = (
        shutil.which("chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    )
    return next((Path(item) for item in candidates if item and Path(item).is_file()), None)


def css_block(source: str, marker: str, *, start: int = 0) -> str:
    marker_index = source.index(marker, start)
    opening = source.index("{", marker_index)
    depth = 1
    for index in range(opening + 1, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1 : index]
    raise AssertionError(f"unterminated CSS block for {marker}")


def studio_client(tmp_path) -> TestClient:
    manager = StudioRunManager(
        workspace_root=tmp_path,
        alias_factory=iter(["launch-001"]).__next__,
        step_delay_ms=0,
    )
    app = create_coordination_studio_app(manager, action_token="test-token")
    return TestClient(app, base_url="http://127.0.0.1")


def test_studio_template_is_product_not_proof_dashboard(tmp_path) -> None:
    response = studio_client(tmp_path).get("/")
    assert response.status_code == 200
    required = (
        "HumanWire",
        "Start a coordination",
        "What needs to be decided?",
        "Who are you in this coordination?",
        "Stakeholders",
        "Start coordination",
        "Brief",
        "Outreach",
        "Resolve",
        "Approve",
        "Schedule",
        "Conversation",
        "Data trail",
    )
    for item in required:
        assert item in response.text
    visible_text = re.sub(r"<[^>]+>", " ", response.text)
    for forbidden in (
        "Synthetic HumanWire progress",
        "local simulation viewer",
        "proof_class",
        "actor_type",
        "fake_caspian",
        "simulated_persona",
    ):
        assert forbidden not in visible_text
    assert 'data-studio-state="composer"' in response.text
    assert "data-flow-canvas" in response.text
    assert 'aria-live="polite"' in response.text


def test_composer_and_workspace_expose_the_complete_accessible_product_shell(
    tmp_path,
) -> None:
    html = studio_client(tmp_path).get("/").text
    assert 'data-studio-state="composer"' in html
    assert re.search(r'<section[^>]+data-studio-state="workspace"[^>]+hidden', html)
    assert len(re.findall(r"<h1(?:\s|>)", html)) == 2
    assert len(re.findall(r'<input[^>]+name="requester_role"', html)) == 4
    participants = re.findall(r'<input[^>]+name="participant_ids"[^>]*>', html)
    assert len(participants) == 8
    assert sum(" checked" in item for item in participants) == 7
    assert "data-stakeholder-count" in html
    assert re.search(r"Elena Torres.*?Review and approval", html, re.DOTALL)
    assert len(re.findall(r"data-template-id=", html)) == 3
    assert len(re.findall(r"data-sequence-stage", html)) == 5
    assert len(re.findall(r"data-lifecycle-stage", html)) == 5
    assert len(re.findall(r"data-start-coordination", html)) == 1
    assert 'role="tablist"' in html
    assert set(re.findall(r'role="tab"[^>]+aria-controls="([^"]+)"', html)) == {
        "conversation-panel",
        "data-panel",
    }
    assert re.search(r"<button[^>]+data-download-json[^>]+disabled", html)
    assert re.search(r"<button[^>]+data-download-csv[^>]+disabled", html)


def test_action_token_is_rendered_only_in_the_meta_boundary(tmp_path) -> None:
    response = studio_client(tmp_path).get("/")
    visible_text = re.sub(r"<[^>]+>", " ", response.text)
    assert "test-token" not in visible_text
    assert re.search(
        r'<meta\s+name="humanwire-action-token"\s+content="test-token">',
        response.text,
    )
    assert response.text.count("test-token") == 1


def test_studio_css_is_scoped_responsive_and_accessible() -> None:
    css = CSS.read_text(encoding="utf-8")
    css = re.sub(
        r"@keyframes\s+[^\{]+\{(?:[^{}]|\{[^{}]*\})*\}",
        "",
        css,
    )
    css_without_at_rules = re.sub(r"@(?:media|keyframes)[^{]+\{", "", css)
    selectors = re.findall(r"(?:^|})\s*([^@}{][^{]*)\{", css_without_at_rules)
    for group in selectors:
        for selector in group.split(","):
            normalized = selector.strip().lstrip("}").strip()
            assert normalized.startswith(".coordination-studio-page")
    assert "280px" in css and "360px" in css
    assert "@media (max-width: 759px)" in css
    assert "@media (max-width: 479px)" in css
    assert "prefers-reduced-motion: reduce" in css
    assert ":focus-visible" in css and "outline: 2px" in css
    assert "min-height: 44px" in css and "min-width: 44px" in css
    for size in re.findall(r"font-size:\s*([0-9.]+)px", css):
        assert float(size) >= 14


def test_all_effective_targets_and_mobile_completion_actions_remain_usable() -> None:
    css = CSS.read_text(encoding="utf-8")
    requester = css_block(css, ".coordination-studio-page__requester-cards label {")
    stakeholder = css_block(css, ".coordination-studio-page__stakeholder-list label {")
    compact = css_block(css, ".coordination-studio-page__compact-fieldset label,\n")
    replay_buttons = css_block(css, ".coordination-studio-page__replay-bar button {")
    for body in (requester, stakeholder, compact, replay_buttons):
        assert re.search(r"min-height:\s*(?:4[4-9]|[5-9][0-9])px", body)

    assert ":has(input:focus-visible)" in css
    tablet = css_block(css, "@media (max-width: 759px)")
    phone = css_block(css, "@media (max-width: 479px)")
    tablet_downloads = css_block(tablet, ".coordination-studio-page__download-controls")
    tablet_replay = css_block(tablet, ".coordination-studio-page__replay-bar")
    assert "display: none" not in tablet_downloads
    assert re.search(r"display:\s*(?:flex|grid)", tablet_downloads)
    assert "flex-wrap: wrap" in tablet_downloads or "grid-template" in tablet_downloads
    assert "display: none" not in tablet_replay
    assert not re.search(
        r"\[data-pause-visuals\][^{]*\{[^}]*display:\s*none",
        phone,
        re.DOTALL,
    )


def test_hostile_async_reset_catalog_graph_and_mobile_contract() -> None:
    for mode in ("normal", "reduced", "mobile"):
        result = subprocess.run(
            ["node", str(HOSTILE_HARNESS), str(SCRIPT), str(FIXTURES), mode],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{mode}: {result.stderr}"


def test_real_completed_graph_rendered_content_has_five_pixel_clearance(
    tmp_path,
) -> None:
    """Break caught: real SVG text escapes its card and collides with another node."""
    chromium = chromium_executable()
    if chromium is None:
        pytest.skip("Chromium is unavailable; rendered SVG bounds require a browser engine")

    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="render-001")
    store, observer = create_studio_progress(request, scenario)
    run_root = tmp_path / "completed-run"
    generate_scenario(
        scenario,
        run_root / "transcript.json",
        run_root,
        mandate_request=request.objective,
        include_change_story=False,
        progress_observer=observer,
        presentation_observer=observer,
    )
    snapshot = store.snapshot()
    assert len(snapshot.events) == 62
    assert len(snapshot.graph_nodes) == 17
    assert len(snapshot.graph_edges) == 57

    client = studio_client(tmp_path / "studio")
    page = client.get("/").text
    catalog = client.get("/api/catalog").json()
    page = page.replace(
        '<link rel="stylesheet" href="/studio-static/coordination-studio.css">',
        f"<style>{CSS.read_text(encoding='utf-8')}</style>",
    )
    fixture = {
        "catalog": catalog,
        "snapshot": snapshot.model_dump(mode="json"),
    }
    fixture_json = json.dumps(fixture).replace("</", "<\\/")
    prelude = textwrap.dedent(
        f"""
        <script>
          const browserFixture = {fixture_json};
          history.replaceState = () => {{}};
          window.fetch = async (url) => {{
            let body;
            let status = 200;
            if (url === "/api/catalog") body = browserFixture.catalog;
            else if (url === "/api/runs") {{
              status = 201;
              body = {{ run_alias: "render-001", workspace_url: "/runs/render-001" }};
            }} else if (url === "/api/runs/render-001") body = browserFixture.snapshot;
            else throw new Error(`Unexpected browser fixture URL: ${{url}}`);
            return new Response(JSON.stringify(body), {{
              status,
              headers: {{ "Content-Type": "application/json", ETag: '"event-62"' }},
            }});
          }};
        </script>
        """
    )
    measurement = textwrap.dedent(
        """
        <script>
          window.addEventListener("load", () => {
            setTimeout(() => document.querySelector("[data-start-coordination]").click(), 25);
            setTimeout(async () => {
              await document.fonts.ready;
              await new Promise((resolve) => setTimeout(resolve, 100));
              const groups = Array.from(document.querySelectorAll("[data-flow-node]"));
              const boxes = groups.map((group) => {
                const bounds = group.getBoundingClientRect();
                const card = group.querySelector("rect").getBoundingClientRect();
                const textBounds = Array.from(group.querySelectorAll("text"), (item) => {
                  const box = item.getBoundingClientRect();
                  return {
                    text: item.textContent,
                    left: box.left,
                    top: box.top,
                    right: box.right,
                    bottom: box.bottom,
                  };
                });
                return {
                  id: group.getAttribute("data-flow-node"),
                  label: group.querySelectorAll("text")[1].textContent,
                  left: bounds.left,
                  top: bounds.top,
                  right: bounds.right,
                  bottom: bounds.bottom,
                  card: { left: card.left, top: card.top, right: card.right, bottom: card.bottom },
                  textBounds,
                };
              });
              const collisions = [];
              for (let left = 0; left < boxes.length; left += 1) {
                for (let right = left + 1; right < boxes.length; right += 1) {
                  const a = boxes[left];
                  const b = boxes[right];
                  const horizontalGap = Math.max(b.left - a.right, a.left - b.right);
                  const verticalGap = Math.max(b.top - a.bottom, a.top - b.bottom);
                  if (horizontalGap < 5 && verticalGap < 5) {
                    collisions.push({
                      pair: `${a.label} <-> ${b.label}`,
                      horizontalGap,
                      verticalGap,
                    });
                  }
                }
              }
              const escapedText = boxes.flatMap((box) => box.textBounds.flatMap((text) => {
                const requiredRightInset = box.id.startsWith("person-") ? 7.5 : 0;
                const contentRight = box.card.right - requiredRightInset;
                const escaped = (
                    text.left < box.card.left - 0.5
                    || text.right > contentRight + 0.5
                    || text.top < box.card.top - 0.5
                    || text.bottom > box.card.bottom + 0.5
                );
                return escaped ? [{
                  label: box.label,
                  text: text.text,
                  rightOverflow: text.right - contentRight,
                }] : [];
              }));
              const result = document.createElement("pre");
              result.id = "graph-bounds-result";
              result.textContent = JSON.stringify({
                nodeCount: boxes.length,
                edgeCount: document.querySelectorAll("[data-flow-edge]").length,
                viewport: { width: window.innerWidth, height: window.innerHeight },
                fontStatus: document.fonts.status,
                collisions,
                escapedText,
              });
              document.body.append(result);
            }, 800);
          });
        </script>
        """
    )
    controller = SCRIPT.read_text(encoding="utf-8").replace("</", "<\\/")
    page = page.replace(
        '<script src="/studio-static/coordination-studio.js" defer></script>',
        f"{prelude}<script>{controller}</script>{measurement}",
    )
    harness = tmp_path / "rendered-graph.html"
    harness.write_text(page, encoding="utf-8")
    profile = tmp_path / "chromium-profile"
    result = subprocess.run(
        [
            str(chromium),
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "--window-size=1702,1104" if sys.platform == "win32" else "--window-size=1680,950",
            "--virtual-time-budget=2200",
            f"--user-data-dir={profile}",
            "--dump-dom",
            harness.as_uri(),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    matched = re.search(
        r'<pre id="graph-bounds-result">(.*?)</pre>',
        result.stdout,
        re.DOTALL,
    )
    assert matched is not None, result.stdout[-2000:]
    measured = json.loads(html.unescape(matched.group(1)))
    assert measured["viewport"] == {"width": 1680, "height": 950}
    assert measured["fontStatus"] == "loaded"
    assert measured["nodeCount"] == 17
    assert measured["edgeCount"] == 57
    assert {
        "escapedText": measured["escapedText"],
        "collisions": measured["collisions"],
    } == {"escapedText": [], "collisions": []}


def test_controller_source_has_no_unsafe_dom_or_token_persistence() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in (
        ".innerHTML",
        "document.write",
        "localStorage",
        "sessionStorage",
        "console.",
        "eval(",
        "new Function",
    ):
        assert forbidden not in source
    for forbidden_copy in ("synthetic", "fake", "proof", "local viewer"):
        assert forbidden_copy not in source.casefold()
    assert "textContent" in source
    assert "createElement" in source
    assert "setAttribute" in source


def test_controller_runs_submit_poll_replay_and_reduced_motion_contract() -> None:
    harness = textwrap.dedent(
        r"""
        const assert = require("node:assert/strict");
        const fs = require("node:fs");
        const scriptPath = process.argv[1];
        const fixturePath = process.argv[2];
        const mode = process.argv[3];
        const fixtureSnapshots = JSON.parse(fs.readFileSync(fixturePath, "utf8"));

        class ClassList {
          constructor() { this.values = new Set(); }
          add(...names) { names.forEach((name) => this.values.add(name)); }
          remove(...names) { names.forEach((name) => this.values.delete(name)); }
          contains(name) { return this.values.has(name); }
          toggle(name, force) {
            const enabled = force === undefined ? !this.contains(name) : Boolean(force);
            if (enabled) this.add(name); else this.remove(name);
            return enabled;
          }
        }

        function matches(node, selector) {
          if (selector.startsWith(".")) return node.classList.contains(selector.slice(1));
          const tags = selector.match(/^[a-z]+/i);
          if (tags && node.tagName !== tags[0].toUpperCase()) return false;
          for (const match of selector.matchAll(/\[([^=\]]+)(?:="([^"]*)")?\]/g)) {
            const value = node.getAttribute(match[1]);
            if (value === null || (match[2] !== undefined && value !== match[2])) return false;
          }
          return true;
        }

        class Element {
          constructor(tag = "div") {
            this.tagName = tag.toUpperCase();
            this.attributes = {};
            this.children = [];
            this.parentNode = null;
            this.classList = new ClassList();
            this.listeners = {};
            this.textContent = "";
            this.value = "";
            this.checked = false;
            this.disabled = false;
            this.hidden = false;
          }
          setAttribute(name, value) {
            this.attributes[name] = String(value);
            if (name === "class") String(value).split(/\s+/).filter(Boolean).forEach((item) => this.classList.add(item));
            if (name === "disabled") this.disabled = true;
          }
          getAttribute(name) { return this.attributes[name] ?? null; }
          removeAttribute(name) { delete this.attributes[name]; if (name === "disabled") this.disabled = false; }
          append(...nodes) { nodes.forEach((node) => { node.parentNode = this; this.children.push(node); }); }
          appendChild(node) { this.append(node); return node; }
          replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
          remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((item) => item !== this); }
          addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
          click() { if (!this.disabled) (this.listeners.click || []).forEach((callback) => callback({ preventDefault() {} })); }
          querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
          querySelectorAll(selector) {
            const found = [];
            const visit = (node) => { if (matches(node, selector)) found.push(node); node.children.forEach(visit); };
            this.children.forEach(visit);
            return found;
          }
        }

        const nodes = {};
        const all = [];
        function register(selector, tag = "div", attrs = {}) {
          const node = new Element(tag);
          Object.entries(attrs).forEach(([name, value]) => node.setAttribute(name, value));
          nodes[selector] = node;
          all.push(node);
          return node;
        }
        register('[data-studio-state="composer"]', "section");
        register('[data-studio-state="workspace"]', "section").hidden = true;
        register("[data-composer-form]", "form");
        register("[data-start-coordination]", "button");
        register("[data-form-status]");
        register("[data-objective]", "textarea").value = "Set up a decision meeting tomorrow to approve the launch plan.";
        const role = register('[name="requester_role"]', "input"); role.value = "manager"; role.checked = true;
        const timing = register('[name="target_timing"]', "input"); timing.value = "tomorrow"; timing.checked = true;
        const customDate = register("[data-custom-date]", "input");
        const conflict = register("[data-include-conflict]", "input"); conflict.checked = true;
        const agentMode = register('[name="agent_mode"]', "input"); agentMode.value = "standard"; agentMode.checked = true;
        const participants = ["inform", "ack", "quick-a", "quick-b", "structured", "approval", "availability"].map((id) => {
          const node = new Element("input"); node.value = id; node.checked = true; return node;
        });
        register("[data-current-template]").setAttribute("data-current-template", "launch-decision");
        register("[data-workspace-objective]"); register("[data-workspace-requester]");
        register("[data-run-state]"); register("[data-connection-label]");
        register("[data-current-stage]"); register("[data-event-progress]");
        register("[data-flow-from]"); register("[data-flow-to]"); register("[data-flow-generated]");
        register("[data-flow-live]"); register("[data-flow-canvas]", "svg");
        register("[data-conversation-list]"); register("[data-data-list]");
        register("[data-outcome-headline]"); register("[data-outcome-summary]");
        register("[data-pause-visuals]", "button"); register("[data-follow-live]", "button");
        register("[data-replay-previous]", "button"); register("[data-replay-next]", "button");
        register("[data-replay-play]", "button"); register("[data-download-json]", "button");
        register("[data-download-csv]", "button"); register("[data-new-coordination]", "button");
        ["brief", "outreach", "resolve", "approve", "schedule"].forEach((stage) => {
          all.push(new Element("li")); all.at(-1).setAttribute("data-lifecycle-stage", stage);
        });
        ["quick-a", "structured"].forEach((id) => {
          all.push(new Element("article")); all.at(-1).setAttribute("data-persona-card", id);
        });
        const meta = register('meta[name="humanwire-action-token"]', "meta"); meta.setAttribute("content", "test-token");

        const domListeners = {};
        global.document = {
          hidden: mode === "hidden",
          body: new Element("body"),
          documentElement: { dataset: {} },
          createElement: (tag) => new Element(tag),
          createElementNS: (_namespace, tag) => new Element(tag),
          querySelector(selector) {
            if (selector.endsWith(":checked")) {
              if (selector.includes("requester_role")) return role;
              if (selector.includes("target_timing")) return timing;
              if (selector.includes("agent_mode")) return agentMode;
            }
            return nodes[selector] || all.find((node) => matches(node, selector)) || null;
          },
          querySelectorAll(selector) {
            if (selector === '[name="participant_ids"]:checked') return participants.filter((node) => node.checked);
            if (selector === "[data-flow-edge]") return nodes["[data-flow-canvas]"].querySelectorAll(selector);
            if (selector === "[data-flow-node]") return nodes["[data-flow-canvas]"].querySelectorAll(selector);
            return all.filter((node) => matches(node, selector));
          },
          addEventListener(type, callback) { (domListeners[type] ||= []).push(callback); },
        };
        document.body.append = (...children) => children.forEach((child) => { child.parentNode = document.body; document.body.children.push(child); });

        const fetchCalls = [];
        const fetchQueue = [];
        global.fetch = async (url, options = {}) => { fetchCalls.push({ url, options }); return fetchQueue.shift(); };
        const intervals = [];
        global.setInterval = (callback, delay) => { intervals.push({ callback, delay }); return intervals.length; };
        global.clearInterval = () => {};
        global.setTimeout = (callback) => { callback(); return 1; };
        global.clearTimeout = () => {};
        global.location = { pathname: "/" };
        global.history = { replaceState(_state, _title, url) { location.pathname = url; } };
        global.window = global;
        global.matchMedia = () => ({ matches: mode === "reduced", addEventListener() {} });
        global.addEventListener = () => {};

        function jsonResponse(body, status = 200, etag = null) {
          return { ok: status >= 200 && status < 300, status, headers: { get: () => etag }, json: async () => structuredClone(body) };
        }
        function click(selector) { document.querySelector(selector).click(); return Promise.resolve(); }
        function text(selector) { return document.querySelector(selector).textContent.trim(); }
        function activeEdges() { return [...document.querySelectorAll("[data-flow-edge]")].filter((node) => node.classList.contains("is-active")); }
        function activePersonas() { return [...document.querySelectorAll("[data-persona-card]")].filter((node) => node.classList.contains("is-active")); }
        function conversationRows() { return nodes["[data-conversation-list]"].querySelectorAll("[data-conversation-row]"); }
        function dataRows() { return nodes["[data-data-list]"].querySelectorAll("[data-data-row]"); }
        function flowStrip() { return { from: text("[data-flow-from]"), to: text("[data-flow-to]"), generated: text("[data-flow-generated]") }; }
        async function pollWith(snapshot) { fetchQueue.push(jsonResponse(snapshot, 200, '"event-' + snapshot.current_event_ordinal + '"')); await intervals[0].callback(); await Promise.resolve(); }

        (async () => {
          fetchQueue.push(jsonResponse({
            stakeholders: [
              { persona_id: "quick-a", display_name: "Priya Shah", role: "Product lead", engagement_label: "Quick response" },
              { persona_id: "structured", display_name: "Anika Rao", role: "Risk & compliance lead", engagement_label: "Structured interview" }
            ],
            templates: [{
              template_id: "launch-decision",
              title: "Launch decision",
              objective: "Set up a decision meeting tomorrow to approve the launch plan.",
              requester_role: "manager",
              participant_ids: ["inform", "ack", "quick-a", "quick-b", "structured", "approval", "availability"],
              target_timing: "tomorrow",
              include_conflict: true
            }]
          }));
          require(scriptPath);
          (domListeners.DOMContentLoaded || []).forEach((callback) => callback());
          await Promise.resolve(); await Promise.resolve();
          assert.equal(fetchCalls[0].url, "/api/catalog");

          fetchQueue.push(jsonResponse({ run_alias: "launch-001", workspace_url: "/runs/launch-001" }, 201));
          await click("[data-start-coordination]");
          await Promise.resolve(); await Promise.resolve();
          assert.equal(fetchCalls[1].url, "/api/runs");
          assert.equal(fetchCalls[1].options.method, "POST");
          assert.equal(fetchCalls[1].options.headers["X-HumanWire-Action"], "test-token");
          assert.equal(JSON.parse(fetchCalls[1].options.body).requester_name, "Alex Morgan");
          assert.deepEqual(Object.keys(JSON.parse(fetchCalls[1].options.body)).sort(), ["agent_mode", "custom_date", "include_conflict", "objective", "participant_ids", "requester_name", "requester_role", "target_timing", "template_id"]);
          assert.equal(location.pathname, "/runs/launch-001");

          await pollWith(fixtureSnapshots[0]);
          assert.equal(text("[data-current-stage]"), "Outreach");
          assert.equal(activeEdges().length, 1);
          assert.equal(activePersonas().length, 1);
          assert.equal(conversationRows().length, 1);
          assert.equal(dataRows().length, 1);

          await pollWith(fixtureSnapshots[1]);
          assert.equal(text("[data-event-progress]"), "Event 2 of 2");
          assert.deepEqual(flowStrip(), {
            from: fixtureSnapshots[1].active_transition.source_label,
            to: fixtureSnapshots[1].active_transition.destination_label,
            generated: fixtureSnapshots[1].active_transition.generated_label,
          });
          assert.equal(activeEdges().length, 1);
          assert.equal(activePersonas().length, 1);
          assert.equal(conversationRows().length, 2);
          assert.equal(dataRows().length, 2);
          assert.equal(nodes["[data-download-json]"].disabled, false);
          assert.equal(nodes["[data-download-csv]"].disabled, false);
          const activeEdge = activeEdges()[0];
          assert.equal(activeEdge.classList.contains("is-travelling"), mode === "normal");

          await click("[data-replay-previous]");
          assert.equal(text("[data-event-progress]"), "Event 1 of 2");
          assert.equal(conversationRows().length, 1);
          await click("[data-replay-next]");
          assert.equal(text("[data-event-progress]"), "Event 2 of 2");
          await click("[data-download-json]");
          assert.equal(location.pathname, "/runs/launch-001");
          assert.equal(document.body.children.at(-1)?.tagName === "A", false);
        })().catch((error) => { process.stderr.write(error.stack + "\n"); process.exitCode = 1; });
        """
    )
    for mode in ("normal", "reduced"):
        result = subprocess.run(
            ["node", "-e", harness, str(SCRIPT), str(FIXTURES), mode],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
