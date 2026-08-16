# HumanWire submission and launch checklist

Official event rules, eligibility, registration state, reuse terms, and deadlines are external pending until the entrant retains the event-specific official evidence.

## Eligibility and submission

- [x] Caspian: official eligibility/rules evidence reviewed through authenticated Devpost records.
- [x] Caspian: registration confirmed live through the authenticated Devpost account.
- [x] Caspian: official submission requirements and judging criterion retained in the packet.
- [x] Caspian: live deadline verified as 2026-08-16 18:30 UTC and entry sent before it.
- [x] Caspian: public repository/video requirements satisfied; submission receipt 1140539 retained.
- [ ] ML Empowerment: retain official eligibility evidence for entrant/team and any organizer exception.
- [ ] ML Empowerment: retain registration confirmation.
- [ ] ML Empowerment: retain official code-reuse/originality terms and map repository history to them.
- [ ] ML Empowerment: retain the official deadline/time-zone evidence and submit before it.
- [ ] ML Empowerment: retain required media/form-field evidence and final submission receipt.
- [ ] Build Beyond: retain official eligibility evidence for entrant/team and any organizer exception.
- [ ] Build Beyond: retain registration confirmation.
- [ ] Build Beyond: retain official code-reuse/originality terms and map repository history to them.
- [ ] Build Beyond: retain the official deadline/time-zone evidence and submit before it.
- [ ] Build Beyond: retain required media/form-field evidence and final submission receipt.
- [ ] Project name and tagline consistently identify HumanWire.
- [x] Public repository, video, and demo links work from signed-out requests.
- [x] Final Devpost record contains the public product and reviewed YouTube video with no private identifiers.

## Qualifying implementation

- [x] Python package uses `caspian-sdk==0.6.1`.
- [x] Email and Telegram enter exactly one `on_message` handler.
- [x] One persisted workflow coordinates both channels.
- [x] HumanWire chooses among six explicit engagement contracts.
- [x] Featherless output is advisory and deterministic policy retains authority.
- [x] English setup, proof, limitations, and privacy instructions are public.

## Repository and privacy

- [x] Publish the final safe snapshot to the public repository (`main` at `2c375b8`).
- [ ] Confirm a fresh-clone editable install exposes only the `humanwire` command.
- [ ] Confirm `.env`, `.env.local`, `.vercel`, `data/organization.json`, databases, direct contact destinations, conversation IDs, provider bodies, and private responses are absent from tracked files and Git history.
- [ ] Confirm no obsolete package, command, fixture, test, or operator script remains.
- [ ] Do not publish `.superpowers/brainstorm/` or any of its token files.
- [ ] Keep the repository public through judging.

## Required live proof

- [ ] Create a distinct private deployment, managed PostgreSQL database, Caspian project, directory, analytics token, email connection, Telegram bot, and consenting operator-owned test identities.
- [ ] Keep `FEATHERLESS_API_KEY` optional; deterministic policy and fallbacks retain authority.
- [ ] Set `DATABASE_URL` to the private PostgreSQL target, `ORGANIZATION_PATH` to the private directory, `ENGAGEMENT_REQUIRE_GO=true`, and `PUBLIC_DEMO=false` in ignored or deployment secrets.
- [ ] Run `alembic upgrade head` as the only private-sandbox schema startup path; do not use `humanwire init-db` or `create_all`.
- [ ] Independently verify the database current revision, set `HUMANWIRE_ALEMBIC_REVISION` to the exact repository head, and run `humanwire sandbox check` without treating its static result as connectivity proof.
- [ ] Review `humanwire sandbox checklist`; keep every item pending until external proof exists.
- [ ] Start exactly one `humanwire listen` owner for the private provider stream/database with the operator's configured Caspian, Telegram, and optional Featherless credentials.
- [ ] Send the manager mandate from its exact registered route and conversation.
- [ ] Show the safe preview and one constrained optional override before release.
- [ ] Show one `INFORM` delivery with no response request.
- [ ] Show one authenticated `ACKNOWLEDGE` response.
- [ ] Complete one `QUICK_RESPONSE` and its exact `CONFIRM <token>` boundary.
- [ ] Begin one `STRUCTURED_INTERVIEW` over email, continue it over Telegram, and confirm its answer-derived evidence.
- [ ] Show one exact `REVIEW_APPROVAL` response.
- [ ] Allow an acknowledgement window to expire and verify the saved alternate-channel step.
- [ ] Show the bounded proposal or verified meeting-ready path.
- [ ] Confirm Decision Room, Reach, and Data match the provider events without exposing private content.
- [ ] Repeat the complete flow three times without database edits or code changes.
- [ ] Retain only safe token aliases, timestamps, aggregate counts, trace hashes, redacted screenshots, and outcomes; never record keys, database URLs/hosts/users/passwords, routes, identities, destinations, private answers, or provider bodies.
- [ ] Document backup, retention, reset, single-listener ownership, and evidence-destruction boundaries before the run.
- [ ] Set `live_provider_verified=true` only after all three flows and the retention/evidence review pass.

