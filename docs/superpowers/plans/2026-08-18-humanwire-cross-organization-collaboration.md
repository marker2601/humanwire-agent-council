# HumanWire Cross-Organization Collaboration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let two or more HumanWire organizations coordinate a shared decision through explicit shared subgraphs, scoped artifacts, and separate human approvals without merging tenant directories or private evidence.

**Architecture:** A collaboration space is a first-class server-owned object containing participant organizations, an explicit visible-subject allowlist, artifact grants, decision-type scope, approval policy, expiry, and revocation state. Each organization keeps its own graph, membership, authority, evidence, and model budget. A safe collaboration projection composes only granted records; council inputs and outputs are partitioned by organization and cross the boundary only through approved sanitized artifacts.

**Tech Stack:** Python 3.12, Pydantic 2, FastAPI, Firebase sessions, Firestore transactions, Cloud Run, Pub/Sub, HumanWire council gateway, Jinja, vanilla JavaScript/SVG, pytest, Node harness.

**Spec:** `docs/superpowers/specs/2026-08-18-humanwire-ai-company-onboarding-design.md`

**Prerequisite plans:**
- `docs/superpowers/plans/2026-08-18-humanwire-organization-foundation.md`
- `docs/superpowers/plans/2026-08-18-humanwire-gemini-company-council.md`

## Global Constraints

- Organization graphs are never merged, copied wholesale, or made readable by another tenant.
- Collaboration requires an explicit participating-organization grant accepted by an authorized owner/admin in every organization.
- The grant lists visible subject IDs, decision types, allowed artifact classes, approver requirements, effective time, expiry, and revocation behavior.
- Each organization evaluates its own membership and Authority Map.
- Every participating organization performs its own required human approval.
- Gemini receives only evidence and organization subjects authorized for the shared decision.
- A collaboration identifier alone never authorizes access.
- Revocation removes future access and execution while preserving immutable audit facts.
- Share tokens are digest-stored, bounded, single-purpose, expiring, and excluded from URLs after exchange.
- Private organization IDs, member UIDs, email addresses, raw evidence, prompts, hidden reasoning, model traces, connector metadata, and internal graph edges never enter another tenant's projection.
- Existing single-organization decisions remain byte/semantic compatible when collaboration is disabled.

## File structure

- `src/humanwire/collaboration_models.py`: collaboration, participant, grant, artifact, approval, and projection contracts.
- `src/humanwire/collaboration_policy.py`: pure access and approval evaluation.
- `src/humanwire/collaboration_store.py`: in-memory and Firestore transactional persistence.
- `src/humanwire/collaboration_service.py`: invitation, acceptance, update, revoke, and shared decision lifecycle.
- `src/humanwire/collaboration_projection.py`: safe shared-subgraph and timeline projection.
- `src/humanwire/decisionos_collaboration_routes.py`: authenticated APIs.
- `src/humanwire/decisionos_static/collaboration-map.js` and `.css`: organization-boundary visualization and controls.

---

### Task 1: Collaboration and grant contracts

**Files:**
- Create: `src/humanwire/collaboration_models.py`
- Create: `tests/humanwire/test_collaboration_models.py`

**Interfaces:**
- Consumes: organization/subject/workspace/authority IDs, safe artifact classes, aware UTC clocks, and strict Pydantic conventions.
- Produces: `CollaborationStatus`, `ParticipantStatus`, `CollaborationSpace`, `CollaborationParticipant`, `SharedSubjectGrant`, `SharedArtifactGrant`, `CollaborationApprovalPolicy`, `CollaborationInvitation`, `CollaborationDecision`, `CollaborationProjection`, `CreateCollaborationRequest`, `AcceptCollaborationRequest`, `CollaborationAccessRequest`, `CollaborationAccessDecision`, `RequiredOrganizationApproval`, `CollaborationApprovalRequest`, and exact collaboration IDs/digests.

- [ ] **Step 1: Write contract RED tests**

```python
def test_collaboration_requires_at_least_two_distinct_organizations() -> None:
    with pytest.raises(ValidationError):
        CollaborationSpace(
            collaboration_id=COLLAB,
            status=CollaborationStatus.DRAFT,
            participant_organization_ids=(ORG_A, ORG_A),
            decision_types=("partnership",),
            expires_at=EXPIRY,
        )


def test_projection_rejects_private_graph_fields() -> None:
    with pytest.raises(ValidationError):
        CollaborationProjection.model_validate(
            {**valid_projection(), "member_uid": "private-user"}
        )
```

