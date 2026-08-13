import base64
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, update

import humanwire.web as web_projection
from humanwire.config import Settings
from humanwire.database import (
    DomainEventRecord,
    EvidenceItemRecord,
    InterviewSessionRecord,
    MandateRecord,
    MeetingPackageRecord,
    RuntimeStatusRecord,
    StakeholderAssignmentRecord,
)
from humanwire.demo import create_demo_app
from humanwire.domain import (
    AvailabilityWindow,
    DomainEvent,
    EngagementDecision,
    EngagementDecisionKind,
    EngagementType,
    EvidenceVisibility,
    MandateState,
    StakeholderState,
)
from humanwire.web import create_app
from humanwire.workflow import json_windows

NOW = datetime(2026, 8, 11, 18, 0, tzinfo=UTC)
_CALENDAR_UID_NAMESPACE = b"humanwire:calendar:uid:v1:"


def expected_private_uid(meeting_id: str) -> str:
    digest = hashlib.sha256(_CALENDAR_UID_NAMESPACE + meeting_id.encode("ascii")).digest()
    token = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"{token}@humanwire.local"


@pytest.fixture
def demo_app():
    return create_demo_app()


@pytest.fixture
def web_client(demo_app) -> TestClient:
    return TestClient(demo_app)


def test_read_only_route_surface_and_html_placeholders(web_client, demo_app) -> None:
    expected_get_paths = {
        "/",
        "/mandates/{token}",
        "/mandates/{token}/reach",
        "/mandates/{token}/data",
        "/mandates/{token}/meeting.ics",
        "/health/live",
        "/health/ready",
        "/api/v1/mandates",
        "/api/v1/mandates/{token}",
        "/api/v1/mandates/{token}/stakeholders",
        "/api/v1/mandates/{token}/outreach-events",
        "/api/v1/mandates/{token}/evidence-summary",
    }
    actual_get_paths = {
        route.path
        for route in demo_app.routes
        if getattr(route, "methods", set()) == {"GET"}
    }
    mutating_methods = {
        method
        for route in demo_app.routes
        for method in getattr(route, "methods", set())
        if method in {"POST", "PUT", "PATCH", "DELETE"}
    }

    assert expected_get_paths <= actual_get_paths
    assert mutating_methods == set()
    for path in ("/", "/mandates/HW-2411", "/mandates/HW-2411/reach", "/mandates/HW-2411/data"):
        response = web_client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")


