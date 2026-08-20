from __future__ import annotations

import pytest

from humanwire.council_tools import (
    CouncilEvidenceRecord,
    CouncilEvidenceStatus,
    CouncilPriorDecision,
    CouncilToolContext,
    CouncilToolDenied,
    build_council_tools,
    list_evidence,
    read_evidence_excerpt,
    read_prior_decision,
)
from humanwire.decisionos_models import (
    DecisionOSContext,
    DecisionOSPrincipal,
    DecisionOSRole,
    MembershipStatus,
    OrganizationMembership,
)

ORG_ID = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
OTHER_ORG_ID = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AB"
WORKSPACE_ID = "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
OTHER_WORKSPACE_ID = "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AB"
DIGEST = "a" * 64


def _decisionos_context() -> DecisionOSContext:
    return DecisionOSContext(
        principal=DecisionOSPrincipal(
            uid="founder-01",
            email_verified=True,
            provider_ids=("google.com",),
        ),
        membership=OrganizationMembership(
            organization_id=ORG_ID,
            uid="founder-01",
            role=DecisionOSRole.DECISION_OWNER,
            status=MembershipStatus.ACTIVE,
        ),
    )


def _evidence(**overrides: object) -> CouncilEvidenceRecord:
    values: dict[str, object] = {
        "organization_id": ORG_ID,
        "workspace_id": WORKSPACE_ID,
        "evidence_id": "evidence_market_01",
        "title": "Pilot interview summary",
        "sanitized_text": "Three pilots completed the bounded evaluation period.",
        "source_digest": DIGEST,
        "extraction_version": "extract-v1",
        "status": CouncilEvidenceStatus.READY,
    }
    values.update(overrides)
    return CouncilEvidenceRecord.model_validate(values)


def _prior(**overrides: object) -> CouncilPriorDecision:
    values: dict[str, object] = {
        "organization_id": ORG_ID,
        "workspace_id": WORKSPACE_ID,
        "decision_id": "decision_prior_01",
        "summary": "The prior team approved a limited pilot after evidence review.",
        "semantic_digest": "b" * 64,
    }
    values.update(overrides)
    return CouncilPriorDecision.model_validate(values)


class FakeRegistry:
    def __init__(self) -> None:
        self.evidence: dict[str, CouncilEvidenceRecord] = {
            "evidence_market_01": _evidence()
        }
        self.prior: dict[str, CouncilPriorDecision] = {
            "decision_prior_01": _prior()
        }
        self.failure: Exception | None = None

    def list_evidence(
        self, organization_id: str, workspace_id: str
    ) -> tuple[CouncilEvidenceRecord, ...]:
        if self.failure is not None:
            raise self.failure
        return tuple(self.evidence.values())

    def load_evidence(
        self,
        organization_id: str,
        workspace_id: str,
        evidence_id: str,
    ) -> CouncilEvidenceRecord | None:
        if self.failure is not None:
            raise self.failure
        return self.evidence.get(evidence_id)

    def load_prior_decision(
        self,
        organization_id: str,
        workspace_id: str,
        decision_id: str,
    ) -> CouncilPriorDecision | None:
        if self.failure is not None:
            raise self.failure
        return self.prior.get(decision_id)


@pytest.fixture
def registry() -> FakeRegistry:
    return FakeRegistry()


@pytest.fixture
def tool_context(registry: FakeRegistry) -> CouncilToolContext:
    return CouncilToolContext(
        context=_decisionos_context(),
        workspace_id=WORKSPACE_ID,
        registry=registry,
    )


def test_tool_cannot_cross_organization(
    tool_context: CouncilToolContext, registry: FakeRegistry
) -> None:
    registry.evidence["evidence_other_01"] = _evidence(
        organization_id=OTHER_ORG_ID,
        evidence_id="evidence_other_01",
    )

    with pytest.raises(CouncilToolDenied, match="^evidence_unavailable$"):
        read_evidence_excerpt(tool_context, "evidence_other_01", 0, 100)


def test_tool_cannot_cross_workspace(
    tool_context: CouncilToolContext, registry: FakeRegistry
) -> None:
    registry.evidence["evidence_other_02"] = _evidence(
        workspace_id=OTHER_WORKSPACE_ID,
        evidence_id="evidence_other_02",
    )

    with pytest.raises(CouncilToolDenied, match="^evidence_unavailable$"):
        read_evidence_excerpt(tool_context, "evidence_other_02", 0, 100)


def test_output_is_bounded_and_cited(tool_context: CouncilToolContext) -> None:
    result = read_evidence_excerpt(tool_context, "evidence_market_01", 0, 40)

    assert len(result.text) <= 40
    assert result.evidence_id == "evidence_market_01"
    assert result.source_digest == DIGEST
    assert result.extraction_version == "extract-v1"
    assert result.start_offset == 0
    assert result.end_offset == len(result.text)


