# HumanWire DecisionOS Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Firebase-backed identity, secure server sessions, organizations, invitations, RBAC, and tenant-bound DecisionOS workspaces without changing the existing public demo.

**Architecture:** Deploy a separate FastAPI application that exchanges Firebase ID tokens for Secure HttpOnly session cookies, resolves organization membership from Firestore on every protected operation, and denies all direct client writes to authoritative data. Keep `create_google_submission_app` and the frozen public proof unchanged; new DecisionOS repositories bind every record to organization and workspace identifiers.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, Firebase Admin SDK, Cloud Firestore, Firebase Web SDK bundled with esbuild, pytest, Firestore Emulator, Cloud Run.

**Spec:** `docs/superpowers/specs/2026-08-17-humanwire-decisionos-design.md`

## Global Constraints

- Existing public submission routes and deterministic transcript bytes must remain unchanged.
- Firebase identity establishes identity; Firestore membership establishes organization authority.
- Direct browser writes to authoritative runs, approvals, membership, invitations, or audit records are denied.
- Every mutation requires exact Host/Origin, a valid server session, CSRF binding, and App Check after enforcement is enabled.
- Session cookies are Secure, HttpOnly, SameSite=Lax, path `/`, and bounded to at most five days.
- Fixed error responses and logs must not retain tokens, cookies, email addresses, provider payloads, invitation secrets, private content, or filesystem paths.
- Use TDD, run the named focused test after every cycle, and commit only the files listed by the task.

---

### Task 1: DecisionOS identity and tenancy models

**Files:**
- Create: `src/humanwire/decisionos_models.py`
- Test: `tests/humanwire/test_decisionos_models.py`

**Interfaces:**
- Consumes: Pydantic v2 strict/frozen model conventions used by `studio_models.py`.
- Produces: `DecisionOSRole`, `DecisionOSPrincipal`, `OrganizationMembership`, `DecisionWorkspace`, `DecisionOSContext`, `WorkspacePlaybook`, and opaque identifier validators.

- [ ] **Step 1: Write strict model tests**

```python
def test_context_requires_matching_active_membership() -> None:
    principal = DecisionOSPrincipal(uid="firebase-user-01", email_verified=True)
    membership = OrganizationMembership(
        organization_id="org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA",
        uid=principal.uid,
        role=DecisionOSRole.DECISION_OWNER,
        status="active",
    )
    context = DecisionOSContext(principal=principal, membership=membership)
    assert context.organization_id == membership.organization_id


@pytest.mark.parametrize("value", ["../org", "ORG SPACE", "", "a" * 129])
def test_opaque_ids_reject_unsafe_values(value: str) -> None:
    with pytest.raises(ValidationError):
        DecisionWorkspace(
            workspace_id=value,
            organization_id="org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA",
            name="Launch decision",
            playbook=WorkspacePlaybook.LAUNCH_DECISION,
            created_by_uid="firebase-user-01",
        )
```

- [ ] **Step 2: Run the tests and capture RED**

Run: `python -m pytest tests/humanwire/test_decisionos_models.py -v`
Expected: collection fails because `humanwire.decisionos_models` does not exist.

- [ ] **Step 3: Implement the strict models**

```python
class DecisionOSRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    DECISION_OWNER = "decision_owner"
    CONTRIBUTOR = "contributor"
    APPROVER = "approver"
    VIEWER = "viewer"


class DecisionOSPrincipal(_DecisionOSModel):
    uid: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    email_verified: bool
    provider_ids: tuple[str, ...] = ()


class DecisionOSContext(_DecisionOSModel):
    principal: DecisionOSPrincipal
    membership: OrganizationMembership

    @model_validator(mode="after")
    def membership_belongs_to_principal(self) -> Self:
        if self.membership.uid != self.principal.uid or self.membership.status != "active":
            raise ValueError("active membership must match principal")
        return self
```

Use ULID-shaped opaque strings for organization/workspace IDs. Do not put names,
emails, or Firebase UIDs into Firestore document paths other than the exact member
document key.

- [ ] **Step 4: Run focused GREEN and lint**

