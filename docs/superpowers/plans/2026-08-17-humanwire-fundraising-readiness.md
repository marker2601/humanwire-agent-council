# HumanWire Fundraising Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Deliver a premium Fundraising Readiness playbook that turns private startup documents into a cited, rubric-based readiness assessment, investment memo, risk register, deck revision plan, diligence list, and founder-approved sharing package.

**Architecture:** Organization-scoped uploads enter Cloud Storage through short-lived server grants, then asynchronous Document AI extraction creates immutable cited evidence records. The transparent ADK council analyzes those records, but deterministic rubric code computes scores and the HumanWire gateway controls artifact finalization and sharing.

**Tech Stack:** Cloud Storage, Document AI, Pub/Sub, Firestore, Google ADK/Gemini, Pydantic v2, FastAPI, existing DecisionOS tenancy and council layers, pytest.

**Spec:** docs/superpowers/specs/2026-08-17-humanwire-decisionos-design.md

## Global Constraints

- No claim guarantees funding, investment, valuation, legal compliance, or financial performance.
- Customer files are private, tenant-bound, encrypted by the platform, excluded from logs/analytics, and never public by default.
- Filenames are display metadata, never trusted storage paths.
- Every extracted fact retains source digest, location, extractor version, and confirmation state.
- The readiness score is deterministic rubric code; a model cannot invent or directly assign a score.
- Generated artifacts separate evidence, inference, assumption, contradiction, and missing information.
- Sharing requires authenticated founder authority and an exact artifact digest.
- The existing Launch Decision playbook and public demo remain compatible.

---

### Task 1: Fundraising domain and rubric contracts

**Files:**
- Create: src/humanwire/fundraising_models.py
- Create: src/humanwire/fundraising_rubric.py
- Test: tests/humanwire/test_fundraising_models.py
- Test: tests/humanwire/test_fundraising_rubric.py

**Interfaces:**
- Consumes: DecisionOS workspace/playbook models and Council evidence classes.
- Produces: FundraisingDocumentType, FundraisingProfile, RubricDimension, RubricFinding, ReadinessAssessment, and evaluate_readiness.

- [ ] **Step 1: Write model and scoring RED tests**

~~~python
def test_missing_evidence_cannot_receive_a_high_score() -> None:
    assessment = evaluate_readiness(profile=minimal_profile(), evidence=())
    assert assessment.overall_score is None
    assert assessment.evidence_coverage == 0
    assert "market_evidence" in assessment.blocked_dimensions


def test_model_text_cannot_override_rubric_score() -> None:
    finding = rubric_finding(score=2, evidence_ids=("e1",))
    assessment = evaluate_readiness(
        profile=valid_profile(),
        evidence=(confirmed_evidence("e1"),),
        model_candidates=(candidate_claiming_score(5),),
        findings=(finding,),
    )
    assert assessment.dimensions[0].score == 2
~~~

Add score-anchor, confidence, coverage, duplicate evidence, unsupported numeric claim, unsafe financial guarantee, and no-data cases.

- [ ] **Step 2: Run RED**

Run: python -m pytest tests/humanwire/test_fundraising_models.py tests/humanwire/test_fundraising_rubric.py -v
Expected: missing-module collection failures.

- [ ] **Step 3: Implement strict models and pure scoring**

~~~python
class ReadinessAssessment(_FundraisingModel):
    rubric_version: Literal["humanwire.fundraising/v1"]
    dimensions: tuple[RubricDimensionResult, ...]
    overall_score: float | None
    evidence_coverage: float = Field(ge=0, le=1)
    blocked_dimensions: tuple[str, ...]


def evaluate_readiness(
    *,
    profile: FundraisingProfile,
    evidence: tuple[ConfirmedEvidence, ...],
    findings: tuple[RubricFinding, ...],
) -> ReadinessAssessment:
    accepted = validate_findings_against_evidence(findings, evidence)
    dimensions = score_dimensions(profile, accepted)
    coverage = evidence_coverage(dimensions)
    return ReadinessAssessment.from_dimensions(dimensions, coverage=coverage)
~~~

The nine dimensions and exact anchors are versioned constants: problem urgency, market evidence, product differentiation, traction quality, business model, financial coherence, team/execution risk, diligence readiness, and fundraising narrative.

- [ ] **Step 4: Run GREEN, lint, and commit**

~~~bash
python -m pytest tests/humanwire/test_fundraising_models.py tests/humanwire/test_fundraising_rubric.py -v
python -m ruff check src/humanwire/fundraising_models.py src/humanwire/fundraising_rubric.py tests/humanwire/test_fundraising_models.py tests/humanwire/test_fundraising_rubric.py
git add src/humanwire/fundraising_models.py src/humanwire/fundraising_rubric.py tests/humanwire/test_fundraising_models.py tests/humanwire/test_fundraising_rubric.py
git commit -m "feat: define fundraising readiness rubric"
~~~

