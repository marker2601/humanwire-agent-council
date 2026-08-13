# Task 8 — Adaptive HumanWire Decision Room report

Date: 2026-08-12

Branch: `codex/humanwire`

Clean base: `bcad8e57ecf5cb723c0cefc8ebed7272e6065db8`

Required commit message: `feat: build the adaptive HumanWire Decision Room`

## Outcome

Implemented the approved read-only Decision Room as package-local, server-rendered Jinja templates with first-party CSS and vanilla JavaScript. The UI renders only persisted Task 7 projections, retains the existing API/auth/privacy/ICS behavior, and adds no mutating browser or HTTP surface.

The reference objective and quick-response names were layout placeholders. Per the Task 7 truth contract, the implementation intentionally displays `Prepare approved weekend launch coverage`, Eli Torres, and Sora Kim. This is the sole required content-only fidelity deviation; the reference composition and semantic roles are preserved.

## TDD evidence

Production UI was absent when the first new test slice ran.

- Exact RED command: `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -k "decision_room or dashboard or engagement_ladder" -v`
- Exact RED result: exit 1; 21 selected tests failed and 58 were deselected. Failures were the expected missing package-local templates/static assets, dashboard/Decision Room HTML, projection helper, semantic controls, responsive hooks, and read-only polling code.
- Browser-found REDs were also reproduced in tests before fixes: projection-key collisions and persisted plan order (2 failures), then ladder/current-step and mobile typography/filter-count defects (4 failures).
- Exact focused GREEN command: `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -k "decision_room or dashboard or engagement_ladder" -v`
- Focused GREEN result after the final fixes: 23 passed, 58 deselected.
- First full functional GREEN: `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -v` → 81 passed.

## Changed files and decisions

- `src/humanwire/web.py`: package-local Jinja/static wiring, dashboard and Decision Room view-model construction, Task 7 adaptive engagement labels/progress/ladders, safe timeline/Reach/evidence summaries, display formatting, and hardening headers. All template context crosses the existing final recursive public-sanitization boundary.
- `src/humanwire/templates/base.html`: semantic shell, skip link, navigation, fixture notice, and live footer.
- `src/humanwire/templates/dashboard.html`: persisted mandate dashboard and truthful GET-only state filters.
- `src/humanwire/templates/mandate.html`: semantic Decision Room regions, adaptive stakeholder table/list, selected engagement ladder, evidence/AI boundary, append-only timeline, and Reach/Data paths.
- `src/humanwire/static/styles.css`: reference-faithful navy/ice/cyan design system, code-native SVG icon treatment, visible focus, desktop grid, sub-760px single-column layout, and sub-480px row-card recomposition without a fixed table width.
- `src/humanwire/static/app.js`: local nav, stakeholder selection, client-only filters, countdown, visible-tab five-second GET polling, fail-closed pause, and no mutation requests.
- `tests/humanwire/test_web.py`: dashboard/Decision Room truth, all six engagement ladders, accessibility/semantics, CSS reflow, JavaScript GET-only polling, local assets, hardening headers, no-mutation, privacy sentinels, and exact persisted row/order/count coverage.

No Task 7 demo fixture, external asset, secret, provider, deployment, Task 9, or Task 10 code was changed.

## Browser and visual verification

Method: Browser/IAB first against the deterministic local app at `http://127.0.0.1:8765/mandates/HW-2411`, served from the workspace virtual environment by a hidden loopback-only Uvicorn process.

Locked references inspected at original detail:

- `.superpowers/sdd/2026-08-11-humanwire/design/task-12-decision-room-desktop.png`
- `.superpowers/sdd/2026-08-11-humanwire/design/task-12-decision-room-mobile.png`

Implementation captures inspected in the same QA pass:

- `.superpowers/sdd/2026-08-12-humanwire-adaptive-engagement/design/task-8-implementation-desktop.png`
- `.superpowers/sdd/2026-08-12-humanwire-adaptive-engagement/design/task-8-implementation-mobile.png`

Exact browser viewport overrides were 1536×1024 and 600×1024. A 390×844 narrow sanity viewport was also inspected. IAB's screenshot raster excludes its scrollbar/browser gutter (1521×1014 and 584×1014 respectively), while the browser viewport overrides and responsive behavior were exercised at the exact required sizes. The IAB full-page mobile capture duplicated/clipped its fixed-shell raster, so the accepted mobile artifact is the exact above-the-fold viewport; lower mobile sections were separately inspected by scrolling to the ladder, evidence/AI, timeline, Reach, Data, and footer regions.

### Fidelity ledger

