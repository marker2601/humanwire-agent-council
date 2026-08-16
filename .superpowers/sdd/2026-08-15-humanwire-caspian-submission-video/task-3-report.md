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

## Offline narration completion addendum

This addendum supersedes the earlier partial-status and missing-narration statements. Final Task 3 status is **DONE_WITH_CONCERNS**: all locally required media now exists and validates, with the accepted visual fallbacks and seven completed narration files. No additional provider/API call was made, and the paid ledger was not altered.

### Local voice method

Narration was produced entirely offline with the installed Windows `System.Speech`/SAPI engine and `Microsoft Zira Desktop` (`en-US`, female). The repository's existing `_script_sections()` parser supplied exactly the seven section prose strings from `submission/caspian-video-script.md`; headings and timing metadata were excluded. No script word was added, removed, or rewritten.

The first render used SAPI rate `+2`. The opening and stakeholder-role sections exceeded their six-second slots, so only those two were rerendered at SAPI rate `+5`; the other five retained rate `+2`. No time-stretch was ultimately necessary. Each local PCM WAV was converted with FFmpeg/libmp3lame to mono 44.1 kHz, 128 kbps MP3 and normalized with `loudnorm=I=-16:TP=-1.5:LRA=11`. WAV intermediates were removed after validation, leaving exactly the seven required MP3 files beneath the ignored repository-owned narration directory.

### Narration assets and validation

| Asset | Slot | Duration | Mean / peak | Silence regions ≥0.5s | SHA-256 |
|---|---:|---:|---:|---:|---|
| `00-presenter_hook.mp3` | 6s | 5.467937s | -16.2 / -1.9 dB | 0 | `dd8b8234ec11ad0bc1cfea09d194cb4b3355cfcdc7fd17793f2cec334499ae94` |
| `01-telegram_authorization.mp3` | 16s | 8.387982s | -16.6 / -1.9 dB | 2 | `28a92beb6970b91192528f584d4f93ba24e42962efe42bdd2df77552648789d4` |
| `02-email_evidence.mp3` | 16s | 9.777506s | -16.9 / -1.9 dB | 1 | `a99685ce5900a24ab6e34bc7210fd8675f0c6571bde483729bd6c96938ac27b8` |
| `03-stakeholder_roles.mp3` | 6s | 5.478186s | -17.1 / -1.9 dB | 0 | `f26b6d554e3539006c6b46e4ce9eb34fdf4df719c7a792516b2f8e3d663c59f8` |
| `04-decision_room.mp3` | 34s | 11.502041s | -16.4 / -1.9 dB | 3 | `6afbb5f89699a59552d0efef6d1d4bc9907a56d1b122c201d723cb8f0095f7c4` |
| `05-replay_and_downloads.mp3` | 20s | 15.931156s | -16.1 / -1.9 dB | 3 | `3aa6fab1ae56dc6eb40d0e3cba63fa07d7a3e43820133467901c8d0f95a8c92f` |
| `06-closing_card.mp3` | 7s | 5.473424s | -16.6 / -1.9 dB | 1 | `536bf78636e70df7ec6d07d80c716717dcfc44cac7146b270d0df744bcdfb475` |

All seven files decode successfully as MP3, mono, 44.1 kHz audio and fit within their manifest durations. FFmpeg waveform validation found non-silent program audio in every file, consistent normalized levels, no clipping, and only natural pause regions. Direct audible inspection is not available to the model in this tool environment; exact parser provenance plus codec, duration, decode, waveform, level, and silence checks are the truthful substitute. This is the remaining quality concern for later human review.

### Final side-effect and cost confirmation

- Additional OpenRouter/provider calls after the failed validation/generation phase: **0**.
- Additional cost: **USD $0.00**; Task 3 actual total remains **USD $0.00**.
- Paid ledger: unchanged, with the original presenter reservation and no stakeholder entry.
- Git-tracked binary media: none; all seven MP3s, two stills, two generated clips, two approved clips, review frames, and the ledger remain ignored/local-only.
- `.superpowers/brainstorm/`: preserved unchanged and untracked.

## Fix round 1/5 — authorization and independent narration-quality gate

### User resolution and contract amendment

The user explicitly ruled that whatever is needed to complete and submit the project is authorized without further approval prompts. This resolves the earlier concern about whether the local Microsoft Zira/SAPI fallback satisfied the brief's “locally recorded” wording. The tracked production plan now states the narrow rule: after the free provider path fails and the user authorizes the fallback, narration may be human-recorded or synthesized entirely offline; no paid voice request or additional provider call is allowed.

The user subsequently established a standing **USD $10.00 total production/submission cap** without another approval request. The plan records that superseding authorization while retaining the already-completed Task 3 client's narrower historical USD $3.00 video-job fence, immutable reservation ledger, and no-retry rule. This fix made zero provider/API calls and cost USD $0.00.

### Professional pacing contract

