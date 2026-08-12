# HumanWire Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace SecondSignal with a competition-ready HumanWire product that turns one authorized mandate into live cross-channel stakeholder interviews, evidence-backed alignment or a meeting-ready package, a responsive Decision Room, Propagation Lanes, and analytics-ready event data.

**Architecture:** Build a new `humanwire` Python package beside the currently working `secondsignal` package so the existing deployment remains recoverable until cutover. A single Caspian handler routes Telegram and email into focused mandate, interview, synthesis, negotiation, and meeting services backed by one SQLite repository and append-only event log. FastAPI renders read-only operational views and APIs from the same records; the final cutover changes the CLI and Vercel entrypoint only after full end-to-end verification.

**Tech Stack:** Python 3.12, `caspian-sdk==0.6.1`, FastAPI, Pydantic v2, pydantic-settings, SQLAlchemy 2, SQLite, Jinja2, vanilla JavaScript/CSS, Featherless OpenAI-compatible chat completions, httpx, pytest, pytest-asyncio, Ruff, Vercel public demo deployment.

## Global Constraints

- Use one Caspian `on_message` handler for both Telegram and email.
- Keep real contact destinations only in an ignored local organization file; commit fictional examples only.
- Model output is untrusted, schema-validated, and unable to create destinations, accept proposals, approve decisions, or mutate state directly.
- Silence, delivery failure, ambiguity, and missing evidence never become agreement or approval.
- Negotiation stops after two rounds.
- Upward outreach asks for sponsorship or approval; it never implies that a lower-level initiator has executive authority.
- Private evidence content is excluded from shared briefs, public views, logs, exports, and shared model prompts.
- The Decision Room and APIs visualize persisted state; they do not become an alternate source of truth.
- Propagation Lanes are the default Reach visualization; a traditional org chart is not required for competition completion.
- External calendar mutation remains disabled unless a real connector is configured; the default output is calendar-ready metadata and a downloadable artifact.
- Build a fresh HumanWire database at `sqlite:///data/humanwire.db`; never reinterpret existing SecondSignal rows as HumanWire mandates.
- Preserve the working `secondsignal` package and `src/index.py` import until HumanWire integration and web tests pass.
- Every task uses test-first development, runs focused tests plus the relevant regression slice, and ends with a focused commit.
- Never commit `.env`, `.env.local`, `.vercel`, `data/organization.json`, database files, API keys, Telegram tokens, email addresses, or private interview content.
- Run Python commands with `.\.venv\Scripts\python.exe` from the repository root.
- Run `git diff --check`, Ruff, and the full test suite before deployment or completion claims.

---

## File Map

```text
pyproject.toml                              HumanWire package metadata and CLI
.env.example                               Safe environment variable names
config/demo-organization.example.json      Fictional organization and registered routes
src/index.py                               Vercel HumanWire demo entrypoint after cutover
src/humanwire/__init__.py                  Package version
src/humanwire/__main__.py                  init-db, listen, web, and smoke CLI
src/humanwire/config.py                    Settings and credential validation
src/humanwire/domain.py                    Shared enums and aggregate models
src/humanwire/commands.py                  Deterministic channel command parser
src/humanwire/directory.py                 People, routes, hierarchy, and authority policy
src/humanwire/database.py                  SQLAlchemy records and session factory
src/humanwire/repository.py                Aggregate persistence and append-only events
src/humanwire/state_machine.py             Mandate and stakeholder transition policy
src/humanwire/model_client.py              Featherless JSON client with safe fallback plumbing
src/humanwire/planning.py                  Mandate planning and directory resolution
src/humanwire/evidence.py                  Evidence extraction, privacy, and confirmation
src/humanwire/interviews.py                Interview state and response-ladder orchestration
src/humanwire/alignment.py                 Conflict detection and two-round negotiation
src/humanwire/meetings.py                  Availability overlap and meeting-ready packages
src/humanwire/messages.py                  Channel-neutral prompt and brief rendering
src/humanwire/services.py                  Mandate creation and synthesis coordination
src/humanwire/workflow.py                  Top-level incoming-message router
src/humanwire/caspian_gateway.py            One-handler Caspian adapter and delivery dispatcher
src/humanwire/container.py                  Dependency composition and due-action worker
src/humanwire/logging_config.py             Safe structured logs
src/humanwire/redaction.py                  Sensitive-text redaction
src/humanwire/web.py                        FastAPI pages, JSON API, and CSV responses
src/humanwire/demo.py                       Deterministic public demo fixture
src/humanwire/templates/base.html           Application shell
src/humanwire/templates/dashboard.html      Mandate list
src/humanwire/templates/mandate.html        Decision Room
src/humanwire/templates/reach.html           Propagation Lanes
src/humanwire/templates/data.html            Technical event table
src/humanwire/static/styles.css              Responsive visual system
src/humanwire/static/app.js                  Polling, countdown, replay, and filters
scripts/seed_humanwire_organization.py       Local registered-route seed helper
scripts/smoke_humanwire.py                   Safe offline and optional live smoke checks
tests/humanwire/conftest.py                  HumanWire factories and in-memory repository fixture
tests/humanwire/test_config.py                Settings and credential validation
tests/humanwire/test_commands.py             Parser behavior
tests/humanwire/test_directory.py            Resolution and authority behavior
tests/humanwire/test_state_machine.py         Transition invariants
tests/humanwire/test_repository.py            Persistence and due-action queries
tests/humanwire/test_planning.py              Featherless and fallback planning
tests/humanwire/test_evidence.py              Extraction and privacy
tests/humanwire/test_interviews.py            Interview and response ladder
tests/humanwire/test_alignment.py             Conflicts and negotiation limits
tests/humanwire/test_meetings.py              Availability and meeting package
tests/humanwire/test_workflow.py              End-to-end application routing
tests/humanwire/test_caspian_gateway.py        One-handler channel integration
tests/humanwire/test_web.py                    Pages, APIs, exports, and redaction
tests/humanwire/test_demo.py                   Public fixture integrity
docs/architecture.md                          HumanWire architecture after cutover
docs/threat-model.md                          HumanWire trust and privacy model
docs/demo-script.md                           Live 75–90 second story
submission/caspian.md                          Caspian-specific submission copy
submission/ml-empowerment.md                   ML Empowerment submission copy
submission/build-beyond.md                     Build Beyond submission copy
README.md                                     Public HumanWire setup and proof
```

## Canonical Domain Contract

All tasks use these exact enum values and field names. Implement them in `src/humanwire/domain.py`; later tasks may add validators and computed properties but may not rename the contract.

