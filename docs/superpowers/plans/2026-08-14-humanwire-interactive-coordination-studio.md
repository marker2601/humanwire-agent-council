# HumanWire Interactive Coordination Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Replace the proof-first local viewer with a two-screen HumanWire product that starts a fresh request-driven coordination, shows realistic named stakeholder agents progressing through conflict, interview, approval, availability, and meeting creation, and replays/downloads the exact saved workflow.

**Architecture:** Keep HumanWire's existing workflow, repository, CaspianGateway boundary, hard-timeout persona isolation, and replay mapping authoritative. Add a strict request/catalog layer, a request-scoped single-mandate runner, a PydanticAI Slim persona adapter behind the existing spawn-safe factory protocol, a richer private-safe product projection, and a loopback-only FastAPI studio with a vanilla-JavaScript graph workspace. The public Vercel app remains read-only and does not mount studio routes or assets.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, Pydantic 2, SQLAlchemy/SQLite, PydanticAI Slim with its OpenAI-compatible provider extra, existing HumanWire CaspianGateway/workflow/repository, vanilla JavaScript, SVG, CSS, pytest, Node DOM harness, Ruff.

## Global Constraints

- The primary UI must not contain the visible words synthetic, fake, proof_class, actor_type, local simulation viewer, or simulated persona.
- The primary UI must use the approved professional identities: Alex Morgan, Maya Chen, Nora Jensen, Priya Shah, Marcus Reed, Anika Rao, Sofia Alvarez, Daniel Brooks, and optional Elena Torres.
- Every animated transition must correspond to an already-saved event or an explicitly inert saved attempt.
- Agents may choose bounded responses only; they may not own sender, route, conversation, message, token, database, workflow, approval, or scheduling authority.
- Every non-silence agent response must traverse the one registered CaspianGateway handler.
- The external-provider state may say Provider connected only after a separately verified connection; the default local product uses the quiet label Workspace channels.
- The first release binds only to 127.0.0.1, permits one active run, and creates a fresh exclusive child run root for every coordination.
- The public Vercel application remains GET-only and must expose neither studio mutation routes nor studio-only JavaScript/CSS.
- JSON and CSV remain disabled until final transcript binding succeeds and must download as attachments without browser navigation.
- Meaningful text must be at least 14 px; every focusable control must be at least 44 by 44 px; the page must have no horizontal overflow at 1280x720, 600x900, or 390x844.
- Reduced-motion users receive state highlighting without path-travel animation; page-hidden playback pauses.
- Existing deterministic generation, frozen replay, smoke output, privacy scans, PostgreSQL opt-in gates, and public-demo tests must remain green.

## File and Responsibility Map

- Create src/humanwire/studio_models.py: strict request, role, timing, agent-mode, catalog, template, and professional scenario construction.
- Create src/humanwire/pydantic_persona.py: PydanticAI-only persona engine and spawn-safe factory; no workflow or repository access.
- Create src/humanwire/studio_projection.py: private-safe conversation, graph, lifecycle, data-trail, outcome, and immutable snapshot models/store/observer.
- Create src/humanwire/studio_run.py: one-active-run manager, fresh-root allocation, background worker lifecycle, model-mode readiness, and run lookup.
- Create src/humanwire/studio_app.py: loopback security middleware, composer/workspace routes, run creation API, polling API, and final exports.
- Create src/humanwire/templates/coordination_studio.html: the approved composer and live-workspace shells.
- Create src/humanwire/studio_static/coordination-studio.css: studio-scoped responsive product styling and motion.
- Create src/humanwire/studio_static/coordination-studio.js: composer submission, safe polling, graph transitions, conversation/data rendering, replay, and downloads.
- Modify src/humanwire/synthetic.py: accept one bounded request objective, disable the independent change story for product runs, and emit safe presentation callbacks without changing default proof behavior.
- Modify src/humanwire/persona_runtime.py: expose the shared prompt builder and retain central PersonaDecision validation for both adapters.
- Modify src/humanwire/__main__.py: add the humanwire studio command without changing existing synthetic commands.
- Modify pyproject.toml: add the bounded PydanticAI dependency and package studio-only assets.
- Modify README.md: document the product command, Standard versus AI-assisted reasoning, and external-provider truth boundary.
- Create tests/humanwire/test_studio_models.py: request/catalog/scenario behavior.
- Create tests/humanwire/test_pydantic_persona.py: typed adapter, privacy, deadline, and no-tool boundary.
- Create tests/humanwire/test_studio_projection.py: exact graph/message/data synchronization and privacy.
- Create tests/humanwire/test_studio_run.py: one-active-run lifecycle, fresh roots, recovery, and second-run isolation.
- Create tests/humanwire/test_studio_app.py: loopback API/security/download/public-separation behavior.
- Create tests/humanwire/test_studio_frontend.py: rendered structure, copy, accessibility, responsive CSS contract, and executable DOM interactions.
- Create tests/humanwire/test_studio_e2e.py: request-to-meeting product acceptance through the real gateway/workflow/repository path.

## Shared Test Fixture Contract

Every new Python test module that needs a product request imports this exact helper from tests/humanwire/studio_fixtures.py. Create that test-support file in Task 1:

~~~python
from humanwire.studio_models import CoordinationRequest


_PRIMARY_PARTICIPANTS = (
    "inform",
    "ack",
    "quick-a",
    "quick-b",
    "structured",
    "approval",
    "availability",
)


def launch_request(**updates: object) -> CoordinationRequest:
    values: dict[str, object] = {
        "template_id": "launch-decision",
        "objective": "Set up a decision meeting tomorrow to approve the launch plan.",
        "requester_name": "Alex Morgan",
        "requester_role": "manager",
        "participant_ids": _PRIMARY_PARTICIPANTS,
        "target_timing": "tomorrow",
        "custom_date": None,
        "include_conflict": True,
        "agent_mode": "standard",
    }
    values.update(updates)
    return CoordinationRequest.model_validate(values)


def conflict_request(**updates: object) -> CoordinationRequest:
    values: dict[str, object] = {
        **launch_request().model_dump(mode="python"),
        "template_id": "cross-team-conflict",
        "objective": (
            "Resolve the launch-readiness disagreement between Product, "
            "Engineering, and Risk."
        ),
        "requester_role": "program_lead",
        "participant_ids": ("quick-a", "quick-b", "structured", "approval"),
    }
    values.update(updates)
    return CoordinationRequest.model_validate(values)
~~~

---

### Task 1: Request Catalog and Request-Scoped Scenario

**Files:**
- Create: src/humanwire/studio_models.py
- Modify: src/humanwire/synthetic.py at SyntheticScenario construction and generate_scenario
- Create: tests/humanwire/studio_fixtures.py
- Create: tests/humanwire/test_studio_models.py
- Modify: tests/humanwire/test_synthetic.py for default-argument compatibility

**Interfaces:**
- Produces: RequesterRole, TargetTiming, StudioAgentMode, CoordinationRequest, StakeholderCard, CoordinationTemplate, StudioCatalog, product_catalog(), and build_coordination_scenario(request, seed, scenario_id).
- Extends: generate_scenario(..., mandate_request: str | None = None, include_change_story: bool | None = None) while preserving current behavior when both are None.
- Consumed by: Task 3 projection metadata, Task 4 run manager, Task 5 API, and Task 6 composer.

- [ ] **Step 1: Write the failing strict-model and catalog tests**

~~~python
import pytest
from pydantic import ValidationError

from humanwire.studio_models import (
    CoordinationRequest,
    RequesterRole,
    StudioAgentMode,
    TargetTiming,
    product_catalog,
)


def test_product_catalog_uses_approved_names_templates_and_copy() -> None:
    catalog = product_catalog()
    assert [person.display_name for person in catalog.stakeholders] == [
        "Maya Chen",
        "Nora Jensen",
        "Priya Shah",
        "Marcus Reed",
        "Anika Rao",
        "Sofia Alvarez",
        "Daniel Brooks",
        "Elena Torres",
    ]
    launch = next(item for item in catalog.templates if item.template_id == "launch-decision")
    assert launch.objective == "Set up a decision meeting tomorrow to approve the launch plan."
    assert launch.requester_role is RequesterRole.MANAGER
    assert launch.target_timing is TargetTiming.TOMORROW
    assert launch.include_conflict is True


def test_coordination_request_is_strict_bounded_and_unique() -> None:
    valid = CoordinationRequest(
        template_id="launch-decision",
        objective="Set up a decision meeting tomorrow to approve the launch plan.",
        requester_name="Alex Morgan",
        requester_role="manager",
        participant_ids=[
            "inform",
            "ack",
            "quick-a",
            "quick-b",
            "structured",
            "approval",
            "availability",
        ],
        target_timing="tomorrow",
        include_conflict=True,
        agent_mode="standard",
    )
    assert valid.agent_mode is StudioAgentMode.STANDARD
    for mutation in (
        {**valid.model_dump(), "unknown": True},
        {**valid.model_dump(), "participant_ids": ["quick-a", "quick-a"]},
        {**valid.model_dump(), "objective": "x" * 1001},
        {**valid.model_dump(), "requester_name": "Someone Else"},
    ):
        with pytest.raises(ValidationError):
            CoordinationRequest.model_validate(mutation)
