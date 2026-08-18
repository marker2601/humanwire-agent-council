(function decisionOSAuthentication(global) {
  "use strict";

  const adapter = global.HumanWireFirebase;

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

  function setStatus(message, tone) {
    const target = element("[data-auth-status]");
    if (!target) return;
    target.textContent = message;
    if (tone) target.dataset.tone = tone;
    else delete target.dataset.tone;
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
    global.location.assign("/app");
  }

  async function signInGoogle() {
    setStatus("Opening secure sign in…");
    try {
      const credentials = await adapter.signInWithGoogle(readConfig());
      await exchangeCredential(credentials);
    } catch (_error) {
      setStatus("Sign in could not be completed. Try again.", "error");
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
    exchangeCredential,
    signInGoogle,
  });

  if (global.document.readyState === "loading") {
    global.document.addEventListener("DOMContentLoaded", initialize, {once: true});
  } else {
    initialize();
  }
})(globalThis);
