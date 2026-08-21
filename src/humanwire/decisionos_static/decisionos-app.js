(function decisionOSWorkspace(global) {
  "use strict";

  const COUNCIL_ACTIVITY_DELAY_MS = 1400;
  const MISSION_ACTIVITY_DELAY_MS = 180;
  const MISSION_QUIET_DELAY_MS = 4000;
  const MISSION_STAGES = Object.freeze([
    "outreach",
    "analysis",
    "synthesis",
    "evidence",
    "decision",
  ]);
  const MISSION_STAGE_COPY = Object.freeze({
    outreach: "Preparing the team",
    analysis: "Specialists are analyzing",
    synthesis: "Challenging the recommendation",
    evidence: "Collecting stakeholder evidence",
    decision: "Decision brief ready",
  });
  const state = {
    organizations: [],
    workspaces: [],
    activeOrganizationId: "",
    activeWorkspaceId: "",
    councilRunning: false,
    missionRunning: false,
    missionReader: null,
    mission: null,
    missionStartedAt: 0,
    missionClock: null,
    missionQuietTimer: null,
    missionStage: "outreach",
    evidence: [],
  };

  function element(selector) {
    return global.document.querySelector(selector);
  }

  function elements(selector) {
    return Array.from(global.document.querySelectorAll(selector));
  }

  function readConfig() {
    const meta = element('meta[name="humanwire-public-config"]');
    if (!meta) return {};
    try {
      return JSON.parse(meta.getAttribute("content") || "{}");
    } catch (_error) {
      return {};
    }
  }

  function cookie(name) {
    const prefix = `${name}=`;
    const row = global.document.cookie
      .split(";")
      .map((item) => item.trim())
      .find((item) => item.startsWith(prefix));
    return row ? decodeURIComponent(row.slice(prefix.length)) : "";
  }

  async function appCheckToken() {
    return global.HumanWireFirebase.appCheckToken(readConfig());
  }

  async function responseJSON(response) {
    if (response.status === 204) return {};
    try {
      return await response.json();
    } catch (_error) {
      return {error: "request_failed"};
    }
  }

  async function getJSON(path) {
    const response = await global.fetch(path, {credentials: "same-origin"});
    const result = await responseJSON(response);
    if (response.status === 401) global.location.assign("/signin");
    if (!response.ok) throw new Error(result.error || "request_failed");
    return result;
  }

  async function postJSON(path, payload) {
    const response = await global.fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-Firebase-AppCheck": await appCheckToken(),
        "X-HumanWire-CSRF": cookie("__Host-humanwire-csrf"),
      },
      body: JSON.stringify(payload),
    });
    const result = await responseJSON(response);
    if (response.status === 401) global.location.assign("/signin");
    if (!response.ok) throw new Error(result.error || "request_failed");
    return result;
  }

  async function postStream(path, payload) {
    const response = await global.fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-Firebase-AppCheck": await appCheckToken(),
        "X-HumanWire-CSRF": cookie("__Host-humanwire-csrf"),
      },
      body: JSON.stringify(payload),
    });
    if (response.status === 401) global.location.assign("/signin");
    if (!response.ok || !response.body) {
      const result = await responseJSON(response);
      throw new Error(result.error || "request_failed");
    }
    return response.body.getReader();
  }

  function setStatus(message, tone) {
    const target = element("[data-app-status]");
    if (!target) return;
    target.textContent = message;
    if (tone) target.dataset.tone = tone;
    else delete target.dataset.tone;
  }

  function replaceOptions(select, rows, valueKey, labelKey) {
    select.replaceChildren();
    for (const row of rows) {
      const option = global.document.createElement("option");
      option.value = row[valueKey];
      option.textContent = row[labelKey];
      select.append(option);
    }
  }

  function show(selector, visible) {
    const target = element(selector);
    if (target) target.hidden = !visible;
  }

  function setPanel(name) {
    for (const panel of elements("[data-panel]")) {
      panel.hidden = panel.dataset.panel !== name;
    }
    for (const button of elements("[data-panel-target]")) {
      button.setAttribute("aria-selected", String(button.dataset.panelTarget === name));
    }
    const heading = element(`[data-panel="${name}"] h1`);
    if (heading) heading.focus({preventScroll: false});
  }

  function activeOrganization() {
    return state.organizations.find(
      (item) => item.organization_id === state.activeOrganizationId,
    );
  }

  function activeWorkspace() {
    return state.workspaces.find((item) => item.workspace_id === state.activeWorkspaceId);
  }

  function renderWorkspace() {
    const workspace = activeWorkspace();
    const organization = activeOrganization();
    show("[data-empty-organizations]", state.organizations.length === 0);
    show(
      "[data-empty-workspaces]",
      state.organizations.length > 0 && state.workspaces.length === 0,
    );
    show("[data-workspace-content]", Boolean(workspace));
    const title = element("[data-workspace-title]");
    const summary = element("[data-workspace-summary]");
    const role = element("[data-current-role]");
    if (workspace && title && summary) {
      title.textContent = workspace.name;
      summary.textContent =
        workspace.playbook === "fundraising_readiness"
          ? "Turn scattered evidence into an investor-ready decision package."
          : "Move a consequential launch from request through evidence and human approval.";
    } else if (title && summary) {
      title.textContent = organization ? organization.name : "Your decision workspace";
      summary.textContent = organization
        ? "Create a workspace for the decision your team needs to make."
        : "Create an organization to establish ownership, roles, and evidence boundaries.";
    }
    if (role && organization) {
      const label = organization.role.replaceAll("_", " ");
      role.textContent = label.charAt(0).toUpperCase() + label.slice(1);
    }
    for (const playbook of elements("[data-playbook]")) {
      playbook.setAttribute(
        "aria-selected",
        String(Boolean(workspace) && playbook.dataset.playbook === workspace.playbook),
      );
    }
  }

  function councilNode(specialistId) {
    return element(`[data-specialist="${specialistId}"]`);
  }

  function setCouncilNodeStatus(specialistId, status) {
    const node = councilNode(specialistId);
    if (!node) return;
    node.dataset.status = status;
    const label = node.querySelector("small");
    if (label) {
      label.textContent =
        status === "complete"
          ? "Complete"
          : status === "running"
            ? "Working"
            : status === "required"
              ? "Required"
            : status === "failed"
              ? "Stopped"
              : status === "blocked"
                ? "Blocked"
                : "Waiting";
    }
    const profile = element(`[data-agent-profile="${specialistId}"]`);
    if (!profile) return;
    profile.dataset.status = status;
    const profileStatus = profile.querySelector("[data-agent-live-status]");
    const task = profile.querySelector("[data-agent-current-task]");
    const copy =
      status === "complete"
        ? ["Complete", "Handoff saved"]
        : status === "running"
          ? ["Working", "Reviewing workspace evidence"]
          : status === "required"
            ? ["Required", "Human approval is required"]
          : status === "failed"
            ? ["Stopped", "Last safe state retained"]
            : status === "blocked"
              ? ["Blocked", "Waiting for human resolution"]
              : ["Ready", "Ready for assignment"];
    if (profileStatus) profileStatus.textContent = copy[0];
    if (task) task.textContent = copy[1];
  }

  function resetCouncilView() {
    for (const node of elements("[data-council-flow] [data-specialist]")) {
      setCouncilNodeStatus(node.dataset.specialist, "waiting");
    }
    const activity = element("[data-council-activity]");
    if (activity) activity.replaceChildren();
    show("[data-council-live]", false);
    show("[data-council-result]", false);
    const stateLabel = element("[data-council-state]");
    if (stateLabel) stateLabel.textContent = "Ready to run";
  }

  function appendCouncilActivity(event) {
    if (!event || typeof event.specialist_id !== "string") return;
    const status =
      event.status === "started"
        ? "running"
        : event.status === "completed"
          ? "complete"
          : event.status;
    setCouncilNodeStatus(event.specialist_id, status);
    show("[data-council-live]", true);
    const activity = element("[data-council-activity]");
    if (!activity) return;
    const row = global.document.createElement("li");
    const name = global.document.createElement("strong");
    const detail = global.document.createElement("span");
    name.textContent = event.display_name;
    const handoffs = {
      market_intelligence: "Decision Synthesis",
      financial_analysis: "Decision Synthesis",
      product_technical: "Decision Synthesis",
      risk_compliance: "Decision Synthesis",
      decision_synthesis: "Red Team",
      red_team: "Final Synthesis",
      final_synthesis: "Human review",
    };
    detail.textContent =
      event.status === "completed"
        ? `Completed · handoff to ${handoffs[event.specialist_id] || "next stage"}`
        : "Started · reviewing assigned inputs";
    row.append(name, detail);
    activity.append(row);
    row.scrollIntoView?.({block: "nearest", behavior: "auto"});
  }

  function appendMissionCouncilActivity(event, specialistId, status) {
    const activity = element("[data-council-activity]");
    if (!activity || !Number.isInteger(event.ordinal)) return;
    if (activity.querySelector?.(`[data-mission-event-ordinal="${event.ordinal}"]`)) return;
    const row = global.document.createElement("li");
    const name = global.document.createElement("strong");
    const detail = global.document.createElement("span");
    const suffix = / (?:started|completed|stopped) analysis\.$/;
    name.textContent = String(event.summary || "Agent Council activity").replace(suffix, "");
    detail.textContent = status === "running"
      ? "Working · reviewing assigned evidence"
      : status === "complete"
        ? "Complete · handoff saved"
        : "Stopped · last safe state retained";
    row.dataset.missionEventOrdinal = String(event.ordinal);
    row.dataset.specialist = specialistId;
    row.append(name, detail);
    activity.append(row);
    show("[data-council-live]", true);
    row.scrollIntoView?.({block: "nearest", behavior: "auto"});
  }

  function renderLatestDecision(projection) {
    const card = element("[data-latest-decision]");
    if (!card || !projection?.recommendation_summary) return;
    const status = element("[data-latest-decision-state]");
    const objective = element("[data-latest-decision-objective]");
    const summary = element("[data-latest-decision-summary]");
    const run = element("[data-latest-decision-run]");
    if (status) {
      const value = projection.state.replaceAll("_", " ");
      status.textContent = value.charAt(0).toUpperCase() + value.slice(1);
    }
    if (objective) objective.textContent = projection.objective;
    if (summary) summary.textContent = projection.recommendation_summary;
    if (run) run.textContent = projection.run_id;
    card.hidden = false;
  }

  function renderEvidence(evidence) {
    state.evidence = Array.isArray(evidence) ? evidence : [];
    const hasDemo = state.evidence.some(
      (item) => item?.provenance === "synthetic_demo",
    );
    for (const disclosure of elements("[data-demo-disclosure]")) {
      disclosure.hidden = !hasDemo;
    }
    for (const selector of ["[data-evidence-preview-list]", "[data-evidence-list]"]) {
      const list = element(selector);
      if (!list) continue;
      list.replaceChildren();
      for (const item of state.evidence) {
        if (!item || typeof item.title !== "string") continue;
        const row = global.document.createElement("li");
        const title = global.document.createElement("strong");
        const detail = global.document.createElement("span");
        title.textContent = item.title;
        detail.textContent =
          item.provenance === "synthetic_demo"
            ? "Ready · Demo run sample"
            : "Ready · workspace evidence";
        row.append(title, detail);
        list.append(row);
      }
      if (!list.children.length) {
        const row = global.document.createElement("li");
        row.textContent = "No evidence records are ready yet.";
        list.append(row);
      }
    }
  }

  function renderClaimList(selector, claims, emptyLabel) {
    const list = element(selector);
    if (!list) return;
    list.replaceChildren();
    for (const claim of claims || []) {
      const row = global.document.createElement("li");
      const statement = global.document.createElement("span");
      const meta = global.document.createElement("small");
      statement.textContent = claim.statement;
      meta.textContent = claim.evidence_ids?.length
        ? `${claim.classification.replaceAll("_", " ")} · ${claim.evidence_ids.join(", ")}`
        : claim.classification.replaceAll("_", " ");
      row.append(statement, meta);
      list.append(row);
    }
    if (!list.children.length) {
      const row = global.document.createElement("li");
      row.textContent = emptyLabel;
      list.append(row);
    }
  }

  function renderCouncilProjection(projection) {
    if (!projection || !Array.isArray(projection.nodes)) return;
    for (const node of projection.nodes) {
      setCouncilNodeStatus(node.specialist_id, node.status);
    }
    setCouncilNodeStatus(
      "human_approval",
      projection.state === "human_approval_required" ? "running" : projection.state,
    );
    const stateLabel = element("[data-council-state]");
    if (stateLabel) {
      stateLabel.textContent = projection.state.replaceAll("_", " ");
    }
    renderLatestDecision(projection);
    if (!projection.recommendation_summary) return;
    const summary = element("[data-recommendation-summary]");
    const action = element("[data-recommended-action]");
    const authority = element("[data-required-human-action]");
    const digest = element("[data-recommendation-digest]");
    if (summary) summary.textContent = projection.recommendation_summary;
    if (action) action.textContent = projection.recommended_action;
    if (authority) authority.textContent = projection.required_human_action;
    if (digest) digest.textContent = projection.recommendation_digest;
    renderClaimList(
      "[data-evidence-claims]",
      projection.evidence_claims,
      "No source-backed claim was accepted.",
    );
    renderClaimList(
      "[data-inference-claims]",
      projection.inference_claims,
      "No uncited model inference remains.",
    );
    const challenges = element("[data-council-challenges]");
    if (challenges) {
      challenges.replaceChildren();
      for (const challenge of projection.challenges || []) {
        const row = global.document.createElement("li");
        row.textContent = `${challenge.issue} Required: ${challenge.required_action}`;
        challenges.append(row);
      }
    }
    show("[data-council-result]", true);
  }

  async function loadLatestCouncil() {
    if (!state.activeOrganizationId || !state.activeWorkspaceId) return;
    try {
      const result = await getJSON(
        `/api/organizations/${state.activeOrganizationId}/workspaces/${state.activeWorkspaceId}/council-runs/latest`,
      );
      if (result.projection) renderCouncilProjection(result.projection);
      else resetCouncilView();
    } catch (_error) {
      resetCouncilView();
    }
  }

  async function loadEvidence() {
    if (!state.activeOrganizationId || !state.activeWorkspaceId) return;
    const result = await getJSON(
      `/api/organizations/${state.activeOrganizationId}/workspaces/${state.activeWorkspaceId}/evidence`,
    );
    renderEvidence(result.evidence || []);
  }

  async function loadDemoEvidence() {
    if (!state.activeOrganizationId || !state.activeWorkspaceId) return;
    const buttons = elements("[data-demo-evidence]");
    for (const button of buttons) button.disabled = true;
    setStatus("Preparing the Demo run company…");
    try {
      const result = await postJSON(
        `/api/organizations/${state.activeOrganizationId}/workspaces/${state.activeWorkspaceId}/demo-evidence`,
        {confirm: "synthetic_demo"},
      );
      renderEvidence(result.evidence || []);
      setStatus("Demo run evidence is ready. Start a mission to watch the team work.");
    } catch (_error) {
      setStatus("The demo company could not be created. Try again.", "error");
    } finally {
      for (const button of buttons) button.disabled = false;
    }
  }

  function waitForCouncilActivity() {
    return new Promise((resolve) => {
      global.setTimeout(resolve, COUNCIL_ACTIVITY_DELAY_MS);
    });
  }

  async function consumeCouncilStream(reader) {
    const decoder = new TextDecoder();
    let buffer = "";
    let terminal = false;
    while (true) {
      const chunk = await reader.read();
      buffer += decoder.decode(chunk.value || new Uint8Array(), {stream: !chunk.done});
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line) continue;
        const envelope = JSON.parse(line);
        if (envelope.type === "started") {
          const stateLabel = element("[data-council-state]");
          if (stateLabel) stateLabel.textContent = "Running";
        } else if (envelope.type === "activity") {
          appendCouncilActivity(envelope.event);
          await waitForCouncilActivity();
        } else if (envelope.type === "complete") {
          terminal = true;
          renderCouncilProjection(envelope.projection);
        } else if (envelope.type === "failed") {
          terminal = true;
          throw new Error("council_unavailable");
        }
      }
      if (chunk.done) break;
    }
    if (!terminal) throw new Error("council_stream_ended");
  }

  async function runCouncil(event) {
    event.preventDefault();
    if (state.councilRunning || !state.activeWorkspaceId) return;
    const form = event.currentTarget;
    const objective = form.elements.objective.value.trim();
    if (objective.length < 10) return;
    state.councilRunning = true;
    const button = element("[data-run-council]");
    if (button) button.disabled = true;
    resetCouncilView();
    const stateLabel = element("[data-council-state]");
    if (stateLabel) stateLabel.textContent = "Running";
    show("[data-council-live]", true);
    setStatus("The Agent Council is investigating this decision…");
    try {
      const reader = await postStream(
        `/api/organizations/${state.activeOrganizationId}/workspaces/${state.activeWorkspaceId}/council-runs`,
        {objective},
      );
      await consumeCouncilStream(reader);
      setStatus("Agent Council complete. Human approval is required.");
    } catch (_error) {
      const stateLabel = element("[data-council-state]");
      if (stateLabel) stateLabel.textContent = "Stopped safely";
      setStatus("The Agent Council stopped. The last saved activity remains available.", "error");
    } finally {
      state.councilRunning = false;
      if (button) button.disabled = false;
    }
  }

  function missionPath(missionId) {
    const base = `/api/organizations/${state.activeOrganizationId}/workspaces/${state.activeWorkspaceId}/missions`;
    return missionId ? `${base}/${missionId}` : base;
  }

  function missionStateLabel(value) {
    return String(value || "ready")
      .replaceAll("_", " ")
      .replace(/^./, (character) => character.toUpperCase());
  }

  function formatMissionElapsed(milliseconds) {
    const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = String(totalSeconds % 60).padStart(2, "0");
    return `${minutes}:${seconds}`;
  }

  function setMissionElapsed(prefix = "Running") {
    const target = element("[data-mission-elapsed]");
    if (!target || !state.missionStartedAt) return;
    const separator = /(?:in|after)$/.test(prefix) ? " " : " · ";
    target.textContent = `${prefix}${separator}${formatMissionElapsed(Date.now() - state.missionStartedAt)}`;
  }

  function clearMissionTimers(finalPrefix) {
    if (state.missionClock !== null) {
      global.clearInterval?.(state.missionClock);
      state.missionClock = null;
    }
    if (state.missionQuietTimer !== null) {
      global.clearTimeout?.(state.missionQuietTimer);
      state.missionQuietTimer = null;
    }
    if (finalPrefix && state.missionStartedAt) {
      setMissionElapsed(finalPrefix);
    } else if (finalPrefix) {
      const target = element("[data-mission-elapsed]");
      if (target) target.textContent = finalPrefix === "Completed in"
        ? "Completed"
        : finalPrefix.replace(/ (?:in|after)$/, "");
    }
  }

  function startMissionTimers() {
    clearMissionTimers();
    state.missionStartedAt = Date.now();
    setMissionElapsed();
    state.missionClock = global.setInterval?.(() => setMissionElapsed(), 1000) ?? null;
  }

  function setMissionPulse(message, tone) {
    const target = element("[data-mission-pulse]");
    if (!target) return;
    target.textContent = message;
    if (tone) target.dataset.tone = tone;
    else delete target.dataset.tone;
  }

  function quietMissionCopy() {
    return {
      outreach: "HumanWire is still assembling the right specialists.",
      analysis: "Gemini specialists are still analyzing the saved evidence.",
      synthesis: "The Agent Council is still challenging and synthesizing the recommendation.",
      evidence: "HumanWire is still organizing the stakeholder evidence.",
      decision: "HumanWire is still preparing the decision brief.",
    }[state.missionStage] || "HumanWire is still coordinating this mission.";
  }

  function armMissionQuietStatus() {
    if (state.missionQuietTimer !== null) global.clearTimeout?.(state.missionQuietTimer);
    state.missionQuietTimer = global.setTimeout?.(() => {
      if (!state.missionRunning) return;
      setMissionPulse(`Still working · ${quietMissionCopy()}`);
    }, MISSION_QUIET_DELAY_MS) ?? null;
  }

  async function waitForMissionPaint() {
    if (global.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) return;
    await new Promise((resolve) => global.setTimeout(resolve, MISSION_ACTIVITY_DELAY_MS));
  }

  function setMissionProgress(stage, terminalState) {
    const normalized = MISSION_STAGES.includes(stage) ? stage : "outreach";
    state.missionStage = normalized;
    const currentIndex = MISSION_STAGES.indexOf(normalized);
    const complete = terminalState === "complete";
    for (const node of elements("[data-mission-progress] [data-mission-step]")) {
      const index = MISSION_STAGES.indexOf(node.dataset.missionStep);
      node.dataset.state = complete || index < currentIndex
        ? "complete"
        : index === currentIndex
          ? "current"
          : "upcoming";
    }
    const meter = element("[data-mission-progress-meter]");
    if (meter) meter.value = complete ? MISSION_STAGES.length : currentIndex + 1;
    const summary = element("[data-mission-progress-summary]");
    if (summary) summary.textContent = complete
      ? "Decision brief ready"
      : MISSION_STAGE_COPY[normalized];
  }

  function setMissionBusy(busy) {
    const form = element("[data-mission-form]");
    if (form) form.setAttribute("aria-busy", String(Boolean(busy)));
    const workspace = element("[data-mission-workspace]");
    if (workspace) workspace.dataset.state = busy ? "running" : "settled";
  }

  function prepareMissionExperience(payload) {
    const stateLabel = element("[data-mission-state]");
    const objective = element("[data-mission-objective]");
    const mode = element("[data-mission-mode-label]");
    const stage = element("[data-mission-stage]");
    const next = element("[data-mission-next-action]");
    const recommendation = element("[data-mission-recommendation]");
    const blocked = element("[data-mission-blocked]");
    const participants = element("[data-mission-participants]");
    const timeline = element("[data-mission-timeline]");
    const button = element("[data-start-mission]");
    show("[data-mission-workspace]", true);
    show("[data-new-mission]", false);
    if (stateLabel) stateLabel.textContent = "Running";
    if (objective) objective.textContent = payload.objective;
    if (mode) mode.textContent = payload.mode === "connected_organization"
      ? "Connected organization"
      : "Demo run";
    if (stage) stage.textContent = "Preparing team";
    if (next) next.textContent = "Watch the team gather and challenge the evidence.";
    if (recommendation) recommendation.textContent = "HumanWire is assembling the right people and agents.";
    if (blocked) {
      blocked.textContent = "";
      blocked.hidden = true;
    }
    if (participants) {
      const item = global.document.createElement("li");
      const identity = global.document.createElement("strong");
      const detail = global.document.createElement("span");
      identity.textContent = "Matching roles to this agenda";
      detail.textContent = "The participant roster will appear as soon as it is saved.";
      item.dataset.actor = "preparing";
      item.append(identity, detail);
      participants.replaceChildren(item);
    }
    if (timeline) timeline.replaceChildren();
    if (button) {
      button.disabled = true;
      button.textContent = "Starting mission…";
    }
    resetCouncilView();
    const councilState = element("[data-council-state]");
    if (councilState) councilState.textContent = "Agent Council starting";
    setMissionBusy(true);
    setMissionProgress("outreach");
    setMissionPulse("HumanWire is assembling the right people and agents…");
    startMissionTimers();
    armMissionQuietStatus();
    element("[data-mission-workspace]")?.scrollIntoView?.({block: "nearest", behavior: "auto"});
  }

  function updateMissionReadiness() {
    const selected = element('[data-mission-mode]:checked');
    const target = element("[data-mission-readiness]");
    if (!target) return;
    target.textContent = selected?.value === "connected_organization"
      ? "Connected organization uses activated members and exact consented routes. Missing configuration stops safely."
      : "Demo run is ready. AI actors stay clearly labeled and no provider is contacted.";
  }

  function renderMissionParticipants(participants) {
    const target = element("[data-mission-participants]");
    if (!target) return;
    target.replaceChildren();
    for (const participant of participants || []) {
      const item = global.document.createElement("li");
      const identity = global.document.createElement("strong");
      const role = global.document.createElement("span");
      const actor = global.document.createElement("small");
      identity.textContent = participant.display_name || "Mission participant";
      role.textContent = participant.role || "Decision participant";
      actor.textContent = participant.actor_label || "Participant";
      item.dataset.actor = String(participant.actor_label || "participant")
        .toLowerCase()
        .replaceAll(" ", "-");
      item.append(identity, role, actor);
      target.append(item);
    }
  }

  function appendMissionEvent(event) {
    if (!event || !Number.isInteger(event.ordinal)) return;
    const target = element("[data-mission-timeline]");
    if (target && !target.querySelector?.(`[data-event-ordinal="${event.ordinal}"]`)) {
      const item = global.document.createElement("li");
      const marker = global.document.createElement("span");
      const copy = global.document.createElement("div");
      const summary = global.document.createElement("strong");
      const stage = global.document.createElement("small");
      item.dataset.eventOrdinal = String(event.ordinal);
      marker.textContent = String(event.ordinal).padStart(2, "0");
      summary.textContent = event.summary || "Mission activity saved.";
      stage.textContent = missionStateLabel(event.stage);
      copy.append(summary, stage);
      item.append(marker, copy);
      target.append(item);
      item.scrollIntoView?.({block: "nearest"});
    }
    const stageLabel = element("[data-mission-stage]");
    if (stageLabel) stageLabel.textContent = missionStateLabel(event.stage);
    setMissionProgress(event.stage);
    setMissionPulse(event.summary || "Mission activity saved.");
    if (state.missionRunning) armMissionQuietStatus();
    const participantId = String(event.participant_id || "");
    if (participantId.startsWith("ai-")) {
      const specialistId = participantId.slice(3).replaceAll("-", "_");
      let status = null;
      if (event.kind === "council.specialist_started") {
        status = "running";
      } else if (event.kind === "council.specialist_completed") {
        status = "complete";
      } else if (event.kind === "council.specialist_failed") {
        status = "failed";
      }
      if (status) {
        setCouncilNodeStatus(specialistId, status);
        appendMissionCouncilActivity(event, specialistId, status);
        const councilState = element("[data-council-state]");
        if (councilState) councilState.textContent = status === "failed"
          ? "Agent Council needs attention"
          : "Agent Council working";
      }
    }
  }

  function renderMission(projection) {
    if (!projection || !Array.isArray(projection.participants) || !Array.isArray(projection.events)) {
      throw new Error("mission_unavailable");
    }
    const preserveLiveRows = Boolean(
      state.missionRunning
      && state.mission?.mission_id === projection.mission_id,
    );
    state.mission = projection;
    show("[data-mission-workspace]", true);
    show("[data-new-mission]", true);
    const start = element("[data-start-mission]");
    if (start) start.hidden = true;
    const stateLabel = element("[data-mission-state]");
    const objective = element("[data-mission-objective]");
    const mode = element("[data-mission-mode-label]");
    const stage = element("[data-mission-stage]");
    const next = element("[data-mission-next-action]");
    const recommendation = element("[data-mission-recommendation]");
    const blocked = element("[data-mission-blocked]");
    const effectiveState = state.missionRunning && projection.state === "ready"
      ? "running"
      : projection.state;
    if (stateLabel) stateLabel.textContent = missionStateLabel(effectiveState);
    if (objective) objective.textContent = projection.objective || "HumanWire mission";
    if (mode) mode.textContent = projection.mode_label || "Mission";
    if (stage) stage.textContent = missionStateLabel(
      state.missionRunning && projection.stage === "request" ? "outreach" : projection.stage,
    );
    if (next) next.textContent = projection.next_action || "Review the saved mission.";
    if (recommendation) {
      recommendation.textContent = projection.recommendation_summary
        || "HumanWire is collecting the evidence needed for this decision.";
    }
    const blockedCopy = {
      no_eligible_participant: "No activated organization member is eligible for this mission.",
      no_consented_route: "The selected member has no active consented communication route.",
      provider_not_configured: "Connected delivery is not configured for this organization.",
      delivery_failed: "The provider did not confirm delivery. No response was invented.",
      delivery_state_unknown: "Delivery could not be confirmed. HumanWire will not retry blindly.",
      organization_not_ready: "Finish organization onboarding before using Connected organization.",
    };
    if (blocked) {
      blocked.textContent = blockedCopy[projection.blocked_reason] || "";
      blocked.hidden = !projection.blocked_reason;
    }
    renderMissionParticipants(projection.participants);
    const timeline = element("[data-mission-timeline]");
    if (!preserveLiveRows) {
      if (timeline) timeline.replaceChildren();
      resetCouncilView();
    }
    for (const event of projection.events) appendMissionEvent(event);
    if (projection.state === "complete") {
      setMissionProgress("decision", "complete");
      setMissionPulse("The decision brief is ready for human review.");
      setCouncilNodeStatus("human_approval", "required");
      const councilState = element("[data-council-state]");
      if (councilState) councilState.textContent = "Decision brief ready";
      setMissionBusy(false);
      clearMissionTimers("Completed in");
    } else if (projection.state === "awaiting_response") {
      setMissionPulse("Outreach is delivered. HumanWire is waiting for an organization response.");
      const councilState = element("[data-council-state]");
      if (councilState) councilState.textContent = "Waiting for response";
      setMissionBusy(false);
      clearMissionTimers("Waiting after");
    } else if (projection.state === "blocked") {
      setMissionPulse("Connected organization stopped safely. Review the readiness requirement.", "error");
      const councilState = element("[data-council-state]");
      if (councilState) councilState.textContent = "Mission blocked safely";
      setMissionBusy(false);
      clearMissionTimers("Stopped after");
    } else if (state.missionRunning) {
      setMissionProgress(projection.stage === "request" ? "outreach" : projection.stage);
      const councilState = element("[data-council-state]");
      if (councilState && projection.events.length < 2) {
        councilState.textContent = "Agent Council starting";
      }
    }
    show("[data-demo-disclosure]", projection.mode === "demo_run");
    if (global.history?.replaceState && projection.mission_id) {
      global.history.replaceState(null, "", `/workspace#mission=${projection.mission_id}`);
    }
  }

  async function createMission(payload) {
    const result = await postJSON(missionPath(), payload);
    if (!result.mission) throw new Error("mission_unavailable");
    renderMission(result.mission);
    return result.mission;
  }

  async function runMission(missionId) {
    return postStream(`${missionPath(missionId)}/run`, {});
  }

  async function consumeMissionStream(reader) {
    state.missionReader = reader;
    const decoder = new TextDecoder();
    let buffer = "";
    let terminal = false;
    while (true) {
      const chunk = await reader.read();
      if (reader !== state.missionReader) return;
      buffer += decoder.decode(chunk.value || new Uint8Array(), {stream: !chunk.done});
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      const envelopes = lines.filter(Boolean).map((line) => JSON.parse(line));
      for (const [index, envelope] of envelopes.entries()) {
        if (envelope.type === "started") {
          const stateLabel = element("[data-mission-state]");
          if (stateLabel) stateLabel.textContent = "Running";
          const councilState = element("[data-council-state]");
          if (councilState) councilState.textContent = "Agent Council starting";
          setMissionProgress("outreach");
          setMissionPulse("Team assembled. Specialists are starting their analysis.");
          armMissionQuietStatus();
        } else if (envelope.type === "activity") {
          appendMissionEvent(envelope.event);
          if (envelopes.slice(index + 1).some((item) => item.type === "activity")) {
            await waitForMissionPaint();
          }
        } else if (["complete", "awaiting_response", "blocked"].includes(envelope.type)) {
          terminal = true;
          renderMission(envelope.mission);
        } else if (envelope.type === "failed") {
          terminal = true;
          throw new Error("mission_unavailable");
        }
      }
      if (chunk.done) break;
    }
    if (!terminal) throw new Error("mission_stream_ended");
  }

  function resetMission() {
    const previous = state.missionReader;
    state.missionReader = null;
    state.mission = null;
    state.missionRunning = false;
    if (previous?.cancel) previous.cancel().catch?.(() => {});
    show("[data-mission-workspace]", false);
    show("[data-new-mission]", false);
    const start = element("[data-start-mission]");
    const stateLabel = element("[data-mission-state]");
    const timeline = element("[data-mission-timeline]");
    if (start) {
      start.hidden = false;
      start.disabled = false;
      start.textContent = "Start mission";
    }
    if (stateLabel) stateLabel.textContent = "Ready";
    if (timeline) timeline.replaceChildren();
    clearMissionTimers();
    state.missionStartedAt = 0;
    const elapsed = element("[data-mission-elapsed]");
    if (elapsed) elapsed.textContent = "Not started";
    setMissionBusy(false);
    setMissionProgress("outreach");
    setMissionPulse("Waiting to start.");
    if (global.history?.replaceState) global.history.replaceState(null, "", "/workspace");
    resetCouncilView();
    updateMissionReadiness();
  }

  async function startMission(event) {
    event.preventDefault();
    if (state.missionRunning || !state.activeWorkspaceId) return;
    const form = event.currentTarget;
    const payload = {
      mode: form.elements.mode.value,
      objective: form.elements.objective.value.trim(),
      urgency: form.elements.urgency.value,
      include_conflict: Boolean(form.elements.include_conflict.checked),
    };
    if (payload.objective.length < 12) return;
    state.missionRunning = true;
    const button = element("[data-start-mission]");
    prepareMissionExperience(payload);
    setStatus("HumanWire is assembling the right people and agents…");
    try {
      const mission = await createMission(payload);
      const reader = await runMission(mission.mission_id);
      await consumeMissionStream(reader);
      const message = state.mission?.state === "awaiting_response"
        ? "Outreach is delivered. HumanWire is waiting for the organization response."
        : state.mission?.state === "blocked"
          ? "Connected organization stopped safely. Review the readiness requirement."
          : "Decision brief ready. Review the evidence and next action.";
      setStatus(message, state.mission?.state === "blocked" ? "error" : undefined);
    } catch (_error) {
      const stateLabel = element("[data-mission-state]");
      if (stateLabel) stateLabel.textContent = "Stopped safely";
      const councilState = element("[data-council-state]");
      if (councilState) councilState.textContent = "Mission stopped safely";
      setMissionPulse("Updates stopped before the brief was ready. The last saved activity is still visible.", "error");
      setMissionBusy(false);
      clearMissionTimers("Stopped after");
      if (button) {
        button.hidden = false;
        button.disabled = false;
        button.textContent = "Retry mission";
      }
      setStatus("The mission stopped. The last saved activity remains available.", "error");
    } finally {
      state.missionRunning = false;
      setMissionBusy(false);
      if (button && !state.mission) {
        button.disabled = false;
        if (button.textContent === "Starting mission…") button.textContent = "Start mission";
      }
    }
  }

  async function loadMissionFromHash() {
    const hash = String(global.location.hash || "");
    const matched = /^#mission=(mis_[0-9A-HJKMNP-TV-Z]{26})$/.exec(hash);
    if (!matched || !state.activeWorkspaceId) return;
    const result = await getJSON(missionPath(matched[1]));
    if (result.mission) renderMission(result.mission);
  }

  async function loadWorkspaces(organizationId) {
    state.activeOrganizationId = organizationId;
    const result = await getJSON(`/api/organizations/${organizationId}/workspaces`);
    state.workspaces = result.workspaces || [];
    const select = element("[data-workspace-list]");
    if (select) {
      replaceOptions(select, state.workspaces, "workspace_id", "name");
      select.disabled = state.workspaces.length === 0;
    }
    state.activeWorkspaceId = state.workspaces[0]?.workspace_id || "";
    renderWorkspace();
    await loadLatestCouncil();
    await loadEvidence().catch(() => renderEvidence([]));
    await loadMissionFromHash().catch(() => resetMission());
  }

  async function loadOrganizations() {
    const result = await getJSON("/api/organizations");
    state.organizations = result.organizations || [];
    const select = element("[data-organization-list]");
    if (select) {
      replaceOptions(select, state.organizations, "organization_id", "name");
      select.disabled = state.organizations.length === 0;
    }
    if (state.organizations.length === 0) {
      state.activeOrganizationId = "";
      state.activeWorkspaceId = "";
      state.workspaces = [];
      renderWorkspace();
      return;
    }
    await loadWorkspaces(state.organizations[0].organization_id);
  }

  async function createOrganization(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const name = form.elements.name.value.trim();
    if (!name) return;
    setStatus("Creating your organization…");
    try {
      await postJSON("/api/organizations", {name});
      form.reset();
      await loadOrganizations();
      setStatus("Organization created. You are its owner.");
    } catch (_error) {
      setStatus("The organization could not be created. Try again.", "error");
    }
  }

  async function createWorkspace(event) {
    event.preventDefault();
    if (!state.activeOrganizationId) return;
    const form = event.currentTarget;
    const name = form.elements.name.value.trim();
    const playbook = form.elements.playbook.value;
    if (!name) return;
    setStatus("Creating the decision workspace…");
    try {
      const workspace = await postJSON(
        `/api/organizations/${state.activeOrganizationId}/workspaces`,
        {name, playbook},
      );
      form.reset();
      await loadWorkspaces(state.activeOrganizationId);
      state.activeWorkspaceId = workspace.workspace_id;
      const select = element("[data-workspace-list]");
      if (select) select.value = state.activeWorkspaceId;
      renderWorkspace();
      setStatus("Decision workspace created.");
    } catch (_error) {
      setStatus("The workspace could not be created. Check your role and try again.", "error");
    }
  }

  function startDecision() {
    if (!state.activeOrganizationId) {
      show("[data-empty-organizations]", true);
      element("#organization-name")?.focus();
      return;
    }
    if (!state.activeWorkspaceId) {
      show("[data-empty-workspaces]", true);
      element("#workspace-name")?.focus();
      setStatus("Name the decision workspace and choose its playbook.");
      return;
    }
    setPanel("home");
    element("#mission-objective")?.focus();
    element("[data-mission-form]")?.scrollIntoView?.({block: "center"});
    setStatus("Choose a mode, set the agenda, and start the mission.");
  }

  function choosePlaybook(event) {
    const value = event.currentTarget.dataset.playbook;
    startDecision();
    const input = element(`input[name="playbook"][value="${value}"]`);
    if (input) input.checked = true;
  }

  async function createInvitation(event) {
    event.preventDefault();
    if (!state.activeOrganizationId) return;
    const role = event.currentTarget.elements.role.value;
    setStatus("Creating a one-time invitation…");
    try {
      const invitation = await postJSON(
        `/api/organizations/${state.activeOrganizationId}/invitations`,
        {role},
      );
      const result = element("[data-invitation-result]");
      const token = element("[data-invitation-token]");
      if (token) token.textContent = invitation.invitation_token;
      if (result) result.hidden = false;
      setStatus("Invitation created. Share it through a trusted channel.");
    } catch (_error) {
      setStatus("The invitation could not be created. Check your role and try again.", "error");
    }
  }

  async function acceptInvitation(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const invitationToken = form.elements.invitation_token.value.trim();
    if (!invitationToken) return;
    setStatus("Joining the organization…");
    try {
      await postJSON("/api/invitations/accept", {invitation_token: invitationToken});
      form.reset();
      await loadOrganizations();
      setStatus("Invitation accepted. Organization access is active.");
    } catch (_error) {
      setStatus("This invitation is unavailable or has already been used.", "error");
    }
  }

  async function signOut() {
    setStatus("Signing out…");
    try {
      await postJSON("/api/session/logout", {confirm: true});
      await global.HumanWireFirebase.signOut(readConfig());
      global.location.assign("/signin");
    } catch (_error) {
      setStatus("Sign out could not be completed. Try again.", "error");
    }
  }

  function initialize() {
    for (const target of elements("[data-panel-target]")) {
      target.addEventListener("click", () => setPanel(target.dataset.panelTarget));
    }
    for (const target of elements("[data-new-decision]")) {
      target.addEventListener("click", startDecision);
    }
    for (const target of elements("[data-open-invite]")) {
      target.addEventListener("click", () => setPanel("team"));
    }
    for (const target of elements("[data-demo-evidence]")) {
      target.addEventListener("click", loadDemoEvidence);
    }
    for (const target of elements("[data-playbook]")) {
      target.addEventListener("click", choosePlaybook);
    }
    element("[data-create-organization]")?.addEventListener("submit", createOrganization);
    element("[data-create-workspace]")?.addEventListener("submit", createWorkspace);
    element("[data-invite-member]")?.addEventListener("submit", createInvitation);
    element("[data-accept-invitation]")?.addEventListener("submit", acceptInvitation);
    element("[data-council-form]")?.addEventListener("submit", runCouncil);
    element("[data-mission-form]")?.addEventListener("submit", startMission);
    element("[data-new-mission]")?.addEventListener("click", resetMission);
    for (const target of elements("[data-mission-mode]")) {
      target.addEventListener("change", updateMissionReadiness);
    }
    element("[data-sign-out]")?.addEventListener("click", signOut);
    element("[data-organization-list]")?.addEventListener("change", (event) => {
      loadWorkspaces(event.currentTarget.value).catch(() => {
        setStatus("The organization could not be loaded.", "error");
      });
    });
    element("[data-workspace-list]")?.addEventListener("change", (event) => {
      resetMission();
      state.activeWorkspaceId = event.currentTarget.value;
      renderWorkspace();
      loadLatestCouncil().catch(() => resetCouncilView());
    });
    loadOrganizations().catch(() => {
      setStatus("The workspace could not be loaded. Sign in again.", "error");
    });
    updateMissionReadiness();
  }

  global.HumanWireDecisionOSApp = Object.freeze({
    loadOrganizations,
    consumeMissionStream,
    consumeCouncilStream,
    createMission,
    loadDemoEvidence,
    postJSON,
    renderEvidence,
    renderMission,
    resetMission,
    runMission,
    startMission,
    runCouncil,
    renderCouncilProjection,
    setPanel,
  });

  if (global.document.readyState === "loading") {
    global.document.addEventListener("DOMContentLoaded", initialize, {once: true});
  } else {
    initialize();
  }
})(globalThis);
