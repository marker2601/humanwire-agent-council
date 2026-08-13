"""Create the initial HumanWire schema.

Revision ID: 0001_humanwire_schema
Revises:
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_humanwire_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hw_mandates",
        sa.Column("mandate_id", sa.String(length=36), nullable=False),
        sa.Column("token", sa.String(length=32), nullable=False),
        sa.Column("initiator_id", sa.String(length=128), nullable=False),
        sa.Column("origin_channel", sa.String(length=32), nullable=False),
        sa.Column("origin_conversation_id", sa.String(length=255), nullable=False),
        sa.Column("origin_message_id", sa.String(length=255), nullable=False),
        sa.Column("redacted_request", sa.Text(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("next_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("mandate_id"),
    )
    op.create_index("ix_hw_mandates_created_at", "hw_mandates", ["created_at"])
    op.create_index("ix_hw_mandates_expires_at", "hw_mandates", ["expires_at"])
    op.create_index(
        "ix_hw_mandates_idempotency_key", "hw_mandates", ["idempotency_key"], unique=True
    )
    op.create_index("ix_hw_mandates_initiator_id", "hw_mandates", ["initiator_id"])
    op.create_index("ix_hw_mandates_state", "hw_mandates", ["state"])
    op.create_index("ix_hw_mandates_token", "hw_mandates", ["token"], unique=True)

    op.create_table(
        "hw_runtime_status",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "hw_assignments",
        sa.Column("assignment_id", sa.String(length=36), nullable=False),
        sa.Column("mandate_id", sa.String(length=36), nullable=False),
        sa.Column("person_id", sa.String(length=128), nullable=False),
        sa.Column("department", sa.String(length=128), nullable=False),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column(
            "engagement_type",
            sa.String(length=40),
            server_default="structured_interview",
            nullable=False,
        ),
        sa.Column(
            "response_required",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("route_ids", sa.JSON(), nullable=False),
        sa.Column("active_route_index", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("interview_id", sa.String(length=36), nullable=True),
        sa.Column("first_contact_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["mandate_id"], ["hw_mandates.mandate_id"]),
        sa.PrimaryKeyConstraint("assignment_id"),
    )
    op.create_index("ix_hw_assignments_interview_id", "hw_assignments", ["interview_id"])
    op.create_index("ix_hw_assignments_mandate_id", "hw_assignments", ["mandate_id"])
    op.create_index(
        "ix_hw_assignments_next_action_at", "hw_assignments", ["next_action_at"]
    )
    op.create_index("ix_hw_assignments_person_id", "hw_assignments", ["person_id"])
    op.create_index("ix_hw_assignments_state", "hw_assignments", ["state"])

    op.create_table(
        "hw_events",
        sa.Column("event_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("mandate_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("assignment_id", sa.String(length=36), nullable=True),
        sa.Column("person_id", sa.String(length=128), nullable=True),
        sa.Column("department", sa.String(length=128), nullable=True),
        sa.Column("direction", sa.String(length=32), nullable=True),
        sa.Column("channel", sa.String(length=32), nullable=True),
        sa.Column("previous_state", sa.String(length=40), nullable=True),
        sa.Column("new_state", sa.String(length=40), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["mandate_id"], ["hw_mandates.mandate_id"]),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_hw_events_assignment_id", "hw_events", ["assignment_id"])
    op.create_index("ix_hw_events_created_at", "hw_events", ["created_at"])
    op.create_index(
        "ix_hw_events_idempotency_key", "hw_events", ["idempotency_key"], unique=True
    )
    op.create_index("ix_hw_events_mandate_id", "hw_events", ["mandate_id"])

    op.create_table(
        "hw_issues",
        sa.Column("issue_id", sa.String(length=36), nullable=False),
        sa.Column("mandate_id", sa.String(length=36), nullable=False),
        sa.Column("issue_type", sa.String(length=40), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("stakeholder_ids", sa.JSON(), nullable=False),
        sa.Column("related_decision", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("blocking", sa.Boolean(), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["mandate_id"], ["hw_mandates.mandate_id"]),
        sa.PrimaryKeyConstraint("issue_id"),
    )
    op.create_index("ix_hw_issues_mandate_id", "hw_issues", ["mandate_id"])

    op.create_table(
        "hw_meeting_packages",
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("mandate_id", sa.String(length=36), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("decision_owner_id", sa.String(length=128), nullable=False),
        sa.Column("required_attendee_ids", sa.JSON(), nullable=False),
        sa.Column("optional_attendee_ids", sa.JSON(), nullable=False),
        sa.Column("proposed_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("proposed_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("agreed_facts", sa.JSON(), nullable=False),
        sa.Column("open_decisions", sa.JSON(), nullable=False),
        sa.Column("agenda", sa.JSON(), nullable=False),
        sa.Column("pre_read_evidence_ids", sa.JSON(), nullable=False),
        sa.Column("calendar_written", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["mandate_id"], ["hw_mandates.mandate_id"]),
        sa.PrimaryKeyConstraint("meeting_id"),
    )
    op.create_index(
        "ix_hw_meeting_packages_created_at", "hw_meeting_packages", ["created_at"]
    )
    op.create_index(
        "ix_hw_meeting_packages_mandate_id",
        "hw_meeting_packages",
        ["mandate_id"],
        unique=True,
    )

    op.create_table(
        "hw_proposals",
        sa.Column("proposal_id", sa.String(length=36), nullable=False),
        sa.Column("mandate_id", sa.String(length=36), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("issue_ids", sa.JSON(), nullable=False),
        sa.Column("required_respondent_ids", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["mandate_id"], ["hw_mandates.mandate_id"]),
        sa.PrimaryKeyConstraint("proposal_id"),
    )
    op.create_index("ix_hw_proposals_created_at", "hw_proposals", ["created_at"])
    op.create_index("ix_hw_proposals_expires_at", "hw_proposals", ["expires_at"])
    op.create_index("ix_hw_proposals_mandate_id", "hw_proposals", ["mandate_id"])
    op.create_index("ix_hw_proposals_state", "hw_proposals", ["state"])

    op.create_table(
        "hw_engagement_decisions",
        sa.Column("decision_id", sa.String(length=36), nullable=False),
        sa.Column("mandate_id", sa.String(length=36), nullable=False),
        sa.Column("assignment_id", sa.String(length=36), nullable=False),
        sa.Column("stakeholder_id", sa.String(length=128), nullable=False),
        sa.Column("response", sa.String(length=32), nullable=False),
        sa.Column("change_text", sa.Text(), nullable=True),
        sa.Column("source_message_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(["assignment_id"], ["hw_assignments.assignment_id"]),
        sa.ForeignKeyConstraint(["mandate_id"], ["hw_mandates.mandate_id"]),
        sa.PrimaryKeyConstraint("decision_id"),
    )
    op.create_index(
        "ix_hw_engagement_decisions_assignment_id",
        "hw_engagement_decisions",
        ["assignment_id"],
        unique=True,
    )
    op.create_index(
        "ix_hw_engagement_decisions_created_at", "hw_engagement_decisions", ["created_at"]
    )
    op.create_index(
        "ix_hw_engagement_decisions_idempotency_key",
        "hw_engagement_decisions",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_hw_engagement_decisions_mandate_id", "hw_engagement_decisions", ["mandate_id"]
    )
    op.create_index(
        "ix_hw_engagement_decisions_stakeholder_id",
        "hw_engagement_decisions",
        ["stakeholder_id"],
    )

    op.create_table(
        "hw_evidence",
        sa.Column("evidence_id", sa.String(length=36), nullable=False),
        sa.Column("mandate_id", sa.String(length=36), nullable=False),
        sa.Column("assignment_id", sa.String(length=36), nullable=False),
        sa.Column("stakeholder_id", sa.String(length=128), nullable=False),
        sa.Column("evidence_type", sa.String(length=40), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_message_id", sa.String(length=255), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("related_decision", sa.Text(), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["assignment_id"], ["hw_assignments.assignment_id"]),
        sa.ForeignKeyConstraint(["mandate_id"], ["hw_mandates.mandate_id"]),
        sa.PrimaryKeyConstraint("evidence_id"),
    )
    op.create_index("ix_hw_evidence_assignment_id", "hw_evidence", ["assignment_id"])
    op.create_index("ix_hw_evidence_created_at", "hw_evidence", ["created_at"])
    op.create_index("ix_hw_evidence_mandate_id", "hw_evidence", ["mandate_id"])
    op.create_index("ix_hw_evidence_stakeholder_id", "hw_evidence", ["stakeholder_id"])

    op.create_table(
        "hw_interviews",
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("mandate_id", sa.String(length=36), nullable=False),
        sa.Column("assignment_id", sa.String(length=36), nullable=False),
        sa.Column("stakeholder_person_id", sa.String(length=128), nullable=False),
        sa.Column("questions", sa.JSON(), nullable=False),
        sa.Column("current_question_index", sa.Integer(), nullable=False),
        sa.Column("current_channel", sa.String(length=32), nullable=True),
        sa.Column("current_route_id", sa.String(length=128), nullable=True),
        sa.Column("current_conversation_id", sa.String(length=255), nullable=True),
        sa.Column("channel_history", sa.JSON(), nullable=False),
        sa.Column("default_visibility", sa.String(length=32), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["assignment_id"], ["hw_assignments.assignment_id"]),
        sa.ForeignKeyConstraint(["mandate_id"], ["hw_mandates.mandate_id"]),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index(
        "ix_hw_interviews_assignment_id", "hw_interviews", ["assignment_id"], unique=True
    )
    op.create_index("ix_hw_interviews_mandate_id", "hw_interviews", ["mandate_id"])
    op.create_index(
        "ix_hw_interviews_stakeholder_person_id", "hw_interviews", ["stakeholder_person_id"]
    )
    op.create_index("ix_hw_interviews_started_at", "hw_interviews", ["started_at"])
    active_interview = sa.text("completed_at IS NULL")
    op.create_index(
        "uq_hw_active_interview_stakeholder",
        "hw_interviews",
        ["mandate_id", "stakeholder_person_id"],
        unique=True,
        sqlite_where=active_interview,
        postgresql_where=active_interview,
    )

    op.create_table(
        "hw_proposal_responses",
        sa.Column("receipt_order", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("response_id", sa.String(length=36), nullable=False),
        sa.Column("proposal_id", sa.String(length=36), nullable=False),
        sa.Column("stakeholder_id", sa.String(length=128), nullable=False),
        sa.Column("response", sa.String(length=32), nullable=False),
        sa.Column("change_text", sa.Text(), nullable=True),
        sa.Column("source_message_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(["proposal_id"], ["hw_proposals.proposal_id"]),
        sa.PrimaryKeyConstraint("receipt_order"),
    )
    op.create_index(
        "ix_hw_proposal_responses_created_at", "hw_proposal_responses", ["created_at"]
    )
    op.create_index(
        "ix_hw_proposal_responses_idempotency_key",
        "hw_proposal_responses",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_hw_proposal_responses_proposal_id", "hw_proposal_responses", ["proposal_id"]
    )
    op.create_index(
        "ix_hw_proposal_responses_response_id",
        "hw_proposal_responses",
        ["response_id"],
        unique=True,
    )
    op.create_index(
        "ix_hw_proposal_responses_stakeholder_id",
        "hw_proposal_responses",
        ["stakeholder_id"],
    )
    op.create_index(
        "uq_hw_proposal_response_source",
        "hw_proposal_responses",
        ["proposal_id", "source_message_id"],
        unique=True,
    )

    op.create_table(
        "hw_release_outbox",
        sa.Column("outbox_id", sa.String(length=64), nullable=False),
        sa.Column("mandate_id", sa.String(length=36), nullable=False),
        sa.Column("assignment_id", sa.String(length=36), nullable=False),
        sa.Column("delivery_id", sa.String(length=48), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("route_index", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("claim_owner", sa.String(length=36), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["assignment_id"], ["hw_assignments.assignment_id"]),
        sa.ForeignKeyConstraint(["mandate_id"], ["hw_mandates.mandate_id"]),
        sa.PrimaryKeyConstraint("outbox_id"),
    )
    op.create_index(
        "ix_hw_release_outbox_assignment_id", "hw_release_outbox", ["assignment_id"]
    )
    op.create_index(
        "ix_hw_release_outbox_claim",
        "hw_release_outbox",
        ["state", "claimed_at", "created_at"],
    )
    op.create_index(
        "ix_hw_release_outbox_created_at", "hw_release_outbox", ["created_at"]
    )
    op.create_index(
        "ix_hw_release_outbox_delivery_id",
        "hw_release_outbox",
        ["delivery_id"],
        unique=True,
    )
    op.create_index(
        "ix_hw_release_outbox_mandate_id", "hw_release_outbox", ["mandate_id"]
    )
    op.create_index("ix_hw_release_outbox_state", "hw_release_outbox", ["state"])


def downgrade() -> None:
    op.drop_table("hw_release_outbox")
    op.drop_table("hw_proposal_responses")
    op.drop_table("hw_interviews")
    op.drop_table("hw_evidence")
    op.drop_table("hw_engagement_decisions")
    op.drop_table("hw_proposals")
    op.drop_table("hw_meeting_packages")
    op.drop_table("hw_issues")
    op.drop_table("hw_events")
    op.drop_table("hw_assignments")
    op.drop_table("hw_runtime_status")
    op.drop_table("hw_mandates")
