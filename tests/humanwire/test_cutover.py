import importlib
import importlib.metadata
import importlib.util
import re
import sys
import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_URL = "https://secondsignal.vercel.app"
PUBLIC_HOST = "secondsignal.vercel.app"
LEGACY_PATHS = (
    "src/secondsignal",
    "tests/conftest.py",
    "scripts/capture_telegram_route.py",
    "scripts/seed_demo_registry.py",
    "scripts/smoke_check.py",
    "config/demo-identities.example.json",
    "submission/devpost.md",
)
SUBMISSIONS = (
    "submission/caspian.md",
    "submission/ml-empowerment.md",
    "submission/build-beyond.md",
)
ENVIRONMENT_KEYS = {
    "CASPIAN_API_KEY",
    "CASPIAN_BASE_URL",
    "TELEGRAM_BOT_TOKEN",
    "CASPIAN_EMAIL_USERNAME",
    "FEATHERLESS_API_KEY",
    "ANALYTICS_READ_TOKEN",
    "FEATHERLESS_BASE_URL",
    "FEATHERLESS_MODEL",
    "DATABASE_URL",
    "ORGANIZATION_PATH",
    "ACKNOWLEDGEMENT_SECONDS",
    "REMINDER_SECONDS",
    "MANDATE_TIMEOUT_SECONDS",
    "ENGAGEMENT_PREVIEW_SECONDS",
    "ENGAGEMENT_REQUIRE_GO",
    "DUE_ACTION_POLL_SECONDS",
    "DASHBOARD_HOST",
    "DASHBOARD_PORT",
    "PUBLIC_DEMO",
}

DETERMINISTIC_WATCH_COMMAND = """# Deterministic, no external model/provider call
.\\.venv\\Scripts\\python.exe -m humanwire synthetic watch `
  --agent-mode deterministic `
  --seed 8842 `
  --run-root work\\synthetic-watch-8842 `
  --output work\\synthetic-watch-8842\\transcript.json"""

FEATHERLESS_WATCH_COMMAND = """# Explicit private exploratory Featherless mode; reads only configured Featherless settings
.\\.venv\\Scripts\\python.exe -m humanwire synthetic watch `
  --agent-mode featherless `
  --seed 8842 `
  --run-root work\\synthetic-model-8842 `
  --output work\\synthetic-model-8842\\transcript.json"""


def test_vercel_entrypoint_serves_the_isolated_humanwire_demo() -> None:
    sys.modules.pop("index", None)
    app = importlib.import_module("index").app
    client = TestClient(app)

    home = client.get("/")

    assert home.status_code == 200
    assert "HumanWire" in home.text
    assert "SecondSignal" not in home.text
    assert client.get("/mandates/HW-2411").status_code == 200
    assert client.get("/mandates/HW-2411/reach").status_code == 200
    assert client.get("/mandates/HW-2411/data").status_code == 200
    assert client.get("/health/live").json() == {"status": "live"}
    assert client.get("/health/ready").json() == {"status": "ready", "mode": "demo"}


def test_installed_distribution_exposes_only_the_humanwire_product() -> None:
    distribution = importlib.metadata.distribution("humanwire")
    scripts = {
        entry.name: entry.value
        for entry in distribution.entry_points
        if entry.group == "console_scripts"
    }
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert scripts == {"humanwire": "humanwire.__main__:main"}
    assert importlib.util.find_spec("humanwire") is not None
    assert importlib.util.find_spec("secondsignal") is None
    assert project["project"]["description"] == (
        "AI chief of staff for adaptive human coordination"
    )
    assert project["tool"]["setuptools"]["package-data"] == {
        "humanwire": [
            "templates/*.html",
            "static/*.css",
            "static/*.js",
            "viewer_static/*.js",
        ]
    }


def test_public_copy_and_environment_describe_the_adaptive_product_truthfully() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "HumanWire" in readme
    assert "minimum necessary engagement" in readme.casefold()
    assert PUBLIC_URL in readme
    for engagement_type in (
        "INFORM",
        "ACKNOWLEDGE",
        "QUICK_RESPONSE",
        "STRUCTURED_INTERVIEW",
        "REVIEW_APPROVAL",
        "AVAILABILITY",
    ):
        assert engagement_type in readme

    for relative_path in SUBMISSIONS:
        copy = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "HumanWire" in copy
        assert "minimum necessary engagement" in copy.casefold()
        assert "does not interview everyone" in copy.casefold()
        assert "calendar" in copy.casefold()
        assert PUBLIC_URL in copy

    environment = dict(
        line.split("=", 1)
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    assert set(environment) == ENVIRONMENT_KEYS
    for secret in (
        "CASPIAN_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "FEATHERLESS_API_KEY",
        "ANALYTICS_READ_TOKEN",
    ):
        assert environment[secret] == ""


def test_cutover_removes_every_obsolete_product_surface() -> None:
    for relative_path in LEGACY_PATHS:
        assert not (ROOT / relative_path).exists()
    assert not list((ROOT / "tests").glob("test_*.py"))

    scanned = []
    for relative_root in ("src", "tests", "scripts", "config", "docs", "submission"):
        for path in (ROOT / relative_root).rglob("*"):
            if not path.is_file() or "docs/superpowers" in path.as_posix():
                continue
            if path == Path(__file__).resolve() or path.suffix not in {".py", ".md", ".html"}:
                continue
            scanned.append(path.read_text(encoding="utf-8"))
    scanned.extend(
        (ROOT / name).read_text(encoding="utf-8")
        for name in ("README.md", "pyproject.toml", ".env.example")
    )
    public_text = "\n".join(scanned).replace(PUBLIC_URL, "").replace(PUBLIC_HOST, "")
    assert "secondsignal" not in public_text.casefold()
    assert "SecondSignal" not in public_text
    assert re.search(r"\bSS-[A-Z0-9]", public_text) is None


def test_agent_runtime_docs_preserve_exact_proof_boundary() -> None:
    text = (ROOT / "docs/synthetic-agent-runtime.md").read_text(encoding="utf-8")
    for label in (
        "proof_class=synthetic_multi_persona",
        "actor_type=simulated_persona",
        "identity_source=synthetic_fixture",
        "transport=fake_caspian",
        "human_attested=false",
        "live_provider_verified=false",
    ):
        assert label in text
    assert "127.0.0.1" in text
    assert "not live Caspian, email, Telegram, Featherless, or human proof" in text
    assert "The public Vercel demo cannot start a simulation" in text


def test_agent_runtime_readme_preserves_exact_watch_commands() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert DETERMINISTIC_WATCH_COMMAND in readme
    assert FEATHERLESS_WATCH_COMMAND in readme


def test_agent_runtime_claim_ledger_classifies_local_viewer_only() -> None:
    claims = (ROOT / "submission/verified-claims.md").read_text(encoding="utf-8")
    viewer_rows = [
        tuple(cell.strip() for cell in line.strip().strip("|").split("|"))
        for line in claims.splitlines()
        if line.startswith("| `humanwire synthetic watch`")
    ]

    assert len(viewer_rows) == 1
    wording, sources, proof_class, prohibited_claims = viewer_rows[0]
    assert wording.startswith("`humanwire synthetic watch` presents a literal-loopback")
    assert "runtime operator guide" in sources
    assert proof_class == "local synthetic proof; not live-provider proof"
    assert "live Caspian/email/Telegram was exercised" in prohibited_claims
    assert "real people participated" in prohibited_claims