Objective review showed that forcing 22 opening words into 5.468 seconds was approximately 241 spoken words per minute and too aggressive for a professional gate. The timeline was rebalanced without changing a spoken word or the 105-second final duration:

- presenter hook: 0–8 seconds (was 0–6);
- Telegram authorization: 8–22 seconds / 14 seconds (was 6–22 / 16 seconds);
- stakeholder roles: 38–46 seconds / 8 seconds (was 38–44 / 6 seconds);
- Decision Room: 46–78 seconds / 32 seconds (was 44–78 / 34 seconds).

All later boundaries remain unchanged. The opening and stakeholder narration were rerendered with the same offline Microsoft Zira Desktop voice at SAPI rate `+2`, replacing their prior rate `+5` versions. Their final durations are 7.772653 seconds and 7.653424 seconds, both inside their eight-second slots and approximately 170 and 118 spoken words per minute respectively. No time-stretch or script edit was used.

Updated asset hashes:

| Asset | Duration / slot | Mean / peak | SHA-256 |
|---|---:|---:|---|
| `00-presenter_hook.mp3` | 7.772653s / 8s | -16.5 / -1.9 dB | `5c5eac1a019503ab45fd7b8568412bffa760fd3cbb46c8a868023483c524bbab` |
| `03-stakeholder_roles.mp3` | 7.653424s / 8s | -17.2 / -1.8 dB | `7e99abfd7ea837afa41cb0aee5b5ccb7375652ddde0446f6f75213f210d5033d` |

The other five narration hashes and durations are unchanged; their new slot limits are 14, 16, 32, 20, and 7 seconds and all continue to fit.

### Independent offline recognition and acoustic evidence

Windows exposes `Microsoft Speech Recognizer 8.0 for Windows (English - US)`. A separate offline `System.Speech.Recognition` dictation pass consumed 16 kHz PCM copies of all seven MP3s. It recognized the complete phrase structure, but confidence was only 0.08–0.52 and it systematically mangled proper/product names (`HumanWire`, `Anika`, JSON/CSV), so it is advisory rather than a reliable acceptance oracle. The initially fast stakeholder clip scored 0.080 confidence. After the natural-rate rerender it scored 0.456 and produced:

```text
Each stakeholder agent has a specific role in form of knowledge answer should approve or provide availability
```

That is 5 edit operations across 15 expected normalized words (33.3% WER), with the errors concentrated in the enumerated role words. The opening's natural-rate transcript retained both sentences and all key concepts but still had proper/function-word substitutions. This confirms that Windows' legacy recognizer is useful for comparative pacing but not sufficiently accurate for semantic certification.

The objective gate therefore also verified every clip independently:

- exact prose source: repository `_script_sections()` parser, with per-section SHA-256 provenance retained in the local audit;
- successful full decode as mono 44.1 kHz MP3;
- duration below the manifest slot;
- normalized mean level from -17.2 to -16.1 dB and peak from -1.9 to -1.8 dB;
- zero clipped samples in every decoded clip;
- leading silence 0.0751–0.0853 seconds and trailing silence 0.5791–0.5910 seconds, proving neither edge is truncated;
- exactly seven narration MP3s and zero WAV intermediates in the narration directory.

A locally playable concatenated review file was created at `work/caspian-video/review/narration-review.mp3`: MP3, mono 44.1 kHz, 69.998186 seconds, 1,121,009 bytes, with a half-second pad between clips. Media remains ignored and untracked. Human listening is deferred to the required final full-video review; unreliable offline ASR does not block composition.

### TDD and commands

RED first proved both missing timing allocations and the missing authorization language:

```text
2 failed, 17 passed
```