~~~

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_studio_models.py -v
~~~

Expected: collection fails with ModuleNotFoundError for humanwire.studio_models.

- [ ] **Step 3: Implement the strict request/catalog models**

Use frozen, strict, extra-forbid Pydantic models. The public constructor surface is:

~~~python
class RequesterRole(StrEnum):
    MANAGER = "manager"
    EXECUTIVE = "executive"
    PROGRAM_LEAD = "program_lead"
    TEAM_LEAD = "team_lead"


class TargetTiming(StrEnum):
    TOMORROW = "tomorrow"
    NEXT_BUSINESS_DAY = "next_business_day"
    CUSTOM = "custom"


class StudioAgentMode(StrEnum):
    STANDARD = "standard"
    MODEL_ASSISTED = "model_assisted"


class CoordinationRequest(_StudioModel):
    template_id: str | None = Field(default=None, pattern=_SAFE_ID)
    objective: str = Field(min_length=12, max_length=1000)
    requester_name: Literal["Alex Morgan"] = "Alex Morgan"
    requester_role: RequesterRole
    participant_ids: tuple[str, ...] = Field(min_length=3, max_length=8)
    target_timing: TargetTiming
    custom_date: date | None = None
    include_conflict: bool = True
    agent_mode: StudioAgentMode = StudioAgentMode.STANDARD

    @model_validator(mode="after")
    def has_valid_participants_and_timing(self) -> Self:
        allowed = {person.persona_id for person in product_catalog().stakeholders}
        if len(set(self.participant_ids)) != len(self.participant_ids):
            raise ValueError("participant IDs must be unique")
        if not set(self.participant_ids) <= allowed:
            raise ValueError("participant IDs must come from the product catalog")
        if (self.target_timing is TargetTiming.CUSTOM) != (self.custom_date is not None):
            raise ValueError("custom timing requires exactly one custom date")
        return self
~~~

Define all eight stakeholder rows and three templates as immutable tuples. Do not derive presentation names from the seeded proof-name generator.

- [ ] **Step 4: Write the failing request-scoped scenario test**

~~~python
def test_build_coordination_scenario_is_single_story_with_product_identities() -> None:
    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="launch-run-001")
    assert scenario.scenario_id == "launch-run-001"
    assert [item.persona_id for item in scenario.personas] == [
        "synthetic-manager",
        "inform",
        "ack",
        "quick-a",
        "quick-b",
        "structured",
        "approval",
        "availability",
    ]
    assert scenario.personas[0].display_name == "Alex Morgan"
    assert scenario.personas[0].role == "Strategy manager"
    assert "approval-change" not in {item.persona_id for item in scenario.personas}
    assert scenario.personas[5].display_name == "Anika Rao"
    assert scenario.personas[5].role == "Risk & compliance lead"
~~~

- [ ] **Step 5: Implement product scenario construction**

build_coordination_scenario must:

1. call default_synthetic_scenario(seed);
2. retain the internal manager plus only request.participant_ids;
3. replace display_name and role from the approved catalog;
4. set the manager display to Alex Morgan and map requester_role to a presentation role;
5. set scenario_id to the supplied safe alias; and
6. omit approval-change unless the selected template explicitly includes that participant.

No private live organization file or environment value may be read.

- [ ] **Step 6: Write RED tests for request text and change-story control**

~~~python
def test_generate_scenario_uses_submitted_objective_and_only_one_mandate(tmp_path) -> None:
    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="launch-run-001")
    result = generate_scenario(
        scenario,
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        mandate_request=request.objective,
        include_change_story=False,
    )
    assert result.terminal_states == ("meeting_ready",)
    session_factory = create_session_factory(
        "sqlite:///" + result.database_path.as_posix()
    )
    repository = SqlAlchemyHumanWireRepository(session_factory)
    mandates = repository.list_recent_mandates(10)
    assert len(mandates) == 1
    assert mandates[0].redacted_request == request.objective
    session_factory.kw["bind"].dispose()


def test_generate_scenario_defaults_preserve_frozen_proof_contract(tmp_path) -> None:
    result = generate_scenario(
        default_synthetic_scenario(seed=0),
        tmp_path / "proof" / "transcript.json",
        tmp_path / "proof",
    )
    assert result.terminal_states == ("meeting_ready", "partial")
~~~

- [ ] **Step 7: Implement the two backward-compatible generation parameters**

At the generate_scenario signature, add:

~~~python
    mandate_request: str | None = None,
    include_change_story: bool | None = None,
~~~

Before constructing the manager envelope:

~~~python
request_text = (
    "Coordinate the deterministic synthetic launch"
    if mandate_request is None
    else mandate_request.strip()
)
if not 12 <= len(request_text) <= 1000:
    raise ValueError("mandate request must be between 12 and 1000 characters")
~~~

Set its text to "/mandate\n" plus request_text. Replace the current change-story boolean with:

~~~python
change_story_enabled = (
    "approval-change" in persona_by_id
    if include_change_story is None
    else include_change_story and "approval-change" in persona_by_id
)
~~~

Do not change replay_transcript or the default proof transcript.

- [ ] **Step 8: Run Task 1 GREEN and adjacent regression**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_studio_models.py tests\humanwire\test_synthetic.py -k "studio or request_scoped or defaults_preserve" -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire\studio_models.py src\humanwire\synthetic.py tests\humanwire\test_studio_models.py
~~~

Expected: all selected tests pass; Ruff reports All checks passed.

- [ ] **Step 9: Commit Task 1**

~~~powershell
git add src/humanwire/studio_models.py src/humanwire/synthetic.py tests/humanwire/studio_fixtures.py tests/humanwire/test_studio_models.py tests/humanwire/test_synthetic.py
git commit -m "feat: accept HumanWire coordination requests"
~~~

---

### Task 2: PydanticAI Stakeholder Decision Adapter

**Files:**
- Create: src/humanwire/pydantic_persona.py
- Modify: src/humanwire/persona_runtime.py
- Modify: pyproject.toml
- Create: tests/humanwire/test_pydantic_persona.py
- Modify: tests/humanwire/test_persona_runtime.py only for shared prompt compatibility

**Interfaces:**
- Consumes: PersonaProfile, PersonaContext, PersonaDecision, PersonaDecisionEngine, PersonaDecisionEngineFactory, and validate_persona_decision.
- Produces: PydanticAIPersonaDecisionEngine and PydanticAIPersonaDecisionEngineFactory.
- The factory remains a frozen strict Pydantic object and is safe to construct in the existing spawned child process.
- Consumed by: Task 4 StudioRunManager when agent_mode is model_assisted.

- [ ] **Step 1: Add the dependency and write the import/factory RED tests**

Add this exact project dependency:

~~~toml
"pydantic-ai-slim[openai]>=1,<2",
~~~

Then add:

~~~python
from pydantic import SecretStr

from humanwire.pydantic_persona import PydanticAIPersonaDecisionEngineFactory


def test_pydantic_factory_is_strict_spawn_safe_configuration() -> None:
    factory = PydanticAIPersonaDecisionEngineFactory(
        api_key=SecretStr("private-test-key"),
        model_identifier="Qwen/Qwen2.5-7B-Instruct",
        base_url="https://api.featherless.ai/v1",
    )
    assert factory.model_identifier == "Qwen/Qwen2.5-7B-Instruct"
    assert "private-test-key" not in repr(factory)
    with pytest.raises(ValidationError):
        PydanticAIPersonaDecisionEngineFactory(
            api_key=SecretStr("private-test-key"),
            model_identifier="model",
            base_url="https://api.featherless.ai/v1",
            extra=True,
        )
~~~

- [ ] **Step 2: Install the locked editable project and verify RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_pydantic_persona.py -v
~~~

Expected: collection fails because humanwire.pydantic_persona does not exist.

- [ ] **Step 3: Extract one shared persona prompt payload**

In persona_runtime.py create:

