# HumanWire synthetic agent runtime

This guide is for the local, operator-owned synthetic viewer. It watches a fresh SQLite-backed simulation while persisted events arrive, then exposes the completed run as validated JSON and CSV evidence. The viewer binds only to the literal loopback address at `http://127.0.0.1:8766`; it is GET-only and is not a deployed control plane. The public Vercel demo cannot start a simulation.

## Proof boundary

Every viewer page and accepted evidence artifact retains these exact labels:

```text
proof_class=synthetic_multi_persona
actor_type=simulated_persona
identity_source=synthetic_fixture
transport=fake_caspian
human_attested=false
live_provider_verified=false
```

Deterministic mode is the reproducible offline proof. It uses the real local workflow and repository boundaries with simulated personas and fake transport, and it makes no external model or provider call. Featherless mode is an explicit private, exploratory model-assisted run. Its suggestions remain schema-constrained and non-authoritative; the local workflow, policy, correlation, transaction fences, and transcript validator decide what may persist.

Neither a deterministic run nor an exploratory model-assisted run proves real people or production transport. A model-assisted run is not a live Featherless claim unless an authorized operator separately records the actual call and retains acceptable evidence. It never establishes live Caspian, email, Telegram, or human proof.

**Non-live disclaimer:** Deterministic simulation and frozen replay are local synthetic proof, not live Caspian, email, Telegram, Featherless, or human proof.

PydanticAI was not added because the existing adapter passed strict tests.

## Start a deterministic watch

Run from the repository root. The run root must not exist: HumanWire atomically claims it and fails closed rather than sharing, overwriting, or deleting it.

```powershell
# Deterministic, no external model/provider call
.\.venv\Scripts\python.exe -m humanwire synthetic watch `
  --agent-mode deterministic `
  --seed 8842 `
  --run-root work\synthetic-watch-8842 `
  --output work\synthetic-watch-8842\transcript.json
```

Open `http://127.0.0.1:8766`. Follow Live tracks newly persisted events. Previous and Next select saved events manually; Play and Pause replay only those saved events. The selected event's `From -> To -> Generated` strip and the single highlighted persona or origin describe the same persisted event.

Downloads remain disabled while the run is starting or running. They activate only after completion, when the terminal states are `meeting_ready,partial` and the transcript is validated and bound to the final evidence. JSON and CSV are attachment downloads; selecting JSON must download the evidence rather than navigate to raw JSON.

## Freeze, download, stop, and replay

1. Use a brand-new ignored run root for every watch or replay. Never reuse `work\synthetic-watch-8842` after the example run has created it.
2. Wait until the viewer reports Complete and confirm the six labels.
3. Download JSON evidence and CSV events while the viewer is still running. Confirm that the completed JSON evidence records terminal states `meeting_ready,partial`. Keep any screenshots and downloaded artifacts under the ignored run root; do not commit them.
4. Treat `transcript.json` as frozen only after strict transcript validation, privacy review, and semantic replay succeed. Never edit a frozen transcript.
5. Stop the viewer with `Ctrl+C`. Stopping the viewer does not mutate persisted workflow state.
6. Replay the frozen transcript into another fresh run root:

```powershell
.\.venv\Scripts\python.exe -m humanwire synthetic replay `
  --transcript work\synthetic-watch-8842\transcript.json `
  --run-root work\synthetic-watch-8842-replay
```

Accept the replay only when transcript validation passes and its semantic trace hash and `meeting_ready,partial` terminal states equal the generation result. Replay consumes recorded actions and must not call a model.

## Explicit exploratory Featherless watch

Inspect only whether the configured `Settings().featherless_api_key` is present; never print, log, paste, or retain its value. If it is absent, record exactly:

```text
model-assisted runtime: PENDING — FEATHERLESS_API_KEY not configured
```

Do not make a model call when the key is absent. When it is present, use a fresh ignored run root and this explicit command:

```powershell
# Explicit private exploratory Featherless mode; reads only configured Featherless settings
.\.venv\Scripts\python.exe -m humanwire synthetic watch `
  --agent-mode featherless `
  --seed 8842 `
  --run-root work\synthetic-model-8842 `
  --output work\synthetic-model-8842\transcript.json
```

Model-assisted output is exploratory, not a replacement for the committed deterministic fixture. Before freezing it, require strict transcript validation, no private text, credential, route, destination, conversation, message identifier, operational UUID, or filesystem-path leak, exactly one gateway handler, and exactly one inbound attempt for every non-silence action. Then replay it into a fresh root and require semantic trace hash equality:

```powershell
.\.venv\Scripts\python.exe -m humanwire synthetic replay `
  --transcript work\synthetic-model-8842\transcript.json `
  --run-root work\synthetic-model-8842-replay
```

A safe model failure is evidence of the fail-closed boundary. It is not permission to weaken validation, privacy, replay, authority, or provenance checks. Never commit the model transcript, database, sidecar, downloads, screenshots, or browser artifacts.

## Local-only operating rules

- Use literal `127.0.0.1`; do not bind the viewer to a hostname, LAN address, wildcard address, proxy, tunnel, or public deployment.
- Do not add provider credentials, private identities, a Supabase project, or private sandbox data to this workflow.
- The viewer observes one local run. It cannot send Caspian, email, or Telegram messages, authenticate a person, or attest a human response.
- Keep public Vercel and private sandbox deployment/configuration unchanged. Deployment and fixture promotion are separate, explicitly authorized operations.