The later stakeholder timing test also failed against the old 6/34-second allocation before the 8/32-second correction. Commands used for the final gate:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_models.py tests\humanwire\test_caspian_video_openrouter.py -q
.\.venv\Scripts\ruff.exe check scripts\caspian_video tests\humanwire\test_caspian_video_models.py tests\humanwire\test_caspian_video_openrouter.py
git diff --check
```

Final result: `42 passed`; Ruff reported `All checks passed!`; `git diff --check`, the seven-file manifest-duration gate, and the zero-tracked-media check all passed. No binary media, secret, provider response, or ledger was staged.

Fix round 1 contract/test commit: `dca7d81ba1f06503dceeb8f2514101be7cc60204`.

## Fix round 2/5 — Whisper intelligibility and 8-second visual coverage

### Delegated acceptance and independent ASR

The user explicitly delegated all professional/audience completion decisions and prohibited further approval prompts. Because a human listening turn is unavailable inside this agent workflow, the weak legacy Windows recognizer was replaced with the strongest practical independent no-cost local gate available here: transient `faster-whisper==1.2.1` with the English `small.en` model, CPU int8 inference. The package was installed only under ignored `work/caspian-video/cache/python/`; the model was downloaded by unauthenticated GET into ignored `work/caspian-video/cache/models/`. No dependency file changed, no paid/provider inference or API POST occurred, and no credential was used.

Command shape:

```powershell
.\.venv\Scripts\python.exe -m pip install --target work\caspian-video\cache\python faster-whisper==1.2.1
$env:PYTHONPATH = "<ignored faster-whisper target>;<repository root>"
$env:HF_HOME = "<ignored model cache>"
.\.venv\Scripts\python.exe work\caspian-video\cache\run_asr.py
```

Expected and actual text were normalized identically: lowercase alphanumerics, `e-mail` → `email`, and `Human Wire` → `HumanWire`. No initial prompt or expected-text grammar biased the transcription. Results:

| Clip | Whisper transcript result | WER | Key-name/concept verdict |
|---|---|---:|---|
| `00-presenter_hook.mp3` | Exact normalized prose | 0.00% | objection, evidence, authority, workflow PASS |
| `01-telegram_authorization.mp3` | Exact after Human Wire normalization | 0.00% | HumanWire, Telegram, Caspian, preview, operator, GO PASS |
| `02-email_evidence.mp3` | Exact after Human Wire normalization | 0.00% | email, unresolved questions, evidence, explicit confirmation PASS |
| `03-stakeholder_roles.mp3` | Exact normalized prose | 0.00% | all six role verbs and availability PASS |
| `04-decision_room.mp3` | Exact after Human Wire normalization | 0.00% | Decision Room, Anika, risk constraint, interview, evidence, revised proposal PASS |
| `05-replay_and_downloads.mp3` | `Sofia` recognized as conventional spelling `Sophia`; all other words exact | 2.38% | Sofia/Sophia accepted spelling variance; Daniel, approval, availability, JSON, CSV, public product, external delivery PASS |
| `06-closing_card.mp3` | Exact after Human Wire normalization | 0.00% | mandate, conversations, meeting, confirmed decisions PASS |

Aggregate result: 1 edit across 174 expected normalized words, **0.57% WER**; language probability was 1.0 for every clip. The automated key-concept gate passed all seven clips. The only variance is explainable proper-name spelling; exact captions retain `Sofia`. No ordinary-language rerender was required.

Objective final audio checks remain clean: 117.6–171.8 spoken words per minute, -17.2 to -16.1 dB mean level, -1.9 to -1.8 dB peak, zero clipped samples, 0.0751–0.0853 seconds leading silence, and 0.5791–0.5910 seconds trailing silence. All seven files decode completely and fit their manifest slots, so endings are neither clipped nor truncated. The final full-video professional/audience review will recheck narration synchronization and exact captions in context; the user has delegated that final acceptance and no intermediate approval is required.

### Presenter visual duration repair

RED: the manifest presenter hook is eight seconds while the accepted presenter fallback was only 6.000 seconds. A focused unit contract first failed because `validate_approved_visual_durations()` did not exist; its input then demonstrated that presenter `6` / stakeholders `8` must be rejected while `8` / `8` passes.

The accepted six-second presenter was deterministically extended with FFmpeg `tpad=stop_mode=clone:stop_duration=2`, preserving the original subtle zoom and cloning its accepted final frame for the last two seconds. Both `work/caspian-video/generated/presenter.mp4` and `work/caspian-video/approved/presenter.mp4` now contain the reviewed file. The stakeholder clip was not changed.

Final presenter metadata:

| Field | Value |
|---|---|
| Duration | 8.000000 seconds |
| Video | H.264, 1280×720, 30 fps, yuv420p |
| Streams | one video stream; no audio |
| Size | 397,803 bytes |
| SHA-256 | `e4b18e81c1d05bf0fb7acd930dc20fc36a0807ac615745aa3fdd579efef344fc` |

Original-resolution frames at 0.1s, 4.0s, 6.1s, and 7.9s were inspected. Verdict: stable presenter and framing, intact original motion through six seconds, seamless/stable cloned tail, and no black frames, jumps, text, logo, UI, additional person, or new artifact. The actual-file duration gate passed with presenter `8.000000` and stakeholders `8.000000` against their eight-second manifest segments.

### Round-2 tracked scope and final gates

Tracked scope is limited to the pure duration-contract function, its focused RED/GREEN test, and this report. Whisper packages, model/cache, ASR audit JSON, review audio, MP3/MP4/PNG files, ledger, and secrets remain ignored and untracked. `.superpowers/brainstorm/` remains untouched.

Final commands:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_models.py tests\humanwire\test_caspian_video_openrouter.py -q
.\.venv\Scripts\ruff.exe check scripts\caspian_video tests\humanwire\test_caspian_video_models.py tests\humanwire\test_caspian_video_openrouter.py
git diff --check
```

Final result: `43 passed`; Ruff reported `All checks passed!`; `git diff --check` passed; Whisper key-concept gate passed all seven clips at 0.57% aggregate WER; the actual-file visual-duration gate passed at presenter `8.000000` and stakeholders `8.000000`; tracked media count remained zero.

Fix round 2 contract/test commit: `e6cd5ff7c67a247a2549c59d82addf741ab82758`.
