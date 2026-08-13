# Task 9 — Adaptive propagation lanes report

Date: 2026-08-12

Branch: `codex/humanwire`

Clean base: `769557518965287e0d5f57aebe418588b28cbb8d`

Required commit message: `feat: visualize adaptive propagation lanes`

## Outcome

Implemented the approved read-only Reach view at `/mandates/{token}/reach`. It renders three direction-based propagation lanes, deterministic first-contact sequence, truthful adaptive engagement progress for every persisted stakeholder, one selected person's exact saved history, and a read-only replay of all saved events.

The view is built from existing public projections only and crosses the final recursive `safe_projection` boundary immediately before Jinja rendering. No raw domain object, event metadata, destination, private evidence, provider payload, internal identifier, organization chart, mutation surface, or client-side JSON state was added.

## TDD evidence

- Exact RED command: `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -k "reach or lane or replay" -v`
- Authoritative RED result before production: 32 selected, 109 deselected; 26 failed and 6 passed. Failures were the expected absent Reach route/template/projection, direction lanes, deterministic sequence, engagement/result mappings, selection/filter state, actual saved replay, privacy boundary, and responsive hooks.
- Exact focused GREEN command: `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -k "reach or lane or replay" -v`
- Focused GREEN: 32 passed, 109 deselected.
- First full functional GREEN: `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -v` → 141 passed before the browser regression was added.
- Browser accessibility RED: `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -k "reach_mobile_navigation_toggle" -v` → 1 failed, 141 deselected; the exact 390×844 browser pass measured the navigation toggle at 38×38px.
- Browser accessibility GREEN after the 44×44px fix: the same command → 1 passed, 141 deselected.

## Changed files and decisions

- `src/humanwire/web.py`: Reach projection, safe direction grouping, global ordering by `(first_contact_at, plan_order, person_id)`, six adaptive engagement/result semantics, exact-person history, stable actual-event replay, safe URL-state parsing, and recursively sanitized template render.
- `src/humanwire/templates/reach.html`: semantic origin, controls, three ordered lane regions, engagement cards, selected ladder/history, 16-event replay, accessible SVG controls, and the existing live GET-only footer.
- `src/humanwire/static/styles.css`: locked navy/ice/cyan visual language, three-column desktop lanes, stacked mobile lanes, 14px meaningful-text floor, 44px controls, visible focus, status borders, selection/replay glow, and reduced-motion behavior.
- `src/humanwire/static/app.js`: deterministic local filters and selection, safe `URLSearchParams`/`replaceState` state, previous/play/next replay over the rendered saved events, hidden-tab pause, reduced-motion manual fallback, and no write request.
- `tests/humanwire/test_web.py`: route/shell/CSP, safe origin, direction grouping, deterministic order/ties, all six semantics, progress/channel/result truth, selection/filter/URL behavior, exact persisted history/replay, XSS/privacy, empty lanes, read-only proof, responsive CSS, and touch target coverage.

No demo fixture, database, schema, domain model, Task 10, Task 11, deployment, or review code was changed.

## Projection and behavior proof

- Gather input includes Downward assignments and truthfully labels any External assignment as External; Coordinate policy contains Lateral; Get approval contains Upward. Empty lanes retain explicit safe empty states.
- Sequence is global rather than per-lane: dated first contacts sort first; exact timestamp ties retain persisted plan order; missing contacts sort last; no person is duplicated or dropped.
- INFORM, ACK, QUICK, STRUCTURED, REVIEW, and AVAILABILITY each have explicit allowlisted result/status/progress semantics. Delivery failures, unreachable, declined, rejected, pending, awaiting, complete, delivered, acknowledged, approved, and recorded states do not collapse into a generic success claim.
- Channel display comes from the active persisted safe route, including Priya Shah's Telegram alternate; dates, direction, role, department, engagement label, progress, and technical GET links remain public-projection fields only.
- Default selection is deterministic, URL selection requires exactly one matching visible person, and duplicate/invalid `status` or `person_id` input fails closed to All plus the deterministic default. Filters preserve global sequence and move selection to the first visible row only when necessary.
- Replay contains all 16 actual persisted events in saved order with allowlisted description, safe time/context, and exact-person/origin highlight. Previous/next are manual and Play advances every two seconds, stops at the end, pauses when hidden, and does not autoplay under reduced motion.
- Browser actions and route inspection confirmed GET-only behavior; event records were unchanged after replay and filtering.

