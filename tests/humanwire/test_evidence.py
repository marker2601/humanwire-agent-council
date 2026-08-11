from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from humanwire.domain import (
    Channel,
    EvidenceItem,
    EvidenceStatus,
    EvidenceType,
    EvidenceVisibility,
)
from humanwire.evidence import (
    EvidenceDraft,
    FeatherlessEvidenceExtractor,
    ModelEvidenceDraft,
    RuleBasedEvidenceExtractor,
    confirm_drafts,
    private_blocker_count,
    shareable_evidence,
)
from humanwire.redaction import redact_sensitive

MANDATE_ID = UUID("00000000-0000-0000-0000-000000000001")
ASSIGNMENT_ID = UUID("00000000-0000-0000-0000-000000000002")
NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


class StaticJsonClient:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> dict:
        self.calls.append((system, user))
        return self.response


@pytest.fixture
def shareable_answer() -> dict[str, object]:
    return {
        "answer": "We must finish the coverage plan.",
        "question": "What constraint applies?",
        "mandate_id": MANDATE_ID,
        "assignment_id": ASSIGNMENT_ID,
        "stakeholder_id": "vp-people",
        "source_message_id": "msg-42",
        "channel": Channel.EMAIL,
        "received_at": NOW,
        "visibility": EvidenceVisibility.SHAREABLE,
    }


@pytest.fixture
def private_answer(shareable_answer) -> dict[str, object]:
    return shareable_answer | {
        "answer": "I cannot discuss this openly; call me at 555-123-4567.",
        "visibility": EvidenceVisibility.PRIVATE,
    }


@pytest.fixture
def extractor() -> FeatherlessEvidenceExtractor:
    client = StaticJsonClient(
        {
            "evidence": [
                {
                    "evidence_type": "constraint",
                    "statement": "We must finish the coverage plan.",
                    "related_decision": "Approve coverage",
                    "deadline": None,
                    "resource": None,
                }
            ]
        }
    )
    return FeatherlessEvidenceExtractor(client, RuleBasedEvidenceExtractor())


def make_evidence(
    *,
    visibility: EvidenceVisibility = EvidenceVisibility.SHAREABLE,
    stakeholder_id: str = "vp-people",
    evidence_type: EvidenceType = EvidenceType.FACT,
    statement: str = "Coverage needs two people.",
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=UUID("00000000-0000-0000-0000-000000000003"),
        mandate_id=MANDATE_ID,
        assignment_id=ASSIGNMENT_ID,
        stakeholder_id=stakeholder_id,
        evidence_type=evidence_type,
        statement=statement,
        visibility=visibility,
        status=EvidenceStatus.ASSERTED,
        source_message_id="msg-42",
        channel=Channel.EMAIL,
        created_at=NOW,
    )


def test_extractor_preserves_source_provenance(extractor, shareable_answer) -> None:
    item = confirm_drafts(extractor.extract(**shareable_answer))[0]

    assert item.source_message_id == "msg-42"
    assert item.channel is Channel.EMAIL
    assert item.stakeholder_id == "vp-people"
    assert item.status is EvidenceStatus.ASSERTED


def test_private_evidence_is_excluded_from_shared_views(extractor, private_answer) -> None:
    drafts = extractor.extract(**private_answer)
    items = confirm_drafts(drafts)

    assert items[0].visibility is EvidenceVisibility.PRIVATE
    assert shareable_evidence(items) == []


def test_anonymous_evidence_drops_identity_in_shared_projection() -> None:
    item = make_evidence(visibility=EvidenceVisibility.ANONYMOUS, stakeholder_id="vp-people")

    projection = shareable_evidence([item])[0]

    assert projection.stakeholder_id is None
    assert projection.statement == item.statement
    with pytest.raises(ValidationError):
        projection.stakeholder_id = "vp-people"  # type: ignore[misc]


@pytest.mark.parametrize("evidence_type", list(EvidenceType))
def test_shareable_projection_retains_every_evidence_type(evidence_type: EvidenceType) -> None:
    item = make_evidence(evidence_type=evidence_type)

    projection = shareable_evidence([item])[0]

    assert projection.evidence_type is evidence_type
    assert projection.stakeholder_id == "vp-people"
    assert not hasattr(projection, "source_message_id")


