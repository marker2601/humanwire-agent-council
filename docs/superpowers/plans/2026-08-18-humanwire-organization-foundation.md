# HumanWire Organization Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build typed whole-organization import, reconciliation, optional activation, authority mapping, and accessible organization/team views without granting access merely because a person was imported.

**Architecture:** New focused organization modules sit beside the existing DecisionOS identity repository. Sources are parsed into a confined snapshot, normalized into an immutable draft, validated, reviewed, and committed transactionally into a tenant-bound graph. The authenticated DecisionOS app exposes only safe projections; graph and table views consume the same projection so counts and relationships cannot diverge.

**Tech Stack:** Python 3.12, Pydantic 2, FastAPI, Firebase session authentication, Firestore transactions, Jinja, vanilla JavaScript/SVG, CSS, pytest, Node harness, openpyxl, pypdf.

**Spec:** `docs/superpowers/specs/2026-08-18-humanwire-ai-company-onboarding-design.md`

## Global Constraints

- Imported subjects never become Firebase users or DecisionOS members automatically.
- `org_subjects` may reference a member UID but cannot grant membership or authority.
- Reporting edges and authority assignments remain separate typed records.
- Only owner/admin contexts with `MANAGE_MEMBERS` may create, reconcile, or commit imports.
- Import commits require the exact reviewed draft digest and reject stale source snapshots.
- Invitations are always an explicit post-commit action.
- Source removal suspends access but never deletes audit attribution.
- Initial file limit is 10 MiB, 5,000 source records, 120 characters per display label, and 64 pages per PDF.
- Formula cells, embedded credentials, commands, private paths, non-finite numbers, cycles, duplicate identities, and cross-tenant references fail closed.
- The graph has a complete tabular equivalent, 44 by 44 CSS-pixel controls, visible focus, keyboard navigation, and reduced-motion behavior.
- Existing DecisionOS auth, tenant isolation, public demo, deterministic persona behavior, and replay hashes remain unchanged when organization features are disabled.

## File structure

- `src/humanwire/organization_models.py`: strict organization, graph, authority, import, reconciliation, and projection contracts.
- `src/humanwire/organization_graph.py`: pure graph and authority validation/evaluation.
- `src/humanwire/organization_sources.py`: bounded CSV, JSON, XLSX, and PDF parsing.
- `src/humanwire/organization_store.py`: in-memory semantic reference and Firestore persistence.
- `src/humanwire/organization_import.py`: draft construction, reconciliation, digest binding, and commit orchestration.
- `src/humanwire/decisionos_organization_routes.py`: authenticated API router.
- `src/humanwire/organization_projection.py`: safe browser projection from authoritative records.
- `src/humanwire/decisionos_static/organization-map.js`: organization, team, authority, and directory interactions.
- `src/humanwire/decisionos_static/organization-map.css`: scoped accessible graph/table presentation.
- Existing `decisionos_app.py`, `decisionos_web.py`, template, Firestore rules, package data, and deployment contracts only receive narrow integration changes.

---

### Task 1: Strict organization and authority contracts

**Files:**
- Create: `src/humanwire/organization_models.py`
- Create: `tests/humanwire/test_organization_models.py`

**Interfaces:**
- Consumes: existing `DecisionOSRole`, organization IDs, Firebase UIDs, and strict Pydantic conventions.
- Produces: `OrganizationSubjectKind`, `SubjectLifecycle`, `OrganizationEdgeKind`, `AuthorityFunction`, `OrganizationSubject`, `OrganizationUnit`, `OrganizationEdge`, `AuthorityAssignment`, `AuthorityRequest`, `AuthorityDecision`, `SourceRecord`, `SourceSnapshot`, `OrganizationGraph`, `OrganizationGraphCandidate`, `ImportDraft`, `ImportReconciliation`, `CommitImportRequest`, `ImportReceipt`, `OrganizationProjection`, and their exact ID patterns.

- [ ] **Step 1: Write model RED tests**

