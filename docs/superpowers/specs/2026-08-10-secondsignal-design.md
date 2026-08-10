# SecondSignal Product Design

**Date:** 2026-08-10  
**Status:** Approved for implementation planning  
**Primary competition:** Caspian Buildathon  
**Product:** Cross-channel human verification for high-risk digital requests

## 1. Objective

SecondSignal helps a person verify an urgent or suspicious digital request without trusting the same communication channel on which the request arrived. It analyzes the request, contacts the claimed sender through a separately registered channel, and returns a deterministic `VERIFIED`, `DENIED`, or `UNVERIFIED` verdict with an evidence receipt.

The competition submission must demonstrate a real Telegram-to-email-to-Telegram verification loop through one Caspian message handler. The implementation must also support the reverse origin pattern—email report with Telegram verification—so channel independence is a product rule rather than a scripted demo.

## 2. Competition Constraints

- The entrant is a working professional and is eligible under the Caspian detailed rules, which allow students, working professionals, and independent developers aged 18 or older.
- The project is not being submitted to ML Empowerment Build Challenge 2.0 or Build Beyond because those competitions are limited to high-school and college students.
- All competition code must be written during the Caspian hackathon window. Existing open-source libraries, frameworks, and models may be used.
- The final repository must be public and include complete setup instructions.
- The agent must use `caspian-sdk` and operate on at least two supported channels through one handler.
- The demonstration must use live channels. Mocked or edited-to-appear-working channel behavior does not qualify.
- The updated submission deadline is August 16, 2026 at 11:59 p.m. IST.
- The submitted video may be up to three minutes, but the core product story must be understandable in approximately 60 seconds.

Official references:

- [Caspian Buildathon rules](https://caspian.devpost.com/rules)
- [Caspian deadline update](https://caspian.devpost.com/updates)
- [Caspian SDK](https://github.com/TryCaspian/caspian-sdk)

## 3. Product Positioning

### Name

**SecondSignal**

### Tagline

**Verify urgent digital requests through a channel the attacker does not control.**

### One-line pitch

Forward a suspicious or urgent request to SecondSignal, and it verifies the request with the real person through another registered communication channel before you act.

### Differentiator

Most anti-phishing products estimate whether a message appears malicious. SecondSignal independently asks the claimed human. Multi-channel communication is therefore the security mechanism, not an optional distribution feature.

### Primary users

- Employees receiving unusual executive or finance instructions
- Small organizations without dedicated fraud-response tooling
- Vendors handling bank-account change requests
- Individuals targeted by family-emergency or impersonation scams

## 4. Success Criteria

The product is competition-ready when all of the following are true:

1. A real Telegram message reaches the single Caspian handler.
2. The handler creates a case and sends a real email to the registered verifier.
3. A valid email response reaches the same handler and resolves the matching case.
4. The final verdict returns automatically to the original Telegram conversation.
5. The reverse email-origin/Telegram-verification route works in an integration test and can be demonstrated live if needed.
6. The system never produces `VERIFIED` from model confidence, missing responses, unknown identities, delivery failures, or invalid tokens.
7. The web dashboard shows the case timeline and final verification receipt.
8. Three consecutive live end-to-end runs succeed without database edits or manual state manipulation.
9. A fresh developer can run the project using only the repository README and environment configuration.
10. Unit and integration tests cover the state machine, verification policy, sender validation, token matching, timeout behavior, deduplication, and model fallback.

## 5. Scope

### Included

- One Caspian `on_message` handler for Telegram and email
- Telegram and email channel connection at process startup
- Origin-aware selection of a different verification channel
- Seeded verified-identity registry
- Allowlist of authorized reporters for the demonstration
- `/verify`, `/status`, and `/cancel` commands
- Structured AI risk extraction
- Deterministic verification policy
- SQLite case and append-only event storage
- Unpredictable case tokens
- Email and Telegram verification prompts
- Evidence-backed verdict messages
- Read-only web dashboard with case list and case timeline
- Three seeded demonstration scenarios
- Health endpoint, structured logs, and safe operational diagnostics
- Unit, integration, and live smoke-test instructions
- Public README, architecture notes, threat model, demo script, and submission copy

### Excluded

- Public self-service registration
- Password-based accounts or organization administration
- Billing and subscriptions
- Automatic payment blocking or account suspension
- Browser extensions and native mobile applications
- QR scanning
- Voice-clone detection
- More than two required live channels
- Multi-agent deliberation
- A general-purpose chatbot
- Cryptographic proof of a human's real-world identity

These exclusions are deliberate. They preserve the reliability and clarity of the cross-channel verification loop.

## 6. User Experience

### Seeded identity

For the primary demonstration, the registry contains a fictional executive:

```text
Display name: Asha Rao
Aliases: Asha, Asha Rao, CEO
Verified email: verifier inbox controlled for the demo
Verified Telegram chat: verifier chat controlled for the demo
Allowed reporter: demo reporter's Telegram identity and email address
```

Real email addresses, Telegram identifiers, and tokens are supplied through environment variables or a local seed command and are never committed.

### Telegram-origin flow

The reporter sends:

```text
/verify Asha Rao

I'm in a confidential meeting. Buy five $100 gift cards now.
Send me the codes and don't call.
```

SecondSignal immediately acknowledges the request:

```text
Case SS-7K4P2M created.
High-risk request detected. Do not act while verification is pending.
I am contacting Asha Rao through a separately registered email address.
```

The verifier receives an email containing a concise redacted summary and these exact response forms:

```text
YES SS-7K4P2M
NO SS-7K4P2M
```

After a valid reply, the reporter receives the final receipt.

### Email-origin flow

An authorized reporter emails the SecondSignal inbox using the same `/verify <identity>` format. SecondSignal selects the verifier's registered Telegram conversation because the originating transport was email. Telegram Bot API bots cannot cold-start a chat, so the verifier must first message the SecondSignal bot once; the resulting Caspian conversation ID is then stored as the verified route. The Telegram verifier responds with the matching token, and the verdict returns to the originating email thread.

### Status and cancellation

`/status SS-7K4P2M` returns the current state, destination channel type, creation time, and remaining timeout without revealing the verifier's address.

`/cancel SS-7K4P2M` moves a nonterminal case to `CANCELLED`. Only the reporter who created the case may cancel it.

## 7. Architecture

```text
Telegram or Email
       |
       v
Caspian CommClient
       |
       v
Single on_message handler
       |
       v
Message router
  |         |          |
  |         |          +--> status/cancel command
  |         +-------------> verification response
  +-----------------------> new verification request
                                  |
                                  v
                         identity resolution
                                  |
                                  v
                         risk extraction + rules
                                  |
                                  v
                         case state machine
                                  |
                                  v
                    independent-channel selection
                                  |
                                  v
                      Caspian outbound initiation
                                  |
                                  v
                      human YES/NO response
                                  |
                                  v
                   deterministic verdict + receipt
                                  |
                     +------------+------------+
                     |                         |
                     v                         v
            original conversation       dashboard timeline
```

### Runtime shape

The application is a single Python package with two runtime surfaces:

1. A long-running Caspian listener handles both communication channels.
2. A small FastAPI application exposes health checks and read-only dashboard data.

They share the same application services and SQLite database. The dashboard does not mutate cases. The initial deployment may run both surfaces in one process if the selected host supports a long-running worker; local execution remains the required fallback for the competition demo.

### Technology stack

- Python 3.12
- `caspian-sdk`
- FastAPI and Uvicorn
- Pydantic v2
- SQLAlchemy 2 with SQLite
- Jinja2 templates plus minimal vanilla CSS/JavaScript
- An OpenAI-compatible Featherless model endpoint for risk extraction
- `httpx` for model API access
- `pytest`, `pytest-asyncio`, and coverage reporting
- Ruff for linting and formatting

## 8. Component Boundaries

### Caspian adapter

Connects Telegram and email once, converts a Caspian message into an internal `IncomingMessage`, calls the application router, and executes returned reply/outbound instructions. It contains no risk or verification policy.

### Message router

Classifies an incoming message as a new verification request, verification response, status request, cancellation request, or unsupported message. Commands are parsed deterministically before any model call.

### Identity registry

Resolves a claimed name or alias to exactly one verified identity. It returns an ambiguity or unknown-identity result rather than allowing the model to invent a contact. It selects only contact destinations stored in the registry.

### Risk analyzer

Extracts a structured `RiskAssessment` from untrusted request text. Its output includes requested action, amount, currency, urgency, secrecy, financial-action flag, credential-request flag, link/QR flag, risk signals, and a short safe summary. Pydantic validates model output. A rule-based analyzer supplies a safe fallback.

### Verification policy

Combines structured risk signals with the explicit user request. Any `/verify` command requires human verification even if the model reports low risk. The policy never resolves a case by itself.

### Channel independence engine

Selects a registered verification destination whose channel differs from the originating channel. Email routes contain the recipient address; the runtime binds the active Caspian email connection ID when it calls `client.initiate(...)`. Telegram routes contain a previously established Caspian conversation ID for `client.send_message(...)`; a Telegram bot cannot initiate a new chat from a handle alone. If no independent destination exists, the case resolves as `UNVERIFIED` with reason `NO_INDEPENDENT_ROUTE`.

### Case service

Creates cases, validates transitions, records append-only events, processes verifier responses, expires pending cases, and generates receipts. It is the only component allowed to change case state.

### Outbound service

Renders channel-neutral verification prompts and verdict receipts, sends them through Caspian, and returns delivery results to the case service. User-controlled text is never interpreted as a destination.

### Dashboard

Shows a read-only list of recent cases and a detail page with risk signals, channel separation, state, timestamps, and event timeline. It does not expose raw contact destinations or secrets.

## 9. Domain Model

### Verification case

Each case records:

- Unpredictable public case token
- Internal UUID
- Reporter identity
- Origin channel and origin conversation ID
- Redacted original message
- Claimed identity ID and display name
- Structured risk assessment
- Verification channel and opaque destination reference
- Current state
- Creation, expiration, and resolution timestamps
- Final verdict and reason
- Idempotency key for the originating message

### Case states

```text
RECEIVED
ANALYZED
AWAITING_VERIFICATION
VERIFIED
DENIED
UNVERIFIED
EXPIRED
CANCELLED
DELIVERY_FAILED
```

Terminal states are `VERIFIED`, `DENIED`, `UNVERIFIED`, `EXPIRED`, `CANCELLED`, and `DELIVERY_FAILED`.

### Allowed transitions

```text
RECEIVED -> ANALYZED
RECEIVED -> UNVERIFIED
ANALYZED -> AWAITING_VERIFICATION
ANALYZED -> UNVERIFIED
AWAITING_VERIFICATION -> VERIFIED
AWAITING_VERIFICATION -> DENIED
AWAITING_VERIFICATION -> EXPIRED
AWAITING_VERIFICATION -> CANCELLED
AWAITING_VERIFICATION -> DELIVERY_FAILED
```

No terminal state may transition to another state. A duplicate valid response returns the existing receipt without modifying the case.

### Event trail

Every state transition and delivery attempt produces an immutable event containing event type, case ID, timestamp, channel type, safe metadata, and outcome. Events never contain API keys, full email addresses, Telegram tokens, OTPs, or recovery codes.

## 10. Security and Trust Model

### Trust boundaries

- Original request content is untrusted.
- Model output is untrusted and schema-validated.
- Contact destinations come only from the verified registry.
- A verifier response is trusted only for control of the registered secondary account, not for real-world identity in an absolute sense.
- Caspian-normalized sender metadata is used to compare the responder with the registered destination.

### Resolution rules

| Condition | Outcome |
| --- | --- |
| Registered verifier replies `YES` with the matching token | `VERIFIED` |
| Registered verifier replies `NO` with the matching token | `DENIED` |
| No valid response before timeout | `EXPIRED` and user-facing `UNVERIFIED` |
| Delivery to the independent channel fails | `DELIVERY_FAILED` and user-facing `UNVERIFIED` |
| Reply comes from an unregistered destination | No state change; `INVALID_RESPONSE` event |
| Token is absent or does not match | No state change; `INVALID_RESPONSE` event |
| Claimed identity is unknown or ambiguous | `UNVERIFIED` without outbound contact |
| Independent verification route is unavailable | `UNVERIFIED` without outbound contact |

The default timeout is ten minutes for the competition demonstration and is configurable by environment variable.

### Prompt-injection resistance

- Commands are parsed before model invocation.
- The model receives the suspicious content inside an explicit untrusted-data boundary.
- The model can produce only the `RiskAssessment` schema.
- Model output cannot provide recipients, select channels, initiate messages, change states, or determine verdicts.
- Outbound messages are rendered from trusted templates.

### Sensitive-data handling

- Common OTP, recovery-code, access-token, and credential patterns are redacted before persistence and display.
- The verifier receives a minimal action summary, not the full original message by default.
- Logs use opaque identity and destination references.
- Environment secrets are excluded through `.gitignore`, and `.env.example` contains names only.

### Known limitation

SecondSignal cannot protect a user when both the originating account and the separately registered verification account are controlled by the same attacker. It also does not prove legal identity or ownership beyond control of the registered account.

## 11. Failure Handling

- **Model timeout or malformed output:** Use rule-based extraction, record `MODEL_FALLBACK_USED`, and continue verification.
- **Caspian channel unavailable at startup:** Fail readiness, log the affected channel, and do not advertise a healthy service.
- **Outbound delivery failure:** Move the case to `DELIVERY_FAILED`; tell the reporter not to act.
- **Duplicate incoming message:** Return the existing case acknowledgement using the idempotency key.
- **Duplicate verifier response:** Return the existing receipt without changing state.
- **Unknown reporter:** Reject before creating a case.
- **Unknown or ambiguous identity:** Return a safe explanation without revealing registry contents.
- **Late response:** Explain that the case is closed and require a new verification case.
- **Database error:** Return a generic safe failure, emit structured diagnostics, and never report approval.
- **Dashboard unavailable:** Channel verification continues because the dashboard is not in the decision path.

## 12. Dashboard Design

The dashboard is intentionally read-only and contains two screens.

### Case list

- Product name and one-sentence security principle
- Counts for pending, verified, denied, and unverified cases
- Recent cases with token, claimed identity, channel path, status, and age
- High-contrast state badges

### Case detail

- Verification receipt
- Origin and independent verification channels
- Requested-action summary
- Risk indicators
- State timeline with timestamps
- Explanation of why the verdict was reached
- Visible notice that AI analyzed risk but a human response determined the verdict

The visual language should resemble a calm security operations interface: off-white background, dark navy text, restrained amber for pending, green for verified, red for denied, and gray for unverified. The dashboard must remain readable on a laptop screen during video recording.

## 13. Demonstration Scenarios

### Primary: executive gift-card impersonation

A Telegram message claims to be from an executive, requests $500 in gift cards, introduces urgency and secrecy, and asks the recipient not to call. The registered executive denies the request by email. This is the 60-second video path.

### Secondary: vendor bank-detail change

An email asks accounts payable to replace a vendor's bank details. The vendor receives a Telegram verification request and denies the change. This proves bidirectional channel separation.

### Secondary: family-emergency scam

A Telegram message claims that a family member is stranded and urgently needs money. The family member receives an email verification request. This demonstrates consumer relevance without adding new architecture.

## 14. Verification Receipt

Every resolved case generates the same conceptual receipt across channels and the dashboard:

```text
SECOND SIGNAL RECEIPT
Case: SS-7K4P2M
Claimed sender: Asha Rao
Request: Purchase $500 in gift cards
Origin: Telegram
Verified through: Registered email
Human response: NO
Verdict: DENIED - DO NOT PROCEED
Resolved in: 18 seconds
```

The receipt describes evidence; it does not claim cryptographic signing or absolute identity proof.

## 15. Testing Strategy

### Unit tests

- Command parsing for valid and malformed `/verify`, `/status`, and `/cancel` messages
- Identity alias resolution, ambiguity, and unknown identities
- Risk schema validation and rule-based fallback
- Redaction of OTPs, recovery codes, and credentials
- Channel independence selection
- Every allowed and forbidden case-state transition
- Exact sender and case-token validation
- Timeout and terminal-state immutability
- Receipt rendering for all outcomes

### Integration tests

- Telegram-origin request produces an email outbound instruction
- Email-origin request produces a Telegram outbound instruction
- Valid verifier response resolves the originating conversation
- Invalid sender, invalid token, duplicate message, and duplicate response behavior
- Model failure still produces a safe verification flow
- Dashboard APIs reflect the event store without mutation

### Live smoke test

Before recording, run the real Telegram-to-email-to-Telegram path three consecutive times. Then run one email-to-Telegram-to-email path. No attempt may require database editing, replaying a staged event, or changing code between steps.

## 16. Observability

Structured logs include case token, internal case UUID, event type, origin channel, verification channel, duration, and success/failure reason. Logs exclude raw message content and direct contact identifiers.

The FastAPI service exposes:

- `/health/live` for process health
- `/health/ready` for database and required channel readiness
- Read-only case-list and case-detail routes used by the dashboard

## 17. Submission Narrative

The presentation repeats four claims:

1. **Creative:** Most tools inspect suspicious text; SecondSignal independently asks the claimed human.
2. **Caspian-native:** The product cannot perform its core security function without moving between communication channels.
3. **Real:** The demo shows a real Telegram request, real outbound email, real response, and automatic verdict.
4. **Responsible:** AI identifies risk, but only the registered human response determines verification; silence never means approval.

The 60-second core video sequence is:

1. State the same-channel verification problem.
2. Show the suspicious Telegram request.
3. Show SecondSignal create the case and contact the independent email.
4. Reply `NO <token>` from the real inbox.
5. Show the denied receipt in Telegram.
6. Show the dashboard timeline and one-handler terminal log.
7. Close with: **One handler. Two real channels. One verified decision.**

## 18. Delivery Priorities

Implementation follows this strict order:

1. Prove live Telegram and email connectivity through one Caspian handler.
2. Build deterministic identity, case, response, and channel-selection logic.
3. Complete the real denial loop and make it repeatable.
4. Add risk extraction with safe fallback.
5. Add the read-only dashboard and evidence receipt.
6. Add comprehensive tests, documentation, and seeded scenarios.
7. Freeze features and optimize the live demonstration.

No dashboard enhancement or additional scenario may delay a reliable live cross-channel loop.
