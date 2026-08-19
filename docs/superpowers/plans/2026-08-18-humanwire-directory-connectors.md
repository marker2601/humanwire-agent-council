# HumanWire Enterprise Directory Connectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Synchronize Microsoft Entra ID and Google Workspace directories into reviewable HumanWire organization drafts using least-privileged read access, immutable source snapshots, and explicit administrator commit.

**Architecture:** Connector adapters authenticate with the source platform, store only isolated secret references in DecisionOS, and produce the same `SourceSnapshot` consumed by the organization import service. Synchronization always creates a proposed diff; it cannot invite, activate, suspend, or mutate the organization graph until a HumanWire administrator reviews and commits a complete snapshot. Provider-specific code remains behind one protocol so disconnect, recovery, and future connectors share semantics.

**Tech Stack:** Python 3.12, Pydantic 2, FastAPI, httpx, Firebase sessions, Firestore, Google Secret Manager, Microsoft Graph v1.0, Google Admin SDK Directory API, OAuth 2.0, pytest, respx/httpx MockTransport, Node harness.

**Spec:** `docs/superpowers/specs/2026-08-18-humanwire-ai-company-onboarding-design.md`

**Prerequisite plan:** `docs/superpowers/plans/2026-08-18-humanwire-organization-foundation.md`

## Global Constraints

- Initial connectors are read-only and never write to Microsoft Entra ID or Google Workspace.
- Microsoft initial scopes are exactly `Organization.Read.All`, `User.Read.All`, and `GroupMember.Read.All`.
- Google initial scopes are exactly `admin.directory.user.readonly`, `admin.directory.group.readonly`, and `admin.directory.orgunit.readonly` under the Google API scope namespace.
- Initial connectors request no mail, chat, calendar, contact, file, drive, device, password, or write scope.
- Firebase Google sign-in does not grant Google Workspace directory access.
- Connector authorization requires an active DecisionOS owner/admin and source-tenant administrator consent.
- OAuth state is single-use, tenant-bound, actor-bound, digest-stored, and expires after ten minutes.
- Access/refresh tokens and client credentials never enter Firestore, browser responses, logs, exceptions, analytics, or model prompts.
- Connector records contain only provider, tenant/customer identifier, safe status, scope version, secret reference, last complete snapshot, and timestamps.
- An incomplete snapshot cannot suspend or remove anyone.
- Synchronization never sends invitations.
- Disconnect revokes local secret material and future sync; it does not delete audit history or the committed organization graph.
- All provider errors map to fixed safe codes.

## File structure

- `src/humanwire/directory_connector_models.py`: connector, authorization, snapshot, status, and secret-reference contracts.
- `src/humanwire/directory_connectors.py`: provider-neutral protocols, registry, state store, and secret store interface.
- `src/humanwire/microsoft_directory.py`: Entra admin consent and Graph reader.
- `src/humanwire/google_workspace_directory.py`: Google OAuth and Admin SDK reader.
- `src/humanwire/directory_sync.py`: snapshot-to-import draft orchestration and reviewed synchronization.
- `src/humanwire/decisionos_connector_routes.py`: authenticated connect/callback/sync/disconnect APIs.
- `src/humanwire/decisionos_static/directory-connectors.js` and `.css`: connector setup, status, diff, and disconnect UI.

---

### Task 1: Provider-neutral connector and secret contracts

**Files:**
- Create: `src/humanwire/directory_connector_models.py`
- Create: `src/humanwire/directory_connectors.py`
- Create: `tests/humanwire/test_directory_connectors.py`

**Interfaces:**
- Consumes: organization IDs, Firebase UIDs, aware clocks, `SourceSnapshot`, and strict Pydantic conventions.
- Produces: `DirectoryProvider`, `ConnectorStatus`, `ConnectorRecord`, `ConnectorAuthorizationRequest`, `AuthorizationStart`, `AuthorizationCallback`, `ConnectedDirectory`, `ConnectorSecret`, `ConnectorSecretReference`, `DirectoryConnectorAdapter`, `ConnectorStateRepository`, `ConnectorSecretStore`, `DirectoryConnectorRegistry`, and fixed `ConnectorUnavailable` errors.

