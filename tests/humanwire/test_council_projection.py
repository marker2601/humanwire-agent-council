from __future__ import annotations

from humanwire.council_models import (
    ChallengeSeverity,
    ClaimClassification,
    CouncilCandidate,
    CouncilChallenge,
    CouncilRecommendation,
    EvidenceClaim,
)
from humanwire.council_projection import build_council_projection
from humanwire.council_runtime import (
    FirestoreCouncilEvidenceRegistry,
    FirestoreCouncilRunStore,
    build_demo_evidence_records,
)
from humanwire.decisionos_models import (
    DecisionOSContext,
    DecisionOSPrincipal,
    DecisionOSRole,
    MembershipStatus,
    OrganizationMembership,
)
from humanwire.google_council import (
    CouncilExecutionEvent,
    CouncilExecutionResult,
    CouncilExecutionStatus,
)


def _result() -> CouncilExecutionResult:
    fact = EvidenceClaim(
        claim_id="claim_market_01",
        statement="Three pilots completed the evaluation period.",
        classification=ClaimClassification.CONFIRMED_FACT,
        evidence_ids=("evidence_market_01",),
        confidence=0.9,
    )
    inference = EvidenceClaim(
        claim_id="claim_inference_01",
        statement="The limited launch may improve conversion learning.",
        classification=ClaimClassification.MODEL_INFERENCE,
        evidence_ids=(),
        confidence=0.65,
    )
    candidate_ids = tuple(
        f"candidate_{name}_01"
        for name in (
            "market_intelligence",
            "financial_analysis",
            "product_technical",
            "risk_compliance",
        )
    )
    candidates = tuple(
        CouncilCandidate(
            candidate_id=candidate_id,
            specialist_id=candidate_id.removeprefix("candidate_").removesuffix("_01"),
            summary="The specialist completed an evidence-bound review.",
            claims=(fact,),
            questions=("Which pilots converted to paid contracts?",),
            recommended_action="Confirm conversion evidence.",
            policy_version="council-v1",
        )
        for candidate_id in candidate_ids
    )
    challenge = CouncilChallenge(
        challenge_id="challenge_red_01",
        challenger_id="red_team",
        target_candidate_id=candidate_ids[0],
        challenged_claim_ids=(fact.claim_id,),
        severity=ChallengeSeverity.MATERIAL,
        issue="Pilot completion does not establish paid conversion.",
        required_action="Confirm conversion before broad launch.",
        policy_version="council-v1",
    )
    recommendation = CouncilRecommendation(
        recommendation_id="recommendation_final_01",
        summary="Proceed with a limited launch after conversion evidence is confirmed.",
        claims=(fact, inference),
        challenges=(challenge,),
        recommended_action="Collect conversion evidence and run a limited launch.",
        required_human_action="An authorized approver must decide on this exact digest.",
        source_candidate_ids=candidate_ids,
        policy_version="council-v1",
    )
    names = (
        "market_intelligence",
        "financial_analysis",
        "product_technical",
        "risk_compliance",
        "decision_synthesis",
        "red_team",
        "final_synthesis",
    )
    events = tuple(
        CouncilExecutionEvent(
            ordinal=index,
            specialist_id=name,
            display_name=name.replace("_", " ").title(),
            status=CouncilExecutionStatus.COMPLETED,
        )
        for index, name in enumerate(names, start=1)
    )
    return CouncilExecutionResult(
        candidates=candidates,
        challenges=(challenge,),
        recommendation=recommendation,
        events=events,
    )


def test_projection_exposes_real_graph_claim_types_and_human_boundary() -> None:
    projection = build_council_projection(
        run_id="council_run_01",
        objective="Decide whether the product is ready for a limited launch.",
        result=_result(),
    )

    assert projection.state == "human_approval_required"
    assert [node.specialist_id for node in projection.nodes] == [
        "market_intelligence",
        "financial_analysis",
        "product_technical",
        "risk_compliance",
        "decision_synthesis",
        "red_team",
        "final_synthesis",
    ]
    assert all(node.status == "complete" for node in projection.nodes)
    assert len(projection.handoffs) == 6
    assert projection.evidence_claims[0].classification == "confirmed_fact"
    assert projection.inference_claims[0].classification == "model_inference"
    assert projection.required_human_action.startswith("An authorized approver")
    assert projection.recommendation_digest == _result().recommendation.semantic_digest


