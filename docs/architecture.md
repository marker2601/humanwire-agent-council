# HumanWire architecture

HumanWire is an adaptive coordination system. It asks for the smallest truthful human contribution needed for a manager mandate; it does not interview everyone.

## Coordination path

1. An authenticated manager sends `/mandate` over a registered email or Telegram route.
2. The planner returns a bounded plan. Model suggestions are advisory; directory policy and typed domain validation decide what may persist.
3. HumanWire persists a preview before outreach. The initiating manager can use `ENGAGE` to change an optional contract, or `GO` to release a plan that requires explicit release. Otherwise the configured preview deadline releases it.
4. The engagement coordinator applies one of six contracts: `INFORM`, `ACKNOWLEDGE`, `QUICK_RESPONSE`, `STRUCTURED_INTERVIEW`, `REVIEW_APPROVAL`, or `AVAILABILITY`. Only quick and structured contracts create interview sessions.
5. Quick and structured answers first persist as asserted, source-bound evidence. After the completed session prompts `CONFIRM <token>`, only that person may confirm over the session's persisted current route and conversation; the atomic confirmation promotes only answer-derived evidence and records a count-only event. Required pending confirmation keeps the mandate active, while an optional unconfirmed answer does not block otherwise-ready work.
6. A contribution evaluator checks exact assignment-bound confirmed evidence, decisions, and availability. Alignment runs only after all required contributions reach a truthful terminal result.
7. A blocking public conflict produces a persisted proposal. Every required respondent must answer each round. One unresolved first round creates round two; an unresolved second round reaches meeting-required and scheduling. There is no third round.
8. Exact authenticated availability from the required attendee set is intersected locally. A meeting package is persisted only from a verified overlap. The ICS route is a read-only download and never writes to a calendar.

A required `CHANGE` decision is different from a proposal change. It is a blocking contribution and produces a safe partial result. It does not create a proposal, availability request, or meeting package.

## Delivery and restart boundary

Initial outreach is committed with a durable outbox row containing stable aggregate and attempt identity, not a destination or provider body. The trusted directory reconstructs the exact registered route. Claims have an owner and lease; renewal and callback completion are fenced by outbox ID, assignment, route position, attempt, and owner. After a crash, an expired claim can be recovered with the same stable message identity.

This is an at-least-once boundary. HumanWire makes duplicate callbacks and inbound replays inert, but it does not claim that an external provider sends exactly once. Email-to-Telegram continuation keeps one persisted interview session, question index, route order, and conversation correlation.

## Product projections

- The public interactive studio renders Decision Room, Reach, and Data from one bounded stream of safe saved progress; its final JSON and CSV downloads come from the validated final evidence in that stream.
- The legacy/local Decision Room, Reach, and Data surfaces rebuild read-only mandate views; their CSV and JSON exports share one canonical 16-field analytics projection and filter contract.
- The local meeting ICS route requires current, verified meeting proof.

Public projections are rebuilt from persisted records and remove contact routes, provider metadata, raw private evidence, raw change text, and credentials. Unknown mandates return a bodyless 404. Internal analytics APIs require the configured bearer token outside demo mode; the token is never returned.

## Storage and production considerations

SQLite is the local transactional boundary used by the deterministic demo and offline proof. Repository transactions, compare-and-save fences, unique identities, the release outbox, events, decisions, proposals, availability, and meeting packages survive workflow restart.

A production migration needs a managed relational database with equivalent constraints and transaction isolation, a single migration owner, independently supervised due-action workers, provider retry/lease monitoring, encrypted backups, retention policy, and secret injection outside the database and logs. The public interactive product uses isolated ephemeral file-backed run roots under the serverless temporary directory; it is not a persistent multi-tenant database. Production cutover and real provider verification are separate operator gates.
