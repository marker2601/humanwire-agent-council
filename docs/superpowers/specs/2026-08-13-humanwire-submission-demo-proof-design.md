# HumanWire Submission Demo and Proof Design

**Date:** 2026-08-13
**Status:** Approved for implementation by the entrant's explicit instruction to proceed
**Repository boundary:** `codex/humanwire` at `f8bad9c69da19fe3f41d5c22ea054c645745ac62`

## Objective

Make HumanWire submission-ready for the Caspian Buildathon, ML Empowerment Build Challenge 2.0, and Build Beyond without weakening its authority, privacy, or read-only public-demo boundaries.

The result must give judges a realistic interactive explanation of how one mandate propagates through people and channels, show which safe data point is generated at every persisted stage, provide real JSON and CSV downloads, and supply a reproducible offline multi-persona simulation. Real provider delivery and real-human testing remain a separate private operator proof.

## Delivery order

1. Upgrade the public replay and JSON export.
2. Complete the three differentiated local Devpost draft packets and a shared asset checklist.
3. Add an isolated deterministic multi-persona simulation with frozen replay.
4. Prepare a private hosted-PostgreSQL/Caspian sandbox contract and verifier.
5. Run full automated, privacy, and browser verification.

This order protects the submission deadlines. Hosted infrastructure and live provider actions may not delay the public demo or local submission packet.

## Proof classes

HumanWire will expose three proof classes and never blend their claims:

| Proof class | Transport | Actor | Persistence | Permitted claim |
|---|---|---|---|---|
| Public demo replay | None | Saved synthetic fixture | In-memory demo repository | Interactive explanation of persisted HumanWire behavior |
| Offline multi-persona simulation | Injected fake Caspian adapter | Deterministic simulated personas | Fresh temporary SQLite | Reproducible behavioral integration through the real gateway/workflow/repository |
| Private live sandbox | Caspian email and Telegram | Consenting operator-owned test identities | Separate private PostgreSQL database | Real provider transport and cross-channel persistence |

The public demo and offline simulation must visibly state:

- `proof_class=synthetic_multi_persona`
- `actor_type=simulated_persona`
- `identity_source=synthetic_fixture`
- `transport=fake_caspian`
- `human_attested=false`
- `live_provider_verified=false`

The private sandbox may set `live_provider_verified=true` only after the operator completes the documented checklist and three consecutive flows. Code, fixtures, screenshots, and submission copy may not imply that this gate has already passed.

## Public replay experience

Reach remains GET-only and derives its view exclusively from persisted safe events. The existing ordered event list, selection, filters, play/pause, previous/next controls, exact assignment binding, reduced-motion behavior, and privacy boundary remain authoritative.

Each replay event gains a safe explanatory projection:

- `stage_label`: one of `Mandate`, `Plan`, `Outreach`, `Response`, `Evidence`, `Decision`, `Proposal`, `Scheduling`, or `Outcome`;
- `source_label`: a public role or system label, never a route, address, conversation, provider body, UUID, or raw private answer;
- `destination_label`: a public stakeholder name/role or `HumanWire`/`Decision Room`;
- `data_point_label`: a short description of the safe persisted fact created or changed by the event;
- `highlight`: the existing exact `mandate + assignment + person` target, `origin`, or `none`.

The replay panel presents a stable flow strip:

```text
From [source]  ->  To [destination]  ->  Generated [safe data point]
```

On event changes, the current origin/stakeholder card and flow strip transition smoothly. Motion communicates causality only; it does not fabricate movement or modify saved state. `prefers-reduced-motion: reduce` disables animated transitions while keeping every value visible. Playback pauses when the page is hidden and never auto-starts.

The public experience remains a replay of a frozen persisted scenario. It does not run models, contact people, create mandates, or write telemetry.

## JSON and CSV exports

The canonical 16-field outreach projection remains the single source for HTML, JSON, and CSV.

