# HumanWire Agent-Persona Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add seeded fictional stakeholder agents and a loopback-only progress viewer so an operator can watch HumanWire persist a synthetic coordination flow, then replay it with Play/Pause/Previous/Next and download completed JSON/CSV evidence.

**Architecture:** Keep HumanWire's workflow, repository, single `CaspianGateway` handler, offline transport, virtual clock, transcript validation, and frozen replay authoritative. Add a narrow persona-decision protocol over the existing `FeatherlessJsonClient`, a public-safe persisted-event projector with an in-memory snapshot store, and a separate GET-only FastAPI viewer that can bind only to loopback. Deterministic generation stays the CI/release path; model-assisted generation is explicit, private, exploratory, and freeze-before-replay.

**Tech Stack:** Python 3.12, Pydantic 2, FastAPI/Jinja2, SQLAlchemy/SQLite, `FeatherlessJsonClient`/httpx, vanilla JavaScript, CSS, pytest, Node DOM harness, uvicorn.

## Global Constraints

- Preserve the existing nine-person scenario shape: one manager, one `INFORM`, one `ACKNOWLEDGE`, two independent `QUICK_RESPONSE`, one `STRUCTURED_INTERVIEW`, one `REVIEW_APPROVAL`, one `AVAILABILITY`, and one separate `CHANGE` authority.
- Preserve stable internal persona IDs; choose distinct fictional display names from a committed catalog by an explicit integer seed and record `identity_seed` plus `identity_generator_version` in the transcript.
- Every synthetic email ends in `.example.test`; synthetic Telegram route, conversation, connection, message, token, and assignment identity remains orchestrator-owned and never enters persona context.
- A persona decision receives only role, engagement contract, bounded fictional private facts, allowed intents, current delivery text, its own inbox/transcript, and virtual time.
- No persona receives repository, workflow, database, event-log, expected-final-state, real or synthetic envelope identity, credentials, environment, filesystem, browser, shell, arbitrary tool, or direct network access.
- The centralized `FeatherlessJsonClient` is the primary model adapter. Do not add LangGraph, Microsoft Agent Framework, CrewAI, AutoGen, or PydanticAI unless the direct strict-output adapter fails its defined tests and a separate reviewed dependency change is approved.
- Every non-silence action is translated centrally and reinjected through the one `OfflineCaspianClient`/`CaspianGateway` handler. Never call `workflow.handle` directly and never mutate repository outcomes to stage a result.
- Invalid, disallowed, late, timed-out, or extra-field model output becomes a safe synthetic silence/error; it never becomes guessed approval, confirmation, evidence, availability, or authority.
- Deterministic generation is the release/CI path. Model-assisted generation is exploratory and non-gating. Frozen replay constructs no policy and calls no model/provider.
- All artifacts and viewer pages show exactly: `proof_class=synthetic_multi_persona`, `actor_type=simulated_persona`, `identity_source=synthetic_fixture`, `transport=fake_caspian`, `human_attested=false`, and `live_provider_verified=false`.
- The viewer exposes only replay-safe labels/counts/status and the final trace hash. It never exposes private facts, prompts, answers, routes, addresses, raw destinations, domain/envelope identifiers, credentials, provider bodies, database coordinates, paths, UUIDs, or model diagnostics.
- The viewer binds only to `127.0.0.1`, accepts only GET/HEAD, has no workflow/model/provider mutation route, and is absent from the public Vercel app and private Supabase/Caspian sandbox.
- Meaningful text is at least 14 px; controls are at least 44 by 44 px; replay respects `prefers-reduced-motion`, pauses while hidden, retains visible focus, and uses a polite live region.
- Completed JSON and CSV are attachments with `Content-Disposition`; partial progress JSON is polling-only and cannot be downloaded as final evidence.
- Keep the public Vercel demo frozen, read-only, deterministic, and GET-only. Do not deploy, modify private Supabase/Caspian data, or contact real identities while implementing this plan.
- Preserve both existing smoke entrypoints' exact eleven PASS lines and keep `.superpowers/brainstorm/` untouched.

---

### Task 1: Seeded fictional identity catalog and transcript provenance

**Files:**
- Create: `src/humanwire/synthetic_identities.py`
- Modify: `src/humanwire/synthetic.py:45-235, 899-960, 1204-1260`
- Modify: `tests/fixtures/humanwire/synthetic_launch_v1.json`
- Test: `tests/humanwire/test_synthetic.py:26-170, 250-410`

**Interfaces:**
- Produces: `IDENTITY_GENERATOR_VERSION: Literal["humanwire.synthetic-identities/v1"]`.
- Produces: `seeded_identity_map(seed: int, persona_ids: Sequence[str]) -> dict[str, FictionalIdentity]`.
- Changes: `default_synthetic_scenario(seed: int = 0) -> SyntheticScenario`.
- Adds: `SyntheticScenario.identity_seed: int` and `SyntheticScenario.identity_generator_version: Literal["humanwire.synthetic-identities/v1"]`.
- Preserves: persona IDs, contracts, allowed intents, route ownership, default seed reproducibility, and current primary `meeting_ready` plus independent `partial` outcome.

- [ ] **Step 1: Write the seeded-identity RED tests**

```python
def test_seeded_identities_are_stable_distinct_and_synthetic() -> None:
    first = default_synthetic_scenario(seed=8842)
    second = default_synthetic_scenario(seed=8842)
    changed = default_synthetic_scenario(seed=8843)

    assert first.model_dump_json() == second.model_dump_json()
    assert first.identity_seed == 8842
    assert first.identity_generator_version == "humanwire.synthetic-identities/v1"
    assert [p.persona_id for p in first.personas] == [p.persona_id for p in changed.personas]
    assert len({p.display_name for p in first.personas}) == 9
    assert all(p.email.endswith("@example.test") for p in first.personas)
    assert [p.display_name for p in first.personas] != [p.display_name for p in changed.personas]


def test_generation_never_reads_or_writes_private_live_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_directory = tmp_path / "private-organization.json"
    private_directory.write_bytes(b'{"sentinel":"LIVE-DIRECTORY-BYTES"}')
    monkeypatch.setenv("ORGANIZATION_PATH", str(private_directory))

    result = generate_scenario(
        default_synthetic_scenario(seed=77),
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
    )

    assert result.transcript.scenario.identity_seed == 77
    assert private_directory.read_bytes() == b'{"sentinel":"LIVE-DIRECTORY-BYTES"}'
```

- [ ] **Step 2: Run the identity tests and record the expected RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_synthetic.py -k "seeded_identities or private_live_directory" -v
```

Expected: FAIL because `default_synthetic_scenario` has no `seed` parameter and `SyntheticScenario` has no generator provenance fields.

- [ ] **Step 3: Add a deterministic catalog that does not depend on Python's random implementation**

```python
# src/humanwire/synthetic_identities.py
from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IDENTITY_GENERATOR_VERSION: Literal["humanwire.synthetic-identities/v1"] = (
    "humanwire.synthetic-identities/v1"
)

_FICTIONAL_NAMES = (
    "Avery Chen", "Maya Brooks", "Eli Torres", "Sora Kim", "Priya Shah",
    "Noah Williams", "Lina Alvarez", "Jonah Reed", "Amara Okafor",
    "Theo Martin", "Nadia Patel", "Miles Bennett", "Inez Ward",
    "Kai Morgan", "Leila Haddad", "Owen Park", "Zara Flores",
    "Ravi Mehta", "Talia Green", "Marco Silva", "Anika Rao",
    "Drew Lawson", "Nora Jensen", "Samira Cole",
)


class FictionalIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    persona_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    email: str = Field(pattern=r"^[a-z0-9-]+@example\.test$")


def seeded_identity_map(
    seed: int, persona_ids: Sequence[str]
) -> dict[str, FictionalIdentity]:
    if seed < 0 or seed > 2_147_483_647:
        raise ValueError("identity seed must be between 0 and 2147483647")
    if len(set(persona_ids)) != len(persona_ids):
        raise ValueError("persona IDs must be unique")
    if len(persona_ids) > len(_FICTIONAL_NAMES):
        raise ValueError("fictional identity catalog is too small")
    ranked = sorted(
        _FICTIONAL_NAMES,
        key=lambda name: hashlib.sha256(
            f"{IDENTITY_GENERATOR_VERSION}:{seed}:{name}".encode("utf-8")
        ).digest(),
    )
    result: dict[str, FictionalIdentity] = {}
    selected = ranked[: len(persona_ids)]
    for persona_id, display_name in zip(persona_ids, selected, strict=True):
        local = re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-")
        result[persona_id] = FictionalIdentity(
            persona_id=persona_id,
            display_name=display_name,
            email=f"{local}@example.test",
        )
    return result
