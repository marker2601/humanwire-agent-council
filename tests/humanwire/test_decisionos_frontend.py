from __future__ import annotations

import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "src" / "humanwire" / "templates"
STATIC = ROOT / "src" / "humanwire" / "decisionos_static"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class _HTMLFacts(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []
        self.resources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"script", "link"}:
            return
        values = dict(attrs)
        resource = values.get("src") or values.get("href")
        if resource:
            self.resources.append(resource)

    def handle_data(self, data: str) -> None:
        normalized = " ".join(data.split())
        if normalized:
            self.text.append(normalized)


def _visible_text(path: Path) -> str:
    facts = _HTMLFacts()
    facts.feed(_source(path))
    return " ".join(facts.text)


def test_signed_out_page_has_one_clear_authentication_path() -> None:
    path = TEMPLATES / "decisionos_login.html"
    source = _source(path)
    visible = _visible_text(path)

    assert "HumanWire DecisionOS" in visible
    assert "Make the decision. Keep the evidence." in visible
    assert "Sign in with Google" in visible
    assert "Use email link" in visible
    assert "people retain approval authority" in visible
    assert "Firebase" not in visible
    assert source.count("data-sign-in-google") == 1
    assert "data-email-link" in source
    assert "data-organization-onboarding" in source


def test_workspace_shell_has_real_navigation_and_authority_regions() -> None:
    path = TEMPLATES / "decisionos_shell.html"
    source = _source(path)
    visible = _visible_text(path)

    for label in ("Home", "Decisions", "Evidence", "Team"):
        assert f'data-panel-target="{label.casefold()}"' in source
        assert label in visible
    for label in (
        "New decision",
        "Invite teammate",
        "Launch decision",
        "Fundraising readiness",
        "Agent Council",
        "Market Intelligence",
        "Financial Analysis",
        "Decision Synthesis",
        "Red Team",
        "Start mission",
        "Evidence that changes the decision",
        "Decision authority",
        "Human approval required",
        "Load demo company",
        "Demo run",
        "Connected organization",
        "AI operating team",
        "Market Intelligence AI",
        "Financial Analysis AI",
        "Product & Technical AI",
        "Risk & Compliance AI",
        "Decision Synthesis AI",
        "Red Team AI",
        "Final Synthesis AI",
    ):
        assert label in visible
    assert "data-organization-list" in source
    assert "data-workspace-list" in source
    assert "data-create-organization" in source
    assert "data-create-workspace" in source
    assert "data-invite-member" in source
    assert "data-sign-out" in source
    assert source.count("data-agent-profile=") == 7
    assert "data-demo-evidence" in source
    assert "data-evidence-list" in source
    assert "data-latest-decision" in source
    assert 'data-mission-mode="demo_run"' in source
    assert 'data-mission-mode="connected_organization"' in source
    assert "data-mission-form" in source
    assert "data-mission-workspace" in source
    assert "data-mission-participants" in source
    assert "data-mission-timeline" in source
    assert "Synthetic demo evidence" not in visible
    assert "fabricated" not in visible.casefold()


def test_css_matches_the_locked_decisionos_design_system() -> None:
    source = _source(STATIC / "decisionos.css")

    assert "--decisionos-ink: #06152f" in source
    assert "--decisionos-cyan: #06b9ed" in source
    assert "--decisionos-white: #ffffff" in source
    assert "min-height: 44px" in source
    assert ":focus-visible" in source
    assert "prefers-reduced-motion: reduce" in source
    assert "@media (max-width: 759px)" in source
    assert "overflow-x: hidden" not in source
    assert "font-size: 1" in source


def test_auth_controller_never_uses_browser_storage_or_logs_credentials() -> None:
    source = _source(STATIC / "decisionos-auth.js")

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "console.log",
        "console.error",
        "innerHTML",
        "document.write",
    ):
        assert forbidden not in source
    assert '"/api/session/login"' in source
    assert "X-Firebase-AppCheck" in source
    assert "credentials.idToken = \"\"" in source
    assert "location.assign(\"/workspace\")" in source