Run: `python -m pytest tests/humanwire/test_decisionos_models.py -v`
Expected: PASS.
Run: `python -m ruff check src/humanwire/decisionos_models.py tests/humanwire/test_decisionos_models.py`
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/humanwire/decisionos_models.py tests/humanwire/test_decisionos_models.py
git commit -m "feat: define DecisionOS tenancy models"
```

### Task 2: Firebase session and App Check boundary

**Files:**
- Modify: `pyproject.toml`
- Create: `src/humanwire/decisionos_auth.py`
- Test: `tests/humanwire/test_decisionos_auth.py`

**Interfaces:**
- Consumes: `DecisionOSPrincipal` from Task 1.
- Produces: `SessionAuthenticator` protocol, `FirebaseSessionAuthenticator`, `AppCheckVerifier`, `SessionCookieConfig`, `fixed_auth_error`, and `csrf_matches`.

- [ ] **Step 1: Add Firebase Admin as an explicit optional dependency**

```toml
decisionos = [
  "firebase-admin>=7,<8",
]
```

Install for development with `python -m pip install -e ".[dev,google,decisionos]"`.

- [ ] **Step 2: Write auth boundary RED tests**

```python
def test_session_cookie_is_secure_http_only_and_bounded(fake_firebase) -> None:
    auth = FirebaseSessionAuthenticator(fake_firebase, max_age=timedelta(days=5))
    result = auth.exchange_id_token("opaque-id-token")
    assert result.principal.uid == "firebase-user-01"
    assert result.cookie.max_age_seconds == 432000
    assert result.cookie.secure is True
    assert result.cookie.http_only is True
    assert result.cookie.same_site == "lax"


def test_private_exception_graph_is_not_retained(fake_firebase) -> None:
    fake_firebase.raise_with_secret("PRIVATE-ID-TOKEN")
    with pytest.raises(AuthenticationUnavailable) as captured:
        FirebaseSessionAuthenticator(fake_firebase).exchange_id_token("opaque")
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "PRIVATE" not in repr(captured.value)
```

Add tests for revoked cookies, unverified email where required, malformed claims,
duplicate/empty App Check tokens, mismatched CSRF cookies/headers, max-age overflow,
and a clock-skewed token.

- [ ] **Step 3: Run auth tests and capture RED**

Run: `python -m pytest tests/humanwire/test_decisionos_auth.py -v`
Expected: collection fails because the auth module is absent.

- [ ] **Step 4: Implement dependency-injected verification**

```python
class SessionAuthenticator(Protocol):
    def exchange_id_token(self, id_token: str) -> AuthenticatedSession:
        raise NotImplementedError

    def verify_session_cookie(
        self, cookie: str, *, check_revoked: bool
    ) -> DecisionOSPrincipal:
        raise NotImplementedError


class FirebaseSessionAuthenticator:
    def exchange_id_token(self, id_token: str) -> AuthenticatedSession:
        try:
            claims = self._auth.verify_id_token(id_token, check_revoked=True)
            cookie = self._auth.create_session_cookie(
                id_token,
                expires_in=self._cookie.max_age,
            )
        except Exception:  # exception details must not cross the boundary
            failed = True
        if failed:
            raise AuthenticationUnavailable("authentication_unavailable") from None
        return AuthenticatedSession(
            principal=_principal_from_claims(claims),
            cookie=self._cookie.bind(cookie),
        )
```

Construct safe exceptions after leaving `except` blocks. Never return decoded claims
to the browser. `AppCheckVerifier.verify()` accepts one ASCII token and returns only
an opaque app ID or a fixed failure.

- [ ] **Step 5: Run focused GREEN and lint**

Run: `python -m pytest tests/humanwire/test_decisionos_auth.py -v`
Expected: PASS.
Run: `python -m ruff check src/humanwire/decisionos_auth.py tests/humanwire/test_decisionos_auth.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/humanwire/decisionos_auth.py tests/humanwire/test_decisionos_auth.py
git commit -m "feat: establish Firebase session boundary"
```

### Task 3: Organization, invitation, and RBAC repository

**Files:**
- Create: `src/humanwire/decisionos_store.py`
- Test: `tests/humanwire/test_decisionos_store.py`

**Interfaces:**
- Consumes: Task 1 models and injected Firestore client/clock/random-token source.
- Produces: `DecisionOSRepository.create_organization`, `create_invitation`, `accept_invitation`, `load_context`, `create_workspace`, and `require_permission`.

- [ ] **Step 1: Write transaction and race tests**

```python
def test_create_organization_atomically_makes_creator_owner(repository, principal) -> None:
    organization = repository.create_organization(principal, "Northstar Labs")
    context = repository.load_context(principal, organization.organization_id)
    assert context.membership.role is DecisionOSRole.OWNER