- [ ] **Step 1: Write contract and lifecycle RED tests**

```python
def test_connector_record_retains_reference_not_token() -> None:
    record = connected_microsoft_record()
    dumped = record.model_dump(mode="json")
    assert dumped["secret_reference"].startswith("projects/")
    assert "access_token" not in dumped
    assert "refresh_token" not in dumped


def test_oauth_state_is_actor_and_tenant_bound(service, owner_context) -> None:
    start = service.start(owner_context, DirectoryProvider.MICROSOFT)
    with pytest.raises(ConnectorUnavailable, match="authorization_invalid"):
        service.complete(other_owner_context(), callback_with(start.state))
```

Cover unknown provider, duplicate connector, non-ASCII tenant ID, extra scopes,
lower/upper-case normalization, expired/replayed state, wrong organization, wrong
actor, suspended member, viewer, disabled connector, secret-store failure, provider
exception graph, and connector record serialization.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_directory_connectors.py -v`

Expected: missing-module failure.

- [ ] **Step 3: Implement exact contracts and protocols**

```python
class DirectoryProvider(StrEnum):
    MICROSOFT = "microsoft"
    GOOGLE_WORKSPACE = "google_workspace"


class DirectoryConnectorAdapter(Protocol):
    def start_authorization(
        self,
        request: ConnectorAuthorizationRequest,
    ) -> AuthorizationStart: ...

    def complete_authorization(
        self,
        callback: AuthorizationCallback,
    ) -> ConnectedDirectory: ...

    def snapshot(self, connector: ConnectorRecord) -> SourceSnapshot: ...

    def revoke(self, connector: ConnectorRecord) -> None: ...


class ConnectorSecretStore(Protocol):
    def put(self, secret: ConnectorSecret) -> ConnectorSecretReference: ...
    def access(self, reference: ConnectorSecretReference) -> ConnectorSecret: ...
    def destroy(self, reference: ConnectorSecretReference) -> None: ...
```

Implement an in-memory state/secret reference for tests and a Secret Manager adapter
that returns fixed errors. OAuth state storage retains SHA-256 digest only.

- [ ] **Step 4: Run GREEN**

Run: `python -m pytest tests/humanwire/test_directory_connectors.py -v`

Expected: PASS and recursive exception/privacy scan finds no test sentinel.

- [ ] **Step 5: Commit**

```powershell
git add src/humanwire/directory_connector_models.py src/humanwire/directory_connectors.py tests/humanwire/test_directory_connectors.py
git commit -m "feat: define secure directory connectors"
```

### Task 2: Microsoft Entra ID and Graph connector

**Files:**
- Create: `src/humanwire/microsoft_directory.py`
- Create: `tests/humanwire/test_microsoft_directory.py`

**Interfaces:**
- Consumes: provider-neutral contracts, app client ID/secret reference, redirect URI, tenant admin consent callback, httpx client, and source limits.
- Produces: `MicrosoftDirectoryConnector`, Graph pagination, immutable source snapshot, and fixed-safe provider status.

- [ ] **Step 1: Write consent and scope RED tests**

```python
def test_admin_consent_url_has_exact_read_only_scopes(connector) -> None:
    start = connector.start_authorization(valid_request())
    query = parse_qs(urlsplit(start.authorization_url).query)
    assert set(query["scope"][0].split()) == {
        "https://graph.microsoft.com/Organization.Read.All",
        "https://graph.microsoft.com/User.Read.All",
        "https://graph.microsoft.com/GroupMember.Read.All",
    }
    assert "Mail.Read" not in query["scope"][0]


