import {readFile} from "node:fs/promises";

import {
  assertFails,
  assertSucceeds,
  initializeTestEnvironment,
} from "@firebase/rules-unit-testing";
import {
  collection,
  doc,
  getDoc,
  getDocs,
  setDoc,
} from "firebase/firestore";
import {
  getBytes,
  listAll,
  ref,
  uploadBytes,
} from "firebase/storage";

const PROJECT_ID = "humanwire-decisionos-test";
const ORG_A = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA";
const ORG_B = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AB";
const WORKSPACE = "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AA";

const rulesRoot = new URL("../../infra/firebase/", import.meta.url);
const testEnvironment = await initializeTestEnvironment({
  projectId: PROJECT_ID,
  firestore: {
    host: "127.0.0.1",
    port: 8087,
    rules: await readFile(new URL("firestore.rules", rulesRoot), "utf8"),
  },
  storage: {
    host: "127.0.0.1",
    port: 9199,
    rules: await readFile(new URL("storage.rules", rulesRoot), "utf8"),
  },
});

try {
  await testEnvironment.withSecurityRulesDisabled(async (context) => {
    const db = context.firestore();
    await setDoc(doc(db, "organizations", ORG_A), {name: "Northstar Labs"});
    await setDoc(doc(db, "organizations", ORG_A, "members", "member-a"), {
      uid: "member-a",
      role: "viewer",
      status: "active",
    });
    await setDoc(doc(db, "organizations", ORG_A, "members", "owner-a"), {
      uid: "owner-a",
      role: "owner",
      status: "active",
    });
    await setDoc(doc(db, "organizations", ORG_A, "members", "suspended-a"), {
      uid: "suspended-a",
      role: "contributor",
      status: "suspended",
    });
    await setDoc(doc(db, "organizations", ORG_B, "members", "member-b"), {
      uid: "member-b",
      role: "viewer",
      status: "active",
    });
    await setDoc(doc(db, "organizations", ORG_A, "projections", "run-a"), {
      state: "running",
      summary: "Sanitized projection",
    });
    await setDoc(
      doc(db, "organizations", ORG_A, "projections", "run-a", "timeline", "0001"),
      {ordinal: 1, label: "Evidence requested"},
    );
    await setDoc(doc(db, "organizations", ORG_B, "projections", "run-b"), {
      state: "complete",
    });
    await setDoc(doc(db, "organizations", ORG_A, "invitations", "inv-a"), {
      role: "viewer",
    });
    await setDoc(doc(db, "organizations", ORG_A, "audit", "audit-1"), {
      event_name: "organization_created",
    });
    await setDoc(doc(db, "humanwire_private_runs", "run-a"), {
      private: "server-only",
    });

    const storage = context.storage();
    await uploadBytes(
      ref(
        storage,
        `organizations/${ORG_A}/workspaces/${WORKSPACE}/artifacts/memo.pdf`,
      ),
      new Uint8Array([1, 2, 3]),
      {contentType: "application/pdf"},
    );
  });

  const memberDb = testEnvironment.authenticatedContext("member-a").firestore();
  const ownerDb = testEnvironment.authenticatedContext("owner-a").firestore();
  const suspendedDb = testEnvironment.authenticatedContext("suspended-a").firestore();
  const outsiderDb = testEnvironment.authenticatedContext("member-b").firestore();
  const unauthenticatedDb = testEnvironment.unauthenticatedContext().firestore();

  await assertSucceeds(
    getDoc(doc(memberDb, "organizations", ORG_A, "projections", "run-a")),
  );
  await assertSucceeds(
    getDoc(
      doc(
        memberDb,
        "organizations",
        ORG_A,
        "projections",
        "run-a",
        "timeline",
        "0001",
      ),
    ),
  );
  await assertSucceeds(
    getDocs(collection(memberDb, "organizations", ORG_A, "projections")),
  );
  await assertFails(
    getDoc(doc(outsiderDb, "organizations", ORG_A, "projections", "run-a")),
  );
  await assertFails(
    getDocs(collection(outsiderDb, "organizations", ORG_A, "projections")),
  );
  await assertFails(
    getDoc(doc(suspendedDb, "organizations", ORG_A, "projections", "run-a")),
  );
  await assertFails(
    getDoc(doc(unauthenticatedDb, "organizations", ORG_A, "projections", "run-a")),
  );
  await assertFails(getDoc(doc(memberDb, "humanwire_private_runs", "run-a")));
  await assertFails(
    getDoc(doc(memberDb, "organizations", ORG_A, "members", "member-a")),
  );
  await assertFails(
    getDoc(doc(ownerDb, "organizations", ORG_A, "invitations", "inv-a")),
  );
  await assertFails(
    getDocs(collection(ownerDb, "organizations", ORG_A, "invitations")),
  );
  await assertFails(
    getDoc(doc(ownerDb, "organizations", ORG_A, "audit", "audit-1")),
  );
  await assertFails(
    getDocs(collection(ownerDb, "organizations", ORG_A, "audit")),
  );
  await assertFails(
    setDoc(doc(ownerDb, "organizations", ORG_A, "members", "member-a"), {
      role: "owner",
      status: "active",
    }),
  );
  await assertFails(
    setDoc(doc(ownerDb, "organizations", ORG_A, "projections", "run-a"), {
      state: "complete",
    }),
  );

  const memberStorage = testEnvironment.authenticatedContext("member-a").storage();
  const ownerStorage = testEnvironment.authenticatedContext("owner-a").storage();
  const suspendedStorage = testEnvironment.authenticatedContext("suspended-a").storage();
  const outsiderStorage = testEnvironment.authenticatedContext("member-b").storage();
  const unauthenticatedStorage = testEnvironment.unauthenticatedContext().storage();
  const artifactPath = `organizations/${ORG_A}/workspaces/${WORKSPACE}/artifacts/memo.pdf`;
  await assertSucceeds(getBytes(ref(memberStorage, artifactPath)));
  await assertFails(getBytes(ref(outsiderStorage, artifactPath)));
  await assertFails(getBytes(ref(suspendedStorage, artifactPath)));
  await assertFails(getBytes(ref(unauthenticatedStorage, artifactPath)));
  await assertFails(
    listAll(
      ref(memberStorage, `organizations/${ORG_A}/workspaces/${WORKSPACE}/artifacts`),
    ),
  );
  await assertFails(
    uploadBytes(
      ref(memberStorage, `organizations/${ORG_A}/workspaces/${WORKSPACE}/artifacts/script.html`),
      new Uint8Array([1, 2, 3]),
      {contentType: "text/html"},
    ),
  );
  await assertFails(
    uploadBytes(
      ref(memberStorage, `organizations/${ORG_A}/workspaces/${WORKSPACE}/artifacts/large.pdf`),
      new Uint8Array(1024 * 1024),
      {contentType: "application/pdf"},
    ),
  );
  await assertFails(
    uploadBytes(
      ref(ownerStorage, `organizations/${ORG_A}/workspaces/${WORKSPACE}/artifacts/owner.pdf`),
      new Uint8Array([1, 2, 3]),
      {contentType: "application/pdf"},
    ),
  );
  await assertFails(
    getBytes(
      ref(
        memberStorage,
        `organizations/${ORG_A}/workspaces/${WORKSPACE}/%2e%2e/private/source`,
      ),
    ),
  );

  process.stdout.write("DecisionOS Firestore and Storage rules: PASS\n");
} finally {
  await testEnvironment.cleanup();
}
