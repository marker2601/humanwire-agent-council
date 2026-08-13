# HumanWire Devpost Packet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the three differentiated narratives into materially complete, truthful local Devpost draft packets and one shared release asset manifest.

**Architecture:** Maintain one citation-backed verified-claim inventory and three event-specific narratives. Preserve placeholders only for external URLs, official form fields, organizer eligibility evidence, uploaded media, and private live-provider evidence.

**Tech Stack:** Markdown, Git history, verified HumanWire tests/docs, official Devpost data when available.

## Global Constraints

- This plan sends nothing to Devpost.
- Official event facts/form fields remain unverified unless fetched live from Devpost.
- Solo eligibility and organizer exceptions require retained event-specific evidence.
- Synthetic/fake-provider proof is never called live provider or real-human proof.
- No private session content, credentials, routes, destinations, provider bodies, or private answers.
- Never publish `.superpowers/brainstorm/` or its token files.

---

### Task 1: Shared verified claims and assets

**Files:**
- Create: `submission/verified-claims.md`
- Create: `submission/assets.md`
- Modify: `submission/checklist.md`

**Interfaces:**
- Consumes: README, architecture, threat model, analytics guide, test names/results, Git history.
- Produces: a claim ledger and exact missing-asset list for all packets.

- [ ] **Step 1: Create the claim ledger**

For each claim, record public wording, supporting file/test, proof class, and prohibited stronger wording. Include six contracts, one handler/two channels, preview/release, evidence confirmation, explicit approval, two-round cap, meeting proof, replay/restart, exports, privacy, Codex-assisted TDD, and provider/SQLite limitations.

- [ ] **Step 2: Create the asset manifest**

List required desktop/mobile screenshots, 75–90 second master video, public repository/demo/video URLs, event eligibility confirmation, registration proof, and final receipts. Mark each as available, recapture, entrant-provided, or external.

- [ ] **Step 3: Update the release checklist**

Add separate eligibility, registration, reuse, deadline, and media rows for all three events, plus the `.superpowers/brainstorm/` publication prohibition.

- [ ] **Step 4: Verify and commit**

Run `rg -n "SecondSignal|live human|live provider verified|PLACEHOLDER|FIXME" submission`, inspect every hit, then commit as `docs: inventory HumanWire submission evidence`.

### Task 2: Complete the Caspian packet

**Files:**
- Modify: `submission/caspian.md`

- [ ] **Step 1: Add title, one-line summary, inspiration, why it matters, how Codex was used, verified features, architecture, setup/testing instructions, screenshot list, video beats, URL/form placeholders, limitations, and readiness notes**
- [ ] **Step 2: Keep single-handler email/Telegram, exact correlation, failure ladder, outbox, and restart proof central; keep real provider proof explicitly pending**
- [ ] **Step 3: Cross-check against `submission/verified-claims.md` and commit as `docs: complete Caspian HumanWire packet`**

### Task 3: Complete the ML Empowerment packet

**Files:**
- Modify: `submission/ml-empowerment.md`

- [ ] **Step 1: Add title, one-line summary, human-empowerment framing, advisory Featherless jobs, deterministic safeguards, confirmation provenance, Codex process, testing, assets, URL/form placeholders, limitations, and readiness**
- [ ] **Step 2: Keep organizer eligibility as external evidence and describe Featherless as implemented rather than live-recorded until verified**
- [ ] **Step 3: Cross-check and commit as `docs: complete ML Empowerment HumanWire packet`**

### Task 4: Complete the Build Beyond packet

**Files:**
- Modify: `submission/build-beyond.md`

- [ ] **Step 1: Add title, one-line summary, persistent mandate-to-meeting story, replay/export surfaces, Codex process, testing, screenshots/video, URL/form placeholders, limitations, and readiness**
- [ ] **Step 2: Keep required CHANGE partial, proposals capped at two, meeting overlap exact, and public surfaces read-only**
- [ ] **Step 3: Cross-check and commit as `docs: complete Build Beyond HumanWire packet`**

### Task 5: Packet verification

**Files:**
- Modify only to repair factual or copy defects.

- [ ] **Step 1: Map every technical statement to the verified claim ledger**
- [ ] **Step 2: Map every missing external item to the asset manifest/checklist**
- [ ] **Step 3: Scan tracked/staged files for `.env`, key/token patterns, addresses, conversation IDs, credential-bearing database URLs, `SecondSignal`, and `.superpowers/brainstorm`**
- [ ] **Step 4: Confirm the only blanks are repository/demo/video URLs, official form fields, registration/eligibility evidence, and private live-proof evidence**
