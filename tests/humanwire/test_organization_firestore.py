from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from humanwire.decisionos_models import DecisionOSPrincipal
from humanwire.decisionos_store import FirestoreDecisionOSRepository
from humanwire.organization_models import (
    ImportDraft,
    OrganizationGraphCandidate,
    OrganizationSubject,
    OrganizationSubjectKind,
    SourceRecord,
    SourceSnapshot,
    SubjectLifecycle,
)
from humanwire.organization_store import FirestoreOrganizationGraphRepository

ORG = "org_01ARZ3NDEKTSV4RRFFQ69G5FAV"
SUBJECT = "sub_01ARZ3NDEKTSV4RRFFQ69G5FAV"
NOW = datetime(2026, 8, 18, 15, 0, tzinfo=UTC)
DIGEST = "1" * 64


class Identifiers:
    def organization_id(self) -> str:
        return ORG

    def workspace_id(self) -> str:
        return "wrk_01ARZ3NDEKTSV4RRFFQ69G5FAV"

    def invitation_id(self) -> str:
        return "inv_01ARZ3NDEKTSV4RRFFQ69G5FAV"

    def invitation_token(self) -> str:
        return "opaque-invitation-token-123456"


def draft() -> ImportDraft:
    snapshot = SourceSnapshot(
        snapshot_id="snap_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        organization_id=ORG,
        source_kind="csv",
        captured_at=NOW,
        records=(
            SourceRecord(
                record_id="rec_01ARZ3NDEKTSV4RRFFQ69G5FAV",
                source_ordinal=1,
                source_identity="directory/alice",
                fields=(("email", "private-alice@example.invalid"),),
            ),
        ),
        semantic_digest=DIGEST,
    )
    return ImportDraft(
        import_id="imp_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        organization_id=ORG,
        source_snapshot=snapshot,
        candidate=OrganizationGraphCandidate(
            organization_id=ORG,
            source_snapshot_id=snapshot.snapshot_id,
            subjects=(
                OrganizationSubject(
                    subject_id=SUBJECT,
                    organization_id=ORG,
                    kind=OrganizationSubjectKind.HUMAN,
                    lifecycle=SubjectLifecycle.DRAFT_IMPORTED,
                    display_name="Alice Example",
                    source_identity="directory/alice",
                ),
            ),
        ),
        base_graph_version=0,
        semantic_digest=DIGEST,
        created_at=NOW,
    )


@pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="requires explicit Firestore emulator",
)
@pytest.mark.firestore_emulator
def test_firestore_emulator_commits_one_tenant_bound_version_transactionally() -> None:
    from google.cloud import firestore

    suffix = uuid4().hex
    collection = f"humanwire_test_organizations_{suffix}"
    audit_collection = f"humanwire_test_org_audit_{suffix}"
    client = firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT", "humanwire-test"))
    decisionos = FirestoreDecisionOSRepository(
        client,
        clock=lambda: NOW,
        identifiers=Identifiers(),
        organization_collection=collection,
        invitation_index_collection=f"humanwire_test_invites_{suffix}",
    )
    repository = FirestoreOrganizationGraphRepository(
        client,
        clock=lambda: NOW,
        organization_collection=collection,
        audit_collection=audit_collection,
    )
    owner = DecisionOSPrincipal(
        uid="firebase-owner-01",
        email_verified=True,
        provider_ids=("google.com",),
    )
    organization = decisionos.create_organization(owner, "Northstar Labs")
    context = decisionos.load_context(owner, organization.organization_id)
    saved = repository.save_import_draft(context, draft())

    receipt = repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    )

    assert receipt.graph_version == 1
    assert repository.load_graph(context).subjects[0].lifecycle is SubjectLifecycle.DIRECTORY_ONLY
    assert repository.commit_graph(
        context,
        draft_id=saved.import_id,
        reviewed_digest=saved.semantic_digest,
    ) == receipt
    assert repository.list_audit(context)[0].receipt == receipt