| Comparison point | Result |
| --- | --- |
| Exact visible copy/data | Pass. `HW-2411`, Arun Patel, Coordinating, persisted objective, six persisted stakeholders, Eli/Sora quick responses, and Priya next action are truthful. Reference-only Daniel/Luis/Q4 placeholder copy is intentionally not used. |
| Page/grid composition | Pass. Desktop preserves the wide stakeholder/timeline column plus action/evidence rail; mobile orders lifecycle, action, stakeholders, ladder, evidence, history, Reach, and Data as the reference does. |
| Typography | Pass. Editorial system sans stack, strong title hierarchy, compact metadata, and at least 14px body/control/row text. |
| Palette/borders/glow | Pass. Deep navy shell, ice text, restrained cyan borders/current glow, green complete, amber pending, and muted secondary text; no excess glow or browser-default controls. |
| Lifecycle rail | Pass. Six steps with Received/Planned complete, Coordinating current, and remaining steps pending; horizontal desktop and vertical mobile rails. |
| Adaptive stakeholder rows | Pass. All six rows remain in persisted plan order and expose truthful INFORM/ACK/QUICK/STRUCTURED/REVIEW roles, channel/alternate state, progress, contact time, and status. Rows recompose at the narrow breakpoint without a fixed minimum width. |
| Selected ladder | Pass. Type-specific steps only, at most one current step, Priya defaults selected, and INFORM/ACK/QUICK/STRUCTURED/REVIEW selections were exercised. |
| Evidence versus AI | Pass. Human evidence is visually green and separately counted; AI is blue, explicitly `Not ready`, and shows zero assumptions/open questions without invented proof. |
| Timeline | Pass. Append-only, most-recent-first persisted events with safe person, channel, and direction labels; no raw metadata or change text. |
| Responsive/overflow | Pass. 600px and 390px states use a single-column composition and compact row grid/cards; no clipped desktop-table dependency or horizontal page scroll appeared. |
| Icons/footer | Pass. Consistent code-native line icons and restrained status symbols; sticky live footer shows five-second refresh and last-updated time. |

### Above-the-fold copy audit

The final render was checked against the brief and references for HumanWire, the fixture notice, objective, Mandate, Initiated by, State, Deadline, Progress, all six lifecycle labels, Next action, Contact Priya through registered Telegram, Due in, Why this matters, Stakeholders, All, In progress, and Completed. No material mismatch, leaked placeholder name, clipped primary label, unreadable state, or invented claim remains. At 390px the long objective wraps intentionally and legibly.

### Browser behavior proof

- Navigation: mobile hamburger opens with `aria-expanded=true`; desktop Mandates/Reach/Data navigation is visible.
- Selection: Inform, Acknowledgement, Quick response, Structured interview, and Approval review rows each produced their type-specific ladder; Priya returns as the default selected row after reload.
- Filters: All, In progress, and Completed updated only local row visibility and pressed state; counts remained 6, 1, and 4.
- Reach/Data: `Open Reach` navigated to `/mandates/HW-2411/reach`; `Open Data view` navigated to `/mandates/HW-2411/data`.
- Countdown/polling: the countdown changed once per second; server logs showed repeated successful `GET /api/v1/mandates/HW-2411` requests at the five-second cadence. Source and tests prove `visibilitychange` cancels timers while hidden and reschedules only when visible; any response/network failure halts polling without inventing state.
- Keyboard/accessibility: semantic banner/nav/main/aside/sections/table/progress/time/footer, skip link, headings and labelled controls were present; keyboard Tab focus displayed the cyan focus treatment. Reduced-motion handling is local and all informative state retains text rather than relying on color alone.
- Console: no browser console warning/error appeared during the Decision Room interactions.

## Privacy, auth, and safe failure proof

- Every Jinja context is produced from safe public projections and recursively sanitized immediately before render; no raw mandate/interview/evidence/event domain object is passed to a template.
- HTML/API/Reach/Data/ICS sentinels cover private evidence text, raw engagement change text, destinations, provider bodies, internal identifiers, meeting identifiers, tokens, and secrets.
- The browser performs GET only. No forms, hidden mutation, POST/PUT/PATCH/DELETE route, inline handler, or external asset was added.
- Existing production analytics bearer-token behavior remains fail-closed; demo analytics remains anonymous.
- Unknown mandate and calendar tokens retain bodyless 404s, including private-phrase collision cases.
- Existing verified-meeting-package/ICS integrity and privacy tests remain green.
- CSP is self-only (`script-src` and `style-src`), with `object-src 'none'`, `base-uri 'none'`, `frame-ancestors 'none'`, and `form-action 'none'`; nosniff, no-referrer, and restrictive Permissions-Policy headers are present.

## Material mismatches found and fixed

Browser QA found and drove fixes for: sanitizer key collisions that rendered `[REDACTED]`; stakeholder rows not following persisted plan order; duplicate `current` ladder steps; missing In-progress/Meeting-ready counts caused by hyphenated projection keys; cramped filter labels; and 11–12px stakeholder text. Final captures contain no `[REDACTED]`, no placeholder names/objective, and no material unresolved visual mismatch.

## Fresh final gates

