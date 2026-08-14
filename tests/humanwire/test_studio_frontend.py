import re
import subprocess
import textwrap
from pathlib import Path

from fastapi.testclient import TestClient

from humanwire.studio_app import create_coordination_studio_app
from humanwire.studio_run import StudioRunManager

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "src/humanwire/templates/coordination_studio.html"
CSS = ROOT / "src/humanwire/studio_static/coordination-studio.css"
SCRIPT = ROOT / "src/humanwire/studio_static/coordination-studio.js"
FIXTURES = ROOT / "tests/humanwire/fixtures/studio-snapshots.json"


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
    for mode in ("normal", "reduced", "hidden"):
        result = subprocess.run(
            ["node", "-e", harness, str(SCRIPT), str(FIXTURES), mode],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