```python
def test_imported_human_is_not_a_membership() -> None:
    subject = OrganizationSubject(
        subject_id="sub_01K00000000000000000000000",
        organization_id="org_01K00000000000000000000000",
        kind=OrganizationSubjectKind.HUMAN,
        lifecycle=SubjectLifecycle.DIRECTORY_ONLY,
        display_name="Avery Morgan",
        source_identity="m365:user-42",
    )
    assert subject.member_uid is None
    assert not hasattr(subject, "role")


def test_ai_specialist_rejects_human_lifecycle() -> None:
    with pytest.raises(ValidationError):
        OrganizationSubject(
            subject_id="sub_01K00000000000000000000000",
            organization_id="org_01K00000000000000000000000",
            kind=OrganizationSubjectKind.AI_SPECIALIST,
            lifecycle=SubjectLifecycle.INVITED,
            display_name="Risk Challenger",
            specialist_key="risk_challenger",
        )


def test_reporting_edge_cannot_encode_approval() -> None:
    with pytest.raises(ValidationError):
        OrganizationEdge(
            edge_id="edge_01K00000000000000000000000",
            organization_id="org_01K00000000000000000000000",
            kind=OrganizationEdgeKind.REPORTS_TO,
            source_subject_id="sub_01K00000000000000000000000",
            target_subject_id="sub_01K00000000000000000000001",
            decision_function=AuthorityFunction.APPROVER,
        )
```

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_organization_models.py -v`

Expected: collection fails because `humanwire.organization_models` does not exist.

- [ ] **Step 3: Implement exact strict types**

```python
class OrganizationSubjectKind(StrEnum):
    HUMAN = "human"
    AI_SPECIALIST = "ai_specialist"
    EXTERNAL = "external"
    SERVICE = "service"


class SubjectLifecycle(StrEnum):
    DRAFT_IMPORTED = "draft_imported"
    DIRECTORY_ONLY = "directory_only"
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    NEEDS_REVIEW = "needs_review"


class OrganizationEdgeKind(StrEnum):
    MEMBER_OF = "member_of"
    REPORTS_TO = "reports_to"
    COLLABORATES_WITH = "collaborates_with"


class AuthorityFunction(StrEnum):
    DECISION_OWNER = "decision_owner"
    EVIDENCE_CONTRIBUTOR = "evidence_contributor"
    RECOMMENDER = "recommender"
    CHALLENGER = "challenger"
    APPROVER = "approver"
    EXECUTION_OWNER = "execution_owner"
    OBSERVER = "observer"


class OrganizationSubject(_OrganizationModel):
    subject_id: str = Field(pattern=_SUBJECT_ID)
    organization_id: str = Field(pattern=_ORGANIZATION_ID)
    kind: OrganizationSubjectKind = Field(strict=False)
    lifecycle: SubjectLifecycle = Field(strict=False)
    display_name: str = Field(min_length=1, max_length=120)
    source_identity: str | None = Field(default=None, max_length=256)
    member_uid: str | None = Field(default=None, pattern=_FIREBASE_UID)
    specialist_key: str | None = Field(default=None, pattern=_SPECIALIST_KEY)
    unit_id: str | None = Field(default=None, pattern=_UNIT_ID)
    title: str | None = Field(default=None, max_length=120)
```

Add validators that bind human lifecycle/member UID, forbid member UID on AI/service records, normalize safe display text, require exact ID prefixes, reject duplicate tuple fields, and require aware datetimes. Define immutable strict models for all interfaces named above and stable `model_dump(mode="json")` representations.

- [ ] **Step 4: Run GREEN and boundary tests**

Run: `python -m pytest tests/humanwire/test_organization_models.py -v`

Expected: PASS for valid records and fail-closed coverage for extra keys, wrong enum casing, malicious display text, Unicode compatibility separators, invalid IDs, duplicate source identities, naive times, and human/AI lifecycle confusion.

- [ ] **Step 5: Commit**

```powershell
git add src/humanwire/organization_models.py tests/humanwire/test_organization_models.py
git commit -m "feat: define DecisionOS organization contracts"
```

### Task 2: Graph integrity and decision authority

**Files:**
- Create: `src/humanwire/organization_graph.py`
- Create: `tests/humanwire/test_organization_graph.py`

**Interfaces:**
- Consumes: immutable contracts from Task 1.
- Produces: `validate_organization_graph(graph: OrganizationGraph) -> GraphValidation`, `authority_for(request: AuthorityRequest, assignments: tuple[AuthorityAssignment, ...]) -> AuthorityDecision`, and `project_team_graph(graph, team_id) -> OrganizationGraph`.

- [ ] **Step 1: Write graph and authority RED tests**

```python
def test_reporting_cycle_is_blocking() -> None:
    result = validate_organization_graph(graph_with_reporting_cycle())
    assert result.committable is False
    assert result.blocking_codes == ("reporting_cycle",)


