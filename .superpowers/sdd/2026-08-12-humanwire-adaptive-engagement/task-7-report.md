# Adaptive Task 7 Report: Contribution Readiness and Adaptive Demo

## Outcome

- Base: `74abda486e983f1e77370a17745da18869ce4d64`
- Branch/worktree: `codex/humanwire` at `.worktrees/humanwire`
- Commit: this report is committed with `feat: project adaptive engagement progress`;
  the exact resulting SHA is recorded in the final handoff because a Git commit cannot
  contain its own hash.
- No Task 8, deployment, external calendar mutation, or nested review was performed.

HumanWire now evaluates contribution readiness from each persisted engagement contract,
keeps decision authority local and deterministic, fences synthesis against stale aggregate
and contribution snapshots, exposes type-aware privacy-safe progress, and seeds the exact
mixed-engagement `HW-2411` demo story.

## TDD evidence

The required focused command was run after tests and before production changes:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_alignment.py tests\humanwire\test_workflow.py -k "engagement or contribution or approval" -v
```

Observed RED: exit `1` during collection with the expected missing contract:
`ImportError: cannot import name 'ContributionState' from humanwire.alignment`.
Pytest collected 234 tests and selected 77 before collection stopped. After the pure helper
implementation, the same slice first became GREEN with 102 passed and 193 deselected.

The synthesis integration RED then had eight focused failures: four strict `AlignmentEngine`
or `HybridAlignmentEngine` keyword errors and four mixed workflows remaining
`INTERVIEWING`. That tranche became GREEN at 110 passed. Projection/demo REDs were 6/6 and
2/2 respectively, and the file-backed stale synthesis RED was 3/3. Each corresponding slice
was GREEN before broader verification. Final focused result after all edge-case tests:

```text
156 passed, 197 deselected; exit 0
```

## Design, authority, and privacy decisions

- `ContributionState`, frozen `ContributionStatus`, and `contribution_status` implement a
  pure, list-order-independent evaluator for all six engagement types. Lifecycle failure,
  requiredness, exact confirmed evidence, authenticated decisions, and exact availability
  are the only readiness inputs.
- QUICK/STRUCTURED proof requires confirmed SHAREABLE or PRIVATE evidence bound to the exact
  mandate, assignment, and stakeholder. Anonymous, asserted, disputed, cross-person,
  cross-assignment, and cross-mandate facts do not count. Private facts may establish
  internal readiness but never enter model/public evidence.
- INFORM, ACKNOWLEDGE, and AVAILABILITY never cover decisions or create agreements. REVIEW
  uses exactly one local persisted decision; reject/change create deterministic hard
  constraints without using raw `change_text`. Assignment-plan contract matching includes
  person, mandate, type, direction, reason, requiredness, response contract, and uniqueness.
- Synthesis builds model/public input only from shareable evidence on valid, completed
  QUICK/STRUCTURED assignments. Deterministic issues remain authoritative; model output is
  bounded advisory data only. Partial output names required blockers and explicitly says no
  agreement or approval was inferred.
- The public projection adds exactly `engagement_type`, `response_required`,
  `engagement_status`, `progress_current`, `progress_total`, plus `phase_label` on mandate
  summaries/details. `INTERVIEWING` remains persisted while projecting as `coordinating`.
  Question progress comes from persisted sessions/plans, approvals from exact decisions, and
  availability from a fresh exact runtime record. Terminal states keep truthful partial
  progress.
- All new fields pass through the existing recursive projection allowlists. Raw decision
  `change_text` joins the private deny corpus, with all-route HTML/JSON/ICS sentinel proof.
  Existing Bearer auth, bodyless 404, route/destination secrecy, and verified ICS behavior
  remain intact.

## Race and atomicity proof

Synthesis now transitions directly from the exact `INTERVIEWING` mandate snapshot to its
final state inside one transaction. The transaction first performs full live/unexpired
mandate compare-and-save and then re-reads and compares the complete assignment, evidence,
decision, and availability snapshots used for evaluation. Any mismatch raises internally so
the whole transaction rolls back; no issue, proposal, brief, event, or delivery survives.
Proposal preparation is pure so proposal/state/issues/events share this transaction.

File-backed WAL SQLite tests prove cancellation and expiry win before synthesis without
terminal resurrection. A final authenticated approval inserted after evaluation invalidates
the snapshot, leaves the mandate `INTERVIEWING` without stale output, and a fresh retry uses
the new decision and aligns. Existing Task 6 route, callback, replay, and terminal invariants
remain covered by the complete HumanWire suite.

## Exact demo

`HW-2411` deterministically contains six assignments: completed QUICK_RESPONSE work for Eli
Torres and Sora Kim with confirmed assignment-bound evidence; Priya Shah as the sole active
three-question STRUCTURED_INTERVIEW on her saved Telegram alternate; completed ACKNOWLEDGE
for Nora Chen with no session; pending REVIEW_APPROVAL for Maya Brooks with no session or
decision; and completed optional INFORM for Inez Ward with no session, timer, reminder,
approval, or agreement evidence. Exactly three sessions exist. Events include preview,
release, type-specific outreach, acknowledgement, quick completions, Priya's alternate
progression, approval pending, and inform delivery without fake approval/alignment.

`HW-2412` uses REVIEW_APPROVAL plus an exact persisted APPROVE decision. `HW-2413` uses
REVIEW_APPROVAL plus an exact persisted CHANGE decision and consistent hard-constraint
meeting package. Repeated demo construction and ambient environment isolation remain proven.

## Files changed

Production:

- `src/humanwire/alignment.py`
- `src/humanwire/services.py`
- `src/humanwire/repository.py`
- `src/humanwire/web.py`
- `src/humanwire/demo.py`

Tests:

- `tests/humanwire/test_alignment.py`
- `tests/humanwire/test_workflow.py`
- `tests/humanwire/test_web.py`
- `tests/humanwire/test_demo.py`
- `tests/humanwire/test_container.py`

No schema, domain model, workflow router, deployment, organization/contact, secret, or
external-system file changed.

## Fresh final verification

All gates below were run after the last production edit:

```text
Required focused RED/GREEN matrix (-v): 156 passed, 197 deselected; exit 0
Required four focused files (-v):       408 passed; exit 0
HumanWire suite (-q):                   927 passed; exit 0
Complete repository suite (-q):       1017 passed; exit 0
Ruff scoped gate:                       All checks passed; exit 0
git diff --check:                       exit 0
git diff --cached --check:              exit 0
```

The only warning is the pre-existing Starlette/httpx TestClient deprecation warning.

## Self-review

The final diff/privacy/race review found and fixed three additional fail-closed edges before
completion: a cross-mandate assignment could look locally valid, an anonymous optional
commitment could appear as an agreement, and a declined interview lost its truthful saved
question progress. Focused RED/GREEN tests now cover each. The review found no remaining
model-authority path, raw decision/private exposure, stale synthesis write, universal-session
assumption, Task 6 invariant regression, Task 8/deployment change, or open Task 7 concern.
