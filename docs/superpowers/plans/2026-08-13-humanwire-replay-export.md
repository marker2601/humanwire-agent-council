# HumanWire Replay and Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Reach explain each persisted event as source → destination → generated safe data, and make JSON download like CSV without removing the inline API.

**Architecture:** Extend the existing privacy-safe Reach view model with allowlisted explanatory labels, render a stable causal flow strip, and update it through the existing replay controller. Add a sibling `.json` attachment route over the canonical 16-field outreach projection.

**Tech Stack:** Python 3.12, FastAPI, Jinja, vanilla JavaScript, CSS, pytest, in-app browser QA.

## Global Constraints

- Public HTTP remains GET-only and synthetic/read-only.
- Labels may not include routes, addresses, conversations, provider bodies, UUIDs, raw private evidence, raw change text, or credentials.
- Replay uses only persisted safe projections and exact mandate + assignment + person bindings.
- Motion stops for reduced-motion preferences and when the page is hidden.
- The existing inline JSON API and canonical 16-field projection remain stable.
- Download failures return safe 404/503 responses without attachment headers.
- Meaningful text stays at least 14px; mobile controls stay at least 44×44px.

---

### Task 1: Replay explanatory projection

**Files:**
- Modify: `src/humanwire/web.py`
- Test: `tests/humanwire/test_web.py`

**Interfaces:**
- Consumes: `_events`, `_event_description`, `_reach_page_view`, exact saved-event identity.
- Produces: `stage_label`, `source_label`, `destination_label`, and `data_point_label` on every replay entry.

- [ ] **Step 1: Write failing projection tests**

Add repository-backed cases for mandate creation, plan preview/release, outreach, acknowledgement, answer, evidence confirmation, decision, proposal, availability, meeting, and outcome. Assert all four fields are public and non-empty. Add unknown and cross-assignment events that use neutral labels and `highlight=none`.

- [ ] **Step 2: Observe RED**

Run `python -m pytest tests/humanwire/test_web.py -k "reach_replay_explains or reach_replay_unknown" -v`. Expected: assertions fail because the explanatory keys do not exist.

- [ ] **Step 3: Implement the pure allowlisted mapper**

Map known event types to one of `Mandate`, `Plan`, `Outreach`, `Response`, `Evidence`, `Decision`, `Proposal`, `Scheduling`, or `Outcome`. Resolve people only from the exact bound row. Unsupported events return neutral `Saved event`, `HumanWire`, and `Decision Room` labels without interpolating metadata.

- [ ] **Step 4: Observe GREEN and commit**

Run the Step 2 command, then commit `src/humanwire/web.py` and `tests/humanwire/test_web.py` as `feat: explain persisted HumanWire events`.

### Task 2: Replay flow-strip interaction

**Files:**
- Modify: `src/humanwire/templates/reach.html`
- Modify: `src/humanwire/static/app.js`
- Modify: `src/humanwire/static/styles.css`
- Test: `tests/humanwire/test_web.py`

**Interfaces:**
- Consumes: Task 1 replay keys in visible fallback markup and safe `data-*` attributes.
- Produces: `[data-replay-source]`, `[data-replay-destination]`, `[data-replay-data-point]`, and accessible change announcements.

- [ ] **Step 1: Write failing template, JavaScript, and CSS tests**

Assert visible `From`, `To`, and `Generated`; safe data attributes; JavaScript updates all three on previous/next/play; exact-card highlighting remains; playback never auto-starts; hidden-page and reduced-motion guards remain; CSS supplies causal transitions and a no-motion override.

- [ ] **Step 2: Observe RED**

Run `python -m pytest tests/humanwire/test_web.py -k "replay_flow or replay_motion" -v` and require missing-hook failures.

- [ ] **Step 3: Implement static-first markup and controller updates**

Render event one as useful non-JavaScript content. On selection, copy only from the chosen replay node into the flow strip and live region. Use opacity/translate changes to explain causality without implying live network traffic.

- [ ] **Step 4: Add responsive and reduced-motion styles**

Use a three-part desktop strip and stacked mobile strip below 759px. Disable transitions inside `prefers-reduced-motion: reduce` while preserving all values.

- [ ] **Step 5: Observe GREEN and commit**

Run the focused command and `python -m pytest tests/humanwire/test_web.py -q`, then commit the four changed files as `feat: animate HumanWire replay provenance`.

### Task 3: JSON attachment download

**Files:**
- Modify: `src/humanwire/web.py`
- Modify: `src/humanwire/templates/data.html`
- Modify: `docs/analytics.md`
- Test: `tests/humanwire/test_web.py`

**Interfaces:**
- Consumes: `_outreach_rows`, current filters/authentication, deny corpus, and `safe_projection`.
- Produces: `GET /api/v1/mandates/{token}/outreach-events.json` as an attachment; the current JSON route remains inline.

- [ ] **Step 1: Write failing download tests**

Cover filtered parity with inline JSON and CSV, `application/json`, safe attachment filename, stable UTF-8 indented content with trailing newline, query preservation, production auth, bodyless 404, late database 503, unsafe-token filename fallback, and private-sentinel exclusion.

- [ ] **Step 2: Observe RED**

Run `python -m pytest tests/humanwire/test_web.py -k "json_download" -v`. Expected: `.json` route is 404 and the Data page still exposes an inline-only JSON control.

- [ ] **Step 3: Implement serialization, filename, route, and UI**

Add `_outreach_json(rows) -> bytes` using `json.dumps(rows, ensure_ascii=False, indent=2) + "\n"`. Add a deny-corpus-safe `.json` filename. Compute rows and filename in one guarded projection. Change the Data-page control to `Download JSON`; document the inline API separately.

- [ ] **Step 4: Observe GREEN and commit**

Run the focused and full web suites, then commit the four files as `feat: download HumanWire analytics as JSON`.

### Task 4: Browser and complete verification

**Files:**
- Modify only if an observed browser defect has an exact failing regression first.

- [ ] **Step 1: Start the deterministic local demo without ambient secrets**
- [ ] **Step 2: At 1280×720, exercise filters, all person selections, previous/next, play/pause, visibility pause, and JSON/CSV downloads**
- [ ] **Step 3: At 600×900 and 390×844, verify stacked flow, 44px controls, ≥14px text, keyboard focus, no page overflow/clipping, and clean console**
- [ ] **Step 4: Emulate reduced motion and confirm manual controls preserve all information while playback refuses to start**
- [ ] **Step 5: Run final gates**

```powershell
python -m pytest tests/humanwire/test_web.py tests/humanwire/test_demo.py -q
python -m pytest tests/humanwire -q
python -m pytest -q
python -m ruff check src tests scripts
git diff --check
```

- [ ] **Step 6: Commit only browser fixes proven by RED→GREEN tests**