~~~python
def persona_prompt_payload(
    profile: PersonaProfile,
    context: PersonaContext,
) -> tuple[str, str]:
    system = (
        "You are one HumanWire stakeholder. Use only the supplied role, constraints, "
        "allowed actions, and your own conversation. Return one typed response. "
        "Never invent identity, routing, authority, credentials, tools, or workflow state."
    )
    user = json.dumps(
        {
            "profile": profile.model_dump(mode="json"),
            "context": context.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return system, user
~~~

Update FeatherlessPersonaDecisionEngine to call this helper and append only its existing JSON schema instructions. Preserve current direct-adapter behavior and tests.

- [ ] **Step 4: Write typed-output, privacy, and deadline RED tests**

Use this exact monkeypatched PydanticAI boundary:

~~~python
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event
from types import SimpleNamespace


@dataclass
class AgentCall:
    user: str
    model_settings: object


def fake_agent_class(decision: PersonaDecision, calls: list[AgentCall]):
    class FakeAgent:
        def __init__(self, model, *, output_type, system_prompt, retries):
            assert output_type is PersonaDecision
            assert retries == 0
            self.system_prompt = system_prompt

        def run_sync(self, user, *, model_settings):
            calls.append(AgentCall(user=user, model_settings=model_settings))
            return SimpleNamespace(output=decision)

    return FakeAgent


def quick_profile() -> PersonaProfile:
    return PersonaProfile(
        role="Product lead",
        private_facts=("PRIVATE-PERSONA-SENTINEL",),
        allowed_intents=(SyntheticIntent.ANSWER,),
        engagement_contract=EngagementType.QUICK_RESPONSE,
    )


def own_context() -> PersonaContext:
    return PersonaContext(
        delivered_message="Does the launch scope support a decision tomorrow?",
        own_inbox=("Does the launch scope support a decision tomorrow?",),
        own_transcript=(),
        virtual_time=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
    )


def valid_answer() -> PersonaDecision:
    return PersonaDecision(
        time_offset_seconds=1,
        intent=SyntheticIntent.ANSWER,
        content="The launch can proceed if rollback ownership is recorded.",
        visibility=PersonaVisibility.SHAREABLE,
    )


def engine_returning(monkeypatch, decision, calls=None):
    recorded = [] if calls is None else calls
    monkeypatch.setattr(
        "humanwire.pydantic_persona.Agent",
        fake_agent_class(decision, recorded),
    )
    return PydanticAIPersonaDecisionEngine(
        model=object(),
        model_identifier="test-model",
    )


def test_pydantic_engine_returns_centrally_validated_typed_decision(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "humanwire.pydantic_persona.Agent",
        fake_agent_class(
            PersonaDecision(
                time_offset_seconds=1,
                intent="answer",
                content="The launch can proceed if rollback ownership is recorded.",
                visibility="shareable",
            ),
            calls,
        ),
    )
    engine = PydanticAIPersonaDecisionEngine(
        model=object(),
        model_identifier="test-model",
    )
    decision = engine.decide(
        quick_profile(),
        own_context(),
        deadline=time.monotonic() + 2,
        cancellation=Event(),
    )
    assert decision.intent is SyntheticIntent.ANSWER
    assert len(calls) == 1
    assert set(json.loads(calls[0].user)) == {"context", "profile"}
    assert "sender_address" not in calls[0].user


@pytest.mark.parametrize(
    "decision",
    [
        PersonaDecision(
            time_offset_seconds=1,
            intent="approve",
            content="Approved",
            visibility="shareable",
        ),
        PersonaDecision(
            time_offset_seconds=1,
            intent="answer",
            content="PRIVATE-PERSONA-SENTINEL",
            visibility="shareable",
        ),
        PersonaDecision(
            time_offset_seconds=1,
            intent="answer",
            content="route_id=forged",
            visibility="shareable",
        ),
    ],
)
def test_pydantic_engine_cannot_bypass_central_validation(monkeypatch, decision) -> None:
    engine = engine_returning(monkeypatch, decision)
    with pytest.raises(ValueError):
        engine.decide(
            quick_profile(),
            own_context(),
            deadline=time.monotonic() + 2,
            cancellation=Event(),
        )


def test_pydantic_engine_refuses_expired_or_cancelled_work_without_call(monkeypatch) -> None:
    calls = []
    engine = engine_returning(monkeypatch, valid_answer(), calls)
    cancellation = Event()
    cancellation.set()
    with pytest.raises(ModelFailure, match="timeout"):
        engine.decide(
            quick_profile(),
            own_context(),
            deadline=time.monotonic() - 1,
            cancellation=cancellation,
        )
    assert calls == []
~~~

- [ ] **Step 5: Implement the thin PydanticAI engine**

The module must construct no tools and no workflow dependencies:

~~~python
class PydanticAIPersonaDecisionEngine:
    def __init__(self, model: Model, model_identifier: str) -> None:
        self._model = model
        self.model_identifier = model_identifier

    def decide(
        self,
        profile: PersonaProfile,
        context: PersonaContext,
        *,
        deadline: float,
        cancellation: Event,
    ) -> PersonaDecision:
        remaining = deadline - time.monotonic()
        if cancellation.is_set() or remaining <= 0:
            raise ModelFailure("timeout")
        system, user = persona_prompt_payload(profile, context)
        agent = Agent(
            self._model,
            output_type=PersonaDecision,
            system_prompt=system,
            retries=0,
        )
        try:
            result = agent.run_sync(
                user,
                model_settings=ModelSettings(timeout=remaining, temperature=0),
            )
        except Exception as error:
            if cancellation.is_set() or time.monotonic() >= deadline:
                raise ModelFailure("timeout") from error
            raise ModelFailure("invalid_response") from error
        if cancellation.is_set() or time.monotonic() >= deadline:
            raise ModelFailure("timeout")
        return validate_persona_decision(profile, result.output)
~~~

The production factory constructs OpenAIProvider with explicit api_key and base_url, then OpenAIChatModel with model_identifier. It must not rely on OPENAI_API_KEY, OPENAI_BASE_URL, or another ambient model variable.

- [ ] **Step 6: Prove factory construction remains inside the killable child**

Add a focused generation test using PydanticAIPersonaDecisionEngineFactory with the existing subprocess harness monkeypatched at the provider HTTP boundary. Assert:

- the parent never stores an Agent, model client, or raw key;
- a timeout produces one inert model-timeout action;
- no child process or humanwire-persona thread survives; and
- successful typed decisions still traverse the one gateway handler.

- [ ] **Step 7: Run Task 2 GREEN and existing hard-timeout regressions**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_pydantic_persona.py tests\humanwire\test_persona_runtime.py tests\humanwire\test_synthetic.py -k "pydantic or persona or timeout or model_assisted" -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire\pydantic_persona.py src\humanwire\persona_runtime.py tests\humanwire\test_pydantic_persona.py
~~~

Expected: all selected tests pass and no network request occurs.

- [ ] **Step 8: Commit Task 2**

~~~powershell
git add pyproject.toml src/humanwire/persona_runtime.py src/humanwire/pydantic_persona.py tests/humanwire/test_persona_runtime.py tests/humanwire/test_pydantic_persona.py
git commit -m "feat: add typed HumanWire stakeholder agents"
~~~

---

### Task 3: Rich Safe Workspace Projection

**Files:**
- Create: src/humanwire/studio_projection.py
- Modify: src/humanwire/synthetic.py at delivery collection, action commit, and progress publication boundaries
- Create: tests/humanwire/test_studio_projection.py
- Modify: tests/humanwire/test_synthetic.py for callback regression

**Interfaces:**
- Consumes: CoordinationRequest, SyntheticProgressSnapshot, RepositoryProgressObserver, project_replay_labels, and already-validated PersonaDecision content.
- Produces: StudioLifecycleStage, StudioTransition, StudioTimelineEvent, StudioConversationItem, StudioGraphNode, StudioGraphEdge, StudioDataPoint, StudioOutcome, StudioWorkspaceSnapshot, StudioProgressStore, StudioProgressObserver, and create_studio_progress(request, scenario).
- Extends: generate_scenario(..., presentation_observer: StudioPresentationObserver | None = None).
- Consumed by: Task 4 run manager and Task 5 APIs.

- [ ] **Step 1: Write strict projection and privacy RED tests**

~~~python
def test_initial_workspace_is_product_copy_with_approved_graph() -> None:
    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="launch-001")
    store, observer = create_studio_progress(request, scenario)
    snapshot = store.snapshot()
    assert snapshot.objective == request.objective
    assert snapshot.requester_name == "Alex Morgan"
    assert snapshot.lifecycle.current == "brief"
    assert [node.label for node in snapshot.graph_nodes[:3]] == [
        "Request",
        "HumanWire",
        "Caspian Gateway",
    ]
    dumped = snapshot.model_dump_json()
    for forbidden in (
        "proof_class",
        "actor_type",
        "simulated_persona",
        "fake_caspian",
        "PRIVATE-PERSONA-SENTINEL",
        "@example.test",
    ):
        assert forbidden not in dumped


def test_workspace_models_reject_identity_and_private_payload_fields() -> None:
    schema = StudioWorkspaceSnapshot.model_json_schema()
    serialized = json.dumps(schema)
    for forbidden in (
        "email",
        "sender_address",
        "route_id",
        "conversation_id",
        "connection_id",
        "message_id",
        "assignment_id",
        "private_facts",
        "prompt",
    ):
        assert forbidden not in serialized
~~~

- [ ] **Step 2: Run projection tests and verify RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_studio_projection.py -v
~~~

Expected: collection fails with ModuleNotFoundError for humanwire.studio_projection.

- [ ] **Step 3: Implement immutable presentation models and store**

Use one frozen strict base model. The core event/message shapes are:

~~~python
class StudioConversationItem(_StudioProjection):
    ordinal: int = Field(ge=1)
    event_ordinal: int = Field(ge=1)
    created_at: datetime
    speaker: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=120)
    direction: Literal["from_humanwire", "to_humanwire", "system"]
    channel: Literal["Email", "Telegram", "Workspace"]
    text: str = Field(min_length=1, max_length=600)
    status: Literal["sent", "received", "no_response", "rejected"]