```python
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class Channel(StrEnum):
    EMAIL = "email"
    TELEGRAM = "telegram"


class Direction(StrEnum):
    DOWNWARD = "downward"
    LATERAL = "lateral"
    UPWARD = "upward"
    EXTERNAL = "external"


class MandateState(StrEnum):
    RECEIVED = "received"
    PLANNED = "planned"
    INTERVIEWING = "interviewing"
    SYNTHESIZING = "synthesizing"
    NEGOTIATING = "negotiating"
    ALIGNED = "aligned"
    MEETING_REQUIRED = "meeting_required"
    SCHEDULING = "scheduling"
    MEETING_READY = "meeting_ready"
    PARTIAL = "partial"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    DELIVERY_FAILED = "delivery_failed"


class StakeholderState(StrEnum):
    NOT_CONTACTED = "not_contacted"
    CONTACT_QUEUED = "contact_queued"
    DELIVERED = "delivered"
    AWAITING_ACKNOWLEDGEMENT = "awaiting_acknowledgement"
    ACKNOWLEDGED = "acknowledged"
    INTERVIEWING = "interviewing"
    COMPLETE = "complete"
    FOLLOW_UP_DUE = "follow_up_due"
    ALTERNATE_CHANNEL = "alternate_channel"
    DECLINED = "declined"
    UNREACHABLE = "unreachable"
    DELIVERY_FAILED = "delivery_failed"


class EvidenceType(StrEnum):
    FACT = "fact"
    CONSTRAINT = "constraint"
    CONCERN = "concern"
    PREFERENCE = "preference"
    COMMITMENT = "commitment"
    AVAILABILITY = "availability"
    DECISION = "decision"


class EvidenceVisibility(StrEnum):
    SHAREABLE = "shareable"
    ANONYMOUS = "anonymous"
    PRIVATE = "private"


class EvidenceStatus(StrEnum):
    ASSERTED = "asserted"
    CLARIFIED = "clarified"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"
    WITHDRAWN = "withdrawn"


class AlignmentIssueType(StrEnum):
    AGREEMENT = "agreement"
    CONTRADICTION = "contradiction"
    RESOURCE_CONFLICT = "resource_conflict"
    DEADLINE_CONFLICT = "deadline_conflict"
    MISSING_EVIDENCE = "missing_evidence"
    AUTHORITY_GAP = "authority_gap"
    HARD_CONSTRAINT = "hard_constraint"
    PRIVATE_BLOCKER = "private_blocker"


class ProposalResponseKind(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    CHANGE = "change"


class ProposalState(StrEnum):
    AWAITING_RESPONSES = "awaiting_responses"
    ALIGNED = "aligned"
    REJECTED = "rejected"
    UNRESOLVED = "unresolved"


class DeliveryKind(StrEnum):
    REPLY_TO_MESSAGE = "reply_to_message"
    SEND_TO_CONVERSATION = "send_to_conversation"
    INITIATE_EMAIL = "initiate_email"


class ContactRoute(BaseModel):
    route_id: str
    channel: Channel
    sender_address: str
    recipient: str | None = None
    conversation_id: str | None = None
    preferred: bool = False


class Person(BaseModel):
    person_id: str
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    role: str
    department: str
    timezone: str
    manager_id: str | None = None
    routes: list[ContactRoute] = Field(default_factory=list)


class IncomingMessage(BaseModel):
    message_id: str
    conversation_id: str
    connection_id: str
    channel: Channel
    sender_address: str
    sender_name: str | None = None
    subject: str | None = None
    text: str
    received_at: datetime


class PlannedStakeholder(BaseModel):
    person_ref: str
    reason: str
    direction: Direction
    required: bool = True
    questions: list[str] = Field(min_length=1, max_length=5)


class MandatePlan(BaseModel):
    objective: str
    required_decisions: list[str] = Field(min_length=1)
    stakeholders: list[PlannedStakeholder] = Field(min_length=1)
    deadline: datetime | None = None
    completion_conditions: list[str] = Field(min_length=1)


class Mandate(BaseModel):
    mandate_id: UUID
    token: str
    initiator_id: str
    origin_channel: Channel
    origin_conversation_id: str
    origin_message_id: str
    redacted_request: str
    objective: str
    plan: MandatePlan
    state: MandateState
    reason: str | None = None
    next_action_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    completed_at: datetime | None = None
    idempotency_key: str


class StakeholderAssignment(BaseModel):
    assignment_id: UUID
    mandate_id: UUID
    person_id: str
    department: str
    direction: Direction
    reason: str
    required: bool
    state: StakeholderState
    route_ids: list[str]
    active_route_index: int = 0
    attempt_count: int = 0
    interview_id: UUID | None = None
    first_contact_at: datetime | None = None
    last_delivery_at: datetime | None = None
    next_action_at: datetime | None = None
    acknowledged_at: datetime | None = None
    completed_at: datetime | None = None
    failure_reason: str | None = None


class InterviewSession(BaseModel):
    session_id: UUID
    mandate_id: UUID
    assignment_id: UUID
    questions: list[str] = Field(min_length=1, max_length=5)
    current_question_index: int = 0
    current_channel: Channel | None = None
    channel_history: list[Channel] = Field(default_factory=list)
    default_visibility: EvidenceVisibility = EvidenceVisibility.SHAREABLE
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class EvidenceItem(BaseModel):
    evidence_id: UUID
    mandate_id: UUID
    assignment_id: UUID
    stakeholder_id: str
    evidence_type: EvidenceType
    statement: str = Field(min_length=1, max_length=600)
    visibility: EvidenceVisibility
    status: EvidenceStatus
    source_message_id: str
    channel: Channel
    created_at: datetime
    related_decision: str | None = None
    deadline: datetime | None = None
    resource: str | None = None


class AlignmentIssue(BaseModel):
    issue_id: UUID
    mandate_id: UUID
    issue_type: AlignmentIssueType
    evidence_ids: list[UUID] = Field(default_factory=list)
    stakeholder_ids: list[str] = Field(default_factory=list)
    related_decision: str | None = None
    summary: str
    blocking: bool
    resolution: str | None = None


class Proposal(BaseModel):
    proposal_id: UUID
    mandate_id: UUID
    round_number: int = Field(ge=1, le=2)
    text: str = Field(min_length=1, max_length=600)
    issue_ids: list[UUID]
    required_respondent_ids: list[str]
    state: ProposalState = ProposalState.AWAITING_RESPONSES
    created_at: datetime
    expires_at: datetime


class ProposalResponse(BaseModel):
    response_id: UUID
    proposal_id: UUID
    stakeholder_id: str
    response: ProposalResponseKind
    change_text: str | None = Field(default=None, max_length=400)
    source_message_id: str
    created_at: datetime
    idempotency_key: str


class AvailabilityWindow(BaseModel):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def end_after_start(self) -> "AvailabilityWindow":
        if self.end <= self.start:
            raise ValueError("availability end must be after start")
        return self


class MeetingPackage(BaseModel):
    meeting_id: UUID
    mandate_id: UUID
    purpose: str
    decision_owner_id: str
    required_attendee_ids: list[str]
    optional_attendee_ids: list[str] = Field(default_factory=list)
    proposed_start: datetime | None = None
    proposed_end: datetime | None = None
    timezone: str = "UTC"
    agreed_facts: list[str]
    open_decisions: list[str]
    agenda: list[str]
    pre_read_evidence_ids: list[UUID]
    calendar_written: bool = False
    created_at: datetime


class DomainEvent(BaseModel):
    event_type: str
    created_at: datetime
    idempotency_key: str
    actor_id: str | None = None
    assignment_id: UUID | None = None
    person_id: str | None = None
    department: str | None = None
    direction: Direction | None = None
    channel: Channel | None = None
    previous_state: str | None = None
    new_state: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeliveryInstruction(BaseModel):
    kind: DeliveryKind
    text: str
    mandate_token: str | None = None
    assignment_id: UUID | None = None
    message_id: str | None = None
    conversation_id: str | None = None
    recipient: str | None = None


class WorkflowResult(BaseModel):
    deliveries: list[DeliveryInstruction] = Field(default_factory=list)
```

`AvailabilityWindow` inputs must contain timezone offsets. `ContactRoute` is deliverable only when email has `recipient` or Telegram has `conversation_id`. API projections may use separate redacted view models; they must not return `sender_address`, `recipient`, or `conversation_id`.

## Phase A — Domain, Persistence, and Intelligence

### Task 1: HumanWire Package, Settings, Domain Types, and Commands

**Files:**

- Create: `src/humanwire/__init__.py`
- Create: `src/humanwire/config.py`
- Create: `src/humanwire/domain.py`
- Create: `src/humanwire/commands.py`
- Create: `tests/humanwire/__init__.py`
- Create: `tests/humanwire/conftest.py`
- Create: `tests/humanwire/test_commands.py`
- Create: `tests/humanwire/test_config.py`
- Modify: `pyproject.toml`
- Modify: `.env.example`

**Interfaces:**

- Produces: `Channel`, `Direction`, `MandateState`, `StakeholderState`, `EvidenceType`, `EvidenceVisibility`, `EvidenceStatus`, `ProposalResponseKind`, `DeliveryKind`, `IncomingMessage`, `ContactRoute`, `Person`, `MandatePlan`, `PlannedStakeholder`, `Mandate`, `StakeholderAssignment`, `InterviewSession`, `EvidenceItem`, `AlignmentIssue`, `Proposal`, `ProposalResponse`, `AvailabilityWindow`, `MeetingPackage`, `DomainEvent`, `DeliveryInstruction`, and `WorkflowResult`.
- Produces: `parse_command(text: str) -> ParsedCommand` and `Settings.require_listener_credentials() -> tuple[str, str]`.
- Consumes: no HumanWire application interfaces.

- [ ] **Step 1: Record the migration baseline**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
git status --short
```

Expected: the existing SecondSignal tests pass, Ruff passes, and only the committed specification/plan history exists.

- [ ] **Step 2: Write failing command and settings tests**

Create `tests/humanwire/test_commands.py` with these cases:

```python
from humanwire.commands import (
    AcknowledgeCommand,
    AvailabilityCommand,
    CancelCommand,
    FreeTextCommand,
    MandateCommand,
    ProposalResponseCommand,
    StatusCommand,
    parse_command,
)
from humanwire.domain import ProposalResponseKind


def test_parses_multiline_mandate() -> None:
    command = parse_command("/mandate\nCoordinate weekend coverage before Friday.")
    assert command == MandateCommand(body="Coordinate weekend coverage before Friday.")


def test_parses_case_actions_case_insensitively() -> None:
    assert parse_command("/status hw-2411") == StatusCommand(token="HW-2411")
    assert parse_command("/cancel hw-2411") == CancelCommand(token="HW-2411")
    assert parse_command("ACK HW-2411") == AcknowledgeCommand(token="HW-2411")
    assert parse_command("ACCEPT hw-2411") == ProposalResponseCommand(
        token="HW-2411", response=ProposalResponseKind.ACCEPT, change_text=None
    )
    assert parse_command("CHANGE HW-2411 Start Monday") == ProposalResponseCommand(
        token="HW-2411",
        response=ProposalResponseKind.CHANGE,
        change_text="Start Monday",
    )


def test_parses_timezone_aware_availability() -> None:
    command = parse_command("AVAILABLE HW-2411 2026-08-14T15:00:00-05:00/2026-08-14T16:00:00-05:00")
    assert isinstance(command, AvailabilityCommand)
    assert command.token == "HW-2411"
    assert command.windows[0].start.isoformat() == "2026-08-14T15:00:00-05:00"


def test_unstructured_reply_remains_free_text() -> None:
    assert parse_command("We need 72 hours of notice.") == FreeTextCommand(
        text="We need 72 hours of notice."
    )
```

Add `tests/humanwire/test_config.py`:

```python
import pytest
from pydantic import SecretStr

from humanwire.config import Settings


def test_listener_credentials_are_required_only_when_listening() -> None:
    with pytest.raises(ValueError, match="CASPIAN_API_KEY"):
        Settings().require_listener_credentials()


def test_listener_credentials_return_plain_values() -> None:
    settings = Settings(
        caspian_api_key=SecretStr("caspian"),
        telegram_bot_token=SecretStr("telegram"),
    )
    assert settings.require_listener_credentials() == ("caspian", "telegram")
```

- [ ] **Step 3: Run the new tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_commands.py tests\humanwire\test_config.py -v
```

Expected: collection fails because `humanwire` does not exist.

- [ ] **Step 4: Add package metadata and settings**

Update `pyproject.toml` to use:

```toml
[project]
name = "humanwire"
version = "0.1.0"
description = "AI chief of staff that interviews the organization"
requires-python = ">=3.12"

[project.scripts]
humanwire = "humanwire.__main__:main"
secondsignal = "secondsignal.__main__:main"

[tool.setuptools.package-data]
humanwire = ["templates/*.html", "static/*.css", "static/*.js"]
secondsignal = ["templates/*.html", "static/*.css"]
```

Keep the existing dependency constraints and temporary `secondsignal` script/package-data entries unchanged until Task 16. Create `src/humanwire/config.py` with these exact settings fields:

```python
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    caspian_api_key: SecretStr | None = None
    caspian_base_url: str = "https://api.trycaspianai.com"
    telegram_bot_token: SecretStr | None = None
    caspian_email_username: str = "humanwire"
    featherless_api_key: SecretStr | None = None
    featherless_base_url: str = "https://api.featherless.ai/v1"
    featherless_model: str = "Qwen/Qwen2.5-7B-Instruct"
    database_url: str = "sqlite:///data/humanwire.db"
    organization_path: Path = Path("data/organization.json")
    acknowledgement_seconds: int = 300
    reminder_seconds: int = 300
    mandate_timeout_seconds: int = 86_400
    due_action_poll_seconds: int = 5
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8000
    public_demo: bool = False

    def require_listener_credentials(self) -> tuple[str, str]:
        missing = []
        if self.caspian_api_key is None:
            missing.append("CASPIAN_API_KEY")
        if self.telegram_bot_token is None:
            missing.append("TELEGRAM_BOT_TOKEN")
        if missing:
            raise ValueError("Missing listener credentials: " + ", ".join(missing))
        return (
            self.caspian_api_key.get_secret_value(),
            self.telegram_bot_token.get_secret_value(),
        )
```

