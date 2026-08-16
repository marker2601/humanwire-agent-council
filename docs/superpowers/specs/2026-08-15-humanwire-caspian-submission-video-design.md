# HumanWire Caspian Submission Video Design

**Date:** 2026-08-15
**Status:** Approved direction; implementation requires a separate approved plan
**Target:** Public Caspian Devpost video, 90–110 seconds, 16:9, 1920×1080

## Objective

Create a concise, professional demo that proves HumanWire coordinates a real decision workflow across Caspian-connected Telegram and email while making the product's larger multi-agent process easy to understand.

The video must satisfy two goals at once:

1. show genuine, judge-verifiable Caspian behavior on at least two channels; and
2. explain the full HumanWire workflow with a polished presenter, illustrated stakeholder characters, and the live Decision Room visualization.

The finished video must never use generated footage to imply that an external message, human response, or provider action occurred when it did not.

## Constraints and Truth Boundary

- The public video must be no longer than three minutes. The editorial target is 105 seconds.
- Telegram and email proof must come from one real, consenting, operator-owned Caspian run.
- Real channel footage may be cropped, sequenced, captioned, and redacted, but its message content and chronology must not be fabricated or rearranged into a false result.
- The deployed HumanWire product is an interactive Standard-agent workflow and visibly states **Standard agents · no external messages**. It explains and replays the complete workflow; it is not presented as the source of live channel delivery.
- The AI presenter is a fictional visual guide. Illustrated stakeholder portraits represent software agents and roles, not real participants.
- Generated presenter or character footage must never cover the channel identity, timestamp, provider state, or other judge-critical proof in the real Caspian shots.
- Credentials, email addresses, Telegram identifiers, provider IDs, database coordinates, tokens, and private interview content must not appear in any frame, subtitle, filename, metadata, or narration.

## Approaches Considered

### A. Hybrid real proof plus AI-assisted explanation — chosen

Use real Caspian Telegram/email recordings for the proof, the real HumanWire product for the complete workflow visualization, and short generated presenter/character shots for clarity and polish.

This is the strongest approach because it is visually engaging without weakening the authenticity of the working demo.

### B. Fully generated product story — rejected

A completely generated video could look polished but would resemble staged evidence and would not prove that Caspian or the two channels actually worked.

### C. Screen recording only — fallback

A direct screen recording is truthful and remains the safe fallback if generation fails. It is less effective at quickly explaining why the agents behave differently and how the workflow fits together.

## Narrative and Storyboard

### 0–8 seconds — Problem and hook

- Show a short fictional professional presenter shot.
- Narration: teams lose decisions when outreach, objections, evidence, approvals, and scheduling live in separate threads.
- On-screen title: **HumanWire — coordination that reaches a decision**.
- Small disclosure: **Visual guide**.

### 8–22 seconds — Real Telegram mandate and authorization

- Show an operator sending the mandate in Telegram.
- Show HumanWire/Caspian returning the preview and the operator sending the explicit `GO` authorization.
- Keep the Caspian surface and chronology visible.
- Caption: **Recorded Caspian run · Telegram**.
- Redact destination details and tokens while retaining enough surrounding UI to verify the channel and interaction.

### 22–40 seconds — Real email interview and confirmation

- Show the resulting email outreach and targeted interview questions.
- Show the consenting operator response and explicit evidence confirmation.
- Caption: **Same recorded Caspian run · Email**.
- Use a clean side label to connect Telegram authorization to email evidence without inserting generated messages.

### 40–70 seconds — HumanWire Decision Room

- Cut to the deployed product at `https://secondsignal.vercel.app/`.
- Start the launch-decision template and show the coordination graph building as saved events arrive.
- Brief illustrated portraits introduce the relevant roles: Maya Chen, Nora Jensen, Priya Shah, Marcus Reed, Anika Rao, Sofia Alvarez, and Daniel Brooks.
- The stakeholder illustrations are overlays beside the real product, never replacements for its graph or saved events.
- Follow the saved story through outreach, Anika's risk conflict, targeted interview, confirmed evidence, and the revised proposal.
- Keep the product's **Standard agents · no external messages** boundary visible or explicitly restate it in the caption.

### 70–90 seconds — Authority and outcome

- Show Sofia's authority-bound approval only after confirmed evidence and the revised proposal.
- Show Daniel's availability only after approval.
- End the product sequence on the meeting-ready package, with the selected-event graph, conversation, and data panes visibly synchronized.
- Use one brief motion graphic to connect conflict → evidence → proposal → approval → availability → meeting.