## Browser and visual verification

Method: Browser/IAB first against the loopback-only deterministic app at `http://127.0.0.1:8765/mandates/HW-2411/reach`.

Locked references inspected at original detail:

- `.superpowers/sdd/2026-08-12-humanwire-adaptive-engagement/design/task-9-reach-desktop.png`
- `.superpowers/sdd/2026-08-12-humanwire-adaptive-engagement/design/task-9-reach-mobile.png`

Final browser renders captured and inspected in the same pass:

- `.superpowers/sdd/2026-08-12-humanwire-adaptive-engagement/design/task-9-implementation-desktop.png`
- `.superpowers/sdd/2026-08-12-humanwire-adaptive-engagement/design/task-9-implementation-mobile.png`

Exact browser viewports were 1280×720, 600×900, and 390×844. The browser's scrollbar gutter makes the corresponding content widths 1265, 585, and 375px; at each size `scrollWidth == clientWidth`, so no horizontal page overflow exists. Computed-style audits found zero visible own-text elements below 14px, no clipped Reach labels in the accepted layouts, and no localhost console warning/error.

IAB's experimental full-page mobile capture duplicated/clipped its fixed-shell raster, so the accepted artifacts are exact viewport captures. Lower lane, selected-history, replay, controls, and footer regions were separately inspected by scrolling. This was a capture-tool limitation, not an application rendering defect.

### Fidelity ledger

| Comparison point | Result |
| --- | --- |
| Shell and origin | Pass. HumanWire navigation, fixture notice, objective, initiator, mandate, state, and safe start preserve the locked hierarchy and palette. |
| Direction lanes | Pass. Desktop has three equal Gather/Coordinate/Approval siblings; mobile stacks them in the same semantic order. |
| Cards and state | Pass. Cyan selection, green complete/delivered/acknowledged, amber pending, progress rails, sequence circles, first/last contact, and technical links match the visual system. |
| Adaptive truth | Pass. All six persisted people and the full structured ladder are shown; no reference placeholder or invented milestone replaces saved state. |
| Controls | Pass. All/In progress/Completed/Pending, persisted-event count, replay jump, previous/play/next, pressed state, focus ring, and 44px touch targets are legible. |
| Replay | Intentional truth-safe expansion. The locked concept sketches six person milestones; the implementation retains all 16 saved events. At 1280×720 the replay begins just below the fold and is available through the prominent jump control/scroll, while mobile presents the saved list and controls in a dedicated lower region. |
| Responsive behavior | Pass. 600px and 390px layouts recompose without fixed-width tables, one-character wrapping, clipped labels, horizontal scroll, or sub-14px text. |
| Footer/motion | Pass. Sticky live refresh footer remains present; replay and smooth scrolling honor visibility and reduced motion. |

### Interaction proof

- All six stakeholder buttons were selected at 600px. Each produced exactly one pressed row, its matching visible saved history, and one canonical `person_id` URL value.
- All four filters were exercised. Visible sets were All = 6, In progress = Priya, Completed = Eli/Sora/Inez/Nora, and Pending = Maya; selection fallback remained deterministic.
- Duplicate and invalid query values restored All plus Priya without exposing or echoing unsafe input.
- Previous/next advanced one actual event; Play advanced at the two-second cadence and Pause stopped it. Tab focus displayed a 2px solid focus outline. Manual controls provide the reduced-motion fallback.
- Technical rows navigated to `/mandates/HW-2411/data?person_id=priya-shah`; Mandates, Reach, and Data navigation paths were exercised.
- Mobile lower-page inspection confirmed Approval, the complete 16-event list, current-event copy, three replay controls, and live footer remain readable.

## Material browser findings and fixes

- Initial desktop cards allowed narrow fact columns to wrap into single-character fragments. The card identity/fact composition was widened and rebalanced, then rechecked at all three viewports with zero clipped accepted-layout labels.
- Exact 390×844 inspection found the inherited navigation toggle at 38×38px. A failing regression test reproduced the defect before the fix; it now measures 44×44px.
- A denser experimental desktop row was evaluated against the locked reference but caused label collisions. It was rejected and removed; the accepted render keeps readable truth and a below-fold replay instead of compressing or omitting saved information.

