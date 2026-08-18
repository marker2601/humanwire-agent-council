import {build} from "esbuild";

const source = String.raw`
import {initializeApp} from "firebase/app";
import {
  GoogleAuthProvider,
  getAuth,
  isSignInWithEmailLink,
  sendSignInLinkToEmail,
  signInWithEmailLink,
  signInWithPopup,
  signOut,
} from "firebase/auth";
import {
  ReCaptchaEnterpriseProvider,
  getToken,
  initializeAppCheck,
} from "firebase/app-check";

let state;

function configure(config) {
  if (state) return state;
  const app = initializeApp(config.firebase);
  const auth = getAuth(app);
  const provider = new GoogleAuthProvider();
  provider.setCustomParameters({prompt: "select_account"});
  const appCheck = initializeAppCheck(app, {
    provider: new ReCaptchaEnterpriseProvider(config.appCheckSiteKey),
    isTokenAutoRefreshEnabled: true,
  });
  state = {auth, provider, appCheck};
  return state;
}

async function credential(result) {
  const idToken = await result.user.getIdToken();
  const checked = await getToken(state.appCheck, false);
  return {idToken, appCheckToken: checked.token};
}

globalThis.HumanWireFirebase = Object.freeze({
  configure,
  async signInWithGoogle(config) {
    const current = configure(config);
    return credential(await signInWithPopup(current.auth, current.provider));
  },
  async sendEmailLink(config, email, url) {
    const current = configure(config);
    await sendSignInLinkToEmail(current.auth, email, {
      url,
      handleCodeInApp: true,
    });
  },
  isEmailLink(config, url) {
    const current = configure(config);
    return isSignInWithEmailLink(current.auth, url);
  },
  async completeEmailLink(config, email, url) {
    const current = configure(config);
    return credential(await signInWithEmailLink(current.auth, email, url));
  },
  async appCheckToken(config) {
    const current = configure(config);
    return (await getToken(current.appCheck, false)).token;
  },
  async signOut(config) {
    const current = configure(config);
    await signOut(current.auth);
  },
});
`;

await build({
  stdin: {
    contents: source,
    loader: "js",
    resolveDir: process.cwd(),
    sourcefile: "decisionos-firebase-entry.js",
  },
  outfile: "src/humanwire/decisionos_static/firebase-adapter.js",
  bundle: true,
  format: "iife",
  platform: "browser",
  target: ["es2022"],
  minify: true,
  sourcemap: false,
  legalComments: "none",
});

process.stdout.write("built src/humanwire/decisionos_static/firebase-adapter.js\n");