```

- [ ] **Step 4: Record the seed in `SyntheticScenario` and apply identities without changing persona roles/contracts**

```python
class SyntheticScenario(_StrictModel):
    schema_version: Literal["humanwire.synthetic/v1"]
    scenario_id: str = Field(pattern=_STABLE_ID_PATTERN)
    identity_seed: int = Field(ge=0, le=2_147_483_647)
    identity_generator_version: Literal["humanwire.synthetic-identities/v1"]
    personas: list[SyntheticPersona] = Field(min_length=1, max_length=32)
    provenance: SyntheticProvenance


def default_synthetic_scenario(seed: int = 0) -> SyntheticScenario:
    persona_ids = (
        "synthetic-manager", "inform", "ack", "quick-a", "quick-b",
        "structured", "approval", "availability", "approval-change",
    )
    identities = seeded_identity_map(seed, persona_ids)
    personas = [
        persona.model_copy(
            update={
                "display_name": identities[persona.persona_id].display_name,
                "email": identities[persona.persona_id].email,
            }
        )
        for persona in _default_persona_contracts()
    ]
    return SyntheticScenario(
        schema_version=SUPPORTED_SCHEMA_VERSION,
        scenario_id="launch-v1",
        identity_seed=seed,
        identity_generator_version=IDENTITY_GENERATOR_VERSION,
        personas=personas,
        provenance=_synthetic_provenance(),
    )
```

`_default_persona_contracts()` is the existing nine-entry persona list moved intact from `default_synthetic_scenario`; it keeps the current IDs, roles, channels, allowed intents, and structured private fixture fact. `_synthetic_provenance()` returns the existing six literal labels without accepting arguments.

Replace the hard-coded manager name/address in generation, replay, and `_synthetic_directory` with the exact `synthetic-manager` persona from the validated scenario:

```python
def _manager_persona(scenario: SyntheticScenario) -> SyntheticPersona:
    matches = [p for p in scenario.personas if p.persona_id == "synthetic-manager"]
    if len(matches) != 1:
        raise ValueError("synthetic scenario requires exactly one manager")
    return matches[0]