class StudioDataPoint(_StudioProjection):
    event_ordinal: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=200)
    effect: Literal["persisted", "inert"]


class StudioTransition(_StudioProjection):
    source: str = Field(pattern=_SAFE_NODE_ID)
    destination: str = Field(pattern=_SAFE_NODE_ID)
    source_label: str = Field(min_length=1, max_length=120)
    destination_label: str = Field(min_length=1, max_length=120)
    generated_label: str = Field(min_length=1, max_length=120)


class StudioTimelineEvent(_StudioProjection):
    timeline_ordinal: int = Field(ge=1)
    persisted_ordinal: int | None = Field(default=None, ge=1)
    created_at: datetime
    stage: StudioLifecycleStage
    effect: Literal["persisted", "inert"]
    active_transition: StudioTransition
    affected_persona_id: str | None = Field(default=None, pattern=_SAFE_PERSONA_ID)
    live_copy: str = Field(min_length=1, max_length=240)


class StudioWorkspaceSnapshot(_StudioProjection):
    schema_version: Literal["humanwire.studio/v1"]
    run_alias: str = Field(pattern=_SAFE_ALIAS)
    objective: str = Field(min_length=12, max_length=1000)
    requester_name: Literal["Alex Morgan"]
    requester_role_label: str
    target_timing_label: str
    run_state: Literal["starting", "running", "complete", "failed"]
    connection_label: Literal["Workspace channels", "Provider connected"]
    lifecycle: StudioLifecycle
    graph_nodes: tuple[StudioGraphNode, ...]
    graph_edges: tuple[StudioGraphEdge, ...]
    events: tuple[StudioTimelineEvent, ...]
    conversations: tuple[StudioConversationItem, ...]
    data_points: tuple[StudioDataPoint, ...]
    active_transition: StudioTransition | None
    current_event_ordinal: int = Field(ge=0)
    total_event_count: int = Field(ge=0)
    outcome: StudioOutcome
    downloads_ready: bool
~~~

StudioProgressStore must deep-validate every published snapshot, return copies, enforce monotonic event/message/data ordinals, reject complete-to-running regression, and require final trace/transcript binding before downloads_ready can become true.

create_studio_progress(request, scenario) constructs the product store, an internal SyntheticProgressStore, the existing RepositoryProgressObserver, and one StudioProgressObserver that owns both the product store and repository delegate. It returns (product_store, observer); generation passes that observer through both observer parameters.

- [ ] **Step 4: Write RED tests for exact message/event synchronization**

~~~python
def test_saved_transition_message_and_data_point_share_one_ordinal(tmp_path) -> None:
    request = launch_request()
    scenario = build_coordination_scenario(request, seed=7, scenario_id="launch-001")
    store, observer = create_studio_progress(request, scenario)
    generate_scenario(
        scenario,
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        mandate_request=request.objective,
        include_change_story=False,
        progress_observer=observer,
        presentation_observer=observer,
    )
    snapshot = store.snapshot()
    evidence = next(
        item for item in snapshot.data_points if item.label == "Evidence confirmed"
    )
    assert any(
        item.event_ordinal == evidence.event_ordinal
        and item.speaker == "Anika Rao"
        for item in snapshot.conversations
    )
    selected = snapshot.events[evidence.event_ordinal - 1]
    assert selected.active_transition.destination == "evidence"
    assert selected.affected_persona_id == "structured"
~~~

- [ ] **Step 5: Implement the observer with central safe renderers**

StudioProgressObserver wraps RepositoryProgressObserver. Its capture method first delegates the repository projection, then converts the newest exact replay event to graph/lifecycle/data state. Add:

~~~python
class StudioPresentationObserver(Protocol):
    def record_outbound(
        self,
        *,
        created_at: datetime,
        persona_id: str,
        channel: Channel,
        message_kind: str,
        safe_text: str,
    ) -> None:
        raise NotImplementedError

    def record_decision(
        self,
        *,
        created_at: datetime,
        persona_id: str,
        channel: Channel,
        intent: SyntheticIntent,
        safe_content: str,
    ) -> None:
        raise NotImplementedError
~~~

The outbound renderer must use an allowlisted message_kind map. It may not expose the raw provider payload or HW token. The inbound renderer accepts only content that already passed validate_persona_decision and then rechecks the product forbidden-pattern corpus.

Map lifecycle stages from saved event types:

~~~python
_LIFECYCLE_BY_STAGE = {
    "Origin": "brief",
    "Outreach": "outreach",
    "Interview": "resolve",
    "Evidence": "resolve",
    "Negotiation": "resolve",
    "Approval": "approve",
    "Availability": "schedule",
    "Meeting": "schedule",
}
~~~

- [ ] **Step 6: Add presentation callbacks to generation**

At the point where a delivery is matched to a persona, call record_outbound with:

- the virtual time;
- persona ID and channel;
- a message_kind selected from known HumanWire delivery headings; and
- a centrally sanitized product sentence.

Immediately after a SyntheticAction is accepted and before wire translation, call record_decision with the validated action content. Silence and error become no_response/rejected items without a fabricated message.

The callback must be optional. Existing proof generation/replay receives None and remains byte-identical.

- [ ] **Step 7: Add adversarial projection tests**

Cover:

- private fact, HW token, email, route/conversation/message keys, command text, and UUID rejection;
- duplicate or regressing ordinal rejection;
- inert attempt displayed as No state change without lifecycle advance;
- one active edge and one affected stakeholder at every ordinal;
- complete snapshot immutability;
- initial and failed states have no final hashes/downloads; and
- product JSON contains none of the prohibited primary-UI words.

- [ ] **Step 8: Run Task 3 GREEN and existing progress/replay regressions**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_studio_projection.py tests\humanwire\test_synthetic.py tests\humanwire\test_synthetic_viewer.py -k "studio or progress or replay or privacy" -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire\studio_projection.py src\humanwire\synthetic.py tests\humanwire\test_studio_projection.py
~~~

Expected: all selected tests pass; old frozen replay hash remains unchanged.

- [ ] **Step 9: Commit Task 3**

~~~powershell
git add src/humanwire/studio_projection.py src/humanwire/synthetic.py tests/humanwire/test_studio_projection.py tests/humanwire/test_synthetic.py
git commit -m "feat: project live HumanWire coordination"
~~~

---

### Task 4: One-Active-Run Studio Manager

**Files:**
- Create: src/humanwire/studio_run.py
- Create: tests/humanwire/test_studio_run.py
- Modify: src/humanwire/synthetic_watch.py only to reuse safe model readiness helpers if extraction is necessary

**Interfaces:**
- Consumes: CoordinationRequest, build_coordination_scenario, StudioProgressStore, StudioProgressObserver, generate_scenario, and PydanticAIPersonaDecisionEngineFactory.
- Produces: StudioRunRecord, StudioRunManager, ActiveRunError, UnknownRunError, ModelModeUnavailable, and RunCreationResult.
- Consumed by: Task 5 FastAPI routes.

- [ ] **Step 1: Write RED tests for no auto-start and one active run**

~~~python
def manager_with_blocking_runner(tmp_path, release, aliases):
    alias_iter = iter(aliases)

    def runner(scenario, output_path, run_root, **kwargs):
        Path(run_root).mkdir()
        release.wait(2)

    return StudioRunManager(
        workspace_root=tmp_path,
        runner=runner,
        alias_factory=alias_iter.__next__,
    )


def completed_manager(tmp_path, *, model_factory_builder=None):
    def runner(scenario, output_path, run_root, **kwargs):
        Path(run_root).mkdir()

    return StudioRunManager(
        workspace_root=tmp_path,
        runner=runner,
        alias_factory=iter(["launch-001"]).__next__,
        model_factory_builder=model_factory_builder,
    )


def test_manager_does_not_start_until_create_run(tmp_path) -> None:
    calls = []
    manager = StudioRunManager(
        workspace_root=tmp_path,
        runner=lambda **kwargs: calls.append(kwargs),
        alias_factory=iter(["launch-001"]).__next__,
    )
    assert manager.list_runs() == ()
    assert manager.active_alias is None
    assert calls == []


def test_manager_allows_exactly_one_active_run(tmp_path) -> None:
    release = Event()
    manager = manager_with_blocking_runner(tmp_path, release, aliases=["launch-001"])
    created = manager.create_run(launch_request())
    assert created.run_alias == "launch-001"
    with pytest.raises(ActiveRunError) as error:
        manager.create_run(conflict_request())
    assert error.value.run_alias == "launch-001"
    assert len(list(tmp_path.iterdir())) == 1
    release.set()
    manager.join(created.run_alias, timeout=2)
~~~

- [ ] **Step 2: Run manager tests and verify RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_studio_run.py -v
~~~

Expected: collection fails with ModuleNotFoundError for humanwire.studio_run.