### 90–105 seconds — Proof summary and call to action

- Show replay controls and the final JSON/CSV evidence downloads.
- Return to the fictional presenter for a short closing line.
- Final card:
  - **HumanWire**
  - **One mandate. The right conversations. A decision-ready meeting.**
  - public product URL and public repository URL
- Optional footer: **Live channel proof shown from one consenting operator-owned run.**

## Visual System

### Presenter

- One fictional professional presenter with a neutral business appearance and a simple dark studio background.
- Use the presenter only for the opening and closing, totaling no more than 12–16 seconds.
- Prefer controlled voice-over added in post rather than relying on generated spoken dialogue. This avoids lip-sync drift, pronunciation errors, and costly retries.

### Stakeholder characters

- Use one consistent illustrated enterprise portrait style for all stakeholder roles.
- Portraits appear as small role cards or a short moving cast montage.
- Names and roles must match the product catalog exactly.
- Do not depict these characters typing real channel messages. Their motion explains the software-agent roles only.

### Product and channel footage

- Preserve legible UI at 1080p.
- Use restrained zooms and highlights rather than decorative transitions.
- Keep channel footage recognizable and product footage readable for judges viewing on a laptop.
- Captions use a high-contrast lower-third style and remain inside title-safe bounds.

## Generation Stack

### OpenRouter video

- `google/veo-3.1-fast` for one short presenter shot, selected for reliable 16:9 output and controlled duration.
- `bytedance/seedance-2.0-fast` for one short stakeholder/cast motion shot, preferably image-to-video from a stable reference image.
- Submit at most one paid generation job per selected video model. Do not automatically retry a failed or unsatisfactory job.
- Generated clips are explanatory B-roll only; they are not Caspian evidence.

### Voice

- Primary path: a short OpenRouter speech request using `deepgram/flux-tts:free`, after a one-line validation of the chosen voice and pronunciation.
- Fallback: local narration or another low-cost OpenRouter speech model only after reviewing its quoted cost and voice sample.
- Captions are authored from the final narration script, not inferred from generated audio.

### Local assembly

- Store working media only under ignored `work/caspian-video/` paths.
- Capture the deployed product and real channels at native resolution.
- Redact sensitive pixels before composition.
- Assemble deterministically with FFmpeg into H.264/AAC MP4, 1920×1080, 30 fps.
- Use subtle sound design only; narration and UI proof remain intelligible without music.

## Spend Guardrail

- Maximum approved design budget: **USD $3.00 total** for OpenRouter video and speech jobs.
- This document does not authorize spending. The user must explicitly approve the $3 ceiling before the first paid `POST /api/v1/videos` request.
- Check available OpenRouter credit immediately before generation.
- Run one job per chosen video model and no automatic retries.
- If either job fails, use the truthful screen-recording fallback rather than spending again without approval.

## Failure and Fallback Behavior

- If the presenter generation fails, replace it with a branded title card plus narration.
- If stakeholder animation fails, use consistent static illustrated role cards with simple local motion.
- If TTS fails, use a locally recorded voice-over or a silent caption-led cut.
- If the live Caspian proof is incomplete, do not construct a success story from the public Standard-agent product. Finish the real channel run or explicitly label the missing proof as pending.
- If any captured frame contains sensitive data that cannot be safely redacted, omit the frame.

## Verification Checklist

Before publication:

- Verify the MP4 is 1920×1080, 16:9, H.264/AAC, and under 180 seconds with `ffprobe`.
- Watch the complete exported file with sound from beginning to end.
- Confirm the real run visibly includes both Telegram and email.
- Confirm explicit authorization precedes outreach and evidence confirmation precedes approval.
- Confirm the public product is never described as delivering external messages.
- Confirm AI presenter and stakeholder imagery cannot be mistaken for live provider proof or real participants.
- Scan every frame, caption, filename, and media metadata for credentials and private identifiers.
- Verify captions are readable and synchronized.
- Verify the public product, repository, and final video links work while signed out.
- Upload the video publicly and replay the hosted copy before placing its URL in Devpost.

## Deliverables

- Final public MP4, 90–110 seconds.
- Final narration script and timed captions.
- Shot list identifying real proof, product footage, and explanatory visuals.
- One safe thumbnail frame.
- Public video URL suitable for Devpost.
- A short evidence note recording the real two-channel run used in the edit without publishing private coordinates.
