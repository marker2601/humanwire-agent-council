# All Things Agentic evidence ledger

Only checked items may become final Devpost claims.

## Official requirements

| Requirement | Evidence | Status |
|---|---|---|
| Taskmaster category | Live run `coordination-3dc459c1f701cb15`; strict 55-event authority chronology | PASS live |
| Gemini 3.5+ | Gemini 3.5 Flash through the global Vertex AI endpoint | PASS live |
| Google agent framework | Google ADK 2.7 specialist agents selected by HumanWire with schema-constrained typed output | PASS live |
| Google Cloud infrastructure | Public/private Cloud Run services, Firestore, and authenticated Pub/Sub | PASS live |
| Repository + spin-up instructions | Public `codex/humanwire` branch and `infra/google/README.md` | PASS signed out |
| Architecture diagram | `submission/all-things-agentic-architecture.png` | PASS asset |
| Video (maximum 4 minutes) | 2:20 edit uses a chronological 117-second live Google run (83.6%), labeled Veo 3.1 Fast guide, Lyria 3 Pro score, and exact cloud proof | PASS master — public release URL pending final human audible review |

## Judging criteria

### Innovation & Operational Utility — 40%

- One objective triggers autonomous outreach, conflict handling, targeted interview, evidence confirmation, revision, approval, availability, and a meeting package.
- Conflict-disabled mode stays truthful and still reaches the outcome.
- Models cannot manufacture identity, evidence, approval, or readiness.

### Architectural Discipline & Tech Stack — 30%

- Public web and IAM-private worker are separate Cloud Run identities from one digest-pinned image.
- Firestore transactionally owns one active run, leases, immutable event order, and terminal binding.
- Pub/Sub uses an OIDC-authenticated dedicated push identity and idempotent delivery.
- Vertex AI uses ADC only in the worker; web cannot invoke it.
- Safe fixed logging, Unicode-normalized privacy checks, exact origin/path/body limits, failure isolation, and history-preserving rollback are covered by tests.

### Demo & Production Readiness — 30%

- Browser QA passed 1680×950, 1280×720, 600×900, and 390×844 locally with no graph collision, clipping, sub-44px control, or console error.
- Refresh, manual replay, selected-row synchronization, terminal hydration, downloads, and reset are covered by hostile controller tests.
- Local Docker build and non-root web/worker health checks pass.
- Live cloud deployment, real ADK/Gemini execution, durable authority verification, and judge-view browser acceptance pass. The bounded Veo/Lyria assets, full product capture, narration, captions, and master verification are complete; final human audible review and signed-out video-link check remain mandatory.

### Additional Google media-model proof

- Veo 3.1 Fast model `veo-3.1-fast-generate-001` produced one eight-second, 1920×1080, 24 fps, H.264 visual guide through Vertex operation `2d4f81ba-99e4-4d79-b4bf-00fc3a46f6c5`. The downloaded asset SHA-256 is `5AB8EFF9FD189B7CC12746B2955157D4247C96D4D39F5459315264A4F9A31709` and its full decode passes.
- Lyria 3 Pro model `lyria-3-pro-preview` produced the original 176.013-second instrumental score through the global Vertex interactions API. The source asset SHA-256 is `F63B61426D1AAFF386A9D22CF787B00EA405F26DFE97A2A35FB8F78F346D72C9`; the score bed is used beneath the 140-second film and is not represented as product output.
- Estimated media-generation spend is `$0.88` (`$0.80` Veo + `$0.08` Lyria). The generated visual is visibly labeled; neither asset substitutes for the chronological live product run.

## Final video master

- Path: `work/all-things-agentic-video/final/humanwire-agent-council-all-things-agentic-final.mp4` (ignored local release asset).
- Duration: 140.053333 seconds; 4,200 decoded frames.
- Video: H.264, 1920×1080, 30 fps, `yuv420p`, BT.709 limited range.
- Audio: AAC stereo, 48 kHz; integrated loudness -17.4 LUFS, true peak -3.7 dBFS, no silence longer than 1.2 seconds below -45 dB.
- Container: faststart atom order `ftyp` → `moov` → `mdat`.
- Size: 45,946,156 bytes.
- SHA-256: `086C3FF07C7F688366CADBC57211DE9B978695F93B553CED9F38A97280F8A7DE`.
- Verification: full decode 4,200/4,200; all caption midpoint and transition sheets inspected; random-seek frames match sequential decode at the hook, conflict, meeting-ready, and cloud-proof checkpoints.

## Reused-work disclosure

- New-project boundary: the repository began on 08-11-26, inside the official 08-03-26 through 08-31-26 submission period. The Google adaptation reused earlier-in-period HumanWire gateway, workflow, repository, product UI, Standard agents, and adapters at commit `b549b514a9abff0c4fd35150b6cc158b61f973c1`.
- Submission-period work from 08-16-26: Gemini/Google ADK mode, cloud repository/dispatch/progress/web/worker adapters, durable browser mode, cloud E2E and hardening, deployment package, diagram, and All Things Agentic submission materials.

## Live Google proof

- Project: `humanwire-agentic-2026`; billing enabled with a bounded budget alert policy.
- Public URL: `https://humanwire-web-wjjhjrgnyq-uc.a.run.app`.
- Web revision: `humanwire-web-00024-gvl`; worker revision: `humanwire-worker-00024-fcv`; both receive 100% traffic.
- Shared image digest: `sha256:beeb4c38559c3fedf771c10efaf621ef100f5015f942c90ecf6aeb5ea995ae7f`.
- IAM: web permits `allUsers`; worker permits only `humanwire-push@humanwire-agentic-2026.iam.gserviceaccount.com`; anonymous worker request returned HTTP 403.
- Bounded scale: both services max one instance; web concurrency 20; worker concurrency 1.
- Verified run: `coordination-3dc459c1f701cb15`, complete, 55/55 saved events, terminal state `meeting_ready`, meeting window `2026-08-18T15:00:00Z`–`15:30:00Z`, zero rejected model responses.
- Authority ordinals: request 1 → outreach 4 → conflict 25 → targeted interview 31 → confirmed evidence 35 → proposal 36 → revision 43 → approval 49 → availability 51 → meeting ready 55. Anika Rao's rollback evidence is saved at event 34.
- Canonical SHA-256: snapshot `9817b9383ac529ead7f054a78cbd7df3bfbcc9cf5ee3f300c48d2d85cf3163ae`; JSON `55f810c9819f7566e03d2a3143945e56ea8e25415888b73013d391fb395adec8`; CSV `437ccaf0ba055f4c54e318699f93943b681a3c72c8ae45b2adebe44be788098a`.
- Repository verifier `verify_cloud_authority_story` passed exact snapshot/JSON/CSV parity and chronology.
- Browser acceptance at 1280×720 and 390×844: complete hydration, one selected replay path, synchronized selected data row, both downloads, no navigation, no horizontal overflow, no sub-44px visible control, and no console warning/error.