Cover duplicate participants, one-tenant space, unbounded expiry, empty decision
scope, wildcard subjects/artifacts, unknown artifact class, raw organization name in
opaque public record, wrong participant status, approval policy without each org,
extra keys, naive times, and private/public model confusion.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_collaboration_models.py -v`

Expected: missing-module failure.

- [ ] **Step 3: Implement strict contracts**

```python
class CollaborationStatus(StrEnum):
    DRAFT = "draft"
    PENDING_ACCEPTANCE = "pending_acceptance"
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class CollaborationParticipant(_CollaborationModel):
    organization_id: str
    status: ParticipantStatus = Field(strict=False)
    accepted_by_uid_digest: str | None = Field(default=None, pattern=_SHA256)
    accepted_at: datetime | None = None


class SharedSubjectGrant(_CollaborationModel):
    organization_id: str
    subject_id: str
    visible_fields: tuple[Literal["display_name", "functional_title", "unit_label"], ...]
```

Define explicit artifact classes, approval requirements, collaboration invitations,
shared decision state, and safe projection. No wildcard or arbitrary field list is
accepted.

- [ ] **Step 4: Run GREEN**

Run: `python -m pytest tests/humanwire/test_collaboration_models.py -v`

Expected: PASS with stable semantic digest under participant input reordering.

- [ ] **Step 5: Commit**

```powershell
git add src/humanwire/collaboration_models.py tests/humanwire/test_collaboration_models.py
git commit -m "feat: define organization collaboration contracts"
```

### Task 2: Collaboration access and approval policy

**Files:**
- Create: `src/humanwire/collaboration_policy.py`
- Create: `tests/humanwire/test_collaboration_policy.py`

**Interfaces:**
- Consumes: collaboration space, participant organizations, current context, organization Authority Maps, decision type, artifact class, time, and requested action.
- Produces: `evaluate_collaboration_access(request) -> CollaborationAccessDecision`, `required_approvals(decision) -> tuple[RequiredOrganizationApproval, ...]`, and `is_collaboration_terminal`.

- [ ] **Step 1: Write fail-closed policy RED tests**

```python
def test_member_of_one_org_cannot_read_other_private_subject() -> None:
    decision = evaluate_collaboration_access(
        access_request(context_for_org_a(), subject_id=PRIVATE_B),
        active_space(),
    )
    assert decision.allowed is False
    assert decision.reason == "subject_not_shared"


def test_shared_decision_requires_each_organization_approval() -> None:
    requirements = required_approvals(shared_decision_for(ORG_A, ORG_B))
    assert tuple(item.organization_id for item in requirements) == (ORG_A, ORG_B)
```

Cover pending, expired, revoked, wrong decision type, unaccepted participant, wrong
workspace, hidden subject field, unshared artifact class, stale grant version,
reporting manager without approval authority, AI approver, suspended member, replayed
approval, and one organization attempting to waive another's approval.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_collaboration_policy.py -v`

Expected: missing-module failure.

- [ ] **Step 3: Implement pure policy**

```python
def required_approvals(
    decision: CollaborationDecision,
) -> tuple[RequiredOrganizationApproval, ...]:
    return tuple(
        RequiredOrganizationApproval(
            organization_id=organization_id,
            decision_type=decision.decision_type,
            recommendation_digest=decision.recommendation_digest,
        )
        for organization_id in decision.participant_organization_ids
    )
```

Evaluate organization membership first, then collaboration status/time/version,
participant acceptance, action scope, subject/artifact grant, and local Authority Map.
Return fixed reasons without revealing whether an unshared resource exists.

- [ ] **Step 4: Run GREEN**

Run: `python -m pytest tests/humanwire/test_collaboration_policy.py -v`

Expected: PASS with deterministic required-approval ordering.

- [ ] **Step 5: Commit**

```powershell
git add src/humanwire/collaboration_policy.py tests/humanwire/test_collaboration_policy.py
git commit -m "feat: enforce collaboration authority"
```

### Task 3: Transactional collaboration repository

**Files:**
- Create: `src/humanwire/collaboration_store.py`
- Create: `tests/humanwire/test_collaboration_store.py`
- Create: `tests/humanwire/test_collaboration_firestore.py`

**Interfaces:**
- Consumes: collaboration models/policy, DecisionOS contexts, Firestore, identifiers, and aware clock.
- Produces: `CollaborationRepository`, `InMemoryCollaborationRepository`, `FirestoreCollaborationRepository`, and methods for create, invite, accept, activate, update grants, create decision, record approval, revoke, and load safe participant view.

- [ ] **Step 1: Write repository RED tests**