- `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -v` → 81 passed, 1 pre-existing Starlette/httpx deprecation warning.
- `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_demo.py tests\humanwire\test_web.py -v` → 85 passed, 1 pre-existing warning.
- `.\.venv\Scripts\python.exe -m pytest tests\humanwire -q` → 965 passed, 1 pre-existing warning.
- `.\.venv\Scripts\python.exe -m pytest -q` → 1055 passed, 1 pre-existing warning.
- `.\.venv\Scripts\python.exe -m ruff check src\humanwire\web.py tests\humanwire\test_web.py` → all checks passed.
- `git diff --check` → passed.
- `git diff --cached --check` → run and recorded after staging.

## Completion identity

Exactly one task commit is created after this report and all gates are final. A Git commit cannot contain its own SHA without changing that SHA, so the exact final commit SHA and clean `git status --short` result are recorded in the final handoff immediately after commit creation.

## Review round 1 — 2026-08-12

Addressed all three Important findings from the first official review without expanding into Task 9, Task 10, deployment, or nested review.

### Exact RED and GREEN

- Review RED: `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -k "inform_projection or never_invents_action or renders_only_live or defaults_to_safe_priya or stylesheet_never" -v` → 34 selected, 33 failed, 1 passed, 80 deselected. Failures reproduced nonterminal INFORM delivery claims, fabricated no-action contact/countdown UI across mandate states, wrong action-person binding, action rendering outside coordinating, and 29 explicit sub-14px declarations.
- First review GREEN: the same focused slice → 34 passed.
- Browser-found reflow RED: `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -k "typography_floor_reflows" -v` → 1 failed, 114 deselected; it caught the old fixed desktop status allocation, auto-fit mobile ladder, and clipped timeline contract.
- Reflow GREEN: the same slice → 1 passed, 114 deselected.

### Correctness fixes

- INFORM progress now claims `delivered` and `1 of 1` only when the persisted assignment is `COMPLETE` with both completion and last-delivery timestamps. `CONTACT_QUEUED`, `DELIVERED` without completion proof, and invalid awaiting states remain pending at zero; `DELIVERY_FAILED` and `UNREACHABLE` retain terminal truth. The INFORM ladder uses the same persisted lifecycle and never promotes a noncomplete delivery to complete.
- Stable stakeholder selection is separate from actionability. An action renders only for an `INTERVIEWING` mandate when the persisted next-action projection resolves to exactly one active assignment with the identical person, timestamp, and safe channel. The action copy uses that assignment's person and persisted safe reason. Otherwise the page displays `No pending action` and omits contact copy, reason, deadline fallback, countdown, countdown data hook, and `Due in`.
- The action/no-action matrix covers every `MandateState`, both without a scheduled assignment action and with one injected. HW-2411's current fixture has no persisted next action, HW-2412 renders `Aligned`, and HW-2413 renders `Meeting ready`; all three truthfully show no pending action in the seeded state.
- Public enum labels replace underscores with spaces before capitalization.

### Accessibility and visual verification

- All explicit meaningful CSS text sizes are at least 14px on desktop and mobile. Live computed-style audits found zero visible own-text elements below 14px at 1536×1024, 600×1024, and 390×844.
- Browser QA found and fixed desktop status clipping, a 600px six-step ladder overlap, and ellipsized mobile history. Desktop column allocation now preserves complete status labels; the ladder reflows 3×2 at 600px and 2×3 at 390px; mobile timeline descriptions and context wrap in a two-column layout.
- Layout metrics at all three viewports showed `scrollWidth == clientWidth` (the browser scrollbar consumes the remaining viewport gutter), so there is no horizontal page overflow.
- Replacement screenshots were written to the existing ignored paths `design/task-8-implementation-desktop.png` and `design/task-8-implementation-mobile.png`. Both locked references and both replacements were inspected together with `view_image` at original detail.
- Fidelity ledger: deep-navy/ice/cyan palette, lifecycle hierarchy, desktop grid, mobile single-column order, adaptive rows, selected ladder, evidence/AI boundary, timeline, Reach/Data panels, icons, and footer remain faithful. Required deviations are persisted Task 7 copy/names, truth-safe `No pending action`, and larger typography/reflow required by the explicit accessibility constraint. No material clipping, overlap, or visual regression remains.
- Browser interaction proof: Completed hides Priya and retains Eli; All restores rows; selecting Eli renders the Quick response ladder while leaving the independent no-action panel truthful. HW-2412 and HW-2413 were directly loaded and showed their spaced state labels with no countdown.
- Browser console contained only unrelated browser-extension warnings/errors; no localhost application warning/error was emitted.

### Review round 1 fresh gates

- `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -v` → 115 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_demo.py tests\humanwire\test_web.py -v` → 119 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\humanwire -q` → 999 passed.
- `.\.venv\Scripts\python.exe -m pytest -q` → 1089 passed.
- `.\.venv\Scripts\python.exe -m ruff check src\humanwire\web.py tests\humanwire\test_web.py` → all checks passed.
- `git diff --check` → passed; staged diff check is recorded at commit time.

The exact review-fix commit SHA and final clean status are recorded in the round-one handoff because a commit cannot contain its own SHA.