```

Add `identity_seed` and `identity_generator_version` to `_semantic_trace(result)["scenario"]` so the generator contract itself participates in the semantic hash independently of display-name changes.

- [ ] **Step 5: Regenerate the checked-in deterministic fixture with seed `0` and verify digest sensitivity**

Run from the worktree:

```powershell
$fixtureRun = Join-Path (Resolve-Path .) ("work\agent-identities-fixture-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m humanwire synthetic generate --output (Join-Path $fixtureRun "transcript.json") --run-root $fixtureRun
Copy-Item -LiteralPath (Join-Path $fixtureRun "transcript.json") -Destination tests\fixtures\humanwire\synthetic_launch_v1.json -Force
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_synthetic.py -k "schema or provenance or seeded or frozen or approved_primary" -v
```

Expected: all selected tests PASS; same-seed transcript bytes match; changing seed changes transcript digest and semantic trace hash while persona IDs/contracts stay fixed.

- [ ] **Step 6: Run Task 1 regressions and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_synthetic.py -v
.\.venv\Scripts\ruff.exe check src\humanwire\synthetic.py src\humanwire\synthetic_identities.py tests\humanwire\test_synthetic.py
git diff --check
git add src/humanwire/synthetic.py src/humanwire/synthetic_identities.py tests/humanwire/test_synthetic.py tests/fixtures/humanwire/synthetic_launch_v1.json
git commit -m "feat: seed fictional HumanWire identities"
```

---

### Task 2: Strict persona decision protocol and Featherless adapter

**Files:**
- Create: `src/humanwire/persona_runtime.py`
- Modify: `src/humanwire/synthetic.py:52-67, 330-560, 1000-1090`
- Modify: `tests/fixtures/humanwire/synthetic_launch_v1.json`
- Create: `tests/humanwire/test_persona_runtime.py`
- Test: `tests/humanwire/test_synthetic.py:419-634`

**Interfaces:**
- Produces: `SyntheticProvenance`, `PersonaVisibility`, `PersonaTranscriptEntry`, `PersonaProfile`, `PersonaContext`, and `PersonaDecision` strict frozen Pydantic models.
- Produces: `PersonaDecisionEngine.decide(profile: PersonaProfile, context: PersonaContext) -> PersonaDecision`.
- Produces: `FeatherlessPersonaDecisionEngine(client: JsonModelClient, model_identifier: str).decide(profile: PersonaProfile, context: PersonaContext) -> PersonaDecision`.
- Preserves: `SyntheticIntent` importability from `humanwire.synthetic` by re-exporting it from the new module.
- Consumes: existing `JsonModelClient.complete_json(system: str, user: str) -> dict` and safe `ModelFailure` reasons.

- [ ] **Step 1: Write strict-context and forged-authority RED tests**

```python
class CapturingClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> dict:
        self.calls.append((system, user))
        return self.payload


def test_model_engine_receives_only_the_approved_persona_context() -> None:
    client = CapturingClient(
        {"time_offset_seconds": 2, "intent": "acknowledge", "content": "ACK", "visibility": "shareable"}
    )
    decision = FeatherlessPersonaDecisionEngine(client, "fixture/model").decide(
        PersonaProfile(
            role="Executive owner",
            private_facts=("fictional constraint",),
            allowed_intents=(SyntheticIntent.ACKNOWLEDGE,),
            engagement_contract=EngagementType.ACKNOWLEDGE,
        ),
        PersonaContext(
            delivered_message="HUMANWIRE ACKNOWLEDGEMENT REQUEST",
            own_inbox=("HUMANWIRE ACKNOWLEDGEMENT REQUEST",),
            own_transcript=(),
            virtual_time=NOW,
        ),
    )

    payload = json.loads(client.calls[0][1])
    assert set(payload) == {"profile", "context", "output_schema"}
    assert set(payload["profile"]) == {"role", "private_facts", "allowed_intents", "engagement_contract"}
    assert set(payload["context"]) == {"delivered_message", "own_inbox", "own_transcript", "virtual_time"}
    assert not re.search(r"sender|route|destination|conversation|connection|message_id|assignment|token|database|repository", client.calls[0][1], re.I)
    assert decision.intent is SyntheticIntent.ACKNOWLEDGE


@pytest.mark.parametrize("forged_key", ["sender_address", "route_id", "conversation_id", "assignment_id", "approved"])
def test_model_engine_rejects_extra_authority_fields(forged_key: str) -> None:
    payload = {
        "time_offset_seconds": 1,
        "intent": "approve",
        "content": "APPROVE",
        "visibility": "shareable",
        forged_key: "forged",
    }
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PersonaDecision.model_validate(payload)
```

- [ ] **Step 2: Run the focused tests and record missing-module RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_persona_runtime.py -v
```

Expected: collection FAIL with `ModuleNotFoundError: No module named 'humanwire.persona_runtime'`.

- [ ] **Step 3: Define the exact shared models and decision protocol**

```python
# src/humanwire/persona_runtime.py
from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from humanwire.domain import EngagementType
from humanwire.model_client import JsonModelClient

MAX_PERSONA_CONTENT_LENGTH = 600
PERSONA_PROMPT_VERSION = "humanwire.persona-decision/v1"


class StrictPersonaModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SyntheticIntent(StrEnum):
    ACKNOWLEDGE = "acknowledge"
    ANSWER = "answer"
    INTERVIEW_RESPONSE = "interview_response"
    CONFIRM_EVIDENCE = "confirm_evidence"
    APPROVE = "approve"
    CHANGE = "change"
    AVAILABILITY = "availability"
    ACCEPT_PROPOSAL = "accept_proposal"
    CHANGE_PROPOSAL = "change_proposal"
    SILENCE = "silence"
    ERROR = "error"


class SyntheticProvenance(StrictPersonaModel):
    proof_class: Literal["synthetic_multi_persona"]
    actor_type: Literal["simulated_persona"]
    identity_source: Literal["synthetic_fixture"]
    transport: Literal["fake_caspian"]
    human_attested: Literal[False]
    live_provider_verified: Literal[False]


class PersonaVisibility(StrEnum):
    SHAREABLE = "shareable"
    ANONYMOUS = "anonymous"
    PRIVATE = "private"


class PersonaTranscriptEntry(StrictPersonaModel):
    timestamp: datetime
    local_sequence: int = Field(ge=1)
    intent: SyntheticIntent
    content: str = Field(min_length=1, max_length=MAX_PERSONA_CONTENT_LENGTH)


class PersonaProfile(StrictPersonaModel):
    role: str = Field(min_length=1, max_length=200)
    private_facts: tuple[str, ...] = Field(max_length=8)
    allowed_intents: tuple[SyntheticIntent, ...] = Field(min_length=1, max_length=8)
    engagement_contract: EngagementType


class PersonaContext(StrictPersonaModel):
    delivered_message: str = Field(min_length=1, max_length=MAX_PERSONA_CONTENT_LENGTH)
    own_inbox: tuple[str, ...] = Field(min_length=1, max_length=64)
    own_transcript: tuple[PersonaTranscriptEntry, ...] = Field(max_length=64)
    virtual_time: datetime


class PersonaDecision(StrictPersonaModel):
    time_offset_seconds: int = Field(ge=0, le=60)
    intent: SyntheticIntent
    content: str = Field(min_length=1, max_length=MAX_PERSONA_CONTENT_LENGTH)
    visibility: PersonaVisibility = PersonaVisibility.SHAREABLE


class PersonaDecisionEngine(Protocol):
    def decide(self, profile: PersonaProfile, context: PersonaContext) -> PersonaDecision:
        raise NotImplementedError
```

- [ ] **Step 4: Implement the central JSON-only adapter with no identity or tools**

```python
class FeatherlessPersonaDecisionEngine:
    def __init__(self, client: JsonModelClient, model_identifier: str) -> None:
        if not model_identifier or len(model_identifier) > 200:
            raise ValueError("model identifier must be bounded")
        self._client = client
        self.model_identifier = model_identifier

    def decide(self, profile: PersonaProfile, context: PersonaContext) -> PersonaDecision:
        system = (
            "You are one fictional HumanWire simulation persona. "
            "Use only the supplied profile and your own context. "
            "Return one JSON object matching output_schema exactly. "
            "Never invent identity, routing, authority, credentials, tools, or workflow state."
        )
        user = json.dumps(
            {
                "profile": profile.model_dump(mode="json"),
                "context": context.model_dump(mode="json"),
                "output_schema": {
                    "time_offset_seconds": "integer 0..60",
                    "intent": [item.value for item in profile.allowed_intents],
                    "content": "non-empty string, maximum 600 characters",
                    "visibility": [item.value for item in PersonaVisibility],
                },
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        decision = PersonaDecision.model_validate(self._client.complete_json(system, user))
        if decision.intent not in profile.allowed_intents:
            raise ValueError("persona decision used a disallowed intent")
        folded = decision.content.casefold()
        if any(fact.casefold() in folded for fact in profile.private_facts):
            raise ValueError("persona decision exposed a private fixture fact")
        if re.search(
            r"\bHW-[A-F0-9]{8}\b|\b[^\s@]+@[^\s@]+\b|"
            r"\b(?:api[_-]?key|authorization|route_id|conversation_id|connection_id|assignment_id)\b|"
            r"^\s*/(?:mandate|go|confirm|decide|available)\b",
            decision.content,
            re.I,
        ):
            raise ValueError("persona decision contained forbidden identity or command data")
        return decision
```

Add parameterized tests for exact private-fact echo, invented email, `HW-` token, route/conversation/connection/assignment key text, and slash commands. Each must fail before any gateway inbound is built.

Add `visibility: PersonaVisibility = PersonaVisibility.SHAREABLE` to `SyntheticAction`, include it in semantic action normalization, and pass it into `_wire_command`. For `ANSWER` and `INTERVIEW_RESPONSE`, wire text is exactly `f"{visibility.value.upper()}: {content}"`; other intents ignore visibility. Refactor the deterministic structured policy's existing `PRIVATE:` answer into `content="must preserve sha256:<digest>"` plus `visibility=PersonaVisibility.PRIVATE`, while its remaining answers stay `SHAREABLE`. This preserves evidence visibility without allowing a model to inject a command prefix through `content`.

- [ ] **Step 5: Re-export shared names and retain the structural isolation tests**

In `synthetic.py`, replace the local duplicate models with imports aliased for existing tests:

```python
from humanwire.persona_runtime import (
    PersonaContext as _PersonaContext,
    PersonaDecision as _PersonaDecision,
    PersonaProfile as _PolicyProfile,
    PersonaTranscriptEntry as _PersonaTranscriptEntry,
    SyntheticIntent,
    SyntheticGenerationMode,
    SyntheticProvenance,
)
```

Run the old graph/closure isolation tests plus the new adapter tests. They must prove that deterministic policy instances retain only `_PolicyProfile` and local completion state, while the centralized model adapter—not a persona policy—retains the model client.

- [ ] **Step 6: Run Task 2 regressions and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_persona_runtime.py tests\humanwire\test_synthetic.py -k "persona or policy or isolation or output" -v
$fixtureRun = Join-Path (Resolve-Path .) ("work\persona-runtime-fixture-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m humanwire synthetic generate --output (Join-Path $fixtureRun "transcript.json") --run-root $fixtureRun
Copy-Item -LiteralPath (Join-Path $fixtureRun "transcript.json") -Destination tests\fixtures\humanwire\synthetic_launch_v1.json -Force
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_synthetic.py -k "frozen or semantic or approved_primary" -v
.\.venv\Scripts\ruff.exe check src\humanwire\persona_runtime.py src\humanwire\synthetic.py tests\humanwire\test_persona_runtime.py tests\humanwire\test_synthetic.py
git diff --check
git add src/humanwire/persona_runtime.py src/humanwire/synthetic.py tests/humanwire/test_persona_runtime.py tests/humanwire/test_synthetic.py tests/fixtures/humanwire/synthetic_launch_v1.json
git commit -m "feat: constrain HumanWire persona decisions"
```

---

### Task 3: Model-assisted generation, deterministic concurrency, and private sidecar

**Files:**
- Modify: `src/humanwire/persona_runtime.py`
- Modify: `src/humanwire/synthetic.py:384-430, 899-1201, 1204-1414`
- Modify: `tests/fixtures/humanwire/synthetic_launch_v1.json`
- Test: `tests/humanwire/test_synthetic.py:542-746, 1072-1278`
- Test: `tests/humanwire/test_persona_runtime.py`

**Interfaces:**
- Adds in `persona_runtime.py`: `SyntheticGenerationMode` values `deterministic`, `model_assisted`, and `frozen_replay`.
- Changes: `generate_scenario(scenario: SyntheticScenario, output_path: str | Path, run_root: str | Path, *, decision_engine: PersonaDecisionEngine | None = None, max_decision_workers: int = 1) -> SyntheticRunResult`.
- Adds: `SyntheticRunResult.mode`, `SyntheticRunResult.model_identifier`, and `SyntheticRunResult.provenance_path`.
- Produces: strict `SyntheticRunSidecar` written only for explicit model-assisted generation.
- Preserves: `replay_transcript(path, run_root)` without policy/model construction and existing deterministic output when `decision_engine is None`.

- [ ] **Step 1: Write RED tests for explicit engine use, safe failure, concurrency, and canonical order**

```python
class BarrierDecisionEngine:
    model_identifier = "fixture/barrier"

    def __init__(self) -> None:
        self.barrier = threading.Barrier(2)
        self.active = 0
        self.maximum_active = 0
        self.lock = threading.Lock()

    def decide(self, profile: PersonaProfile, context: PersonaContext) -> PersonaDecision:
        with self.lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        self.barrier.wait(timeout=2)
        with self.lock:
            self.active -= 1
        return scripted_decision_for(profile, context)


def test_model_decisions_may_overlap_but_commit_in_canonical_order(tmp_path) -> None:
    engine = BarrierDecisionEngine()
    first = generate_scenario(
        concurrent_generation_scenario(),
        tmp_path / "a" / "transcript.json",
        tmp_path / "a",
        decision_engine=engine,
        max_decision_workers=2,
    )
    second = generate_scenario(
        concurrent_generation_scenario(),
        tmp_path / "b" / "transcript.json",
        tmp_path / "b",
        decision_engine=BarrierDecisionEngine(),
        max_decision_workers=2,
    )

    assert engine.maximum_active == 2
    assert first.transcript.model_dump_json() == second.transcript.model_dump_json()
    assert [canonical_action_order(first.transcript.scenario, a) for a in first.transcript.actions] == sorted(
        canonical_action_order(first.transcript.scenario, a) for a in first.transcript.actions
    )


def test_model_failure_records_error_without_gateway_authority(tmp_path) -> None:
    result = generate_scenario(
        one_person_scenario(),
        tmp_path / "run" / "transcript.json",
        tmp_path / "run",
        decision_engine=FailingDecisionEngine(ModelFailure("timeout")),
    )
    action = result.transcript.actions[0]
    assert action.intent is SyntheticIntent.ERROR
    assert action.content == "synthetic_model_timeout"
    assert result.inbound_envelopes == ()
```

Also add tests proving one persona never has two concurrent decisions, a disallowed intent creates no inbound envelope, duplicate trigger order is deterministic, and replay raises if any model/policy constructor is touched.

- [ ] **Step 2: Run the focused tests and record signature/behavior RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_synthetic.py tests\humanwire\test_persona_runtime.py -k "model_decision or canonical_order or one_in_flight or model_failure or replay_never" -v
```

Expected: FAIL because `generate_scenario` does not accept a decision engine or worker count and `SyntheticRunResult` has no generation mode/sidecar fields.

- [ ] **Step 3: Add explicit mode and safe model provenance types**

```python
class SyntheticGenerationMode(StrEnum):
    DETERMINISTIC = "deterministic"
    MODEL_ASSISTED = "model_assisted"
    FROZEN_REPLAY = "frozen_replay"


class SyntheticRunSidecar(_StrictModel):
    schema_version: Literal["humanwire.synthetic-run/v1"]
    mode: Literal["model_assisted"]
    model_identifier: str = Field(min_length=1, max_length=200)
    prompt_version: Literal["humanwire.persona-decision/v1"]
    identity_seed: int = Field(ge=0, le=2_147_483_647)
    transcript_sha256: str = Field(pattern=_DIGEST_PATTERN)
    provenance: SyntheticProvenance
```

Do not serialize a key, base URL, prompt body, private fact, raw model body, route, address, database path, or exception text.

- [ ] **Step 4: Batch only distinct-persona decisions and commit results in queue order**

Use a private candidate model that holds `persona_id`, sanitized profile/context, trigger metadata, and delivery channel but no decision. Partition each persisted delivery boundary so each batch contains at most one candidate per persona:

```python
def _evaluate_model_batch(
    engine: PersonaDecisionEngine,
    candidates: list[_DecisionCandidate],
    max_workers: int,
) -> list[PersonaDecision | Exception]:
    workers = max(1, min(max_workers, len(candidates), 8))
    if workers == 1:
        return [_safe_decide(engine, item.profile, item.context) for item in candidates]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="humanwire-persona") as pool:
        futures = [pool.submit(_safe_decide, engine, item.profile, item.context) for item in candidates]
        return [future.result() for future in futures]
```

Build actions from `zip(candidates, results, strict=True)`, then push them to the heap keyed by `canonical_action_order(scenario, action)`. Define that function as `(action.timestamp, scenario_persona_rank[action.persona_id], action.trigger_id, action.local_sequence)`, and use the same function in `SyntheticTranscript.is_valid_transcript`. Never append actions in future-completion order. Keep deterministic policies on the current sequential stateful path.

- [ ] **Step 5: Map failures to safe inert outcomes and write the sidecar exclusively**

```python
def _safe_model_failure(error: Exception) -> tuple[SyntheticIntent, str]:
    if isinstance(error, ModelFailure) and error.reason in {
        "timeout", "network_error", "invalid_response", "invalid_json", "invalid_schema"
    }:
        return SyntheticIntent.ERROR, f"synthetic_model_{error.reason}"
    return SyntheticIntent.ERROR, "synthetic_model_invalid_output"
```

After the transcript is atomically written, write `provenance.json` with exclusive create only in model-assisted mode. A sidecar write failure fails the run before it is reported complete; it never rewrites the transcript or retries workflow effects.

- [ ] **Step 6: Verify deterministic and replay behavior is unchanged**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_persona_runtime.py tests\humanwire\test_synthetic.py -v
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_gateway.py tests\humanwire\test_workflow.py tests\humanwire\test_demo.py -q
$fixtureRun = Join-Path (Resolve-Path .) ("work\model-runtime-fixture-" + [guid]::NewGuid().ToString("N"))
.\.venv\Scripts\python.exe -m humanwire synthetic generate --output (Join-Path $fixtureRun "transcript.json") --run-root $fixtureRun
Copy-Item -LiteralPath (Join-Path $fixtureRun "transcript.json") -Destination tests\fixtures\humanwire\synthetic_launch_v1.json -Force
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_synthetic.py -k "frozen or semantic or approved_primary" -v
```

Expected: all tests PASS; deterministic generation remains byte-identical for the same seed; replay constructs neither decision engine nor deterministic policy; both primary/change outcomes remain `meeting_ready,partial`.

- [ ] **Step 7: Run privacy/lint checks and commit**

```powershell
.\.venv\Scripts\ruff.exe check src\humanwire\synthetic.py src\humanwire\persona_runtime.py tests\humanwire\test_synthetic.py tests\humanwire\test_persona_runtime.py
rg -n "api_key|Authorization|PRIVATE-PERSONA-SENTINEL|conversation_id|connection_id|sender_address|database_path" tests\fixtures\humanwire\synthetic_launch_v1.json
git diff --check
git add src/humanwire/synthetic.py src/humanwire/persona_runtime.py tests/humanwire/test_synthetic.py tests/humanwire/test_persona_runtime.py tests/fixtures/humanwire/synthetic_launch_v1.json
git commit -m "feat: run model-assisted HumanWire personas"
```

Expected privacy scan: no credential/provider/database values; only schema-approved synthetic transcript fields. The private sentinel must not appear.

---

### Task 4: Shared replay labels and persisted-progress projection

**Files:**
- Create: `src/humanwire/replay_projection.py`
- Create: `src/humanwire/synthetic_progress.py`
- Modify: `src/humanwire/web.py:1511-1580, 1632-1785`
- Modify: `src/humanwire/synthetic.py:899-1414`
- Create: `tests/humanwire/test_synthetic_progress.py`
- Test: `tests/humanwire/test_web.py:1659-2200`

**Interfaces:**
- Produces: `ReplayLabels` and `project_replay_labels(event_type: str, person_name: str | None) -> ReplayLabels`.
- Produces: `SyntheticProgressEvent`, `SyntheticPersonaProgress`, `SyntheticProgressSnapshot`, `SyntheticEvidenceBundle`, `SyntheticProgressStore`, `SyntheticScenarioView`, and `SyntheticProgressObserver`.
- Produces: `RepositoryProgressObserver.capture(repository, scenario, *, mode, run_state, runtime_status, active_persona_id=None, final_trace_sha256=None) -> None`.
- Changes: `generate_scenario(scenario: SyntheticScenario, output_path: str | Path, run_root: str | Path, *, decision_engine: PersonaDecisionEngine | None = None, max_decision_workers: int = 1, progress_observer: SyntheticProgressObserver | None = None)` and `replay_transcript(path: str | Path, run_root: str | Path, *, progress_observer: SyntheticProgressObserver | None = None)`.
- Import direction: `persona_runtime.py` owns shared provenance/mode types; `synthetic_progress.py` imports only `persona_runtime.py` and structural protocols; `synthetic.py` may import `synthetic_progress.py`. `synthetic_progress.py` must not import `synthetic.py`, preventing a cycle.
- Preserves: current public Reach labels, exact binding/fail-closed behavior, and public demo output when no observer exists.

- [ ] **Step 1: Write replay parity, mid-run truth, and recursive privacy RED tests**

```python
def test_shared_labels_preserve_existing_reach_contract() -> None:
    labels = project_replay_labels("interview.evidence_confirmed", "Avery Chen")
    assert labels.model_dump() == {
        "stage": "Evidence",
        "source": "Avery Chen",
        "destination": "HumanWire",
        "data_point": "Evidence confirmed",
    }


def test_mid_run_snapshot_contains_only_persisted_steps(tmp_path) -> None:
    store = SyntheticProgressStore(initial_progress(default_synthetic_scenario(seed=8)))
    observer = BlockingProgressObserver(store, release_after_event_count=5)
    worker = threading.Thread(
        target=generate_scenario,
        kwargs={
            "scenario": default_synthetic_scenario(seed=8),
            "output_path": tmp_path / "run" / "transcript.json",
            "run_root": tmp_path / "run",
            "progress_observer": observer,
        },
    )
    worker.start()
    snapshot = observer.wait_for_block(timeout=5)
    assert snapshot.run_state == "running"
    assert snapshot.saved_event_count == 5
    assert sum(event.effect == "persisted" for event in snapshot.events) == 5
    assert snapshot.final_trace_sha256 is None
    assert all(event.ordinal <= 5 for event in snapshot.events)
    observer.release()
    worker.join(timeout=10)
    assert not worker.is_alive()


def test_progress_json_has_no_private_or_identity_fields(completed_progress) -> None:
    raw = completed_progress.model_dump_json()
    forbidden = re.compile(
        r"PRIVATE-PERSONA-SENTINEL|api[_-]?key|route|address|destination_id|conversation|connection|message_id|assignment_id|database|[0-9a-f]{8}-[0-9a-f-]{27,}",
        re.I,
    )
    assert forbidden.search(raw) is None
```

Add cross-assignment, missing-person, duplicate-person, and unknown-event tests. They must retain the saved event with `highlight_target="none"` and generic `No public data point`, never attribute it to another persona.

- [ ] **Step 2: Run the focused tests and record missing-module RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_synthetic_progress.py tests\humanwire\test_web.py -k "progress or replay_labels or reach_binds_history" -v
```

Expected: collection FAIL because `humanwire.synthetic_progress` and `humanwire.replay_projection` do not exist.

- [ ] **Step 3: Extract the allowlisted replay mapping into a pure module**

Move the complete current `_REPLAY_EVENT_EXPLANATIONS` literal from `web.py` into `replay_projection.py` without adding event types. Implement:

```python
class ReplayLabels(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    stage: str
    source: str
    destination: str
    data_point: str


def project_replay_labels(event_type: str, person_name: str | None) -> ReplayLabels:
    definition = REPLAY_EVENT_EXPLANATIONS.get(event_type)
    if definition is None:
        return ReplayLabels(
            stage="Saved event",
            source="HumanWire",
            destination="Decision Room",
            data_point="No public data point",
        )
    stage, data_point, raw_source, raw_destination = definition
    source = person_name if raw_source == "person" else raw_source
    destination = person_name if raw_destination == "person" else raw_destination
    if not source or not destination:
        return project_replay_labels("", None)
    return ReplayLabels(
        stage=stage,
        source=source,
        destination=destination,
        data_point=data_point,
    )
```

Update `_reach_page_view` to consume this function while retaining its exact mandate+assignment+person binding checks. Run the full web replay tests before adding progress code.

- [ ] **Step 4: Define the strict safe snapshot and thread-safe store**

```python
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from humanwire.persona_runtime import SyntheticGenerationMode, SyntheticProvenance


class _StrictProgressModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SyntheticRunState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class SyntheticRuntimeStatus(StrEnum):
    PERSISTED = "persisted"
    WAITING_FOR_AGENT = "waiting_for_agent"
    SYNTHETIC_SILENCE = "synthetic_silence"
    SYNTHETIC_TIMEOUT = "synthetic_timeout"
    MODEL_ERROR = "model_error"
    WORKFLOW_REJECTED = "workflow_rejected"
    TERMINAL_FAILURE = "terminal_failure"
    UNAVAILABLE = "unavailable"


class SyntheticProgressEvent(_StrictProgressModel):
    timeline_ordinal: int = Field(ge=1)
    persisted_ordinal: int | None = Field(default=None, ge=1)
    created_at: datetime
    story: Literal["primary", "change"]
    effect: Literal["persisted", "inert_attempt"]
    stage: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1, max_length=120)
    destination: str = Field(min_length=1, max_length=120)
    data_point: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=200)
    highlight_target: str = Field(pattern=r"^(origin|none|persona-[1-9][0-9]*)$")
    persona_label: str | None = Field(default=None, max_length=120)
    contract: str | None = Field(default=None, max_length=64)


class SyntheticPersonaProgress(_StrictProgressModel):
    ordinal: int = Field(ge=1)
    display_name: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=200)
    contract: str | None = Field(default=None, max_length=64)
    status: str = Field(min_length=1, max_length=64)
    progress_current: int = Field(ge=0)
    progress_total: int = Field(ge=0)