def test_snapshot_reads_users_groups_members_and_managers_only(graph_transport) -> None:
    connector = microsoft_connector(graph_transport)
    snapshot = connector.snapshot(connected_record())
    assert snapshot.complete is True
    assert graph_transport.request_paths == (
        "/v1.0/organization",
        "/v1.0/users",
        "/v1.0/groups",
        "/v1.0/groups/group-1/members",
        "/v1.0/users/user-1/manager",
        "/v1.0/users/user-2/manager",
    )
```

Cover admin denied, wrong returned tenant, missing admin consent, state mismatch,
authorization-code injection, token response duplicate keys, non-Bearer token, scope
inflation, pagination loop, cross-host nextLink, more than 5,000 users, disabled/guest
users, hidden groups, manager 404, Graph 429 Retry-After cap, timeout, partial page,
malicious profile values, and response/private error retention.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_microsoft_directory.py -v`

Expected: missing-module failure.

- [ ] **Step 3: Implement exact admin-consent and token boundaries**

Build the authorization URL from fixed Microsoft endpoints and configured app ID.
Validate exact HTTPS redirect URI and callback tenant. Exchange authorization only at
`login.microsoftonline.com/{tenant}/oauth2/v2.0/token`. Store provider secret payload
through `ConnectorSecretStore`, then discard local token variables before returning.

- [ ] **Step 4: Implement Graph v1.0 snapshot**

Select only stable IDs, display name, mail/userPrincipalName as source identity,
jobTitle, department, officeLocation, accountEnabled, groups, and manager IDs. Do not
request messages, chats, calendars, contacts, drives, or devices. Follow only HTTPS
`graph.microsoft.com/v1.0` next links, cap pages/records/bytes, and mark the snapshot
incomplete on any required-page failure.

- [ ] **Step 5: Run GREEN**

Run:

```powershell
python -m pytest tests/humanwire/test_microsoft_directory.py -v
python -m ruff check src/humanwire/microsoft_directory.py tests/humanwire/test_microsoft_directory.py
```

Expected: PASS with exact ordered source rows and no provider/private value in errors.

- [ ] **Step 6: Commit**

```powershell
git add src/humanwire/microsoft_directory.py tests/humanwire/test_microsoft_directory.py
git commit -m "feat: connect Microsoft organization directories"
```

### Task 3: Google Workspace directory connector

**Files:**
- Create: `src/humanwire/google_workspace_directory.py`
- Create: `tests/humanwire/test_google_workspace_directory.py`

**Interfaces:**
- Consumes: provider-neutral contracts, OAuth client configuration, admin callback, Google Admin SDK Directory endpoints, httpx client, and source limits.
- Produces: `GoogleWorkspaceDirectoryConnector` and immutable directory source snapshots.

- [ ] **Step 1: Write OAuth and scope RED tests**

```python
def test_google_authorization_has_exact_admin_read_scopes(connector) -> None:
    start = connector.start_authorization(valid_request())
    scopes = set(parse_qs(urlsplit(start.authorization_url).query)["scope"][0].split())
    assert scopes == {
        "https://www.googleapis.com/auth/admin.directory.user.readonly",
        "https://www.googleapis.com/auth/admin.directory.group.readonly",
        "https://www.googleapis.com/auth/admin.directory.orgunit.readonly",
    }
    assert all("gmail" not in value and "drive" not in value for value in scopes)


def test_firebase_login_does_not_satisfy_workspace_consent(connector) -> None:
    with pytest.raises(ConnectorUnavailable, match="admin_consent_required"):
        connector.snapshot(record_with_firebase_session_only())
```