def test_manager_without_authority_cannot_approve() -> None:
    decision = authority_for(
        AuthorityRequest(
            organization_id=ORG,
            subject_id=MANAGER,
            decision_type="fundraising_readiness",
            function=AuthorityFunction.APPROVER,
            occurred_at=NOW,
        ),
        assignments=(unrelated_manager_assignment(),),
    )
    assert decision.allowed is False
    assert decision.reason == "authority_missing"
```

Cover self-reporting, multi-node cycles, orphan unit, cross-organization edge, duplicate primary manager, expired authority, wrong decision type, overlapping conflicting policies, suspended subject, and authority bound to an AI specialist as approver.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_organization_graph.py -v`

Expected: missing-module failure.

- [ ] **Step 3: Implement pure validation and evaluation**

```python
def authority_for(
    request: AuthorityRequest,
    assignments: tuple[AuthorityAssignment, ...],
) -> AuthorityDecision:
    matches = tuple(
        item
        for item in assignments
        if item.organization_id == request.organization_id
        and item.subject_id == request.subject_id
        and item.decision_type == request.decision_type
        and item.function is request.function
        and item.effective_from <= request.occurred_at
        and (item.effective_until is None or request.occurred_at < item.effective_until)
    )
    return AuthorityDecision(
        allowed=len(matches) == 1,
        reason=None if len(matches) == 1 else "authority_missing",
        assignment_id=matches[0].assignment_id if len(matches) == 1 else None,
    )
```

Implement deterministic cycle detection, tenant binding, referential integrity,
single-primary-manager rules, authority conflict detection, AI-approver prohibition,
and canonical sorted diagnostics. Do not infer authority from `REPORTS_TO`.

- [ ] **Step 4: Run GREEN**

Run: `python -m pytest tests/humanwire/test_organization_graph.py -v`

Expected: PASS with deterministic diagnostic ordering under shuffled input.

- [ ] **Step 5: Commit**

```powershell
git add src/humanwire/organization_graph.py tests/humanwire/test_organization_graph.py
git commit -m "feat: validate organization and authority graphs"
```

### Task 3: Tenant-bound organization repository

**Files:**
- Create: `src/humanwire/organization_store.py`
- Create: `tests/humanwire/test_organization_store.py`
- Create: `tests/humanwire/test_organization_firestore.py`
- Modify: `src/humanwire/decisionos_store.py`

**Interfaces:**
- Consumes: `DecisionOSContext`, `DecisionOSPermission.MANAGE_MEMBERS`, Task 1 models, and Task 2 validation.
- Produces: `OrganizationGraphRepository`, `InMemoryOrganizationGraphRepository`, `FirestoreOrganizationGraphRepository`, `commit_graph`, `load_graph`, `save_import_draft`, `load_import_draft`, `list_imports`, and `bind_member`.

- [ ] **Step 1: Write semantic repository RED tests**

```python
def test_commit_requires_exact_reviewed_digest(repository, admin_context) -> None:
    draft = repository.save_import_draft(admin_context, valid_import_draft())
    with pytest.raises(ImportUnavailable):
        repository.commit_graph(
            admin_context,
            draft_id=draft.import_id,
            reviewed_digest="0" * 64,
        )


def test_directory_subject_does_not_create_membership(repository, admin_context) -> None:
    receipt = repository.commit_graph(
        admin_context,
        draft_id=repository.save_import_draft(admin_context, one_person_draft()).import_id,
        reviewed_digest=one_person_draft().semantic_digest,
    )
    assert receipt.committed_subject_count == 1
    with pytest.raises(OrganizationUnavailable):
        repository.load_context(principal_for_imported_person(), ORG)
```

Cover wrong tenant, viewer/admin permissions, stale graph version, duplicate commit,
concurrent commit, audit append, suspend-not-delete, active-member binding, last-owner
protection, and exception graph redaction.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_organization_store.py -v`

Expected: missing-module failure.

- [ ] **Step 3: Implement locked in-memory semantics**

```python
class OrganizationGraphRepository(Protocol):
    def save_import_draft(
        self,
        context: DecisionOSContext,
        draft: ImportDraft,
    ) -> ImportDraft: ...

    def commit_graph(
        self,
        context: DecisionOSContext,
        *,
        draft_id: str,
        reviewed_digest: str,
    ) -> ImportReceipt: ...

    def load_graph(self, context: DecisionOSContext) -> OrganizationGraph: ...
