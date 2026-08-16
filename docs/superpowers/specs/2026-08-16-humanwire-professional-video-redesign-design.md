# HumanWire professional submission video redesign

## Objective

Replace the current HumanWire submission video with a polished, product-first 16:9 demo that a first-time Devpost judge can understand without prior context. The replacement must use the authentic HumanWire screen recording as the primary evidence, explain the decision workflow clearly, and feel like a concise enterprise product launch rather than a slideshow.

The target is an 80-second, 1920x1080, 30 fps video. The existing public video remains unchanged until the replacement passes all review gates and its public upload is verified.

## Non-negotiable truth and safety boundaries

- Product footage is never passed through a generative video model. UI text, events, controls, and chronology remain pixel-authentic.
- Generated humans and characters are visual guides or software-agent illustrations, never represented as real stakeholders or recorded external conversations.
- Product footage retains the visible disclosure `Standard agents · no external messages`.
- The video may describe Caspian as the configurable transport boundary. It may not claim recorded Telegram or email provider delivery.
- No credential, provider response, local path, private fact, email address, internal token, or hidden browser content may enter the rendered video or tracked repository.
- Total production spend is capped at USD $10.00. The earlier ambiguous presenter reservation is treated conservatively as USD $1.00, leaving a maximum new reservation of USD $9.00.

## Chosen production stack

### Authentic product layer

The real public-product capture is the visual authority. Product moments are cut chronologically and displayed full-screen or inside a large 16:9 stage occupying at least 72% of the frame. Crop-and-zoom operations may enlarge an existing region but must not redraw, replace, or generate interface pixels.

### Generated visual layer

OpenRouter is the only paid provider boundary.

1. `kwaivgi/kling-v3.0-std` creates one six-second, silent, image-guided visual-guide shot. The shot uses a fictional presenter, restrained movement, a premium navy studio, and no text, logos, UI, or additional people.
2. `bytedance/seedance-2.0` creates one six-second, silent, reference-guided software-agent coordination shot. It shows distinct fictional agent roles connected through one cyan path, without chat messages, channel UI, logos, or real-person implications.
3. `google/gemini-3.1-flash-tts-preview` produces the narration. The directed delivery is confident, warm, and conversational at 135–160 spoken words per minute. If that model fails before producing a valid asset, `minimax/speech-2.8-hd` is the single fallback.

Each generated visual receives one initial job and at most one replacement job if it fails the visual gate. Narration receives one initial job and at most one correction job. The spend ledger reserves before every POST and refuses any request that could raise cumulative exposure above USD $10.00.

### Composition layer

Remotion renders the motion-design timeline locally. It owns:

- full-screen product crops and eased camera moves;
- animated titles and keyword emphasis;
- fictional agent portrait overlays and role labels;
- restrained cyan flow lines that point to the corresponding real UI state;
- captions, transitions, sound cues, and the final call to action;
- a locally synthesized low-volume ambient bed, avoiding an external music-license dependency.

Generated video is never used as an editor. It supplies two short standalone visual moments; Remotion preserves and presents the evidence.

## Storyboard and timing

| Time | Story | Visual treatment |
|---|---|---|
| 0–6s | Decisions fail when objection, evidence, and authority do not meet. | Moving fictional visual guide, HumanWire title, one sentence of kinetic type. |
| 6–14s | A user asks HumanWire to coordinate a launch decision. | Full-screen real composer; objective, role, stakeholder set, and Start action receive sequential focus. |
| 14–26s | HumanWire selects the minimum necessary conversations through one gateway. | Real live graph and conversation panes; six-second software-agent cutaway appears briefly as a transition, not evidence. |
| 26–44s | A risk creates conflict; a focused interview produces confirmed evidence and revises the proposal. | Full-screen chronological product footage with Anika, conflict, interview, evidence, and revision callouts synchronized to saved events. |
| 44–56s | Approval precedes availability and the meeting package. | Sofia approval, Daniel availability, and Meeting package ready shown in strict saved-event order. |
| 56–68s | Replay and exports make the decision inspectable. | Real Previous, Next, Play, Follow live, JSON, and CSV interactions; selected event, graph, conversation, and data row remain synchronized. |
| 68–75s | Caspian is the configurable consented-delivery boundary; this public run uses Standard agents. | Compact animated architecture strip over authentic product footage with the truth disclosure fully visible. |
| 75–80s | HumanWire turns one mandate into the right conversations and a decision-ready meeting. | Clean CTA with product and repository URLs. |

