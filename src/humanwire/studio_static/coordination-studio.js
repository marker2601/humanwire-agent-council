"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const svgNamespace = "http://www.w3.org/2000/svg";
  const composer = document.querySelector('[data-studio-state="composer"]');
  const workspace = document.querySelector('[data-studio-state="workspace"]');
  const actionToken = document.querySelector('meta[name="humanwire-action-token"]');
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const mobileLayout = window.matchMedia("(max-width: 759px)");
  const state = {
    catalog: null,
    runAlias: null,
    snapshot: null,
    events: [],
    selectedOrdinal: 0,
    etag: null,
    followLive: true,
    visualsPaused: false,
    playing: false,
    pollTimer: null,
    playTimer: null,
    renderTimer: null,
    renderQueue: [],
    rendering: false,
  };

  if (composer === null || workspace === null || actionToken === null) {
    return;
  }

  function one(selector) {
    return document.querySelector(selector);
  }

  function titleCase(value) {
    return String(value || "")
      .replaceAll("_", " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function setText(selector, value) {
    const element = one(selector);
    if (element !== null) {
      element.textContent = value == null ? "" : String(value);
    }
  }

  function setStudioState(selected) {
    composer.hidden = selected !== "composer";
    workspace.hidden = selected !== "workspace";
  }

  function selectedValue(name, fallback) {
    const selected = one(`[name="${name}"]:checked`);
    return selected === null ? fallback : selected.value;
  }

  function setSelectedValue(name, value) {
    document.querySelectorAll(`[name="${name}"]`).forEach((input) => {
      input.checked = input.value === value;
    });
  }

  function updateStakeholderCount() {
    const count = document.querySelectorAll('[name="participant_ids"]:checked').length;
    const numberWords = ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight"];
    setText("[data-stakeholder-count]", `${numberWords[count] || String(count)} selected`);
  }

  function syncStakeholderCatalog(stakeholders) {
    const byId = new Map(stakeholders.map((person) => [person.persona_id, person]));
    document.querySelectorAll('[name="participant_ids"]').forEach((input) => {
      const person = byId.get(input.value);
      const label = input.parentNode;
      if (person === undefined || label === null) {
        return;
      }
      const name = label.querySelector("strong");
      const role = label.querySelector("small");
      const engagement = label.querySelector("em");
      if (name !== null) {
        name.textContent = person.display_name;
      }
      if (role !== null) {
        role.textContent = person.role;
      }
      if (engagement !== null) {
        engagement.textContent = person.engagement_label;
      }
    });
    updateStakeholderCount();
  }

  function applyTemplate(template) {
    if (template === undefined || template === null) {
      return;
    }
    const objective = one("[data-objective]");
    const templateRoot = one("[data-current-template]");
    if (objective !== null) {
      objective.value = template.objective;
    }
    setSelectedValue("requester_role", template.requester_role);
    setSelectedValue("target_timing", template.target_timing);
    document.querySelectorAll('[name="participant_ids"]').forEach((input) => {
      input.checked = template.participant_ids.includes(input.value);
    });
    updateStakeholderCount();
    const conflict = one("[data-include-conflict]");
    if (conflict !== null) {
      conflict.checked = template.include_conflict;
    }
    if (templateRoot !== null) {
      templateRoot.setAttribute("data-current-template", template.template_id);
    }
    document.querySelectorAll("[data-template-id]").forEach((button) => {
      button.setAttribute(
        "aria-pressed",
        button.getAttribute("data-template-id") === template.template_id ? "true" : "false",
      );
    });
    updateCustomDate();
  }

  async function loadCatalog() {
    try {
      const response = await fetch("/api/catalog", {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error("catalog_unavailable");
      }
      const catalog = await response.json();
      if (!Array.isArray(catalog.templates) || !Array.isArray(catalog.stakeholders)) {
        throw new Error("catalog_invalid");
      }
      state.catalog = catalog;
      syncStakeholderCatalog(catalog.stakeholders);
      const current = one("[data-current-template]");
      const templateId = current === null
        ? "launch-decision"
        : current.getAttribute("data-current-template");
      applyTemplate(catalog.templates.find((item) => item.template_id === templateId));
    } catch (_error) {
      setText("[data-form-status]", "The coordination catalog is unavailable. Refresh to try again.");
      const start = one("[data-start-coordination]");
      if (start !== null) {
        start.disabled = true;
      }
    }
  }

  function requestPayload() {
    const currentTemplate = one("[data-current-template]");
    const customDate = one("[data-custom-date]");
    const objective = one("[data-objective]");
    const conflict = one("[data-include-conflict]");
    const timing = selectedValue("target_timing", "tomorrow");
    return {
      template_id: currentTemplate === null
        ? null
        : currentTemplate.getAttribute("data-current-template"),
      objective: objective === null ? "" : objective.value.trim(),
      requester_name: "Alex Morgan",
      requester_role: selectedValue("requester_role", "manager"),
      participant_ids: Array.from(
        document.querySelectorAll('[name="participant_ids"]:checked'),
        (input) => input.value,
      ),
      target_timing: timing,
      custom_date: timing === "custom" && customDate !== null && customDate.value
        ? customDate.value
        : null,
      include_conflict: conflict !== null && conflict.checked,
      agent_mode: selectedValue("agent_mode", "standard"),
    };
  }

  async function startRun() {
    const start = one("[data-start-coordination]");
    if (start === null || start.disabled) {
      return;
    }
    start.disabled = true;
    setText("[data-form-status]", "Starting coordination…");
    try {
      const response = await fetch("/api/runs", {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-HumanWire-Action": actionToken.getAttribute("content") || "",
        },
        body: JSON.stringify(requestPayload()),
      });
      const result = await response.json();
      if (response.status === 409 && result.error === "active_run" && result.run_alias) {
        enterWorkspace(result.run_alias, `/runs/${result.run_alias}`);
        return;
      }
      if (!response.ok || !result.run_alias || !result.workspace_url) {
        if (result.error === "model_unavailable") {
          setText("[data-form-status]", "Model-assisted mode is unavailable. Choose Standard agents.");
        } else {
          setText("[data-form-status]", "The coordination could not start. Review the form and try again.");
        }
        start.disabled = false;
        return;
      }
      enterWorkspace(result.run_alias, result.workspace_url);
    } catch (_error) {
      setText("[data-form-status]", "The coordination could not start. Try again.");
      start.disabled = false;
    }
  }

  function enterWorkspace(runAlias, workspaceUrl) {
    state.runAlias = runAlias;
    setStudioState("workspace");
    history.replaceState({ runAlias }, "", workspaceUrl);
    startPolling();
  }

  function startPolling() {
    if (state.pollTimer !== null) {
      clearInterval(state.pollTimer);
    }
    state.pollTimer = setInterval(pollRun, 500);
  }

  function stopPolling() {
    if (state.pollTimer !== null) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  async function pollRun() {
    if (state.runAlias === null) {
      return;
    }
    const headers = {
      Accept: "application/json",
      "X-HumanWire-Event-Ordinal": String(state.events.length),
    };
    if (state.etag !== null) {
      headers["If-None-Match"] = state.etag;
    }
    try {
      const response = await fetch(`/api/runs/${state.runAlias}`, { headers });
      if (response.status === 304) {
        return;
      }
      if (!response.ok) {
        throw new Error("snapshot_unavailable");
      }
      const snapshot = await response.json();
      const etag = response.headers.get("ETag");
      if (etag !== null) {
        state.etag = etag;
      }
      receiveSnapshot(snapshot);
    } catch (_error) {
      setText("[data-flow-live]", "Updates are temporarily unavailable. The last saved state remains visible.");
    }
  }

  function receiveSnapshot(snapshot) {
    if (snapshot.run_alias !== state.runAlias || !Array.isArray(snapshot.events)) {
      return;
    }
    state.snapshot = snapshot;
    setText("[data-workspace-objective]", snapshot.objective);
    setText(
      "[data-workspace-requester]",
      `${snapshot.requester_name} · ${snapshot.requester_role_label}`,
    );
    setText("[data-run-state]", titleCase(snapshot.run_state));
    setText("[data-connection-label]", snapshot.connection_label);
    setText("[data-outcome-headline]", snapshot.outcome.headline);
    setText("[data-outcome-summary]", snapshot.outcome.summary);
    renderGraph(snapshot);
    renderLifecycle(snapshot.lifecycle, snapshot.lifecycle.current);
    updateDownloads(snapshot.downloads_ready);

    const unseen = snapshot.events.filter(
      (event) => event.timeline_ordinal > state.events.length,
    );
    unseen.forEach((event) => {
      if (event.timeline_ordinal === state.events.length + 1) {
        state.events.push(event);
      }
    });

    if (state.selectedOrdinal > 0) {
      renderEvent(Math.min(state.selectedOrdinal, state.events.length));
    }

    if (state.events.length === 0) {
      setText("[data-event-progress]", "Event 0 of 0");
      setText("[data-current-stage]", titleCase(snapshot.lifecycle.current));
      renderVisibleRows(0);
    } else if (state.followLive && !state.visualsPaused && !document.hidden) {
      state.renderQueue.push(...unseen);
      drainRenderQueue();
    } else if (state.selectedOrdinal === 0) {
      renderEvent(1);
    }

    if (snapshot.run_state === "complete" || snapshot.run_state === "failed") {
      stopPolling();
    }
    updateControls();
  }

  function cancelRenderQueue() {
    if (state.renderTimer !== null) {
      clearTimeout(state.renderTimer);
      state.renderTimer = null;
    }
    state.renderQueue.length = 0;
    state.rendering = false;
  }

  function drainRenderQueue() {
    if (
      state.rendering
      || state.renderQueue.length === 0
      || state.visualsPaused
      || !state.followLive
      || document.hidden
    ) {
      return;
    }
    state.rendering = true;
    const event = state.renderQueue.shift();
    renderEvent(event.timeline_ordinal);
    state.rendering = false;
    if (state.renderQueue.length > 0) {
      state.renderTimer = setTimeout(() => {
        state.renderTimer = null;
        drainRenderQueue();
      }, 520);
    }
  }

  function graphNodeWidth(node, useMobile) {
    if (useMobile) {
      return 190;
    }
    if (node.kind === "stakeholder") {
      return 180;
    }
    if (node.kind === "artifact") {
      return 160;
    }
    if (node.kind === "gateway") {
      return 150;
    }
    return 130;
  }

  function graphPositions(nodes, useMobile) {
    const positions = {};
    if (useMobile) {
      const people = nodes.filter((item) => item.kind === "stakeholder");
      const artifacts = nodes.filter((item) => item.kind === "artifact");
      positions.request = { x: 120, y: 20 };
      positions.humanwire = { x: 120, y: 92 };
      positions["caspian-gateway"] = { x: 120, y: 164 };
      people.forEach((item, index) => {
        positions[item.node_id] = { x: index % 2 === 0 ? 12 : 218, y: 260 + Math.floor(index / 2) * 68 };
      });
      const peopleBottom = 260 + Math.ceil(people.length / 2) * 68;
      const artifactStart = peopleBottom + 52;
      artifacts.forEach((item, index) => {
        positions[item.node_id] = { x: index % 2 === 0 ? 12 : 218, y: artifactStart + Math.floor(index / 2) * 72 };
      });
      return {
        height: Math.max(920, artifactStart + Math.ceil(artifacts.length / 2) * 72 + 20),
        positions,
      };
    }
    const people = nodes.filter((item) => item.kind === "stakeholder");
    const pitch = 54;
    const height = Math.max(394, people.length * pitch + 16);
    const centerY = (height - 44) / 2;
    positions.request = { x: 10, y: centerY };
    positions.humanwire = { x: 155, y: centerY };
    positions["caspian-gateway"] = { x: 300, y: centerY };
    people.forEach((item, index) => {
      positions[item.node_id] = { x: 465, y: 8 + index * pitch };
    });
    const artifactPitch = 54;
    const artifactStart = Math.max(8, (height - 4 * 44 - 3 * 10) / 2);
    const artifactPositions = {
      conflict: { x: 660, y: artifactStart },
      interview: { x: 660, y: artifactStart + artifactPitch },
      evidence: { x: 660, y: artifactStart + artifactPitch * 2 },
      proposal: { x: 660, y: artifactStart + artifactPitch * 3 },
      approval: { x: 835, y: centerY - 86 },
      availability: { x: 835, y: centerY },
      meeting: { x: 835, y: centerY + 86 },
    };
    Object.assign(positions, artifactPositions);
    return { height, positions };
  }

  function svgElement(name, attributes) {
    const element = document.createElementNS(svgNamespace, name);
    Object.entries(attributes).forEach(([key, value]) => {
      element.setAttribute(key, value);
    });
    return element;
  }

  function renderGraph(snapshot) {
    const canvas = one("[data-flow-canvas]");
    if (canvas === null || !Array.isArray(snapshot.graph_nodes) || !Array.isArray(snapshot.graph_edges)) {
      return;
    }
    const isMobile = mobileLayout.matches;
    const width = isMobile ? 420 : 1000;
    const nodeHeight = isMobile ? 54 : 44;
    const layout = graphPositions(snapshot.graph_nodes, isMobile);
    const height = layout.height;
    const positions = layout.positions;
    canvas.setAttribute("viewBox", `0 0 ${width} ${height}`);
    canvas.replaceChildren();
    const title = svgElement("title", {});
    title.textContent = "Saved coordination flow";
    const description = svgElement("desc", {});
    description.textContent = "The highlighted path is the selected saved transition. Labels remain available in the From, To, and Generated summary.";
    canvas.append(title, description);
    const nodesById = new Map(snapshot.graph_nodes.map((node) => [node.node_id, node]));

    snapshot.graph_edges.forEach((edge, index) => {
      const source = positions[edge.source];
      const destination = positions[edge.destination];
      const sourceNode = nodesById.get(edge.source);
      const destinationNode = nodesById.get(edge.destination);
      if (
        source === undefined
        || destination === undefined
        || sourceNode === undefined
        || destinationNode === undefined
      ) {
        return;
      }
      const sourceWidth = graphNodeWidth(sourceNode, isMobile);
      const destinationWidth = graphNodeWidth(destinationNode, isMobile);
      const path = svgElement("path", {
        class: "studio-flow-edge",
        "data-flow-edge": "",
        "data-source": edge.source,
        "data-destination": edge.destination,
        "data-lane": String(index),
      });
      const startX = source.x + sourceWidth;
      const startY = source.y + nodeHeight / 2;
      const endX = destination.x;
      const endY = destination.y + nodeHeight / 2;
      if (isMobile) {
        const verticalStartX = source.x + sourceWidth / 2;
        const verticalEndX = destination.x + destinationWidth / 2;
        const middleY = (startY + endY) / 2 + ((index % 5) - 2) * 4;
        path.setAttribute(
          "d",
          `M ${verticalStartX} ${source.y + nodeHeight} C ${verticalStartX} ${middleY}, ${verticalEndX} ${middleY}, ${verticalEndX} ${destination.y}`,
        );
      } else {
        const middleX = (startX + endX) / 2 + ((index % 7) - 3) * 3;
        path.setAttribute("d", `M ${startX} ${startY} C ${middleX} ${startY}, ${middleX} ${endY}, ${endX} ${endY}`);
      }
      canvas.append(path);
    });

    snapshot.graph_nodes.forEach((node) => {
      const position = positions[node.node_id];
      if (position === undefined) {
        return;
      }
      const nodeWidth = graphNodeWidth(node, isMobile);
      const group = svgElement("g", {
        class: "studio-flow-node",
        transform: `translate(${position.x} ${position.y})`,
        "data-flow-node": node.node_id,
      });
      const card = svgElement("rect", { width: nodeWidth, height: nodeHeight });
      const symbol = svgElement("text", { x: 9, y: isMobile ? 23 : 17, class: "studio-flow-symbol" });
      symbol.textContent = node.initials || (node.kind === "artifact" ? "◆" : "HW");
      const label = svgElement("text", { x: 34, y: isMobile ? 22 : 16 });
      label.textContent = node.label;
      const role = svgElement("text", { x: 34, y: isMobile ? 42 : 33, class: "studio-flow-role" });
      role.textContent = node.role || titleCase(node.kind);
      group.append(card, symbol, label, role);
      canvas.append(group);
    });
  }

  function renderEvent(ordinal) {
    if (ordinal < 1 || ordinal > state.events.length || state.snapshot === null) {
      return;
    }
    const event = state.events[ordinal - 1];
    state.selectedOrdinal = ordinal;
    document.querySelectorAll("[data-flow-node]").forEach((node) => {
      node.classList.remove("is-active");
    });
    document.querySelectorAll("[data-flow-edge]").forEach((edge) => {
      edge.classList.remove("is-active", "is-travelling");
    });
    document.querySelectorAll("[data-persona-card]").forEach((card) => {
      card.classList.remove("is-active");
    });

    document.querySelectorAll("[data-flow-node]").forEach((node) => {
      const id = node.getAttribute("data-flow-node");
      if (id === event.active_transition.source || id === event.active_transition.destination) {
        node.classList.add("is-active");
      }
    });
    document.querySelectorAll("[data-flow-edge]").forEach((edge) => {
      if (
        edge.getAttribute("data-source") === event.active_transition.source
        && edge.getAttribute("data-destination") === event.active_transition.destination
      ) {
        edge.classList.add("is-active");
        if (!reducedMotion.matches && !document.hidden && !state.visualsPaused) {
          edge.classList.add("is-travelling");
        }
      }
    });
    if (event.affected_persona_id !== null) {
      document.querySelectorAll("[data-persona-card]").forEach((card) => {
        if (card.getAttribute("data-persona-card") === event.affected_persona_id) {
          card.classList.add("is-active");
        }
      });
    }

    setText("[data-flow-from]", event.active_transition.source_label);
    setText("[data-flow-to]", event.active_transition.destination_label);
    setText("[data-flow-generated]", event.active_transition.generated_label);
    setText("[data-flow-live]", event.live_copy);
    setText("[data-current-stage]", titleCase(event.stage));
    setText("[data-event-progress]", `Event ${ordinal} of ${state.events.length}`);
    renderLifecycle(state.snapshot.lifecycle, event.stage);
    renderVisibleRows(ordinal);
    updateControls();
  }

  function renderLifecycle(lifecycle, selectedStage) {
    const completed = new Set(lifecycle.completed || []);
    document.querySelectorAll("[data-lifecycle-stage]").forEach((item) => {
      const stage = item.getAttribute("data-lifecycle-stage");
      item.classList.toggle("is-complete", completed.has(stage));
      item.classList.toggle("is-current", stage === selectedStage);
      const status = item.querySelector("small");
      if (status !== null) {
        status.textContent = completed.has(stage)
          ? "Completed"
          : stage === selectedStage
            ? "In progress"
            : "Pending";
      }
    });
  }

  function timeLabel(value) {
    const text = String(value || "");
    return text.length >= 16 ? `${text.slice(11, 16)} UTC` : "Saved";
  }

  function renderVisibleRows(ordinal) {
    if (state.snapshot === null) {
      return;
    }
    const conversationList = one("[data-conversation-list]");
    const dataList = one("[data-data-list]");
    if (conversationList !== null) {
      conversationList.replaceChildren();
      state.snapshot.conversations
        .filter((item) => item.event_ordinal <= ordinal)
        .forEach((item) => {
          const row = document.createElement("article");
          row.setAttribute("class", "studio-conversation-row");
          row.setAttribute("data-conversation-row", String(item.ordinal));
          if (item.status === "rejected" || item.status === "no_response") {
            row.classList.add("is-attention");
          }
          const header = document.createElement("header");
          const speaker = document.createElement("strong");
          speaker.textContent = item.speaker;
          const time = document.createElement("time");
          time.textContent = timeLabel(item.created_at);
          header.append(speaker, time);
          const role = document.createElement("small");
          role.textContent = `${item.role} · ${item.channel}`;
          const message = document.createElement("p");
          message.textContent = item.text;
          row.append(header, role, message);
          conversationList.append(row);
        });
    }
    if (dataList !== null) {
      dataList.replaceChildren();
      state.snapshot.data_points
        .filter((item) => item.event_ordinal <= ordinal)
        .forEach((item) => {
          const row = document.createElement("article");
          row.setAttribute("class", "studio-data-row");
          row.setAttribute("data-data-row", String(item.event_ordinal));
          const ordinalLabel = document.createElement("span");
          ordinalLabel.textContent = String(item.event_ordinal);
          const label = document.createElement("strong");
          label.textContent = item.label;
          const summary = document.createElement("p");
          summary.textContent = item.summary;
          const effect = document.createElement("small");
          effect.textContent = item.effect === "persisted" ? "Saved" : "No state change";
          row.append(ordinalLabel, label, summary, effect);
          dataList.append(row);
        });
    }
  }

  function updateDownloads(ready) {
    const json = one("[data-download-json]");
    const csv = one("[data-download-csv]");
    const reset = one("[data-new-coordination]");
    if (json !== null) {
      json.disabled = !ready;
    }
    if (csv !== null) {
      csv.disabled = !ready;
    }
    if (reset !== null) {
      reset.hidden = !ready;
    }
  }

  function updateControls() {
    const previous = one("[data-replay-previous]");
    const next = one("[data-replay-next]");
    const play = one("[data-replay-play]");
    const follow = one("[data-follow-live]");
    const pause = one("[data-pause-visuals]");
    const terminal = state.snapshot !== null
      && (state.snapshot.run_state === "complete" || state.snapshot.run_state === "failed");
    if (previous !== null) {
      previous.disabled = state.selectedOrdinal <= 1;
    }
    if (next !== null) {
      next.disabled = state.selectedOrdinal >= state.events.length;
    }
    if (play !== null) {
      play.disabled = state.events.length < 2 || (!terminal && !state.visualsPaused);
      play.setAttribute("aria-pressed", state.playing ? "true" : "false");
      play.textContent = state.playing ? "Pause" : "Play";
    }
    if (follow !== null) {
      follow.setAttribute("aria-pressed", state.followLive ? "true" : "false");
    }
    if (pause !== null) {
      pause.setAttribute("aria-pressed", state.visualsPaused ? "true" : "false");
      pause.textContent = state.visualsPaused ? "Resume visuals" : "Pause visuals";
    }
  }

  function selectManual(ordinal) {
    cancelRenderQueue();
    state.followLive = false;
    state.visualsPaused = true;
    stopPlayback();
    renderEvent(ordinal);
  }

  function stopPlayback() {
    if (state.playTimer !== null) {
      clearInterval(state.playTimer);
      state.playTimer = null;
    }
    state.playing = false;
    updateControls();
  }

  function togglePlayback() {
    if (state.playing) {
      stopPlayback();
      return;
    }
    const terminal = state.snapshot !== null
      && (state.snapshot.run_state === "complete" || state.snapshot.run_state === "failed");
    if (!terminal && !state.visualsPaused) {
      return;
    }
    cancelRenderQueue();
    if (state.selectedOrdinal >= state.events.length) {
      state.selectedOrdinal = 0;
    }
    state.followLive = false;
    state.playing = true;
    state.playTimer = setInterval(() => {
      if (state.selectedOrdinal >= state.events.length) {
        stopPlayback();
        return;
      }
      renderEvent(state.selectedOrdinal + 1);
    }, 900);
    updateControls();
  }

  function downloadAttachment(suffix, filename) {
    if (state.runAlias === null) {
      return;
    }
    const anchor = document.createElement("a");
    anchor.setAttribute("href", `/api/runs/${state.runAlias}/${suffix}`);
    anchor.setAttribute("download", filename);
    anchor.hidden = true;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
  }

  function newCoordination() {
    stopPolling();
    stopPlayback();
    cancelRenderQueue();
    state.runAlias = null;
    state.snapshot = null;
    state.events.length = 0;
    state.selectedOrdinal = 0;
    state.etag = null;
    state.followLive = true;
    state.visualsPaused = false;
    clearPresentation();
    setStudioState("composer");
    history.replaceState({}, "", "/");
    setText("[data-form-status]", "");
    const start = one("[data-start-coordination]");
    if (start !== null) {
      start.disabled = false;
    }
    updateControls();
  }

  function clearPresentation() {
    const canvas = one("[data-flow-canvas]");
    const conversation = one("[data-conversation-list]");
    const data = one("[data-data-list]");
    if (canvas !== null) {
      canvas.replaceChildren();
      canvas.setAttribute("viewBox", "0 0 1000 360");
    }
    if (conversation !== null) {
      conversation.replaceChildren();
    }
    if (data !== null) {
      data.replaceChildren();
    }
    document.querySelectorAll("[data-lifecycle-stage]").forEach((item) => {
      item.classList.remove("is-complete", "is-current");
      const status = item.querySelector("small");
      if (status !== null) {
        status.textContent = "Pending";
      }
    });
    document.querySelectorAll("[data-persona-card]").forEach((card) => {
      card.classList.remove("is-active");
    });
    [
      "[data-workspace-objective]",
      "[data-workspace-requester]",
      "[data-run-state]",
      "[data-connection-label]",
      "[data-current-stage]",
      "[data-flow-from]",
      "[data-flow-to]",
      "[data-flow-generated]",
      "[data-flow-live]",
      "[data-outcome-headline]",
      "[data-outcome-summary]",
    ].forEach((selector) => setText(selector, ""));
    setText("[data-event-progress]", "Event 0 of 0");
    updateDownloads(false);
    showMobileTab("conversation");
  }

  function updateCustomDate() {
    const customDate = one("[data-custom-date]");
    if (customDate !== null) {
      customDate.disabled = selectedValue("target_timing", "tomorrow") !== "custom";
    }
  }

  function showMobileTab(selected) {
    workspace.setAttribute("data-mobile-panel", selected);
    document.querySelectorAll("[data-mobile-tab]").forEach((tab) => {
      tab.setAttribute(
        "aria-selected",
        tab.getAttribute("data-mobile-tab") === selected ? "true" : "false",
      );
    });
  }

  one("[data-start-coordination]").addEventListener("click", (event) => {
    event.preventDefault();
    startRun();
  });
  document.querySelectorAll("[data-template-id]").forEach((button) => {
    button.addEventListener("click", () => {
      if (state.catalog === null) {
        return;
      }
      applyTemplate(
        state.catalog.templates.find(
          (item) => item.template_id === button.getAttribute("data-template-id"),
        ),
      );
    });
  });
  document.querySelectorAll('[name="target_timing"]').forEach((input) => {
    input.addEventListener("change", updateCustomDate);
  });
  document.querySelectorAll('[name="participant_ids"]').forEach((input) => {
    input.addEventListener("change", updateStakeholderCount);
  });
  one("[data-pause-visuals]").addEventListener("click", () => {
    state.visualsPaused = !state.visualsPaused;
    if (state.visualsPaused) {
      state.followLive = false;
      cancelRenderQueue();
    } else if (state.snapshot !== null && state.events.length > 0) {
      state.followLive = true;
      renderEvent(state.events.length);
    }
    updateControls();
  });
  one("[data-follow-live]").addEventListener("click", () => {
    cancelRenderQueue();
    state.followLive = true;
    state.visualsPaused = false;
    stopPlayback();
    if (state.events.length > 0) {
      renderEvent(state.events.length);
    }
  });
  one("[data-replay-previous]").addEventListener("click", () => {
    selectManual(Math.max(1, state.selectedOrdinal - 1));
  });
  one("[data-replay-next]").addEventListener("click", () => {
    selectManual(Math.min(state.events.length, state.selectedOrdinal + 1));
  });
  one("[data-replay-play]").addEventListener("click", togglePlayback);
  one("[data-download-json]").addEventListener("click", () => {
    downloadAttachment("evidence.json", `${state.runAlias}-evidence.json`);
  });
  one("[data-download-csv]").addEventListener("click", () => {
    downloadAttachment("events.csv", `${state.runAlias}-events.csv`);
  });
  one("[data-new-coordination]").addEventListener("click", newCoordination);
  document.querySelectorAll("[data-mobile-tab]").forEach((tab) => {
    tab.addEventListener("click", () => showMobileTab(tab.getAttribute("data-mobile-tab")));
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      cancelRenderQueue();
      stopPlayback();
      document.querySelectorAll("[data-flow-edge]").forEach((edge) => {
        edge.classList.remove("is-travelling");
      });
    } else if (state.followLive && state.events.length > 0) {
      renderEvent(state.events.length);
    }
  });
  window.addEventListener("resize", () => {
    if (state.snapshot !== null) {
      renderGraph(state.snapshot);
      if (state.selectedOrdinal > 0) {
        renderEvent(state.selectedOrdinal);
      }
    }
  });

  const route = location.pathname.match(/^\/runs\/([A-Za-z0-9][A-Za-z0-9._-]{0,63})$/);
  loadCatalog();
  showMobileTab("conversation");
  if (route !== null) {
    state.runAlias = route[1];
    setStudioState("workspace");
    startPolling();
  } else {
    setStudioState("composer");
  }
  updateControls();
});
