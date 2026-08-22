"use strict";

const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

class FakeElement {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.hidden = false;
    this.textContent = "";
    this.disabled = false;
    this.value = "";
    this.checked = false;
    this.attributes = {};
    this.queries = new Map();
    this.replaceCount = 0;
  }

  append(...children) { this.children.push(...children); }
  replaceChildren(...children) {
    this.replaceCount += 1;
    this.children = [...children];
  }
  querySelector(selector) {
    const ordinal = /^\[data-event-ordinal="(\d+)"\]$/.exec(selector);
    if (ordinal) {
      return this.children.find((item) => item.dataset.eventOrdinal === ordinal[1]) || null;
    }
    const missionEvent = /^\[data-mission-event-ordinal="(\d+)"\]$/.exec(selector);
    if (missionEvent) {
      return this.children.find((item) => item.dataset.missionEventOrdinal === missionEvent[1]) || null;
    }
    return this.queries.get(selector) || null;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  getAttribute(name) { return this.attributes[name] ?? null; }
  scrollIntoView() {}
  addEventListener() {}
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return {promise, resolve};
}

function response(payload, status = 200, body = null) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body,
    async json() { return payload; },
  };
}

function event(ordinal, kind, stage, summary, participantId = null) {
  return {
    ordinal,
    kind,
    stage,
    summary,
    participant_id: participantId,
    created_at: `2026-08-21T12:00:${String(ordinal).padStart(2, "0")}Z`,
  };
}

const events = [
  event(1, "mission.created", "request", "Mission created."),
  event(2, "mission.started", "outreach", "Mission started."),
  event(3, "council.specialist_started", "analysis", "Market Intelligence started analysis.", "ai-market-intelligence"),
  event(4, "council.specialist_completed", "analysis", "Market Intelligence completed analysis.", "ai-market-intelligence"),
  event(5, "council.completed", "synthesis", "Launch with a bounded pilot."),
  event(6, "stakeholder.response_recorded", "evidence", "AI stakeholder evidence recorded.", "demo-owner"),
  event(7, "decision_brief.ready", "decision", "Decision brief ready."),
];

function projection(state = "ready", eventRows = [events[0]]) {
  return {
    mission_id: "mis_01HQ7XK9WPH4Y8ZQK3R2N1M6AA",
    objective: "Approve the launch decision with current evidence.",
    mode: "demo_run",
    mode_label: "Demo run",
    state,
    stage: state === "complete" ? "decision" : eventRows[eventRows.length - 1].stage,
    participants: [
      {
        participant_id: "ai-market-intelligence",
        display_name: "Market Intelligence",
        role: "Market Intelligence AI",
        actor_label: "AI specialist",
        response_required: false,
      },
      {
        participant_id: "demo-owner",
        display_name: "Sofia Alvarez",
        role: "Decision owner AI",
        actor_label: "AI stakeholder",
        response_required: true,
      },
    ],
    events: eventRows,
    next_action: state === "complete" ? "Review the decision brief." : "Start the mission.",
    recommendation_summary: state === "complete" ? "Launch with a bounded pilot." : null,
    delivery_status: null,
    blocked_reason: null,
  };
}

async function tick() {
  await new Promise((resolve) => setImmediate(resolve));
}