def test_dashboard_templates_and_static_assets_are_package_local(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    client = TestClient(create_demo_app())

    dashboard = client.get("/")
    stylesheet = client.get("/static/styles.css")
    script = client.get("/static/app.js")

    assert dashboard.status_code == 200
    assert stylesheet.status_code == 200
    assert script.status_code == 200
    assert dashboard.headers["content-type"].startswith("text/html")
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert "javascript" in script.headers["content-type"]
    assert 'href="/static/styles.css"' in dashboard.text
    assert 'src="/static/app.js"' in dashboard.text
    assert not re.search(r'(?:href|src)=["\'](?:https?:)?//', dashboard.text)
    assert "<style" not in dashboard.text.lower()
    assert not re.search(r"<script(?![^>]+src=|[^>]+type=\"application/json\")", dashboard.text)


@pytest.mark.parametrize(
    ("state_filter", "expected_tokens"),
    [
        ("active", {"HW-2411"}),
        ("aligned", {"HW-2412"}),
        ("meeting-ready", {"HW-2413"}),
        ("partial", set()),
        ("failed", set()),
    ],
)
def test_dashboard_truthfully_filters_persisted_mandates(
    web_client, state_filter, expected_tokens
) -> None:
    response = web_client.get("/", params={"state": state_filter})

    assert response.status_code == 200
    assert 'data-testid="dashboard"' in response.text
    rendered_tokens = set(
        re.findall(r'data-mandate-token="(HW-[0-9]+)"', response.text)
    )
    assert rendered_tokens == expected_tokens
    for name in ("Active", "Aligned", "Meeting ready", "Partial", "Failed"):
        assert name in response.text


def test_dashboard_lists_only_persisted_demo_rows(web_client) -> None:
    response = web_client.get("/")

    assert response.status_code == 200
    assert set(re.findall(r'data-mandate-token="(HW-[0-9]+)"', response.text)) == {
        "HW-2411",
        "HW-2412",
        "HW-2413",
    }
    assert "Prepare approved weekend launch coverage" in response.text
    assert "Confirm incident review ownership" in response.text
    assert "Resolve the launch approval decision" in response.text
    assert "Q4 customer migration" not in response.text
    assert re.search(r"Meeting ready\s*<span>1</span>", response.text)


def test_decision_room_uses_coordination_and_adaptive_engagements(web_client) -> None:
    html = web_client.get("/mandates/HW-2411").text

    assert 'data-testid="decision-room"' in html
    assert 'data-testid="workflow-step-coordinating"' in html
    assert 'aria-current="step"' in html
    assert 'data-testid="next-action"' in html
    assert 'data-testid="stakeholders"' in html
    assert 'data-testid="engagement-ladder"' in html
    assert 'data-testid="activity-timeline"' in html
    assert 'data-testid="human-evidence"' in html
    assert 'data-testid="ai-draft"' in html
    assert 'data-testid="reach-preview"' in html
    assert 'data-testid="technical-data"' in html
    assert 'data-testid="live-refresh"' in html
    assert "Engagement progress" in html
    assert "Structured interview" in html
    assert "Quick response" in html
    assert "Acknowledgement" in html
    assert "Approval review" in html
    assert "Inform only" in html
    assert "Interview progress" not in html
    assert "Interviewing" not in html


def test_decision_room_renders_exact_persisted_hw_2411_rows(web_client) -> None:
    html = web_client.get("/mandates/HW-2411").text

    expected = {
        "priya-shah": ("Priya Shah", "Structured interview", "1 of 3", "In progress"),
        "eli-torres": ("Eli Torres", "Quick response", "1 of 1", "Complete"),
        "sora-kim": ("Sora Kim", "Quick response", "1 of 1", "Complete"),
        "nora-chen": ("Nora Chen", "Acknowledgement", "1 of 1", "Acknowledged"),
        "maya-brooks": ("Maya Brooks", "Approval review", "0 of 1", "Pending"),
        "inez-ward": ("Inez Ward", "Inform only", "1 of 1", "Delivered"),
    }
    for person_id, values in expected.items():
        row = re.search(
            rf'<tr[^>]+data-person="{person_id}".*?</tr>', html, re.DOTALL
        )
        assert row is not None
        assert all(value in row.group(0) for value in values)
    assert "Daniel Kim" not in html
    assert "Luis Alvarez" not in html
    assert re.findall(r'<tr[^>]+data-person="([^"]+)"', html) == [
        "priya-shah",
        "eli-torres",
        "sora-kim",
        "nora-chen",
        "maya-brooks",
        "inez-ward",
    ]


def test_decision_room_has_no_projection_key_collision_placeholders(web_client) -> None:
    html = web_client.get("/mandates/HW-2411").text

    assert "[REDACTED]" not in html
    assert "Lateral" in html
    assert "In progress" in html


def _ladder_row(engagement_type: str, **updates) -> dict[str, object]:
    row: dict[str, object] = {
        "engagement_type": engagement_type,
        "engagement_status": "pending",
        "state": "awaiting_acknowledgement",
        "attempt_count": 1,
        "channel": "email",
        "channel_is_alternate": False,
        "progress_current": 0,
        "progress_total": 1,
        "first_contact_at": "2026-08-11T15:04:00+00:00",
        "last_delivery_at": "2026-08-11T15:06:00+00:00",
        "acknowledged_at": None,
        "completed_at": None,
    }
    row.update(updates)
    return row


@pytest.mark.parametrize(
    ("row", "labels", "forbidden"),
    [
        (
            _ladder_row(
                "inform", state="complete", engagement_status="delivered",
                completed_at="2026-08-11T15:12:00+00:00",
            ),
            ["Primary", "Delivered"],
            ["Reminder", "Acknowledged", "Interview", "Decision"],
        ),
        (
            _ladder_row(
                "acknowledge", state="complete", engagement_status="acknowledged",
                acknowledged_at="2026-08-11T15:10:00+00:00",
                completed_at="2026-08-11T15:10:00+00:00",
            ),
            ["Primary", "Acknowledged"],
            ["Quick response", "Interview", "Confirmation", "Decision"],
        ),
        (
            _ladder_row(
                "quick_response", state="complete", engagement_status="complete",
                progress_current=1, acknowledged_at="2026-08-11T15:10:00+00:00",
                completed_at="2026-08-11T15:12:00+00:00",
            ),
            ["Primary", "Acknowledged", "Quick response", "Confirmation"],
            ["Interview", "Decision", "Availability"],
        ),
        (
            _ladder_row(
                "structured_interview", state="interviewing",
                engagement_status="in progress", attempt_count=2,
                channel="telegram", channel_is_alternate=True,
                progress_current=1, progress_total=3,
                acknowledged_at="2026-08-11T15:09:00+00:00",
            ),
            [
                "Primary", "Reminder", "Alternate Telegram", "Acknowledged",
                "Interview", "Confirmation",
            ],
            ["Quick response", "Decision", "Availability"],
        ),
        (
            _ladder_row("review_approval"),
            ["Primary", "Decision"],
            ["Quick response", "Interview", "Confirmation", "Availability"],
        ),
        (
            _ladder_row("availability", engagement_status="missing"),
            ["Primary", "Availability"],
            ["Quick response", "Interview", "Confirmation", "Decision"],
        ),
    ],
    ids=["inform", "acknowledge", "quick", "structured", "review", "availability"],
)
def test_engagement_ladder_matrix_uses_only_type_specific_steps(row, labels, forbidden) -> None:
    ladder = web_projection._engagement_ladder(row)
    rendered_labels = [step["label"] for step in ladder]

    assert rendered_labels == labels
    assert all(label not in rendered_labels for label in forbidden)
    assert sum(step["status"] == "current" for step in ladder) <= 1
    assert all(step["status"] in {"complete", "current", "pending"} for step in ladder)


def test_engagement_ladder_has_only_the_earliest_current_step() -> None:
    ladder = web_projection._engagement_ladder(
        _ladder_row("acknowledge", attempt_count=2)
    )

    assert [(step["label"], step["status"]) for step in ladder] == [
        ("Primary", "complete"),
        ("Reminder", "current"),
        ("Acknowledged", "pending"),
    ]


def test_decision_room_defaults_to_safe_priya_selection_without_inventing_action(
    web_client,
) -> None:
    html = web_client.get("/mandates/HW-2411").text

    assert 'data-selected-person="priya-shah"' in html
    assert "Selected: Priya Shah" in html
    assert "No pending action" in html
    assert "Contact Priya through registered Telegram" not in html
    assert "data-countdown" not in html
    assert 'data-deadline=""' in html
    assert "Alternate Telegram" in html
    assert "Interview" in html
    for forbidden in (
        "demo-route-priya-shah-alternate",
        "demo-conversation-priya-shah",
        "route_id",
        "connection_id",
        "conversation_id",
        "sender_id",
        "recipient",
        "@example",
    ):
        assert forbidden not in html


def test_decision_room_local_controls_and_links_are_read_only(web_client) -> None:
    html = web_client.get("/mandates/HW-2411").text

    for state in ("all", "in-progress", "completed", "pending", "delivered"):
        assert f'data-filter="{state}"' in html
    assert re.search(r"In progress\s*<span>1</span>", html)
    assert 'aria-pressed="true"' in html
    assert 'href="/mandates/HW-2411/reach"' in html
    assert 'href="/mandates/HW-2411/data"' in html
    assert "<form" not in html.lower()
    assert "contenteditable" not in html.lower()
    assert "javascript:" not in html.lower()
    assert not re.search(r"on(?:click|change|submit|keydown)=", html, re.IGNORECASE)


def test_decision_room_separates_human_evidence_from_persisted_ai_truth(web_client) -> None:
    html = web_client.get("/mandates/HW-2411").text
    human = re.search(r'data-testid="human-evidence".*?</section>', html, re.DOTALL)
    ai = re.search(r'data-testid="ai-draft".*?</section>', html, re.DOTALL)

    assert human is not None and ai is not None
    assert "3" in human.group(0)
    assert "Evidence items saved" in human.group(0)
    assert "Missing responses" in human.group(0)
    assert "Assumptions" in ai.group(0)
    assert re.search(r"Assumptions.*?>0<", ai.group(0), re.DOTALL)
    assert "Not ready" in ai.group(0)


def test_decision_room_has_semantic_accessible_structure_and_hardening(web_client) -> None:
    response = web_client.get("/mandates/HW-2411")
    html = response.text

    assert html.count("<h1") == 1
    assert html.index('class="skip-link"') < html.index("<header")
    for landmark in ("<header", "<nav", "<main", "<section", "<aside", "<footer"):
        assert landmark in html
    assert 'aria-label="Primary"' in html
    assert 'aria-live="polite"' in html
    assert 'type="button"' in html
    assert 'scope="col"' in html
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "script-src 'self'" in response.headers["content-security-policy"]


def test_decision_room_css_reflows_without_a_fixed_desktop_table(web_client) -> None:
    css = web_client.get("/static/styles.css").text

    assert ":focus-visible" in css
    assert re.search(r"font-size:\s*(?:14px|0\.875rem)", css)
    assert "@media (max-width: 759px)" in css
    assert re.search(
        r"@media \(max-width: 759px\).*?\.decision-layout\s*\{[^}]*grid-template-columns:\s*1fr",
        css,
        re.DOTALL,
    )
    assert re.search(
        r"@media \(max-width: 759px\).*?\.stakeholder-row\s*\{[^}]*display:\s*grid",
        css,
        re.DOTALL,
    )
    assert "overflow-x: clip" in css
    assert "min-width: 760px" not in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert re.search(r"\.stakeholder-row td\s*\{[^}]*font-size:\s*14px", css, re.DOTALL)
    assert re.search(r"\.state-filters button\s*\{[^}]*font-size:\s*14px", css, re.DOTALL)


def test_decision_room_stylesheet_never_shrinks_meaningful_text_below_14px(
    web_client,
) -> None:
    css = web_client.get("/static/styles.css").text
    numeric_sizes = re.findall(r"font-size:\s*([0-9.]+)(px|rem)", css)

    assert numeric_sizes
    too_small = [
        f"{value}{unit}"
        for value, unit in numeric_sizes
        if (float(value) if unit == "px" else float(value) * 14) < 14
    ]
    assert too_small == []


def test_typography_floor_reflows_dense_content_instead_of_clipping_it(web_client) -> None:
    css = web_client.get("/static/styles.css").text

    assert re.search(r"\.next-action\s*\{[^}]*align-self:\s*start", css, re.DOTALL)
    assert re.search(
        r"\.stakeholder-table th:nth-child\(8\)\s*\{\s*width:\s*13%",
        css,
    )
    assert re.search(
        r"@media \(max-width: 759px\).*?\.engagement-ladder ol\s*\{[^}]*"
        r"grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)",
        css,
        re.DOTALL,
    )
    assert re.search(
        r"@media \(max-width: 759px\).*?\.activity-timeline strong\s*\{[^}]*"
        r"white-space:\s*normal",
        css,
        re.DOTALL,
    )
    assert re.search(
        r"@media \(max-width: 479px\).*?\.engagement-ladder ol\s*\{[^}]*"
        r"grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)",
        css,
        re.DOTALL,
    )


def test_decision_room_javascript_polls_persisted_state_only(web_client) -> None:
    script = web_client.get("/static/app.js").text

    assert "window.HumanWire" in script
    assert "refreshMandate" in script
    assert "/api/v1/mandates/${encodeURIComponent(token)}" in script
    assert 'method: "GET"' in script
    assert "document.visibilityState" in script
    assert "visibilitychange" in script
    assert "5000" in script
    assert "updated_at" in script
    assert "window.location.reload" in script
    assert "countdown" in script
    assert "clearInterval" in script or "clearTimeout" in script
    for forbidden in ('"POST"', '"PUT"', '"PATCH"', '"DELETE"'):
        assert forbidden not in script
    for forbidden in ("ANALYTICS_READ_TOKEN", "provider_body", "synthesized event"):
        assert forbidden not in script


def test_public_demo_has_no_mutating_routes_or_hidden_mutation(web_client) -> None:
    before = web_client.get("/api/v1/mandates/HW-2411/outreach-events").json()

    response = web_client.post("/api/v1/mandates/HW-2411/cancel", json={"reason": "no"})

    assert response.status_code == 405
    assert web_client.get("/api/v1/mandates/HW-2411/outreach-events").json() == before


def test_mandate_api_contains_live_workflow_state_but_no_routes(web_client) -> None:
    payload = web_client.get("/api/v1/mandates/HW-2411").json()

    assert payload["token"] == "HW-2411"
    assert payload["state"] == "interviewing"
    assert payload["initiator"]["person_id"] == "arun-patel"
    assert payload["initiator"]["name"] == "Arun Patel"
    assert payload["next_action"] is None
    serialized = json.dumps(payload)
    for forbidden in (
        "@example.com",
        "@example.test",
        "tg-priya",
        "route_id",
        "connection_id",
        "sender_id",
        "origin_conversation_id",
        "origin_message_id",
        "idempotency_key",
        "PRIVATE",
        "medical leave",
    ):
        assert forbidden not in serialized


def test_public_progress_projects_each_persisted_engagement_contract(web_client) -> None:
    detail = web_client.get("/api/v1/mandates/HW-2411").json()
    summary = next(
        item
        for item in web_client.get("/api/v1/mandates").json()
        if item["token"] == "HW-2411"
    )
    rows = {
        item["person_id"]: item
        for item in web_client.get("/api/v1/mandates/HW-2411/stakeholders").json()
    }

    assert detail["state"] == "interviewing"
    assert detail["phase_label"] == "coordinating"
    assert summary["phase_label"] == "coordinating"
    assert {
        person_id: (
            row["engagement_type"],
            row["response_required"],
            row["engagement_status"],
            row["progress_current"],
            row["progress_total"],
        )
        for person_id, row in rows.items()
    } == {
        "eli-torres": ("quick_response", True, "complete", 1, 1),
        "sora-kim": ("quick_response", True, "complete", 1, 1),
        "priya-shah": ("structured_interview", True, "in progress", 1, 3),
        "nora-chen": ("acknowledge", True, "acknowledged", 1, 1),
        "maya-brooks": ("review_approval", True, "pending", 0, 1),
        "inez-ward": ("inform", False, "delivered", 1, 1),
    }


@pytest.mark.parametrize(
    ("state", "status"),
    [
        (StakeholderState.DELIVERY_FAILED, "delivery failed"),
        (StakeholderState.UNREACHABLE, "unreachable"),
        (StakeholderState.DECLINED, "declined"),
    ],
)
def test_terminal_assignment_progress_is_truthful(demo_app, state, status) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2411")
    assignment = next(
        item
        for item in repository.list_assignments(mandate.mandate_id)
        if item.person_id == "inez-ward"
    )
    repository.save_assignment(
        assignment.model_copy(
            update={"state": state, "completed_at": None, "failure_reason": status}
        )
    )

    rows = TestClient(demo_app).get(
        "/api/v1/mandates/HW-2411/stakeholders"
    ).json()
    row = next(item for item in rows if item["person_id"] == "inez-ward")

    assert row["engagement_status"] == status
    assert (row["progress_current"], row["progress_total"]) == (0, 1)


@pytest.mark.parametrize(
    (
        "state",
        "expected_status",
        "expected_progress",
        "expected_ladder",
    ),
    [
        (
            StakeholderState.CONTACT_QUEUED,
            "pending",
            (0, 1),
            [("Primary", "current"), ("Delivered", "pending")],
        ),
        (
            StakeholderState.DELIVERED,
            "pending",
            (0, 1),
            [("Primary", "complete"), ("Delivered", "current")],
        ),
        (
            StakeholderState.AWAITING_ACKNOWLEDGEMENT,
            "pending",
            (0, 1),
            [("Primary", "complete"), ("Delivered", "pending")],
        ),
        (
            StakeholderState.COMPLETE,
            "delivered",
            (1, 1),
            [("Primary", "complete"), ("Delivered", "complete")],
        ),
        (
            StakeholderState.DELIVERY_FAILED,
            "delivery failed",
            (0, 1),
            [("Primary", "complete"), ("Delivered", "pending")],
        ),
        (
            StakeholderState.UNREACHABLE,
            "unreachable",
            (0, 1),
            [("Primary", "complete"), ("Delivered", "pending")],
        ),
    ],
    ids=[
        "contact-queued",
        "delivered-without-completion-proof",
        "invalid-awaiting-state-fails-safe",
        "delivery-confirmed-complete",
        "delivery-failed",
        "unreachable",
    ],
)
def test_inform_projection_and_ladder_require_delivery_confirmed_completion(
    demo_app,
    state,
    expected_status,
    expected_progress,
    expected_ladder,
) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2411")
    assignment = next(
        item
        for item in repository.list_assignments(mandate.mandate_id)
        if item.person_id == "inez-ward"
    )
    no_outreach = state is StakeholderState.CONTACT_QUEUED
    repository.save_assignment(
        assignment.model_copy(
            update={
                "state": state,
                "attempt_count": 0 if no_outreach else 1,
                "first_contact_at": None if no_outreach else assignment.first_contact_at,
                "last_delivery_at": None if no_outreach else assignment.last_delivery_at,
                "completed_at": (
                    assignment.completed_at if state is StakeholderState.COMPLETE else None
                ),
            }
        )
    )

    rows = TestClient(demo_app).get(
        "/api/v1/mandates/HW-2411/stakeholders"
    ).json()
    row = next(item for item in rows if item["person_id"] == "inez-ward")
    ladder = web_projection._engagement_ladder(row)

    assert row["engagement_status"] == expected_status
    assert (row["progress_current"], row["progress_total"]) == expected_progress
    assert [(step["label"], step["status"]) for step in ladder] == expected_ladder


def _next_action_region(html: str) -> str:
    match = re.search(r'data-testid="next-action".*?</aside>', html, re.DOTALL)
    assert match is not None
    return match.group(0)


@pytest.mark.parametrize("state", list(MandateState), ids=lambda state: state.value)
def test_decision_room_never_invents_action_without_exact_persisted_next_action(
    demo_app,
    state,
) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2411")
    repository.save_mandate(
        mandate.model_copy(
            update={
                "state": state,
                "next_action_at": None,
                "completed_at": NOW if state in {
                    MandateState.ALIGNED,
                    MandateState.MEETING_READY,
                    MandateState.PARTIAL,
                    MandateState.EXPIRED,
                    MandateState.CANCELLED,
                    MandateState.DELIVERY_FAILED,
                } else None,
            }
        )
    )
    for assignment in repository.list_assignments(mandate.mandate_id):
        repository.save_assignment(assignment.model_copy(update={"next_action_at": None}))

    html = TestClient(demo_app).get("/mandates/HW-2411").text
    action = _next_action_region(html)
    expected_state = "Coordinating" if state is MandateState.INTERVIEWING else (
        state.value.replace("_", " ").capitalize()
    )

    assert expected_state in html
    assert "No pending action" in action
    assert "Contact " not in action
    assert "Due in" not in action
    assert "Why this matters" not in action
    assert "data-countdown" not in action
    assert 'data-deadline=""' in html


@pytest.mark.parametrize("state", list(MandateState), ids=lambda state: state.value)
def test_decision_room_renders_only_live_coordinating_exact_assignment_action(
    demo_app,
    state,
) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2411")
    repository.save_mandate(mandate.model_copy(update={"state": state}))
    assignments = repository.list_assignments(mandate.mandate_id)
    maya = next(item for item in assignments if item.person_id == "maya-brooks")
    scheduled_at = NOW + timedelta(minutes=20)
    repository.save_assignment(maya.model_copy(update={"next_action_at": scheduled_at}))

    html = TestClient(demo_app).get("/mandates/HW-2411").text
    action = _next_action_region(html)

    assert 'data-selected-person="priya-shah"' in html
    assert "Selected: Priya Shah" in html
    if state is MandateState.INTERVIEWING:
        assert "Contact Maya through registered Email" in action
        assert "data-countdown" in action
        assert scheduled_at.isoformat() in html
        assert "Contact Priya" not in action
        assert "Review the approval request" in action
    else:
        assert "No pending action" in action
        assert "Contact " not in action
        assert "Why this matters" not in action
        assert "data-countdown" not in action
        assert 'data-deadline=""' in html


def test_declined_structured_interview_preserves_truthful_question_progress(
    demo_app,
) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2411")
    assignment = next(
        item
        for item in repository.list_assignments(mandate.mandate_id)
        if item.person_id == "priya-shah"
    )
    repository.save_assignment(
        assignment.model_copy(update={"state": StakeholderState.DECLINED})
    )

    rows = TestClient(demo_app).get(
        "/api/v1/mandates/HW-2411/stakeholders"
    ).json()
    row = next(item for item in rows if item["person_id"] == "priya-shah")

    assert row["engagement_status"] == "declined"
    assert (row["progress_current"], row["progress_total"]) == (1, 3)


def test_missing_legacy_interview_fails_safe_without_inventing_progress(demo_app) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2411")
    assignment = next(
        item
        for item in repository.list_assignments(mandate.mandate_id)
        if item.person_id == "priya-shah"
    )
    with repository._session_factory() as session:
        session.execute(
            delete(InterviewSessionRecord).where(
                InterviewSessionRecord.session_id == str(assignment.interview_id)
            )
        )
        session.commit()

    rows = TestClient(demo_app).get(
        "/api/v1/mandates/HW-2411/stakeholders"
    ).json()
    row = next(item for item in rows if item["person_id"] == "priya-shah")

    assert row["engagement_status"] == "awaiting response"
    assert (row["progress_current"], row["progress_total"]) == (0, 3)


@pytest.mark.parametrize(
    "identity_mutation",
    [
        {"mandate_id": uuid4()},
        {"assignment_id": uuid4()},
    ],
    ids=["cross-mandate", "cross-assignment"],
)
def test_question_progress_requires_exact_interview_aggregate_identity(
    demo_app, identity_mutation
) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2411")
    assignment = next(
        item
        for item in repository.list_assignments(mandate.mandate_id)
        if item.person_id == "priya-shah"
    )
    interview = repository.get_interview(assignment.interview_id).model_copy(
        update=identity_mutation
    )
    planned = next(
        item
        for item in mandate.plan.stakeholders
        if item.person_ref == assignment.person_id
    )

    row = web_projection._assignment_projection(
        repository,
        assignment,
        {assignment.interview_id: interview},
        planned,
        {},
    )

    assert row["engagement_status"] == "awaiting response"
    assert (row["progress_current"], row["progress_total"]) == (0, 3)
    assert row["interview_status"] == "not_started"
    assert row["current_question"] is None
    assert row["channel"] is None


def test_non_question_engagement_never_projects_an_interview_session(demo_app) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2411")
    question_assignment = next(
        item
        for item in repository.list_assignments(mandate.mandate_id)
        if item.person_id == "priya-shah"
    )
    assignment = question_assignment.model_copy(
        update={
            "engagement_type": EngagementType.INFORM,
            "response_required": False,
        }
    )
    interview = repository.get_interview(assignment.interview_id)

    row = web_projection._assignment_projection(
        repository,
        assignment,
        {assignment.interview_id: interview},
        None,
        {},
    )

    assert row["interview_status"] == "not_started"
    assert row["current_question"] is None
    assert row["channel"] is None


def test_availability_progress_requires_the_exact_persisted_record(demo_app) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2411")
    assignment = next(
        item
        for item in repository.list_assignments(mandate.mandate_id)
        if item.person_id == "inez-ward"
    ).model_copy(
        update={
            "engagement_type": EngagementType.AVAILABILITY,
            "response_required": True,
        }
    )
    repository.save_assignment(assignment)
    repository.set_runtime_status(
        f"availability:{mandate.mandate_id}:{assignment.person_id}",
        "2026-08-12T15:00:00+00:00/2026-08-12T16:00:00+00:00",
        assignment.completed_at,
    )

    rows = TestClient(demo_app).get(
        "/api/v1/mandates/HW-2411/stakeholders"
    ).json()
    row = next(item for item in rows if item["person_id"] == assignment.person_id)

    assert row["engagement_status"] == "recorded"
    assert (row["progress_current"], row["progress_total"]) == (1, 1)


def test_raw_engagement_change_text_is_denied_on_every_public_route(demo_app) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2411")
    assignment = next(
        item
        for item in repository.list_assignments(mandate.mandate_id)
        if item.person_id == "maya-brooks"
    )
    repository.add_engagement_decision(
        EngagementDecision(
            decision_id=uuid4(),
            mandate_id=mandate.mandate_id,
            assignment_id=assignment.assignment_id,
            stakeholder_id="unrelated-reviewer",
            response=EngagementDecisionKind.CHANGE,
            change_text="PRIVATE-DECISION-CHANGE-SENTINEL",
            source_message_id="private-change-source",
            created_at=NOW,
            idempotency_key="private-change-sentinel",
        )
    )
    client = TestClient(demo_app)
    paths = (
        "/",
        "/mandates/HW-2411",
        "/mandates/HW-2411/reach",
        "/mandates/HW-2411/data",
        "/api/v1/mandates",
        "/api/v1/mandates/HW-2411",
        "/api/v1/mandates/HW-2411/stakeholders",
        "/api/v1/mandates/HW-2411/outreach-events",
        "/api/v1/mandates/HW-2411/evidence-summary",
        "/mandates/HW-2413/meeting.ics",
    )

    responses = [client.get(path) for path in paths]
    stakeholder_rows = client.get(
        "/api/v1/mandates/HW-2411/stakeholders"
    ).json()
    reviewer = next(
        item for item in stakeholder_rows if item["person_id"] == assignment.person_id
    )

    assert all(response.status_code == 200 for response in responses)
    assert reviewer["engagement_status"] == "pending"
    assert (reviewer["progress_current"], reviewer["progress_total"]) == (0, 1)
    assert "PRIVATE-DECISION-CHANGE-SENTINEL" not in "".join(
        response.text for response in responses
    )


@pytest.mark.parametrize(
    ("response", "status"),
    [
        (EngagementDecisionKind.APPROVE, "approved"),
        (EngagementDecisionKind.REJECT, "rejected"),
        (EngagementDecisionKind.CHANGE, "change requested"),
    ],
)
def test_review_progress_comes_only_from_the_exact_persisted_decision(
    demo_app, response, status
) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2411")
    assignment = next(
        item
        for item in repository.list_assignments(mandate.mandate_id)
        if item.person_id == "maya-brooks"
    )
    repository.add_engagement_decision(
        EngagementDecision(
            decision_id=uuid4(),
            mandate_id=mandate.mandate_id,
            assignment_id=assignment.assignment_id,
            stakeholder_id=assignment.person_id,
            response=response,
            change_text="PRIVATE-REVIEW-SENTINEL" if response is EngagementDecisionKind.CHANGE else None,
            source_message_id=f"review-progress-{response.value}",
            created_at=NOW,
            idempotency_key=f"review-progress-{response.value}",
        )
    )

    rows = TestClient(demo_app).get(
        "/api/v1/mandates/HW-2411/stakeholders"
    ).json()
    row = next(item for item in rows if item["person_id"] == assignment.person_id)

    assert row["engagement_status"] == status
    assert (row["progress_current"], row["progress_total"]) == (1, 1)


@pytest.mark.parametrize(
    "identity_mutation",
    [
        {"mandate_id": uuid4()},
        {"assignment_id": uuid4()},
        {"stakeholder_id": "unrelated-reviewer"},
    ],
    ids=["cross-mandate", "cross-assignment", "cross-person"],
)
def test_review_progress_requires_exact_decision_aggregate_identity(
    demo_app, identity_mutation
) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2411")
    assignment = next(
        item
        for item in repository.list_assignments(mandate.mandate_id)
        if item.person_id == "maya-brooks"
    )
    decision = EngagementDecision(
        decision_id=uuid4(),
        mandate_id=mandate.mandate_id,
        assignment_id=assignment.assignment_id,
        stakeholder_id=assignment.person_id,
        response=EngagementDecisionKind.APPROVE,
        source_message_id="mismatched-review-source",
        created_at=NOW,
        idempotency_key="mismatched-review-decision",
    ).model_copy(update=identity_mutation)

    row = web_projection._assignment_projection(
        repository,
        assignment,
        {},
        None,
        {assignment.assignment_id: decision},
    )

    assert row["engagement_status"] == "pending"
    assert (row["progress_current"], row["progress_total"]) == (0, 1)


def test_non_review_engagement_never_projects_an_approval_decision(demo_app) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2411")
    review_assignment = next(
        item
        for item in repository.list_assignments(mandate.mandate_id)
        if item.person_id == "maya-brooks"
    )
    assignment = review_assignment.model_copy(
        update={"engagement_type": EngagementType.ACKNOWLEDGE}
    )
    decision = EngagementDecision(
        decision_id=uuid4(),
        mandate_id=assignment.mandate_id,
        assignment_id=assignment.assignment_id,
        stakeholder_id=assignment.person_id,
        response=EngagementDecisionKind.APPROVE,
        source_message_id="wrong-type-review-source",
        created_at=NOW,
        idempotency_key="wrong-type-review-decision",
    )

    row = web_projection._assignment_projection(
        repository,
        assignment,
        {},
        None,
        {assignment.assignment_id: decision},
    )

    assert row["engagement_status"] == "awaiting acknowledgement"
    assert (row["progress_current"], row["progress_total"]) == (0, 1)


def test_public_projection_redacts_a_destination_even_if_it_reaches_persisted_text(
    demo_app,
) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2411")
    assert mandate is not None
    repository.save_mandate(
        mandate.model_copy(
            update={"objective": "Contact malicious-contact@example.invalid or @malicious_chat"}
        )
    )
    assignment = repository.list_assignments(mandate.mandate_id)[0]
    repository.save_assignment(
        assignment.model_copy(update={"reason": "Contact owner@example.test"})
    )
    repository.set_runtime_status(
        f"public.person:{assignment.person_id}",
        json.dumps({"name": "Owner @malicious_chat", "role": "owner@example.test"}),
        NOW,
    )

    client = TestClient(demo_app)
    responses = [
        client.get("/api/v1/mandates/HW-2411"),
        client.get("/api/v1/mandates/HW-2411/stakeholders"),
    ]

    assert all(response.status_code == 200 for response in responses)
    serialized = "".join(response.text for response in responses)
    assert "malicious-contact@example.invalid" not in serialized
    assert "owner@example.test" not in serialized
    assert "@malicious_chat" not in serialized
    assert "[REDACTED]" in serialized


def test_every_public_surface_applies_one_recursive_sanitization_boundary(demo_app) -> None:
    repository = demo_app.state.repository
    primary = repository.get_mandate_by_token("HW-2411")
    meeting = repository.get_mandate_by_token("HW-2413")
    assert primary is not None and meeting is not None
    sentinels = {
        "malicious-contact@example.invalid",
        "@malicious_chat",
        "Bearer persisted-secret",
    }
    with repository._session_factory() as session:
        session.execute(
            update(MandateRecord)
            .where(MandateRecord.mandate_id == str(primary.mandate_id))
            .values(
                initiator_id="malicious-contact@example.invalid",
                objective="Contact @malicious_chat using Bearer persisted-secret",
            )
        )
        assignment_id = session.scalar(
            StakeholderAssignmentRecord.__table__.select()
            .with_only_columns(StakeholderAssignmentRecord.assignment_id)
            .where(StakeholderAssignmentRecord.mandate_id == str(primary.mandate_id))
            .limit(1)
        )
        session.execute(
            update(StakeholderAssignmentRecord)
            .where(StakeholderAssignmentRecord.assignment_id == assignment_id)
            .values(
                person_id="malicious-contact@example.invalid",
                department="@malicious_chat",
                reason="Bearer persisted-secret",
            )
        )
        evidence_id = session.scalar(
            EvidenceItemRecord.__table__.select()
            .with_only_columns(EvidenceItemRecord.evidence_id)
            .where(EvidenceItemRecord.mandate_id == str(primary.mandate_id))
            .limit(1)
        )
        session.execute(
            update(EvidenceItemRecord)
            .where(EvidenceItemRecord.evidence_id == evidence_id)
            .values(
                stakeholder_id="malicious-contact@example.invalid",
                statement="@malicious_chat",
                related_decision="Bearer persisted-secret",
                resource="malicious-contact@example.invalid",
            )
        )
        event_id = session.scalar(
            DomainEventRecord.__table__.select()
            .with_only_columns(DomainEventRecord.event_id)
            .where(DomainEventRecord.mandate_id == str(primary.mandate_id))
            .limit(1)
        )
        session.execute(
            update(DomainEventRecord)
            .where(DomainEventRecord.event_id == event_id)
            .values(
                event_type="malicious-contact@example.invalid",
                actor_id="@malicious_chat",
                person_id="malicious-contact@example.invalid",
                department="Bearer persisted-secret",
                previous_state="@malicious_chat",
                new_state="Bearer persisted-secret",
                event_metadata={
                    "references": [{"person_id": "malicious-contact@example.invalid"}],
                    "malicious-contact@example.invalid": "Bearer persisted-secret",
                },
            )
        )
        session.execute(
            update(MeetingPackageRecord)
            .where(MeetingPackageRecord.mandate_id == str(meeting.mandate_id))
            .values(
                purpose="Bearer persisted-secret",
                optional_attendee_ids=["malicious-contact@example.invalid"],
                agreed_facts=["@malicious_chat"],
                open_decisions=["malicious-contact@example.invalid"],
                agenda=["Bearer persisted-secret"],
            )
        )
        session.execute(
            update(RuntimeStatusRecord)
            .where(RuntimeStatusRecord.key == "public.person:maya-chen")
            .values(
                value=json.dumps(
                    {
                        "name": "malicious-contact@example.invalid",
                        "role": "@malicious_chat",
                        "metadata": {"secret": "Bearer persisted-secret"},
                    }
                )
            )
        )
        session.commit()

    client = TestClient(demo_app)
    paths = [
        "/",
        "/mandates/HW-2411",
        "/mandates/HW-2411/reach",
        "/mandates/HW-2411/data",
        "/mandates/HW-2413",
        "/mandates/HW-2413/meeting.ics",
        "/api/v1/mandates",
        "/api/v1/mandates/HW-2411",
        "/api/v1/mandates/HW-2411/stakeholders",
        "/api/v1/mandates/HW-2411/outreach-events",
        "/api/v1/mandates/HW-2411/evidence-summary",
        "/health/live",
        "/health/ready",
        "/mandates/HW-UNKNOWN",
    ]
    responses = [client.get(path) for path in paths]
    serialized = "\n".join(
        response.text + json.dumps(dict(response.headers)) for response in responses
    )

    assert all(sentinel not in serialized for sentinel in sentinels)
    assert "interviewing" in serialized
    assert "downward" in serialized
    assert "mandate.interviewing" in serialized
    event_rows = responses[9].json()
    redacted_event = next(row for row in event_rows if row["event_type"] == "[REDACTED]")
    assert redacted_event["metadata"] == {
        "references": [{"person_id": "[REDACTED]"}]
    }


def test_private_evidence_text_is_denied_on_every_surface_but_public_text_remains(
    demo_app,
) -> None:
    repository = demo_app.state.repository
    primary = repository.get_mandate_by_token("HW-2411")
    meeting = repository.get_mandate_by_token("HW-2413")
    assert primary is not None and meeting is not None
    private_statement = "The north conference room will be unavailable on Friday."
    private_decision = "Move the review to the quiet room."
    private_resource = "Blue binder reference seven."
    public_statement = "Approved public coverage remains available."

    primary_evidence = repository.list_evidence(primary.mandate_id)
    private_primary = next(
        item for item in primary_evidence if item.visibility == EvidenceVisibility.PRIVATE
    )
    shareable_primary = next(
        item for item in primary_evidence if item.visibility == EvidenceVisibility.SHAREABLE
    )
    repository.add_evidence(
        private_primary.model_copy(
            update={
                "evidence_id": uuid4(),
                "mandate_id": meeting.mandate_id,
                "assignment_id": repository.list_assignments(meeting.mandate_id)[0].assignment_id,
                "statement": private_statement,
                "related_decision": private_decision,
                "resource": private_resource,
                "source_message_id": "private-source-ordinary-phrase",
            }
        )
    )

    meeting_plan = meeting.plan.model_dump(mode="json")
    meeting_plan["objective"] = private_statement
    with repository._session_factory() as session:
        session.execute(
            update(EvidenceItemRecord)
            .where(EvidenceItemRecord.evidence_id == str(private_primary.evidence_id))
            .values(
                statement=private_statement,
                related_decision=private_decision,
                resource=private_resource,
            )
        )
        session.execute(
            update(EvidenceItemRecord)
            .where(EvidenceItemRecord.evidence_id == str(shareable_primary.evidence_id))
            .values(statement=public_statement)
        )
        session.execute(
            update(MandateRecord)
            .where(MandateRecord.mandate_id == str(primary.mandate_id))
            .values(objective=private_statement)
        )
        session.execute(
            update(MandateRecord)
            .where(MandateRecord.mandate_id == str(meeting.mandate_id))
            .values(objective=private_statement, plan=meeting_plan)
        )
        assignment_id = session.scalar(
            StakeholderAssignmentRecord.__table__.select()
            .with_only_columns(StakeholderAssignmentRecord.assignment_id)
            .where(StakeholderAssignmentRecord.mandate_id == str(primary.mandate_id))
            .limit(1)
        )
        session.execute(
            update(StakeholderAssignmentRecord)
            .where(StakeholderAssignmentRecord.assignment_id == assignment_id)
            .values(reason=private_decision)
        )
        event_id = session.scalar(
            DomainEventRecord.__table__.select()
            .with_only_columns(DomainEventRecord.event_id)
            .where(DomainEventRecord.mandate_id == str(primary.mandate_id))
            .limit(1)
        )
        session.execute(
            update(DomainEventRecord)
            .where(DomainEventRecord.event_id == event_id)
            .values(
                department=private_resource,
                event_metadata={"references": [{"status": private_statement}]},
            )
        )
        session.execute(
            update(MeetingPackageRecord)
            .where(MeetingPackageRecord.mandate_id == str(meeting.mandate_id))
            .values(purpose=private_statement)
        )
        session.execute(
            update(RuntimeStatusRecord)
            .where(RuntimeStatusRecord.key == "public.person:maya-chen")
            .values(value=json.dumps({"name": "Maya Chen", "role": private_decision}))
        )
        session.commit()

    client = TestClient(demo_app)
    paths = [
        "/",
        "/mandates/HW-2411",
        "/mandates/HW-2411/reach",
        "/mandates/HW-2411/data",
        "/mandates/HW-2413",
        "/mandates/HW-2413/meeting.ics",
        "/api/v1/mandates",
        "/api/v1/mandates/HW-2411",
        "/api/v1/mandates/HW-2411/stakeholders",
        "/api/v1/mandates/HW-2411/outreach-events",
        "/api/v1/mandates/HW-2411/evidence-summary",
    ]
    responses = [client.get(path) for path in paths]
    serialized = "\n".join(
        response.text + json.dumps(dict(response.headers)) for response in responses
    )

    assert all(response.status_code == 200 for response in responses)
    assert private_statement not in serialized
    assert private_decision not in serialized
    assert private_resource not in serialized
    assert "[PRIVATE]" in serialized
    assert public_statement in serialized
    anonymous = client.get("/api/v1/mandates/HW-2411/evidence-summary").json()
    anonymous_item = next(
        item for item in anonymous["items"] if item["stakeholder_id"] is None
    )
    assert anonymous_item["statement"]
    assert anonymous_item["stakeholder_id"] is None


def test_private_common_word_does_not_damage_independently_public_prose(demo_app) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2411")
    assert mandate is not None
    evidence = repository.list_evidence(mandate.mandate_id)
    private = next(item for item in evidence if item.visibility == EvidenceVisibility.PRIVATE)
    shareable = next(
        item for item in evidence if item.visibility == EvidenceVisibility.SHAREABLE
    )
    distinctive = "The north conference room will be unavailable on Friday."
    public_statement = "Approved public coverage remains available."
    with repository._session_factory() as session:
        session.execute(
            update(EvidenceItemRecord)
            .where(EvidenceItemRecord.evidence_id == str(private.evidence_id))
            .values(statement="coverage", related_decision=distinctive)
        )
        session.execute(
            update(EvidenceItemRecord)
            .where(EvidenceItemRecord.evidence_id == str(shareable.evidence_id))
            .values(statement=public_statement)
        )
        session.execute(
            update(MandateRecord)
            .where(MandateRecord.mandate_id == str(mandate.mandate_id))
            .values(objective="coverage")
        )
        assignment_id = session.scalar(
            StakeholderAssignmentRecord.__table__.select()
            .with_only_columns(StakeholderAssignmentRecord.assignment_id)
            .where(StakeholderAssignmentRecord.mandate_id == str(mandate.mandate_id))
            .limit(1)
        )
        session.execute(
            update(StakeholderAssignmentRecord)
            .where(StakeholderAssignmentRecord.assignment_id == assignment_id)
            .values(reason=f"Discuss {distinctive} after lunch.")
        )
        event_id = session.scalar(
            DomainEventRecord.__table__.select()
            .with_only_columns(DomainEventRecord.event_id)
            .where(DomainEventRecord.mandate_id == str(mandate.mandate_id))
            .limit(1)
        )
        session.execute(
            update(DomainEventRecord)
            .where(DomainEventRecord.event_id == event_id)
            .values(event_metadata={"references": [{"status": distinctive}]})
        )
        session.commit()

    client = TestClient(demo_app)
    detail = client.get("/api/v1/mandates/HW-2411")
    stakeholders = client.get("/api/v1/mandates/HW-2411/stakeholders")
    events = client.get("/api/v1/mandates/HW-2411/outreach-events")
    summary = client.get("/api/v1/mandates/HW-2411/evidence-summary")
    serialized = "\n".join(
        response.text for response in (detail, stakeholders, events, summary)
    )

    assert all(
        response.status_code == 200 for response in (detail, stakeholders, events, summary)
    )
    assert detail.json()["objective"] == "[PRIVATE]"
    assert public_statement in serialized
    assert "Coverage requires a documented handoff." in serialized
    assert distinctive not in serialized
    assert "Discuss [PRIVATE] after lunch." in serialized
    assert events.json()[0]["metadata"] == {"references": [{"status": "[PRIVATE]"}]}


def test_private_token_and_meeting_id_are_removed_at_final_html_header_and_ics_boundary(
    demo_app,
) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2413")
    assert mandate is not None
    package = repository.get_meeting_package(mandate.mandate_id)
    evidence = repository.list_evidence(mandate.mandate_id)
    assignments = repository.list_assignments(mandate.mandate_id)
    assert package is not None and evidence and assignments
    meeting_id = str(package.meeting_id)
    repository.add_evidence(
        evidence[0].model_copy(
            update={
                "evidence_id": uuid4(),
                "assignment_id": assignments[0].assignment_id,
                "visibility": EvidenceVisibility.PRIVATE,
                "statement": mandate.token,
                "source_message_id": meeting_id,
                "related_decision": meeting_id,
            }
        )
    )

    client = TestClient(demo_app)
    html_response = client.get("/mandates/HW-2413")
    json_response = client.get("/api/v1/mandates/HW-2413")
    ics_response = client.get("/mandates/HW-2413/meeting.ics")
    serialized = "\n".join(
        response.text + json.dumps(dict(response.headers))
        for response in (html_response, json_response, ics_response)
    )

    assert html_response.status_code == 200
    assert json_response.status_code == 200
    assert ics_response.status_code == 200
    assert mandate.token not in serialized
    assert meeting_id not in serialized
    assert json_response.json()["token"] == "[PRIVATE]"
    assert "HumanWire [PRIVATE]" in html_response.text
    assert ics_response.headers["content-disposition"] == (
        'attachment; filename="humanwire-meeting.ics"'
    )
    assert "\r" not in ics_response.headers["content-disposition"]
    assert "\n" not in ics_response.headers["content-disposition"]
    assert "BEGIN:VCALENDAR" in ics_response.text
    assert "DTSTART:20260814T200000Z" in ics_response.text
    assert "SUMMARY:Resolve the launch approval decision" in ics_response.text
    assert (
        "UID:vJY2mP0JbTd-bRDXxYbHdnb9MGNM2YdfO2GeXyg1-hk@humanwire.local\r\n"
    ) in ics_response.text


def test_denied_meeting_ids_have_unique_stable_rfc_safe_calendar_uids() -> None:
    first_id = "11111111-1111-4111-8111-111111111111"
    second_id = "22222222-2222-4222-8222-222222222222"
    first = web_projection._public_calendar_uid(first_id, frozenset({first_id}))
    repeated = web_projection._public_calendar_uid(first_id, frozenset({first_id}))
    second = web_projection._public_calendar_uid(second_id, frozenset({second_id}))

    assert len(f"UID:{first}".encode()) <= 75
    assert first == expected_private_uid(first_id)
    assert repeated == first
    assert second == expected_private_uid(second_id)
    assert second != first
    assert first_id not in first
    assert second_id not in second
    assert all(character.isascii() for character in first + second)
    assert "\r" not in first + second
    assert "\n" not in first + second


def test_private_corpus_cannot_replace_ics_structure_or_property_names(demo_app) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2413")
    assert mandate is not None
    evidence = repository.list_evidence(mandate.mandate_id)
    assignments = repository.list_assignments(mandate.mandate_id)
    assert evidence and assignments
    repository.add_evidence(
        evidence[0].model_copy(
            update={
                "evidence_id": uuid4(),
                "assignment_id": assignments[0].assignment_id,
                "visibility": EvidenceVisibility.PRIVATE,
                "statement": "BEGIN:VCALENDAR",
                "related_decision": "VERSION:2.0",
                "resource": "BEGIN:VEVENT",
                "source_message_id": "END:VEVENT",
            }
        )
    )
    repository.add_evidence(
        evidence[0].model_copy(
            update={
                "evidence_id": uuid4(),
                "assignment_id": assignments[0].assignment_id,
                "visibility": EvidenceVisibility.PRIVATE,
                "statement": "END:VCALENDAR",
                "related_decision": "UID",
                "resource": "SUMMARY",
                "source_message_id": "DTSTART",
            }
        )
    )

    response = TestClient(demo_app).get("/mandates/HW-2413/meeting.ics")

    assert response.status_code == 200
    assert response.content.startswith(b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n")
    assert response.content.endswith(b"END:VEVENT\r\nEND:VCALENDAR\r\n")
    assert response.text.count("BEGIN:VCALENDAR\r\n") == 1
    assert response.text.count("BEGIN:VEVENT\r\n") == 1
    assert response.text.count("END:VEVENT\r\n") == 1
    assert response.text.count("END:VCALENDAR\r\n") == 1
    assert "\r\nUID:" in response.text
    assert "\r\nSUMMARY:" in response.text
    assert "\r\nDTSTART:" in response.text
    assert "\n" not in response.text.replace("\r\n", "")


def test_list_filter_stakeholders_events_evidence_and_reach_are_persisted_projections(
    web_client,
) -> None:
    mandates = web_client.get("/api/v1/mandates").json()
    aligned = web_client.get("/api/v1/mandates", params={"state": "aligned"}).json()
    stakeholders = web_client.get("/api/v1/mandates/HW-2411/stakeholders").json()
    events = web_client.get("/api/v1/mandates/HW-2411/outreach-events").json()
    evidence = web_client.get("/api/v1/mandates/HW-2411/evidence-summary").json()
    reach_html = web_client.get("/mandates/HW-2411/reach").text

    assert [item["token"] for item in mandates] == ["HW-2413", "HW-2412", "HW-2411"]
    assert [item["token"] for item in aligned] == ["HW-2412"]
    assert {(item["direction"], item["state"]) for item in stakeholders} >= {
        ("downward", "complete"),
        ("lateral", "interviewing"),
        ("upward", "complete"),
        ("upward", "awaiting_acknowledgement"),
    }
    assert len([item for item in stakeholders if item["direction"] == "downward" and item["state"] == "complete"]) == 3
    assert [item["created_at"] for item in events] == sorted(item["created_at"] for item in events)
    assert len(events) >= 12
    assert evidence["counts"] == {"shareable": 2, "anonymous": 1, "private_blockers": 1}
    assert evidence["items"][1]["stakeholder_id"] is None
    assert "Private medical leave details" not in json.dumps(evidence)
    assert all(lane in reach_html for lane in ("downward", "lateral", "upward"))


@pytest.mark.parametrize(
    "path",
    [
        "/mandates/HW-UNKNOWN",
        "/mandates/HW-UNKNOWN/reach",
        "/mandates/HW-UNKNOWN/data",
        "/mandates/HW-UNKNOWN/meeting.ics",
        "/api/v1/mandates/HW-UNKNOWN",
        "/api/v1/mandates/HW-UNKNOWN/stakeholders",
        "/api/v1/mandates/HW-UNKNOWN/outreach-events",
        "/api/v1/mandates/HW-UNKNOWN/evidence-summary",
    ],
)
def test_unknown_tokens_return_safe_404(path, web_client) -> None:
    response = web_client.get(path)

    assert response.status_code == 404
    assert response.content == b""
    assert "HW-UNKNOWN" not in response.text
    assert "sql" not in response.text.lower()


def test_known_private_error_phrase_cannot_collide_with_bodyless_404_routes(demo_app) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2411")
    assert mandate is not None
    private = next(
        item
        for item in repository.list_evidence(mandate.mandate_id)
        if item.visibility == EvidenceVisibility.PRIVATE
    )
    with repository._session_factory() as session:
        session.execute(
            update(EvidenceItemRecord)
            .where(EvidenceItemRecord.evidence_id == str(private.evidence_id))
            .values(statement="Not found")
        )
        session.commit()

    client = TestClient(demo_app)
    paths = [
        "/mandates/HW-2411/meeting.ics",
        "/mandates/HW-UNKNOWN",
        "/mandates/HW-UNKNOWN/reach",
        "/mandates/HW-UNKNOWN/data",
        "/mandates/HW-UNKNOWN/meeting.ics",
        "/api/v1/mandates/HW-UNKNOWN",
        "/api/v1/mandates/HW-UNKNOWN/stakeholders",
        "/api/v1/mandates/HW-UNKNOWN/outreach-events",
        "/api/v1/mandates/HW-UNKNOWN/evidence-summary",
    ]
    responses = [client.get(path) for path in paths]

    assert all(response.status_code == 404 for response in responses)
    assert all(response.content == b"" for response in responses)
    assert all("content-type" not in response.headers for response in responses)
    assert "Not found" not in "".join(response.text for response in responses)


def test_health_separates_liveness_from_production_readiness(demo_app) -> None:
    repository = demo_app.state.repository
    settings = Settings(
        _env_file=None,
        caspian_api_key="fictional-key",
        telegram_bot_token="fictional-token",
        due_action_poll_seconds=5,
    )
    repository.set_runtime_status("channel.email", "ready", NOW)
    repository.set_runtime_status("channel.telegram", "ready", NOW)
    repository.set_runtime_status("listener.heartbeat", "alive", NOW)
    client = TestClient(create_app(repository, settings, clock=lambda: NOW))

    assert client.get("/health/live").json() == {"status": "live"}
    assert client.get("/health/ready").json() == {"status": "ready"}

    repository.set_runtime_status("listener.heartbeat", "alive", NOW - timedelta(seconds=31))
    stale = client.get("/health/ready")
    assert stale.status_code == 503
    assert stale.json() == {"status": "not_ready", "reason": "listener_unavailable"}
    assert client.get("/health/live").status_code == 200


def test_production_readiness_requires_configuration_and_channel_state(demo_app) -> None:
    repository = demo_app.state.repository
    client = TestClient(
        create_app(repository, Settings(_env_file=None), clock=lambda: NOW)
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "reason": "configuration_unavailable"}


@pytest.mark.parametrize(
    ("configured", "authorization", "expected_status"),
    [
        ("", "Bearer ", 401),
        ("   ", "Bearer    ", 401),
        ("fictional-read-token", None, 401),
        ("fictional-read-token", "Bearer wrong-token", 401),
        ("fictional-read-token", "Bearer fictional-read-token", 200),
    ],
)
def test_production_analytics_fail_closed_for_blank_or_invalid_bearer_tokens(
    demo_app, configured, authorization, expected_status
) -> None:
    repository = demo_app.state.repository
    settings = Settings(
        _env_file=None,
        analytics_read_token=configured,
    )
    client = TestClient(create_app(repository, settings, clock=lambda: NOW))

    response = client.get(
        "/api/v1/mandates",
        headers={"Authorization": authorization} if authorization is not None else {},
    )

    assert response.status_code == expected_status
    assert configured not in response.text if configured else True


def test_demo_analytics_remain_anonymous(web_client) -> None:
    assert web_client.get("/api/v1/mandates").status_code == 200


class FailingRepository:
    def __getattr__(self, name):
        del name
        raise RuntimeError("sqlite:///private/path?token=secret")


def test_database_failure_is_safe_for_readiness_and_api() -> None:
    client = TestClient(
        create_app(
            FailingRepository(),
            Settings(
                _env_file=None,
                caspian_api_key="fictional-key",
                telegram_bot_token="fictional-token",
                analytics_read_token="fictional-read-token",
            ),
            clock=lambda: NOW,
        )
    )

    ready = client.get("/health/ready")
    api = client.get(
        "/api/v1/mandates",
        headers={"Authorization": "Bearer fictional-read-token"},
    )

    assert ready.status_code == 503
    assert ready.json() == {"status": "not_ready", "reason": "database_unavailable"}
    assert api.status_code == 503
    assert api.json() == {"detail": "Service unavailable"}
    assert "private/path" not in ready.text + api.text
    assert "secret" not in ready.text + api.text


def test_ics_requires_persisted_verified_meeting_ready_package(web_client) -> None:
    ready = web_client.get("/mandates/HW-2413/meeting.ics")
    not_ready = web_client.get("/mandates/HW-2411/meeting.ics")

    assert ready.status_code == 200
    assert ready.headers["content-type"].startswith("text/calendar")
    assert ready.headers["content-disposition"] == 'attachment; filename="HW-2413-meeting.ics"'
    assert "BEGIN:VCALENDAR" in ready.text
    assert "DTSTART:20260814T200000Z" in ready.text
    assert not_ready.status_code == 404
    assert "verified" not in not_ready.text.lower()


def test_ics_fails_closed_when_persisted_verification_is_missing(demo_app) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2413")
    assert mandate is not None
    repository.set_runtime_status(
        f"availability:{mandate.mandate_id}:maya-chen", "malformed", NOW
    )
    client = TestClient(demo_app)

    response = client.get("/mandates/HW-2413/meeting.ics")

    assert response.status_code == 404
    assert "malformed" not in response.text


def test_ics_rejects_package_creation_timestamp_not_proven_by_event(demo_app) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2413")
    assert mandate is not None
    package = repository.get_meeting_package(mandate.mandate_id)
    assert package is not None
    repository.save_meeting_package(
        package.model_copy(update={"created_at": package.created_at + timedelta(hours=2)})
    )

    response = TestClient(demo_app).get("/mandates/HW-2413/meeting.ics")

    assert response.status_code == 404


def test_ics_rejects_missing_package_creation_event(demo_app) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2413")
    assert mandate is not None
    with repository._session_factory() as session:
        session.execute(
            delete(DomainEventRecord).where(
                DomainEventRecord.mandate_id == str(mandate.mandate_id),
                DomainEventRecord.event_type == "meeting.package_created",
            )
        )
        session.commit()

    response = TestClient(demo_app).get("/mandates/HW-2413/meeting.ics")

    assert response.status_code == 404


def test_ics_rejects_duplicate_conflicting_package_creation_events(demo_app) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2413")
    assert mandate is not None
    package = repository.get_meeting_package(mandate.mandate_id)
    assert package is not None
    repository.append_event(
        mandate.mandate_id,
        DomainEvent(
            event_type="meeting.package_created",
            created_at=package.created_at + timedelta(minutes=1),
            idempotency_key="duplicate-package-creation-proof",
            actor_id=mandate.initiator_id,
            metadata={"meeting_id": str(package.meeting_id)},
        ),
    )

    response = TestClient(demo_app).get("/mandates/HW-2413/meeting.ics")

    assert response.status_code == 404


def test_ics_rejects_package_owner_substitution_even_with_matching_attendees_and_availability(
    demo_app,
) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2413")
    assert mandate is not None
    package = repository.get_meeting_package(mandate.mandate_id)
    assert package is not None
    intruder = "intruder-fictional"
    changed = package.model_copy(
        update={
            "decision_owner_id": intruder,
            "required_attendee_ids": sorted([*package.required_attendee_ids, intruder]),
        }
    )
    repository.save_meeting_package(changed)
    window = AvailabilityWindow(
        start=datetime(2026, 8, 14, 20, 0, tzinfo=UTC),
        end=datetime(2026, 8, 14, 21, 0, tzinfo=UTC),
    )
    repository.set_runtime_status(
        f"availability:{mandate.mandate_id}:{intruder}",
        json_windows(type("Command", (), {"windows": [window]})()),
        NOW,
    )

    response = TestClient(demo_app).get("/mandates/HW-2413/meeting.ics")

    assert response.status_code == 404
    assert intruder not in response.text


def test_ics_rejects_missing_persisted_evidence(demo_app) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2413")
    assert mandate is not None
    with repository._session_factory() as session:
        session.execute(
            delete(EvidenceItemRecord).where(
                EvidenceItemRecord.mandate_id == str(mandate.mandate_id)
            )
        )
        session.commit()

    response = TestClient(demo_app).get("/mandates/HW-2413/meeting.ics")

    assert response.status_code == 404


def test_ics_rejects_availability_changed_after_package_creation(demo_app) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2413")
    assert mandate is not None
    package = repository.get_meeting_package(mandate.mandate_id)
    assert package is not None
    attendee_id = package.required_attendee_ids[0]
    stored = repository.get_runtime_status(
        f"availability:{mandate.mandate_id}:{attendee_id}"
    )
    assert stored is not None
    repository.set_runtime_status(
        f"availability:{mandate.mandate_id}:{attendee_id}",
        stored[0],
        package.created_at + timedelta(seconds=1),
    )

    response = TestClient(demo_app).get("/mandates/HW-2413/meeting.ics")

    assert response.status_code == 404


@pytest.mark.parametrize(
    "updates",
    [
        {"required_attendee_ids": ["maya-chen"]},
        {"purpose": "Altered package purpose"},
        {
            "proposed_start": datetime(2026, 8, 14, 20, 30, tzinfo=UTC),
            "proposed_end": datetime(2026, 8, 14, 21, 0, tzinfo=UTC),
        },
    ],
)
def test_ics_rejects_removed_attendee_or_altered_package_fields(demo_app, updates) -> None:
    repository = demo_app.state.repository
    mandate = repository.get_mandate_by_token("HW-2413")
    assert mandate is not None
    package = repository.get_meeting_package(mandate.mandate_id)
    assert package is not None
    repository.save_meeting_package(package.model_copy(update=updates))

    response = TestClient(demo_app).get("/mandates/HW-2413/meeting.ics")

    assert response.status_code == 404