### Task 2: Private upload grants and object registry

**Files:**
- Create: src/humanwire/evidence_uploads.py
- Modify: src/humanwire/decisionos_app.py
- Test: tests/humanwire/test_evidence_uploads.py
- Test: tests/humanwire/test_decisionos_app.py
- Modify: infra/firebase/storage.rules

**Interfaces:**
- Consumes: DecisionOSContext and Cloud Storage client.
- Produces: create_upload_grant, finalize_upload, quarantine_upload, EvidenceUpload, and exact upload/finalize endpoints.

- [ ] **Step 1: Write upload boundary RED tests**

~~~python
def test_grant_uses_server_generated_path(upload_service, context) -> None:
    grant = upload_service.create_upload_grant(
        context,
        workspace_id="wrk_01",
        document_type=FundraisingDocumentType.PITCH_DECK,
        media_type="application/pdf",
        size_bytes=2_000_000,
    )
    assert grant.object_name.startswith(
        f"organizations/{context.organization_id}/workspaces/wrk_01/uploads/"
    )
    assert "pitch deck.pdf" not in grant.object_name


def test_cross_tenant_finalize_is_not_found(upload_service, other_context) -> None:
    with pytest.raises(UploadUnavailable):
        upload_service.finalize_upload(other_context, "upload_org_a")
~~~

Cover duplicate headers, wrong Origin/App Check/CSRF, unsupported media types, size cap, zero-byte files, metadata mismatch, checksum mismatch, expired/reused grants, path traversal, quarantined objects, and private exception graphs.

- [ ] **Step 2: Run RED**

Run: python -m pytest tests/humanwire/test_evidence_uploads.py -v
Expected: missing-module collection failure.

- [ ] **Step 3: Implement short-lived grants and finalization**

~~~python
class EvidenceUploadService:
    def create_upload_grant(
        self,
        context: DecisionOSContext,
        *,
        workspace_id: str,
        document_type: FundraisingDocumentType,
        media_type: str,
        size_bytes: int,
    ) -> UploadGrant:
        raise NotImplementedError

    def finalize_upload(
        self,
        context: DecisionOSContext,
        upload_id: str,
        *,
        sha256: str,
    ) -> EvidenceUpload:
        raise NotImplementedError
~~~

Finalize reads object metadata server-side, verifies generation/size/type/checksum, marks the immutable generation accepted once, and emits an extraction job. Browser Storage Rules continue to deny arbitrary writes outside a grant path.

- [ ] **Step 4: Run GREEN and commit**

~~~bash
python -m pytest tests/humanwire/test_evidence_uploads.py tests/humanwire/test_decisionos_app.py -q
git add src/humanwire/evidence_uploads.py src/humanwire/decisionos_app.py infra/firebase/storage.rules tests/humanwire/test_evidence_uploads.py tests/humanwire/test_decisionos_app.py
git commit -m "feat: accept private fundraising evidence"
~~~

### Task 3: Document AI extraction pipeline

**Files:**
- Create: src/humanwire/document_intake.py
- Create: src/humanwire/document_worker.py
- Modify: src/humanwire/cloud_dispatch.py
- Test: tests/humanwire/test_document_intake.py
- Test: tests/humanwire/test_document_worker.py

**Interfaces:**
- Consumes: finalized EvidenceUpload, Document AI client, Pub/Sub dispatcher, and evidence repository.
- Produces: DocumentExtraction, EvidenceExcerpt, ExtractionStatus, and idempotent extraction jobs.

- [ ] **Step 1: Write extraction RED tests**

~~~python
def test_extracted_fact_retains_exact_source_binding() -> None:
    extraction = parse_document_ai_response(
        upload=accepted_upload(),
        response=document_ai_fixture(),
    )
    first = extraction.excerpts[0]
    assert first.upload_id == accepted_upload().upload_id
    assert first.source_digest == accepted_upload().sha256
    assert first.page_number == 1
    assert first.start_offset >= 0


def test_duplicate_pubsub_delivery_is_idempotent(worker, repository) -> None:
    assert worker.process(job()) == "completed"
    assert worker.process(job()) == "duplicate"
    assert repository.extraction_count == 1
~~~

Cover malformed processor responses, page/offset overflow, oversized text, unsupported spreadsheets, timeout, object generation mismatch, worker lease expiry/recovery, provider exception privacy, and deleted source invalidation.

- [ ] **Step 2: Run RED**

Run: python -m pytest tests/humanwire/test_document_intake.py tests/humanwire/test_document_worker.py -v
Expected: collection failures.

- [ ] **Step 3: Implement typed provider adapter and worker**