class SyntheticAggregateCounts(_StrictProgressModel):
    personas: int = Field(ge=0)
    persisted_events: int = Field(ge=0)
    inert_attempts: int = Field(ge=0)
    complete_assignments: int = Field(ge=0)
    pending_assignments: int = Field(ge=0)
    terminal_mandates: int = Field(ge=0)


class SyntheticProgressSnapshot(_StrictProgressModel):
    schema_version: Literal["humanwire.synthetic-progress/v1"]
    provenance: SyntheticProvenance
    run_alias: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    scenario_label: str = Field(min_length=1, max_length=120)
    mode: SyntheticGenerationMode
    run_state: SyntheticRunState
    runtime_status: SyntheticRuntimeStatus
    active_persona_label: str | None = Field(default=None, max_length=120)
    active_contract: str | None = Field(default=None, max_length=64)
    saved_event_count: int = Field(ge=0)
    timeline_event_count: int = Field(ge=0)
    current_timeline_ordinal: int = Field(ge=0)
    current_persisted_ordinal: int = Field(ge=0)
    events: tuple[SyntheticProgressEvent, ...]
    personas: tuple[SyntheticPersonaProgress, ...]
    aggregate_counts: SyntheticAggregateCounts
    terminal_states: tuple[str, ...] = ()
    final_trace_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class SyntheticEvidenceBundle(_StrictProgressModel):
    schema_version: Literal["humanwire.synthetic-evidence/v1"]
    provenance: SyntheticProvenance
    run_alias: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    scenario_label: str = Field(min_length=1, max_length=120)
    mode: SyntheticGenerationMode
    identity_seed: int = Field(ge=0, le=2_147_483_647)
    terminal_states: tuple[str, ...]
    aggregate_counts: SyntheticAggregateCounts
    events: tuple[SyntheticProgressEvent, ...]
    trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SyntheticPersonaView(Protocol):
    persona_id: str
    display_name: str
    role: str