- [ ] **Step 3: Implement run records and atomic manager state**

~~~python
@dataclass
class StudioRunRecord:
    run_alias: str
    request: CoordinationRequest
    run_root: Path
    transcript_path: Path
    store: StudioProgressStore
    observer: StudioProgressObserver
    worker: threading.Thread | None = None


@dataclass(frozen=True)
class RunCreationResult:
    run_alias: str
    workspace_url: str


@dataclass(frozen=True)
class StudioFinalBinding:
    snapshot: StudioWorkspaceSnapshot
    evidence: SyntheticEvidenceBundle
    transcript_path: Path


class StudioRunManager:
    def __init__(
        self,
        *,
        workspace_root: Path,
        seed: int = 0,
        step_delay_ms: int = 350,
        max_decision_workers: int = 4,
        model_factory_builder: Callable[[], PersonaDecisionEngineFactory] | None = None,
        alias_factory: Callable[[], str] = safe_run_alias,
        runner: Callable[..., SyntheticRunResult] = generate_scenario,
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._seed = seed
        self._step_delay_ms = step_delay_ms
        self._max_decision_workers = max_decision_workers
        self._model_factory_builder = model_factory_builder
        self._alias_factory = alias_factory
        self._runner = runner
        self._lock = threading.RLock()
        self._records: dict[str, StudioRunRecord] = {}
        self._active_alias: str | None = None

    def create_run(self, request: CoordinationRequest) -> RunCreationResult:
        request = CoordinationRequest.model_validate(request)
        with self._lock:
            if self._active_alias is not None:
                raise ActiveRunError(self._active_alias)
            alias = validate_run_alias(self._alias_factory())
            if alias in self._records:
                raise FileExistsError("coordination run alias already exists")
            run_root = self._workspace_root / alias
            if run_root.exists():
                raise FileExistsError("coordination run root already exists")
            scenario = build_coordination_scenario(
                request,
                seed=self._seed,
                scenario_id=alias,
            )
            store, observer = create_studio_progress(request, scenario)
            decision_engine = self._decision_engine_for(request)
            record = StudioRunRecord(
                run_alias=alias,
                request=request,
                run_root=run_root,
                transcript_path=run_root / "transcript.json",
                store=store,
                observer=observer,
            )
            worker = threading.Thread(
                target=self._run_one,
                args=(record, scenario, decision_engine),
                name="humanwire-studio-" + alias,
                daemon=False,
            )
            record.worker = worker
            self._records[alias] = record
            self._active_alias = alias
            worker.start()
            return RunCreationResult(alias, "/runs/" + alias)

    def snapshot(self, run_alias: str) -> StudioWorkspaceSnapshot:
        with self._lock:
            record = self._record(run_alias)
        return record.store.snapshot()

    def final_binding(self, run_alias: str) -> StudioFinalBinding | None:
        with self._lock:
            record = self._record(run_alias)
        evidence = record.observer.evidence_bundle()
        snapshot = record.store.snapshot()
        if evidence is None or not snapshot.downloads_ready:
            return None
        return StudioFinalBinding(snapshot, evidence, record.transcript_path)

    def list_runs(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._records)

    def join(self, run_alias: str, timeout: float | None = None) -> None:
        with self._lock:
            worker = self._record(run_alias).worker
        if worker is None:
            raise RuntimeError("coordination worker was not started")
        worker.join(timeout)
        if worker.is_alive():
            raise TimeoutError("coordination worker did not finish")
~~~

Guard _records and _active_alias with one RLock. The alias factory returns a safe presentation alias; create_run rejects a duplicate alias and passes a non-existent child path to generate_scenario. The worker is non-daemon, catches private exceptions, publishes one fixed failed snapshot, and clears active_alias in finally only if it still owns that alias.

The three private methods have these exact responsibilities:

~~~python
def _record(self, run_alias: str) -> StudioRunRecord:
    alias = validate_run_alias(run_alias)
    try:
        return self._records[alias]
    except KeyError as error:
        raise UnknownRunError(alias) from error


def _decision_engine_for(
    self,
    request: CoordinationRequest,
) -> PersonaDecisionEngineFactory | None:
    if request.agent_mode is StudioAgentMode.STANDARD:
        return None
    if self._model_factory_builder is None:
        raise ModelModeUnavailable("model_credentials_missing")
    return self._model_factory_builder()


def _run_one(self, record, scenario, decision_engine) -> None:
    try:
        self._runner(
            scenario,
            record.transcript_path,
            record.run_root,
            decision_engine=decision_engine,
            max_decision_workers=self._max_decision_workers,
            progress_observer=record.observer,
            presentation_observer=record.observer,
            mandate_request=record.request.objective,
            include_change_story=False,
        )
    except Exception:
        record.store.publish_failed()
    finally:
        with self._lock:
            if self._active_alias == record.run_alias:
                self._active_alias = None
~~~

_run_one must wrap the observer in the existing presentation-only pacing behavior when step_delay_ms is nonzero; sleeping happens only after a newly persisted product ordinal is published.

- [ ] **Step 4: Write model-mode readiness RED tests**

~~~python
def test_model_assisted_request_requires_explicit_model_factory(tmp_path) -> None:
    manager = StudioRunManager(workspace_root=tmp_path)
    with pytest.raises(ModelModeUnavailable, match="model_credentials_missing"):
        manager.create_run(launch_request(agent_mode="model_assisted"))
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_standard_request_never_builds_model_factory(tmp_path) -> None:
    built = []
    manager = completed_manager(
        tmp_path,
        model_factory_builder=lambda: built.append(True),
    )
    manager.create_run(launch_request(agent_mode="standard"))
    manager.join("launch-001", timeout=3)
    assert built == []
~~~

- [ ] **Step 5: Implement explicit engine selection**

Standard mode passes decision_engine=None. Model-assisted mode calls model_factory_builder exactly once before any child run root is created. The default builder reads Settings only when model-assisted mode was explicitly requested, requires a non-blank Featherless key, and returns PydanticAIPersonaDecisionEngineFactory with explicit key, model identifier, and base URL.

- [ ] **Step 6: Add run completion and second-run isolation tests**

~~~python
def test_completed_run_allows_new_isolated_coordination(tmp_path) -> None:
    aliases = iter(["launch-001", "conflict-002"])
    manager = StudioRunManager(
        workspace_root=tmp_path,
        seed=7,
        step_delay_ms=0,
        alias_factory=aliases.__next__,
    )
    first = manager.create_run(launch_request())
    manager.join(first.run_alias, timeout=10)
    first_bytes = first.transcript_path.read_bytes()
    second = manager.create_run(conflict_request())
    manager.join(second.run_alias, timeout=10)
    assert first.run_root != second.run_root
    assert first.transcript_path.read_bytes() == first_bytes
    assert manager.snapshot(first.run_alias).run_state == "complete"
    assert manager.snapshot(second.run_alias).run_state in {"complete", "failed"}
~~~

Also test unknown aliases, path traversal aliases, worker failure, join timeout, and concurrent two-thread create_run where exactly one caller wins.

- [ ] **Step 7: Run Task 4 GREEN and watch regressions**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_studio_run.py tests\humanwire\test_synthetic_watch.py -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire\studio_run.py src\humanwire\synthetic_watch.py tests\humanwire\test_studio_run.py
~~~

Expected: all tests pass; old watch behavior remains unchanged.

- [ ] **Step 8: Commit Task 4**

~~~powershell
git add src/humanwire/studio_run.py src/humanwire/synthetic_watch.py tests/humanwire/test_studio_run.py tests/humanwire/test_synthetic_watch.py
git commit -m "feat: manage HumanWire coordination runs"
~~~

---

### Task 5: Loopback Studio API, Security, CLI, and Attachments

**Files:**
- Create: src/humanwire/studio_app.py
- Create: src/humanwire/studio_exports.py
- Modify: src/humanwire/__main__.py
- Modify: pyproject.toml
- Create: tests/humanwire/test_studio_app.py
- Modify: tests/humanwire/test_container.py for CLI delegation compatibility
- Modify: tests/humanwire/test_cutover.py for package-data/public-boundary assertions

**Interfaces:**
- Consumes: StudioRunManager, CoordinationRequest, product_catalog, StudioWorkspaceSnapshot, and final transcript/evidence binding.
- Produces: create_coordination_studio_app(manager, action_token), validate_studio_host(host), StudioOptions, and run_coordination_studio(options).
- CLI: humanwire studio --workspace-root PATH --port 8766 --seed 0 --step-delay-ms 350 --max-decision-workers 4.
- Consumed by: Task 6 frontend and Task 7 end-to-end verification.

- [ ] **Step 1: Write route and security RED tests**

~~~python
def studio_client(tmp_path) -> tuple[TestClient, StudioRunManager]:
    manager = StudioRunManager(
        workspace_root=tmp_path,
        alias_factory=iter(["launch-001"]).__next__,
        step_delay_ms=0,
    )
    app = create_coordination_studio_app(manager, action_token="test-action-token")
    return TestClient(app, base_url="http://127.0.0.1"), manager


def test_studio_home_is_idle_product_and_has_no_started_run(tmp_path) -> None:
    client, manager = studio_client(tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    assert "Start a coordination" in response.text
    assert "Synthetic HumanWire progress" not in response.text
    assert manager.list_runs() == ()


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 403),
        ({"X-HumanWire-Action": "wrong"}, 403),
        ({"X-HumanWire-Action": "test-action-token", "Content-Type": "text/plain"}, 415),
        (
            {
                "X-HumanWire-Action": "test-action-token",
                "Content-Type": "application/json",
                "Origin": "https://attacker.example",
            },
            403,
        ),
    ],
)
def test_create_run_requires_loopback_action_boundary(tmp_path, headers, status) -> None:
    client, _manager = studio_client(tmp_path)
    response = client.post(
        "/api/runs",
        headers=headers,
        content=json.dumps(launch_request().model_dump(mode="json")),
    )
    assert response.status_code == status
    assert "objective" not in response.text
