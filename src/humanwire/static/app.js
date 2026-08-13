(function () {
  "use strict";

  document.documentElement.classList.add("js");

  const refreshState = {
    timer: null,
    countdownTimer: null,
    halted: false,
    lastAnnouncedMinute: null,
  };
  const reachReplayState = {
    timer: null,
    index: 0,
    playing: false,
  };

  function decisionRoom() {
    return document.querySelector('[data-testid="decision-room"]');
  }

  function refreshFooter() {
    return document.querySelector('[data-testid="live-refresh"]');
  }

  function stopPolling(halt) {
    if (refreshState.timer !== null) {
      window.clearTimeout(refreshState.timer);
      refreshState.timer = null;
    }
    if (halt) {
      refreshState.halted = true;
      const status = document.querySelector("[data-refresh-status]");
      if (status) {
        status.textContent = "Live refresh paused";
      }
    }
  }

  function stopCountdown() {
    if (refreshState.countdownTimer !== null) {
      window.clearInterval(refreshState.countdownTimer);
      refreshState.countdownTimer = null;
    }
  }

  function formatClock(seconds) {
    const safe = Math.max(Math.floor(seconds), 0);
    const hours = Math.floor(safe / 3600);
    const minutes = Math.floor((safe % 3600) / 60);
    const remainingSeconds = safe % 60;
    return [hours, minutes, remainingSeconds]
      .map((value) => String(value).padStart(2, "0"))
      .join(":");
  }

  function updateCountdown() {
    const room = decisionRoom();
    const countdown = document.querySelector("[data-countdown]");
    if (!room || !countdown || !room.dataset.deadline) {
      return;
    }
    const dueAt = Date.parse(room.dataset.deadline);
    if (Number.isNaN(dueAt)) {
      return;
    }
    const seconds = Math.max((dueAt - Date.now()) / 1000, 0);
    countdown.textContent = formatClock(seconds);
    const minute = Math.floor(seconds / 60);
    if (minute !== refreshState.lastAnnouncedMinute) {
      refreshState.lastAnnouncedMinute = minute;
      const live = document.querySelector("[data-countdown-live]");
      if (live) {
        const hours = Math.floor(minute / 60);
        const minutes = minute % 60;
        live.textContent = `Due in ${hours} hours and ${minutes} minutes`;
      }
    }
  }

  function startCountdown() {
    stopCountdown();
    if (document.visibilityState !== "visible") {
      return;
    }
    updateCountdown();
    refreshState.countdownTimer = window.setInterval(updateCountdown, 1000);
  }

  function formatUpdatedAt(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "—";
    }
    return new Intl.DateTimeFormat(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(date);
  }

  async function refreshMandate(token) {
    const footer = refreshFooter();
    if (
      !token ||
      refreshState.halted ||
      document.visibilityState !== "visible" ||
      !footer
    ) {
      return null;
    }
    try {
      const response = await window.fetch(
        `/api/v1/mandates/${encodeURIComponent(token)}`,
        {
          method: "GET",
          headers: { Accept: "application/json" },
          credentials: "same-origin",
          cache: "no-store",
        },
      );
      if (!response.ok) {
        stopPolling(true);
        return null;
      }
      const persisted = await response.json();
      if (persisted.deadline) {
        const room = decisionRoom();
        if (room) {
          room.dataset.deadline = persisted.deadline;
          updateCountdown();
        }
      }
      const baseline = footer.dataset.refreshBaseline;
      if (persisted.updated_at && baseline && persisted.updated_at !== baseline) {
        window.location.reload();
        return persisted;
      }
      const lastUpdated = document.querySelector("[data-last-updated]");
      if (lastUpdated && persisted.updated_at) {
        lastUpdated.dateTime = persisted.updated_at;
        lastUpdated.textContent = formatUpdatedAt(persisted.updated_at);
      }
      return persisted;
    } catch (_error) {
      stopPolling(true);
      return null;
    }
  }

  function scheduleRefresh() {
    stopPolling(false);
    const footer = refreshFooter();
    if (
      !footer ||
      !footer.dataset.refreshToken ||
      refreshState.halted ||
      document.visibilityState !== "visible"
    ) {
      return;
    }
    refreshState.timer = window.setTimeout(async function pollPersistedMandate() {
      await refreshMandate(footer.dataset.refreshToken);
      if (!refreshState.halted) {
        scheduleRefresh();
      }
    }, 5000);
  }

  function initializeNavigation() {
    const toggle = document.querySelector("[data-nav-toggle]");
    const navigation = document.querySelector("[data-navigation]");
    if (!toggle || !navigation) {
      return;
    }
    toggle.addEventListener("click", function toggleNavigation() {
      const open = navigation.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
    });
  }

  function initializeFilters() {
    const rows = Array.from(document.querySelectorAll(".stakeholder-row"));
    const filters = Array.from(document.querySelectorAll("[data-filter]"));
    const count = document.querySelector("[data-filter-count]");
    filters.forEach((filter) => {
      filter.addEventListener("click", function filterStakeholders() {
        const selectedFilter = filter.dataset.filter;
        filters.forEach((button) => {
          button.setAttribute("aria-pressed", String(button === filter));
        });
        let visible = 0;
        rows.forEach((row) => {
          const groups = (row.dataset.filterGroups || "").split(" ");
          const show = selectedFilter === "all" || groups.includes(selectedFilter);
          row.hidden = !show;
          if (show) {
            visible += 1;
          }
        });
        if (count) {
          count.textContent = String(visible);
        }
      });
    });
  }

  function renderLadder(row) {
    const list = document.querySelector("#engagement-ladder-list");
    const title = document.querySelector("#selected-ladder-title");
    if (!list || !title) {
      return;
    }
    let steps;
    try {
      steps = JSON.parse(row.dataset.ladder || "[]");
    } catch (_error) {
      return;
    }
    list.replaceChildren();
    steps.forEach((step, index) => {
      const item = document.createElement("li");
      item.className = `ladder-step is-${step.status}`;
      const marker = document.createElement("span");
      marker.className = "ladder-marker";
      marker.textContent = String(index + 1);
      const copy = document.createElement("span");
      const label = document.createElement("strong");
      label.textContent = step.label;
      const detail = document.createElement("small");
      detail.textContent = step.detail;
      copy.append(label, detail);
      item.append(marker, copy);
      list.append(item);
    });
    const suffix = document.createElement("small");
    suffix.textContent = ` — ${row.dataset.engagement}`;
    title.replaceChildren(`Selected: ${row.dataset.name}`, suffix);
  }

  function initializeStakeholderSelection() {
    const rows = Array.from(document.querySelectorAll(".stakeholder-row"));
    const container = document.querySelector('[data-testid="stakeholders"]');
    rows.forEach((row) => {
      const button = row.querySelector(".stakeholder-select");
      if (!button) {
        return;
      }
      button.addEventListener("click", function selectStakeholder() {
        rows.forEach((candidate) => {
          const candidateButton = candidate.querySelector(".stakeholder-select");
          candidate.classList.toggle("is-selected", candidate === row);
          if (candidateButton) {
            candidateButton.setAttribute("aria-pressed", String(candidate === row));
          }
          if (candidate === row) {
            candidate.dataset.selected = "true";
          } else {
            delete candidate.dataset.selected;
          }
        });
        if (container) {
          container.dataset.selectedPerson = row.dataset.person || "";
        }
        renderLadder(row);
      });
    });
  }

  function reachPage() {
    return document.querySelector('[data-testid="reach-page"]');
  }

  function orderedReachRows() {
    return Array.from(document.querySelectorAll("[data-lane-person]")).sort(
      (left, right) => Number(left.dataset.sequence) - Number(right.dataset.sequence),
    );
  }

  function updateReachUrl(filter, personId) {
    const url = new URL(window.location.href);
    url.searchParams.delete("status");
    url.searchParams.delete("person_id");
    if (filter && filter !== "all") {
      url.searchParams.set("status", filter);
    }
    if (personId) {
      url.searchParams.set("person_id", personId);
    }
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }

  function selectReachRow(row, filter, writeUrl) {
    const page = reachPage();
    if (!page || !row || row.hidden) {
      return;
    }
    const rows = orderedReachRows();
    const histories = Array.from(document.querySelectorAll("[data-history-person]"));
    rows.forEach((candidate) => {
      const selected = candidate === row;
      candidate.classList.toggle("is-selected", selected);
      candidate.dataset.selected = String(selected);
      const button = candidate.querySelector("[data-reach-select]");
      if (button) {
        button.setAttribute("aria-pressed", String(selected));
      }
    });
    histories.forEach((history) => {
      const selected = history.dataset.historyPerson === row.dataset.lanePerson;
      history.hidden = !selected;
      history.classList.toggle("is-current", selected);
    });
    page.dataset.selectedPerson = row.dataset.lanePerson || "";
    if (writeUrl) {
      updateReachUrl(filter, row.dataset.lanePerson || "");
    }
  }

  function applyReachFilter(selectedFilter, writeUrl) {
    const page = reachPage();
    const filters = Array.from(document.querySelectorAll("[data-reach-filter]"));
    const rows = orderedReachRows();
    if (!page || !filters.length || !rows.length) {
      return;
    }
    const allowed = filters.map((button) => button.dataset.reachFilter);
    const filter = allowed.includes(selectedFilter) ? selectedFilter : "all";
    filters.forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.reachFilter === filter));
    });
    rows.forEach((row) => {
      const groups = (row.dataset.filterGroups || "").split(" ").filter(Boolean);
      row.hidden = filter !== "all" && !groups.includes(filter);
    });
    page.dataset.reachFilterState = filter;
    const selected = rows.find((row) => row.dataset.selected === "true" && !row.hidden);
    const next = selected || rows.find((row) => !row.hidden);
    if (next) {
      selectReachRow(next, filter, writeUrl);
    } else if (writeUrl) {
      updateReachUrl(filter, "");
    }
  }

  function initializeReachFilters() {
    const page = reachPage();
    const filters = Array.from(document.querySelectorAll("[data-reach-filter]"));
    if (!page || !filters.length) {
      return;
    }
    const params = new URLSearchParams(window.location.search);
    const statusValues = params.getAll("status");
    const allowed = filters.map((button) => button.dataset.reachFilter);
    const initial =
      statusValues.length === 0
        ? "all"
        : statusValues.length === 1 && allowed.includes(statusValues[0])
          ? statusValues[0]
          : "all";
    applyReachFilter(initial, false);
    filters.forEach((button) => {
      button.addEventListener("click", function filterReachRows() {
        applyReachFilter(button.dataset.reachFilter || "all", true);
      });
    });
  }

  function initializeReachSelection() {
    const page = reachPage();
    const rows = orderedReachRows();
    if (!page || !rows.length) {
      return;
    }
    const params = new URLSearchParams(window.location.search);
    const personValues = params.getAll("person_id");
    const requested = personValues.length === 1 ? personValues[0] : "";
    const exactMatches = rows.filter(
      (row) => requested && row.dataset.lanePerson === requested && !row.hidden,
    );
    const selected =
      exactMatches.length === 1
        ? exactMatches[0]
        : rows.find((row) => row.dataset.selected === "true" && !row.hidden) ||
          rows.find((row) => !row.hidden);
    if (selected) {
      selectReachRow(selected, page.dataset.reachFilterState || "all", false);
    }
    rows.forEach((row) => {
      const button = row.querySelector("[data-reach-select]");
      if (!button) {
        return;
      }
      button.addEventListener("click", function selectReachPerson() {
        selectReachRow(row, page.dataset.reachFilterState || "all", true);
      });
    });
  }

  function stopReachReplay() {
    if (reachReplayState.timer !== null) {
      window.clearInterval(reachReplayState.timer);
      reachReplayState.timer = null;
    }
    reachReplayState.playing = false;
    const play = document.querySelector("[data-replay-play]");
    const action = document.querySelector("[data-replay-action]");
    if (play) {
      play.setAttribute("aria-pressed", "false");
      play.setAttribute("aria-label", "Play saved events");
    }
    if (action) {
      action.textContent = "Play";
    }
  }

  function showReachReplayEvent(nextIndex, announce) {
    const events = Array.from(document.querySelectorAll("[data-replay-event]"));
    if (!events.length) {
      return;
    }
    const index = Math.min(Math.max(nextIndex, 0), events.length - 1);
    const changed = index !== reachReplayState.index;
    reachReplayState.index = index;
    const current = events[index];
    events.forEach((event, eventIndex) => {
      const active = eventIndex === index;
      event.classList.toggle("is-current", active);
      event.setAttribute("aria-hidden", String(!active));
    });
    document
      .querySelectorAll(".reach-step.is-replay-current, .reach-origin.is-replay-current")
      .forEach((node) => node.classList.remove("is-replay-current"));
    if (current.dataset.highlight === "origin") {
      const origin = document.querySelector('[data-testid="reach-origin"]');
      if (origin) {
        origin.classList.add("is-replay-current");
      }
    } else if (current.dataset.highlight && current.dataset.highlight !== "none") {
      const matching = orderedReachRows().filter(
        (row) => row.dataset.lanePerson === current.dataset.highlight,
      );
      if (matching.length === 1) {
        matching[0].classList.add("is-replay-current");
      }
    }
    const sourceDescription = current.querySelector(".replay-copy strong");
    const sourceTime = current.querySelector("time");
    const description = document.querySelector("[data-replay-description]");
    const time = document.querySelector("[data-replay-time]");
    const progress = document.querySelector("[data-replay-progress]");
    const live = document.querySelector("[data-replay-live]");
    const flow = document.querySelector("[data-replay-flow]");
    const source = document.querySelector("[data-replay-source]");
    const destination = document.querySelector("[data-replay-destination]");
    const dataPoint = document.querySelector("[data-replay-data-point]");
    const sourceLabel = current.dataset.replaySource || "";
    const destinationLabel = current.dataset.replayDestination || "";
    const dataPointLabel = current.dataset.replayDataPoint || "";
    if (description && sourceDescription) {
      description.textContent = sourceDescription.textContent;
    }
    if (time && sourceTime) {
      time.textContent = sourceTime.textContent;
      time.dateTime = sourceTime.dateTime;
    }
    if (source) {
      source.textContent = sourceLabel;
    }
    if (destination) {
      destination.textContent = destinationLabel;
    }
    if (dataPoint) {
      dataPoint.textContent = dataPointLabel;
    }
    if (
      changed &&
      flow &&
      !window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      flow.classList.remove("is-changing");
      void flow.offsetWidth;
      flow.classList.add("is-changing");
      window.setTimeout(function settleReplayFlow() {
        flow.classList.remove("is-changing");
      }, 180);
    }
    const progressText = `Event ${index + 1} of ${events.length}`;
    if (progress) {
      progress.textContent = progressText;
    }
    if ((announce || changed) && live && sourceDescription) {
      live.textContent = `${progressText}: From ${sourceLabel}; To ${destinationLabel}; Generated ${dataPointLabel}. ${sourceDescription.textContent}`;
    }
    if (index === events.length - 1) {
      stopReachReplay();
    }
  }

  function startReachReplay() {
    const events = Array.from(document.querySelectorAll("[data-replay-event]"));
    if (
      !events.length ||
      document.visibilityState !== "visible" ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      stopReachReplay();
      return;
    }
    if (reachReplayState.index >= events.length - 1) {
      showReachReplayEvent(0, true);
    }
    reachReplayState.playing = true;
    const play = document.querySelector("[data-replay-play]");
    const action = document.querySelector("[data-replay-action]");
    if (play) {
      play.setAttribute("aria-pressed", "true");
      play.setAttribute("aria-label", "Pause saved events");
    }
    if (action) {
      action.textContent = "Pause";
    }
    reachReplayState.timer = window.setInterval(function advanceSavedEvent() {
      showReachReplayEvent(reachReplayState.index + 1, false);
    }, 2000);
  }

  function initializeReachReplay() {
    const events = Array.from(document.querySelectorAll("[data-replay-event]"));
    if (!events.length) {
      return;
    }
    const previous = document.querySelector("[data-replay-previous]");
    const next = document.querySelector("[data-replay-next]");
    const play = document.querySelector("[data-replay-play]");
    const jump = document.querySelector("[data-replay-jump]");
    if (previous) {
      previous.addEventListener("click", function previousSavedEvent() {
        stopReachReplay();
        showReachReplayEvent(reachReplayState.index - 1, true);
      });
    }
    if (next) {
      next.addEventListener("click", function nextSavedEvent() {
        stopReachReplay();
        showReachReplayEvent(reachReplayState.index + 1, true);
      });
    }
    if (play) {
      play.addEventListener("click", function toggleSavedEventReplay() {
        if (reachReplayState.playing) {
          stopReachReplay();
        } else {
          startReachReplay();
        }
      });
    }
    if (jump) {
      jump.addEventListener("click", function revealSavedEvents() {
        const panel = document.querySelector('[data-testid="reach-replay"]');
        if (panel) {
          panel.scrollIntoView({
            behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
              ? "auto"
              : "smooth",
            block: "start",
          });
        }
      });
    }
    document.addEventListener("visibilitychange", function pauseHiddenReplay() {
      if (document.visibilityState !== "visible") {
        stopReachReplay();
      }
    });
    showReachReplayEvent(0, false);
  }

  function initializeVisibility() {
    document.addEventListener("visibilitychange", function handleVisibility() {
      if (document.visibilityState === "visible") {
        startCountdown();
        scheduleRefresh();
      } else {
        stopPolling(false);
        stopCountdown();
      }
    });
  }

  window.HumanWire = Object.freeze({ refreshMandate });

  document.addEventListener("DOMContentLoaded", function initializeHumanWire() {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      document.documentElement.classList.add("reduced-motion");
    }
    initializeNavigation();
    initializeFilters();
    initializeStakeholderSelection();
    initializeReachFilters();
    initializeReachSelection();
    initializeReachReplay();
    initializeVisibility();
    startCountdown();
    scheduleRefresh();
  });
})();
