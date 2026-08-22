"use strict";

const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

class FakeElement {
  constructor() {
    this.children = [];
    this.dataset = {};
    this.hidden = false;
    this.textContent = "";
    this.disabled = false;
    this.value = "";
    this.attributes = {};
    this.queries = new Map();
  }

  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  querySelector(selector) {
    const ordinal = /^\[data-event-ordinal="(\d+)"\]$/.exec(selector);
    if (ordinal) {
      return this.children.find((item) => item.dataset.eventOrdinal === ordinal[1]) || null;
    }
    return this.queries.get(selector) || null;
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  scrollIntoView() {}
  addEventListener() {}
}

function response(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return payload; },
  };
}

function projection(state = "ready") {
  return {
    mission_id: "mis_01HQ7XK9WPH4Y8ZQK3R2N1M6AA",
    objective: "Approve the launch decision with current evidence.",
    mode: "demo_run",
    mode_label: "Demo run",
    state,
    stage: state === "complete" ? "decision" : "request",
    participants: [
      {
        participant_id: "ai-market-intelligence",
        display_name: "Market Intelligence",
        role: "Market Intelligence AI",
        actor_label: "AI specialist",
        response_required: false,
      },
      {
        participant_id: "demo-decision-owner",
        display_name: "Sofia Alvarez",
        role: "Decision owner AI",
        actor_label: "AI stakeholder",
        response_required: true,
      },
    ],
    events: [
      {
        ordinal: 1,
        kind: "mission.created",
        stage: "request",
        summary: "Mission created.",
        participant_id: null,
        created_at: "2026-08-21T12:00:00Z",
      },
      ...(state === "complete" ? [{
        ordinal: 2,
        kind: "decision_brief.ready",
        stage: "decision",
        summary: "Decision brief ready.",
        participant_id: null,
        created_at: "2026-08-21T12:00:01Z",
      }] : []),
    ],
    next_action: state === "complete" ? "Review the decision brief." : "Start the mission.",
    recommendation_summary: state === "complete" ? "Launch with a bounded pilot." : null,
    delivery_status: null,
    blocked_reason: null,
  };
}

function councilProjection() {
  return {
    run_id: "council_run_01",
    objective: "Approve the launch decision with current evidence.",
    state: "human_approval_required",
    nodes: [
      {specialist_id: "market_intelligence", status: "complete"},
      {specialist_id: "human_approval", status: "required"},
    ],
    recommendation_summary: "Launch with a bounded pilot.",
    recommended_action: "Proceed with the limited pilot.",
    required_human_action: "Review and approve the plan.",
    recommendation_digest: "a".repeat(64),
    evidence_claims: [],
    inference_claims: [],
    challenges: [],
  };
}

async function main() {
  const selectors = new Map();
  const flowNodes = [];
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
    const node = new FakeElement();
    node.dataset.specialist = name;
    node.queries.set("small", new FakeElement());
    selectors.set(`[data-specialist="${name}"]`, node);
    flowNodes.push(node);
  }
  for (const selector of [
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
  const mode = new FakeElement();
  mode.value = "demo_run";
  selectors.set('[data-mission-mode]:checked', mode);

  const requests = [];
  const context = {
    globalThis: null,
    document: {
      readyState: "loading",
      cookie: "__Host-humanwire-csrf=csrf-token",
      addEventListener() {},
      querySelector(selector) { return selectors.get(selector) || null; },
      querySelectorAll(selector) {
        if (selector === "[data-council-flow] [data-specialist]") return flowNodes;
        if (selector === "[data-demo-disclosure]") return [];
        return [];
      },
      createElement() { return new FakeElement(); },
    },
    location: {hash: "#mission=mis_01HQ7XK9WPH4Y8ZQK3R2N1M6AA", assign() {}},
    history: {replaceState(_state, _title, url) { context.location.hash = url.split("#")[1] ? `#${url.split("#")[1]}` : ""; }},
    HumanWireFirebase: {async appCheckToken() { return "app-check"; }},
    TextDecoder,
    Uint8Array,
    setTimeout,
    clearTimeout,
    fetch: async (path, options = {}) => {
      requests.push({path, options});
      if (path === "/api/organizations") {
        return response({organizations: [{organization_id: "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA", name: "Northstar", role: "owner"}]});
      }
      if (path.endsWith("/workspaces")) {
        return response({workspaces: [{workspace_id: "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AA", name: "Launch", playbook: "launch_decision"}]});
      }
      if (path.endsWith("/council-runs/latest")) return response({projection: councilProjection()});
      if (path.endsWith("/evidence")) return response({evidence: []});
      if (path.endsWith("/missions/mis_01HQ7XK9WPH4Y8ZQK3R2N1M6AA")) {
        return response({mission: projection("complete")});
      }
      if (path.endsWith("/missions")) return response({mission: projection()});
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
  assert.strictEqual(selectors.get("[data-council-result]").hidden, false);
  const created = await context.HumanWireDecisionOSApp.createMission({
    mode: "demo_run",
    objective: "Approve the launch decision with current evidence.",
    urgency: "standard",
    include_conflict: true,
  });
  assert.strictEqual(created.mode_label, "Demo run");
  const createRequest = requests.find((item) => item.path.endsWith("/missions"));
  assert.deepStrictEqual(
    Object.keys(JSON.parse(createRequest.options.body)).sort(),
    ["include_conflict", "mode", "objective", "urgency"],
  );
  assert.strictEqual(selectors.get("[data-mission-participants]").children.length, 2);
  assert.strictEqual(selectors.get("[data-mission-workspace]").hidden, false);

  const envelopes = [
    {type: "started"},
    {type: "activity", event: projection("complete").events[1]},
    {type: "complete", mission: projection("complete")},
  ];
  let read = false;
  const reader = {
    async read() {
      if (read) return {done: true, value: new Uint8Array()};
      read = true;
      return {
        done: false,
        value: new TextEncoder().encode(`${envelopes.map(JSON.stringify).join("\n")}\n`),
      };
    },
  };
  await context.HumanWireDecisionOSApp.consumeMissionStream(reader);
  assert.strictEqual(selectors.get("[data-mission-state]").textContent, "Complete");
  assert.strictEqual(selectors.get("[data-mission-timeline]").children.length, 2);
  assert.strictEqual(selectors.get("[data-mission-recommendation]").textContent, "Launch with a bounded pilot.");

  const early = {async read() { return {done: true, value: new Uint8Array()}; }};
  await assert.rejects(
    context.HumanWireDecisionOSApp.consumeMissionStream(early),
    /mission_stream_ended/,
  );

  let cancelled = 0;
  const obsolete = {
    async read() { return new Promise(() => {}); },
    async cancel() { cancelled += 1; },
  };
  void context.HumanWireDecisionOSApp.consumeMissionStream(obsolete);
  context.HumanWireDecisionOSApp.resetMission();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.strictEqual(cancelled, 1);
  assert.strictEqual(selectors.get("[data-mission-workspace]").hidden, true);

  process.stdout.write("decisionos mission harness: PASS\n");
}

main().catch((error) => {
  process.stderr.write(`${error.stack}\n`);
  process.exitCode = 1;
});