Cover wrong hosted domain/customer ID, non-admin callback, missing offline consent,
state/replay, refresh failure, scope inflation, pagination token loop, cross-host URL,
deleted/suspended user, alias ambiguity, nested groups, org-unit cycles, more than
5,000 users, rate limit, partial response, private provider error, and disconnect.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_google_workspace_directory.py -v`

Expected: missing-module failure.

- [ ] **Step 3: Implement OAuth boundary**

Use fixed Google OAuth endpoints, PKCE where supported, actor/organization-bound
state, exact redirect URI, prompt for admin consent, and exact read-only scopes.
Store the refresh credential only through `ConnectorSecretStore`; return a safe
customer/domain reference and no token.

- [ ] **Step 4: Implement Admin SDK snapshot**

Read users, groups/members, and organization units with bounded pagination. Map
stable Google IDs, primary email as source identity, name, title, department, manager
relation when provided by configured schema, suspended state, groups, and org unit.
Mark incomplete if a required collection fails.

- [ ] **Step 5: Run GREEN**

Run:

```powershell
python -m pytest tests/humanwire/test_google_workspace_directory.py -v
python -m ruff check src/humanwire/google_workspace_directory.py tests/humanwire/test_google_workspace_directory.py
```

Expected: PASS with no Firebase credential reuse.

- [ ] **Step 6: Commit**

```powershell
git add src/humanwire/google_workspace_directory.py tests/humanwire/test_google_workspace_directory.py
git commit -m "feat: connect Google Workspace directories"
```

### Task 4: Reviewed synchronization and safe removals

**Files:**
- Create: `src/humanwire/directory_sync.py`
- Create: `tests/humanwire/test_directory_sync.py`

**Interfaces:**
- Consumes: connector registry, connector state repository, `OrganizationImportService`, current organization graph, and authenticated admin context.
- Produces: `DirectorySyncService.start`, `.preview`, `.commit`, `.disconnect`, `SynchronizationDiff`, and `SynchronizationReceipt`.

- [ ] **Step 1: Write synchronization RED tests**

```python
def test_complete_snapshot_proposes_removed_member_as_suspended(sync_service) -> None:
    sync = sync_service.start(admin_context(), connector_missing_one_active_member())
    diff = sync_service.preview(admin_context(), sync.sync_id)
    assert diff.suspended_subject_ids == (REMOVED_MEMBER,)
    assert repository.subject(REMOVED_MEMBER).lifecycle is SubjectLifecycle.ACTIVE


def test_incomplete_snapshot_never_proposes_removal(sync_service) -> None:
    sync = sync_service.start(admin_context(), incomplete_connector_snapshot())
    diff = sync_service.preview(admin_context(), sync.sync_id)
    assert diff.committable is False
    assert diff.suspended_subject_ids == ()
```

Cover additions, moves, title changes, unchanged rows, source ID reuse, active member
removal, last owner, wrong tenant, stale snapshot, simultaneous sync, duplicate request,
manual correction, auto-accept disabled by default, disconnect during sync, and retry.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_directory_sync.py -v`

Expected: missing-module failure.

- [ ] **Step 3: Implement sync-to-import orchestration**

```python
class DirectorySyncService:
    def start(
        self,
        context: DecisionOSContext,
        connector_id: str,
    ) -> DirectorySynchronization: ...

    def preview(
        self,
        context: DecisionOSContext,
        sync_id: str,
    ) -> SynchronizationDiff: ...

    def commit(
        self,
        context: DecisionOSContext,
        request: CommitSynchronizationRequest,
    ) -> SynchronizationReceipt: ...
```

`start` obtains a complete snapshot and delegates to the import service. `commit`
requires exact sync/draft digests and acknowledgements. Suspension is applied in the
same graph commit and separately disables an existing membership; audit identity is
retained.

- [ ] **Step 4: Implement disconnect**

Disconnect changes connector status first, prevents new sync claims, revokes provider
authorization when possible, destroys local secret material, records fixed outcome,
and preserves the last committed organization graph/import receipt.

- [ ] **Step 5: Run GREEN**

Run: `python -m pytest tests/humanwire/test_directory_sync.py tests/humanwire/test_organization_import.py -q`

Expected: PASS with deterministic diff ordering and no invitation calls.

- [ ] **Step 6: Commit**

```powershell
git add src/humanwire/directory_sync.py tests/humanwire/test_directory_sync.py
git commit -m "feat: reconcile directory synchronization"
```

### Task 5: Connector APIs and onboarding interface