Update `.env.example` to use the HumanWire username, database, organization path, and timeout names without values for secrets.

- [ ] **Step 5: Implement exact command types and parser precedence**

Create frozen dataclasses in `src/humanwire/commands.py` for `MandateCommand`, `StatusCommand`, `CancelCommand`, `AcknowledgeCommand`, `ProposalResponseCommand`, `AvailabilityCommand`, and `FreeTextCommand`. Parse in this order: proposal response, acknowledgement, availability, status, cancel, mandate, free text. Use token pattern `HW-[A-Z0-9]{4,8}` and `datetime.fromisoformat()` for each slash-delimited availability window. Invalid availability syntax must return `FreeTextCommand` rather than raising.

The proposal regex must accept exactly:

```python
PROPOSAL = re.compile(
    r"^(?P<answer>ACCEPT|REJECT|CHANGE)[ \t]+(?P<token>HW-[A-Z0-9]{4,8})"
    r"(?:[ \t]+(?P<change>[^\r\n].*))?$",
    re.IGNORECASE,
)
```

- [ ] **Step 6: Implement the domain models and shared fixtures**

Create the enums and Pydantic models named in the Interfaces block. Enforce these model rules:

```python
class MandatePlan(BaseModel):
    objective: str
    required_decisions: list[str] = Field(min_length=1)
    stakeholders: list[PlannedStakeholder] = Field(min_length=1)
    deadline: datetime | None = None
    completion_conditions: list[str] = Field(min_length=1)


class PlannedStakeholder(BaseModel):
    person_ref: str
    reason: str
    direction: Direction
    required: bool = True
    questions: list[str] = Field(min_length=1, max_length=5)


class DeliveryInstruction(BaseModel):
    kind: DeliveryKind
    text: str
    mandate_token: str | None = None
    assignment_id: UUID | None = None
    message_id: str | None = None
    conversation_id: str | None = None
    recipient: str | None = None
```

Define `MandateState` and `StakeholderState` exactly as the approved specification. Put reusable UTC timestamps, fictional people, and `IncomingMessage` factories in `tests/humanwire/conftest.py`.

- [ ] **Step 7: Run focused tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_commands.py tests\humanwire\test_config.py -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire tests\humanwire
git diff --check
```

Expected: all focused tests pass.

Commit:

```powershell
git add pyproject.toml .env.example src/humanwire tests/humanwire
git commit -m "feat: establish HumanWire domain"
```

### Task 2: Organization Directory and Authority-Aware Routing

**Files:**

- Create: `src/humanwire/directory.py`
- Create: `config/demo-organization.example.json`
- Create: `tests/humanwire/test_directory.py`
- Create: `scripts/seed_humanwire_organization.py`

**Interfaces:**

- Consumes: `Channel`, `ContactRoute`, `Direction`, `IncomingMessage`, and `Person` from `humanwire.domain`.
- Produces: `OrganizationDocument`, `InitiatorPolicy`, `OrganizationDirectory.load(path)`, `resolve_person(ref)`, `person_for_sender(message)`, `is_authorized_initiator(message)`, `classify_direction(initiator_id, target_id)`, `validate_target(initiator_id, target_id, requested_direction)`, and `ordered_routes(person_id)`.

- [ ] **Step 1: Write failing directory and policy tests**

Create tests that build a fictional chain `CEO -> COO -> VP Support -> Support Manager -> Team Lead` plus a lateral `VP People`. Include:

```python
def test_classifies_downward_lateral_and_upward_routes(directory) -> None:
    assert directory.classify_direction("manager", "team-lead") is Direction.DOWNWARD
    assert directory.classify_direction("manager", "vp-people") is Direction.LATERAL
    assert directory.classify_direction("manager", "vp-support") is Direction.UPWARD


def test_policy_blocks_unapproved_target(directory) -> None:
    with pytest.raises(UnauthorizedTargetError):
        directory.validate_target("manager", "cfo", Direction.LATERAL)


def test_orders_preferred_then_alternate_deliverable_routes(directory) -> None:
    routes = directory.ordered_routes("vp-people")
    assert [route.channel for route in routes] == [Channel.EMAIL, Channel.TELEGRAM]
    assert routes[1].conversation_id == "tg-priya"


def test_matches_initiator_from_registered_sender(directory, telegram_message) -> None:
    assert directory.person_for_sender(telegram_message).person_id == "manager"
    assert directory.is_authorized_initiator(telegram_message) is True
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_directory.py -v
```

Expected: FAIL because `humanwire.directory` does not exist.

- [ ] **Step 3: Implement the directory document and indexes**

Use these models:

```python
class InitiatorPolicy(BaseModel):
    person_id: str
    allowed_directions: set[Direction]
    allowed_departments: set[str]
    max_upward_levels: int = Field(default=1, ge=0, le=5)


class OrganizationDocument(BaseModel):
    people: list[Person]
    initiator_policies: list[InitiatorPolicy]
```

Build casefolded indexes for person ID, display name, aliases, and `(channel, sender_address)`. `resolve_person()` must raise `UnknownPersonError` or `AmbiguousPersonError`; it must never return the first ambiguous alias.

- [ ] **Step 4: Implement relationship and authority logic**

Walk `manager_id` chains with a visited set. Classify a target below the initiator as `DOWNWARD`, above as `UPWARD`, and all other valid people as `LATERAL`. `validate_target()` must:

1. Verify the requested direction matches the computed direction.
2. Verify the direction is allowed.
3. Verify the target department is allowed.
4. Count upward hops and enforce `max_upward_levels`.
5. Return the resolved `Person` only after all checks pass.

`ordered_routes()` must filter email routes without `recipient` and Telegram routes without `conversation_id`, then sort preferred routes first without inventing identifiers.

- [ ] **Step 5: Add fictional organization configuration and seed helper**

The committed example must contain only these fictional roles: Jordan Lee/CEO, Maya Chen/COO, Nora Williams/VP Support, Arun Patel/Support Manager, US Team Lead, APAC Team Lead, and Priya Raman/VP People. Use placeholder addresses ending in `example.com` and placeholder Telegram conversation IDs.

`scripts/seed_humanwire_organization.py` must read real values from environment variables, validate with `OrganizationDocument`, write only to `Settings.organization_path`, refuse to overwrite without `--force`, and print no contact value.

- [ ] **Step 6: Run tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_directory.py -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire\directory.py scripts\seed_humanwire_organization.py tests\humanwire\test_directory.py
git diff --check
```

Commit:

```powershell
git add src/humanwire/directory.py config/demo-organization.example.json scripts/seed_humanwire_organization.py tests/humanwire/test_directory.py
git commit -m "feat: add authority-aware organization directory"
```

### Task 3: HumanWire Persistence and State Machines

**Files:**

- Create: `src/humanwire/database.py`
- Create: `src/humanwire/repository.py`
- Create: `src/humanwire/state_machine.py`
- Create: `tests/humanwire/test_repository.py`
- Create: `tests/humanwire/test_state_machine.py`

**Interfaces:**

- Consumes: all aggregate types from `humanwire.domain`.
- Produces: `SqlAlchemyHumanWireRepository`, `DuplicateMandateError`, `MandateStateMachine.transition()`, `StakeholderStateMachine.transition()`, and `create_session_factory(database_url)`.
- Repository methods: `add_mandate`, `save_mandate`, `get_mandate_by_token`, `get_mandate_by_idempotency_key`, `list_recent_mandates`, `add_assignment`, `save_assignment`, `get_assignment`, `list_assignments`, `add_interview`, `save_interview`, `get_interview`, `find_active_interview`, `list_interviews`, `add_evidence`, `list_evidence`, `add_issue`, `list_issues`, `add_proposal`, `save_proposal`, `get_active_proposal`, `add_proposal_response`, `list_proposal_responses`, `save_meeting_package`, `get_meeting_package`, `append_event`, `list_events`, `list_due_assignments`, `set_runtime_status`, and `get_runtime_status`.

- [ ] **Step 1: Write failing transition tests**

Parameterize every documented normal transition and terminal state. Add these invariants:

```python
def test_required_approver_cannot_be_completed_from_unreachable(make_assignment, now) -> None:
    assignment = make_assignment(required=True, state=StakeholderState.UNREACHABLE)
    with pytest.raises(InvalidTransitionError):
        StakeholderStateMachine().transition(
            assignment, StakeholderState.COMPLETE, "forced", now
        )


def test_terminal_mandate_is_immutable(make_mandate, now) -> None:
    mandate = make_mandate(state=MandateState.ALIGNED)
    with pytest.raises(InvalidTransitionError):
        MandateStateMachine().transition(
            mandate, MandateState.INTERVIEWING, "reopen", now
        )
```

- [ ] **Step 2: Write failing repository round-trip and due-action tests**

Cover every aggregate and verify:

```python
def test_event_order_is_stable(repository, sample_mandate, now) -> None:
    repository.add_mandate(sample_mandate)
    repository.append_event(sample_mandate.mandate_id, DomainEvent(
        event_type="mandate.created",
        created_at=now,
        idempotency_key="mandate:sample:created",
        metadata={"safe": True},
    ))
    repository.append_event(sample_mandate.mandate_id, DomainEvent(
        event_type="mandate.planned",
        created_at=now,
        idempotency_key="mandate:sample:planned",
        metadata={},
    ))
    assert [event.event_type for event in repository.list_events(sample_mandate.mandate_id)] == [
        "mandate.created", "mandate.planned"
    ]


def test_due_assignments_excludes_terminal_states(repository, due_assignments, now) -> None:
    tokens = {item.assignment_id for item in repository.list_due_assignments(now)}
    assert due_assignments.follow_up.assignment_id in tokens
    assert due_assignments.complete.assignment_id not in tokens
```

- [ ] **Step 3: Run the tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_state_machine.py tests\humanwire\test_repository.py -v
```

Expected: FAIL because persistence and state modules do not exist.

- [ ] **Step 4: Implement SQLAlchemy records and fresh schema**

Create records for mandates, stakeholder assignments, interview sessions, evidence items, alignment issues, proposals, proposal responses, meeting packages, domain events, and runtime status. Use string UUID primary keys, indexed public tokens, JSON for validated nested values, timezone-aware timestamps, and foreign keys with SQLite enforcement.

Use separate table names beginning with `hw_`, for example:

```python
class MandateRecord(Base):
    __tablename__ = "hw_mandates"