~~~python
class DocumentProcessor(Protocol):
    def process(self, source: DocumentSource) -> ProviderDocument:
        raise NotImplementedError


class DocumentWorker:
    def process(self, message: DocumentJob) -> ExtractionDisposition:
        claim = self._repository.claim(message)
        if claim is not ClaimStatus.CLAIMED:
            return disposition_for(claim)
        # Process one immutable object generation and commit one typed extraction.
~~~

All provider results pass local range, size, MIME, digest, and schema validation before persistence.

- [ ] **Step 4: Run GREEN, fixtures, and commit**

~~~bash
python -m pytest tests/humanwire/test_document_intake.py tests/humanwire/test_document_worker.py -v
python -m ruff check src/humanwire/document_intake.py src/humanwire/document_worker.py tests/humanwire/test_document_intake.py tests/humanwire/test_document_worker.py
git add src/humanwire/document_intake.py src/humanwire/document_worker.py src/humanwire/cloud_dispatch.py tests/humanwire/test_document_intake.py tests/humanwire/test_document_worker.py
git commit -m "feat: extract cited fundraising evidence"
~~~

### Task 4: Fundraising specialist council

**Files:**
- Create: src/humanwire/fundraising_council.py
- Modify: src/humanwire/council_registry.py
- Modify: src/humanwire/council_gateway.py
- Test: tests/humanwire/test_fundraising_council.py

**Interfaces:**
- Consumes: confirmed evidence, fundraising rubric, and generic council runner.
- Produces: market, finance, product, risk, investor-fit, diligence, red-team, and synthesis candidates.

- [ ] **Step 1: Write playbook RED tests**

~~~python
def test_financial_specialist_cannot_promote_unconfirmed_numbers() -> None:
    result = run_fundraising_council(evidence_with_unconfirmed_revenue())
    revenue_claim = claim_named(result, "revenue")
    assert revenue_claim.classification != ClaimClassification.CONFIRMED_FACT


def test_red_team_surfaces_cross_document_contradiction() -> None:
    result = run_fundraising_council(conflicting_market_size_sources())
    assert any(item.kind == "contradiction" for item in result.challenges)
~~~

Add missing deck/model, incomplete extraction, arbitrary investor ranking, invented valuation, unsupported market number, PII leakage, partial specialist failure, and deterministic gateway rejection cases.

- [ ] **Step 2: Run RED**

Run: python -m pytest tests/humanwire/test_fundraising_council.py -v.

- [ ] **Step 3: Implement registry and typed outputs**

The council can recommend investor-fit criteria but does not scrape personal contact data or claim that a named investor will invest. Each recommendation includes evidence IDs, unresolved questions, confidence, and rubric dimension.

- [ ] **Step 4: Run GREEN and commit**

~~~bash
python -m pytest tests/humanwire/test_fundraising_council.py tests/humanwire/test_google_council.py -q
git add src/humanwire/fundraising_council.py src/humanwire/council_registry.py src/humanwire/council_gateway.py tests/humanwire/test_fundraising_council.py
git commit -m "feat: add fundraising specialist council"
~~~

### Task 5: Founder artifacts and share authority

**Files:**
- Create: src/humanwire/fundraising_artifacts.py
- Create: src/humanwire/artifact_sharing.py
- Test: tests/humanwire/test_fundraising_artifacts.py
- Test: tests/humanwire/test_artifact_sharing.py

**Interfaces:**
- Consumes: accepted recommendation, readiness assessment, evidence registry, and authenticated approver.
- Produces: InvestmentMemo, EvidenceMatrix, RiskRegister, DeckRevisionPlan, InvestorQuestions, DiligenceChecklist, FounderActionPlan, and bounded ShareGrant.

- [ ] **Step 1: Write artifact and sharing RED tests**

~~~python
def test_memo_has_citations_and_explicit_missing_evidence() -> None:
    memo = build_investment_memo(accepted_case())
    assert memo.claims
    assert all(claim.evidence_ids or claim.classification != "confirmed_fact" for claim in memo.claims)
    assert memo.missing_evidence


def test_share_grant_is_digest_bound_and_revocable(service, founder_context) -> None:
    grant = service.create_share_grant(founder_context, artifact_id="artifact_01")
    service.replace_artifact(founder_context, artifact_id="artifact_01")
    with pytest.raises(ShareUnavailable):
        service.load_shared(grant.token)
~~~

Cover wrong role, cross-tenant artifact, expired/reused/revoked grants, private artifact fields, source deletion, formula injection in CSV, HTML injection, and stale recommendation digest.

- [ ] **Step 2: Run RED**

Run: python -m pytest tests/humanwire/test_fundraising_artifacts.py tests/humanwire/test_artifact_sharing.py -v.

- [ ] **Step 3: Implement immutable artifacts and digest-bound sharing**

