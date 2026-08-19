# Task 7 report — explicit subject activation

## Outcome

Implemented optional, subject-bound bulk invitations and authenticated acceptance without imported-email claim matching or provider delivery. The feature remains disabled unless an `ActivationService` is injected into `DecisionOSDependencies`; legacy generic invitations and disabled organization routes remain compatible.

## Controller rulings applied

- Added exact POST routes `/api/organizations/{organization_id}/subject-invitations` and `/api/subject-invitations/accept`, with server-owned delivery consent and fixed safe response bodies.
- Added the minimal DecisionOS/organization transaction seam needed to create membership, consume the one-time grant, bind the exact subject, advance the graph, and publish audits atomically.
- Invitation issue changes selected committed human subjects from `directory_only` to `invited` in one graph version. Acceptance changes exactly one invited subject to `active` with the authenticated UID in one version. Retry/delivery status changes do not advance the graph.
- Replaced permissive Task 6 final-state receipt carry with private, immutable, digest-bound `invitations_created` / `invitation_accepted` transition records. Carry reads replay the exact contiguous chain and compare the canonical replay with the stored target graph; missing, duplicate, corrupt, or out-of-order state fails closed.

The cost is a small private transition-chain dependency for post-import projection reads and additive repository seams. No transition, token, digest, UID, email, or provider data is exposed by bulk issue receipts.

## Security and behavior

- Create requires a fresh exact `MANAGE_MEMBERS` context and explicit canonical subject IDs; only committed, unbound human directory subjects are eligible.
- AI, external, review, suspended, already-bound, duplicate, cross-tenant, and owner/admin role-escalation cases fail closed.
- Tokens are cryptographically opaque, one-time, expiry/revocation/retry-bound, and only their SHA-256 digests are persisted. Digest comparisons are constant-time.
- Acceptance authenticates a verified principal before membership exists and returns one generic `invitation_unavailable` failure for invalid, expired, revoked, corrupt, wrong-principal/tenant, or replayed grants.
- No configured transport means `not_delivered` and no external side effect. Injected transport receives only an opaque subject grant; partial delivery and retry state are truthful and idempotent.
- Both new routes use the existing exact App Check, origin/CSRF, authentication, content-type, body-size, and method profiles.

## TDD and verification