- The existing JSON API route remains an inline authenticated API for integrations.
- A new `.json` download route returns the same filtered rows with `Content-Disposition: attachment` and a privacy-safe filename.
- The Data-page control points to the download route and reads `Download JSON`.
- CSV behavior, filtering, formula hardening, and safe filenames remain unchanged.
- JSON is serialized as UTF-8, stable indented JSON with a trailing newline.
- Successful JSON and CSV downloads preserve the current filter query exactly.
- Lookup, projection, deny-corpus, or database failures return the existing safe 404/503 semantics without an attachment header or private error content.

## Offline synthetic multi-persona simulation

The simulation is a separate module, not an extension of public HTTP mutation and not a hidden production mode.

### Components

- `offline_caspian.py`: reusable deterministic fake Caspian transport used by smoke and simulation.
- `synthetic.py`: versioned scenario and transcript models, persona policies, virtual clock, event queue, generation, frozen replay, provenance, semantic trace normalization, and hashing.
- `scripts/synthetic_humanwire.py`: thin command wrapper.
- `tests/fixtures/humanwire/synthetic_launch_v1.json`: frozen safe transcript.
- `test_synthetic.py`: isolation, determinism, provenance, tamper, restart, privacy, and gateway-boundary tests.

### Persona isolation

Every persona receives only its public role, bounded private fixture facts, allowed intents, current engagement contract, delivered message, its own transcript, and virtual time. A persona never receives another persona's transcript, the database, system event log, secrets, routes, sender address, connection ID, conversation ID, message ID, or expected final result.

Persona output is a strict response envelope with:

- schema version;
- stable persona and action IDs;
- triggering outbound digest;
- time offset;
- allowed intent;
- bounded content.

The orchestrator—not the persona—derives sender, route, conversation, channel envelope, and provider message identity from the synthetic directory. Every response re-enters the existing single Caspian gateway handler. Direct workflow or repository mutation is prohibited.

### Modes

`generate` runs deterministic stateful persona policies and writes a transcript only to an explicit output path. `replay` loads the frozen transcript and must not instantiate or invoke persona policy code.

Both modes use explicit settings with `_env_file=None`, a new temporary file-backed SQLite database, `.example.test` identities, deterministic advisory adapters, a fixed UTC clock, and the injected fake Caspian client. They may not read ambient environment variables, `.env`, the real organization directory, production databases, deployed URLs, provider SDK clients, or external networks.

### Scenario

The primary scenario contains all six engagement contracts. It includes delivery-only context, authenticated acknowledgement, two independent quick responses, a structured interview that changes from email to Telegram, exact evidence confirmation, an explicit approval response, a saved alternate-channel step, two bounded proposal rounds, availability intersection, and one meeting package. A separate scenario retains a required approval `CHANGE` as partial and creates no proposal or meeting.

### Trace and determinism

The published trace uses semantic aliases instead of database UUIDs. Private response content appears only as a digest. Stable sorted UTF-8 JSON is hashed with SHA-256.

Two fresh generations and frozen replay must produce the same semantic trace hash. Restarting mid-scenario must preserve the final hash. Any change to persona, route, channel, order, source message, safe content, delivery, or persisted transition must change the hash or fail validation. Exact duplicate inbound attempts remain visible in the action trace but must not duplicate persisted effects.

## Submission packets

The existing `submission/caspian.md`, `submission/ml-empowerment.md`, and `submission/build-beyond.md` remain separate. Each packet gains:

- title and one-line summary;
- problem, solution, why it matters;
- event-specific technology value;
- verified features and architecture;
- responsible-AI boundary;
- factual description of how Codex was used;
- testing instructions;
- screenshot shot list;
- 75–90 second demo outline;
- placeholders for public repository, demo, video, and organizer-confirmed eligibility evidence;
- explicit limitations and proof-class wording.