class DomainEventRecord(Base):
    __tablename__ = "hw_events"
```

Do not alter or drop SecondSignal tables. `create_session_factory()` must keep the existing `StaticPool` behavior for `sqlite://` tests.

- [ ] **Step 5: Implement repository mappings and atomic writes**

Each repository method opens one short session. State-changing service operations that save an aggregate and append its event must use a new `transaction()` context returning a session-bound `RepositoryUnitOfWork` so the save and event commit together:

```python
with repository.transaction() as unit:
    unit.save_mandate(updated)
    unit.append_event(updated.mandate_id, event)
```

Convert naive SQLite datetimes to UTC on read. Enforce unique mandate idempotency keys and event idempotency keys. Never store a full destination in `DomainEvent.metadata`; store person and route IDs.

- [ ] **Step 6: Implement explicit state transition maps**

Copy the approved mandate and stakeholder transitions verbatim from the design specification. Set `completed_at` only for terminal mandate states. Set assignment `completed_at` only for `COMPLETE`, `DECLINED`, `UNREACHABLE`, or `DELIVERY_FAILED`. An undocumented transition raises `InvalidTransitionError` containing `source -> target`.

- [ ] **Step 7: Run tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_state_machine.py tests\humanwire\test_repository.py -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire\database.py src\humanwire\repository.py src\humanwire\state_machine.py tests\humanwire
git diff --check
```

Commit:

```powershell
git add src/humanwire/database.py src/humanwire/repository.py src/humanwire/state_machine.py tests/humanwire
git commit -m "feat: persist HumanWire mandates and events"
```

### Task 4: Featherless JSON Client and Mandate Planning

**Files:**

- Create: `src/humanwire/model_client.py`
- Create: `src/humanwire/planning.py`
- Create: `tests/humanwire/test_planning.py`

**Interfaces:**

- Consumes: `MandatePlan`, `Person`, `PlannedStakeholder`, `Direction`, and `OrganizationDirectory`.
- Produces: `JsonModelClient.complete_json(system: str, user: str) -> dict`, `FeatherlessJsonClient`, `MandatePlanner.plan(text: str, initiator: Person) -> ResolvedPlan`, `FeatherlessMandatePlanner`, `RuleBasedMandatePlanner`, and `ResolvedPlan`.
- `ResolvedPlan` uses the exact contract below; unresolved or unauthorized references are returned as explicit errors rather than silently removed.

```python
class ResolvedPlan(BaseModel):
    plan: MandatePlan
    people: list[Person]
    planner: str
    fallback_reason: str | None = None
```

- [ ] **Step 1: Write failing planning tests**

Use `httpx.MockTransport` to cover valid JSON, timeout, HTTP error, invalid JSON, invalid schema, unknown person, ambiguous alias, and unauthorized upward route. Assert:

```python
def test_valid_model_plan_is_resolved_against_directory(planner, manager) -> None:
    result = planner.plan("Coordinate weekend coverage", manager)
    assert [person.person_id for person in result.people] == [
        "us-lead", "apac-lead", "vp-people", "vp-support"
    ]
    assert result.plan.stakeholders[2].direction is Direction.LATERAL


def test_invalid_model_output_uses_rule_fallback(failing_client, directory, manager) -> None:
    planner = FeatherlessMandatePlanner(failing_client, directory, RuleBasedMandatePlanner(directory))
    result = planner.plan(
        "Interview US Team Lead, APAC Team Lead, Priya Raman, and Nora Williams about weekend coverage.",
        manager,
    )
    assert result.planner == "rules"
    assert result.fallback_reason == "invalid_schema"
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_planning.py -v
```

Expected: FAIL because planner modules do not exist.

- [ ] **Step 3: Implement the reusable Featherless client**

POST to `{base_url}/chat/completions` with temperature `0`, `response_format={"type": "json_object"}` when accepted, and bounded tokens. Delimit the request as `UNTRUSTED_CONTENT_START/END`. Return a dictionary only. Raise a typed `ModelFailure` with reasons `timeout`, `network_error`, `http_<status>`, `invalid_response`, `invalid_json`, or `invalid_schema`. Set headers:

```python
{
    "Authorization": f"Bearer {api_key}",
    "HTTP-Referer": "https://secondsignal.vercel.app",
    "X-Title": "HumanWire",
    "Content-Type": "application/json",
}
```

No key or response body may appear in an exception message or log.

- [ ] **Step 4: Implement structured planning and fallback**

The system prompt must require exactly the `MandatePlan` schema, limit each stakeholder to five questions, and forbid contact details. After model validation, resolve every `person_ref` through `OrganizationDirectory`, recompute direction, and call `validate_target()`.

The rule fallback extracts explicit names/aliases from the mandate text, creates one required decision (`"Approve and prepare the requested mandate"`), one completion condition (`"Every required stakeholder is complete or explicitly unreachable"`), and these default questions:

```python
[
    "What facts should the decision owner know?",
    "What hard constraint could block this mandate?",
    "What commitment can you make, and by when?",
]
```

If fallback cannot resolve at least one authorized stakeholder, raise `PlanNeedsClarification` with safe candidate names, not addresses.

- [ ] **Step 5: Run tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_planning.py -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire\model_client.py src\humanwire\planning.py tests\humanwire\test_planning.py
git diff --check
```

Commit:

```powershell
git add src/humanwire/model_client.py src/humanwire/planning.py tests/humanwire/test_planning.py
git commit -m "feat: plan mandates with Featherless"
```

### Task 5: Evidence Extraction, Confirmation, and Privacy

**Files:**

- Create: `src/humanwire/evidence.py`
- Create: `tests/humanwire/test_evidence.py`
- Create: `src/humanwire/redaction.py`

**Interfaces:**

- Consumes: `EvidenceItem`, `EvidenceStatus`, `EvidenceType`, `EvidenceVisibility`, `InterviewSession`, `IncomingMessage`, and `JsonModelClient`.
- Produces: `EvidenceDraft`, `EvidenceExtractor.extract(answer, question, mandate_id, assignment_id, stakeholder_id, source_message_id, channel, received_at, visibility) -> list[EvidenceDraft]`, `FeatherlessEvidenceExtractor`, `RuleBasedEvidenceExtractor`, `confirm_drafts(drafts) -> list[EvidenceItem]`, and `shareable_evidence(items)`.

- [ ] **Step 1: Write failing evidence and redaction tests**

Cover all evidence types and visibility modes. Include:

```python
def test_private_evidence_is_excluded_from_shared_views(extractor, private_answer) -> None:
    drafts = extractor.extract(**private_answer)
    items = confirm_drafts(drafts)
    assert items[0].visibility is EvidenceVisibility.PRIVATE
    assert shareable_evidence(items) == []


def test_anonymous_evidence_drops_identity_in_shared_projection(make_evidence) -> None:
    item = make_evidence(visibility=EvidenceVisibility.ANONYMOUS, stakeholder_id="vp-people")
    projection = shareable_evidence([item])[0]
    assert projection.stakeholder_id is None
    assert projection.statement == item.statement


def test_extractor_preserves_source_provenance(extractor, shareable_answer) -> None:
    item = confirm_drafts(extractor.extract(**shareable_answer))[0]
    assert item.source_message_id == "msg-42"
    assert item.channel is Channel.EMAIL
```

Reuse and extend SecondSignal redaction tests for OTPs, bearer tokens, recovery codes, and direct contact values.

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_evidence.py -v
```

Expected: FAIL because evidence module does not exist.

- [ ] **Step 3: Implement schema-validated extraction**

The model may return only `ModelEvidenceDraft` fields. The extractor enriches each validated item into `EvidenceDraft` with provenance supplied by the service:

```python
class ModelEvidenceDraft(BaseModel):
    evidence_type: EvidenceType
    statement: str = Field(min_length=1, max_length=600)
    related_decision: str | None = Field(default=None, max_length=240)
    deadline: datetime | None = None
    resource: str | None = Field(default=None, max_length=120)


class EvidenceDraft(ModelEvidenceDraft):
    mandate_id: UUID
    assignment_id: UUID
    stakeholder_id: str
    source_message_id: str
    channel: Channel
    created_at: datetime
    visibility: EvidenceVisibility
```

The service supplies stakeholder, message, channel, time, and visibility metadata; the model never supplies provenance. Set new items to `ASSERTED`. Redact sensitive patterns before persistence. The rule fallback splits short sentences and classifies constraint keywords (`must`, `cannot`, `requires`, `blocked`) as `CONSTRAINT`, commitment keywords (`will`, `can deliver`, `by`) as `COMMITMENT`, availability dates as `AVAILABILITY`, and remaining sentences as `FACT`.

- [ ] **Step 4: Implement visibility projections**

`shareable_evidence()` returns immutable projections. `SHAREABLE` retains the stakeholder ID, `ANONYMOUS` replaces it with `None`, and `PRIVATE` is omitted. A separate `private_blocker_count()` may return a count only. No shared projection includes raw message text.

- [ ] **Step 5: Run tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_evidence.py tests\test_redaction.py -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire\evidence.py src\humanwire\redaction.py tests\humanwire\test_evidence.py
git diff --check
```

Commit:

```powershell
git add src/humanwire/evidence.py src/humanwire/redaction.py tests/humanwire/test_evidence.py
git commit -m "feat: capture provenance-safe evidence"
```

## Phase B — Interviews, Alignment, and Meetings

### Task 6: Interview Sessions and the Cross-Channel Response Ladder

**Files:**

- Create: `src/humanwire/interviews.py`
- Create: `src/humanwire/messages.py`
- Create: `tests/humanwire/test_interviews.py`

**Interfaces:**

- Consumes: `OrganizationDirectory`, `SqlAlchemyHumanWireRepository`, `StakeholderStateMachine`, `EvidenceExtractor`, `DeliveryInstruction`, and assignment/interview models.
- Produces: `InterviewCoordinator.start_assignment(assignment, questions, now) -> WorkflowResult`, `acknowledge(message, assignment, now) -> WorkflowResult`, `record_answer(message, assignment, now) -> WorkflowResult`, `process_due_assignment(assignment, now) -> WorkflowResult`, and renderers `render_interview_intro`, `render_question`, `render_reminder`, `render_channel_switch`, and `render_unreachable_notice`.

- [ ] **Step 1: Write failing interview-start and continuation tests**

Add tests proving email and Telegram delivery construction, bounded question count, explicit acknowledgement, evidence persistence, and continuation on an alternate channel:

```python
def test_acknowledgement_on_alternate_channel_resumes_same_session(
    coordinator, assignment_after_email_timeout, telegram_ack, repository, now
) -> None:
    result = coordinator.acknowledge(telegram_ack, assignment_after_email_timeout, now)
    session = repository.get_interview(assignment_after_email_timeout.interview_id)
    assert session.current_channel is Channel.TELEGRAM
    assert session.current_question_index == 0
    assert result.deliveries[0].conversation_id == "tg-priya"
    assert "Question 1 of 3" in result.deliveries[0].text


def test_answer_advances_question_and_persists_evidence(
    coordinator, interviewing_assignment, email_answer, repository, now
) -> None:
    result = coordinator.record_answer(email_answer, interviewing_assignment, now)
    assert "Question 2 of 3" in result.deliveries[0].text
    assert repository.list_evidence(interviewing_assignment.mandate_id)
```

- [ ] **Step 2: Write failing response-ladder tests**

Cover these exact transitions:

```python
@pytest.mark.parametrize(
    ("attempt", "expected_state", "event_type"),
    [
        (0, StakeholderState.AWAITING_ACKNOWLEDGEMENT, "outreach.primary_sent"),
        (1, StakeholderState.FOLLOW_UP_DUE, "outreach.reminder_sent"),
        (2, StakeholderState.ALTERNATE_CHANNEL, "outreach.alternate_sent"),
        (3, StakeholderState.UNREACHABLE, "stakeholder.unreachable"),
    ],
)
def test_response_ladder_never_assumes_agreement(
    coordinator, assignment_for_attempt, attempt, expected_state, event_type, now
) -> None:
    assignment = assignment_for_attempt(attempt)
    coordinator.process_due_assignment(assignment, now)
    saved = coordinator.repository.get_assignment(assignment.assignment_id)
    assert saved.state is expected_state
    assert event_type in [e.event_type for e in coordinator.repository.list_events(assignment.mandate_id)]
```

Also test that two undeliverable routes produce `DELIVERY_FAILED`, one route produces no duplicate alternate send, and a completed assignment never appears in due work.

- [ ] **Step 3: Run the tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_interviews.py -v
```

Expected: FAIL because interview coordinator does not exist.

- [ ] **Step 4: Implement interview creation and sender correlation**

Create one `InterviewSession` per assignment with at most five questions. Store the current question index, current route ID, channel history, visibility default, and acknowledgement state. A reply is valid only when `(channel, normalized sender)` belongs to the assignment's person and the case token matches either the command or active conversation correlation.

Treat `SHAREABLE`, `ANONYMOUS`, or `PRIVATE` at the start of an answer as an override for that answer. `ACK <token>` moves to `ACKNOWLEDGED` then `INTERVIEWING` and sends the current question. A plain answer from a registered route may implicitly acknowledge, then records the answer.

- [ ] **Step 5: Implement the exact response ladder**

Use assignment fields `attempt_count`, `active_route_index`, `next_action_at`, and `last_delivery_at`. The sequence is:

```text
attempt 0 -> preferred route intro -> AWAITING_ACKNOWLEDGEMENT
attempt 1 -> same-route reminder -> FOLLOW_UP_DUE
attempt 2 -> alternate route intro with continuity text -> ALTERNATE_CHANNEL
attempt 3 -> UNREACHABLE and notify mandate owner
```

Set the next due time from `Settings.acknowledgement_seconds` after an intro and `Settings.reminder_seconds` after a reminder. Persist the assignment transition and domain event atomically before returning the delivery. Mark delivery success or failure through explicit coordinator methods called by the gateway.

- [ ] **Step 6: Implement concise channel-neutral messages**

Every intro includes `HUMANWIRE INTERVIEW`, token, mandate summary, reason for contact, question count, sharing modes, and `ACK <token>`. The alternate-channel message explicitly says the previous channel did not receive an acknowledgement and that the same interview will continue. Never render a destination address, private evidence, or another stakeholder's raw answer.

- [ ] **Step 7: Run tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_interviews.py tests\humanwire\test_repository.py -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire\interviews.py src\humanwire\messages.py tests\humanwire\test_interviews.py
git diff --check
```

Commit:

```powershell
git add src/humanwire/interviews.py src/humanwire/messages.py tests/humanwire/test_interviews.py
git commit -m "feat: orchestrate cross-channel interviews"
```

### Task 7: Alignment Analysis and Two-Round Negotiation

**Files:**

- Create: `src/humanwire/alignment.py`
- Create: `tests/humanwire/test_alignment.py`
- Modify: `src/humanwire/messages.py`

**Interfaces:**

- Consumes: shareable evidence projections, `AlignmentIssue`, `Proposal`, `ProposalResponse`, `ProposalResponseKind`, `JsonModelClient`, and repository methods.
- Produces: `AlignmentReport`, `AlignmentEngine.analyze(plan, evidence, assignments) -> AlignmentReport`, `HybridAlignmentEngine`, `NegotiationCoordinator.create_proposal(mandate, report, round_number, now) -> Proposal`, `record_response(proposal, stakeholder_id, kind, change_text, now)`, and `evaluate_round(proposal) -> NegotiationOutcome`.

Use these exact result contracts:

```python
class NegotiationOutcome(StrEnum):
    ALIGNED = "aligned"
    NEXT_ROUND = "next_round"
    MEETING_REQUIRED = "meeting_required"


class AlignmentReport(BaseModel):
    mandate_id: UUID
    agreements: list[str] = Field(default_factory=list)
    issues: list[AlignmentIssue] = Field(default_factory=list)
    covered_decisions: list[str] = Field(default_factory=list)
    private_blocker_count: int = 0
    is_aligned: bool

    @property
    def blocking_issue_count(self) -> int:
        return sum(issue.blocking for issue in self.issues)
```

- [ ] **Step 1: Write failing deterministic alignment tests**

Include:

```python
def test_conflicting_facts_remain_disputed(engine, evidence_factory) -> None:
    evidence = [
        evidence_factory(EvidenceType.FACT, "Launch starts Friday", stakeholder_id="ops"),
        evidence_factory(EvidenceType.FACT, "Launch starts Monday", stakeholder_id="people"),
    ]
    report = engine.analyze(sample_plan(), evidence, complete_assignments())
    assert report.is_aligned is False
    assert any(issue.issue_type == "contradiction" for issue in report.issues)


def test_missing_required_response_blocks_alignment(engine, unreachable_required_assignment) -> None:
    report = engine.analyze(sample_plan(), [], [unreachable_required_assignment])
    assert report.is_aligned is False
    assert report.blocking_issue_count == 1
    assert report.issues[0].issue_type == "missing_evidence"
```

Add resource conflict, deadline conflict, hard-constraint, private-blocker count, and compatible-commitment cases.

- [ ] **Step 2: Write failing negotiation-limit and response tests**

Assert that all required stakeholders must explicitly accept, `REJECT` prevents alignment, `CHANGE` creates an open change request, duplicate responses are idempotent, and round three raises `NegotiationLimitReached`:

```python
def test_third_round_is_forbidden(coordinator, mandate, report, now) -> None:
    with pytest.raises(NegotiationLimitReached):
        coordinator.create_proposal(mandate, report, round_number=3, now=now)
```

- [ ] **Step 3: Run tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_alignment.py -v
```

Expected: FAIL because alignment module does not exist.

- [ ] **Step 4: Implement deterministic checks before semantic analysis**

Always create blocking issues for missing required assignments, disputed evidence, incompatible explicit deadlines, and resource quantities that exceed an asserted limit. Group candidate semantic comparisons by `related_decision`; never send private evidence to the model. The model may add issues or suggested agreements, but deterministic blocking issues cannot be removed.

`AlignmentReport.is_aligned` is true only when every required assignment is `COMPLETE`, at least one confirmed/shareable or anonymous evidence item supports each required decision, and no blocking issue remains.

- [ ] **Step 5: Implement bounded proposal drafting and evaluation**

Create proposals only for rounds 1 or 2. Prompt Featherless with safe evidence and issues, require a 600-character proposal, and prepend `HUMANWIRE DRAFT PROPOSAL`. Fall back to a deterministic proposal that lists each blocking issue and asks stakeholders to choose `ACCEPT`, `REJECT`, or `CHANGE`.

`evaluate_round()` returns `ALIGNED` only if every required respondent has one latest `ACCEPT` and none has `REJECT` or `CHANGE`. Round 1 unresolved returns `NEXT_ROUND`; round 2 unresolved returns `MEETING_REQUIRED`.

- [ ] **Step 6: Add proposal message rendering**

Render the related evidence summary, proposal text, round `n of 2`, deadline, and exact response commands. Do not include private statements or anonymous identities.

- [ ] **Step 7: Run tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_alignment.py tests\humanwire\test_evidence.py -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire\alignment.py src\humanwire\messages.py tests\humanwire\test_alignment.py
git diff --check
```

Commit:

```powershell
git add src/humanwire/alignment.py src/humanwire/messages.py tests/humanwire/test_alignment.py
git commit -m "feat: add bounded alignment negotiation"
```

### Task 8: Availability and Meeting-Ready Packages

**Files:**

- Create: `src/humanwire/meetings.py`
- Create: `tests/humanwire/test_meetings.py`
- Modify: `src/humanwire/messages.py`

**Interfaces:**

- Consumes: `AvailabilityWindow`, `MeetingPackage`, `MandatePlan`, `AlignmentReport`, `Person`, and evidence projections.
- Produces: `MeetingCoordinator.required_attendees()`, `record_availability()`, `find_overlap()`, `build_package()`, `render_availability_request`, `render_meeting_confirmation`, `render_meeting_reminder`, and `render_ics(package) -> bytes`.

- [ ] **Step 1: Write failing attendee and overlap tests**

Cover timezone normalization, overlapping windows, no overlap, optional-attendee exclusion, and authority-owner inclusion:

```python
def test_smallest_attendee_set_includes_issue_owners_and_decision_owner(coordinator) -> None:
    attendees = coordinator.required_attendees(sample_report(), assignments(), decision_owner_id="coo")
    assert attendees == {"manager", "vp-people", "coo"}


def test_overlap_is_calculated_in_utc(coordinator) -> None:
    slot = coordinator.find_overlap(windows_in_chicago_and_london())
    assert slot.start.isoformat() == "2026-08-14T20:00:00+00:00"
    assert slot.end.isoformat() == "2026-08-14T20:30:00+00:00"
```