class SyntheticScenarioView(Protocol):
    scenario_id: str
    identity_seed: int
    provenance: SyntheticProvenance
    personas: Sequence[SyntheticPersonaView]


class SyntheticProgressObserver(Protocol):
    def capture(
        self,
        repository: object,
        scenario: SyntheticScenarioView,
        *,
        mode: SyntheticGenerationMode,
        run_state: SyntheticRunState,
        runtime_status: SyntheticRuntimeStatus,
        active_persona_id: str | None = None,
        final_trace_sha256: str | None = None,
    ) -> None:
        raise NotImplementedError

    def mark_unavailable(self) -> None:
        raise NotImplementedError

    def record_inert_attempt(
        self,
        *,
        virtual_time: datetime,
        story: Literal["primary", "change"],
        persona_id: str | None,
        contract: str | None,
        runtime_status: SyntheticRuntimeStatus,
        data_point: str,
    ) -> None:
        raise NotImplementedError


class SyntheticProgressStore:
    def __init__(self, initial: SyntheticProgressSnapshot) -> None:
        self._lock = threading.Lock()
        self._snapshot = initial

    def publish(self, snapshot: SyntheticProgressSnapshot) -> None:
        with self._lock:
            if len(snapshot.events) < len(self._snapshot.events):
                raise ValueError("synthetic progress cannot lose persisted events")
            self._snapshot = snapshot

    def snapshot(self) -> SyntheticProgressSnapshot:
        with self._lock:
            return self._snapshot.model_copy(deep=True)