async function main() {
  const selectors = new Map();
  const flowNodes = [];
  const progressNodes = [];
  for (const name of [
    "market_intelligence",
    "financial_analysis",
    "product_technical",
    "risk_compliance",
    "decision_synthesis",
    "red_team",
    "final_synthesis",
    "human_approval",
  ]) {
    const node = new FakeElement("li");
    node.dataset.specialist = name;
    node.queries.set("small", new FakeElement("small"));
    selectors.set(`[data-specialist="${name}"]`, node);
    flowNodes.push(node);
  }
  for (const stage of ["outreach", "analysis", "synthesis", "evidence", "decision"]) {
    const node = new FakeElement("li");
    node.dataset.missionStep = stage;
    progressNodes.push(node);
  }
  for (const selector of [
    "[data-mission-form]",
    "[data-mission-workspace]",
    "[data-new-mission]",
    "[data-start-mission]",
    "[data-mission-state]",
    "[data-mission-objective]",
    "[data-mission-mode-label]",
    "[data-mission-stage]",
    "[data-mission-next-action]",
    "[data-mission-recommendation]",
    "[data-mission-blocked]",
    "[data-mission-participants]",
    "[data-mission-timeline]",
    "[data-mission-progress]",
    "[data-mission-progress-meter]",
    "[data-mission-progress-summary]",
    "[data-mission-elapsed]",
    "[data-mission-pulse]",
    "[data-council-state]",
    "[data-council-activity]",
    "[data-council-live]",
    "[data-council-result]",
    "[data-latest-decision]",
    "[data-organization-list]",
    "[data-workspace-list]",
    "[data-app-status]",
    "[data-mission-readiness]",
  ]) selectors.set(selector, new FakeElement());
  const startButton = selectors.get("[data-start-mission]");
  startButton.textContent = "Start mission";

  const mode = new FakeElement("input");
  mode.value = "demo_run";
  selectors.set('[data-mission-mode]:checked', mode);

  const form = selectors.get("[data-mission-form]");
  form.elements = {
    mode,
    objective: {value: "Approve the launch decision with current evidence."},
    urgency: {value: "standard"},
    include_conflict: {checked: true},
  };

  const createGate = deferred();
  let latestCouncilLoads = 0;
  const pendingReads = [];
  const reader = {
    read() {
      const gate = deferred();
      pendingReads.push(gate);
      return gate.promise;
    },
  };
  const timers = [];
  let timerId = 0;
  const context = {
    globalThis: null,
    document: {
      readyState: "loading",
      cookie: "__Host-humanwire-csrf=csrf-token",
      addEventListener() {},
      querySelector(selector) { return selectors.get(selector) || null; },
      querySelectorAll(selector) {
        if (selector === "[data-council-flow] [data-specialist]") return flowNodes;
        if (selector === "[data-mission-progress] [data-mission-step]") return progressNodes;
        if (selector === "[data-demo-disclosure]") return [];
        return [];
      },
      createElement(tagName) { return new FakeElement(tagName); },
    },
    location: {hash: "", assign() {}},
    history: {replaceState(_state, _title, url) { context.location.hash = url.split("#")[1] ? `#${url.split("#")[1]}` : ""; }},
    HumanWireFirebase: {async appCheckToken() { return "app-check"; }},
    matchMedia() { return {matches: false}; },
    TextDecoder,
    Uint8Array,
    Date,
    setTimeout(callback, delay) {
      timerId += 1;
      timers.push({id: timerId, callback, delay, active: true, interval: false});
      return timerId;
    },
    clearTimeout(id) {
      const timer = timers.find((item) => item.id === id);
      if (timer) timer.active = false;
    },
    setInterval(callback, delay) {
      timerId += 1;
      timers.push({id: timerId, callback, delay, active: true, interval: true});
      return timerId;
    },
    clearInterval(id) {
      const timer = timers.find((item) => item.id === id);
      if (timer) timer.active = false;
    },
    fetch: async (path) => {
      if (path === "/api/organizations") {
        return response({organizations: [{organization_id: "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA", name: "Northstar", role: "owner"}]});
      }
      if (path.endsWith("/workspaces")) {
        return response({workspaces: [{workspace_id: "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AA", name: "Launch", playbook: "launch_decision"}]});
      }
      if (path.endsWith("/council-runs/latest")) {
        latestCouncilLoads += 1;
        return response({
          projection: latestCouncilLoads > 1 ? {
            state: "human_approval_required",
            recommendation_summary: "Launch with a bounded pilot.",
            recommended_action: "Approve a limited launch.",
            required_human_action: "Confirm the decision owner.",
            recommendation_digest: "a".repeat(64),
            nodes: [{specialist_id: "market_intelligence", status: "complete"}],
            evidence_claims: [{
              statement: "Pilot demand is supported by saved evidence.",
              classification: "source_backed",
              evidence_ids: ["evidence-01"],
            }],
            inference_claims: [{
              statement: "A limited launch can contain execution risk.",
              classification: "model_inference",
              evidence_ids: [],
            }],
            challenges: [{
              issue: "Independent testing remains required.",
              required_action: "Complete the test before broad launch.",
            }],
            events: [],
          } : null,
        });
      }
      if (path.endsWith("/evidence")) return response({evidence: []});
      if (path.endsWith("/missions")) return createGate.promise;
      if (path.endsWith("/run")) return response({}, 200, {getReader() { return reader; }});
      throw new Error(`unexpected request: ${path}`);
    },
  };
  context.globalThis = context;
  vm.runInNewContext(
    fs.readFileSync("src/humanwire/decisionos_static/decisionos-app.js", "utf8"),
    context,
    {filename: "decisionos-app.js"},
  );

  await context.HumanWireDecisionOSApp.loadOrganizations();
  const running = context.HumanWireDecisionOSApp.startMission({
    preventDefault() {},
    currentTarget: form,
  });
  await tick();

  assert.strictEqual(selectors.get("[data-mission-workspace]").hidden, false);
  assert.strictEqual(form.getAttribute("aria-busy"), "true");
  assert.strictEqual(startButton.disabled, true);
  assert.strictEqual(startButton.textContent, "Starting mission…");
  assert.strictEqual(selectors.get("[data-mission-state]").textContent, "Running");
  assert.strictEqual(selectors.get("[data-mission-progress-summary]").textContent, "Preparing the team");
  assert.match(selectors.get("[data-mission-pulse]").textContent, /assembling/i);
  assert.strictEqual(progressNodes[0].dataset.state, "current");

  const quiet = timers.find((item) => item.delay === 4000 && item.active);
  assert.ok(quiet, "quiet-period reassurance timer must be armed immediately");
  quiet.callback();
  assert.match(selectors.get("[data-mission-pulse]").textContent, /Still working/i);

  createGate.resolve(response({mission: projection()}));
  while (pendingReads.length === 0) await tick();
  pendingReads.shift().resolve({
    done: false,
    value: new TextEncoder().encode([
      JSON.stringify({type: "started"}),
      JSON.stringify({type: "activity", event: events[1]}),
      JSON.stringify({type: "activity", event: events[2]}),
      "",
    ].join("\n")),
  });
  await tick();

  assert.strictEqual(selectors.get("[data-mission-timeline]").children.length, 2);
  assert.strictEqual(selectors.get('[data-specialist="market_intelligence"]').dataset.status, "waiting");
  const firstPace = timers.find((item) => item.delay === 180 && item.active);
  assert.ok(firstPace, "buffered activities must be presented one at a time");
  firstPace.active = false;
  firstPace.callback();
  while (pendingReads.length === 0) await tick();

  assert.strictEqual(selectors.get('[data-specialist="market_intelligence"]').dataset.status, "running");
  assert.strictEqual(selectors.get("[data-council-state]").textContent, "Agent Council working");
  assert.match(selectors.get("[data-mission-pulse]").textContent, /Market Intelligence started analysis/);
  assert.strictEqual(progressNodes[1].dataset.state, "current");
  assert.strictEqual(selectors.get("[data-council-live]").hidden, false);

  pendingReads.shift().resolve({
    done: false,
    value: new TextEncoder().encode([
      JSON.stringify({type: "activity", event: events[3]}),
      JSON.stringify({type: "activity", event: events[4]}),
      JSON.stringify({type: "activity", event: events[5]}),
      JSON.stringify({type: "activity", event: events[6]}),
      JSON.stringify({type: "complete", mission: projection("complete", events)}),
      "",
    ].join("\n")),
  });
  while (pendingReads.length === 0) {
    await tick();
    const pace = timers.find((item) => item.delay === 180 && item.active);
    if (pace) {
      pace.active = false;
      pace.callback();
    }
  }
  pendingReads.shift().resolve({done: true, value: new Uint8Array()});
  await running;

  assert.strictEqual(selectors.get("[data-mission-state]").textContent, "Complete");
  assert.strictEqual(form.getAttribute("aria-busy"), "false");
  assert.strictEqual(selectors.get('[data-specialist="market_intelligence"]').dataset.status, "complete");
  assert.strictEqual(selectors.get('[data-specialist="human_approval"]').dataset.status, "required");
  assert.strictEqual(selectors.get("[data-council-state]").textContent, "human approval required");
  assert.strictEqual(selectors.get("[data-mission-progress-summary]").textContent, "Decision brief ready");
  assert.strictEqual(selectors.get("[data-mission-progress-meter]").value, 5);
  assert.strictEqual(selectors.get("[data-mission-progress-meter]").textContent, "5 of 5 stages complete");
  assert.strictEqual(selectors.get("[data-mission-timeline]").children.length, events.length);
  assert.strictEqual(selectors.get("[data-mission-timeline]").replaceCount, 2);
  assert.match(selectors.get("[data-mission-elapsed]").textContent, /^Completed in /);
  assert.match(selectors.get("[data-mission-pulse]").textContent, /decision brief is ready/i);
  assert.strictEqual(latestCouncilLoads, 2);
  assert.strictEqual(selectors.get("[data-council-result]").hidden, false);

  context.HumanWireDecisionOSApp.resetMission();
  assert.strictEqual(startButton.textContent, "Start mission");
  assert.strictEqual(form.getAttribute("aria-busy"), "false");

  context.HumanWireDecisionOSApp.renderMission(projection("complete", events));
  assert.strictEqual(selectors.get("[data-mission-elapsed]").textContent, "Completed");
  assert.strictEqual(selectors.get('[data-specialist="market_intelligence"]').dataset.status, "complete");

  process.stdout.write("decisionos mission experience harness: PASS\n");
}

main().catch((error) => {
  process.stderr.write(`${error.stack}\n`);
  process.exitCode = 1;
});
