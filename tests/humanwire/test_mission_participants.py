from __future__ import annotations

from datetime import UTC, datetime

from humanwire.decisionos_models import (
    DecisionOSContext,
    DecisionOSPrincipal,
    DecisionOSRole,
    DecisionWorkspace,
    MembershipStatus,
    OrganizationMembership,
    WorkspacePlaybook,
)
from humanwire.mission_models import MissionActorType, MissionMode, MissionRequest
from humanwire.mission_participants import MissionParticipantResolver
from humanwire.organization_models import (
    OrganizationGraph,
    OrganizationSubject,
    OrganizationSubjectKind,
    OrganizationUnit,
    SubjectLifecycle,
)

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
ORG = "org_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
WORKSPACE = "wrk_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
UNIT = "unit_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
ACTIVE = "sub_01HQ7XK9WPH4Y8ZQK3R2N1M6AA"
DIRECTORY_ONLY = "sub_01HQ7XK9WPH4Y8ZQK3R2N1M6AB"
SUSPENDED = "sub_01HQ7XK9WPH4Y8ZQK3R2N1M6AC"


class FixedGraphRepository:
    def __init__(self, graph: OrganizationGraph) -> None:
        self.graph = graph

    def load_graph(self, _context) -> OrganizationGraph:
        return self.graph


def context() -> DecisionOSContext:
    principal = DecisionOSPrincipal(
        uid="firebase-owner-01",
        email_verified=True,
        provider_ids=("google.com",),
    )
    return DecisionOSContext(
        principal=principal,
        membership=OrganizationMembership(
            organization_id=ORG,
            uid=principal.uid,
            role=DecisionOSRole.OWNER,
            status=MembershipStatus.ACTIVE,
        ),
    )


def workspace() -> DecisionWorkspace:
    return DecisionWorkspace(
        workspace_id=WORKSPACE,
        organization_id=ORG,
        name="Launch decisions",
        playbook=WorkspacePlaybook.LAUNCH_DECISION,
        created_by_uid="firebase-owner-01",
    )


def request(mode: MissionMode) -> MissionRequest:
    return MissionRequest(
        mode=mode,
        objective="Approve the launch decision with current evidence.",
        urgency="standard",
        include_conflict=True,
    )


def person(
    subject_id: str,
    lifecycle: SubjectLifecycle,
    display_name: str,
    *,
    member_uid: str | None = None,
) -> OrganizationSubject:
    return OrganizationSubject(
        subject_id=subject_id,
        organization_id=ORG,
        kind=OrganizationSubjectKind.HUMAN,
        lifecycle=lifecycle,
        display_name=display_name,
        source_identity=f"directory/{subject_id}",
        member_uid=member_uid,
        specialist_key=None,
        unit_id=UNIT,
        title="Decision owner",
    )


def graph() -> OrganizationGraph:
    return OrganizationGraph(
        organization_id=ORG,
        version=1,
        subjects=(
            person(
                ACTIVE,
                SubjectLifecycle.ACTIVE,
                "Avery Morgan",
                member_uid="firebase-avery-01",
            ),
            person(DIRECTORY_ONLY, SubjectLifecycle.DIRECTORY_ONLY, "Riley Chen"),
            person(SUSPENDED, SubjectLifecycle.SUSPENDED, "Jordan Kim"),
        ),
        units=(
            OrganizationUnit(
                unit_id=UNIT,
                organization_id=ORG,
                name="Launch team",
                parent_unit_id=None,
                leader_subject_id=ACTIVE,
            ),
        ),
        edges=(),
        authority_assignments=(),
        created_at=NOW,
    )


def resolver() -> MissionParticipantResolver:
    return MissionParticipantResolver(graph_repository=FixedGraphRepository(graph()))


def test_demo_resolution_returns_only_ai_specialists_and_demo_stakeholders() -> None:
    participants = resolver().resolve(context(), workspace(), request(MissionMode.DEMO_RUN))

    assert {item.actor_type for item in participants} == {
        MissionActorType.AI_SPECIALIST,
        MissionActorType.DEMO_STAKEHOLDER,
    }
    assert all(item.subject_id is None for item in participants)


def test_connected_resolution_keeps_ai_agents_and_only_active_humans() -> None:
    participants = resolver().resolve(
        context(),
        workspace(),
        request(MissionMode.CONNECTED_ORGANIZATION),
    )

    human_subjects = {
        item.subject_id
        for item in participants
        if item.actor_type is MissionActorType.HUMAN_MEMBER
    }
    assert MissionActorType.AI_SPECIALIST in {item.actor_type for item in participants}
    assert human_subjects == {ACTIVE}
    assert DIRECTORY_ONLY not in human_subjects
    assert SUSPENDED not in human_subjects


def test_connected_resolution_never_exposes_member_uid_or_source_identity() -> None:
    participants = resolver().resolve(
        context(),
        workspace(),
        request(MissionMode.CONNECTED_ORGANIZATION),
    )
    serialized = " ".join(item.model_dump_json() for item in participants)

    assert "firebase-avery-01" not in serialized
    assert "directory/" not in serialized