```

- [ ] **Step 5: Project only exact persisted bindings and publish at causal boundaries**

For each mandate sorted by `created_at`, read `list_assignments` and `list_events`. Bind a person-scoped event only when exactly one assignment and exactly one scenario persona match `(mandate, assignment_id, person_id)`. Assign story `primary` to the first mandate and `change` to the second. Convert a bound persona to its one-based scenario ordinal and emit only `highlight_target="persona-N"`; do not emit `persona_id`. Build descriptions only from allowlisted replay labels plus safe channel/direction labels; never use event metadata or raw answer text. Merge the observer's safe inert-attempt records with persisted events by `(virtual_time, effect_order, local_ordinal)`. Persisted rows receive `persisted_ordinal`; silence, model error, rejected/duplicate inbound, and timeout rows use `effect="inert_attempt"`, `persisted_ordinal=None`, and `data_point="No workflow data saved"` (or the exact safe timeout/silence label).

Add a private generation helper:

```python
def _publish_progress(
    observer: SyntheticProgressObserver | None,
    repository: SqlAlchemyHumanWireRepository,
    scenario: SyntheticScenario,
    **state: object,
) -> None:
    if observer is None:
        return
    try:
        observer.capture(repository, scenario, **state)
    except Exception:  # progress is presentation-only
        observer.mark_unavailable()
```

Call it after manager mandate/release, after every gateway inbound completes, after every due-work dispatch, after the separate change mandate is saved, before a model call with `waiting_for_agent`, after silence/error with unchanged event count, and once after transcript creation with `run_state=complete` plus `semantic_trace_hash(result)`. Observer failure must not retry or roll back a workflow step.

Before each inbound, read the current persisted event count; after the one gateway call, read it again. If the count did not increase, call `record_inert_attempt(virtual_time=action.timestamp, story=story, persona_id=action.persona_id, contract=contract.value, runtime_status=SyntheticRuntimeStatus.WORKFLOW_REJECTED, data_point="No workflow data saved")`. Silence/error/timeout call `record_inert_attempt` without building an inbound envelope. This makes duplicate and rejected attempts visible without representing them as persisted workflow effects.

- [ ] **Step 6: Run progress/web regressions and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_synthetic_progress.py tests\humanwire\test_web.py -k "progress or replay or reach" -v
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_synthetic.py -v
.\.venv\Scripts\ruff.exe check src\humanwire\replay_projection.py src\humanwire\synthetic_progress.py src\humanwire\synthetic.py src\humanwire\web.py tests\humanwire\test_synthetic_progress.py tests\humanwire\test_web.py
git diff --check
git add src/humanwire/replay_projection.py src/humanwire/synthetic_progress.py src/humanwire/synthetic.py src/humanwire/web.py tests/humanwire/test_synthetic_progress.py tests/humanwire/test_web.py
git commit -m "feat: project persisted HumanWire simulation progress"
```

---

### Task 5: Loopback-only Follow Live and replay viewer

**Files:**
- Create: `src/humanwire/synthetic_viewer.py`
- Create: `src/humanwire/templates/synthetic_progress.html`
- Create: `src/humanwire/static/synthetic-progress.js`
- Modify: `src/humanwire/static/styles.css`
- Create: `tests/humanwire/test_synthetic_viewer.py`

**Interfaces:**
- Produces: `create_synthetic_viewer_app(store: SyntheticProgressStore, transcript_path: Path) -> FastAPI`.
- Produces: `run_synthetic_viewer(app: FastAPI, *, host: str = "127.0.0.1", port: int = 8766) -> None`.
- Routes: GET `/`, GET `/progress.json`, GET `/evidence.json`, GET `/events.csv`, and `/static/*`; all mutation methods return 405.
- Consumes: `SyntheticProgressSnapshot` only; the template/JS never receives repository/domain objects.

- [ ] **Step 1: Write endpoint, boundary, attachment, and DOM interaction RED tests**

```python
def test_viewer_is_get_only_and_progress_is_no_store(running_viewer_client) -> None:
    progress = running_viewer_client.get("/progress.json")
    assert progress.status_code == 200
    assert progress.headers["cache-control"] == "no-store"
    assert progress.json()["run_state"] == "running"
    for method in ("post", "put", "patch", "delete"):
        assert getattr(running_viewer_client, method)("/").status_code == 405


def test_final_downloads_are_unavailable_until_completion(running_viewer_client) -> None:
    for path in ("/evidence.json", "/events.csv"):
        response = running_viewer_client.get(path)
        assert response.status_code == 409
        assert "content-disposition" not in response.headers


def test_completed_json_and_csv_are_attachments(completed_viewer_client) -> None:
    json_response = completed_viewer_client.get("/evidence.json")
    csv_response = completed_viewer_client.get("/events.csv")
    assert json_response.headers["content-disposition"] == (
        'attachment; filename="humanwire-synthetic-evidence.json"'
    )
    assert csv_response.headers["content-disposition"] == (
        'attachment; filename="humanwire-synthetic-events.csv"'
    )
    evidence = json.loads(json_response.content)
    assert evidence["provenance"]["transport"] == "fake_caspian"
    assert "actions" not in evidence
    assert "outbound_digests" not in evidence
    assert csv_response.text.startswith("ordinal,created_at,story,stage,source,destination,data_point")


@pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.10", "example.test"])
def test_viewer_rejects_non_loopback_binding(host: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        validate_viewer_host(host)


def test_public_demo_has_no_local_progress_surface(web_client) -> None:
    assert web_client.get("/progress.json").status_code == 404
    assert web_client.get("/evidence.json").status_code == 404
    assert web_client.get("/events.csv").status_code == 404
```

Add a Node DOM harness that loads production `synthetic-progress.js`, supplies two polling snapshots, clicks Follow Live/Previous/Next/Play/Pause, and asserts exact `From`, `To`, `Generated`, progress copy, one-card highlight, polite live announcement, JSON disabled-before-complete/enabled-after-complete, hidden-page pause, and reduced-motion no-autoplay.

