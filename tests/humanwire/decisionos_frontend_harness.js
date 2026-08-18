"use strict";

const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

function load(path, context) {
  vm.runInNewContext(fs.readFileSync(path, "utf8"), context, {filename: path});
}

async function main() {
  const requests = [];
  const credentials = {idToken: "private-id-token", appCheckToken: "app-check"};
  const redirects = [];
  const listeners = {};
  const authStatus = {dataset: {}, textContent: ""};
  let googleRedirectStarts = 0;
  let googleRedirectCompletions = 0;
  const context = {
    globalThis: null,
    document: {
      readyState: "loading",
      addEventListener(name, callback) { listeners[name] = callback; },
      querySelector(selector) {
        return selector === "[data-auth-status]" ? authStatus : null;
      },
      querySelectorAll() { return []; },
      cookie: "__Host-humanwire-csrf=csrf-token",
    },
    location: {href: "https://decisionos.test/signin", origin: "https://decisionos.test", assign(url) { redirects.push(url); }},
    fetch: async (url, options = {}) => {
      requests.push({url, options});
      return {ok: true, status: 204, json: async () => ({organizations: []})};
    },
    HumanWireFirebase: {
      async beginGoogleSignIn() { googleRedirectStarts += 1; },
      async completeGoogleSignIn() {
        googleRedirectCompletions += 1;
        return credentials;
      },
      async appCheckToken() { return "app-check"; },
      async signOut() {},
      isEmailLink() { return false; },
    },
    setTimeout,
    clearTimeout,
    URL,
  };
  context.globalThis = context;

  load("src/humanwire/decisionos_static/decisionos-auth.js", context);
  await context.HumanWireDecisionOSAuth.signInGoogle();
  assert.strictEqual(googleRedirectStarts, 1);
  assert.strictEqual(requests.length, 0);

  await context.HumanWireDecisionOSAuth.completeGoogleSignIn();
  assert.strictEqual(googleRedirectCompletions, 1);
  assert.strictEqual(requests.length, 1);
  assert.strictEqual(requests[0].url, "/api/session/login");
  assert.strictEqual(JSON.parse(requests[0].options.body).id_token, "private-id-token");
  assert.strictEqual(requests[0].options.headers["X-Firebase-AppCheck"], "app-check");
  assert.strictEqual(credentials.idToken, "");
  assert.deepStrictEqual(redirects, ["/workspace"]);

  context.HumanWireFirebase.completeGoogleSignIn = async () => {
    throw {code: "appCheck/recaptcha-error"};
  };
  await context.HumanWireDecisionOSAuth.completeGoogleSignIn();
  assert.strictEqual(authStatus.dataset.reason, "appcheck_recaptcha_error");
  assert.strictEqual(authStatus.textContent, "Sign in could not be completed. Try again.");

  load("src/humanwire/decisionos_static/decisionos-app.js", context);
  await context.HumanWireDecisionOSApp.postJSON("/api/session/logout", {confirm: true});
  assert.strictEqual(requests[1].options.headers["X-HumanWire-CSRF"], "csrf-token");
  assert.strictEqual(requests[1].options.headers["X-Firebase-AppCheck"], "app-check");
  assert.strictEqual(requests[1].options.credentials, "same-origin");

  process.stdout.write("decisionos frontend harness: PASS\n");
}

main().catch((error) => {
  process.stderr.write(`${error.stack}\n`);
  process.exitCode = 1;
});
