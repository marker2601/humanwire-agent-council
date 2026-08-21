"""Resolve safe mission actors without exposing directory identities or routes."""

from __future__ import annotations

from humanwire.council_registry import specialist_registry
from humanwire.decisionos_models import DecisionOSContext, DecisionWorkspace
from humanwire.decisionos_store import DecisionOSPermission, require_permission
from humanwire.mission_models import (
    MissionActorType,
    MissionMode,
    MissionParticipant,
    MissionRequest,
)
from humanwire.organization_canonical import exact_canonical_model
from humanwire.organization_models import (
    OrganizationGraph,
    OrganizationSubjectKind,
    SubjectLifecycle,
)
from humanwire.organization_store import OrganizationGraphRepository
from humanwire.studio_models import product_catalog


class MissionParticipantUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("mission_participant_unavailable")


def _participant_id(prefix: str, raw: str) -> str:
    value = raw.replace("_", "-").casefold()
    return f"{prefix}-{value}"


class MissionParticipantResolver:
    def __init__(self, *, graph_repository: OrganizationGraphRepository) -> None:
        self._graph_repository = graph_repository

    @staticmethod
    def _specialists(workspace: DecisionWorkspace) -> tuple[MissionParticipant, ...]:
        return tuple(
            MissionParticipant(
                participant_id=_participant_id("ai", item.specialist_id),
                actor_type=MissionActorType.AI_SPECIALIST,
                display_name=item.display_name,
                role=f"{item.display_name} AI",
                subject_id=None,
                response_required=False,
            )
            for item in specialist_registry(workspace.playbook)
        )

    @staticmethod
    def _demo_stakeholders() -> tuple[MissionParticipant, ...]:
        return tuple(
            MissionParticipant(
                participant_id=_participant_id("demo", item.persona_id),
                actor_type=MissionActorType.DEMO_STAKEHOLDER,
                display_name=item.display_name,
                role=item.role,
                subject_id=None,
                response_required=True,
            )
            for item in product_catalog().stakeholders
        )

    def resolve(
        self,
        context: DecisionOSContext,
        workspace: DecisionWorkspace,
        request: MissionRequest,
    ) -> tuple[MissionParticipant, ...]:
        if (
            type(context) is not DecisionOSContext
            or type(workspace) is not DecisionWorkspace
            or type(request) is not MissionRequest
            or workspace.organization_id != context.organization_id
        ):
            raise MissionParticipantUnavailable()
        require_permission(context, DecisionOSPermission.READ_WORKSPACE)
        specialists = self._specialists(workspace)
        if request.mode is MissionMode.DEMO_RUN:
            return specialists + self._demo_stakeholders()
        failed = False
        raw_graph = None
        try:
            raw_graph = self._graph_repository.load_graph(context)
        except Exception:  # noqa: BLE001 - repository details stay private
            failed = True
        graph = exact_canonical_model(raw_graph, OrganizationGraph)
        if failed or graph is None or graph.organization_id != context.organization_id:
            raise MissionParticipantUnavailable()
        humans = tuple(
            MissionParticipant(
                participant_id=_participant_id(
                    "human",
                    item.subject_id.removeprefix("sub_"),
                ),
                actor_type=MissionActorType.HUMAN_MEMBER,
                display_name=item.display_name,
                role=item.title or "Organization participant",
                subject_id=item.subject_id,
                response_required=True,
            )
            for item in sorted(graph.subjects, key=lambda subject: subject.subject_id)
            if item.kind is OrganizationSubjectKind.HUMAN
            and item.lifecycle is SubjectLifecycle.ACTIVE
            and item.member_uid is not None
        )
        return specialists + humans


__all__ = ["MissionParticipantResolver", "MissionParticipantUnavailable"]