- [ ] **Step 2: Run viewer tests and record missing-module/template RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_synthetic_viewer.py -v
```

Expected: collection FAIL because `humanwire.synthetic_viewer` and the viewer template/static controller do not exist.

- [ ] **Step 3: Build a separate GET-only app with security headers**

```python
def validate_viewer_host(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise ValueError("synthetic viewer host must be a loopback IP") from error
    if not address.is_loopback:
        raise ValueError("synthetic viewer host must be loopback")
    return host


def create_synthetic_viewer_app(
    store: SyntheticProgressStore,
    transcript_path: Path,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(_PACKAGE_DIR / "static")), name="static")

    @app.middleware("http")
    async def safe_local_surface(request: Request, call_next):
        if request.method not in {"GET", "HEAD"}:
            return JSONResponse(status_code=405, content={"detail": "Method not allowed"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response
```

`/progress.json` returns `store.snapshot().model_dump(mode="json")`. `/evidence.json` first validates the completed internal transcript using `load_transcript`, then returns `SyntheticEvidenceBundle` built only from the completed safe snapshot; it never returns the transcript, actions, trigger digests, prompts, or answer content. `/events.csv` serializes only snapshot event fields and prefixes cells beginning with `=`, `+`, `-`, `@`, tab, carriage return, or newline with a single quote.

- [ ] **Step 4: Build the truthful live/replay DOM**

The template must render the six exact provenance labels, run mode/state/status, persona cards, saved-event count, a Follow Live toggle, a hidden event list, the visible provenance strip, replay controls, live region, and disabled download controls:

```html
<main id="main-content" class="synthetic-progress-page shell" data-synthetic-viewer>
  <aside class="proof-provenance" aria-label="Synthetic proof provenance">
    <strong>Synthetic agent simulation</strong>
    <ul>
      <li><code>proof_class=synthetic_multi_persona</code></li>
      <li><code>actor_type=simulated_persona</code></li>
      <li><code>identity_source=synthetic_fixture</code></li>
      <li><code>transport=fake_caspian</code></li>
      <li><code>human_attested=false</code></li>
      <li><code>live_provider_verified=false</code></li>
    </ul>
  </aside>
  <section class="synthetic-run-status" aria-live="polite" data-run-status>Starting</section>
  <ol class="synthetic-personas" data-persona-list></ol>
  <section class="replay-panel" aria-labelledby="synthetic-replay-heading">
    <button type="button" data-follow-live aria-pressed="true">Follow live</button>
    <ol class="replay-events" data-replay-list></ol>
    <dl class="replay-flow-strip" data-replay-flow>
      <div><dt>From</dt><dd data-replay-source>No persisted event yet</dd></div>
      <div><dt>To</dt><dd data-replay-destination>HumanWire</dd></div>
      <div><dt>Generated</dt><dd data-replay-data-point>Waiting</dd></div>
    </dl>
    <button type="button" data-replay-previous aria-label="Previous saved event">Previous</button>
    <button type="button" data-replay-play aria-label="Play saved events" aria-pressed="false">Play</button>
    <button type="button" data-replay-next aria-label="Next saved event">Next</button>
    <p class="sr-only" aria-live="polite" aria-atomic="true" data-replay-live></p>
  </section>
  <a href="/evidence.json" download data-evidence-json aria-disabled="true">Download JSON evidence</a>
  <a href="/events.csv" download data-evidence-csv aria-disabled="true">Download CSV events</a>
</main>
```

Use `textContent`, `setAttribute`, and `replaceChildren`; never assign server values through `innerHTML`. Poll `/progress.json` only while visible. Follow Live selects the newest persisted event. Any manual event selection disables Follow Live until its button is pressed again.

- [ ] **Step 5: Add responsive/accessibility CSS and executable DOM tests**

Add explicit CSS declarations for `min-height: 44px` controls, at least `font-size: 14px`, three-column desktop persona cards, one-column below `759px`, two-column replay flow below `759px`, visible `:focus-visible`, and reduced-motion transition removal:

```css
@media (prefers-reduced-motion: reduce) {
  .synthetic-progress-page *,
  .synthetic-progress-page *::before,
  .synthetic-progress-page *::after {
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
    animation-duration: 0.01ms !important;
  }
}
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_synthetic_viewer.py -v
node --check src\humanwire\static\synthetic-progress.js
```

Expected: endpoint and DOM harness tests PASS with no network beyond TestClient/local fetch stubs.

- [ ] **Step 6: Run viewer/web regressions and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_synthetic_viewer.py tests\humanwire\test_web.py -v
.\.venv\Scripts\ruff.exe check src\humanwire\synthetic_viewer.py tests\humanwire\test_synthetic_viewer.py
node --check src\humanwire\static\synthetic-progress.js
git diff --check
git add src/humanwire/synthetic_viewer.py src/humanwire/templates/synthetic_progress.html src/humanwire/static/synthetic-progress.js src/humanwire/static/styles.css tests/humanwire/test_synthetic_viewer.py
git commit -m "feat: replay live HumanWire simulation progress"
```

---

### Task 6: Explicit `synthetic watch` orchestration and model configuration

**Files:**
- Create: `src/humanwire/synthetic_watch.py`
- Modify: `src/humanwire/__main__.py:76-168`
- Modify: `scripts/synthetic_humanwire.py`
- Test: `tests/humanwire/test_synthetic.py:783-1305`
- Test: `tests/humanwire/test_synthetic_viewer.py`

**Interfaces:**
- Adds CLI: `humanwire synthetic generate --seed <int>`.
- Adds CLI: `humanwire synthetic watch --output <path> --run-root <path> --seed <int> --agent-mode deterministic|featherless --port <int> --step-delay-ms <int> --max-decision-workers <int>`.
- Produces: `SyntheticWatchOptions` and `run_synthetic_watch(options: SyntheticWatchOptions) -> int`.
- Guarantees: viewer host is hard-coded to `127.0.0.1`; deterministic mode never constructs `Settings` or a model client; Featherless mode requires an explicit existing key and records only safe model metadata.

- [ ] **Step 1: Write parser, safe-error, host, and orchestration RED tests**

```python
def test_watch_cli_binds_only_loopback_and_starts_generation_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, object] = {}

    def fake_uvicorn_run(app, *, host, port, **kwargs):
        calls.update(app=app, host=host, port=port, kwargs=kwargs)

    monkeypatch.setattr("humanwire.synthetic_watch.uvicorn.run", fake_uvicorn_run)
    exit_code = main([
        "synthetic", "watch",
        "--output", str(tmp_path / "run" / "transcript.json"),
        "--run-root", str(tmp_path / "run"),
        "--seed", "8842",
        "--agent-mode", "deterministic",
        "--port", "8766",
        "--step-delay-ms", "0",
        "--max-decision-workers", "1",
    ])
    assert exit_code == 0
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 8766


def test_deterministic_watch_ignores_ambient_featherless_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FEATHERLESS_API_KEY", "DO-NOT-READ")
    monkeypatch.setattr(Settings, "__init__", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Settings read")))
    assert run_test_watch(tmp_path, agent_mode="deterministic") == 0


def test_featherless_watch_without_key_fails_safely(tmp_path, capsys) -> None:
    assert run_test_watch(tmp_path, agent_mode="featherless", api_key=None) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "synthetic_status=failed\nfailure_reason=model_credentials_missing\n"
```

Also assert parser rejects duplicate flags, port outside `1024..65535`, delay outside `0..3000`, workers outside `1..8`, and any unsupported agent mode. There is no `--host` option.

- [ ] **Step 2: Run the CLI tests and record missing-command RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_synthetic.py tests\humanwire\test_synthetic_viewer.py -k "watch_cli or featherless_watch or deterministic_watch" -v
```

Expected: FAIL because the `watch` parser and `humanwire.synthetic_watch` do not exist.

- [ ] **Step 3: Add exact parser options without changing generate/replay defaults**

```python
watch = synthetic_modes.add_parser(
    "watch",
    help="run a loopback-only synthetic simulation viewer",
)
watch.add_argument("--output", required=True)
watch.add_argument("--run-root", required=True)
watch.add_argument("--seed", type=int, default=0)
watch.add_argument("--agent-mode", choices=("deterministic", "featherless"), default="deterministic")
watch.add_argument("--port", type=int, default=8766)
watch.add_argument("--step-delay-ms", type=int, default=350)
watch.add_argument("--max-decision-workers", type=int, default=4)
generate.add_argument("--seed", type=int, default=0)
```

Validate numeric ranges in `SyntheticWatchOptions`; do not expose a host argument.

- [ ] **Step 4: Build the engine only in explicit Featherless mode**

```python
def _decision_engine(mode: str) -> PersonaDecisionEngine | None:
    if mode == "deterministic":
        return None
    settings = Settings()
    if settings.featherless_api_key is None:
        raise ModelRuntimeUnavailable("model_credentials_missing")
    client = FeatherlessJsonClient(
        api_key=settings.featherless_api_key.get_secret_value(),
        model=settings.featherless_model,
        base_url=settings.featherless_base_url,
    )
    return FeatherlessPersonaDecisionEngine(client, settings.featherless_model)
```

The key is passed only into `FeatherlessJsonClient`, never stored in the scenario, transcript, sidecar, progress store, exception, stdout, template, or browser response.

- [ ] **Step 5: Start the local viewer before the generation worker and pace presentation only**

```python
def run_synthetic_watch(options: SyntheticWatchOptions) -> int:
    scenario = default_synthetic_scenario(seed=options.seed)
    store = SyntheticProgressStore(initial_progress(scenario, options.mode))
    observer = RepositoryProgressObserver(
        store,
        step_delay_seconds=options.step_delay_ms / 1000,
    )
    app = create_synthetic_viewer_app(store, Path(options.output))

    worker = threading.Thread(
        target=_run_generation_safely,
        kwargs={
            "scenario": scenario,
            "output_path": Path(options.output),
            "run_root": Path(options.run_root),
            "decision_engine": _decision_engine(options.agent_mode),
            "max_decision_workers": options.max_decision_workers,
            "progress_observer": observer,
        },
        name="humanwire-synthetic-generation",
        daemon=False,
    )
    worker.start()
    print(f"viewer_url=http://127.0.0.1:{options.port}")
    try:
        uvicorn.run(app, host="127.0.0.1", port=options.port, log_level="warning")
    finally:
        worker.join()
    return 0
```

`_run_generation_safely` publishes `FAILED` with one fixed safe runtime status and no exception text. `step_delay_seconds` sleeps only after a new persisted event snapshot is published; it does not change virtual timestamps, transcript ordering, or workflow state. The non-daemon worker plus `finally: worker.join()` ensures closing the viewer cannot terminate generation midway.

- [ ] **Step 6: Verify old CLI/smoke output and the new watch boundary**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_synthetic.py tests\humanwire\test_synthetic_viewer.py -k "cli or watch or run_root or network" -v
$script = .\.venv\Scripts\python.exe scripts\smoke_humanwire.py 2>$null
$module = .\.venv\Scripts\python.exe -m humanwire smoke 2>$null
if (($script -join "`n") -ne ($module -join "`n") -or $script.Count -ne 11) { throw "smoke output changed" }
```

Expected: existing generate/replay safe summaries remain compatible, both smoke entrypoints remain byte-identical eleven-line output, and watch never binds publicly.

- [ ] **Step 7: Commit the CLI orchestration**

```powershell
.\.venv\Scripts\ruff.exe check src\humanwire\__main__.py src\humanwire\synthetic_watch.py scripts\synthetic_humanwire.py tests\humanwire\test_synthetic.py tests\humanwire\test_synthetic_viewer.py
git diff --check
git add src/humanwire/__main__.py src/humanwire/synthetic_watch.py scripts/synthetic_humanwire.py tests/humanwire/test_synthetic.py tests/humanwire/test_synthetic_viewer.py
git commit -m "feat: watch HumanWire persona simulations locally"
```

---

### Task 7: Operator guide, browser verification, privacy gates, and final acceptance

**Files:**
- Create: `docs/synthetic-agent-runtime.md`
- Modify: `README.md`
- Modify: `docs/demo-script.md`
- Modify: `submission/verified-claims.md`
- Test: `tests/humanwire/test_cutover.py`

**Interfaces:**
- Documents: deterministic watch, explicit Featherless watch, frozen replay/download workflow, six exact labels, local-only limits, and private/live proof separation.
- Preserves: public demo deployment, private Supabase/Caspian configuration, official claim ledger boundaries, and existing smoke contract.

- [ ] **Step 1: Write documentation-contract RED tests**

```python
def test_agent_runtime_docs_preserve_exact_proof_boundary() -> None:
    text = Path("docs/synthetic-agent-runtime.md").read_text(encoding="utf-8")
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
```

Add assertions that README contains both exact watch commands and that `submission/verified-claims.md` classifies the new viewer as local synthetic proof, not live-provider proof.

- [ ] **Step 2: Run the docs contract and record RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_cutover.py -k "agent_runtime" -v
```

Expected: FAIL because `docs/synthetic-agent-runtime.md` and the exact commands/boundary copy do not exist.

- [ ] **Step 3: Write the exact operator commands and evidence rules**

Include these commands verbatim:

```powershell
# Deterministic, no external model/provider call
.\.venv\Scripts\python.exe -m humanwire synthetic watch `
  --agent-mode deterministic `
  --seed 8842 `
  --run-root work\synthetic-watch-8842 `
  --output work\synthetic-watch-8842\transcript.json

# Explicit private exploratory Featherless mode; reads only configured Featherless settings
.\.venv\Scripts\python.exe -m humanwire synthetic watch `
  --agent-mode featherless `
  --seed 8842 `
  --run-root work\synthetic-model-8842 `
  --output work\synthetic-model-8842\transcript.json
```

State that each run root must not exist, the viewer is `http://127.0.0.1:8766`, stopping the viewer does not mutate persisted workflow state, JSON/CSV activate only after completion, model-assisted output must pass transcript validation/privacy/replay before freezing, and PydanticAI was not added because the existing adapter passed strict tests.

- [ ] **Step 4: Run deterministic local viewer browser QA**

Use the `build-web-apps:frontend-testing-debugging` skill and the in-app browser only. Start deterministic watch with a new ignored run root and inspect:

1. `1280x720`: Follow Live advances through persisted events, exactly one persona/origin highlight exists, `From -> To -> Generated` matches the selected hidden event, controls have visible focus, and downloads are disabled while running.
2. `600x900`: cards and provenance/replay sections stack with no page-level horizontal overflow, no clipped meaningful label, text at least 14 px, and controls at least 44 by 44 px.
3. `390x844`: navigation and replay controls remain reachable, the replay strip reflows, and JSON/CSV downloads remain usable.
4. Reduced motion: manual Previous/Next works, Play does not auto-animate.
5. Hidden page: active Play pauses on `visibilitychange`.
6. Completion: terminal states show `meeting_ready,partial`; JSON and CSV responses have attachment headers; clicking JSON downloads instead of navigating to raw JSON.

Save screenshots only under an ignored `work/` run directory. Do not commit generated database, transcript, screenshots, model sidecar, or private browser artifacts.

- [ ] **Step 5: Run one explicit model-assisted exploratory check only when the configured key exists**

First inspect only whether `Settings().featherless_api_key is not None`; never print its value. If absent, record `model-assisted runtime: PENDING — FEATHERLESS_API_KEY not configured` and continue because live Featherless proof is a non-goal. If present, use a new ignored run root, run the exact Featherless watch command, and accept its transcript only when all of these hold:

```powershell
.\.venv\Scripts\python.exe -m humanwire synthetic replay `
  --transcript work\synthetic-model-8842\transcript.json `
  --run-root work\synthetic-model-8842-replay
```

Require transcript validation, no private/credential/route/UUID leak, exactly one gateway handler, every non-silence action represented by one inbound attempt, and replay semantic hash equality. Do not replace the committed deterministic fixture with model-assisted output in this task.

- [ ] **Step 6: Run focused and broad automated gates**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_persona_runtime.py tests\humanwire\test_synthetic.py tests\humanwire\test_synthetic_progress.py tests\humanwire\test_synthetic_viewer.py -v
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_gateway.py tests\humanwire\test_workflow.py tests\humanwire\test_demo.py tests\humanwire\test_web.py -q
.\.venv\Scripts\python.exe -m pytest tests\humanwire -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
node --check src\humanwire\static\app.js
node --check src\humanwire\static\synthetic-progress.js
git diff --check
```

Expected: all tests PASS except the already documented opt-in PostgreSQL skips; the existing Starlette/httpx deprecation warning may remain.

- [ ] **Step 7: Run final scope and privacy scans**

```powershell
rg -n "PRIVATE-PERSONA-SENTINEL|FEATHERLESS_API_KEY=|CASPIAN_API_KEY=|TELEGRAM_BOT_TOKEN=|postgres(ql)?://[^ ]+:[^ ]+@" src tests docs README.md submission tests\fixtures\humanwire\synthetic_launch_v1.json
rg -n "proof_class=synthetic_multi_persona|actor_type=simulated_persona|identity_source=synthetic_fixture|transport=fake_caspian|human_attested=false|live_provider_verified=false" docs\synthetic-agent-runtime.md README.md submission\verified-claims.md
git status --short
git diff --stat
```

Expected: no secret/private sentinel in committed artifacts; all six labels appear in the guide/claim boundary; changed files are only those named in this plan; `.superpowers/brainstorm/` remains untouched.

- [ ] **Step 8: Commit documentation and acceptance tests**

```powershell
git add README.md docs/synthetic-agent-runtime.md docs/demo-script.md submission/verified-claims.md tests/humanwire/test_cutover.py
git diff --cached --check
git commit -m "docs: publish HumanWire agent simulation guide"
```

- [ ] **Step 9: Request a fresh independent review before any deployment or fixture promotion**

The reviewer must inspect the complete diff from the pre-plan base through Task 7, rerun only focused adversarial checks, and return Critical/Important findings for: persona isolation, forged authority, concurrency ordering, replay no-model behavior, snapshot privacy, loopback/GET-only enforcement, download attachment semantics, public/private separation, and the six exact provenance labels. Resolve every Critical/Important finding with a new RED, minimal fix, fresh gates, and a separate commit. Do not deploy as part of this plan.