~~~

- [ ] **Step 2: Run the API tests and verify RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_studio_app.py -v
~~~

Expected: collection fails with ModuleNotFoundError for humanwire.studio_app.

- [ ] **Step 3: Implement loopback app and fixed security middleware**

validate_studio_host accepts only the literal 127.0.0.1. create_coordination_studio_app:

- disables OpenAPI, Swagger, and ReDoc;
- mounts shared static CSS only if needed and studio assets only at /studio-static;
- rejects Host values other than 127.0.0.1 with an optional numeric port;
- allows GET, HEAD, and the exact POST /api/runs only;
- applies no-store, CSP, nosniff, referrer, permissions, and frame headers;
- checks Content-Length is present, numeric, and no greater than 8192 for POST;
- checks application/json, the exact action token, and a loopback Origin when supplied;
- validates CoordinationRequest without reflecting its values in errors; and
- returns fixed JSON error codes such as invalid_request, active_run, and model_unavailable.

The successful creation response is:

~~~python
JSONResponse(
    status_code=201,
    content={
        "run_alias": created.run_alias,
        "workspace_url": f"/runs/{created.run_alias}",
    },
)
~~~

- [ ] **Step 4: Add host, method, body, and public-separation tests**

The matrix must cover:

- Host values 127.0.0.1, 127.0.0.1:8766, localhost, ::1, attacker.example, duplicate Host, and an invalid port;
- GET/HEAD success and PUT/PATCH/DELETE/OPTIONS rejection;
- missing, negative, nonnumeric, duplicate, and oversized Content-Length;
- invalid JSON, duplicate JSON keys, unknown fields, invalid participant IDs, and objective boundaries;
- active-run 409 with only the safe active alias;
- model-unavailable 409 with no key/base URL/model value;
- public create_demo_app returns 404 for /api/runs and /studio-static/coordination-studio.js;
- local studio returns 200 for its controller and stylesheet; and
- all error responses omit Content-Disposition.

Use a duplicate-key rejecting JSON loader before Pydantic validation.

- [ ] **Step 5: Implement final JSON and CSV exports**

studio_exports.py defines a product evidence model from the final StudioWorkspaceSnapshot plus the already-bound SyntheticEvidenceBundle. JSON must include request summary, terminal outcome, graph/event timeline, conversations, and data points, but none of the internal provenance keys or forbidden identity fields.

CSV fields are:

~~~python
_STUDIO_CSV_FIELDS = (
    "timeline_ordinal",
    "persisted_ordinal",
    "effect",
    "created_at",
    "stage",
    "source",
    "destination",
    "channel",
    "direction",
    "stakeholder",
    "data_point",
    "summary",
)
~~~

Use the existing formula-prefix defense for every rendered cell. Both endpoints require manager.final_binding(run_alias), set Content-Disposition to attachment with a safe alias filename, and return 409 final_evidence_unavailable before completion.

- [ ] **Step 6: Write attachment and parity tests**

~~~python
def test_completed_json_and_csv_are_attachments_with_event_parity(tmp_path) -> None:
    client, manager = studio_client(tmp_path)
    created = client.post(
        "/api/runs",
        headers={
            "Content-Type": "application/json",
            "X-HumanWire-Action": "test-action-token",
        },
        content=launch_request().model_dump_json(),
    )
    assert created.status_code == 201
    run_alias = created.json()["run_alias"]
    manager.join(run_alias, timeout=20)
    json_response = client.get(f"/api/runs/{run_alias}/evidence.json")
    csv_response = client.get(f"/api/runs/{run_alias}/events.csv")
    assert json_response.status_code == csv_response.status_code == 200
    assert "attachment" in json_response.headers["content-disposition"]
    assert "attachment" in csv_response.headers["content-disposition"]
    json_events = json_response.json()["events"]
    csv_events = list(csv.DictReader(io.StringIO(csv_response.text)))
    assert len(json_events) == len(csv_events)
    assert [str(item["timeline_ordinal"]) for item in json_events] == [
        item["timeline_ordinal"] for item in csv_events
    ]
    assert [item["effect"] for item in json_events] == [
        item["effect"] for item in csv_events
    ]
~~~

Also assert CR/LF/tab/formula-prefixed objective or summary values are neutralized in CSV and that private facts, addresses, HW tokens, UUIDs, routes, and credentials are absent from both formats.

- [ ] **Step 7: Add the studio CLI**

In __main__.py add a top-level studio subparser:

~~~python
studio = subcommands.add_parser(
    "studio",
    help="start the private HumanWire coordination studio",
)
studio.add_argument("--workspace-root", required=True, action=_OnceValue)
studio.add_argument("--port", type=_bounded_integer("port", 1024, 65535), default=8766)
studio.add_argument("--seed", type=_bounded_integer("seed", 0, 2_147_483_647), default=0)
studio.add_argument(
    "--step-delay-ms",
    type=_bounded_integer("step delay", 0, 3000),
    default=350,
)
studio.add_argument(
    "--max-decision-workers",
    type=_bounded_integer("max decision workers", 1, 8),
    default=4,
)
~~~

run_coordination_studio constructs the manager and app but does not create a run. It generates one per-process action token with secrets.token_urlsafe, embeds it only in the served HTML context, prints only studio_url=http://127.0.0.1:PORT, and starts uvicorn on validate_studio_host("127.0.0.1").