def test_private_answer_never_reaches_model_client(private_answer) -> None:
    client = StaticJsonClient({"evidence": []})
    extractor = FeatherlessEvidenceExtractor(client, RuleBasedEvidenceExtractor())

    drafts = extractor.extract(**private_answer)

    assert client.calls == []
    assert drafts[0].statement == "I cannot discuss this openly; call me at [REDACTED]."


def test_model_output_cannot_set_provenance_or_coerce_fields(shareable_answer) -> None:
    client = StaticJsonClient(
        {
            "evidence": [
                {
                    "evidence_type": "fact",
                    "statement": 7,
                    "related_decision": None,
                    "deadline": None,
                    "resource": None,
                    "stakeholder_id": "attacker",
                }
            ]
        }
    )
    extractor = FeatherlessEvidenceExtractor(client, RuleBasedEvidenceExtractor())

    drafts = extractor.extract(**shareable_answer)

    assert drafts[0].stakeholder_id == "vp-people"
    assert drafts[0].evidence_type is EvidenceType.CONSTRAINT
    assert drafts[0].statement == "We must finish the coverage plan."


@pytest.mark.parametrize(
    ("answer", "evidence_type"),
    [
        ("The service will launch Friday.", EvidenceType.COMMITMENT),
        ("I can deliver the report by Friday.", EvidenceType.COMMITMENT),
        ("I am available on 2026-08-14.", EvidenceType.AVAILABILITY),
        ("We must retain the current schedule.", EvidenceType.CONSTRAINT),
        ("The current schedule covers two shifts.", EvidenceType.FACT),
    ],
)
def test_rule_fallback_classifies_short_sentences(answer: str, evidence_type: EvidenceType) -> None:
    drafts = RuleBasedEvidenceExtractor().extract(
        answer=answer,
        question="What can you share?",
        mandate_id=MANDATE_ID,
        assignment_id=ASSIGNMENT_ID,
        stakeholder_id="vp-people",
        source_message_id="msg-42",
        channel=Channel.EMAIL,
        received_at=NOW,
        visibility=EvidenceVisibility.SHAREABLE,
    )

    assert [draft.evidence_type for draft in drafts] == [evidence_type]


def test_private_blocker_count_exposes_only_a_count() -> None:
    items = [
        make_evidence(visibility=EvidenceVisibility.PRIVATE, evidence_type=EvidenceType.CONSTRAINT),
        make_evidence(visibility=EvidenceVisibility.PRIVATE, evidence_type=EvidenceType.FACT),
        make_evidence(visibility=EvidenceVisibility.SHAREABLE, evidence_type=EvidenceType.CONSTRAINT),
    ]

    assert private_blocker_count(items) == 1


def test_model_evidence_draft_forbids_unexpected_fields() -> None:
    with pytest.raises(ValidationError):
        ModelEvidenceDraft.model_validate(
            {
                "evidence_type": "fact",
                "statement": "Coverage is complete.",
                "related_decision": None,
                "deadline": None,
                "resource": None,
                "mandate_id": str(MANDATE_ID),
            }
        )


def test_confirmation_redacts_values_when_a_draft_bypasses_an_extractor() -> None:
    draft = EvidenceDraft(
        evidence_type=EvidenceType.FACT,
        statement="My OTP is 449102.",
        mandate_id=MANDATE_ID,
        assignment_id=ASSIGNMENT_ID,
        stakeholder_id="vp-people",
        source_message_id="msg-42",
        channel=Channel.EMAIL,
        created_at=NOW,
        visibility=EvidenceVisibility.PRIVATE,
    )

    item = confirm_drafts([draft])[0]

    assert "449102" not in item.statement
    assert item.statement == "My OTP [REDACTED]."


def test_redaction_removes_credentials_and_direct_contact_values() -> None:
    text = (
        "OTP 449102 recovery code AB12-CD34 token Bearer abc.def.ghi "
        "email me at person@example.test or call 555-123-4567 or message "
        "@private_handle https://t.me/private_handle"
    )

    result = redact_sensitive(text)

    for secret in (
        "449102",
        "AB12-CD34",
        "abc.def.ghi",
        "person@example.test",
        "555-123-4567",
        "@private_handle",
        "t.me/private_handle",
    ):
        assert secret not in result
    assert result.count("[REDACTED]") == 7
