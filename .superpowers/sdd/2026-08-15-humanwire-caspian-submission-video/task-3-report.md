# Task 3 report — presenter, stakeholder motion, and narration

## Status

Partial, with safe visual fallbacks accepted. Both required reference stills and both approved-duration local fallback clips exist and passed review. Narration is blocked because the single free validation request failed before producing an MP3. Neither paid video job completed; the gated CLI recorded a presenter reservation and then failed with a sanitized provider error before reaching the stakeholder job. No paid retry or replacement job was submitted.

## Spend authority and cost

The user explicitly authorized up to USD $3.00 total for exactly one 6-second Veo 3.1 Fast job and one 8-second Seedance 2.0 Fast job, with no automatic paid retries. The gated invocation used the exact flags `--confirm-paid-generation --approve-spend-usd 3.00` once.

Actual completed OpenRouter cost recorded locally: **USD $0.00**. The ledger contains one presenter reservation with `cost_usd: 0`, no completed entry, and no job ID. No stakeholder entry exists. No provider response body or job ID is reproduced here.

## Request accounting

- Image generation: three built-in image-generation calls. The presenter passed on the first call. The first stakeholder result was rejected because it contained eight cards; one targeted edit produced the accepted exactly-seven-card still. These calls were not OpenRouter video jobs.
- Free TTS: exactly one opening-sentence validation POST using `deepgram/flux-tts:free`. It failed closed with the sanitized client error `OpenRouter request failed`; no MP3 was created, so audible inspection was impossible. Exactly zero seven-section TTS requests followed. No paid or alternate synthetic voice was invoked.
- Paid video: the gated CLI was invoked exactly once. The local reservation proves it entered the presenter branch, but the sanitized error boundary does not distinguish a failure in the post-reservation credit GET from a failure in the presenter video POST. Therefore the defensible provider count is zero completed paid jobs, zero stakeholder POSTs, and at most one presenter POST. The presenter reservation permanently prevents a retry. The CLI aborted before Seedance and its all-or-nothing guard prevents a safe resume.

## Exact paid requests

Before the gated run, a RED/GREEN regression fixed `_approved_specs()` so the CLI uses the approved prompts verbatim and attaches the approved first-frame paths:

```text
Six-second 16:9 cinematic commercial shot based on the provided first frame. A fictional professional visual guide looks into camera with calm confidence, makes one subtle open-hand gesture, and holds a natural attentive expression. Slow controlled camera push-in, premium dark enterprise studio, restrained cyan accent lights, realistic human motion, no speech, no lip-sync emphasis, no text, no logos, no UI, no extra people, no camera shake.
```

```text
Eight-second 16:9 motion-graphics shot based on the provided first frame. Seven illustrated enterprise software-agent role cards activate one after another around a central cyan coordination path; fine connection lines flow from role to role and converge toward a decision node. Smooth professional motion, coherent navy and cyan palette, cards and faces remain stable, no speech bubbles, no typed messages, no text mutation, no logos, no implication of real people or live communication.
```

The request contracts remain 6 seconds / 720p / 16:9 / audio disabled for `google/veo-3.1-fast` and 8 seconds / 720p / 16:9 / audio disabled for `bytedance/seedance-2.0-fast`.

## Reference stills

Both accepted stills are 1672×941 RGB PNGs generated with the built-in image-generation skill and copied into the ignored repository-owned work tree.

| Asset | SHA-256 | Bytes | Inspection |
|---|---|---:|---|
| `work/caspian-video/references/presenter.png` | `7430eb1872fe7196c58b9b5049884d8062dfad13008186d6e6d4d0d0b4dd43be` | 1,522,183 | Accepted: one fictional South Asian presenter, intact face/hands, clean left title space, no text, logo, UI, microphone, clipping, or extra person. |
| `work/caspian-video/references/stakeholders.png` | `427117b688c77a8ce29479fe5a6b9b384bbbf4365200baadf8ba8a16412b9bce` | 1,667,332 | Accepted after one rejection/edit: exactly seven distinct illustrated agent cards, central cyan path, no text, logo, chat UI, duplicated face, clipping, or implied live communication. |