Add studio_static/*.js and studio_static/*.css to setuptools package data.

- [ ] **Step 8: Run Task 5 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_studio_app.py tests\humanwire\test_container.py tests\humanwire\test_cutover.py -v
.\.venv\Scripts\python.exe -m ruff check src\humanwire\studio_app.py src\humanwire\studio_exports.py src\humanwire\__main__.py tests\humanwire\test_studio_app.py
~~~

Expected: all tests pass; public studio paths remain absent.

- [ ] **Step 9: Commit Task 5**

~~~powershell
git add pyproject.toml src/humanwire/studio_app.py src/humanwire/studio_exports.py src/humanwire/__main__.py tests/humanwire/test_studio_app.py tests/humanwire/test_container.py tests/humanwire/test_cutover.py
git commit -m "feat: serve private HumanWire coordination studio"
~~~

---

### Task 6: Product Composer, Live Graph Workspace, and Replay

**Files:**
- Create: src/humanwire/templates/coordination_studio.html
- Create: src/humanwire/studio_static/coordination-studio.css
- Create: src/humanwire/studio_static/coordination-studio.js
- Create: tests/humanwire/fixtures/studio-snapshots.json
- Create: tests/humanwire/test_studio_frontend.py
- Modify: tests/humanwire/test_studio_app.py for rendered context

**Interfaces:**
- Consumes: GET /api/catalog, POST /api/runs, GET /api/runs/{alias}, final attachment endpoints, and StudioWorkspaceSnapshot.
- Produces: a two-state browser product: composer and live workspace.
- No client code may construct workflow results; it renders only server-projected state.

- [ ] **Step 1: Write rendered product-copy and structure RED tests**

~~~python
def test_studio_template_is_product_not_proof_dashboard(tmp_path) -> None:
    client, _manager = studio_client(tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    required = (
        "HumanWire",
        "Start a coordination",
        "What needs to be decided?",
        "Who are you in this coordination?",
        "Stakeholders",
        "Start coordination",
        "Brief",
        "Outreach",
        "Resolve",
        "Approve",
        "Schedule",
        "Conversation",
        "Data trail",
    )
    for text in required:
        assert text in response.text
    for forbidden in (
        "Synthetic HumanWire progress",
        "local simulation viewer",
        "proof_class",
        "actor_type",
        "fake_caspian",
        "simulated_persona",
    ):
        assert forbidden not in BeautifulSoup(response.text, "html.parser").get_text(" ")
    assert 'data-studio-state="composer"' in response.text
    assert 'data-flow-canvas' in response.text
    assert 'aria-live="polite"' in response.text
~~~

- [ ] **Step 2: Run frontend tests and verify RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_studio_frontend.py -v
~~~

Expected: tests fail because coordination_studio.html and studio assets do not exist.

- [ ] **Step 3: Implement the approved two-state HTML shell**

The composer contains:

- left HumanWire navigation;
- heading Start a coordination;
- objective textarea seeded from Launch decision;
- four requester-role radio cards;
- eight stakeholder checkbox cards with the seven primary people selected;
- timing controls;
- three template cards;
- five-step preview; and
- one Start coordination button.

The workspace is initially hidden and contains:

- objective/requester header;
- lifecycle rail;
- SVG graph with stable node/edge data attributes;
- stakeholder cards;
- conversation panel;
- data trail;
- outcome card;
- Pause visuals / Follow live / Previous / Next / Play controls;
- JSON / CSV download controls; and
- New coordination after completion.

The action token appears only in a meta element:

~~~html
<meta name="humanwire-action-token" content="{{ action_token | e }}">
~~~

Do not place it in visible text, URLs, localStorage, or logs.

- [ ] **Step 4: Implement studio-scoped responsive CSS**

All selectors must begin with .coordination-studio-page or an exact descendant of that body class. Define:

- deep navy shell and ice/cyan product palette;
- 280 px left nav, flexible graph, 360 px conversation rail at desktop;
- visible five-stage lifecycle;
- SVG canvas with node cards and curved edge paths;
- active edge dash/pulse animation;
- green complete, amber attention, cyan active states with text/icon redundancy;
- conversation bubbles and data rows;
- 14 px minimum meaningful text;
- 44 px minimum controls;
- 2 px visible focus ring;
- 759 px and 479 px reflow breakpoints;
- mobile vertical graph and Conversation/Data tabs;
- prefers-reduced-motion disabling path travel; and
- no global html/body/a/button rules outside the body scope.

- [ ] **Step 5: Write the executable DOM RED harness**

The Node harness loads production coordination-studio.js into a minimal DOM and mocked fetch queue. It must execute:

~~~javascript
const fs = require("node:fs");
const fixtureSnapshots = JSON.parse(
  fs.readFileSync("tests/humanwire/fixtures/studio-snapshots.json", "utf8"),
);
const fetchCalls = [];
const fetchQueue = [];
const intervals = [];
global.fetch = async (url, options = {}) => {
  fetchCalls.push({ url, options });
  return fetchQueue.shift();
};
global.setInterval = (callback) => {
  intervals.push(callback);
  return intervals.length;
};

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    json: async () => structuredClone(body),
  };
}

function click(selector) {
  document.querySelector(selector).click();
  return Promise.resolve();
}

function text(selector) {
  return document.querySelector(selector).textContent.trim();
}

function activeEdges() {
  return [...document.querySelectorAll("[data-flow-edge].is-active")];
}

function activePersonas() {
  return [...document.querySelectorAll("[data-persona-card].is-active")];
}

function conversationRows() {
  return [...document.querySelectorAll("[data-conversation-row]")];
}

function dataRows() {
  return [...document.querySelectorAll("[data-data-row]")];
}

function flowStrip() {
  return {
    from: text("[data-flow-from]"),
    to: text("[data-flow-to]"),
    generated: text("[data-flow-generated]"),
  };
}

function pollWith(snapshot) {
  fetchQueue.push(jsonResponse(snapshot));
  return intervals.at(-1)();
}

function snapshotAt(ordinal) {
  return fixtureSnapshots[ordinal - 1];
}

fetchQueue.push(jsonResponse({
  run_alias: "launch-001",
  workspace_url: "/runs/launch-001",
}, 201));
await click("[data-start-coordination]");
assert.equal(fetchCalls[0].url, "/api/runs");
assert.equal(fetchCalls[0].options.method, "POST");
assert.equal(fetchCalls[0].options.headers["X-HumanWire-Action"], "test-token");
assert.equal(location.pathname, "/runs/launch-001");

await pollWith(snapshotAt(1));
assert.equal(text("[data-current-stage]"), "Outreach");
assert.equal(activeEdges().length, 1);
assert.equal(activePersonas().length, 1);
assert.equal(conversationRows().length, 1);
assert.equal(dataRows().length, 1);

await pollWith(snapshotAt(2));
assert.equal(text("[data-event-progress]"), "Event 2 of 2");
assert.equal(flowStrip().from, snapshotAt(2).active_transition.source_label);
assert.equal(flowStrip().to, snapshotAt(2).active_transition.destination_label);
assert.equal(flowStrip().generated, snapshotAt(2).active_transition.generated_label);
~~~

Run the harness in normal, reduced-motion, and initially hidden modes.

- [ ] **Step 6: Implement composer submission and safe polling**

The controller:

1. fetches /api/catalog once;
2. seeds or changes the form from an allowlisted template;
3. serializes only the exact request fields;
4. posts with JSON and X-HumanWire-Action;
5. switches to workspace and calls history.replaceState;
6. polls at 500 ms with If-None-Match/current ordinal;
7. queues unseen ordinals and renders them sequentially;
8. never renders innerHTML from response strings;
9. pauses visual following without pausing the worker; and
10. stops polling only at complete or failed.

Use textContent, setAttribute, and createElement for all server-projected strings.

- [ ] **Step 7: Implement graph/replay synchronization**

For each selected event:

- clear every active node, edge, and persona state;
- activate exactly the source node, destination node, one edge, and optional stakeholder;
- update From, To, Generated and the accessible live sentence from the same event object;
- reveal conversation/data items whose event_ordinal is less than or equal to the selected ordinal;
- update lifecycle completion from snapshot state, not from client guesses; and
- preserve the event list without mutating or replacing saved entries.

Play advances at 900 ms only after completion or while visuals are paused from live. visibilitychange pauses playback. Manual Previous/Next always work. Reduced motion prevents the travel class but not selection.

- [ ] **Step 8: Implement attachment downloads and new coordination**

JSON/CSV buttons are disabled unless downloads_ready is true. On click, create a temporary hidden anchor with the exact attachment endpoint and download attribute, click it, remove it, and retain the workspace URL. New coordination returns to /, clears presentation-only state, and does not delete the completed run.

- [ ] **Step 9: Add CSS/accessibility/privacy contract tests**

Assert:

- every studio selector is scoped;
- every meaningful font rule is at least 14 px;
- all interactive targets have min-height/min-width 44 px;
- graph/table mobile reflow exists below 759 px and 479 px;
- focus-visible and prefers-reduced-motion rules exist;
- no prohibited primary-UI word is present in template/JS-rendered strings;
- no token is written to URL/localStorage/sessionStorage/console;
- no eval, Function constructor, document.write, or unsafe innerHTML;
- exact one H1 per visible state;
- roles, labels, live regions, and tab states are correct; and
- public Reach CSS/JS behavior remains unchanged.

- [ ] **Step 10: Run Task 6 GREEN**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_studio_frontend.py tests\humanwire\test_studio_app.py tests\humanwire\test_synthetic_viewer.py tests\humanwire\test_web.py -v
node --check src\humanwire\studio_static\coordination-studio.js
.\.venv\Scripts\python.exe -m ruff check tests\humanwire\test_studio_frontend.py
~~~

Expected: all tests pass, Node reports no syntax error, and existing public/web viewer behavior remains green.

- [ ] **Step 11: Commit Task 6**

~~~powershell
git add src/humanwire/templates/coordination_studio.html src/humanwire/studio_static/coordination-studio.css src/humanwire/studio_static/coordination-studio.js tests/humanwire/fixtures/studio-snapshots.json tests/humanwire/test_studio_frontend.py tests/humanwire/test_studio_app.py
git commit -m "feat: visualize live HumanWire coordination"
~~~

---

### Task 7: End-to-End Product Acceptance, Documentation, and Browser Proof

**Files:**
- Create: tests/humanwire/test_studio_e2e.py
- Modify: README.md
- Modify: submission/demo.md
- Modify: submission/verified-claims.md
- Modify: submission/checklist.md
- Modify: tests/humanwire/test_cutover.py for exact product command/copy
- Update only if deterministic meaning changed: tests/humanwire/fixtures/synthetic-personas-v1.json

**Interfaces:**
- Consumes every prior task.
- Produces the reviewed local product, exact demo workflow, truthful documentation, screenshots, and final gate evidence.

- [ ] **Step 1: Write the request-to-meeting RED acceptance test**

~~~python
def test_launch_request_visibly_resolves_conflict_and_creates_meeting(tmp_path) -> None:
    manager = StudioRunManager(
        workspace_root=tmp_path,
        seed=7,
        step_delay_ms=0,
        max_decision_workers=4,
        alias_factory=iter(["launch-001"]).__next__,
    )
    app = create_coordination_studio_app(manager, action_token="acceptance-token")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/runs",
            headers={
                "Content-Type": "application/json",
                "X-HumanWire-Action": "acceptance-token",
            },
            content=launch_request().model_dump_json(),
        )
        assert response.status_code == 201
        manager.join("launch-001", timeout=20)
        snapshot = client.get("/api/runs/launch-001").json()

    assert snapshot["run_state"] == "complete"
    assert snapshot["outcome"]["state"] == "meeting_ready"
    assert snapshot["outcome"]["meeting_start"] == "2026-08-14T15:00:00Z"
    assert snapshot["outcome"]["meeting_end"] == "2026-08-14T15:30:00Z"
    assert {"Alex Morgan", "Anika Rao"} <= set(snapshot["outcome"]["required_attendees"])
    event_labels = [item["label"] for item in snapshot["data_points"]]
    for required in (
        "Coordination request saved",
        "Outreach sent",
        "Conflict identified",
        "Interview answer recorded",
        "Evidence confirmed",
        "Proposal revised",
        "Approval recorded",
        "Availability recorded",
        "Meeting package created",
    ):
        assert required in event_labels
    assert any(
        item["speaker"] == "Anika Rao"
        and "rollback" in item["text"].casefold()
        for item in snapshot["conversations"]
    )
~~~

- [ ] **Step 2: Run the exact acceptance test and verify RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_studio_e2e.py::test_launch_request_visibly_resolves_conflict_and_creates_meeting -v
~~~

Expected: fail on the first missing product milestone or projection mismatch; do not weaken the assertion to match an incomplete story.

- [ ] **Step 3: Close only acceptance-discovered integration gaps**

Fix the smallest responsible layer:

- request/scenario bug in studio_models.py;
- agent decision bug in pydantic_persona.py or existing deterministic policy;
- missing callback/projection in studio_projection.py;
- lifecycle/worker bug in studio_run.py; or
- rendering-only bug in studio JS/CSS.

Do not bypass the real gateway/workflow/repository path and do not directly insert evidence, decisions, availability, or meeting entities from the studio.

- [ ] **Step 4: Add restart, replay, download, and second-run acceptance**

Add tests proving:

- refresh after any ordinal reconstructs the same snapshot;
- manual replay changes only presentation selection;
- replay does not invoke a persona engine or gateway mutation;
- JSON/CSV rows match every persisted/inert event;
- a second coordination starts after completion with a new run root;
- first transcript/database/download bytes remain unchanged;
- one registered gateway handler processes both Email and Telegram shapes;
- the structured interview begins via Email and confirms on Telegram;
- model-assisted mode is PENDING without key and makes zero model calls; and
- standard mode completes without reading ambient model/provider settings.

- [ ] **Step 5: Update exact product documentation**

README must lead the local experience with:

~~~powershell
python -m humanwire studio --workspace-root .worktrees/humanwire-runs --port 8766
~~~

Document:

- Start a coordination is the primary local product;
- Standard agent reasoning works without a model credential;
- AI-assisted reasoning uses the PydanticAI adapter only when FEATHERLESS_API_KEY is configured;
- both modes still use HumanWire's authoritative workflow and one CaspianGateway handler;
- Workspace channels does not claim external provider delivery;
- external Caspian/email/Telegram verification remains a separate private checklist; and
- the old synthetic CLI is retained as internal deterministic evidence, not the primary product screen.

Update submission copy only with behavior actually verified by Task 7. Do not claim a live model or external provider without retained evidence.

- [ ] **Step 6: Run focused automated acceptance**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_studio_models.py tests\humanwire\test_pydantic_persona.py tests\humanwire\test_studio_projection.py tests\humanwire\test_studio_run.py tests\humanwire\test_studio_app.py tests\humanwire\test_studio_frontend.py tests\humanwire\test_studio_e2e.py -v
~~~

Expected: all focused tests pass with no unexpected skips.

- [ ] **Step 7: Start a fresh product server for browser verification**

Use a non-existent ignored run workspace:

~~~powershell
.\.venv\Scripts\python.exe -m humanwire studio --workspace-root .superpowers\studio-review-runs --port 8766 --step-delay-ms 500
~~~

Open only the literal loopback URL http://127.0.0.1:8766/ in the in-app browser.

- [ ] **Step 8: Verify the approved desktop workflow**

At 1280x720:

1. confirm the composer, objective, requester role, professional names, templates, and five-step preview;
2. confirm no run starts before clicking Start coordination;
3. start Launch decision;
4. observe live path transitions through Request, HumanWire, Caspian Gateway, and stakeholder nodes;
5. confirm messages and data points enter on the same selected event;
6. confirm Anika's conflict, targeted interview, evidence confirmation, revised proposal, Sofia's approval, availability, and meeting package;
7. confirm exactly one active path and stakeholder;
8. pause visuals, follow live, move Previous/Next, and Play/Pause after completion;
9. click JSON and CSV and verify downloads occur without navigation;
10. click New coordination and verify a clean composer; and
11. confirm zero console warnings/errors and no overlay or horizontal overflow.

Save screenshots only under the ignored task report directory.

- [ ] **Step 9: Verify tablet and mobile**

At 600x900 and 390x844:

- exact viewport is reported after reload;
- graph becomes vertical without clipped connectors;
- Conversation/Data tabs are reachable and preserve selected event;
- every focusable control is at least 44 by 44 px;
- meaningful visible text is at least 14 px;
- page scrollWidth equals clientWidth;
- keyboard focus is visible;
- reduced-motion contract is exercised when the in-app browser supports it; and
- JSON/CSV downloads and New coordination remain usable.

If the in-app browser cannot apply a viewport/media state, record the exact limitation and retain the automated contract test; do not substitute an unapproved external browser.

- [ ] **Step 10: Compare implementation to both approved concept images**

Use view_image on:

- the approved request-screen concept;
- the approved live-workspace concept;
- the latest desktop composer screenshot; and
- the latest desktop live-workspace screenshot.

Create a fidelity ledger covering shell, hierarchy, graph prominence, lifecycle, names, conversation, data trail, palette, density, responsive behavior, and motion. Fix material drift before final gates.

- [ ] **Step 11: Run full verification**

Run:

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
node --check src\humanwire\studio_static\coordination-studio.js
node --check src\humanwire\viewer_static\synthetic-progress.js
.\.venv\Scripts\python.exe scripts\smoke_humanwire.py
.\.venv\Scripts\python.exe -m humanwire smoke
git diff --check
~~~

Expected:

- HumanWire and repository suites exit 0;
- only documented opt-in PostgreSQL skips remain;
- Ruff and both Node checks exit 0;
- both smoke commands emit the exact same 11 PASS lines;
- diff check is clean; and
- privacy scan finds no real credentials, private facts, live routes, UUIDs, or forbidden product copy in studio assets.

- [ ] **Step 12: Write the ignored report and commit Task 7**

Write the exact RED/GREEN commands, test counts, browser evidence, fidelity ledger, privacy scan, remaining external-provider/model limitations, and commit SHA to this ignored report:

~~~text
.superpowers/sdd/2026-08-14-humanwire-interactive-coordination-studio/task-7-report.md
~~~

Then commit:

~~~powershell
git add README.md submission/demo.md submission/verified-claims.md submission/checklist.md tests/humanwire/test_cutover.py tests/humanwire/test_studio_e2e.py
git commit -m "feat: launch interactive HumanWire coordination"
~~~

## Plan Self-Review

### Spec coverage

- Composer, roles, stakeholder picker, timing, and templates: Task 1 and Task 6.
- Fresh request-created run and one-active-run boundary: Task 4 and Task 5.
- Professional identities and no proof vocabulary in primary UI: Task 1, Task 3, and Task 6.
- PydanticAI selected as a bounded typed persona layer: Task 2.
- Existing HumanWire authority and one CaspianGateway handler: Tasks 1-4 and Task 7 acceptance.
- Agreement, conflict, interview, confirmation, proposal, approval, availability, and meeting: Task 1 scenario plus Task 7 acceptance.
- Rich conversations, generated data, graph, lifecycle, and exact event synchronization: Task 3 and Task 6.
- Smooth truthful animation, replay, reduced motion, hidden pause, and accessibility: Task 6 and Task 7 browser QA.
- JSON/CSV attachment downloads and parity: Task 5, Task 6, and Task 7.
- New coordination after completion: Task 4, Task 6, and Task 7.
- Loopback mutation safety and public-app separation: Task 5.
- Responsive desktop/tablet/mobile verification and visual fidelity: Task 6 and Task 7.
- Truthful local/external-provider wording: Global Constraints, Task 5, Task 6, and Task 7 docs.

### Type consistency

- CoordinationRequest is defined in Task 1 and consumed unchanged by Tasks 3-7.
- PydanticAIPersonaDecisionEngineFactory implements the existing PersonaDecisionEngineFactory protocol and is consumed only by StudioRunManager.
- StudioProgressObserver implements the existing progress methods plus StudioPresentationObserver callbacks.
- StudioWorkspaceSnapshot is the single server-to-browser contract used by manager, app, exports, frontend, and acceptance tests.
- StudioRunManager is the sole owner of active run state, stores, roots, and worker threads.
- The browser never consumes SyntheticProgressSnapshot or internal provenance objects directly.

### Completion-marker scan

The plan contains no unresolved marker or unspecified test step. Each task defines concrete files, interfaces, a failing command, an implementation boundary, a passing command, and a commit.