```

Use one `RLock` for the in-memory reference. Bind every key to
`context.organization_id`. Store immutable committed versions and append the exact
actor UID, prior/new graph version, source snapshot digest, and import receipt to
the audit stream.

- [ ] **Step 4: Implement Firestore transaction parity**

Use collections defined by the spec. One Firestore transaction must read current
graph version and draft, validate organization/digest/status, create a new version,
write subjects/units/edges/authority documents, mark the draft committed, and append
an audit document. Use deterministic document IDs. Never enumerate another tenant.

- [ ] **Step 5: Run semantic and emulator gates**

Run:

```powershell
python -m pytest tests/humanwire/test_organization_store.py -v
python -m pytest tests/humanwire/test_organization_firestore.py -m firestore_emulator -v
```

Expected: in-memory suite PASS; emulator suite PASS only when an explicit disposable
emulator is configured, otherwise documented skip.

- [ ] **Step 6: Commit**

```powershell
git add src/humanwire/organization_store.py src/humanwire/decisionos_store.py tests/humanwire/test_organization_store.py tests/humanwire/test_organization_firestore.py
git commit -m "feat: persist tenant-bound organization graphs"
```

### Task 4: Confined organization source parsing

**Files:**
- Modify: `pyproject.toml`
- Create: `src/humanwire/organization_sources.py`
- Create: `tests/humanwire/test_organization_sources.py`
- Create: `tests/fixtures/humanwire/organization/sample.csv`
- Create: `tests/fixtures/humanwire/organization/sample.json`
- Create: `tests/fixtures/humanwire/organization/sample.xlsx`
- Create: `tests/fixtures/humanwire/organization/sample.pdf`

**Interfaces:**
- Consumes: bytes, declared filename, content type, organization ID, and upload limits.
- Produces: `parse_organization_source(request: ParseOrganizationSourceRequest) -> SourceSnapshot`.

- [ ] **Step 1: Write parser RED tests**

```python
@pytest.mark.parametrize("fixture", ["sample.csv", "sample.json", "sample.xlsx"])
def test_structured_sources_produce_equal_canonical_rows(fixture: str) -> None:
    snapshot = parse_organization_source(source_request(fixture))
    assert snapshot.records == expected_source_records()
    assert snapshot.semantic_digest == EXPECTED_DIGEST


def test_xlsx_formula_is_rejected() -> None:
    with pytest.raises(OrganizationSourceRejected, match="source_unsafe"):
        parse_organization_source(xlsx_with_formula("=HYPERLINK(\"https://internal\")"))
```

Cover zip bombs, macros, external relationships, traversal names, malformed UTF-8,
duplicate JSON keys, NaN/Infinity, CSV formulas, NUL/control characters, overlong
cells, more than 5,000 rows, files over 10 MiB, PDFs over 64 pages, encrypted PDFs,
embedded files, JavaScript/actions, credentials, paths, and provider exceptions.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_organization_sources.py -v`

Expected: missing-module failure.

- [ ] **Step 3: Add bounded parsing dependencies and implementation**

Add:

```toml
decisionos = [
  "firebase-admin>=7,<8",
  "openpyxl>=3.1,<4",
  "pypdf>=6,<7",
]
```

Implement exact dispatch by lowercase extension plus matching MIME allowlist. Read
XLSX with `read_only=True`, `data_only=False`, reject any formula and unsupported
relationship before accepting cells. Parse PDF into bounded page fragments only;
PDF text is never treated as a committed person row without mapping and review.
Canonicalize rows by stable source ordinal and hash canonical JSON with SHA-256.

- [ ] **Step 4: Run GREEN and package gates**

Run:

```powershell
python -m pytest tests/humanwire/test_organization_sources.py -v
python -m ruff check src/humanwire/organization_sources.py tests/humanwire/test_organization_sources.py
```

Expected: PASS and no source filename, path, or parsed private value appears in fixed
error messages.

- [ ] **Step 5: Commit**