- [ ] **Step 2: Write failing package and ICS tests**

Assert the package contains purpose, owner, minimal attendees, agreed facts, open decisions, agenda, pre-read evidence IDs, proposed slot, and explicit `calendar_written=False`. Parse the generated bytes as text and verify `BEGIN:VCALENDAR`, UTC timestamps, escaped title, and no private evidence.

- [ ] **Step 3: Run tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_meetings.py -v
```

Expected: FAIL because meeting coordinator does not exist.

- [ ] **Step 4: Implement deterministic attendee and availability logic**

Required attendees are the mandate initiator, decision owner, and one owner for each unresolved blocking issue. Deduplicate people and exclude optional observers. Convert every availability window to UTC, intersect in 30-minute increments, and choose the earliest overlap. If none exists, return `None` and one explicit availability-retry result; do not invent a time.

- [ ] **Step 5: Build the meeting package and calendar artifact**

Generate a deterministic agenda:

```text
1. Confirm agreed facts
2. Resolve each open decision in severity order
3. Assign owner and deadline for each commitment
4. Confirm the final decision record
```

Build `.ics` content locally with `METHOD:PUBLISH`; do not add a calendar API dependency. Set `calendar_written=False` and expose a download route later. Meeting confirmation text must say `Proposed meeting` until the required attendees acknowledge the slot.

- [ ] **Step 6: Run tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_meetings.py -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire\meetings.py src\humanwire\messages.py tests\humanwire\test_meetings.py
git diff --check
```

Commit:

```powershell
git add src/humanwire/meetings.py src/humanwire/messages.py tests/humanwire/test_meetings.py
git commit -m "feat: prepare evidence-backed meetings"
```

### Task 9: Application Services and Incoming-Message Workflow

**Files:**

- Create: `src/humanwire/services.py`
- Create: `src/humanwire/workflow.py`
- Create: `tests/humanwire/test_workflow.py`
- Modify: `src/humanwire/messages.py`

**Interfaces:**

- Consumes: all prior services, parsers, state machines, directory, and repository.
- Produces: `MandateService.create(message, command) -> WorkflowResult`, `SynthesisService.run(mandate_id, now) -> WorkflowResult`, `HumanWireWorkflow.handle(message) -> WorkflowResult`, `process_due(now) -> WorkflowResult`, and `mark_delivery_result(instruction, succeeded, now) -> WorkflowResult`.

- [ ] **Step 1: Write the failing manager-originated end-to-end test**

Use deterministic fake planner/extractor and real in-memory repository:

```python
def test_manager_mandate_creates_three_routes_and_real_deliveries(
    workflow, telegram_mandate, repository
) -> None:
    result = workflow.handle(telegram_mandate)
    mandate = repository.list_recent_mandates(1)[0]
    assignments = repository.list_assignments(mandate.mandate_id)
    assert mandate.state is MandateState.INTERVIEWING
    assert {item.direction for item in assignments} == {
        Direction.DOWNWARD, Direction.LATERAL, Direction.UPWARD
    }
    assert len(result.deliveries) == 5  # origin acknowledgement + four stakeholders
    assert {delivery.kind for delivery in result.deliveries[1:]} == {
        DeliveryKind.INITIATE_EMAIL, DeliveryKind.SEND_TO_CONVERSATION
    }
```

- [ ] **Step 2: Add failing routing and synthesis tests**

Cover unauthorized initiator, duplicate mandate idempotency, status/cancel ownership, registered acknowledgement, free-text interview answers, proposal responses, availability replies, late replies, unknown tokens, ambiguous active interviews, model fallback event, required unreachable stakeholder, aligned outcome, round-two meeting-required outcome, and meeting-ready outcome.

Assert every externally visible state has a corresponding append-only event and that duplicate channel messages return existing state without a duplicate delivery.

- [ ] **Step 3: Run tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_workflow.py -v
```

Expected: FAIL because application services do not exist.

- [ ] **Step 4: Implement mandate creation atomically**

Compute idempotency as SHA-256 of `channel|message_id|connection_id`. Resolve and authorize the initiator, plan and resolve stakeholders, create the mandate in `RECEIVED`, transition to `PLANNED`, create assignments/interviews, transition to `INTERVIEWING`, and return one origin acknowledgement plus initial interview deliveries. Persist `model.fallback` when applicable.

If planning needs clarification, reply with safe names and create no mandate. If no permitted route exists for a required stakeholder, create the assignment as `DELIVERY_FAILED` and keep the mandate partial rather than claiming interviews started successfully.

- [ ] **Step 5: Implement deterministic command routing**

Route case commands before free text. For free text, find exactly one active interview for the registered sender and conversation; zero matches returns usage help and more than one match asks for `ACK <token>`. Proposal and availability responses require both token match and assigned person match. Only the original authorized initiator may cancel.

**Approved amendment (2026-08-11):** terminal interview history on the same conversation makes tokenless correlation ambiguous. A newer active interview may accept tokenless free text only after it has been acknowledged on that exact conversation. Until then, return a safe `ACK <token>` selection prompt and make no durable mutation. A valid explicit token may select the intended active interview and preserve authenticated cross-channel continuation.

- [ ] **Step 6: Implement automatic synthesis gates**

After each completed assignment, check whether all required assignments are terminal. Transition to `SYNTHESIZING`, run alignment, and then:

- `ALIGNED`: persist alignment brief and send it to the initiator and required stakeholders.
- blocking issues with negotiation available: transition to `NEGOTIATING`, create round 1, and send the proposal.
- unresolved after round 2: transition through `MEETING_REQUIRED` to `SCHEDULING` and request availability.
- all availability received: create package, transition to `MEETING_READY`, and send proposed meeting confirmation plus artifact link.
- required unreachable: transition to `PARTIAL` with an explicit missing-person explanation.

Every transition and outbound request is saved before delivery.

- [ ] **Step 7: Implement due processing and delivery callbacks**

`process_due(now)` loads due assignments, expired mandates, and due reminders once. Use event idempotency keys such as `assignment:{id}:attempt:{count}`. `mark_delivery_result()` records success; on failure it advances to the next registered route or terminal delivery failure without recursive infinite callbacks.

- [ ] **Step 8: Run workflow regressions and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_workflow.py tests\humanwire\test_interviews.py tests\humanwire\test_alignment.py tests\humanwire\test_meetings.py -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire tests\humanwire
git diff --check
```

Commit:

```powershell
git add src/humanwire/services.py src/humanwire/workflow.py src/humanwire/messages.py tests/humanwire/test_workflow.py
git commit -m "feat: coordinate HumanWire mandates end to end"
```

### Task 10: Caspian Gateway, Container, CLI, and Due-Action Worker

**Files:**

- Create: `src/humanwire/caspian_gateway.py`
- Create: `src/humanwire/container.py`
- Create: `src/humanwire/logging_config.py`
- Create: `src/humanwire/__main__.py`
- Create: `tests/humanwire/test_caspian_gateway.py`
- Create: `tests/humanwire/test_container.py`
- Create: `tests/humanwire/test_logging.py`

**Interfaces:**

- Consumes: `Settings`, `HumanWireWorkflow`, `SqlAlchemyHumanWireRepository`, `DeliveryInstruction`, and `WorkflowResult`.
- Produces: `CaspianGateway.connect()`, `listen()`, `dispatch()`, `to_incoming_message()`, `ApplicationContainer.build()`, `DueActionWorker.run_once/start/stop`, and CLI commands `init-db`, `listen`, `web`, and `smoke`.

- [ ] **Step 1: Port and rewrite failing gateway tests**

Adapt the proven fake client from `tests/test_caspian_gateway.py`. Verify exactly one handler registration after email and Telegram connect, queue concurrency, Gmail quoted-reply extraction, sender normalization, each delivery kind, runtime readiness, and delivery callback behavior.

Add this central assertion:

```python
def test_one_handler_processes_both_channels(gateway, fake_client, workflow) -> None:
    gateway.connect()
    assert fake_client.on_message_registration_count == 1
    fake_client.handler(email_message())
    fake_client.handler(telegram_message())
    assert [call.channel for call in workflow.calls] == [Channel.EMAIL, Channel.TELEGRAM]
```

- [ ] **Step 2: Write failing container and worker tests**

Verify offline construction selects rule fallbacks, configured Featherless selects the real JSON client without opening channels, `run_once()` writes heartbeat and dispatches each due result once, and worker thread name is `humanwire-due-actions`.

- [ ] **Step 3: Run tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_gateway.py tests\humanwire\test_container.py tests\humanwire\test_logging.py -v
```

Expected: FAIL because runtime adapters do not exist.

- [ ] **Step 4: Port the proven gateway boundary**

Copy only transport-safe patterns from `secondsignal.caspian_gateway`: connect email, connect Telegram, register one handler, normalize incoming messages, strip visible Gmail replies, and dispatch reply/send/initiate instructions. Replace verification-specific delivery detection with assignment-aware delivery callbacks using `instruction.assignment_id` and `instruction.mandate_token`.

On `CommError`, call `workflow.mark_delivery_result(instruction, False, now)` once and dispatch returned recovery messages with failure callbacks disabled. All successful deliveries call the same method with `True` so the event log matches reality.

- [ ] **Step 5: Compose all dependencies without network side effects**

`ApplicationContainer.build(settings, clock=None)` creates session factory, repository, directory, rule fallbacks, optional Featherless client, planners/extractors/engines, state machines, interview/alignment/meeting services, and workflow. It does not create `CommClient` or open a network connection.

`DueActionWorker.run_once()` writes `listener.heartbeat`, calls `workflow.process_due(now)`, and dispatches results. The CLI opens channels only in `listen`.

- [ ] **Step 6: Implement safe structured logging and CLI**

Port JSON logging but allow only `mandate_token`, `event_type`, `state`, `person_id`, `department`, `direction`, `channel`, `attempt`, `duration_ms`, `reason`, and `correlation_id` extras. Add a test that raw message text, email addresses, private statements, and secrets are absent.

The CLI description is `AI chief of staff that interviews the organization`. `init-db` prints a password-redacted URL; `web` starts FastAPI; `listen` starts gateway then worker; `smoke` delegates to the smoke script.

- [ ] **Step 7: Run runtime tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_gateway.py tests\humanwire\test_container.py tests\humanwire\test_logging.py -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire tests\humanwire
git diff --check
```

Commit:

```powershell
git add src/humanwire/caspian_gateway.py src/humanwire/container.py src/humanwire/logging_config.py src/humanwire/__main__.py tests/humanwire
git commit -m "feat: connect HumanWire to Caspian channels"
```

## Phase C — Decision Room, Reach, and Analytics

### Task 11: Read-Only Web API and Deterministic Public Demo

**Files:**

- Create: `src/humanwire/web.py`
- Create: `src/humanwire/demo.py`
- Create: `tests/humanwire/test_web.py`
- Create: `tests/humanwire/test_demo.py`

**Interfaces:**

- Consumes: repository query methods, `Settings`, `render_ics`, and all public aggregate projections.
- Produces: `create_app(repository, settings, clock=None, demo_mode=False) -> FastAPI`, `create_demo_app() -> FastAPI`, HTML routes, health routes, and `/api/v1` JSON routes.

- [ ] **Step 1: Write failing API and safety tests**

Create a TestClient and assert:

```python
def test_mandate_api_contains_live_workflow_state_but_no_routes(web_client) -> None:
    payload = web_client.get("/api/v1/mandates/HW-2411").json()
    assert payload["state"] == "interviewing"
    assert payload["next_action"]["event_type"] == "outreach.alternate_send"
    serialized = json.dumps(payload)
    assert "@example.com" not in serialized
    assert "tg-priya" not in serialized
    assert "PRIVATE" not in serialized


def test_public_demo_has_no_mutating_routes(web_client) -> None:
    assert web_client.post("/api/v1/mandates/HW-2411/cancel").status_code == 405
```

Cover mandate list/detail, assignments, outreach events, evidence summary, unknown token 404, liveness, readiness, stale heartbeat, database failure, and ICS download headers.

- [ ] **Step 2: Write failing demo integrity tests**

Assert the fixture contains one manager-originated mandate, downward/lateral/upward assignments, one completed interview, one in progress, one alternate-channel follow-up, at least 12 ordered events, and no real addresses. Health must be ready in demo mode without loading `.env` or the local organization file.

- [ ] **Step 3: Run tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py tests\humanwire\test_demo.py -v
```

Expected: FAIL because web and demo modules do not exist.

- [ ] **Step 4: Implement redacted projections and routes**

Create projection functions for mandate summary, Decision Room detail, reach lanes, event rows, evidence summary, and meeting package. They accept domain objects and return dictionaries without direct contact routes or private content.

Add:

```text
GET /
GET /mandates/{token}
GET /mandates/{token}/reach
GET /mandates/{token}/data
GET /mandates/{token}/meeting.ics
GET /health/live
GET /health/ready
GET /api/v1/mandates
GET /api/v1/mandates/{token}
GET /api/v1/mandates/{token}/stakeholders
GET /api/v1/mandates/{token}/outreach-events
GET /api/v1/mandates/{token}/evidence-summary
```

No POST, PUT, PATCH, or DELETE routes are included in the competition web build.

- [ ] **Step 5: Seed the exact public story**

Seed `HW-2411` at a fixed relative time with Arun Patel as initiator, two downward team-lead assignments complete, Priya in alternate-channel follow-up, Nora acknowledged upward, and Maya reviewing approval. Include a second completed aligned case and one meeting-ready case so list filters are meaningful. Use `sqlite://` and fictional IDs only.

- [ ] **Step 6: Run tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py tests\humanwire\test_demo.py -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire\web.py src\humanwire\demo.py tests\humanwire
git diff --check
```

Commit:

```powershell
git add src/humanwire/web.py src/humanwire/demo.py tests/humanwire/test_web.py tests/humanwire/test_demo.py
git commit -m "feat: expose HumanWire read-only web data"
```

### Task 12: Responsive Decision Room

**Files:**

- Create: `src/humanwire/templates/base.html`
- Create: `src/humanwire/templates/dashboard.html`
- Create: `src/humanwire/templates/mandate.html`
- Create: `src/humanwire/static/styles.css`
- Create: `src/humanwire/static/app.js`
- Modify: `src/humanwire/web.py`
- Modify: `tests/humanwire/test_web.py`

**Interfaces:**

- Consumes: redacted projections from `humanwire.web`.
- Produces: accessible mandate list and Decision Room pages, polling controller `window.HumanWire.refreshMandate(token)`, countdown updates, and status filters.

- [ ] **Step 1: Write failing semantic page tests**

Assert the dashboard and Decision Room contain stable test IDs and real fixture data:

```python
def test_decision_room_exposes_current_step_and_next_action(web_client) -> None:
    html = web_client.get("/mandates/HW-2411").text
    assert 'data-testid="workflow-step-interviewing"' in html
    assert 'aria-current="step"' in html
    assert 'data-testid="next-action"' in html
    assert "Contact Priya through registered Telegram" in html


def test_decision_room_links_to_reach_and_data_views(web_client) -> None:
    html = web_client.get("/mandates/HW-2411").text
    assert 'href="/mandates/HW-2411/reach"' in html
    assert 'href="/mandates/HW-2411/data"' in html
```

Also assert no inline private text, direct destination, fabricated organization-wide counts, or mutating form.

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -k "decision_room or dashboard" -v
```

Expected: FAIL because templates do not exist.

- [ ] **Step 3: Build the application shell and mandate list**

Use a calm navy/ice visual system, minimum body text `14px`, visible keyboard focus, skip link, semantic landmarks, and responsive layout. The list shows active, aligned, meeting-ready, partial, and failed counts plus token, objective, initiator, state, current step, progress, and age.

Demo mode displays an explicit `Interactive fixture — live channel proof is shown in the recorded demo` notice.

- [ ] **Step 4: Build the Decision Room components**

Render:

- lifecycle stepper with completed, current glowing, and future states;
- stakeholder cards with department, direction, safe channel label, acknowledgement, question progress, and last contact;
- selected response ladder with primary, reminder, alternate, acknowledgement, interview, and confirmation steps;
- metrics for reached, complete, unresolved, unreachable, and assumptions (`0` unless an event proves otherwise);
- next-action card with due countdown;
- evidence and conflict summaries;
- append-only event timeline;
- compact Reach preview and links to full views.

At widths below `760px`, stack panels and keep each stakeholder status readable without horizontal scrolling.

- [ ] **Step 5: Add polling without fake state**

`app.js` polls `/api/v1/mandates/{token}` every five seconds only when the document is visible. It updates timestamps/countdown and reloads the page when the server's `updated_at` changes. It never advances state locally. Replay controls on the Decision Room operate only on already returned events.

- [ ] **Step 6: Verify page behavior and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire\web.py tests\humanwire\test_web.py
git diff --check
```

Manually open `http://127.0.0.1:8000/mandates/HW-2411` at desktop width and a 600-pixel viewport. Expected: no overlapping cards, clipped labels, or horizontal page scroll.

Commit:

```powershell
git add src/humanwire/templates src/humanwire/static src/humanwire/web.py tests/humanwire/test_web.py
git commit -m "feat: build the HumanWire Decision Room"
```

### Task 13: Propagation Lanes Reach Page

**Files:**

- Create: `src/humanwire/templates/reach.html`
- Modify: `src/humanwire/static/styles.css`
- Modify: `src/humanwire/static/app.js`
- Modify: `src/humanwire/web.py`
- Modify: `tests/humanwire/test_web.py`

**Interfaces:**

- Consumes: reach-lane projection grouped by `Direction.DOWNWARD`, `Direction.LATERAL`, and `Direction.UPWARD`.
- Produces: responsive Propagation Lanes, event replay, state filters, person detail disclosure, and links to matching technical rows.

- [ ] **Step 1: Write failing Reach projection and HTML tests**

Assert:

```python
def test_reach_page_uses_lanes_not_an_org_chart(web_client) -> None:
    html = web_client.get("/mandates/HW-2411/reach").text
    assert 'data-testid="lane-downward"' in html
    assert 'data-testid="lane-lateral"' in html
    assert 'data-testid="lane-upward"' in html
    assert 'data-testid="org-chart"' not in html
    assert "Arun Patel" in html
    assert "Priya Raman" in html


def test_lane_steps_are_ordered_by_first_contact_time(web_client) -> None:
    payload = web_client.get("/api/v1/mandates/HW-2411/stakeholders").json()
    downward = [item for item in payload if item["direction"] == "downward"]
    assert downward == sorted(downward, key=lambda item: item["first_contact_at"])
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -k "reach or lane" -v
```

Expected: FAIL because Reach page is missing.

- [ ] **Step 3: Implement the approved visual hierarchy**

Render one spacious origin card followed by three lane cards:

```text
Gather input        Direction.DOWNWARD
Coordinate policy  Direction.LATERAL
Get approval        Direction.UPWARD
```

Each lane step shows person, role/department, status, last safe channel label, timestamp, and concise result. Completed steps use green, active steps use a restrained glow, follow-up uses amber, and pending steps use neutral gray. On wide screens use three columns; below `850px` stack lanes. Do not draw connector lines between lanes or render a compressed hierarchy.

- [ ] **Step 4: Add replay, filters, and details from saved events**

Replay iterates through event timestamps and highlights only the matching persisted lane step. Filters hide/show complete, active, pending, and unreachable steps. Clicking a person expands delivery/acknowledgement/interview history already present in the API projection and provides `View technical rows` linking to `/data?person_id=<id>`.

No animation changes a case state, sends a message, or fabricates an intermediate event.

- [ ] **Step 5: Verify responsive rendering and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -v
git diff --check
```

Inspect at `1280x720` and `600x900`. Expected: three columns at desktop, stacked readable cards at compact width, no font below `11px`, no horizontal overflow, and no overlapping labels.

Commit:

```powershell
git add src/humanwire/templates/reach.html src/humanwire/static src/humanwire/web.py tests/humanwire/test_web.py
git commit -m "feat: visualize mandate propagation lanes"
```

### Task 14: Technical Event Table, CSV, JSON, and Power BI Contract

**Files:**

- Create: `src/humanwire/templates/data.html`
- Modify: `src/humanwire/web.py`
- Modify: `src/humanwire/static/styles.css`
- Modify: `tests/humanwire/test_web.py`
- Create: `docs/analytics.md`

**Interfaces:**

- Consumes: append-only domain events and redacted assignment projections.
- Produces: `/mandates/{token}/data`, `/api/v1/mandates/{token}/outreach-events.csv`, stable JSON fields, filtering query parameters, and Power BI Web connector instructions.

- [ ] **Step 1: Write failing table and export tests**

Cover filter by department, person ID, channel, direction, event type, and timestamp range. Assert stable CSV headers:

```python
EXPECTED_HEADERS = [
    "mandate_token", "timestamp", "initiator_id", "source_department",
    "target_person_id", "target_department", "direction", "channel",
    "event_type", "previous_state", "new_state", "outcome", "response_latency_seconds",
]


