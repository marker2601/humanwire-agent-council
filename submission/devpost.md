# SecondSignal

## Inspiration

Most security tools inspect messages. SecondSignal verifies the human behind them through a channel the attacker does not control.

Business email compromise, executive impersonation, fake vendor bank changes, and family-emergency scams exploit a simple weakness: people often verify a suspicious request by replying on the same account that may already be compromised. We wanted to turn “call them another way” from informal advice into a working, auditable agent.

## What it does

SecondSignal receives a suspicious request through Caspian on email or Telegram. A registered reporter names the person who allegedly sent it. The agent redacts sensitive strings, extracts the requested action and risk signals, creates a time-limited case, and contacts that person through a separately registered channel.

The claimed person replies `YES` or `NO` with the live case token. SecondSignal accepts the response only when the channel, sender address, token, and pending case all match. It then returns `VERIFIED`, `DENIED`, or `UNVERIFIED` to the original conversation and creates a read-only evidence receipt.

The model never determines the verdict. AI analyzes risk; a registered human response determines the decision.

## How we built it

We built SecondSignal in Python 3.12 around `caspian-sdk`. One `on_message` handler receives both email and Telegram events and maps them into a single typed workflow.

The workflow uses:

- Caspian email and Telegram connections through one handler;
- `initiate` for a new email verification conversation;
- `send_message` for an existing Telegram conversation;
- a local verified-route registry with explicit reporter authorization;
- Featherless for constrained structured risk extraction, with a deterministic rules fallback;
- Pydantic domain models and a strict state machine;
- SQLAlchemy and SQLite for cases, idempotency, audit events, and runtime health;
- FastAPI and Jinja for a read-only evidence dashboard.

Risk extraction is isolated from authority. The model receives delimited untrusted text and may return only a validated fact schema. Code selects the identity, enforces the independent channel, validates the responder, transitions the case, and renders the verdict.

## Challenges we ran into

The hardest design issue was making channel independence real instead of cosmetic. Telegram bots cannot initiate a conversation with an arbitrary user, so the reverse email-to-Telegram route must use a conversation captured from a genuine inbound Telegram message. We made that limitation explicit and built a safe capture utility.

We also had to distinguish a useful AI role from an unsafe one. Letting a model decide whether a request is genuine would make the result probabilistic and vulnerable to prompt injection. We restricted AI to redacted risk extraction and made every verdict depend on a deterministic sender, channel, token, and state check.

Finally, delivery failures and duplicate messages needed first-class behavior. The system is idempotent, terminal states are immutable, and missing evidence always fails closed as unverified.

## Accomplishments that we're proud of

- A real bidirectional email ↔ Telegram verification flow through one Caspian handler
- Deterministic human authority over every final verdict
- Exact responder-channel and sender-address validation
- Immutable case states with replay-safe idempotency
- Privacy-safe JSON logging and redacted model input
- A dashboard that exposes evidence without exposing private routes or mutation controls
- An offline three-scenario smoke check plus comprehensive unit and integration tests
- A 1280×720 receipt frame that communicates the entire trust decision in one shot

## What we learned

Cross-channel communication is not merely a delivery feature; it can be a security primitive. The important boundary is not “AI versus no AI.” It is whether probabilistic analysis is allowed to control an irreversible decision.

We also learned that channel capabilities shape product architecture. Email can be initiated, while Telegram requires prior user contact. A unified SDK does not erase those differences—it gives the application one place to handle them correctly.

## What's next for SecondSignal

Next we would add authenticated route enrollment, managed secret storage, audited registry changes, rate limiting, and a production database. We would extend the same policy to additional Caspian channels, add multi-person approval for very high-value actions, and explore device-bound or cryptographic attestation where a stronger identity claim is required.

We would also build organization policies such as “all bank-detail changes require two independent confirmations” while preserving the core rule: AI can surface risk, but only verified human evidence can authorize the verdict.
