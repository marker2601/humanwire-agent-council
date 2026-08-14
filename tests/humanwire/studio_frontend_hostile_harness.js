"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const scriptPath = process.argv[2];
const fixtures = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const mode = process.argv[4];

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
  const tag = selector.match(/^[a-z]+/i);
  if (tag && node.tagName !== tag[0].toUpperCase()) return false;
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
  append(...children) { children.forEach((child) => { child.parentNode = this; this.children.push(child); }); }
  appendChild(child) { this.append(child); return child; }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((child) => child !== this); }
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
  dispatch(type) { (this.listeners[type] || []).forEach((callback) => callback({ preventDefault() {} })); }
  click() { if (!this.disabled) this.dispatch("click"); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    const found = [];
    const visit = (node) => { if (matches(node, selector)) found.push(node); node.children.forEach(visit); };
    this.children.forEach(visit);
    return found;
  }
}

const catalog = {
  stakeholders: [
    ["inform", "Maya Chen", "Executive sponsor", "Inform"],
    ["ack", "Nora Jensen", "Communications lead", "Acknowledge"],
    ["quick-a", "Priya Shah", "Product lead", "Quick response"],
    ["quick-b", "Marcus Reed", "Engineering lead", "Quick response"],
    ["structured", "Anika Rao", "Risk & compliance lead", "Structured interview"],
    ["approval", "Sofia Alvarez", "Approval owner", "Review and approval"],
    ["availability", "Daniel Brooks", "Operations lead", "Availability"],
    ["approval-change", "Elena Torres", "Business owner", "Review and approval"],
  ].map(([persona_id, display_name, role, engagement_label]) => ({ persona_id, display_name, role, engagement_label })),
  templates: [
    { template_id: "launch-decision", title: "Launch decision", objective: "Set up a decision meeting tomorrow to approve the launch plan.", requester_role: "manager", participant_ids: ["inform", "ack", "quick-a", "quick-b", "structured", "approval", "availability"], target_timing: "tomorrow", include_conflict: true },
    { template_id: "cross-team-conflict", title: "Resolve a cross-team conflict", objective: "Resolve the launch-readiness disagreement between Product, Engineering, and Risk.", requester_role: "program_lead", participant_ids: ["quick-a", "quick-b", "structured", "approval"], target_timing: "tomorrow", include_conflict: true },
  ],
};

const nodes = {};
const all = [];
function register(selector, tag = "div", attributes = {}) {
  const node = new Element(tag);
  Object.entries(attributes).forEach(([name, value]) => node.setAttribute(name, value));
  nodes[selector] = node; all.push(node); return node;
}

register('[data-studio-state="composer"]', "section");
register('[data-studio-state="workspace"]', "section").hidden = true;
register("[data-composer-form]", "form"); register("[data-start-coordination]", "button");
register("[data-form-status]"); register("[data-stakeholder-count]").textContent = "Seven selected";
register("[data-objective]", "textarea").value = catalog.templates[0].objective;
const role = register('[name="requester_role"]', "input"); role.value = "manager"; role.checked = true;
const timing = register('[name="target_timing"]', "input"); timing.value = "tomorrow"; timing.checked = true;
register("[data-custom-date]", "input");
const conflict = register("[data-include-conflict]", "input"); conflict.checked = true;
const agentMode = register('[name="agent_mode"]', "input"); agentMode.value = "standard"; agentMode.checked = true;
register("[data-current-template]").setAttribute("data-current-template", "launch-decision");

const participants = catalog.stakeholders.map((person, index) => {
  const label = new Element("label");
  const input = new Element("input"); input.value = person.persona_id; input.checked = index < 7; input.setAttribute("name", "participant_ids");
  const text = new Element("span"); const name = new Element("strong"); const roleText = new Element("small"); const engagement = new Element("em");
  name.textContent = person.display_name; roleText.textContent = person.role;
  engagement.textContent = person.persona_id === "approval-change" ? "Change authority" : person.engagement_label;
  text.append(name, roleText); label.append(input, text, engagement); all.push(input, label); return input;
});
const launchButton = register('[data-template-id="launch-decision"]', "button", { "data-template-id": "launch-decision" });
const conflictButton = register('[data-template-id="cross-team-conflict"]', "button", { "data-template-id": "cross-team-conflict" });