**Files:**
- Create: `src/humanwire/decisionos_connector_routes.py`
- Modify: `src/humanwire/decisionos_app.py`
- Modify: `src/humanwire/decisionos_web.py`
- Modify: `src/humanwire/templates/decisionos_shell.html`
- Create: `src/humanwire/decisionos_static/directory-connectors.js`
- Create: `src/humanwire/decisionos_static/directory-connectors.css`
- Create: `tests/humanwire/test_decisionos_connector_app.py`
- Create: `tests/humanwire/directory_connector_frontend_harness.js`
- Create: `tests/humanwire/test_directory_connector_frontend.py`

**Interfaces:**
- Consumes: connector registry/state, sync service, Firebase context, CSRF/App Check, and Organization Map.
- Produces: connect/callback/status/sync/commit/disconnect routes and interactive connector cards/diff review.

- [ ] **Step 1: Write hostile API RED tests**

```python
def test_start_microsoft_requires_owner_or_admin(client, contributor_headers) -> None:
    response = client.post(
        f"/api/organizations/{ORG}/connectors/microsoft/start",
        headers=contributor_headers,
        json={"confirm": True},
    )
    assert response.status_code == 403
    assert response.json() == {"error": "authorization_denied"}


def test_callback_never_returns_provider_token(client, valid_callback) -> None:
    response = client.get(valid_callback)
    assert response.status_code == 303
    assert "token" not in response.headers["location"].casefold()
    assert "token" not in response.text.casefold()
```

Cover exact raw path, callback query allowlist, state duplicates, Host/Origin, CSRF on
mutations, App Check, wrong org, wrong actor, callback exception, sync conflict,
disconnect retry, fixed headers/errors, and no secret logs.

- [ ] **Step 2: Write executable frontend RED tests**

Harness covers idle, authorization redirect, connected, synchronizing, diff ready,
blocking conflict, committed, stale, expired consent, provider unavailable, and
disconnect. Assert no button is decorative and every state has one safe next action.

- [ ] **Step 3: Run RED**

Run: `python -m pytest tests/humanwire/test_decisionos_connector_app.py tests/humanwire/test_directory_connector_frontend.py -v`

Expected: routes and UI are absent.

- [ ] **Step 4: Implement exact routes**

```text
POST /api/organizations/{org}/connectors/{provider}/start
GET  /api/connectors/{provider}/callback
GET  /api/organizations/{org}/connectors
POST /api/organizations/{org}/connectors/{connector}/sync
GET  /api/organizations/{org}/connectors/{connector}/syncs/{sync}
POST /api/organizations/{org}/connectors/{connector}/syncs/{sync}/commit
POST /api/organizations/{org}/connectors/{connector}/disconnect
```

The callback is an exact GET with allowlisted query keys and single values. Mutations
retain existing DecisionOS security middleware and fixed safe envelopes.

- [ ] **Step 5: Implement connector and diff UI**

Show Microsoft 365 and Google Workspace as real configured/unconfigured cards, exact
read scopes, last complete sync, record counts, proposed additions/changes/suspensions,
blocking errors, Review changes, Commit, and Disconnect. Never imply inbox/calendar
access. Reuse the Organization Map to preview the proposed graph before commit.

- [ ] **Step 6: Run GREEN and browser QA**

```powershell
python -m pytest tests/humanwire/test_decisionos_connector_app.py tests/humanwire/test_directory_connector_frontend.py tests/humanwire/test_decisionos_app.py -q
node --check src/humanwire/decisionos_static/directory-connectors.js
node tests/humanwire/directory_connector_frontend_harness.js
```

Browser-check desktop/tablet/mobile with provider calls mocked at the server boundary;
verify no clipping, false connected status, hidden scope, decorative button, console
error, or sub-44px control.

- [ ] **Step 7: Commit**

