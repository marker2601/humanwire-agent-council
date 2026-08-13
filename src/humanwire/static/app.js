(function () {
  "use strict";

  document.documentElement.classList.add("js");

  const refreshState = {
    timer: null,
    countdownTimer: null,
    halted: false,
    lastAnnouncedMinute: null,
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
    initializeVisibility();
    startCountdown();
    scheduleRefresh();
  });
})();