```python
def test_space_activates_only_after_every_participant_accepts(repository) -> None:
    space = repository.create(owner_a(), create_request_for(ORG_A, ORG_B))
    repository.accept(owner_a(), acceptance_for(space, ORG_A))
    assert repository.load(owner_a(), space.collaboration_id).status == "pending_acceptance"
    repository.accept(owner_b(), acceptance_for(space, ORG_B))
    assert repository.load(owner_a(), space.collaboration_id).status == "active"


def test_one_tenant_cannot_update_other_tenant_grants(repository) -> None:
    with pytest.raises(CollaborationUnavailable):
        repository.update_grants(owner_a(), grant_update_for_org_b())
```

Cover duplicate invitation, token replay/expiry, invite enumeration, participant
removal, stale grant digest, concurrent acceptance, concurrent revoke/decision,
approval idempotency, wrong recommendation digest, terminal mutation, audit append,
cross-tenant read/write, and private exception graphs.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_collaboration_store.py -v`

Expected: missing-module failure.

- [ ] **Step 3: Implement locked semantic reference**

```python
class CollaborationRepository(Protocol):
    def create(
        self,
        context: DecisionOSContext,
        request: CreateCollaborationRequest,
    ) -> CollaborationSpace: ...

    def accept(
        self,
        context: DecisionOSContext,
        request: AcceptCollaborationRequest,
    ) -> CollaborationSpace: ...

    def record_approval(
        self,
        context: DecisionOSContext,
        request: CollaborationApprovalRequest,
    ) -> CollaborationDecision: ...
```

Store token digests only and use constant-time comparison. Every repository operation
loads caller membership and participant record before any resource detail is returned.

- [ ] **Step 4: Implement Firestore transaction parity**

Use `organization_collaborations/{collaboration_id}` plus server-only invitation,
decision, approval, and audit subcollections. Transactions bind collaboration/version,
participant org, actor UID digest, grant digest, recommendation digest, nonce, state,
and time. No participant document contains another organization's private graph.

- [ ] **Step 5: Run GREEN and emulator gate**

```powershell
python -m pytest tests/humanwire/test_collaboration_store.py -v
python -m pytest tests/humanwire/test_collaboration_firestore.py -m firestore_emulator -v
```

Expected: semantic suite PASS; emulator suite PASS under explicit disposable emulator
or documented skip otherwise.

- [ ] **Step 6: Commit**

```powershell
git add src/humanwire/collaboration_store.py tests/humanwire/test_collaboration_store.py tests/humanwire/test_collaboration_firestore.py
git commit -m "feat: persist organization collaborations"
```

### Task 4: Collaboration lifecycle and partitioned council input

**Files:**
- Create: `src/humanwire/collaboration_service.py`
- Modify: `src/humanwire/council_orchestrator.py`
- Modify: `src/humanwire/council_gateway.py`
- Create: `tests/humanwire/test_collaboration_service.py`
- Create: `tests/humanwire/test_collaboration_council.py`

**Interfaces:**
- Consumes: collaboration repository/policy, organization graphs, safe artifact registry, council planner/orchestrator, and per-organization approval gateway.
- Produces: `CollaborationService`, `build_partitioned_council_request`, shared decision lifecycle, and dual/multi-organization approval finality.

- [ ] **Step 1: Write service and evidence-boundary RED tests**

```python
def test_council_input_contains_only_granted_subjects_and_artifacts(service) -> None:
    request = service.build_council_request(active_collaboration_decision())
    serialized = request.model_dump_json()
    assert SHARED_A_LABEL in serialized
    assert SHARED_B_LABEL in serialized
    assert PRIVATE_A_LABEL not in serialized
    assert PRIVATE_B_LABEL not in serialized


def test_first_organization_approval_does_not_finalize(service) -> None:
    decision = service.approve(approver_a(), valid_approval_for_a())
    assert decision.status == "approval_required"
    decision = service.approve(approver_b(), valid_approval_for_b())
    assert decision.status == "approved"
```

Cover unaccepted participant, revoked space during run, grant change during run,
shared artifact deletion, model attempt to reference private evidence, unequal local
authority requirements, reject/request-changes by either organization, stale digest,
partial council failure, cost partition, and audit ordering.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_collaboration_service.py tests/humanwire/test_collaboration_council.py -v`

Expected: missing service and partition support.

- [ ] **Step 3: Implement lifecycle service**

```python
class CollaborationService:
    def create(
        self,
        context: DecisionOSContext,
        request: CreateCollaborationRequest,
    ) -> CollaborationSpace: ...

    def build_council_request(
        self,
        context: DecisionOSContext,
        decision_id: str,
    ) -> CouncilRunRequest: ...

    def approve(
        self,
        context: DecisionOSContext,
        request: CollaborationApprovalRequest,
    ) -> CollaborationDecision: ...
```