## Public product and browser QA

### Local coordination studio

- [x] The primary local command opens an idle **Start a coordination** composer and creates no run before submission.
- [x] Standard agent reasoning completes the request-to-meeting workflow without reading ambient model/provider settings.
- [x] The saved standard run includes rollback conflict, targeted interview, evidence confirmation, proposal revision, approval, availability, and a meeting package through one CaspianGateway handler.
- [x] Refresh, manual presentation replay, JSON/CSV parity, and second-run isolation are covered by local acceptance.
- [x] In-app Browser acceptance passed at 1280x720, 600x900, and 390x844 with synchronized replay, responsive tabs, attachment downloads, visible keyboard focus, 44px controls, 14px text, no page overflow, and a clean console.
- [x] Reduced-motion behavior remains covered by the automated contract; the in-app Browser reported no active reduced-motion preference to exercise manually.
- [ ] PENDING: retain a separate live PydanticAI/Featherless run before claiming live model use.
- [ ] PENDING: retain external Caspian, email, and Telegram evidence before claiming external delivery. **Workspace channels** alone is not that evidence.
- [x] Keep the older synthetic CLI classified as internal deterministic evidence, not the primary product screen.

- [x] [secondsignal.vercel.app](https://secondsignal.vercel.app/) is live after local and production gates passed.
- [x] `/`, `/api/catalog`, the exact guarded POST `/api/runs`, and public assets work signed out; unknown/private routes fail closed.
- [x] A submitted request streams progressive saved updates and reaches the meeting-ready outcome.
- [x] Decision Room, Reach, and Data navigation work during and after the run; JSON and CSV downloads use final validated stream evidence.
- [x] Desktop, 600-pixel, and 390-pixel widths pass clipping, overlap, target-size, readability, and navigation checks.
- [x] Browser acceptance reported no relevant console errors or framework overlays.
- [x] The public product visibly says **Standard agents · no external messages**, offers no model mode, and loads no ambient provider/model configuration.

## Final local gate

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe scripts\smoke_humanwire.py
.\.venv\Scripts\python.exe -m humanwire smoke
.\.venv\Scripts\python.exe -m humanwire sandbox checklist
.\.venv\Scripts\python.exe -m pip check
git diff --check
git status --short
```

- [ ] All tests, static checks, smoke checks, installability, diff checks, and privacy scans pass.
- [ ] No tracked or staged secret, direct destination, database, or deployment metadata exists.
- [ ] README, architecture, threat model, demo script, analytics guide, and all three submission narratives describe only verified behavior.
- [ ] One coherent `feat: launch HumanWire` commit exists before deployment.

## Claims boundary

- [x] HumanWire does not interview everyone; it selects the minimum necessary engagement.
- [x] Delivery and silence do not imply acknowledgement, evidence, approval, availability, or alignment.
- [x] A required approval `CHANGE` is a partial blocker, not a meeting trigger.
- [x] Provider delivery is described as at least once.
- [x] ICS is a local read-only artifact; no external calendar write is claimed.
- [x] Power BI support is a documented redacted import contract, not certification.
- [x] The deterministic demo and fake-provider smoke are not represented as live provider proof.
- [x] No organizer endorsement or production security certification is claimed.

## Official references

- [Caspian Buildathon overview](https://caspian.devpost.com/)
- [Detailed competition rules](https://caspian.devpost.com/rules)
- [Deadline-extension update](https://caspian.devpost.com/updates)