```powershell
git add pyproject.toml src/humanwire/organization_sources.py tests/humanwire/test_organization_sources.py tests/fixtures/humanwire/organization
git commit -m "feat: parse organization sources safely"
```

### Task 5: Draft mapping, reconciliation, and commit orchestration

**Files:**
- Create: `src/humanwire/organization_import.py`
- Create: `tests/humanwire/test_organization_import.py`

**Interfaces:**
- Consumes: `SourceSnapshot`, `OrganizationGraphRepository`, `OrganizationMapper`, existing graph version, and authenticated context.
- Produces: `OrganizationImportService.create_draft`, `.reconcile`, `.commit`, `RuleOrganizationMapper`, and the `OrganizationMapper.map(snapshot, current_graph) -> OrganizationGraphCandidate` protocol used later by the Gemini plan.

- [ ] **Step 1: Write import lifecycle RED tests**

```python
def test_draft_reconciles_every_source_row(service, admin_context) -> None:
    draft = service.create_draft(admin_context, complete_snapshot())
    reconciliation = service.reconcile(admin_context, draft.import_id)
    assert reconciliation.source_count == 4
    assert reconciliation.normalized_count == 4
    assert reconciliation.rejected_count == 0
    assert sum(reconciliation.lifecycle_counts.values()) == 4


def test_import_never_sends_invitation(service, admin_context, invitation_spy) -> None:
    draft = service.create_draft(admin_context, complete_snapshot())
    service.commit(
        admin_context,
        CommitImportRequest(
            import_id=draft.import_id,
            reviewed_digest=draft.semantic_digest,
            acknowledged_codes=(),
        ),
    )
    assert invitation_spy.calls == []
```

Cover manual corrections, duplicate merge requiring an explicit operation, blocking
versus acknowledged gaps, stale source, stale graph, mapper timeout with deterministic
fallback, invalid mapper output, cyclic reports, leaderless teams, missing authority,
idempotent repeated request, and concurrent admin commits.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_organization_import.py -v`

Expected: missing-module failure.

- [ ] **Step 3: Implement mapping and service state machine**

```python
class OrganizationMapper(Protocol):
    def map(
        self,
        snapshot: SourceSnapshot,
        current_graph: OrganizationGraph,
    ) -> OrganizationGraphCandidate: ...


class OrganizationImportService:
    def create_draft(
        self,
        context: DecisionOSContext,
        snapshot: SourceSnapshot,
    ) -> ImportDraft: ...

    def reconcile(
        self,
        context: DecisionOSContext,
        import_id: str,
    ) -> ImportReconciliation: ...

    def commit(
        self,
        context: DecisionOSContext,
        request: CommitImportRequest,
    ) -> ImportReceipt: ...