for (const selector of ["[data-workspace-objective]", "[data-workspace-requester]", "[data-run-state]", "[data-connection-label]", "[data-current-stage]", "[data-event-progress]", "[data-flow-from]", "[data-flow-to]", "[data-flow-generated]", "[data-flow-live]", "[data-conversation-list]", "[data-data-list]", "[data-outcome-headline]", "[data-outcome-summary]"]) register(selector);
register("[data-flow-canvas]", "svg");
for (const selector of ["[data-pause-visuals]", "[data-follow-live]", "[data-replay-previous]", "[data-replay-next]", "[data-replay-play]", "[data-download-json]", "[data-download-csv]", "[data-new-coordination]"]) register(selector, "button");
nodes["[data-new-coordination]"].hidden = true;
const dataTab = register('[data-mobile-tab="data"]', "button", { "data-mobile-tab": "data" });
const conversationTab = register('[data-mobile-tab="conversation"]', "button", { "data-mobile-tab": "conversation" });
for (const stage of ["brief", "outreach", "resolve", "approve", "schedule"]) {
  const item = new Element("li"); item.setAttribute("data-lifecycle-stage", stage); const status = new Element("small"); status.textContent = "Pending"; item.append(status); all.push(item);
}
for (const person of catalog.stakeholders) { const card = new Element("article"); card.setAttribute("data-persona-card", person.persona_id); all.push(card); }
const meta = register('meta[name="humanwire-action-token"]', "meta"); meta.setAttribute("content", "test-token");

const documentListeners = {};
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
    if (selector === '[name="participant_ids"]') return participants;
    if (selector === '[name="participant_ids"]:checked') return participants.filter((node) => node.checked);
    if (selector === "[data-flow-edge]" || selector === "[data-flow-node]") return nodes["[data-flow-canvas]"].querySelectorAll(selector);
    return all.filter((node) => matches(node, selector));
  },
  addEventListener(type, callback) { (documentListeners[type] ||= []).push(callback); },
};
document.body.append = (...children) => children.forEach((child) => { child.parentNode = document.body; document.body.children.push(child); });

const fetchCalls = [];
const fetchQueue = [];
global.fetch = async (url, options = {}) => { fetchCalls.push({ url, options }); const response = fetchQueue.shift(); assert.ok(response, `unexpected fetch ${url}`); return response; };
let timerId = 0;
const timeouts = new Map();
const intervals = new Map();
global.setTimeout = (callback, delay) => { const id = ++timerId; timeouts.set(id, { callback, delay }); return id; };
global.clearTimeout = (id) => { timeouts.delete(id); };
global.setInterval = (callback, delay) => { const id = ++timerId; intervals.set(id, { callback, delay }); return id; };
global.clearInterval = (id) => { intervals.delete(id); };
global.location = { pathname: "/" };
global.history = { replaceState(_state, _title, url) { location.pathname = url; } };
global.window = global;
global.matchMedia = (query) => ({ matches: query.includes("prefers-reduced-motion") ? mode === "reduced" : mode === "mobile", addEventListener() {} });
global.addEventListener = () => {};