No static disclosure card remains on screen. No shot is visually unchanged for more than three seconds unless a product interaction is actively being explained.

## Visual and audio direction

- Palette: HumanWire navy, cyan, restrained lime success accents, and warm neutral skin tones.
- Typography: one modern sans-serif family with a three-level hierarchy; no text smaller than 28 rendered pixels at 1080p.
- Captions: authored, maximum two lines and 42 characters per line, positioned outside judge-critical UI.
- Product text: enlarged through crops rather than recreated as overlays.
- Transitions: short masked wipes, focus pulls, and eased stage moves; no generic slideshow dissolves.
- Narration loudness: approximately -16 LUFS integrated, true peak no higher than -1.5 dBTP.
- Ambient bed: approximately 12 dB below narration, with ducking during dense explanation.
- Sound cues: only subtle start, conflict, confirmation, approval, and completion accents.

## Data flow and file boundaries

1. Read-only catalog and credit GETs confirm model capability and budget.
2. Paid requests create ignored assets beneath `work/caspian-video-v2/generated/` and write only sanitized status, model, reservation, actual cost, and output hash to an atomic ignored ledger.
3. The compositor reads approved generated assets, authentic product clips, narration, and the locked timeline specification.
4. Review frames, transcripts, loudness reports, and candidate masters remain beneath ignored `work/caspian-video-v2/` paths.
5. Only source, tests, captions, manifest, script, and evidence documentation may be committed. Binary media and credentials remain untracked.

## Failure behavior

- A failed or malformed provider response is sanitized and cannot expose the authenticated request or provider body.
- A generated clip with facial, hand, text, logo, consistency, or motion defects is rejected. If its one replacement also fails, the shot is removed and replaced by a Remotion-native kinetic product opening; a poor AI clip is never forced into the final video.
- If TTS fails twice, the video is not uploaded with the existing Windows Zira narration. The candidate remains blocked for a professional voice asset.
- If any authentic product crop hides required truth text or changes event chronology, the render fails.
- The existing YouTube and Devpost entries are not changed until the replacement upload passes public verification.

## Acceptance gates

### Professional editor gate

- Duration is 78–82 seconds; output is 1920x1080, H.264, yuv420p, 30 fps, AAC stereo 48 kHz, faststart.
- The real product is the dominant visual for at least 70% of runtime.
- The first eight seconds communicate the problem and product name.
- No static shot exceeds three seconds without purposeful internal motion.
- Every caption is readable, correctly timed, and collision-free.
- Narration pacing, loudness, silence, clipping, and transcript checks pass.
- Random-seek and continuous-decode review find no corrupted frames or unstable generated faces.

### First-time audience gate

Without reading submission copy, a reviewer can answer:

1. What problem does HumanWire solve?
2. What request starts the workflow?
3. Why does conflict trigger a focused interview?
4. How does confirmed evidence change the proposal?
5. Why do approval and availability occur in that order?
6. What does replay/export prove?
7. What role does the Caspian gateway play, and what was not externally recorded?

### Truth, privacy, and submission gate

- Saved-event chronology matches the product authority story.
- `Standard agents · no external messages` remains visible and legible during product footage.
- No fabricated Telegram/email proof or real-person claim appears.
- Privacy and secret scans return no unsafe matches.
- Tracked media count remains zero.
- The new YouTube URL returns signed-out HTTP 200 before Devpost is updated.
- Devpost is updated and resubmitted only after the new public video passes the final check.

## Rejected approaches

- A fully generative 80-second video is rejected because models can mutate product text, controls, and event chronology.
- A presenter-led HeyGen video is rejected for this deadline because it would reduce product evidence and introduce a second paid account boundary. If OpenRouter cannot produce an acceptable six-second guide, the Remotion-native kinetic product opening is used instead.
- Re-editing the current slideshow structure is rejected; its long disclosure cards, small product pane, still-image motion, and synthetic desktop voice are the core creative failure.