def test_invitation_is_one_time_and_role_bounded(repository, owner, invitee) -> None:
    invitation = repository.create_invitation(
        owner,
        role=DecisionOSRole.APPROVER,
        expires_in=timedelta(days=7),
    )
    membership = repository.accept_invitation(invitee, invitation.token)
    assert membership.role is DecisionOSRole.APPROVER
    with pytest.raises(InvitationUnavailable):
        repository.accept_invitation(invitee, invitation.token)
```

Add two-thread acceptance, expired/revoked invite, cross-organization token, owner
demotion, last-owner removal, inactive membership, role escalation, missing
workspace, and alias-only cross-tenant load tests.

- [ ] **Step 2: Run repository tests and capture RED**

Run: `python -m pytest tests/humanwire/test_decisionos_store.py -v`
Expected: missing-module collection failure.

- [ ] **Step 3: Implement one in-memory semantic repository first**

```python
class DecisionOSRepository(Protocol):
    def load_context(
        self, principal: DecisionOSPrincipal, organization_id: str
    ) -> DecisionOSContext:
        raise NotImplementedError

    def create_workspace(
        self,
        context: DecisionOSContext,
        *,
        name: str,
        playbook: WorkspacePlaybook,
    ) -> DecisionWorkspace:
        raise NotImplementedError