function response(body, status = 200, etag = null) { return { ok: status >= 200 && status < 300, status, headers: { get: (name) => name === "ETag" ? etag : null }, json: async () => structuredClone(body) }; }
function text(selector) { return document.querySelector(selector).textContent.trim(); }
async function click(selector) { document.querySelector(selector).click(); await Promise.resolve(); }
function pendingTimeouts() { return timeouts.size; }
function runTimeouts() { let guard = 0; while (timeouts.size) { assert.ok(++guard < 100); const [id, timer] = timeouts.entries().next().value; timeouts.delete(id); timer.callback(); } }
async function runInterval(delay) { const entry = [...intervals.entries()].find(([, timer]) => timer.delay === delay); assert.ok(entry, `missing ${delay}ms interval`); await entry[1].callback(); await Promise.resolve(); await Promise.resolve(); }
function activeEdges() { return [...document.querySelectorAll("[data-flow-edge]")].filter((edge) => edge.classList.contains("is-active")); }
function conversationRows() { return nodes["[data-conversation-list]"].querySelectorAll("[data-conversation-row]"); }
function dataRows() { return nodes["[data-data-list]"].querySelectorAll("[data-data-row]"); }
function flowStrip() { return [text("[data-flow-from]"), text("[data-flow-to]"), text("[data-flow-generated]")]; }

function fullGraph() {
  const base = [{ node_id: "request", label: "Request", kind: "request", persona_id: null }, { node_id: "humanwire", label: "HumanWire", kind: "service", persona_id: null }, { node_id: "caspian-gateway", label: "Caspian Gateway", kind: "gateway", persona_id: null }];
  const people = catalog.stakeholders.slice(0, 7).map((person) => ({ node_id: `persona-${person.persona_id}`, label: person.display_name, kind: "stakeholder", persona_id: person.persona_id, role: person.role, initials: person.display_name.split(" ").map((part) => part[0]).join("") }));
  const artifacts = [["conflict", "Conflict"], ["interview", "Targeted interview"], ["evidence", "Evidence"], ["proposal", "Decision proposal"], ["approval", "Approval"], ["availability", "Availability"], ["meeting", "Meeting package"]].map(([node_id, label]) => ({ node_id, label, kind: "artifact", persona_id: null }));
  const pairs = [["request", "humanwire"], ["humanwire", "caspian-gateway"], ...people.map((person) => ["caspian-gateway", person.node_id]), ["persona-quick-a", "evidence"], ["persona-structured", "interview"], ["interview", "proposal"], ["proposal", "approval"], ["approval", "availability"], ["availability", "meeting"]];
  return { nodes: [...base, ...people, ...artifacts], edges: pairs.map(([source, destination]) => ({ source, destination, active: false })) };
}

function snapshotWithEvents(count, runState = "running", downloadsReady = false) {
  const snapshot = structuredClone(fixtures[1]); const graph = fullGraph(); snapshot.graph_nodes = graph.nodes; snapshot.graph_edges = graph.edges;
  snapshot.run_state = runState; snapshot.downloads_ready = downloadsReady; snapshot.events = []; snapshot.conversations = []; snapshot.data_points = [];
  for (let ordinal = 1; ordinal <= count; ordinal += 1) {
    const index = (ordinal - 1) % 2;
    const event = structuredClone(fixtures[1].events[index]); event.timeline_ordinal = ordinal; event.persisted_ordinal = ordinal; snapshot.events.push(event);
    const conversation = structuredClone(fixtures[1].conversations[index]); conversation.ordinal = ordinal; conversation.event_ordinal = ordinal; snapshot.conversations.push(conversation);
    const data = structuredClone(fixtures[1].data_points[index]); data.event_ordinal = ordinal; snapshot.data_points.push(data);
  }
  snapshot.current_event_ordinal = count; snapshot.total_event_count = count; snapshot.active_transition = snapshot.events.at(-1).active_transition;
  if (runState === "complete") snapshot.lifecycle = { current: "schedule", stages: ["brief", "outreach", "resolve", "approve", "schedule"], completed: ["brief", "outreach", "resolve", "approve", "schedule"] };
  return snapshot;
}

async function poll(snapshot, status = 200, etag = null) { fetchQueue.push(response(snapshot, status, etag)); await runInterval(500); }

