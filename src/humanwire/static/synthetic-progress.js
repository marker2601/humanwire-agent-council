(() => {
  "use strict";

  const POLL_INTERVAL_MS = 1000;
  const PLAY_INTERVAL_MS = 1600;

  function label(value) {
    if (!value) return "Waiting";
    return String(value)
      .replaceAll("_", " ")
      .replace(/^./, (character) => character.toUpperCase());
  }

  function initializeSyntheticViewer() {
    const root = document.querySelector("[data-synthetic-viewer]");
    if (!root) return;

    const runStatus = document.querySelector("[data-run-status]");
    const runMode = document.querySelector("[data-run-mode]");
    const runState = document.querySelector("[data-run-state]");
    const runtimeStatus = document.querySelector("[data-runtime-status]");
    const activePersona = document.querySelector("[data-active-persona]");
    const savedEventCount = document.querySelector("[data-saved-event-count]");
    const personaList = document.querySelector("[data-persona-list]");
    const replayList = document.querySelector("[data-replay-list]");
    const followLive = document.querySelector("[data-follow-live]");
    const previous = document.querySelector("[data-replay-previous]");
    const play = document.querySelector("[data-replay-play]");
    const next = document.querySelector("[data-replay-next]");
    const replayProgress = document.querySelector("[data-replay-progress]");
    const replaySource = document.querySelector("[data-replay-source]");
    const replayDestination = document.querySelector("[data-replay-destination]");
    const replayDataPoint = document.querySelector("[data-replay-data-point]");
    const replayDescription = document.querySelector("[data-replay-description]");
    const replayTime = document.querySelector("[data-replay-time]");
    const replayLive = document.querySelector("[data-replay-live]");
    const jsonDownload = document.querySelector("[data-evidence-json]");
    const csvDownload = document.querySelector("[data-evidence-csv]");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let events = [];
    let selectedIndex = -1;
    let following = true;
    let pollTimer = null;
    let playTimer = null;
    let polling = false;

    function setDownloadsEnabled(enabled) {
      [jsonDownload, csvDownload].forEach((download) => {
        if (!download) return;
        if (enabled) {
          download.removeAttribute("aria-disabled");
          download.removeAttribute("tabindex");
        } else {
          download.setAttribute("aria-disabled", "true");
          download.setAttribute("tabindex", "-1");
        }
      });
    }

    function stopPlayback() {
      if (playTimer !== null) {
        window.clearInterval(playTimer);
        playTimer = null;
      }
      play.setAttribute("aria-pressed", "false");
      play.setAttribute("aria-label", "Play saved events");
      play.textContent = "Play";
    }

    function setFollowing(nextFollowing) {
      following = nextFollowing;
      followLive.setAttribute("aria-pressed", String(following));
    }

    function selectEvent(index, { manual = false, announce = true } = {}) {
      if (events.length === 0) {
        selectedIndex = -1;
        replaySource.textContent = "No persisted event yet";
        replayDestination.textContent = "HumanWire";
        replayDataPoint.textContent = "Waiting";
        replayDescription.textContent = "Waiting for the first saved event.";
        replayTime.textContent = "";
        replayProgress.textContent = "Event 0 of 0";
        replayLive.textContent = "";
        root.querySelectorAll("[data-highlight-target]").forEach((node) => {
          node.classList.remove("is-replay-current");
        });
        return;
      }
      selectedIndex = Math.max(0, Math.min(index, events.length - 1));
      if (manual) setFollowing(false);
      const event = events[selectedIndex];
      replaySource.textContent = event.source;
      replayDestination.textContent = event.destination;
      replayDataPoint.textContent = event.data_point;
      replayDescription.textContent = event.description;
      const generated = new Date(event.created_at);
      replayTime.textContent = Number.isNaN(generated.valueOf())
        ? "Saved time unavailable"
        : generated.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
      replayTime.setAttribute("datetime", event.created_at);
      replayProgress.textContent = `Event ${selectedIndex + 1} of ${events.length}`;
      root.querySelectorAll("[data-highlight-target]").forEach((node) => {
        node.classList.toggle(
          "is-replay-current",
          node.getAttribute("data-highlight-target") === event.highlight_target,
        );
      });
      if (announce) {
        replayLive.textContent = `Event ${selectedIndex + 1} of ${events.length}: From ${event.source}; To ${event.destination}; Generated ${event.data_point}. ${event.description}`;
      }
    }

    function renderPersonas(personas) {
      const cards = personas.map((persona) => {
        const item = document.createElement("li");
        item.classList.add("synthetic-persona-card");
        item.setAttribute("data-highlight-target", `persona-${persona.ordinal}`);
        const heading = document.createElement("h3");
        heading.textContent = persona.display_name;
        const role = document.createElement("p");
        role.textContent = persona.role;
        const contract = document.createElement("p");
        contract.classList.add("synthetic-persona-contract");
        contract.textContent = persona.contract ? label(persona.contract) : "Awaiting assignment";
        const status = document.createElement("p");
        status.textContent = `${label(persona.status)} · ${persona.progress_current} of ${persona.progress_total}`;
        item.append(heading, role, contract, status);
        return item;
      });
      personaList.replaceChildren(...cards);
    }

    function renderEventList() {
      const rows = events.map((event, index) => {
        const item = document.createElement("li");
        item.textContent = `${index + 1}. ${event.description}`;
        return item;
      });
      replayList.replaceChildren(...rows);
    }

    function renderSnapshot(snapshot) {
      const persisted = Array.isArray(snapshot.events)
        ? snapshot.events.filter((event) => event.effect === "persisted")
        : [];
      events = persisted;
      runMode.textContent = label(snapshot.mode);
      runState.textContent = label(snapshot.run_state);
      runtimeStatus.textContent = label(snapshot.runtime_status);
      activePersona.textContent = snapshot.active_persona_label || "Waiting";
      savedEventCount.textContent = String(snapshot.saved_event_count);
      const countLabel = snapshot.saved_event_count === 1 ? "saved event" : "saved events";
      runStatus.textContent = `${label(snapshot.run_state)} · ${snapshot.runtime_status} · ${snapshot.saved_event_count} ${countLabel}`;
      renderPersonas(Array.isArray(snapshot.personas) ? snapshot.personas : []);
      renderEventList();
      setDownloadsEnabled(snapshot.run_state === "complete" && Boolean(snapshot.final_trace_sha256));
      if (following) {
        selectEvent(events.length - 1, { announce: events.length > 0 });
      } else if (events.length === 0) {
        selectEvent(-1);
      } else {
        selectEvent(Math.min(selectedIndex, events.length - 1), { announce: false });
      }
    }

    async function pollProgress() {
      if (document.visibilityState !== "visible" || polling) return;
      polling = true;
      try {
        const response = await fetch("/progress.json", {
          cache: "no-store",
          headers: { Accept: "application/json" },
        });
        if (response.ok) renderSnapshot(await response.json());
      } catch (_error) {
        runStatus.textContent = "Progress temporarily unavailable";
      } finally {
        polling = false;
      }
    }

    function startPolling() {
      if (document.visibilityState !== "visible" || pollTimer !== null) return;
      void pollProgress();
      pollTimer = window.setInterval(pollProgress, POLL_INTERVAL_MS);
    }

    function stopPolling() {
      if (pollTimer !== null) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    function advanceSavedEvent() {
      if (selectedIndex >= events.length - 1) {
        stopPlayback();
        return;
      }
      selectEvent(selectedIndex + 1, { manual: true });
    }

    previous.addEventListener("click", () => {
      stopPlayback();
      selectEvent(selectedIndex - 1, { manual: true });
    });
    next.addEventListener("click", () => {
      stopPlayback();
      selectEvent(selectedIndex + 1, { manual: true });
    });
    followLive.addEventListener("click", () => {
      stopPlayback();
      setFollowing(true);
      selectEvent(events.length - 1);
    });
    play.addEventListener("click", () => {
      if (playTimer !== null) {
        stopPlayback();
        return;
      }
      if (reducedMotion || document.visibilityState !== "visible" || events.length < 2) {
        stopPlayback();
        return;
      }
      if (selectedIndex >= events.length - 1) selectEvent(0, { manual: true });
      setFollowing(false);
      play.setAttribute("aria-pressed", "true");
      play.setAttribute("aria-label", "Pause saved events");
      play.textContent = "Pause";
      playTimer = window.setInterval(advanceSavedEvent, PLAY_INTERVAL_MS);
    });
    [jsonDownload, csvDownload].forEach((download) => {
      download.addEventListener("click", (event) => {
        if (download.getAttribute("aria-disabled") === "true") event.preventDefault();
      });
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") startPolling();
      else {
        stopPolling();
        stopPlayback();
      }
    });

    setDownloadsEnabled(false);
    startPolling();
  }

  document.addEventListener("DOMContentLoaded", initializeSyntheticViewer);
})();