def test_app_controller_targets_only_real_protected_routes() -> None:
    source = _source(STATIC / "decisionos-app.js")

    for route in (
        "/api/organizations",
        "/api/session/logout",
        "/api/invitations/accept",
        "/workspaces",
        "/invitations",
    ):
        assert route in source
    for forbidden in (
        "localStorage",
        "sessionStorage",
        "console.log",
        "console.error",
        "innerHTML",
        "document.write",
    ):
        assert forbidden not in source
    assert "X-HumanWire-CSRF" in source
    assert "X-Firebase-AppCheck" in source
    assert "aria-selected" in source
    for name in (
        "createMission",
        "runMission",
        "consumeMissionStream",
        "renderMission",
        "resetMission",
    ):
        assert f"function {name}" in source
    assert "recipient" not in source
    assert "mission_stream_ended" in source


def test_frontend_build_is_pinned_and_produces_a_local_firebase_adapter() -> None:
    package = json.loads(_source(ROOT / "package.json"))
    build = _source(ROOT / "scripts" / "build_decisionos_frontend.mjs")

    assert package["scripts"]["build:decisionos"] == "node scripts/build_decisionos_frontend.mjs"
    assert package["dependencies"]["firebase"].count(".") == 2
    assert not package["dependencies"]["firebase"].startswith(("^", "~"))
    assert package["devDependencies"]["esbuild"].count(".") == 2
    assert "decisionos_static/firebase-adapter.js" in build.replace("\\", "/")
    assert "initializeAppCheck" in build
    assert "ReCaptchaEnterpriseProvider" in build


def test_login_defers_app_check_enforcement_to_the_server_boundary() -> None:
    build = _source(ROOT / "scripts" / "build_decisionos_frontend.mjs")

    assert "result.user.getIdToken(true)" in build
    assert "async function optionalAppCheckToken()" in build
    assert "return (await getToken(state.appCheck, false)).token;" in build
    assert 'return "";' in build
    assert "appCheckToken: await optionalAppCheckToken()" in build
    assert "result?.user || current.auth.currentUser" in build


def test_templates_load_only_local_scripts_and_styles() -> None:
    for name in ("decisionos_login.html", "decisionos_shell.html"):
        facts = _HTMLFacts()
        facts.feed(_source(TEMPLATES / name))
        assert facts.resources
        assert all(item.startswith("/decisionos-static/") for item in facts.resources)


def test_google_popup_returns_into_the_server_session_boundary() -> None:
    completed = subprocess.run(
        ["node", "tests/humanwire/decisionos_frontend_harness.js"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "decisionos frontend harness: PASS\n"


def test_council_frontend_paces_activity_and_synchronizes_team_and_decision() -> None:
    completed = subprocess.run(
        ["node", "tests/humanwire/decisionos_council_demo_harness.js"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "decisionos council demo harness: PASS\n"


def test_mission_frontend_keeps_mode_stream_and_reset_truthful() -> None:
    completed = subprocess.run(
        ["node", "tests/humanwire/decisionos_mission_harness.js"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "decisionos mission harness: PASS\n"


def test_mission_frontend_shows_truthful_live_progress_without_a_blank_wait() -> None:
    template = _source(TEMPLATES / "decisionos_shell.html")
    styles = _source(STATIC / "decisionos.css")
    controller = _source(STATIC / "decisionos-app.js")

    for selector in (
        "data-mission-progress",
        "data-mission-progress-meter",
        "data-mission-progress-summary",
        "data-mission-elapsed",
        "data-mission-pulse",
    ):
        assert selector in template
    for stage in ("outreach", "analysis", "synthesis", "evidence", "decision"):
        assert f'data-mission-step="{stage}"' in template
    assert "aria-live=\"polite\"" in template
    assert "aria-busy" in controller
    assert "Still working" in controller
    assert "prefers-reduced-motion: reduce" in styles

    completed = subprocess.run(
        ["node", "tests/humanwire/decisionos_mission_experience_harness.js"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "decisionos mission experience harness: PASS\n"