function assertGraphGeometry() {
  const canvas = nodes["[data-flow-canvas]"]; const groups = canvas.querySelectorAll("[data-flow-node]"); assert.equal(groups.length, 17);
  const boxes = groups.map((group) => { const transform = group.getAttribute("transform").match(/translate\(([-0-9.]+) ([-0-9.]+)\)/); const rect = group.querySelector("rect"); return { id: group.getAttribute("data-flow-node"), x: Number(transform[1]), y: Number(transform[2]), width: Number(rect.getAttribute("width")), height: Number(rect.getAttribute("height")) }; });
  for (let left = 0; left < boxes.length; left += 1) for (let right = left + 1; right < boxes.length; right += 1) { const a = boxes[left]; const b = boxes[right]; const overlaps = a.x < b.x + b.width + 5 && a.x + a.width + 5 > b.x && a.y < b.y + b.height + 5 && a.y + a.height + 5 > b.y; assert.equal(overlaps, false, `${a.id} overlaps ${b.id}`); }
  const viewBox = canvas.getAttribute("viewBox").split(/\s+/).map(Number); assert.ok(viewBox[3] >= Math.max(...boxes.map((box) => box.y + box.height)) + 8);
  canvas.querySelectorAll("[data-flow-edge]").forEach((edge) => assert.notEqual(edge.getAttribute("data-lane"), null));
}