@pytest.mark.parametrize(
    ("evidence_id", "start", "length"),
    [
        ("../evidence_market_01", 0, 100),
        ("evidence_market_01", -1, 100),
        ("evidence_market_01", 0, 0),
        ("evidence_market_01", 0, 501),
        ("evidence_market_01", True, 100),
        ("evidence_market_01", 0, "100"),
    ],
)
def test_excerpt_rejects_traversal_and_unbounded_spans(
    tool_context: CouncilToolContext,
    evidence_id: object,
    start: object,
    length: object,
) -> None:
    with pytest.raises(CouncilToolDenied, match="^evidence_unavailable$"):
        read_evidence_excerpt(tool_context, evidence_id, start, length)


@pytest.mark.parametrize(
    "status",
    [
        CouncilEvidenceStatus.QUARANTINED,
        CouncilEvidenceStatus.DELETED,
        CouncilEvidenceStatus.STALE,
    ],
)
def test_nonready_evidence_is_never_returned(
    tool_context: CouncilToolContext,
    registry: FakeRegistry,
    status: CouncilEvidenceStatus,
) -> None:
    registry.evidence["evidence_market_01"] = _evidence(status=status)

    assert list_evidence(tool_context).items == ()
    with pytest.raises(CouncilToolDenied, match="^evidence_unavailable$"):
        read_evidence_excerpt(tool_context, "evidence_market_01", 0, 100)


def test_catalog_is_canonical_and_minimized(tool_context: CouncilToolContext) -> None:
    catalog = list_evidence(tool_context)

    assert len(catalog.items) == 1
    item = catalog.items[0]
    assert item.model_dump() == {
        "evidence_id": "evidence_market_01",
        "title": "Pilot interview summary",
        "source_digest": DIGEST,
        "extraction_version": "extract-v1",
    }
    assert "sanitized_text" not in repr(item)


def test_prior_decision_is_tenant_bound_and_minimized(
    tool_context: CouncilToolContext, registry: FakeRegistry
) -> None:
    result = read_prior_decision(tool_context, "decision_prior_01")

    assert result.model_dump() == {
        "decision_id": "decision_prior_01",
        "summary": "The prior team approved a limited pilot after evidence review.",
        "semantic_digest": "b" * 64,
    }
    registry.prior["decision_prior_01"] = _prior(organization_id=OTHER_ORG_ID)
    with pytest.raises(CouncilToolDenied, match="^decision_unavailable$"):
        read_prior_decision(tool_context, "decision_prior_01")


def test_registry_failures_have_fixed_private_exception_boundary(
    tool_context: CouncilToolContext, registry: FakeRegistry
) -> None:
    registry.failure = RuntimeError(
        "PRIVATE-PROVIDER-TRACE C:\\private\\service-account.json"
    )

    with pytest.raises(CouncilToolDenied, match="^evidence_unavailable$") as captured:
        list_evidence(tool_context)

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "PRIVATE" not in repr(captured.value)
    assert "service-account" not in repr(captured.value)


def test_private_source_content_is_rejected_not_projected(
    tool_context: CouncilToolContext, registry: FakeRegistry
) -> None:
    registry.evidence["evidence_market_01"] = _evidence().model_copy(
        update={"sanitized_text": "Contact founder@example.invalid for access."}
    )

    with pytest.raises(CouncilToolDenied, match="^evidence_unavailable$") as captured:
        read_evidence_excerpt(tool_context, "evidence_market_01", 0, 100)

    assert "founder@example.invalid" not in repr(captured.value)


def test_build_tools_exposes_only_three_readonly_functions(
    tool_context: CouncilToolContext,
) -> None:
    tools = build_council_tools(tool_context)

    assert tuple(tool.name for tool in tools) == (
        "list_evidence",
        "read_evidence_excerpt",
        "read_prior_decision",
    )
    assert all("registry" not in repr(tool) for tool in tools)


def test_bound_tools_return_fixed_errors_for_model_generated_invalid_calls(
    tool_context: CouncilToolContext,
    registry: FakeRegistry,
) -> None:
    tools = {tool.name: tool for tool in build_council_tools(tool_context)}

    assert tools["read_evidence_excerpt"].func(
        evidence_id="evidence_market_01", start=0, length=1_000
    ) == {"error": "evidence_unavailable"}
    assert tools["read_prior_decision"].func(decision_id="not-a-decision") == {
        "error": "decision_unavailable"
    }
    registry.failure = RuntimeError("PRIVATE-provider-message")
    assert tools["list_evidence"].func() == {"error": "evidence_unavailable"}