- Missing-module RED: collection failed with `ModuleNotFoundError: humanwire.organization_activation` (0 collected, 1 error).
- Initial core GREEN: 22 tests passed.
- Route RED: 8 expected failures before optional dependency/profile/route wiring; corrected to GREEN.
- Transition-chain RED: 6 genuine lifecycle/persistence failures (plus one corrected test-fixture name error) before publisher/replay implementation.
- Focused activation + Firestore: 120 passed.
- Task3/Task6 store + Firestore compatibility: 138 passed.
- Full organization + DecisionOS gate: 625 passed with the explicit emulator marker excluded.
- Repository-wide Ruff: passed.
- Firestore emulator marker: 1 skipped because `FIRESTORE_EMULATOR_HOST` was not configured.
- Scoped `git diff --check`: passed (Git reported only the repository's existing LF/CRLF notice).

No push, deployment, cloud/provider call, Firebase mutation, invitation delivery, or emulator startup was performed.

## Review fix round 1

Addressed the first post-implementation security review as a separate TDD round.

- RED: 12 activation failures covered server-route consent and route-less recovery, authorization-before-token creation, corrupt in-memory relation state, cross-kind invitation namespace collisions, durable unknown delivery, recursive traceback token reachability, and hostile response serializers/scalars. Six genuine Firestore REDs covered exact relation schemas/indexes, bounded transition reads, and combined transaction capacity; one additional failure was a test identifier defect and was corrected before production work.
- Route results are now reconstructed through the hook-free Task 6 canonicalizer and serialized only from detached exact models. Hidden/private fields, scalar subclasses, shadow serializers, and inconsistent nested receipts fail with fixed safe responses.
- Token-bearing issue, delivery, and acceptance work is contained below public service frames. Helpers catch provider/storage exceptions, return only token-free outcomes, clear bearer references, and public failures clear service/token locals before raising fixed errors.
- A non-null transport now requires one exact server-owned route ID. Route-less active grants may be atomically revoked and reissued when that route is first configured without advancing the already-`invited` graph. A different non-null route remains fail-closed.
- Delivery persists `delivery_sending` across the invitation, subject state, and global token index before the external call. An exception leaves an unknown state that survives restart, cannot be accepted, does not emit a new token, and is never automatically retried.
- Both adapters now validate the complete grant/state/global-index relation with exact schemas, built-in scalar types, tenant/subject/role/route/retry/status bindings, and a discriminated invitation kind. In-memory generic and subject grants share one ID/digest namespace and both acceptance paths reject cross-kind ambiguity.
- Activation transition replay performs only deterministic per-version reads and enforces a bounded contiguous chain, so unrelated future documents are not materialized.
- Firestore issue and acceptance prepare the exact combined graph/chunk/state/transition/audit/grant/index write plan before the first mutation and enforce the project-safe 450-write ceiling. A 5,000-subject/5,000-edge graph plus 100 invitations fails at exact prestate; the fitting 100-subject boundary succeeds.

Focused activation verification passed 46 tests. Focused Firestore verification passed 95 tests with the explicit emulator case skipped. The combined focused gate passed 141 tests with one emulator skip. No external invitation transport, provider, deployment, Firebase, cloud mutation, or emulator startup was used.

The final organization + DecisionOS compatibility gate passed all 646 selected tests with the emulator marker excluded. Repository-wide Ruff and the scoped whitespace diff check passed. The explicit Firestore emulator test was skipped because `FIRESTORE_EMULATOR_HOST` was not configured.

## Review fix round 2

Verified and addressed three Important and two Minor follow-up findings with another RED/GREEN cycle.

- RED: nine genuine failures reproduced missing INVITED subject state issuing a second grant, the inverse DIRECTORY_ONLY graph with a phantom grant relation, unrecoverable pre-provider PENDING state in memory and after Firestore restart, rejection of the valid 5,050-transition maximum lifecycle, ignored duplicate transition-version claims, and scalar-subclass keys accepted by the invitation/state/index exact loaders. The conservative over-limit guard already rejected a 10,001-transition request and remained as regression coverage.
- Prepared organization issue mutations now carry an exact relation requirement. An INVITED subject must have exactly one canonical subject-grant relation, while a DIRECTORY_ONLY subject must have none; the inverse or missing relation fails before token generation or any graph/grant publication.
- A same-route PENDING grant is now atomically revoked and reissued after a known pre-provider failure. The graph stays at the already-INVITED version, the former token is inert, and the retry receives a fresh bearer. SENDING remains a durable unknown state that returns no token and is never automatically delivered again.
- Firestore transition carry now performs one server-bounded range query ordered by graph version, limited to the exact expected count plus one duplicate detector. The bound is a conservative 10,000 transitions, above the derived 5,050 maximum for fifty 100-person issue batches plus 5,000 individual acceptances. Missing, duplicate, noncanonical, or more-than-10,000 chains fail closed; unrelated future documents stay outside the materialized range.
- Exact subject invitation mappings now require every stored key to be a built-in `str` before comparing the expected key set. Scalar-subclass keys fail for invitation, subject-state, and global-index documents.

Fresh focused verification passed 150 tests with the explicit emulator case skipped. No external provider, invitation delivery, deployment, Firebase/cloud mutation, push, or emulator startup was performed.

The final round-2 organization + DecisionOS gate passed all 655 selected tests (the prior 646-test gate plus nine new regressions) with the emulator marker excluded. Repository-wide Ruff and the scoped whitespace diff check passed. The explicit emulator test remained truthfully skipped because `FIRESTORE_EMULATOR_HOST` was not configured.

## Review fix round 3

Addressed the two Important findings from the third independent review in a separate TDD cycle.

- RED: five exact regressions failed before production changes. Both adapters revoked and reissued an expired `delivery_sending` grant; the Firestore generic path accepted both a removed-discriminator subject record and a subject record disguised as the exact historical generic schema; and new generic records lacked an explicit discriminator.
- `delivery_sending` is now handled before every expiry and retry branch in memory and Firestore. It remains a durable unknown outcome indefinitely, returns the same invitation ID without a token, performs no persistence or graph write, and cannot trigger another delivery. Same-route `delivery_pending` recovery remains unchanged.
- New Firestore generic invitation and global-index documents now store `invitation_kind = "generic"`. Acceptance uses one exact relational loader with built-in key/type checks, exact current-or-historical schema pairing, tenant/ID/status/role/expiry equality, and a bounded subject-state digest lookup. Subject grants with a removed discriminator, including an exact-legacy-shaped disguise, fail closed without membership creation or graph mutation. Exact historical generic records remain accepted.
- Existing in-memory shared invitation ID/digest namespace and cross-kind ambiguity tests remained green.

Fresh focused activation + Firestore verification passed 155 tests with one explicit emulator skip (156 collected). The non-emulator organization + DecisionOS compatibility gate passed all 660 selected tests. Repository-wide Ruff and the scoped whitespace diff check passed. The explicit Firestore emulator test was skipped because `FIRESTORE_EMULATOR_HOST` was not configured.

No external provider, invitation delivery, deployment, Firebase/cloud mutation, push, or emulator startup was performed.

## Review fix round 4

Addressed the two Important findings and one Minor finding from the fourth independent review in a separate TDD cycle.

- RED: six targeted regressions reproduced exact SDK timestamp rejection, attacker datetime hook dispatch, both missing and digest-rebound subject-state disguises creating generic memberships, absent historical provenance, and equal-but-new in-memory state publication for expired `delivery_sending`. A seventh focused RED then proved that provenance creation time was not constrained to precede invitation expiry.
- Firestore timestamp loading now accepts only exact built-in `datetime` or the exact trusted `google.api_core.datetime_helpers.DatetimeWithNanoseconds` type. It reads fields through base/member descriptors, accepts only built-in timezone objects, detaches to an exact aware UTC `datetime`, rejects non-microsecond nanoseconds rather than truncating them, and performs all invitation/state/index relational comparisons on normalized values. Datetime subclasses and hostile timezone hooks are rejected without dispatch.
- The frozen legacy contract was traced to one discriminator-less invitation document, one digest-addressed global index document, and a generic audit event that did not bind invitation ID, digest, role, or expiry. Because that audit was insufficient provenance, exact historical records now receive a private immutable migration record. A tenant cutover marker is created atomically with the first subject invitation; an exact historical pair must have identical unmodified provider creation metadata before that marker. The one-time provenance record transactionally binds organization, invitation, digest, creation time, role, and expiry before membership creation. Missing/rebound subject state and exact legacy-shaped subject invitation/index disguises remain inert across restart.
- Expired `delivery_sending` retry in the in-memory adapter now returns before replacement publication when no invitation was created, preserving every state/audit reference and the audit sequence exactly.
- The conditional real-emulator scenario now covers SDK-decoded current generic acceptance, a frozen historical generic migration across repository restart, and subject issue → delivery → repository restart → acceptance. It is truthfully skipped when `FIRESTORE_EMULATOR_HOST` is unset.

Fresh focused activation + Firestore verification passed 160 tests with one explicit emulator skip. The non-emulator organization + DecisionOS compatibility gate passed all 665 selected tests, with two emulator cases deselected. Both emulator-marked cases were then collected and truthfully skipped because `FIRESTORE_EMULATOR_HOST` was not configured. Repository-wide Ruff and the scoped whitespace diff check passed.

No external provider, invitation delivery, deployment, Firebase/cloud mutation, push, or emulator startup was performed.

## Review fix round 5

Addressed the final Important finding by removing every circular runtime provenance path and introducing a separate trusted pre-cutover initialization phase.

- RED: runtime generic acceptance of a newly manufactured discriminator-less pair created its own trust and membership; the repository had no explicit initialization seam; an activation service could expose subject invitation operations before initialization; and a restarted service attempted migration after subject evidence already existed. Additional exact corruption matrices covered absent, rebound, and recreated state/index relations, disguised subject grants, deleted/recreated markers and provenance, forged marker/provenance pairs, migration races, and every accessible subject-schema evidence class.
- A trusted tenant-bound `initialize_subject_invitation_schema` transaction now runs before subject invitation features are exposed. It refuses explicit subject grants/indexes, subject state, activation transitions, and organization graph INVITED/ACTIVE or member-bound indicators. It scans only exact frozen legacy generic invitation/index relations, then atomically writes one immutable version-2 cutover marker plus exact per-record provenance. The manifest, tenant, random cutover identity, creation metadata, role, expiry, and relation count are all bound; the transaction is idempotent only when the complete existing marker/provenance set matches exactly.
- Runtime generic acceptance is read-only with respect to cutover trust. It never creates or repairs a marker or provenance record. Legacy generic acceptance requires the exact initialized marker and matching immutable provenance; a missing, replaced, recreated, rebound, forged, or partially initialized relation fails closed without membership or graph mutation. Current generic records continue to write and require the explicit `generic` discriminator.
- Subject issue, retry, delivery, acceptance, and true no-op paths only verify initialization. They cannot recreate a deleted marker. `ActivationService.initialize_tenant` is an explicit read-only readiness gate, so a process restart verifies the durable marker instead of rerunning migration after subject evidence exists. The existing trusted SDK timestamp normalization and indefinite `delivery_sending` no-op behavior remain unchanged.
- The real-emulator scenario now creates the exact frozen legacy pair, runs trusted initialization, restarts the repository, accepts the historical invitation, and then exercises subject issue/delivery/restart/acceptance. No emulator bootstrap or Firebase mutation occurs when the emulator host is unset.

Fresh focused activation + Firestore verification passed all 187 non-emulator tests. The non-emulator organization + DecisionOS compatibility gate passed all 682 selected tests (the prior 665-test gate plus 17 new regressions). Repository-wide Ruff and the scoped whitespace diff check passed. Both emulator-marked cases were collected and truthfully skipped because `FIRESTORE_EMULATOR_HOST` was not configured.

No external provider, invitation delivery, deployment, Firebase/cloud mutation, push, or emulator startup was performed.