```

Permission rules are an explicit immutable map. `viewer` has read only;
`approver` cannot manage members; `decision_owner` cannot modify organization
settings; only owner/admin can invite; the final owner cannot be removed.

- [ ] **Step 4: Implement Firestore transactions with identical semantics**

Store only `sha256(invitation_token)` and return the raw token exactly once.
Organization creation writes the organization, owner membership, and audit event in
one transaction. Invitation acceptance checks hash, expiry, status, and email-domain
constraint if configured, then writes membership and marks the invitation accepted
in one transaction.

- [ ] **Step 5: Run emulator and semantic parity GREEN**

Run: `python -m pytest tests/humanwire/test_decisionos_store.py -v`
Expected: in-memory tests PASS; emulator tests skip unless an explicit disposable
emulator variable is set.
Run with emulator: `python -m pytest -m firestore_emulator tests/humanwire/test_decisionos_store.py -v`
Expected: Firestore and in-memory parameterized cases PASS.

- [ ] **Step 6: Commit**

```bash
git add src/humanwire/decisionos_store.py tests/humanwire/test_decisionos_store.py
git commit -m "feat: add DecisionOS organization authority"
```

### Task 4: Protected DecisionOS FastAPI application

**Files:**
- Create: `src/humanwire/decisionos_app.py`
- Test: `tests/humanwire/test_decisionos_app.py`

**Interfaces:**
- Consumes: authenticator and repository from Tasks 2–3.
- Produces: `create_decisionos_app`, `/api/session/*`, `/api/organizations/*`, `/api/workspaces/*`, and protected `/app` routes.

- [ ] **Step 1: Write exact-route and hostile-header RED tests**

```python
def test_protected_app_requires_verified_session(client) -> None:
    response = client.get("/app")
    assert response.status_code == 401
    assert response.json() == {"error": "authentication_required"}


def test_cross_tenant_workspace_alias_is_not_an_authority(client, member_session) -> None:
    response = client.get(
        "/api/organizations/org_A/workspaces/workspace_B",
        cookies=member_session.cookies,
    )
    assert response.status_code == 404
    assert response.json() == {"error": "workspace_not_found"}
```

Cover encoded raw paths, duplicate Cookie/Origin/App-Check/CSRF headers, wrong Host,
query strings on mutation routes, unsupported methods, oversized bodies, malformed
JSON, stale/revoked sessions, role failures, login/logout cookie attributes, and
route/render exception redaction.

- [ ] **Step 2: Run app tests and capture RED**

Run: `python -m pytest tests/humanwire/test_decisionos_app.py -v`
Expected: missing-module collection failure.

- [ ] **Step 3: Implement middleware and safe endpoints**

```python
@dataclass(frozen=True)
class DecisionOSDependencies:
    authenticator: SessionAuthenticator
    app_check: AppCheckVerifier
    repository: DecisionOSRepository
    allowed_hosts: frozenset[str]


def create_decisionos_app(dependencies: DecisionOSDependencies) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    # Exact raw-path, Host/Origin, body, session, CSRF and App Check boundaries.
    return app
```

Mutation order is: validate raw request shape, Host/Origin, size/type, App Check,
session, CSRF, membership, role, model schema, then repository transaction. No
failure branch may reveal whether a different organization owns an identifier.

- [ ] **Step 4: Prove the old demo remains byte-compatible**

Run: `python -m pytest tests/humanwire/test_google_submission_app.py tests/humanwire/test_google_e2e.py tests/humanwire/test_submission_app.py -q`
Expected: PASS with no fixture or digest changes.

- [ ] **Step 5: Commit**

```bash
git add src/humanwire/decisionos_app.py tests/humanwire/test_decisionos_app.py
git commit -m "feat: serve authenticated DecisionOS workspaces"
```

### Task 5: Firebase sign-in and workspace shell

**Files:**
- Create: `package.json`
- Create: `scripts/build_decisionos_frontend.mjs`
- Create: `src/humanwire/templates/decisionos_login.html`
- Create: `src/humanwire/templates/decisionos_shell.html`
- Create: `src/humanwire/decisionos_static/decisionos-auth.js`
- Create: `src/humanwire/decisionos_static/decisionos-app.js`
- Create: `src/humanwire/decisionos_static/decisionos.css`
- Modify: `pyproject.toml`
- Test: `tests/humanwire/test_decisionos_frontend.py`
- Test: `tests/humanwire/decisionos_frontend_harness.js`

**Interfaces:**
- Consumes: Task 4 routes and server-rendered public Firebase configuration.
- Produces: accessible sign-in, organization switcher, workspace home, invitation acceptance, and sign-out.

- [ ] **Step 1: Add source-level frontend contract tests**

```python
def test_signed_out_page_has_one_clear_authentication_action(app_client) -> None:
    page = app_client.get("/signin")
    assert 'data-sign-in-google' in page.text
    assert "Sign in with Google" in page.text
    assert "Firebase" not in visible_text(page.text)


def test_application_navigation_targets_real_panels(app_client, owner_session) -> None:
    page = app_client.get("/app", cookies=owner_session.cookies)
    assert_real_navigation(page.text, ["Home", "Decisions", "Evidence", "Team"])
```

- [ ] **Step 2: Add an executable Node harness RED**

The harness supplies fake Firebase functions and fetch responses, then asserts:

- Google sign-in exchanges an ID token once and clears it from client state.
- auth failure stays on the sign-in page with fixed copy.
- organization selection reloads only authorized data.
- invitation acceptance cannot be repeated.
- sign-out clears application state and server session.
- no token, cookie, email, or private response enters DOM, storage, console, URL, or
  analytics.

Run: `node tests/humanwire/decisionos_frontend_harness.js`
Expected: FAIL because the controller is absent.

- [ ] **Step 3: Bundle pinned Firebase modules and implement the shell**

```javascript
const credential = await signInWithPopup(auth, googleProvider);
const idToken = await credential.user.getIdToken();
await postSession("/api/session/login", { id_token: idToken });
await signOut(auth);
location.assign("/app");
```

The production controller never stores the ID token. The workspace shell uses real
buttons or anchors, 44×44 minimum targets, visible focus, 14px minimum text, truthful
empty states, and no fake navigation labels.

- [ ] **Step 4: Run frontend and browser gates**

Run: `python -m pytest tests/humanwire/test_decisionos_frontend.py -v`
Run: `node tests/humanwire/decisionos_frontend_harness.js`
Run: `node --check src/humanwire/decisionos_static/decisionos-auth.js`
Run: `node --check src/humanwire/decisionos_static/decisionos-app.js`
Expected: all PASS.

Use a real browser at 1680×950, 600×900, and 390×844. Verify sign-in, organization
creation, invitation acceptance, keyboard order, focus visibility, no horizontal
overflow, loading/failure states, and sign-out. Record no real email or token in
screenshots.

- [ ] **Step 5: Commit**

```bash
git add package.json scripts/build_decisionos_frontend.mjs src/humanwire/templates/decisionos_login.html src/humanwire/templates/decisionos_shell.html src/humanwire/decisionos_static pyproject.toml tests/humanwire/test_decisionos_frontend.py tests/humanwire/decisionos_frontend_harness.js
git commit -m "feat: add DecisionOS authenticated workspace shell"
```

### Task 6: Firestore and Storage client security rules

**Files:**
- Create: `infra/firebase/firebase.json`
- Create: `infra/firebase/firestore.rules`
- Create: `infra/firebase/storage.rules`
- Create: `tests/firebase/package.json`
- Create: `tests/firebase/decisionos.rules.test.mjs`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: collection layout in the approved spec.
- Produces: client read access to sanitized projections for active members; all authoritative writes denied.

- [ ] **Step 1: Write emulator rule tests before rules**

```javascript
await assertSucceeds(memberDb.doc("organizations/orgA/projections/run1").get());
await assertFails(memberDb.doc("organizations/orgB/projections/run1").get());
await assertFails(memberDb.doc("humanwire_private_runs/run1").get());
await assertFails(ownerDb.doc("organizations/orgA/members/user2").set({role: "owner"}));
```

Cover inactive members, viewers, cross-tenant queries, list operations, nested
timeline reads, invitation enumeration, audit access, storage path traversal,
content type, size, and unauthenticated requests.

- [ ] **Step 2: Run rule tests and capture RED**

Run: `npx firebase emulators:exec --config infra/firebase/firebase.json --only firestore,storage "node tests/firebase/decisionos.rules.test.mjs"`
Expected: FAIL because rules are absent.

- [ ] **Step 3: Implement fail-closed rules**

```text
match /humanwire_private_runs/{document=**} {
  allow read, write: if false;
}

match /organizations/{orgId}/projections/{runId}/{document=**} {
  allow read: if isActiveMember(orgId);
  allow write: if false;
}
```

Rules are defense in depth; server IAM remains the authority for Admin SDK access.

- [ ] **Step 4: Run rules GREEN and commit**

Run the emulator command from Step 2. Expected: PASS.

```bash
git add infra/firebase tests/firebase .gitignore
git commit -m "feat: lock DecisionOS client data boundaries"
```

### Task 7: Separate Cloud Run deployment and release proof

**Files:**
- Create: `src/humanwire/decisionos_web.py`
- Create: `infra/google/deploy-decisionos.ps1`
- Create: `infra/google/deploy-decisionos.sh`
- Modify: `infra/google/README.md`
- Modify: `Dockerfile`
- Test: `tests/humanwire/test_decisionos_deployment_contract.py`
- Test: `tests/humanwire/test_decisionos_e2e.py`

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces: separately named `humanwire-decisionos` service and an end-to-end tenant proof.

- [ ] **Step 1: Write deployment contract RED tests**

Assert separate service name, dedicated service account, Firebase project variables,
no API keys in command arguments, Secret Manager references, Firestore indexes/rules
deployment, App Check monitor-before-enforce flag, and unchanged submission service.

Run: `python -m pytest tests/humanwire/test_decisionos_deployment_contract.py -v`
Expected: FAIL because deployment files are absent.

- [ ] **Step 2: Implement lazy production construction**

```python
@lru_cache(maxsize=1)
def build_decisionos_web_app() -> FastAPI:
    settings = DecisionOSSettings()
    firebase_app = initialize_firebase(settings)
    return create_decisionos_app(build_dependencies(settings, firebase_app))
```

Importing the module must not connect to Firebase, Firestore, or a model. Health
checks must not reveal project, organization, or service-account identifiers.

- [ ] **Step 3: Add end-to-end isolated tenant proof**

Create organizations A and B, sign in two fake principals, create one workspace per
organization, invite one approver, and prove every cross-tenant read/mutation fails.
Also prove the public demo's deterministic transcript and semantic digest are
byte-identical to the base fixture.

- [ ] **Step 4: Run final foundation gates**

```powershell
python -m pytest tests/humanwire/test_decisionos_models.py tests/humanwire/test_decisionos_auth.py tests/humanwire/test_decisionos_store.py tests/humanwire/test_decisionos_app.py tests/humanwire/test_decisionos_frontend.py tests/humanwire/test_decisionos_deployment_contract.py tests/humanwire/test_decisionos_e2e.py -q
python -m pytest tests/humanwire/test_google_submission_app.py tests/humanwire/test_google_e2e.py tests/humanwire/test_synthetic.py -q
python -m ruff check src tests scripts
git diff --check
```

Expected: all pass; only documented opt-in emulator tests may skip.

- [ ] **Step 5: Run independent security and product review**

Review session handling, exception graphs, cross-tenant queries, invitation races,
role escalation, Security Rules, IAM, public-demo compatibility, mobile UX, and
privacy scans. Resolve every Critical or Important finding before deployment.

- [ ] **Step 6: Commit**

```bash
git add src/humanwire/decisionos_web.py infra/google Dockerfile tests/humanwire/test_decisionos_deployment_contract.py tests/humanwire/test_decisionos_e2e.py
git commit -m "feat: deploy HumanWire DecisionOS foundation"
```