def test_projection_contains_no_prompt_tool_payload_or_private_context() -> None:
    projection = build_council_projection(
        run_id="council_run_01",
        objective="Decide whether the product is ready for a limited launch.",
        result=_result(),
    )
    payload = projection.model_dump_json()

    assert "founder-01" not in payload
    assert "google.com" not in payload
    assert "tool_payload" not in payload
    assert "prompt" not in payload
    assert "UNTRUSTED" not in payload


def test_running_projection_keeps_waiting_running_and_complete_distinct() -> None:
    events = (
        CouncilExecutionEvent(
            ordinal=1,
            specialist_id="market_intelligence",
            display_name="Market Intelligence",
            status=CouncilExecutionStatus.COMPLETED,
        ),
        CouncilExecutionEvent(
            ordinal=2,
            specialist_id="financial_analysis",
            display_name="Financial Analysis",
            status=CouncilExecutionStatus.STARTED,
        ),
    )

    projection = build_council_projection(
        run_id="council_run_01",
        objective="Assess launch readiness.",
        events=events,
    )
    statuses = {node.specialist_id: node.status for node in projection.nodes}

    assert projection.state == "running"
    assert statuses["market_intelligence"] == "complete"
    assert statuses["financial_analysis"] == "running"
    assert statuses["red_team"] == "waiting"


def test_firestore_latest_restores_strict_projection_from_json_arrays() -> None:
    organization_id = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
    workspace_id = "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
    run_id = "council_run_01"
    projection = build_council_projection(
        run_id=run_id,
        objective="Decide whether the product is ready for a limited launch.",
        result=_result(),
    )
    latest_path = (
        "decisionos_organizations",
        organization_id,
        "workspaces",
        workspace_id,
        "council_state",
        "latest",
    )
    run_path = (
        "decisionos_organizations",
        organization_id,
        "workspaces",
        workspace_id,
        "council_runs",
        run_id,
    )

    class Snapshot:
        def __init__(self, payload):
            self.exists = payload is not None
            self._payload = payload

        def to_dict(self):
            return self._payload

    class Reference:
        def __init__(self, client, path):
            self._client = client
            self._path = path

        def collection(self, name):
            return Collection(self._client, (*self._path, name))

        def get(self):
            return Snapshot(self._client.rows.get(self._path))

    class Collection:
        def __init__(self, client, path):
            self._client = client
            self._path = path

        def document(self, identifier):
            return Reference(self._client, (*self._path, identifier))

    class Client:
        def __init__(self):
            self.rows = {
                latest_path: {"run_id": run_id},
                run_path: {"projection": projection.model_dump(mode="json")},
            }

        def collection(self, name):
            return Collection(self, (name,))

    context = DecisionOSContext(
        principal=DecisionOSPrincipal(
            uid="owner-01",
            email_verified=True,
            provider_ids=("google.com",),
        ),
        membership=OrganizationMembership(
            organization_id=organization_id,
            uid="owner-01",
            role=DecisionOSRole.OWNER,
            status=MembershipStatus.ACTIVE,
        ),
    )

    restored = FirestoreCouncilRunStore(Client()).load_latest(context, workspace_id)

    assert restored == projection


def test_demo_evidence_records_are_stable_explicit_and_safe() -> None:
    organization_id = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
    workspace_id = "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"

    first = build_demo_evidence_records(organization_id, workspace_id)
    second = build_demo_evidence_records(organization_id, workspace_id)

    assert first == second
    assert len(first) == 5
    assert all(item.evidence_id.startswith("evidence_demo_") for item in first)
    assert all(item.title.startswith("Synthetic demo · ") for item in first)
    assert all(item.sanitized_text.startswith("Synthetic demo evidence. ") for item in first)
    assert all("@" not in item.sanitized_text for item in first)


def test_firestore_demo_evidence_is_persisted_as_real_workspace_records() -> None:
    organization_id = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
    workspace_id = "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"

    class Reference:
        def __init__(self, client, path):
            self._client = client
            self._path = path

        def collection(self, name):
            return Collection(self._client, (*self._path, name))

        def set(self, payload):
            self._client.rows[self._path] = payload

    class Collection:
        def __init__(self, client, path):
            self._client = client
            self._path = path

        def document(self, identifier):
            return Reference(self._client, (*self._path, identifier))

    class Client:
        def __init__(self):
            self.rows = {}

        def collection(self, name):
            return Collection(self, (name,))

    client = Client()
    registry = FirestoreCouncilEvidenceRegistry(client)
    records = registry.seed_demo_evidence(organization_id, workspace_id)

    assert len(records) == 5
    assert len(client.rows) == 5
    assert all(row["status"] == "ready" for row in client.rows.values())
    assert all(row["organization_id"] == organization_id for row in client.rows.values())
