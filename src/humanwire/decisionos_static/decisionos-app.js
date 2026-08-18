(function decisionOSWorkspace(global) {
  "use strict";

  const state = {
    organizations: [],
    workspaces: [],
    activeOrganizationId: "",
    activeWorkspaceId: "",
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
    show("[data-empty-workspaces]", true);
    element("#workspace-name")?.focus();
    setStatus("Name the decision workspace and choose its playbook.");
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
    for (const target of elements("[data-playbook]")) {
      target.addEventListener("click", choosePlaybook);
    }
    element("[data-create-organization]")?.addEventListener("submit", createOrganization);
    element("[data-create-workspace]")?.addEventListener("submit", createWorkspace);
    element("[data-invite-member]")?.addEventListener("submit", createInvitation);
    element("[data-accept-invitation]")?.addEventListener("submit", acceptInvitation);
    element("[data-sign-out]")?.addEventListener("click", signOut);
    element("[data-organization-list]")?.addEventListener("change", (event) => {
      loadWorkspaces(event.currentTarget.value).catch(() => {
        setStatus("The organization could not be loaded.", "error");
      });
    });
    element("[data-workspace-list]")?.addEventListener("change", (event) => {
      state.activeWorkspaceId = event.currentTarget.value;
      renderWorkspace();
    });
    loadOrganizations().catch(() => {
      setStatus("The workspace could not be loaded. Sign in again.", "error");
    });
  }

  global.HumanWireDecisionOSApp = Object.freeze({
    loadOrganizations,
    postJSON,
    setPanel,
  });

  if (global.document.readyState === "loading") {
    global.document.addEventListener("DOMContentLoaded", initialize, {once: true});
  } else {
    initialize();
  }
})(globalThis);
