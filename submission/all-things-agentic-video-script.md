# HumanWire — All Things Agentic four-minute demo

This is a production script, not proof by itself. Record the final demo only after the live Google deployment passes acceptance. Keep the product portion one continuous take; use cuts only between the architecture introduction, the live take, and the cloud-console proof.

## 0:00–0:22 — The problem

**Visual:** HumanWire title, then the launch-decision composer.

**Narration:**

“Decisions do not stall because teams need more generated text. They stall because the wrong people are asked for the wrong contribution, objections arrive late, evidence is mistaken for assertion, and approval is treated as implied. HumanWire turns one objective into an evidence-backed, authority-approved, meeting-ready decision.”

## 0:22–0:48 — Architecture and trust boundary

**Visual:** `all-things-agentic-architecture.png`; highlight web, Firestore/Pub/Sub, private worker, ADK/Gemini, then the green authority boundary.

**Narration:**

“A public Cloud Run service creates a durable Firestore run before publishing an opaque Pub/Sub message. An authenticated push invokes a private worker. Google ADK coordinates Gemini 3.6 Flash specialists, but their typed decisions are only candidates. HumanWire alone validates identity, evidence, approval, scheduling, and persistence.”

## 0:48–2:48 — One continuous live Taskmaster run

**Visual:** Start from the deployed Cloud Run URL. Submit the fixed Launch decision template. Do not speed up or edit the workflow portion.

**Narration checkpoints:**

- “One click starts the asynchronous workflow; the browser is not running the agent.”
- At outreach: “Each stakeholder receives only the engagement their role requires.”
- At conflict: “Anika rejects an unsupported assumption, so HumanWire opens a targeted interview instead of forcing agreement.”
- At evidence: “The answer becomes usable only after the evidence gate confirms it.”
- At proposal revision: “The proposal changes because the saved evidence changed—not because the UI advanced an animation.”
- At approval: “Sofia acts only after evidence and revision. Her explicit approval is distinct from acknowledgement.”
- At availability: “Daniel is contacted only after approval.”
- At completion: “The meeting package is ready because every authoritative prerequisite is satisfied.”

Keep the selected event synchronized across the graph, Conversation, Data, and lifecycle panes. Show exactly one highlighted graph path.

## 2:48–3:20 — Durability and replay

**Visual:** Refresh the completed workspace. Use Previous to select the conflict event, switch Conversation/Data tabs, then Follow Live. Trigger JSON and CSV downloads without navigating.

**Narration:**

“A refresh reconstructs the same immutable prefix from Firestore. Replay selects the same event across every pane. JSON and CSV are regenerated from that timeline and bound to the final transcript and semantic trace, so another instance can serve the same evidence.”

## 3:20–3:48 — Visible Google Cloud proof

**Visual:** Cloud Run services/revisions, image digest, private worker IAM, Pub/Sub authenticated subscription, safe Firestore run structure, and fixed Cloud Logging events. Do not show account email, billing details, credentials, tokens, prompts, private model responses, project secrets, or raw logs.

**Narration:**

“Both Cloud Run services use the same digest-pinned image and dedicated service identities. Pub/Sub alone can invoke the private worker. Firestore owns the lease and immutable timeline. Vertex AI is available only to the worker through Application Default Credentials. The web identity cannot call the model.”

## 3:48–4:00 — Close

**Visual:** Meeting package ready, repository URL, Taskmaster label.

**Narration:**

“HumanWire is not another chatbot. It is a durable Taskmaster that does the coordination work while preserving the authority that makes a decision real. The complete code and reproduction steps are public.”

## Recording gate

- Runtime no longer than 4:00.
- Product workflow shown as one continuous, unedited live take.
- Visible qualifying `.run.app` URL and Google Cloud evidence.
- No secret, account email, project credential, private evidence, contact route, token, prompt, or raw provider output.
- Architecture matches the deployed services and IAM.
- Captions are accurate, max two lines, and do not cover product controls.
- Audio is intelligible at normal speed; no unreviewed synthetic narration.
- Repository and video links are public in a signed-out browser.