## Accepted clips and fallback decisions

The presenter provider branch failed without a completed asset, so the exact mandated six-second `ffmpeg` zoompan fallback was used. Because the CLI aborted before it could safely submit Seedance and the existing presenter reservation makes rerunning the command impermissible, the exact mandated eight-second stakeholder fallback was also used. No replacement paid job was submitted.

First, middle, and final frames for each clip were extracted under `work/caspian-video/review/generated/` and all six were inspected at original resolution. The frames preserve stable faces/cards and coherent navy/cyan styling; there is no text mutation, extra face, channel-like UI, proof-footage resemblance, clipping, logo, watermark, or artifact. The only motion is the brief-approved controlled zoom.

| Accepted asset | Duration | Video | SHA-256 | Bytes |
|---|---:|---|---|---:|
| `work/caspian-video/approved/presenter.mp4` | 6.000 s | H.264, 1280×720, 30 fps, yuv420p | `880a56370cfaabdbfa4321068e356a0898306a42edd3d165da8c30e2bf3c7347` | 391,852 |
| `work/caspian-video/approved/stakeholders.mp4` | 8.000 s | H.264, 1280×720, 30 fps, yuv420p | `c0b7334e85650d1d162543db44f283b13acbb69c0f4df78c793e5636f585e553` | 829,016 |

The corresponding `work/caspian-video/generated/*.mp4` hashes are identical to the accepted copies.

## Narration

No narration clip is accepted or present. The required validation MP3 was never written, so the seven files `00-presenter_hook.mp3` through `06-closing_card.mp3` were intentionally not requested. The brief requires locally recorded narration after this failure mode; no such recording was available in the repository, and generating a paid or alternate synthetic voice would exceed authority.

## TDD and verification

The pre-POST audit found that the Task 2 CLI used abbreviated prompts and omitted both first frames. A new regression first failed:

```text
FAILED test_approved_specs_use_the_authorized_prompts_and_first_frames
1 failed, 22 passed
```

After the production fix:

```text
41 passed
All checks passed!
```

Commands used for the GREEN check were:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_openrouter.py tests\humanwire\test_caspian_video_models.py -q
.\.venv\Scripts\ruff.exe check scripts\caspian_video tests\humanwire\test_caspian_video_openrouter.py
git diff --check
```

The full `tests/humanwire` suite also completed at 100% with exit code 0.

`ffprobe` confirmed exact 6.000-second and 8.000-second durations, H.264 video, 1280×720, 30 fps, and yuv420p. The ignored-media boundary was retained; Git does not track any PNG, MP3, MP4, review frame, ledger, provider body, secret, or job ID.

## Privacy scan

The credential file remained external at `..\..\.env.video`; its values were never printed, copied, committed, or recorded in the report. Provider response bodies were not printed or retained. The final filename-only key-pattern scan excluded external environment files and binary media. Its only match was the OpenRouter client regression-test file because that test deliberately constructs an authorization header from the literal `PRIVATE-OPENROUTER-SENTINEL`; this is an intentional fake value used to verify secret redaction, not a credential. No Task 3 output or report matched.

## Tracked changes and commit

Tracked scope is limited to the required exact-request fix, its regression test, and this report:

- `scripts/caspian_video/openrouter.py`
- `tests/humanwire/test_caspian_video_openrouter.py`
- `.superpowers/sdd/2026-08-15-humanwire-caspian-submission-video/task-3-report.md`

The commit SHA for this tracked Task 3 change is reported in the task handoff because a commit cannot contain its own final SHA. `.superpowers/brainstorm/` was preserved exactly and remains unrelated/untracked.

## Concerns

1. Narration is incomplete and blocks final composition until the user supplies seven locally recorded, manifest-aligned clips (or grants separate authority for another voice path).
2. The paid client reserves a job before a credit GET and records only the reservation, so a sanitized failure cannot prove whether the video POST occurred. This report conservatively states “at most one presenter POST.”
3. The generation loop is all-or-nothing: a fenced presenter failure aborts before Seedance, while the required existing-ledger refusal prevents resuming the second approved job. Both visual fallbacks are safe and accepted, but neither paid model produced an asset.