Share tokens store only hashes, expire, can be revoked, and reveal a separately generated sanitized artifact. The private artifact remains inaccessible through the share route.

- [ ] **Step 4: Run GREEN and commit**

~~~bash
python -m pytest tests/humanwire/test_fundraising_artifacts.py tests/humanwire/test_artifact_sharing.py -q
git add src/humanwire/fundraising_artifacts.py src/humanwire/artifact_sharing.py tests/humanwire/test_fundraising_artifacts.py tests/humanwire/test_artifact_sharing.py
git commit -m "feat: generate founder-approved fundraising artifacts"
~~~

### Task 6: Fundraising workspace experience

**Files:**
- Modify: src/humanwire/templates/decisionos_shell.html
- Modify: src/humanwire/decisionos_static/decisionos-app.js
- Modify: src/humanwire/decisionos_static/decisionos.css
- Test: tests/humanwire/test_fundraising_frontend.py
- Modify: tests/humanwire/decisionos_frontend_harness.js

**Interfaces:**
- Consumes: upload, extraction, council, assessment, artifact, and sharing endpoints.
- Produces: guided evidence checklist, extraction status, council view, rubric, artifacts, and founder approval flow.

- [ ] **Step 1: Write frontend RED tests**

Require visible evidence coverage separate from score, source citations, missing-evidence blockers, explicit AI inference labels, retryable extraction failures, artifact digest confirmation, and no “guaranteed funding” or fictional investor copy.

- [ ] **Step 2: Extend the executable harness**

Exercise upload grant/finalize, progress, failed extraction/retry, council partial failure, score rendering, source drill-down, stale approval rejection, sanitized share link, revocation, mobile tabs, and full reset.

- [ ] **Step 3: Implement the guided flow**

The first screen asks what the founder is raising, stage, target, timing, and available evidence. It does not begin with infrastructure or agent settings. Every page shows the next required human action.

- [ ] **Step 4: Browser QA and commit**

~~~bash
python -m pytest tests/humanwire/test_fundraising_frontend.py -v
node tests/humanwire/decisionos_frontend_harness.js
git add src/humanwire/templates/decisionos_shell.html src/humanwire/decisionos_static tests/humanwire/test_fundraising_frontend.py tests/humanwire/decisionos_frontend_harness.js
git commit -m "feat: deliver fundraising readiness workspace"
~~~

Use synthetic non-identifying startup documents. Verify desktop, tablet, 390×844, keyboard, screen-reader names, 44×44 targets, no overflow, no console errors, and truthful final artifacts.

### Task 7: Privacy, evaluation, cost, and release

**Files:**
- Create: tests/fixtures/humanwire/fundraising/
- Create: tests/humanwire/test_fundraising_e2e.py
- Modify: src/humanwire/council_evals.py
- Modify: infra/google/README.md
- Create: docs/fundraising-readiness.md

**Interfaces:**
- Consumes: all prior tasks.
- Produces: deterministic end-to-end proof, aggregate evaluation, cost budget, and operator runbook.

- [ ] **Step 1: Add hostile privacy and truth fixtures**

Fixtures include conflicting metrics, hidden spreadsheet formulas, prompt injection, PII, unsupported valuation claims, malformed PDFs, duplicate pages, and cross-tenant identifiers. Tests require safe rejection or correct classification.

- [ ] **Step 2: Add cost and latency budgets**

A run has explicit document page, extraction, specialist, token, retry, elapsed-time, and estimated-cost caps. Exceeding any cap yields a partial result and never silently spends beyond the bound.

- [ ] **Step 3: Run final gates**

~~~powershell
python -m pytest tests/humanwire/test_fundraising_models.py tests/humanwire/test_fundraising_rubric.py tests/humanwire/test_evidence_uploads.py tests/humanwire/test_document_intake.py tests/humanwire/test_document_worker.py tests/humanwire/test_fundraising_council.py tests/humanwire/test_fundraising_artifacts.py tests/humanwire/test_artifact_sharing.py tests/humanwire/test_fundraising_frontend.py tests/humanwire/test_fundraising_e2e.py -q
python -m pytest tests/humanwire/test_google_e2e.py tests/humanwire/test_synthetic.py -q
python -m ruff check src tests
git diff --check
~~~

- [ ] **Step 4: Independent professional, security, and first-time-founder review**

Resolve all Critical and Important findings. Require the reviewer to judge whether a founder understands what to do, which statements are evidence, what remains missing, and what HumanWire will not do.

- [ ] **Step 5: Commit**

~~~bash
git add tests/fixtures/humanwire/fundraising tests/humanwire/test_fundraising_e2e.py src/humanwire/council_evals.py infra/google/README.md docs/fundraising-readiness.md
git commit -m "test: qualify Fundraising Readiness release"
~~~