## Fresh final gates

- `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -v` → 142 passed, 1 pre-existing Starlette/httpx deprecation warning.
- `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_demo.py tests\humanwire\test_web.py -v` → 146 passed, 1 pre-existing warning.
- `.\.venv\Scripts\python.exe -m pytest tests\humanwire -q` → 1026 passed, 1 pre-existing warning.
- `.\.venv\Scripts\python.exe -m pytest -q` → 1116 passed, 1 pre-existing warning.
- `.\.venv\Scripts\python.exe -m ruff check src\humanwire\web.py tests\humanwire\test_web.py` → all checks passed.
- `git diff --check` → passed.
- `git diff --cached --check` → run and recorded after staging.

## Completion identity

Exactly one task commit is created after this report and all gates are final. A Git commit cannot contain its own SHA without changing that SHA, so the exact final commit SHA and clean `git status --short` result are recorded in the final handoff immediately after commit creation.

## Review round 1 — 2026-08-12

Addressed the official Important event-identity finding without changing visible Reach composition, Task 10, Task 11, deployment, or review scope.

### Root cause and fix

The repository persists `DomainEvent.assignment_id`, but the public `_events` projection dropped it before Reach construction. Reach consequently matched history/highlights on `person_id` alone and treated any event without a person as a mandate-origin event. A malformed Eli-assignment/Priya-person event could therefore appear in Priya's history and highlight Priya; an assignment-bound event missing its person could highlight origin.

Reach now carries `(mandate_id, assignment_id, person_id)` only through an internal construction path. A person event binds only when all three values match exactly one rendered assignment and both the rendered person and assignment identities are unique. Cross-assignment, missing-person, missing-assignment, wrong-mandate, duplicate-person, duplicate-assignment, and unbound engagement events remain in the ordered saved replay but target `none` and enter no person's history. Only an allowlisted mandate-level event with both assignment and person absent and the exact current mandate targets origin. The internal identifiers are dropped before the final recursive public projection and never appear in Jinja context, DOM, replay JSON, or the existing public API/data views.

The same exact binding now chooses the latest saved channel used by stakeholder projection; a cross-assignment Telegram event cannot change Maya's truthful Email channel. No missing assignment is inferred from a person ID, including legacy records.

The deterministic demo fixture now persists the exact assignment ID on every person event, including the secondary meeting-package person event. Mandate-level demo events retain neither person nor assignment identity.

### Strict TDD evidence

- Demo RED: `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_demo.py -k "event" -v` → 1 failed, 4 deselected. The person-bound HW-2413 meeting-package event had no assignment identity.
- Reach RED: `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -k "reach_binds_history or reach_duplicate_rendered or reach_origin_wrong" -v` → 4 failed, 142 deselected. The identity-aware projection/binding contract did not exist.
- Focused GREEN: the same Reach command → 4 passed, 142 deselected; demo identity slice → 1 passed, 4 deselected.
- Broader Reach GREEN: `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -k "reach or lane or replay" -v` → 37 passed, 109 deselected.

Repository-backed tests cover the reviewer probe, cross-assignment channel poisoning, assignment-without-person, person-without-assignment, engagement event with neither identity, duplicate persisted person, duplicate rendered assignment, wrong mandate, exact origin allowlist, internal identifier non-exposure, exact actual event order/count, and selected-history/replay targets. Demo tests assert every person event has its exact assignment pair and mandate-level events have neither.

### Review round 1 final gates

- `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_demo.py -v` → 5 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_web.py -v` → 146 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_demo.py tests\humanwire\test_repository.py -v` → 94 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_repository.py -v` → 89 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\humanwire -q` → 1031 passed.
- `.\.venv\Scripts\python.exe -m pytest -q` → 1121 passed.
- `.\.venv\Scripts\python.exe -m ruff check src\humanwire\web.py src\humanwire\demo.py tests\humanwire\test_web.py tests\humanwire\test_demo.py` → all checks passed.
- `git diff --check` → passed; staged diff check is recorded at commit time.

The exact review-fix commit SHA and final clean status are recorded in the round-one handoff because a commit cannot contain its own SHA.
