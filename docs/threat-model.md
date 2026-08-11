# SecondSignal threat model

## Security objective

SecondSignal reduces the chance that a user obeys a high-risk request from a compromised communication account. It asks the claimed sender to confirm or deny the request through a separately registered route and makes the resulting evidence understandable to the reporter.

## Assets

- The integrity of verification verdicts
- The mapping between a person and their registered channel routes
- Reporter and verifier addresses and conversation identifiers
- Caspian, Telegram, and Featherless credentials
- Suspicious message content and redacted summaries
- Case tokens, state, expiry time, and audit history
- Service readiness and delivery status

## Actors

- **Reporter:** a registered user requesting verification.
- **Verifier:** the person whose identity is being claimed.
- **Same-channel attacker:** controls or impersonates the account that sent the suspicious request.
- **External attacker:** guesses tokens, forges messages, replays traffic, or attempts service disruption.
- **Operator:** configures routes and runs the listener and dashboard.
- **Service providers:** Caspian and the configured communication and inference services.

## Trust boundaries

1. Untrusted inbound message content crosses from Caspian into the gateway.
2. Sender metadata crosses from a channel provider into the authorization and verifier-matching policy.
3. Redacted suspicious content may cross to Featherless for structured extraction.
4. Private route configuration crosses from local operator storage into the workflow.
5. Persisted cases cross into the read-only dashboard through a sanitizing view model.

## Threats and controls

| Threat | Attack path | Controls | Residual risk |
|---|---|---|---|
| Same-channel account compromise | An attacker sends an urgent request and tries to confirm it on the same account. | The route selector rejects the origin channel and sends the challenge through a different registered channel. | The attacker may still pressure the reporter or cause delay. |
| Prompt injection in suspicious text | The forwarded request tells the model to approve it, contact a different person, reveal data, or ignore policy. | Content is delimited as untrusted, redacted, and used only for a validated fact schema. Code controls identity, route, state, delivery, and verdict. | A model can mislabel risk facts; the human verdict remains authoritative. |
| Forged verifier reply | An attacker sends `YES` or `NO` with a visible token. | Channel, normalized sender address, case token, and pending state must all match the stored route. Invalid attempts are rejected and audited. | Provider sender metadata is trusted; weaknesses in the provider account or metadata are outside this application. |
| Token guessing | An attacker guesses a six-character public case token. | Tokens are random, scoped to a required registered sender and channel, expire quickly, and cannot bypass route checks. | Tokens are evidence references, not standalone authentication secrets. Rate limiting is delegated to the provider and deployment layer. |
| Duplicate or replayed messages | A provider retries a report or an attacker replays an old answer. | Reports use a hash-based idempotency key. Case tokens are unique. Terminal states are immutable. | A replay can still generate a harmless “already resolved” reply. |
| Sensitive-data leakage | Messages, logs, templates, or source control expose secrets or private identifiers. | Redaction occurs before model calls and persistence; logs use an allowlist; dashboard views omit addresses; `.env`, databases, and the real registry are ignored. | Operators must still avoid forwarding highly sensitive content and must protect local files and service dashboards. |
| Both accounts compromised | The attacker controls both the request channel and the separately registered verification account. | No application control can restore independence after both trust anchors fail. | SecondSignal cannot protect this case. Manual or in-person verification is required. |
| Denial of service | An attacker floods commands or prevents the operator from receiving results. | Authorization, narrow parsing, idempotency, bounded recent-case queries, and provider queue handling limit some amplification. | This demonstration has no dedicated rate limiter or distributed scaling. |
| Delivery outage | Caspian or a channel cannot deliver the independent challenge. | A verification delivery error transitions the case to `DELIVERY_FAILED`; timeouts transition to `EXPIRED`; both produce an unverified receipt. | A legitimate request may remain unresolved until service returns. |
| Dashboard mutation attempt | An attacker tries to approve or deny a case through HTTP. | The dashboard exposes GET routes only and has no workflow, registry, or Caspian dependency. | Deployment still needs ordinary network access controls if exposed beyond localhost. |

## Security invariants

- Same-channel verification is never accepted.
- No AI output directly changes case state.
- Only a valid registered human response can produce `VERIFIED` or `DENIED`.
- Missing routes, timeouts, cancellation, and delivery failures never produce `VERIFIED`.
- Every state change is checked by the deterministic state machine and recorded.
- Public evidence omits private routing identifiers.

## Explicit limitations

SecondSignal does not claim:

- legal identity proof;
- cryptographic attestation of a person or device;
- prevention or blocking of a payment;
- correctness when both registered accounts are compromised;
- protection against a malicious operator who changes the local registry;
- enterprise-grade rate limiting, high availability, or multi-tenant isolation.

For a production deployment, route enrollment should require a stronger ceremony, registry changes should be authenticated and audited, secrets should use a managed vault, persistence should move to a hardened database, and externally reachable endpoints should have authentication, rate limits, and monitoring.
