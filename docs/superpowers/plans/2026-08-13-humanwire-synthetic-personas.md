# HumanWire Synthetic Persona Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic isolated multi-persona generation and frozen replay through the existing injected Caspian gateway, workflow, SQLite repository, and public projections.

**Architecture:** Extract the fake transport into a reusable offline adapter. Build a standalone simulation module with strict transcript models, independent deterministic persona policies, a virtual clock, frozen replay, semantic trace normalization, and explicit non-live provenance.

**Tech Stack:** Python 3.12, Pydantic, SQLAlchemy/SQLite, Caspian gateway injection, pytest.

## Global Constraints

- No production HTTP mutation mode and no direct repository outcome mutation.
- No `.env`, ambient configuration, real directory, provider/model client, DNS, socket, deployed URL, or real contact.
- Every inbound action passes through the one Caspian gateway handler.
- Personas cannot control envelope identity, tokens, routes, conversations, or expected outcome.
- Replay never invokes persona generation.
- Every artifact declares synthetic/non-live provenance.
- Private fixture content appears in published trace only as a digest.

---

### Task 1: Reusable offline Caspian adapter

**Files:**
- Create: `src/humanwire/offline_caspian.py`
- Modify: `src/humanwire/smoke.py`
- Test: `tests/humanwire/test_caspian_gateway.py`
- Test: `tests/humanwire/test_demo.py`

**Interfaces:**
- Produces: `OfflineCaspianClient`, safe captured deliveries, `emit_inbound(...)`, and configured provider failure.
- Preserves: exact eleven-line smoke output and gateway behavior.

- [ ] **Step 1: Write failing extraction-contract tests for one handler, two channel envelopes, reply/initiate/send capture, and provider failure**
- [ ] **Step 2: Run `python -m pytest tests/humanwire/test_caspian_gateway.py tests/humanwire/test_demo.py -k "offline_caspian or adaptive_product_flow" -v` and observe missing-module RED**
- [ ] **Step 3: Extract only fake transport/envelope mechanics; keep smoke scenario logic in `smoke.py`**
- [ ] **Step 4: Run the focused suites and both smoke entrypoints; require unchanged output**
- [ ] **Step 5: Commit as `refactor: share offline Caspian proof adapter`**

### Task 2: Transcript schema and validation

**Files:**
- Create: `src/humanwire/synthetic.py`
- Create: `tests/humanwire/test_synthetic.py`

**Interfaces:**
- Produces strict `SyntheticScenario`, `SyntheticPersona`, `SyntheticAction`, `SyntheticTranscript`, and `SyntheticProvenance` models plus transcript load/validation functions.

- [ ] **Step 1: Write failing tests for unsupported version, duplicate action, unknown persona/channel, non-monotonic time, missing/mismatched trigger, extra fields, oversized content, real domains, and absent provenance**
- [ ] **Step 2: Run `python -m pytest tests/humanwire/test_synthetic.py -k "schema or tamper or provenance" -v` and observe RED**
- [ ] **Step 3: Implement strict `extra=forbid` models, ASCII stable IDs, bounded strings, explicit intents, `.example.test` identities, ordered actions, and SHA-256 transcript digest**
- [ ] **Step 4: Run GREEN and commit as `feat: validate synthetic HumanWire transcripts`**

### Task 3: Independent deterministic persona generation

**Files:**
- Modify: `src/humanwire/synthetic.py`
- Test: `tests/humanwire/test_synthetic.py`

**Interfaces:**
- Produces: `generate_scenario(scenario, output_path, run_root) -> SyntheticRunResult`.

- [ ] **Step 1: Write failing tests for six distinct policies, own-inbox-only context, identical fresh-generation transcripts, deterministic `(timestamp, persona_id, local_sequence)` ordering, and no expected-state access**
- [ ] **Step 2: Run the focused tests and observe RED**
- [ ] **Step 3: Implement fixed-clock personas and queue using `_env_file=None`, fresh file SQLite, synthetic directory, deterministic adapters, and `OfflineCaspianClient`; centrally translate allowed intents into existing wire commands**
- [ ] **Step 4: Treat timeout/invalid persona output as synthetic silence/error, run GREEN, and commit as `feat: generate isolated HumanWire personas`**

### Task 4: Frozen replay and semantic trace

**Files:**
- Modify: `src/humanwire/synthetic.py`
- Create: `tests/fixtures/humanwire/synthetic_launch_v1.json`
- Test: `tests/humanwire/test_synthetic.py`

**Interfaces:**
- Produces: `replay_transcript(path, run_root) -> SyntheticRunResult` and `semantic_trace_hash(result) -> str`.

- [ ] **Step 1: Write failing tests for generate/replay hash equality, UUID/temp-path independence, no policy calls in replay, restart equality, material-change sensitivity, duplicate inbound visibility, and ambiguous ID failure**
- [ ] **Step 2: Run focused tests and observe RED**
- [ ] **Step 3: Normalize mandate→token, assignment→token/person, interview→assignment, evidence→assignment/source/ordinal, proposal→token/round, meeting→token, and outbox→assignment/attempt/route; hash stable sorted compact UTF-8 JSON**
- [ ] **Step 4: Generate, review, and freeze the safe transcript fixture; rerun GREEN**
- [ ] **Step 5: Commit as `feat: replay synthetic HumanWire personas`**

### Task 5: CLI, documentation, isolation, and final gates

**Files:**
- Create: `scripts/synthetic_humanwire.py`
- Modify: `README.md`
- Modify: `docs/demo-script.md`
- Modify: `submission/verified-claims.md`
- Test: `tests/humanwire/test_synthetic.py`

- [ ] **Step 1: Write failing tests for explicit output/run-root paths, no ambient config, no writes outside run root, no socket/provider/model calls, exact provenance, private-text exclusion, and non-zero tamper exit**
- [ ] **Step 2: Implement a thin installed-module wrapper that prints only safe identifiers, proof labels, counts, terminal state, and trace hash**
- [ ] **Step 3: Run focused simulation, gateway/workflow/demo, both smoke entrypoints, HumanWire, full repository, Ruff, and diff gates**
- [ ] **Step 4: Commit as `docs: publish synthetic HumanWire proof`**