No packet may call fake transport live, claim real humans approved anything, claim a hosted database exists before it does, or imply organizer endorsement. The Caspian packet emphasizes the single cross-channel handler and durable delivery. The ML packet emphasizes constrained advisory model use and deterministic human authority. Build Beyond emphasizes the persistent end-to-end mandate-to-meeting lifecycle and interactive product surfaces.

## Hosted database and real tester boundary

The public Vercel demo remains deterministic, synthetic, GET-only, and isolated from ambient configuration. It does not accept names, email addresses, Telegram identifiers, private answers, or visitor-created mandates.

The private tester sandbox uses a separate operator-owned deployment, directory, provider project, and database. The target storage is managed PostgreSQL; SQLite remains the offline proof boundary.

Before connecting any hosted PostgreSQL instance, HumanWire must have:

- an installed PostgreSQL driver;
- versioned Alembic migrations for every HumanWire table, constraint, and index;
- dialect-portable active-interview uniqueness behavior;
- PostgreSQL integration tests for release, outbox claim/lease, callback, confirmation, synthesis, and scheduling races;
- a connection/readiness verifier that prints no URL, password, hostname, direct contact, or row content;
- documented backup, retention, reset, and single-operator ownership rules.

Creating an external database, Caspian project, email connection, Telegram bot, or provider transmission requires the operator's private account and credentials. Those are the only implementation steps that may pause for user input. Credentials must be supplied through ignored local or deployment secrets and must never enter Git, screenshots, logs, test fixtures, or chat output.

Public usage awareness is limited to privacy-safe aggregate telemetry only if a later explicit design enables it. This implementation does not add public write endpoints or visitor tracking.

## Error and privacy behavior

- Invalid or ambiguous replay identity highlights nothing.
- Missing explanatory metadata falls back to neutral allowlisted labels; it never exposes raw event metadata.
- Invalid synthetic output becomes recorded synthetic silence/error and cannot advance state.
- Transcript tampering, unsupported versions, route mismatch, trigger mismatch, non-monotonic time, duplicate action IDs, or digest mismatch fail closed.
- Network attempts in offline generation or replay fail the run.
- Public projections continue to deny direct contact, route, conversation, message, provider, credential, UUID, private evidence, and raw change data.
- Download errors return safe status responses without filenames.

## Verification

Implementation follows strict red-green-refactor cycles.

Required automated gates:

- replay projection and template tests for every stage and malformed identity;
- JavaScript/CSS contract tests for flow updates, play/pause, reduced motion, visibility pause, and responsive layout;
- JSON download parity, headers, filenames, filters, authentication, 404, 503, and privacy tests;
- synthetic generation/replay determinism, isolation, provenance, tamper, restart, idempotency, and no-network tests;
- existing smoke, workflow, gateway, repository, demo, web, cutover, privacy, and full repository suites;
- Ruff and both working/staged diff checks.

Required browser gates at 1280×720, 600×900, and 390×844:

- previous/play/pause/next update the selected persisted event;
- From/To/Generated values match the event and exact highlighted card;
- filters, person selection, and replay remain coherent;
- JSON and CSV controls download the correct safe filename and filtered content;
- reduced-motion rendering remains complete;
- no page overflow, clipped meaningful text, sub-14px meaningful text, console errors, inaccessible controls, or broken navigation.

## Completion criteria

The work is complete when:

1. The public demo explains every replayed event's source, destination, and generated safe data point with accessible smooth transitions.
2. JSON and CSV both download while the canonical inline JSON API remains available.
3. The offline persona generator and frozen replay produce identical semantic trace hashes through the real injected gateway boundary with zero network access.
4. Every simulation artifact visibly declares synthetic/non-live provenance.
5. All three truthful Devpost draft packets and the shared checklist are materially complete except for external URLs, official form-only fields, eligibility evidence, video upload, and private live-proof evidence.
6. The private PostgreSQL/Caspian sandbox has a tested local integration contract and operator checklist; no claim is made that external resources were created until credentials are supplied and verification succeeds.
7. All automated and browser gates pass with a clean worktree and no tracked secrets or private identifiers.
