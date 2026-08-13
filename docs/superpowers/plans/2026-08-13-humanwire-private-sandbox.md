# HumanWire Private Sandbox Readiness Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare HumanWire for a private managed-PostgreSQL and Caspian email/Telegram sandbox without changing the public demo or claiming external proof prematurely.

**Architecture:** Add a PostgreSQL driver and Alembic migrations, make constraints portable, verify transactional invariants against an explicitly supplied disposable database, and provide privacy-safe readiness tooling. External resources remain an operator step.

**Tech Stack:** Python 3.12, SQLAlchemy 2, PostgreSQL, psycopg 3, Alembic, pytest, Caspian SDK.

## Global Constraints

- Public Vercel remains deterministic, synthetic, GET-only, and disconnected from the private database.
- No database/provider/bot/email connection is created without private credentials and operator authorization.
- Tests/output never print database URLs, hostnames, usernames, passwords, addresses, conversations, provider bodies, or private answers.
- Hosted proof uses a distinct project, directory, database, and operator-owned test identities.
- One live listener owns a provider stream/database during proof.
- Set `ENGAGEMENT_REQUIRE_GO=true` in the sandbox.
- Three completed flows are required before `live_provider_verified=true`.

---

### Task 1: PostgreSQL portability and migrations

**Files:**
- Modify: `pyproject.toml`
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `migrations/versions/0001_humanwire_schema.py`
- Modify: `src/humanwire/database.py`
- Create: `tests/humanwire/test_postgres_contract.py`

**Interfaces:**
- Consumes: SQLAlchemy `Base.metadata` and every current record/index.
- Produces: a reviewed migration path and dialect-portable schema.

- [ ] **Step 1: Write failing migration/DDL parity tests for every table, column, foreign key, unique constraint, index, and active-interview uniqueness**
- [ ] **Step 2: Run `python -m pytest tests/humanwire/test_postgres_contract.py -k "migration or ddl" -v` and observe missing-migration RED**
- [ ] **Step 3: Add `psycopg[binary]`, Alembic, and a hand-reviewed initial migration; preserve SQLite behavior and add equivalent PostgreSQL partial uniqueness**
- [ ] **Step 4: Run contract, database, and repository GREEN suites**
- [ ] **Step 5: Commit as `feat: migrate HumanWire with PostgreSQL`**

### Task 2: Disposable PostgreSQL transaction gate

**Files:**
- Modify: `tests/humanwire/test_postgres_contract.py`
- Modify only focused repository/workflow tests needed for explicit URL parameterization.

**Interfaces:**
- Consumes: explicit `HUMANWIRE_TEST_POSTGRES_URL`.
- Produces: an opt-in dialect integration gate; absent URL skips without a connection attempt.

- [ ] **Step 1: Add integration tests for upgrade, release CAS, outbox lease/fence, callbacks, confirmation, synthesis, proposal, availability, and meeting races using a unique validated schema**
- [ ] **Step 2: Verify clean skips with no environment variable and no ambient URL discovery**
- [ ] **Step 3: Pause for an operator-supplied disposable URL, validate its scheme/target without echoing it, run the gate, and drop only the exact test schema**
- [ ] **Step 4: Commit after PostgreSQL GREEN as `test: verify HumanWire PostgreSQL transactions`**

### Task 3: Privacy-safe sandbox readiness tooling

**Files:**
- Modify: `src/humanwire/__main__.py`
- Create: `src/humanwire/sandbox.py`
- Create: `tests/humanwire/test_sandbox.py`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `submission/checklist.md`

**Interfaces:**
- Produces: `humanwire sandbox check` and `humanwire sandbox checklist`; both are read-only and make no provider connection.

- [ ] **Step 1: Write failing no-side-effect tests that report secrets only by variable name, validate PostgreSQL/migration and route counts safely, name prerequisites, and prohibit network/provider/repository writes**
- [ ] **Step 2: Implement `PASS`, `FAIL`, or `PENDING` output plus safe requirement names**
- [ ] **Step 3: Run focused tests, both commands, privacy scans, and complete regression gates**
- [ ] **Step 4: Commit as `feat: prepare private HumanWire sandbox`**

### Task 4: Operator-owned external setup and proof

**Files:**
- Use ignored local evidence only; commit no credential, database artifact, provider export, or private screenshot.

- [ ] **Step 1: Obtain a disposable managed PostgreSQL URL, Caspian key/project, email connection, Telegram bot, and exact operator-owned test identities; Featherless stays optional**
- [ ] **Step 2: Create isolated resources with an ignored directory, separate analytics token, explicit GO, and one listener**
- [ ] **Step 3: Run migrations and require live/ready health, ready channels, and fresh listener heartbeat**
- [ ] **Step 4: Execute three flows covering INFORM, ACK, QUICK+CONFIRM, email structured interview continued on Telegram+CONFIRM, explicit approval, alternate progression, bounded proposal/scheduling, and matching read-only projections without database edits**
- [ ] **Step 5: Retain only safe token aliases, timestamps, redacted screenshots, aggregate counts, trace hashes, and outcomes**
- [ ] **Step 6: Update claims only after all three flows pass; do not claim production readiness, scalability, or real organizational authority**
