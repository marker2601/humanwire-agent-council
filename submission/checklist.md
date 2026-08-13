# HumanWire submission and launch checklist

Official event rules, eligibility, registration state, reuse terms, and deadlines are external pending until the entrant retains the event-specific official evidence.

## Eligibility and submission

- [ ] Caspian: retain official eligibility evidence for entrant/team and any organizer exception.
- [ ] Caspian: retain registration confirmation.
- [ ] Caspian: retain official code-reuse/originality terms and map repository history to them.
- [ ] Caspian: retain the official deadline/time-zone evidence and submit before it.
- [ ] Caspian: retain required media/form-field evidence and final submission receipt.
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
- [ ] Public repository, video, and demo links work in a signed-out browser.
- [ ] Final page preview contains no missing media, private identifiers, or broken links.

## Qualifying implementation

- [x] Python package uses `caspian-sdk==0.6.1`.
- [x] Email and Telegram enter exactly one `on_message` handler.
- [x] One persisted workflow coordinates both channels.
- [x] HumanWire chooses among six explicit engagement contracts.
- [x] Featherless output is advisory and deterministic policy retains authority.
- [x] English setup, proof, limitations, and privacy instructions are public.

## Repository and privacy

- [ ] Push the final `codex/humanwire` history to the public repository.
- [ ] Confirm a fresh-clone editable install exposes only the `humanwire` command.
- [ ] Confirm `.env`, `.env.local`, `.vercel`, `data/organization.json`, databases, direct contact destinations, conversation IDs, provider bodies, and private responses are absent from tracked files and Git history.
- [ ] Confirm no obsolete package, command, fixture, test, or operator script remains.
- [ ] Do not publish `.superpowers/brainstorm/` or any of its token files.
- [ ] Keep the repository public through judging.

## Required live proof

- [ ] Start `humanwire listen` with the operator's configured Caspian, Telegram, and optional Featherless credentials.
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
- [ ] Retain only safe tokens, timestamps, and screenshots; never record keys, routes, addresses, private answers, or provider bodies.

## Public demo and browser QA

- [ ] Update [secondsignal.vercel.app](https://secondsignal.vercel.app) only after local and live gates pass.
- [ ] `/`, `/mandates/HW-2411`, `/mandates/HW-2411/reach`, `/mandates/HW-2411/data`, and `/health/live` return 200.
- [ ] Dashboard, Decision Room, Reach, Data, JSON, CSV, and ICS visibly identify HumanWire.
- [ ] Test desktop, 600-pixel, and 390-pixel widths for clipping, overlap, readable controls, and navigation.
- [ ] Confirm there are no relevant browser console errors or framework overlays.
- [ ] Confirm the demo is labeled synthetic/read-only and loads no ambient configuration.

## Final local gate

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe scripts\smoke_humanwire.py
.\.venv\Scripts\python.exe -m humanwire smoke
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