```

`RuleOrganizationMapper` maps explicit source columns only. Ambiguous records become
`NEEDS_REVIEW`; it never guesses email identity, approval authority, or a manager.
Commit delegates exact graph validation and transactional persistence to Task 3.

- [ ] **Step 4: Run GREEN**

Run: `python -m pytest tests/humanwire/test_organization_import.py -v`

Expected: PASS with byte-identical reconciliation under reordered source dictionaries.

- [ ] **Step 5: Commit**

```powershell
git add src/humanwire/organization_import.py tests/humanwire/test_organization_import.py
git commit -m "feat: reconcile organization imports"
```

### Task 6: Safe projection and authenticated organization APIs

**Files:**
- Create: `src/humanwire/organization_projection.py`
- Create: `src/humanwire/decisionos_organization_routes.py`
- Modify: `src/humanwire/decisionos_app.py`
- Modify: `src/humanwire/decisionos_web.py`
- Create: `tests/humanwire/test_decisionos_organization_app.py`
- Modify: `tests/humanwire/test_decisionos_deployment_contract.py`

**Interfaces:**
- Consumes: DecisionOS principal/context, source parser, import service, graph repository, and projection builder.
- Produces: exact organization import/graph endpoints and `build_organization_projection(graph, reconciliation) -> OrganizationProjection`.

- [ ] **Step 1: Write hostile API RED tests**

```python
def test_upload_returns_draft_without_inviting(client, owner_headers) -> None:
    response = client.post(
        f"/api/organizations/{ORG}/imports",
        headers=owner_headers,
        files={"source": ("team.csv", CSV_BYTES, "text/csv")},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "draft"
    assert "invitation" not in response.text.casefold()


def test_other_tenant_cannot_read_graph(client, tenant_b_headers) -> None:
    response = client.get(
        f"/api/organizations/{ORG_A}/organization-graph",
        headers=tenant_b_headers,
    )
    assert response.status_code == 404
    assert response.json() == {"error": "organization_not_found"}
```

Cover exact raw paths, HEAD/GET/POST methods, query strings, body length, multipart
ambiguity, duplicate headers, CSRF, App Check, missing membership, viewer writes,
stale digest, private source leakage, fixed errors, exception graphs, and safe headers.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_decisionos_organization_app.py -v`

Expected: import routes return 404 or are missing.

- [ ] **Step 3: Implement a narrow router and dependency wiring**

Expose:

```text
POST /api/organizations/{organization_id}/imports
GET  /api/organizations/{organization_id}/imports/{import_id}
POST /api/organizations/{organization_id}/imports/{import_id}/corrections
POST /api/organizations/{organization_id}/imports/{import_id}/commit
GET  /api/organizations/{organization_id}/organization-graph
GET  /api/organizations/{organization_id}/authority-map
```

Register `APIRouter` inside `create_decisionos_app`. Extend
`DecisionOSDependencies` with exact optional organization services; fail startup when
the feature is enabled but dependencies are absent. Keep disabled-mode route behavior
and existing app bytes unchanged.

- [ ] **Step 4: Implement safe projection**

Projection includes IDs, display labels, functional titles, units, lifecycle/identity
labels, safe graph edges, authority functions, source type, synchronization time, and
reconciliation counts. It excludes email by default, connector IDs, source identities,
member UIDs, tokens, raw rows, evidence, prompts, and provider traces.

- [ ] **Step 5: Run GREEN and regressions**

Run:

```powershell
python -m pytest tests/humanwire/test_decisionos_organization_app.py tests/humanwire/test_decisionos_app.py tests/humanwire/test_decisionos_auth.py -q
python -m ruff check src/humanwire/organization_projection.py src/humanwire/decisionos_organization_routes.py tests/humanwire/test_decisionos_organization_app.py
```

Expected: PASS with no change to unsigned redirect or API 401 behavior.

- [ ] **Step 6: Commit**

```powershell
git add src/humanwire/organization_projection.py src/humanwire/decisionos_organization_routes.py src/humanwire/decisionos_app.py src/humanwire/decisionos_web.py tests/humanwire/test_decisionos_organization_app.py tests/humanwire/test_decisionos_deployment_contract.py
git commit -m "feat: expose organization onboarding safely"
```

### Task 7: Optional activation and subject-bound bulk invitations

**Files:**
- Create: `src/humanwire/organization_activation.py`
- Modify: `src/humanwire/decisionos_store.py`
- Modify: `src/humanwire/decisionos_organization_routes.py`
- Create: `tests/humanwire/test_organization_activation.py`

**Interfaces:**
- Consumes: committed human subjects, owner/admin context, existing invitation repository, and explicit selected subject IDs.
- Produces: `ActivationService.create_invitations`, subject-bound invitation grants, activation receipts, and `bind_member` after verified acceptance.

- [ ] **Step 1: Write invitation separation RED tests**

```python
def test_bulk_invite_only_targets_explicit_subject_ids(service, admin_context) -> None:
    receipt = service.create_invitations(
        admin_context,
        BulkInvitationRequest(subject_ids=(ALICE, BOB), role=DecisionOSRole.CONTRIBUTOR),
    )
    assert receipt.requested_subject_ids == (ALICE, BOB)
    assert repository.subject(CAROL).lifecycle is SubjectLifecycle.DIRECTORY_ONLY


def test_acceptance_binds_verified_uid_once(service, invited_principal) -> None:
    membership = service.accept(invited_principal, valid_subject_invitation_token())
    assert membership.uid == invited_principal.uid
    assert repository.subject(ALICE).member_uid == invited_principal.uid
    with pytest.raises(InvitationUnavailable):
        service.accept(other_principal(), valid_subject_invitation_token())
```

Cover AI/external targets, already active subjects, duplicate email display metadata,
expired/revoked tokens, invitation enumeration, wrong tenant, role escalation, partial
delivery, retry, and imported email not matching authenticated claims.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_organization_activation.py -v`

Expected: missing-module failure.

- [ ] **Step 3: Implement explicit activation service**

```python
class ActivationService:
    def create_invitations(
        self,
        context: DecisionOSContext,
        request: BulkInvitationRequest,
    ) -> BulkInvitationReceipt: ...

    def accept(
        self,
        principal: DecisionOSPrincipal,
        token: str,
    ) -> OrganizationMembership: ...
```

Issue one opaque token per selected committed human subject. Store only token digests.
Bind membership and subject in one transaction. Invitation transport remains an
injected provider and is not invoked when no consented route exists.

- [ ] **Step 4: Run GREEN**

Run: `python -m pytest tests/humanwire/test_organization_activation.py tests/humanwire/test_decisionos_store.py -q`

Expected: PASS with generic legacy invitations preserved for existing callers.

- [ ] **Step 5: Commit**

```powershell
git add src/humanwire/organization_activation.py src/humanwire/decisionos_store.py src/humanwire/decisionos_organization_routes.py tests/humanwire/test_organization_activation.py
git commit -m "feat: activate imported organization members"
```

### Task 8: Interactive organization, team, directory, and authority views

**Files:**
- Modify: `src/humanwire/templates/decisionos_shell.html`
- Create: `src/humanwire/decisionos_static/organization-map.js`
- Create: `src/humanwire/decisionos_static/organization-map.css`
- Modify: `src/humanwire/decisionos_static/decisionos-app.js`
- Modify: `src/humanwire/decisionos_static/decisionos.css`
- Modify: `pyproject.toml`
- Create: `tests/humanwire/organization_frontend_harness.js`
- Create: `tests/humanwire/test_organization_frontend.py`
- Modify: `tests/humanwire/test_decisionos_frontend.py`

**Interfaces:**
- Consumes: `OrganizationProjection` from Task 6 and import/activation endpoints.
- Produces: Organization navigation with Org chart, Teams, Directory, AI workforce, Invitations, Integrations, Import history, and Authority map.

- [ ] **Step 1: Write DOM and controller RED tests**

```python
def test_organization_shell_exposes_all_verified_views() -> None:
    source = decisionos_shell_source()
    for label in (
        "Org chart",
        "Teams",
        "Directory",
        "AI workforce",
        "Invitations",
        "Integrations",
        "Import history",
        "Authority map",
    ):
        assert label in source


def test_graph_and_table_counts_match_executable_harness() -> None:
    result = run_node_harness("complete-import")
    assert result["sourceCount"] == 246
    assert result["renderedSubjectCount"] == 242
    assert result["directoryRowCount"] == 242
    assert result["blockingErrors"] == 0
```

Harness modes must cover empty, upload, mapping, partial, needs-review, committed,
stale, disconnected, cross-team, authority, reduced motion, mobile, and failed graph
render with table fallback.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/humanwire/test_organization_frontend.py -v`

Expected: required organization controls and controller are missing.

- [ ] **Step 3: Implement the product shell and controller**

Replace `Invite teammate` as the primary header action with `Build AI team`; preserve
`Invite people` inside Organization. Add upload/connect/create entry cards, staged
reconciliation, exact count summary, filters, search, collapsible units, subject
drawer, graph/table toggle, and explicit draft commit. Render SVG via DOM creation
only; never inject source HTML. Keep identity kind and lifecycle as separate visible
text labels.

- [ ] **Step 4: Implement stable layouts**

Use computed lane sizes from rendered subject/unit counts, minimum 16 CSS-pixel gaps,
wrapped labels inside cards, and an accessible table containing identical edges.
Organization boundaries and authority edges use labels and line patterns in addition
to color. Preserve selected subject and viewport across unchanged refreshes.

- [ ] **Step 5: Run GREEN, Node, and browser acceptance**

Run:

```powershell
python -m pytest tests/humanwire/test_organization_frontend.py tests/humanwire/test_decisionos_frontend.py -q
node --check src/humanwire/decisionos_static/organization-map.js
node tests/humanwire/organization_frontend_harness.js
```

Browser-check a real 242-subject committed fixture at 1680x950, 600x900, and 390x844.
Assert no node/text collision, horizontal page overflow, hidden primary action,
sub-44px control, count mismatch, console error, or inaccessible relationship.

- [ ] **Step 6: Commit**

```powershell
git add src/humanwire/templates/decisionos_shell.html src/humanwire/decisionos_static/organization-map.js src/humanwire/decisionos_static/organization-map.css src/humanwire/decisionos_static/decisionos-app.js src/humanwire/decisionos_static/decisionos.css pyproject.toml tests/humanwire/organization_frontend_harness.js tests/humanwire/test_organization_frontend.py tests/humanwire/test_decisionos_frontend.py
git commit -m "feat: visualize the DecisionOS organization"
```

### Task 9: Firestore rules, end-to-end proof, and release gate

**Files:**
- Modify: `infra/firebase/firestore.rules`
- Modify: `infra/firebase/firestore.indexes.json`
- Modify: `infra/google/firestore.rules`
- Modify: `infra/google/firestore.indexes.json`
- Modify: `infra/google/deploy-decisionos.ps1`
- Modify: `infra/google/deploy-decisionos.sh`
- Modify: `infra/google/README.md`
- Create: `tests/humanwire/test_organization_e2e.py`
- Modify: `tests/humanwire/test_decisionos_deployment_contract.py`

**Interfaces:**
- Consumes: all tasks in this plan.
- Produces: deterministic whole-organization onboarding proof and deployable disabled/enabled feature configurations.

- [ ] **Step 1: Write end-to-end RED proof**

Create one owner, import a fixture with 246 rows, reconcile 242 committed subjects,
correct two duplicates and two manager gaps, commit the exact digest, verify zero
invitations, explicitly invite two subjects, activate one verified principal, render
equal graph/table counts, evaluate an authority assignment, perform a second sync,
suspend one removed active member without deleting history, and verify tenant B
cannot read any graph/import/authority document.

- [ ] **Step 2: Write rule and deployment RED tests**

Assert browser clients cannot write organizations, graph, import, authority, audit,
connector, or specialist collections directly. Assert only safe projections readable
under membership rules. Require explicit `HUMANWIRE_DECISIONOS_ORGANIZATION_ENABLED`
and size/count/cost settings in deployment scripts.

- [ ] **Step 3: Run RED**

Run:

```powershell
python -m pytest tests/humanwire/test_organization_e2e.py tests/humanwire/test_decisionos_deployment_contract.py -v
```

Expected: rules/configuration and E2E feature wiring fail.

- [ ] **Step 4: Implement rules, indexes, feature flag, and operator docs**

Document upload caps, connector-independent import operation, recovery, audit review,
suspension, export/deletion boundary, feature rollback, and how to verify counts before
activation. The feature flag must leave existing routes and bytes unchanged when off.

- [ ] **Step 5: Run final gates**

```powershell
python -m pytest tests/humanwire/test_organization_models.py tests/humanwire/test_organization_graph.py tests/humanwire/test_organization_store.py tests/humanwire/test_organization_sources.py tests/humanwire/test_organization_import.py tests/humanwire/test_decisionos_organization_app.py tests/humanwire/test_organization_activation.py tests/humanwire/test_organization_frontend.py tests/humanwire/test_organization_e2e.py -q
python -m pytest tests/humanwire/test_decisionos_models.py tests/humanwire/test_decisionos_store.py tests/humanwire/test_decisionos_auth.py tests/humanwire/test_decisionos_app.py tests/humanwire/test_decisionos_frontend.py tests/humanwire/test_decisionos_e2e.py -q
python -m ruff check src tests
node --check src/humanwire/decisionos_static/organization-map.js
node tests/humanwire/organization_frontend_harness.js
git diff --check
```

- [ ] **Step 6: Independent security, privacy, product, and accessibility review**

Require no Critical or Important findings across tenant isolation, membership versus
directory semantics, authority separation, source privacy, upload safety, invitation
consent, graph/table truth, mobile behavior, and rollback.

- [ ] **Step 7: Commit**

```powershell
git add infra/firebase/firestore.rules infra/firebase/firestore.indexes.json infra/google/firestore.rules infra/google/firestore.indexes.json infra/google/deploy-decisionos.ps1 infra/google/deploy-decisionos.sh infra/google/README.md tests/humanwire/test_organization_e2e.py tests/humanwire/test_decisionos_deployment_contract.py
git commit -m "test: qualify organization onboarding"
```
