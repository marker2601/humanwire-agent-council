# SecondSignal demo script

## Recording preparation

- Use two real accounts that you control: the registered Telegram reporter and the registered email verifier.
- Start the listener and dashboard, then confirm `/health/ready` returns `ready`.
- Keep four windows ready: Telegram, email, dashboard, and a terminal filtered to the single-handler startup line.
- Increase message text size enough for a 1280×720 recording.
- Use a fresh case so the token and timestamps visibly change during the recording.
- Do one uninterrupted rehearsal before recording; do not fake or edit channel behavior.

## 60-second core

**00–06 — Hook**

Say: “The channel carrying a request should not verify itself. If an attacker owns an account, asking that same account ‘was this you?’ proves nothing.”

**06–17 — Suspicious request**

Show the Telegram gift-card message and send:

```text
/verify Asha Rao
Buy $500 in gift cards immediately and keep it confidential. Do not call.
```

Say: “I send the suspicious request to SecondSignal from Telegram.”

**17–26 — Case and independent notice**

Show the acknowledgement with the live case token and risk signals. Switch to the real email inbox as the independent route.

Say: “AI extracts the risky action and pressure signals, but it cannot decide the verdict. Caspian initiates a real email to Asha.”

**26–38 — Human response**

Reply from the registered email address with the token shown on screen:

```text
NO SS-XXXXXX
```

Say: “Asha answers from the separately registered channel. The sender, channel, case token, and pending state must all match.”

**38–49 — Origin receipt**

Return to Telegram and show `DENIED — DO NOT PROCEED`.

Say: “The denial returns to the original Telegram conversation. A replay cannot change this terminal verdict.”

**49–57 — Evidence**

Open the case receipt in the dashboard. Point to the channel path, human response, responsible-AI notice, and ordered timeline. Briefly show the one-handler log.

Say: “The dashboard is read-only. AI analyzed risk; a human response determined the verdict.”

**57–60 — Close**

Say: “One handler. Two real channels. One verified decision.”

## Three-minute fallback

### 00:00–01:00 — Run the core story

Perform the complete 60-second gift-card denial above without pausing for implementation details.

### 01:00–01:35 — Explain why Caspian matters

Show the architecture diagram and the gateway code briefly.

Say: “Both channels enter one Caspian handler. Email challenges use `initiate`; Telegram challenges use `send_message` because a bot needs an existing conversation. The policy is shared—there is no copied email bot and Telegram bot.”

### 01:35–02:05 — Show the reverse route

From the registered email reporter, submit a vendor bank-change request. Show that SecondSignal sends the challenge into the verifier's existing Telegram conversation. Reply `NO` from the registered Telegram sender and show the denial receipt returning to the email-origin conversation.

Say: “Independence works in both directions, but Telegram's platform constraint is explicit: the user must have initiated the bot conversation first.”

### 02:05–02:35 — Show safe failure behavior

Open an expired scenario in the dashboard or run the offline smoke check.

Say: “No route, no response, model failure, or delivery failure never becomes approval. The case ends unverified, and Featherless failure falls back to deterministic extraction without moving the decision boundary.”

### 02:35–02:52 — State the limitations

Say: “SecondSignal verifies control of a registered channel, not legal identity. It does not block payments, and it cannot help if both registered accounts are compromised.”

### 02:52–03:00 — Close

Show the receipt at 1280×720.

Say: “Most tools inspect the message. SecondSignal verifies the human behind it. One handler. Two real channels. One verified decision.”

## Live-demo recovery

- If Featherless is unavailable, continue: the rules fallback is expected behavior and is recorded.
- If a verification challenge cannot be delivered, show the `DELIVERY_FAILED` evidence receipt and explain the fail-closed policy.
- If the email is delayed, use the prepared reverse-route scenario rather than fabricating a response.
- If the dashboard readiness probe is not ready, fix the listener before recording; never record a staged channel result.