```powershell
git add src/humanwire/decisionos_connector_routes.py src/humanwire/decisionos_app.py src/humanwire/decisionos_web.py src/humanwire/templates/decisionos_shell.html src/humanwire/decisionos_static/directory-connectors.js src/humanwire/decisionos_static/directory-connectors.css tests/humanwire/test_decisionos_connector_app.py tests/humanwire/directory_connector_frontend_harness.js tests/humanwire/test_directory_connector_frontend.py
git commit -m "feat: onboard connected organizations"
```

### Task 6: Secret Manager, rules, deployment, and end-to-end release gate

**Files:**
- Modify: `pyproject.toml`
- Modify: `infra/firebase/firestore.rules`
- Modify: `infra/google/firestore.rules`
- Modify: `infra/google/deploy-decisionos.ps1`
- Modify: `infra/google/deploy-decisionos.sh`
- Modify: `infra/google/README.md`
- Create: `tests/humanwire/test_directory_connector_e2e.py`
- Modify: `tests/humanwire/test_decisionos_deployment_contract.py`

**Interfaces:**
- Consumes: all connector tasks, explicit secret names, OAuth application metadata, and service-account IAM.
- Produces: deployable disabled/enabled connector configuration and deterministic provider-boundary E2E proof.

- [ ] **Step 1: Write deployment and E2E RED tests**

The E2E connects a mocked Microsoft tenant, imports users/groups/managers, previews a
move/removal diff, commits, proves zero invites, reconnects idempotently, simulates an
incomplete snapshot that cannot suspend, disconnects, and proves secrets/tokens never
appear in Firestore exports, logs, HTTP responses, exceptions, or analytics. Repeat
the equivalent authorization/snapshot contract for Google Workspace.

- [ ] **Step 2: Run RED**

Run:

```powershell
python -m pytest tests/humanwire/test_directory_connector_e2e.py tests/humanwire/test_decisionos_deployment_contract.py -v
```

Expected: missing secret/IAM/config/rule contracts.

- [ ] **Step 3: Add production secret adapter and dependencies**

Add `google-cloud-secret-manager>=2.24,<3` to the `decisionos` optional dependency.
Bind the DecisionOS service account to access only the named connector secrets and
per-connector secret prefix. Connector documents are server-only under Firestore
rules; browser access is denied.

- [ ] **Step 4: Add exact deployment settings**

Require feature flags, Microsoft application ID/secret name/redirect URI, Google
OAuth client ID/secret name/redirect URI, fixed scope versions, source caps, sync
timeouts, and allowed callback hosts. Disabled mode requires no connector secret.
Deployment output must print booleans and resource names only.

- [ ] **Step 5: Run final gates**

```powershell
python -m pytest tests/humanwire/test_directory_connectors.py tests/humanwire/test_microsoft_directory.py tests/humanwire/test_google_workspace_directory.py tests/humanwire/test_directory_sync.py tests/humanwire/test_decisionos_connector_app.py tests/humanwire/test_directory_connector_frontend.py tests/humanwire/test_directory_connector_e2e.py -q
python -m pytest tests/humanwire/test_organization_e2e.py tests/humanwire/test_decisionos_auth.py tests/humanwire/test_decisionos_app.py tests/humanwire/test_decisionos_deployment_contract.py -q
python -m ruff check src tests
node --check src/humanwire/decisionos_static/directory-connectors.js
node tests/humanwire/directory_connector_frontend_harness.js
git diff --check
```

- [ ] **Step 6: Independent OAuth, privacy, tenant, synchronization, and product review**

Require no Critical or Important findings. Review official Microsoft/Google scope
documentation again immediately before production consent because provider scopes and
consent behavior can change.

- [ ] **Step 7: Commit**

```powershell
git add pyproject.toml infra/firebase/firestore.rules infra/google/firestore.rules infra/google/deploy-decisionos.ps1 infra/google/deploy-decisionos.sh infra/google/README.md tests/humanwire/test_directory_connector_e2e.py tests/humanwire/test_decisionos_deployment_contract.py
git commit -m "test: qualify enterprise directory sync"
```