Freeze grant and artifact digests into a decision at creation. Build separate source
partitions per organization, then a shared safe input containing only granted fields.
Store per-organization model cost and approval independently.

- [ ] **Step 4: Extend council gateway without weakening single-org behavior**

The gateway validates every candidate evidence reference against the frozen shared
grant. A collaboration recommendation requires every local approval challenge before
terminal approval. Feature-off and single-organization paths remain semantically
unchanged.

- [ ] **Step 5: Run GREEN and council regressions**

```powershell
python -m pytest tests/humanwire/test_collaboration_service.py tests/humanwire/test_collaboration_council.py -q
python -m pytest tests/humanwire/test_council_gateway.py tests/humanwire/test_council_orchestrator.py -q
```

Expected: PASS and single-org fixtures retain exact plan/recommendation digests.

- [ ] **Step 6: Commit**

```powershell
git add src/humanwire/collaboration_service.py src/humanwire/council_orchestrator.py src/humanwire/council_gateway.py tests/humanwire/test_collaboration_service.py tests/humanwire/test_collaboration_council.py
git commit -m "feat: coordinate decisions across organizations"
```

### Task 5: Safe collaboration projection, APIs, and boundary graph

**Files:**
- Create: `src/humanwire/collaboration_projection.py`
- Create: `src/humanwire/decisionos_collaboration_routes.py`
- Modify: `src/humanwire/decisionos_app.py`
- Modify: `src/humanwire/decisionos_web.py`
- Modify: `src/humanwire/templates/decisionos_shell.html`
- Create: `src/humanwire/decisionos_static/collaboration-map.js`
- Create: `src/humanwire/decisionos_static/collaboration-map.css`
- Create: `tests/humanwire/test_decisionos_collaboration_app.py`
- Create: `tests/humanwire/collaboration_frontend_harness.js`
- Create: `tests/humanwire/test_collaboration_frontend.py`

**Interfaces:**
- Consumes: collaboration service/repository/policy and safe organization/council projections.
- Produces: collaboration create/accept/grant/revoke/decision APIs and organization-boundary product view.

- [ ] **Step 1: Write hostile API RED tests**

```python
def test_partner_projection_contains_only_shared_subgraph(client, partner_headers) -> None:
    response = client.get(
        f"/api/collaborations/{COLLAB}/projection",
        headers=partner_headers,
    )
    assert response.status_code == 200
    assert response.json()["subject_count"] == 4
    assert PRIVATE_A_LABEL not in response.text
    assert "member_uid" not in response.text


def test_revoked_collaboration_is_not_readable(client, partner_headers) -> None:
    response = client.get(
        f"/api/collaborations/{REVOKED}/projection",
        headers=partner_headers,
    )
    assert response.status_code == 404
```

Cover exact paths/methods, Host/Origin, CSRF/App Check, token exchange, invitation
replay, wrong org, unaccepted participant, grant update, stale digest, expiry,
revocation, artifacts, approvals, fixed errors/headers, and private exception graph.

- [ ] **Step 2: Write executable frontend RED tests**

Harness covers draft, pending partner, active, shared decision, conflict, approval A,
approval B, reject, request changes, revoked, expired, mobile, reduced motion, and
graph failure/table fallback. Assert boundaries and shared edge labels are visible.

- [ ] **Step 3: Run RED**

Run: `python -m pytest tests/humanwire/test_decisionos_collaboration_app.py tests/humanwire/test_collaboration_frontend.py -v`

Expected: missing routes and UI.

- [ ] **Step 4: Implement exact routes and safe projection**

```text
POST /api/organizations/{org}/collaborations
POST /api/collaborations/accept
GET  /api/collaborations/{collaboration}/projection
POST /api/collaborations/{collaboration}/grants
POST /api/collaborations/{collaboration}/decisions
POST /api/collaborations/{collaboration}/decisions/{decision}/approval
POST /api/collaborations/{collaboration}/revoke
```

Exchange invitation token into the authenticated session and redirect to a clean URL.
Projection uses collaboration-scoped opaque display IDs and exact granted labels;
internal tenant IDs/paths remain private.

- [ ] **Step 5: Implement the organization-to-organization map**

Render each organization in a separately labeled boundary with only granted subjects,
shared decision nodes, authorized artifacts, and cross-boundary handoffs. Provide an
identical table, grant inspector, approval status per organization, expiry/revoke
controls, and final shared package. Never show a collapsed count implying hidden
subjects are accessible.

- [ ] **Step 6: Run GREEN and browser acceptance**

