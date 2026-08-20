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
    this.attributes = {};
    this.queries = new Map();
  }

  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  querySelector(selector) { return this.queries.get(selector) || null; }
  setAttribute(name, value) { this.attributes[name] = value; }
  scrollIntoView() {}
}

async function main() {
  const selectors = new Map();
  const flowNodes = [];
  const teamNodes = new Map();
  const specialists = [
    "market_intelligence",
    "financial_analysis",
    "product_technical",
    "risk_compliance",
    "decision_synthesis",
    "red_team",
    "final_synthesis",
    "human_approval",
  ];
  for (const specialist of specialists) {
    const node = new FakeElement();
    node.dataset.specialist = specialist;
    node.queries.set("small", new FakeElement());
    selectors.set(`[data-specialist="${specialist}"]`, node);
    flowNodes.push(node);
    if (specialist !== "human_approval") {
      const profile = new FakeElement();
      profile.queries.set("[data-agent-live-status]", new FakeElement());
      profile.queries.set("[data-agent-current-task]", new FakeElement());
      selectors.set(`[data-agent-profile="${specialist}"]`, profile);
      teamNodes.set(specialist, profile);
    }
  }
  for (const selector of [
    "[data-council-state]",
    "[data-council-activity]",
    "[data-council-live]",
    "[data-council-result]",
    "[data-latest-decision]",
    "[data-latest-decision-state]",
    "[data-latest-decision-objective]",
    "[data-latest-decision-summary]",
    "[data-latest-decision-run]",
  ]) selectors.set(selector, new FakeElement());

  const delays = [];
  const context = {
    globalThis: null,
    document: {
      readyState: "loading",
      cookie: "",
      addEventListener() {},
      querySelector(selector) { return selectors.get(selector) || null; },
      querySelectorAll(selector) {
        return selector === "[data-council-flow] [data-specialist]" ? flowNodes : [];
      },
      createElement() { return new FakeElement(); },
    },
    location: {assign() {}},
    HumanWireFirebase: {async appCheckToken() { return ""; }},
    TextDecoder,
    Uint8Array,
    setTimeout(callback, milliseconds) {
      delays.push(milliseconds);
      callback();
      return delays.length;
    },
    clearTimeout() {},
  };
  context.globalThis = context;
  vm.runInNewContext(
    fs.readFileSync("src/humanwire/decisionos_static/decisionos-app.js", "utf8"),
    context,
    {filename: "decisionos-app.js"},
  );

  const envelopes = [
    {type: "started"},
    {type: "activity", event: {specialist_id: "market_intelligence", display_name: "Market Intelligence", status: "started"}},
    {type: "activity", event: {specialist_id: "market_intelligence", display_name: "Market Intelligence", status: "completed"}},
    {type: "activity", event: {specialist_id: "financial_analysis", display_name: "Financial Analysis", status: "started"}},
    {type: "activity", event: {specialist_id: "financial_analysis", display_name: "Financial Analysis", status: "completed"}},
    {
      type: "complete",
      projection: {
        run_id: "council_run_demo_01",
        objective: "Assess the synthetic demo company's limited launch readiness.",
        state: "human_approval_required",
        nodes: [
          {specialist_id: "market_intelligence", status: "complete"},
          {specialist_id: "financial_analysis", status: "complete"},
        ],
        recommendation_summary: "Proceed with a human-reviewed limited launch.",
        recommended_action: "Approve a bounded launch.",
        required_human_action: "An authorized person must approve this decision.",
        recommendation_digest: "a".repeat(64),
        evidence_claims: [],
        inference_claims: [],
        challenges: [],
      },
    },
  ];
  let read = false;
  const reader = {
    async read() {
      if (read) return {done: true, value: new Uint8Array()};
      read = true;
      return {
        done: false,
        value: new TextEncoder().encode(envelopes.map((row) => JSON.stringify(row)).join("\n") + "\n"),
      };
    },
  };

  await context.HumanWireDecisionOSApp.consumeCouncilStream(reader);

  assert.deepStrictEqual(delays, [1400, 1400, 1400, 1400]);
  assert.strictEqual(selectors.get("[data-council-activity]").children.length, 4);
  assert.strictEqual(
    teamNodes.get("market_intelligence").querySelector("[data-agent-live-status]").textContent,
    "Complete",
  );
  assert.strictEqual(selectors.get("[data-latest-decision]").hidden, false);
  assert.strictEqual(
    selectors.get("[data-latest-decision-state]").textContent,
    "Human approval required",
  );
  assert.strictEqual(
    selectors.get("[data-latest-decision-summary]").textContent,
    "Proceed with a human-reviewed limited launch.",
  );
  process.stdout.write("decisionos council demo harness: PASS\n");
}

main().catch((error) => {
  process.stderr.write(`${error.stack}\n`);
  process.exitCode = 1;
});
