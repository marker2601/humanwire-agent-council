(function decisionOSAuthentication(global) {
  "use strict";

  const adapter = global.HumanWireFirebase;
  const SAFE_AUTH_REASONS = Object.freeze({
    "auth/network-request-failed": "network_request_failed",
    "auth/operation-not-supported-in-this-environment": "environment_unsupported",
    "auth/unauthorized-domain": "unauthorized_domain",
    "auth/web-storage-unsupported": "web_storage_unsupported",
  });

  function element(selector) {
    return global.document.querySelector(selector);
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

  function setStatus(message, tone, reason) {
    const target = element("[data-auth-status]");
    if (!target) return;
    target.textContent = message;
    if (tone) target.dataset.tone = tone;
    else delete target.dataset.tone;
    if (reason) target.dataset.reason = reason;
    else delete target.dataset.reason;
  }

  function safeAuthReason(error) {
    const code = typeof error?.code === "string" ? error.code : "";
    if (SAFE_AUTH_REASONS[code]) return SAFE_AUTH_REASONS[code];
    if (/^(?:auth|appCheck)\/[a-z][a-z-]{0,79}$/.test(code)) {
      return code.replace("/", "_").replaceAll("-", "_").toLowerCase();
    }
    return "unknown";
  }

  async function exchangeCredential(credentials) {
    const body = JSON.stringify({id_token: credentials.idToken});
    const appCheckToken = credentials.appCheckToken;
    credentials.idToken = "";
    credentials.appCheckToken = "";
    const response = await global.fetch("/api/session/login", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-Firebase-AppCheck": appCheckToken,
      },
      body,
    });
    if (!response.ok) throw new Error("authentication_failed");
    global.location.assign("/workspace");
  }

  async function signInGoogle() {
    setStatus("Redirecting to secure Google sign in…");
    try {
      await adapter.beginGoogleSignIn(readConfig());
    } catch (error) {
      setStatus("Sign in could not be completed. Try again.", "error", safeAuthReason(error));
    }
  }

  async function completeGoogleSignIn() {
    try {
      const credentials = await adapter.completeGoogleSignIn(readConfig());
      if (credentials) await exchangeCredential(credentials);
    } catch (error) {
      setStatus("Sign in could not be completed. Try again.", "error", safeAuthReason(error));
    }
  }

  function showEmailForm() {
    const form = element("[data-email-form]");
    if (!form) return;
    form.hidden = false;
    const input = form.querySelector('input[name="email"]');
    if (input) input.focus();
  }

  async function submitEmail(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const input = form.querySelector('input[name="email"]');
    const email = input ? input.value.trim() : "";
    if (!email) return;
    setStatus("Preparing your secure email link…");
    try {
      const config = readConfig();
      if (adapter.isEmailLink(config, global.location.href)) {
        await exchangeCredential(
          await adapter.completeEmailLink(config, email, global.location.href),
        );
      } else {
        await adapter.sendEmailLink(config, email, `${global.location.origin}/signin`);
        input.value = "";
        setStatus("Check your inbox for the secure sign-in link.");
      }
    } catch (_error) {
      setStatus("The secure email link could not be sent. Try again.", "error");
    }
  }

  function initialize() {
    const google = element("[data-sign-in-google]");
    const email = element("[data-email-link]");
    const form = element("[data-email-form]");
    if (google) google.addEventListener("click", signInGoogle);
    if (email) email.addEventListener("click", showEmailForm);
    if (form) form.addEventListener("submit", submitEmail);
    void completeGoogleSignIn();
    try {
      if (adapter.isEmailLink(readConfig(), global.location.href)) {
        showEmailForm();
        setStatus("Enter the email address that received this link to finish signing in.");
      }
    } catch (_error) {
      setStatus("Sign in is temporarily unavailable.", "error");
    }
  }

  global.HumanWireDecisionOSAuth = Object.freeze({
    completeGoogleSignIn,
    exchangeCredential,
    signInGoogle,
  });

  if (global.document.readyState === "loading") {
    global.document.addEventListener("DOMContentLoaded", initialize, {once: true});
  } else {
    initialize();
  }
})(globalThis);