def test_csv_is_redacted_and_power_bi_compatible(web_client) -> None:
    response = web_client.get("/api/v1/mandates/HW-2411/outreach-events.csv")
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert response.headers["content-type"].startswith("text/csv")
    assert list(rows[0]) == EXPECTED_HEADERS
    assert "@" not in response.text
    assert "tg-priya" not in response.text
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -k "csv or data_table or filter" -v
```

Expected: FAIL because data page/export is missing.

- [ ] **Step 3: Implement one canonical analytics projection**

Create `_outreach_rows(repository, mandate, filters)` and use it for HTML, JSON, and CSV. Compute response latency from the first delivery event to the first acknowledgement/answer event. Use empty strings for unavailable optional values, ISO-8601 UTC timestamps, lowercase enum values, deterministic event order, and `Content-Disposition: attachment; filename="HW-2411-outreach-events.csv"`.

The endpoint must not serialize arbitrary event metadata. Map only approved fields into the stable contract.

- [ ] **Step 4: Build the separate table page and documentation**

The table page shows filter controls, export links, row count, last updated time, and columns matching the API. Keep it horizontally scrollable inside the table container rather than the full page.

`docs/analytics.md` documents:

1. Power BI Desktop -> Get Data -> Web.
2. Use the authenticated JSON URL or downloaded CSV.
3. Expand JSON records using the stable fields above.
4. Refresh using a read-only token in non-demo environments.
5. Never connect Power BI directly to `humanwire.db`.

- [ ] **Step 5: Run tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire\web.py tests\humanwire\test_web.py
git diff --check
```

Commit:

```powershell
git add src/humanwire/templates/data.html src/humanwire/static/styles.css src/humanwire/web.py tests/humanwire/test_web.py docs/analytics.md
git commit -m "feat: expose HumanWire analytics data"
```

## Phase D — Cutover, Proof, and Deployment

### Task 15: Full Integration, Seed Utilities, and Smoke Harness

**Files:**

- Create: `scripts/smoke_humanwire.py`
- Modify: `tests/humanwire/test_workflow.py`
- Modify: `tests/humanwire/test_caspian_gateway.py`
- Modify: `tests/humanwire/test_demo.py`
- Modify: `src/humanwire/__main__.py`

**Interfaces:**

- Consumes: complete HumanWire package.
- Produces: deterministic offline smoke report and opt-in live checklist without transmitting until explicitly run with `--live`.

- [ ] **Step 1: Add a single complete integration test**

Drive this sequence through `HumanWireWorkflow.handle()` using real repository/services and fake channel/model adapters:

```text
manager /mandate
US lead answer on email
APAC lead answer on Telegram
People email timeout
People Telegram ACK and answers
VP Support ACCEPT
COO CHANGE
round 2 unresolved
availability replies
MEETING_READY
```

Assert the final package, minimum attendee set, event ordering, channel switch continuity, two-round cap, and absence of private evidence from every delivery/export.

- [ ] **Step 2: Add a fake-Caspian transport integration test**

Feed both email and Telegram message objects through the one registered handler and assert dispatched replies, initiations, send-message calls, delivery callbacks, and final state all match the workflow test. No test may call Caspian or Featherless over the network.

- [ ] **Step 3: Implement offline smoke harness**

`scripts/smoke_humanwire.py` builds the deterministic demo, calls health, list, detail, Reach, data, CSV, and ICS endpoints, then prints exactly:

```text
PASS domain
PASS interview-ladder
PASS negotiation-limit
PASS meeting-package
PASS decision-room
PASS propagation-lanes
PASS analytics-export
PASS privacy-scan
```

Exit nonzero on any failed assertion. `--live` only prints the operator checklist and requires `--confirm-live`; it does not synthesize or send test messages automatically.

- [ ] **Step 4: Run the integration gate and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire -v
.\.venv\Scripts\python.exe scripts\smoke_humanwire.py
.\.venv\Scripts\python.exe -m ruff check src\humanwire tests\humanwire scripts\smoke_humanwire.py
git diff --check
```

Commit:

```powershell
git add scripts/smoke_humanwire.py src/humanwire/__main__.py tests/humanwire
git commit -m "test: prove the HumanWire product flow"
```

### Task 16: Product Cutover, Documentation, Submission Assets, and Live Deployment

**Files:**

- Modify: `src/index.py`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/threat-model.md`
- Modify: `docs/demo-script.md`
- Create: `submission/caspian.md`
- Create: `submission/ml-empowerment.md`
- Create: `submission/build-beyond.md`
- Modify: `submission/checklist.md`
- Delete after verification: `src/secondsignal/`
- Delete after verification: obsolete root `tests/test_*.py` files for SecondSignal
- Delete after verification: `config/demo-identities.example.json`
- Delete after verification: `scripts/capture_telegram_route.py`
- Delete after verification: `scripts/seed_demo_registry.py`

**Interfaces:**

- Consumes: the fully verified HumanWire package and approved product specification.
- Produces: coherent public repository, HumanWire CLI, HumanWire Vercel demo, live-listener instructions, three truthful submission narratives, and final verification evidence.

- [ ] **Step 1: Switch the public entrypoint only after the integration gate passes**

Change `src/index.py` to:

```python
"""Vercel entrypoint for the safe, read-only HumanWire demo."""

from humanwire.demo import create_demo_app

app = create_demo_app()
```

Run the full HumanWire tests immediately. If they fail, restore the import before any deployment.

- [ ] **Step 2: Rewrite public documentation around one product**

README order:

1. Live demo link and 20-second pitch.
2. The coordination problem.
3. 75–90 second product flow.
4. Why Caspian and Featherless are essential.
5. Architecture and safety invariants.
6. Local setup and organization seed.
7. Listener and web commands.
8. Tests, offline smoke, and live checklist.
9. Analytics/Power BI contract.
10. Limitations and calendar boundary.

Rewrite architecture, threat model, and demo script from the approved HumanWire specification. Do not retain SecondSignal claims, screenshots, commands, or verification terminology.

- [ ] **Step 3: Create differentiated but consistent submission copy**

Each submission document contains problem, solution, live flow, technology, responsible-AI boundaries, setup/demo link, limitations, and proof checklist. Emphasize:

- Caspian: one handler, two live channels, real failover and replies.
- ML Empowerment: Featherless planning/extraction/alignment plus human authority.
- Build Beyond: full agentic workflow, persistence, meeting preparation, and analytics interoperability.

Do not claim organizer endorsement, calendar writes, Power BI certification, production security certification, or live actions not actually demonstrated.

- [ ] **Step 4: Remove obsolete product code only after cutover tests pass**

Verify no imports outside `docs/superpowers` reference `secondsignal`:

```powershell
rg -n "secondsignal|SecondSignal|SS-" src tests scripts config README.md docs submission -g '!docs/superpowers/**'
```

Delete the old package, old product tests, old registry example, and old seed/capture scripts. Preserve the historical SecondSignal design and implementation plan under `docs/superpowers` as development history. Run editable install again so stale package metadata cannot mask missing imports.

- [ ] **Step 5: Run the final local quality gate**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe scripts\smoke_humanwire.py
git diff --check
git status --short
```

Expected: every test and smoke check passes; only intended HumanWire changes are present; no secret or database file is staged.

- [ ] **Step 6: Commit the coherent product cutover**

```powershell
git add src tests scripts config README.md docs submission pyproject.toml .env.example
git status --short
git commit -m "feat: launch HumanWire"
```

- [ ] **Step 7: Run the real live-channel proof**

With the user's already configured Caspian, Telegram, and Featherless credentials:

1. Start `humanwire listen`.
2. Send the manager-originated mandate from the registered Telegram initiator.
3. Complete at least one email interview and one Telegram interview.
4. Allow one email acknowledgement window to expire and verify the alternate Telegram outreach.
5. Complete proposal or meeting-ready flow.
6. Confirm the Decision Room events match the real channel events.
7. Repeat the complete flow three times without database edits or code changes.

Record tokens, timestamps, and safe screenshots only; never record keys, direct addresses, or private answers.

- [ ] **Step 8: Deploy and verify the Vercel public demo**

Deploy from the already linked Vercel project using the installed Vercel workflow/CLI procedure. Then verify:

```text
GET https://secondsignal.vercel.app/                         -> 200 HumanWire dashboard
GET https://secondsignal.vercel.app/mandates/HW-2411        -> 200 Decision Room
GET https://secondsignal.vercel.app/mandates/HW-2411/reach  -> 200 Propagation Lanes
GET https://secondsignal.vercel.app/health/live              -> 200
```

The domain may remain `secondsignal.vercel.app` for continuity, but the visible product, page titles, metadata, and content must all say HumanWire. If a HumanWire domain is added later, configure a redirect rather than breaking the submitted URL.

- [ ] **Step 9: Final verification and release commit if deployment metadata changes**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe scripts\smoke_humanwire.py
git diff --check
git status --short
```

If tracked deployment or documentation files changed, commit only those files:

```powershell
git add README.md submission docs src/index.py
git commit -m "docs: finalize HumanWire launch evidence"
```

## Completion Checklist

- [ ] One real Caspian handler processes both Telegram and email.
- [ ] Manager, executive, or other authorized origin is represented correctly.
- [ ] Real interviews persist across a channel switch.
- [ ] Response ladder visibly reaches reminder, alternate channel, and unreachable states.
- [ ] Evidence provenance and visibility rules pass automated tests.
- [ ] Silence and delivery failure never produce alignment.
- [ ] Two-round negotiation cap is enforced.
- [ ] Aligned and meeting-ready paths both work.
- [ ] Decision Room reflects only persisted events.
- [ ] Propagation Lanes are clean at desktop and compact widths.
- [ ] Technical table, JSON, CSV, and Power BI instructions use one redacted contract.
- [ ] Public demo is deterministic and clearly labeled.
- [ ] Live channel proof succeeds three consecutive times.
- [ ] Full tests, Ruff, smoke, and `git diff --check` pass.
- [ ] No secrets, contact destinations, private evidence, database files, or `.vercel` metadata are committed.
- [ ] README, architecture, threat model, demo script, and three submission documents describe only verified behavior.