```powershell
python -m pytest tests/humanwire/test_decisionos_collaboration_app.py tests/humanwire/test_collaboration_frontend.py -q
node --check src/humanwire/decisionos_static/collaboration-map.js
node tests/humanwire/collaboration_frontend_harness.js
```

Browser-check desktop/tablet/mobile with two organizations and a completed dual-
approval decision. Require no private label, graph collision, clipping, stale approval,
console error, sub-44px control, or graph/table mismatch.

- [ ] **Step 7: Commit**

```powershell
git add src/humanwire/collaboration_projection.py src/humanwire/decisionos_collaboration_routes.py src/humanwire/decisionos_app.py src/humanwire/decisionos_web.py src/humanwire/templates/decisionos_shell.html src/humanwire/decisionos_static/collaboration-map.js src/humanwire/decisionos_static/collaboration-map.css tests/humanwire/test_decisionos_collaboration_app.py tests/humanwire/collaboration_frontend_harness.js tests/humanwire/test_collaboration_frontend.py
git commit -m "feat: visualize partner organization decisions"
```

### Task 6: Rules, end-to-end privacy proof, and release gate

**Files:**
- Modify: `infra/firebase/firestore.rules`
- Modify: `infra/firebase/firestore.indexes.json`
- Modify: `infra/google/firestore.rules`
- Modify: `infra/google/firestore.indexes.json`
- Modify: `infra/google/deploy-decisionos.ps1`
- Modify: `infra/google/deploy-decisionos.sh`
- Modify: `infra/google/README.md`
- Create: `tests/humanwire/test_collaboration_e2e.py`
- Modify: `tests/humanwire/test_decisionos_deployment_contract.py`

**Interfaces:**
- Consumes: all collaboration tasks.
- Produces: deterministic two-organization release proof and disabled/enabled deployment configuration.

- [ ] **Step 1: Write full lifecycle RED proof**

Create organizations A and B, import private graphs, share two subjects from each,
invite/accept both owners, start a partnership decision, prove private subjects and
evidence never enter the other tenant/projection/model payload, record challenge and
revision, approve in A, prove still nonterminal, approve in B, generate a sanitized
shared package, revoke access, prove future reads/runs fail, and preserve immutable
audit facts. Add organization C attacks against every alias/token/path.

- [ ] **Step 2: Write Firestore rule and index RED tests**

Direct browser reads/writes to collaboration authority, invitation digests, approval
nonces, private evidence, model payloads, and audit are denied. Browser-readable
projection requires active participant membership and active unexpired grant.

- [ ] **Step 3: Run RED**

Run:

```powershell
python -m pytest tests/humanwire/test_collaboration_e2e.py tests/humanwire/test_decisionos_deployment_contract.py -v
```

Expected: rules/configuration/E2E wiring fail.

- [ ] **Step 4: Implement rules, indexes, feature flag, and operator runbook**

Require explicit `HUMANWIRE_DECISIONOS_COLLABORATION_ENABLED`, maximum participants,
maximum shared subjects/artifacts, maximum lifetime, and revocation settings. Document
incident revocation, audit review, partner offboarding, artifact invalidation, and
feature rollback.

- [ ] **Step 5: Run final gates**

```powershell
python -m pytest tests/humanwire/test_collaboration_models.py tests/humanwire/test_collaboration_policy.py tests/humanwire/test_collaboration_store.py tests/humanwire/test_collaboration_service.py tests/humanwire/test_collaboration_council.py tests/humanwire/test_decisionos_collaboration_app.py tests/humanwire/test_collaboration_frontend.py tests/humanwire/test_collaboration_e2e.py -q
python -m pytest tests/humanwire/test_organization_e2e.py tests/humanwire/test_company_council_e2e.py tests/humanwire/test_decisionos_auth.py tests/humanwire/test_decisionos_app.py -q
python -m ruff check src tests
node --check src/humanwire/decisionos_static/collaboration-map.js
node tests/humanwire/collaboration_frontend_harness.js
git diff --check
```

- [ ] **Step 6: Independent tenant, authority, privacy, product, and browser review**

Require no Critical or Important findings. Review from each participant and attacker
viewpoint; confirm no tenant can infer another's hidden organization size, people,
edges, evidence, internal authority, model usage, or connector state.

- [ ] **Step 7: Commit**

```powershell
git add infra/firebase/firestore.rules infra/firebase/firestore.indexes.json infra/google/firestore.rules infra/google/firestore.indexes.json infra/google/deploy-decisionos.ps1 infra/google/deploy-decisionos.sh infra/google/README.md tests/humanwire/test_collaboration_e2e.py tests/humanwire/test_decisionos_deployment_contract.py
git commit -m "test: qualify partner organization collaboration"
```