(async () => {
  const failures = [];
  const expect = (label, assertion) => { try { assertion(); } catch (error) { failures.push(`${label}: ${error.message}`); } };
  fetchQueue.push(response(catalog)); require(scriptPath); (documentListeners.DOMContentLoaded || []).forEach((callback) => callback()); await Promise.resolve(); await Promise.resolve();
  expect("catalog Elena label", () => assert.equal(participants.at(-1).parentNode.querySelector("em").textContent, "Review and approval"));
  expect("initial selected count", () => assert.equal(text("[data-stakeholder-count]"), "Seven selected"));
  conflictButton.click(); expect("template selected count", () => assert.equal(text("[data-stakeholder-count]"), "Four selected"));
  const approval = participants.find((item) => item.value === "approval"); approval.checked = false; approval.dispatch("change"); expect("manual selected count", () => assert.equal(text("[data-stakeholder-count]"), "Three selected")); launchButton.click();

  fetchQueue.push(response({ run_alias: "launch-001", workspace_url: "/runs/launch-001" }, 201)); await click("[data-start-coordination]"); await Promise.resolve();
  const four = snapshotWithEvents(4); await poll(four, 200, '"event-4"');
  assert.equal(text("[data-event-progress]"), "Event 1 of 4"); assert.equal(pendingTimeouts(), 1);
  expect("17-node graph geometry", assertGraphGeometry);
  await click("[data-pause-visuals]"); expect("Pause cancels render timeout", () => assert.equal(pendingTimeouts(), 0));
  timeouts.clear(); expect("Pause gates queued advance", () => assert.equal(text("[data-event-progress]"), "Event 1 of 4"));
  await click("[data-replay-next]"); assert.equal(text("[data-event-progress]"), "Event 2 of 4"); const selectedFlow = flowStrip();
  await poll(four, 200, '"event-4b"');
  expect("unchanged 200 preserves selected ordinal", () => assert.equal(text("[data-event-progress]"), "Event 2 of 4"));
  expect("unchanged 200 preserves strip", () => assert.deepEqual(flowStrip(), selectedFlow));
  expect("unchanged 200 reapplies active edge", () => assert.equal(activeEdges().length, 1));
  const secondPoll = fetchCalls.filter((call) => call.url === "/api/runs/launch-001").at(-1); assert.equal(secondPoll.options.headers["If-None-Match"], '"event-4"'); assert.equal(secondPoll.options.headers["X-HumanWire-Event-Ordinal"], "4");
  await poll(null, 304); expect("304 preserves selection", () => assert.equal(activeEdges().length, 1));

  await click("[data-follow-live]"); expect("Follow renders live event", () => assert.equal(text("[data-event-progress]"), "Event 4 of 4"));
  const eight = snapshotWithEvents(8); await poll(eight, 200, '"event-8"');
  expect("new queue starts at next unseen event", () => assert.equal(text("[data-event-progress]"), "Event 5 of 8"));
  runTimeouts();
  const ten = snapshotWithEvents(10); await poll(ten, 200, '"event-10"'); assert.equal(pendingTimeouts(), 1);
  await click("[data-replay-previous]"); expect("manual selection cancels render timeout", () => assert.equal(pendingTimeouts(), 0)); const manualProgress = text("[data-event-progress]");
  timeouts.clear(); expect("manual selection gates queued advance", () => assert.equal(text("[data-event-progress]"), manualProgress));
  await click("[data-follow-live]"); expect("Follow clears queue and selects latest", () => assert.equal(text("[data-event-progress]"), "Event 10 of 10"));

  const complete = snapshotWithEvents(10, "complete", true); await poll(complete, 200, '"event-10-complete"');
  expect("terminal rebuild preserves live edge", () => assert.equal(activeEdges().length, 1));
  assert.equal(nodes["[data-download-json]"].disabled, false); assert.equal(nodes["[data-new-coordination]"].hidden, false);
  await click("[data-replay-previous]"); await click("[data-replay-play]"); await runInterval(900); expect("Play advances", () => assert.equal(text("[data-event-progress]"), "Event 10 of 10")); await runInterval(900); assert.equal([...intervals.values()].some((timer) => timer.delay === 900), false);
  await click("[data-replay-previous]"); await click("[data-replay-play]"); document.hidden = true; (documentListeners.visibilitychange || []).forEach((callback) => callback()); assert.equal([...intervals.values()].some((timer) => timer.delay === 900), false); assert.equal([...document.querySelectorAll("[data-flow-edge]")].some((edge) => edge.classList.contains("is-travelling")), false);
  dataTab.click(); assert.equal(nodes['[data-studio-state="workspace"]'].getAttribute("data-mobile-panel"), "data"); conversationTab.click(); assert.equal(nodes['[data-studio-state="workspace"]'].getAttribute("data-mobile-panel"), "conversation");

  await click("[data-new-coordination]");
  expect("reset returns composer", () => { assert.equal(nodes['[data-studio-state="composer"]'].hidden, false); assert.equal(nodes['[data-studio-state="workspace"]'].hidden, true); });
  expect("reset clears graph", () => assert.equal(nodes["[data-flow-canvas]"].children.length, 0));
  expect("reset clears rows", () => { assert.equal(conversationRows().length, 0); assert.equal(dataRows().length, 0); });
  expect("reset clears flow and outcome", () => { assert.deepEqual(flowStrip(), ["", "", ""]); assert.equal(text("[data-outcome-headline]"), ""); assert.equal(text("[data-outcome-summary]"), ""); });
  expect("reset clears progress", () => assert.equal(text("[data-event-progress]"), "Event 0 of 0"));
  expect("reset clears downloads", () => { assert.equal(nodes["[data-download-json]"].disabled, true); assert.equal(nodes["[data-download-csv]"].disabled, true); assert.equal(nodes["[data-new-coordination]"].hidden, true); });
  expect("reset clears timers", () => { assert.equal(timeouts.size, 0); assert.equal(intervals.size, 0); });
  expect("reset clears lifecycle", () => document.querySelectorAll("[data-lifecycle-stage]").forEach((item) => { assert.equal(item.classList.contains("is-current"), false); assert.equal(item.classList.contains("is-complete"), false); assert.equal(item.querySelector("small").textContent, "Pending"); }));
  assert.equal(location.pathname, "/");
  if (failures.length) throw new Error(failures.join("\n"));
})().catch((error) => { process.stderr.write(`${error.stack}\n`); process.exitCode = 1; });
