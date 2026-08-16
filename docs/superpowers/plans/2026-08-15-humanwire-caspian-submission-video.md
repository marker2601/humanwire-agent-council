# HumanWire Caspian Submission Video Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce, verify, publish, and document a truthful 90–110 second HumanWire video that shows one real Caspian run across Telegram and email, the public Decision Room workflow, and short AI-assisted explanatory visuals.

**Architecture:** Keep all credentials and captured media outside Git under `work/caspian-video/`. Add a small tested Python package under `scripts/caspian_video/` for safe manifest validation, cost-gated OpenRouter requests, Windows capture commands, FFmpeg assembly, and ffprobe verification; keep editorial content and evidence notes under `submission/`. The final edit treats real Caspian channel footage as proof, the public Standard-agent product as workflow explanation, and generated presenter/stakeholder clips as labeled visual guidance.

**Tech Stack:** Python 3.12, Pydantic 2, httpx, pytest, FFmpeg/ffprobe, OpenRouter Video API, OpenRouter Text-to-Speech API, Codex image generation, in-app Browser/Windows computer control.

## Global Constraints

- Final public video: 90–110 seconds, 1920×1080, 16:9, H.264/AAC, 30 fps, and under the Caspian three-minute limit.
- Real judge proof must show one consenting operator-owned Caspian run using both Telegram and email.
- Real channel chronology may be trimmed, captioned, cropped, and redacted, but never fabricated or rearranged into a false result.
- The public product must remain visibly and verbally scoped as **Standard agents · no external messages**.
- Generated presenter and stakeholder characters are explanatory visuals, never real participants or live-provider proof.
- Never expose credentials, email addresses, Telegram identifiers, route or conversation IDs, database coordinates, tokens, private answers, or provider bodies.
- `OPENROUTER_API_KEY` is read only from the ignored `.env.video`; it is never printed, serialized, placed in a command line, committed, or included in exception text.
- Standing user authorization permits necessary production and submission spend up to USD $10.00 total without another approval request.
- The completed Task 3 OpenRouter attempt retained its narrower USD $3.00 video-job ceiling and no-retry fence; do not alter its paid ledger or submit replacement video jobs when accepted local fallbacks exist.
- Generate at most one `google/veo-3.1-fast` job and one `bytedance/seedance-2.0-fast` job; no automatic retries.
- Store raw/generated/intermediate/final media only under ignored `work/caspian-video/`.
- No provider or Devpost claim is promoted beyond what the final recording visibly proves.

---

## File Structure

- Modify `.gitignore` to protect `.env.video` for every checkout, not only this local Git exclude.
- Create `submission/caspian-video-script.md` as the exact narration and on-screen-copy source.
- Create `submission/caspian-video-manifest.json` as the safe, deterministic 105-second edit decision list.
- Create `scripts/caspian_video/__init__.py` as the package boundary.
- Create `scripts/caspian_video/models.py` for strict manifest, generation, approval, and final-media models.
- Create `scripts/caspian_video/openrouter.py` for authenticated OpenRouter catalog, credits, submit, poll, download, and TTS calls.
- Create `scripts/caspian_video/media.py` for constrained capture, trim/redaction, FFmpeg composition, captions, and ffprobe verification.
- Create `scripts/caspian_video/__main__.py` for the `python -m scripts.caspian_video` command surface.
- Create `tests/humanwire/test_caspian_video_models.py` for editorial, timing, and privacy contracts.
- Create `tests/humanwire/test_caspian_video_openrouter.py` for mocked API, cost, retry, and secret-safety contracts.
- Create `tests/humanwire/test_caspian_video_media.py` for command construction, path confinement, and final-media verification.
- Create `submission/caspian-video-captions.srt` from the approved final narration timings.
- Create `submission/caspian-video-evidence.md` after final verification with safe proof, model, cost, hash, and public-link facts.
- Modify `submission/caspian.md`, `submission/checklist.md`, and `submission/assets.md` only after the public video URL works signed out.

---

### Task 1: Lock the editorial contract and safe manifest

**Files:**
- Modify: `.gitignore`
- Create: `submission/caspian-video-script.md`
- Create: `submission/caspian-video-manifest.json`
- Create: `scripts/caspian_video/__init__.py`
- Create: `scripts/caspian_video/models.py`
- Create: `tests/humanwire/test_caspian_video_models.py`

**Interfaces:**
- Consumes: approved design `docs/superpowers/specs/2026-08-15-humanwire-caspian-submission-video-design.md`.
- Produces: `VideoManifest.load(path: Path) -> VideoManifest`, `VideoSegment`, `GenerationSpec`, `SpendApproval`, and a validated 105-second timeline used by every later task.

- [ ] **Step 1: Write the failing manifest tests**

```python
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.caspian_video.models import ProofClass, SpendApproval, VideoManifest


ROOT = Path(__file__).resolve().parents[2]


def test_submission_video_manifest_is_truthful_and_105_seconds() -> None:
    manifest = VideoManifest.load(ROOT / "submission/caspian-video-manifest.json")

    assert manifest.total_duration_seconds == 105
    assert 90 <= manifest.total_duration_seconds <= 110
    assert [segment.id for segment in manifest.segments] == [
        "presenter_hook",
        "telegram_authorization",
        "email_evidence",
        "stakeholder_roles",
        "decision_room",
        "replay_and_downloads",
        "closing_card",
    ]
    assert {
        segment.channel
        for segment in manifest.segments
        if segment.proof_class is ProofClass.RECORDED_CASPIAN
    } == {"telegram", "email"}
    assert all(
        segment.disclosure == "Visual guide"
        for segment in manifest.segments
        if segment.proof_class is ProofClass.GENERATED_VISUAL
    )
    assert any(
        "Standard agents · no external messages" in segment.required_copy
        for segment in manifest.segments
        if segment.proof_class is ProofClass.PUBLIC_PRODUCT
    )


@pytest.mark.parametrize("approved", [False, None])
def test_spend_approval_must_be_explicit(approved: bool | None) -> None:
    with pytest.raises(ValidationError):
        SpendApproval(approved=approved, ceiling_usd="3.00")


def test_spend_approval_rejects_more_than_design_ceiling() -> None:
    with pytest.raises(ValidationError):
        SpendApproval(approved=True, ceiling_usd="3.01")
```

- [ ] **Step 2: Run the test to verify the missing package fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_models.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'scripts.caspian_video'`.

- [ ] **Step 3: Implement strict models and loader**

Create `scripts/caspian_video/models.py` with these public types and validations:

```python
from __future__ import annotations

import json
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class ProofClass(StrEnum):
    GENERATED_VISUAL = "generated_visual"
    RECORDED_CASPIAN = "recorded_caspian"
    PUBLIC_PRODUCT = "public_product"
    TITLE_CARD = "title_card"


class VideoSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,40}$")
    start_seconds: int = Field(ge=0, le=180)
    duration_seconds: int = Field(ge=1, le=60)
    source: str = Field(
        pattern=r"^work/caspian-video/approved/[a-z0-9_-]+\.mp4$"
    )
    proof_class: ProofClass
    channel: Literal["telegram", "email"] | None = None
    disclosure: Literal["Visual guide"] | None = None
    required_copy: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_truth_boundary(self) -> "VideoSegment":
        if self.proof_class is ProofClass.RECORDED_CASPIAN and self.channel is None:
            raise ValueError("recorded Caspian segments require a channel")
        if self.proof_class is ProofClass.GENERATED_VISUAL and self.disclosure != "Visual guide":
            raise ValueError("generated visuals require the fixed disclosure")
        return self


class VideoManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: Literal["HumanWire — coordination that reaches a decision"]
    width: Literal[1920]
    height: Literal[1080]
    fps: Literal[30]
    segments: tuple[VideoSegment, ...]

    @property
    def total_duration_seconds(self) -> int:
        return sum(segment.duration_seconds for segment in self.segments)

    @model_validator(mode="after")
    def validate_timeline(self) -> "VideoManifest":
        expected_start = 0
        for segment in self.segments:
            if segment.start_seconds != expected_start:
                raise ValueError("segments must be contiguous and ordered")
            expected_start += segment.duration_seconds
        if not 90 <= expected_start <= 110:
            raise ValueError("editorial duration must be 90–110 seconds")
        return self

    @classmethod
    def load(cls, path: Path) -> "VideoManifest":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class SpendApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approved: Literal[True]
    ceiling_usd: Decimal

    @model_validator(mode="after")
    def validate_ceiling(self) -> "SpendApproval":
        if self.ceiling_usd != Decimal("3.00"):
            raise ValueError("spend ceiling must equal the approved design ceiling")
        return self


class GenerationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["presenter", "stakeholders"]
    model: Literal["google/veo-3.1-fast", "bytedance/seedance-2.0-fast"]
    prompt: str = Field(min_length=40, max_length=2_000)
    duration: Literal[6, 8]
    resolution: Literal["720p"]
    aspect_ratio: Literal["16:9"]
    generate_audio: Literal[False]
    first_frame: Path | None = None

    @model_validator(mode="after")
    def validate_model_duration(self) -> "GenerationSpec":
        expected = {
            "google/veo-3.1-fast": ("presenter", 6),
            "bytedance/seedance-2.0-fast": ("stakeholders", 8),
        }
        if (self.name, self.duration) != expected[self.model]:
            raise ValueError("generation name and duration must match the approved model")
        return self


class GenerationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["presenter", "stakeholders"]
    model: Literal["google/veo-3.1-fast", "bytedance/seedance-2.0-fast"]
    status: Literal["completed"]
    cost_usd: Decimal = Field(ge=0, le=3)
    output_path: Path


class VideoSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key: SecretStr
    presenter_model: Literal["google/veo-3.1-fast"]
    stakeholder_model: Literal["bytedance/seedance-2.0-fast"]
```

Import `SecretStr` from Pydantic. `GenerationReceipt` intentionally omits job, generation, account, and provider IDs.

- [ ] **Step 4: Write the exact narration and edit manifest**

Create `submission/caspian-video-script.md` with this final narration:

```markdown
# HumanWire Caspian Video Script

## 0–8 seconds

Important decisions rarely fail because teams lack messages. They fail because the right objection, evidence, and authority never meet in one workflow.

## 8–22 seconds

HumanWire begins with a mandate in Telegram. Before any outreach, Caspian returns a preview, and the operator explicitly authorizes the run with GO.

## 22–38 seconds

The same run continues by email, where HumanWire asks only the unresolved questions, records the response as evidence, and requires explicit confirmation before it can influence a decision.

## 38–46 seconds

Each stakeholder agent has a specific role: inform, acknowledge, answer, challenge, approve, or provide availability.

## 46–78 seconds

In the Decision Room, the saved workflow becomes visible. Anika raises a risk constraint. HumanWire opens a targeted interview, confirms the evidence, and revises the proposal instead of hiding the disagreement.

## 78–98 seconds

Only then does Sofia exercise approval authority. Daniel provides availability after approval, and HumanWire assembles a decision-ready meeting package.

Every step can be replayed, inspected, and downloaded as JSON or CSV, while the public product remains clearly separated from external delivery.

## 98–105 seconds

HumanWire: one mandate, the right conversations, and a meeting built on confirmed decisions.
```

Create `submission/caspian-video-manifest.json` with this exact content:

```json
{
  "title": "HumanWire — coordination that reaches a decision",
  "width": 1920,
  "height": 1080,
  "fps": 30,
  "segments": [
    {
      "id": "presenter_hook",
      "start_seconds": 0,
      "duration_seconds": 8,
      "source": "work/caspian-video/approved/presenter.mp4",
      "proof_class": "generated_visual",
      "disclosure": "Visual guide",
      "required_copy": ["HumanWire — coordination that reaches a decision"]
    },
    {
      "id": "telegram_authorization",
      "start_seconds": 8,
      "duration_seconds": 14,
      "source": "work/caspian-video/approved/telegram.mp4",
      "proof_class": "recorded_caspian",
      "channel": "telegram",
      "required_copy": ["Recorded Caspian run · Telegram"]
    },
    {
      "id": "email_evidence",
      "start_seconds": 22,
      "duration_seconds": 16,
      "source": "work/caspian-video/approved/email.mp4",
      "proof_class": "recorded_caspian",
      "channel": "email",
      "required_copy": ["Same recorded Caspian run · Email"]
    },
    {
      "id": "stakeholder_roles",
      "start_seconds": 38,
      "duration_seconds": 8,
      "source": "work/caspian-video/approved/stakeholders.mp4",
      "proof_class": "generated_visual",
      "disclosure": "Visual guide",
      "required_copy": ["Role-specific software agents"]
    },
    {
      "id": "decision_room",
      "start_seconds": 46,
      "duration_seconds": 32,
      "source": "work/caspian-video/approved/decision-room.mp4",
      "proof_class": "public_product",
      "required_copy": ["Standard agents · no external messages"]
    },
    {
      "id": "replay_and_downloads",
      "start_seconds": 78,
      "duration_seconds": 20,
      "source": "work/caspian-video/approved/replay-downloads.mp4",
      "proof_class": "public_product",
      "required_copy": ["Standard agents · no external messages"]
    },
    {
      "id": "closing_card",
      "start_seconds": 98,
      "duration_seconds": 7,
      "source": "work/caspian-video/approved/closing-card.mp4",
      "proof_class": "title_card",
      "required_copy": ["One mandate. The right conversations. A decision-ready meeting."]
    }
  ]
}
```

The seven contiguous durations are `8, 14, 16, 8, 32, 20, 7`, totaling exactly 105 seconds. The opening and stakeholder-role explanations each receive eight seconds for professionally understandable narration. The following authorization and Decision Room segments retain fourteen and thirty-two seconds respectively; the 22-second, 38-second, 78-second, 98-second, and 105-second boundaries remain unchanged.

- [ ] **Step 5: Protect local configuration for every clone**

Add exactly this line to `.gitignore`:

```gitignore
.env.video
```

- [ ] **Step 6: Run the focused test and lint**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_models.py -v
.\.venv\Scripts\python.exe -m ruff check scripts\caspian_video tests\humanwire\test_caspian_video_models.py
git diff --check
```

Expected: all tests pass and both static checks exit 0.

- [ ] **Step 7: Commit the editorial contract**

```powershell
git add .gitignore submission/caspian-video-script.md submission/caspian-video-manifest.json scripts/caspian_video tests/humanwire/test_caspian_video_models.py
git commit -m "feat: define Caspian video production contract"
```

---

### Task 2: Build the cost-gated OpenRouter media client

**Files:**
- Modify: `scripts/caspian_video/models.py`
- Create: `scripts/caspian_video/openrouter.py`
- Create: `scripts/caspian_video/__main__.py`
- Create: `tests/humanwire/test_caspian_video_openrouter.py`

**Interfaces:**
- Consumes: `GenerationSpec` and `SpendApproval` from Task 1; `.env.video` with `OPENROUTER_API_KEY`, `OPENROUTER_STAKEHOLDER_MODEL`, and `OPENROUTER_PRESENTER_MODEL`.
- Produces: `OpenRouterMediaClient`, `load_video_settings(path: Path) -> VideoSettings`, `generate_approved_assets(settings: VideoSettings, approval: SpendApproval, work_root: Path) -> tuple[GenerationReceipt, GenerationReceipt]`, `synthesize_narration_sections(script: Path, manifest: VideoManifest, output_dir: Path) -> tuple[Path, ...]`, and CLI commands `preflight`, `generate`, and `tts`.

- [ ] **Step 1: Write mocked API and secret-safety tests**

```python
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from scripts.caspian_video.models import GenerationSpec, SpendApproval
from scripts.caspian_video.openrouter import OpenRouterMediaClient, VideoGenerationError


def approved() -> SpendApproval:
    return SpendApproval(approved=True, ceiling_usd=Decimal("3.00"))


def presenter() -> GenerationSpec:
    return GenerationSpec(
        name="presenter",
        model="google/veo-3.1-fast",
        prompt="Fictional professional visual guide in a dark enterprise studio, subtle push-in",
        duration=6,
        resolution="720p",
        aspect_ratio="16:9",
        generate_audio=False,
    )


def test_submit_poll_and_download_use_exact_openrouter_routes(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(202, json={"id": "job-safe", "status": "pending"})
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"safe-mp4", headers={"content-type": "video/mp4"})
        return httpx.Response(
            200,
            json={
                "id": "job-safe",
                "status": "completed",
                "usage": {"cost": 0.48, "is_byok": False},
            },
        )

    client = OpenRouterMediaClient(
        api_key=SecretStr("PRIVATE-OPENROUTER-SENTINEL"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
    )
    output = tmp_path / "presenter.mp4"
    receipt = client.generate_video(presenter(), approved(), output)

    assert output.read_bytes() == b"safe-mp4"
    assert receipt.cost_usd == Decimal("0.48")
    assert [request.url.path for request in calls] == [
        "/api/v1/videos",
        "/api/v1/videos/job-safe",
        "/api/v1/videos/job-safe/content",
    ]
    assert json.loads(calls[0].content)["generate_audio"] is False


def test_generation_failure_never_retains_secret() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="PRIVATE-OPENROUTER-SENTINEL")

    client = OpenRouterMediaClient(
        api_key=SecretStr("PRIVATE-OPENROUTER-SENTINEL"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(VideoGenerationError) as raised:
        client.generate_video(presenter(), approved(), Path("unused.mp4"))
    assert str(raised.value) == "OpenRouter request failed"
    assert "PRIVATE" not in repr(raised.value)


def test_existing_ledger_blocks_a_second_paid_job(tmp_path: Path) -> None:
    ledger = tmp_path / "jobs.json"
    ledger.write_text('{"presenter":{"status":"completed"}}', encoding="utf-8")
    with pytest.raises(VideoGenerationError, match="job already recorded"):
        OpenRouterMediaClient.guard_single_job(ledger, "presenter")
```

- [ ] **Step 2: Run the test to verify the client is missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_openrouter.py -v
```

Expected: collection fails because `scripts.caspian_video.openrouter` does not exist.

- [ ] **Step 3: Implement configuration, catalog, credits, video, and TTS calls**

Implement `OpenRouterMediaClient` with these exact routes:

```python
class VideoGenerationError(RuntimeError):
    """Fixed-message boundary for media generation failures."""


BASE_URL = "https://openrouter.ai"
VIDEO_MODELS_PATH = "/api/v1/videos/models"
VIDEO_PATH = "/api/v1/videos"
TTS_PATH = "/api/v1/audio/speech"
CREDITS_PATH = "/api/v1/credits"
POLL_INTERVAL_SECONDS = 30
MAX_POLLS = 40
MAX_VIDEO_BYTES = 200 * 1024 * 1024
```

Submit this payload for each approved video job:

```python
payload = {
    "model": spec.model,
    "prompt": spec.prompt,
    "duration": spec.duration,
    "resolution": spec.resolution,
    "aspect_ratio": spec.aspect_ratio,
    "generate_audio": False,
}
```

If `first_frame` is present, add one `frame_images` item with `frame_type="first_frame"` and a local PNG/JPEG data URL. Poll only `pending` and `in_progress`; accept only `completed`; convert `failed`, `cancelled`, `expired`, malformed JSON, timeout, non-2xx, wrong content type, and oversized responses into fixed safe `VideoGenerationError` messages. Download from `/api/v1/videos/{validated_job_id}/content?index=0`; validate job IDs with `^[A-Za-z0-9_-]{1,128}$` before placing them in a URL.

For speech, post:

```python
{
    "model": "deepgram/flux-tts:free",
    "input": narration,
    "response_format": "mp3",
}
```

Accept only `audio/mpeg` and a nonempty body. Do not automatically invoke a paid fallback.

`synthesize_narration_sections()` parses only headings matching `^## (\d+)–(\d+) seconds$`, joins the non-heading paragraphs beneath each heading, requires exactly seven sections with boundaries equal to the manifest, and writes one MP3 per section as `{index:02d}-{segment.id}.mp3`. Probe each MP3 and reject it if its duration exceeds the corresponding segment.

`load_video_settings()` must parse only the three `OPENROUTER_*` keys from `.env.video`, reject duplicates/unknown assignments, return the API key as `SecretStr`, and never mutate `os.environ`.

- [ ] **Step 4: Implement the explicit command gate**

The `generate` CLI requires both flags:

```powershell
--confirm-paid-generation --approve-spend-usd 3.00
```

Build `SpendApproval` only when both are present. Store job ID, model, status, and actual `usage.cost` in ignored `work/caspian-video/openrouter/jobs.json`; never store headers or the API key. Before submission, fetch the live model catalog and assert the requested duration/resolution/aspect ratio are still supported. Check `/api/v1/credits` before and after each job, but print only `credit_available=true|false` and actual job cost, never account identifiers.

- [ ] **Step 5: Run tests, lint, and a read-only preflight**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_openrouter.py -v
.\.venv\Scripts\python.exe -m ruff check scripts\caspian_video tests\humanwire\test_caspian_video_openrouter.py
.\.venv\Scripts\python.exe -m scripts.caspian_video preflight --env-file ..\..\.env.video
git diff --check
```

Expected: tests and checks pass; preflight prints only model capability booleans and `credential_valid=true`; it submits no video or speech request.

- [ ] **Step 6: Commit the safe OpenRouter client**

```powershell
git add scripts/caspian_video tests/humanwire/test_caspian_video_openrouter.py
git commit -m "feat: add cost-gated submission media client"
```

---

### Task 3: Generate the presenter, stakeholder motion, and narration

**Files:**
- Create locally only: `work/caspian-video/references/presenter.png`
- Create locally only: `work/caspian-video/references/stakeholders.png`
- Create locally only: `work/caspian-video/generated/presenter.mp4`
- Create locally only: `work/caspian-video/generated/stakeholders.mp4`
- Create locally only: `work/caspian-video/generated/narration/00-presenter_hook.mp3` through `06-closing_card.mp3`
- Create locally only: `work/caspian-video/openrouter/jobs.json`

**Interfaces:**
- Consumes: explicit user approval of the USD $3.00 ceiling, Task 2 CLI, and exact narration from `submission/caspian-video-script.md`.
- Produces: two approved visual-guide clips and seven segment-aligned narration clips for composition; no Git-tracked binary media.

- [ ] **Step 1: Ask for the spend confirmation immediately before generation**

Use this exact question:

> OpenRouter is ready. Do you explicitly approve up to USD $3.00 total for one 6-second Veo 3.1 Fast job and one 8-second Seedance 2.0 Fast job, with no automatic retries?

Do not proceed until the user explicitly approves the dollar ceiling.

- [ ] **Step 2: Generate two reference images with the image-generation skill**

Presenter prompt:

```text
Create a 16:9 cinematic key frame for a fictional professional visual guide, a confident South Asian woman in her early 30s wearing a modern navy business jacket, standing in a refined dark enterprise technology studio with subtle cyan HumanWire-style light accents. Medium shot, centered, warm natural expression, hands relaxed, photorealistic but clearly a fictional commercial presenter, no logos, no text, no UI, no microphones, no other people, safe title space on the left, consistent realistic lighting.
```

Stakeholder prompt:

```text
Create a 16:9 polished editorial illustration of seven diverse enterprise software-agent role portraits arranged as separate elegant cards around a central cyan coordination path. The roles are executive sponsor, communications lead, domain expert, delivery lead, risk and compliance lead, approval owner, and operations lead. Professional modern clothing, distinct faces, coherent navy/cyan visual system, no text, no logos, no chat messages, no real-person likenesses, no implying these are human participants, generous spacing for later role labels.
```

Inspect both images before continuing. Reject images containing text, logos, duplicated faces, clipped people, or channel-like messages.

- [ ] **Step 3: Run the free narration validation and section synthesis**

Synthesize only the opening sentence first using `deepgram/flux-tts:free`. Confirm the output is an MP3 and listen to it. If the endpoint rejects the missing voice or the pronunciation is unusable, stop the provider path. After explicit user authorization, use either human-recorded narration or offline local speech synthesis; make no paid voice request and no additional provider call.

If the validation passes, synthesize each of the seven Markdown sections independently so every voice clip can begin at its matching manifest time:

```powershell
.\.venv\Scripts\python.exe -m scripts.caspian_video tts --env-file ..\..\.env.video --script submission/caspian-video-script.md --manifest submission/caspian-video-manifest.json --output-dir work/caspian-video/generated/narration
```

The command makes exactly seven free TTS requests, names files with the zero-padded manifest index and segment ID, and rejects any narration whose ffprobe duration exceeds that segment's duration.

- [ ] **Step 4: Submit exactly the two approved video jobs**

Veo prompt:

```text
Six-second 16:9 cinematic commercial shot based on the provided first frame. A fictional professional visual guide looks into camera with calm confidence, makes one subtle open-hand gesture, and holds a natural attentive expression. Slow controlled camera push-in, premium dark enterprise studio, restrained cyan accent lights, realistic human motion, no speech, no lip-sync emphasis, no text, no logos, no UI, no extra people, no camera shake.
```

Seedance prompt:

```text
Eight-second 16:9 motion-graphics shot based on the provided first frame. Seven illustrated enterprise software-agent role cards activate one after another around a central cyan coordination path; fine connection lines flow from role to role and converge toward a decision node. Smooth professional motion, coherent navy and cyan palette, cards and faces remain stable, no speech bubbles, no typed messages, no text mutation, no logos, no implication of real people or live communication.
```

Run:

```powershell
.\.venv\Scripts\python.exe -m scripts.caspian_video generate --env-file ..\..\.env.video --confirm-paid-generation --approve-spend-usd 3.00
```

The command must refuse to run if either job name already exists in the ledger.

- [ ] **Step 5: Verify and inspect generated assets**

Run `ffprobe` on both video outputs and all seven narration files. Extract first, middle, and final PNG frames from both MP4 files under `work/caspian-video/review/generated/`. Inspect every frame with `view_image`. Confirm no text mutation, extra faces, channel-like UI, or visual resemblance to proof footage. Record the two actual OpenRouter costs without job IDs.

- [ ] **Step 6: Apply the no-spend fallback if a paid job fails**

If Veo fails, create the six-second presenter clip from the approved still with:

```powershell
ffmpeg -loop 1 -i work/caspian-video/references/presenter.png -t 6 -vf "scale=1280:720,zoompan=z='min(zoom+0.0008,1.05)':d=180:s=1280x720:fps=30,format=yuv420p" -an -c:v libx264 -crf 18 work/caspian-video/generated/presenter.mp4
```

If Seedance fails, create the eight-second stakeholder clip from the approved still with:

```powershell
ffmpeg -loop 1 -i work/caspian-video/references/stakeholders.png -t 8 -vf "scale=1280:720,zoompan=z='min(zoom+0.0005,1.04)':d=240:s=1280x720:fps=30,format=yuv420p" -an -c:v libx264 -crf 18 work/caspian-video/generated/stakeholders.mp4
```

Do not submit a replacement paid job. After inspection, copy only the accepted clips to `work/caspian-video/approved/presenter.mp4` and `work/caspian-video/approved/stakeholders.mp4`.

---

### Task 4: Add safe Windows capture and redaction tooling

**Files:**
- Create: `scripts/caspian_video/media.py`
- Modify: `scripts/caspian_video/__main__.py`
- Create: `tests/humanwire/test_caspian_video_media.py`
- Create locally only: `work/caspian-video/redactions.json`

**Interfaces:**
- Consumes: ignored work root and FFmpeg/ffprobe executables.
- Produces: `build_capture_command(output: Path, fps: int) -> list[str]`, blocking `run_capture(name: str, work_root: Path) -> Path`, `trim_clip(source: Path, output: Path, start: float, duration: float) -> Path`, `cover_regions(source: Path, output: Path, regions: tuple[CoverRegion, ...]) -> Path`, and constrained CLI commands `capture`, `trim`, and `cover`.

- [ ] **Step 1: Write failing capture and confinement tests**

```python
from pathlib import Path

import pytest

from scripts.caspian_video.media import MediaPathError, build_capture_command, safe_media_path


def test_capture_command_uses_argument_vector_and_ignored_root(tmp_path: Path) -> None:
    output = tmp_path / "raw" / "channels.mp4"
    command = build_capture_command(output, fps=30)
    assert command[:8] == [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "gdigrab",
        "-framerate",
        "30",
    ]
    assert command[-1] == str(output.resolve())
    assert "shell" not in " ".join(command).lower()


@pytest.mark.parametrize(
    "value",
    ["../private.mp4", "C:/Users/private.mp4", "work/caspian-video/../../private.mp4"],
)
def test_media_path_rejects_traversal_and_absolute_paths(tmp_path: Path, value: str) -> None:
    with pytest.raises(MediaPathError):
        safe_media_path(tmp_path, value)


def test_redaction_uses_opaque_cover_not_reversible_blur() -> None:
    from scripts.caspian_video.media import cover_filter

    assert cover_filter(x=10, y=20, width=300, height=40) == (
        "drawbox=x=10:y=20:w=300:h=40:color=0x081522@1:t=fill"
    )
```

- [ ] **Step 2: Run the test to verify media tooling is missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_media.py -v
```

Expected: collection fails because `scripts.caspian_video.media` does not exist.

- [ ] **Step 3: Implement capture and opaque redaction**

Define the strict redaction boundary first:

```python
from pydantic import BaseModel, ConfigDict, Field


class MediaPathError(RuntimeError):
    """Fixed-message boundary for unsafe or unavailable media paths."""


class CoverRegion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    x: int = Field(ge=0, le=7680)
    y: int = Field(ge=0, le=4320)
    width: int = Field(ge=1, le=7680)
    height: int = Field(ge=1, le=4320)
```

Build Windows capture with this argument vector and no shell interpolation:

```python
[
    "ffmpeg", "-hide_banner", "-loglevel", "warning",
    "-f", "gdigrab", "-framerate", "30", "-draw_mouse", "1",
    "-i", "desktop", "-c:v", "libx264", "-preset", "veryfast",
    "-crf", "18", "-pix_fmt", "yuv420p", str(output.resolve()),
]
```

`run_capture()` calls `subprocess.run(command, check=True)` with an argument vector, `shell=False`, and inherited PTY stdin. It blocks until the same terminal sends FFmpeg the literal `q` character. It never writes a PID file, enumerates processes, or kills another FFmpeg process.

For redaction, accept a JSON array of integer rectangles and apply opaque `drawbox` filters. Reject negative coordinates, zero dimensions, more than 20 rectangles, paths outside the work root, and output overwrites. Preserve no original audio from channel captures; the final video uses narration.

- [ ] **Step 4: Run focused tests and a five-second capture smoke**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_media.py -v
.\.venv\Scripts\python.exe -m scripts.caspian_video capture --name capture-smoke
```

Run the capture command in a PTY-backed shell session. After five seconds, send the literal `q` character to that same session with `write_stdin`; wait for FFmpeg to exit 0.

Verify the smoke file is a readable 30 fps MP4, then remove only that exact ignored smoke file.

- [ ] **Step 5: Commit capture tooling**

```powershell
git add scripts/caspian_video tests/humanwire/test_caspian_video_media.py
git commit -m "feat: capture and redact submission footage"
```

---

### Task 5: Record and approve the real two-channel proof and product workflow

**Files:**
- Create locally only: `work/caspian-video/raw/caspian-channels.mp4`
- Create locally only: `work/caspian-video/raw/public-product.mp4`
- Create locally only: `work/caspian-video/approved/telegram.mp4`
- Create locally only: `work/caspian-video/approved/email.mp4`
- Create locally only: `work/caspian-video/approved/decision-room.mp4`
- Create locally only: `work/caspian-video/approved/replay-downloads.mp4`
- Create locally only: `work/caspian-video/review/`

**Interfaces:**
- Consumes: a ready private Caspian listener, one consenting operator-owned Telegram/email identity, the deployed product, and Task 4 capture commands.
- Produces: four privacy-reviewed proof/product clips matching the exact manifest durations.

- [ ] **Step 1: Confirm private run readiness without printing coordinates**

Check only booleans/counts: listener ready, Telegram route ready, email route ready, exactly one consenting directory participant, no pre-existing mandate in the fresh evidence root. Do not print destination values or IDs.

- [ ] **Step 2: Record one continuous Caspian proof take**

Start `caspian-channels` capture. In the visible Telegram client:

1. send the prepared `/mandate` launch-decision request;
2. wait for the Caspian preview;
3. show that no outreach occurs before authorization;
4. send the exact `GO` response;
5. keep the channel UI and chronological order visible.

Then show the email received from the same run:

1. show the targeted question;
2. send the consenting response;
3. show the confirmation request;
4. send the exact `CONFIRM` response;
5. show the resulting evidence acknowledgement.

Stop capture after the acknowledgement. Do not show inbox lists, unrelated messages, account menus, or browser autofill.

- [ ] **Step 3: Trim and cover sensitive regions**

Trim a 16-second Telegram clip and a 16-second email clip from the continuous take. Apply opaque covers to every address, handle, token, avatar, notification, account label, URL coordinate, and unrelated message. Keep the provider/channel chrome, relevant message text, ordering, and timestamps visible enough to establish authenticity.

- [ ] **Step 4: Record the public product workflow**

Open `https://secondsignal.vercel.app/` signed out. Start the launch-decision template. Record the complete flow without reloading:

1. request saved;
2. outreach begins;
3. Anika raises the risk conflict;
4. targeted interview answers appear;
5. evidence is confirmed;
6. proposal is revised;
7. Sofia approves;
8. Daniel provides availability;
9. meeting package becomes ready;
10. replay Previous/Next/Play/Pause/Follow and JSON/CSV controls work.

Keep **Standard agents · no external messages** visible at the start and again during the replay segment.

- [ ] **Step 5: Trim the public product clips**

Create one 34-second Decision Room clip and one 20-second replay/download clip. Use speed changes only on waiting time, never on saved message content or event order. Do not insert or delete events to manufacture a milestone.

- [ ] **Step 6: Perform frame-by-frame approval**

Extract one PNG per second from all four clips. Inspect the complete contact sheet and the first/middle/last full-resolution frames. Reject and recut any clip with:

- a visible private coordinate or token;
- clipped required copy;
- a channel label that could be mistaken for generated footage;
- generated artwork over judge-critical proof;
- product chronology that differs from the recorded saved events.

- [ ] **Step 7: Establish the public repository URL before the closing card**

Check `https://github.com/marker2601/humanwire` while signed out. If it does not exist, pause composition and use the separate finishing-branch/GitHub publication workflow to privacy-audit and publish the approved `codex/humanwire` history. Do not guess or render a repository URL that is not publicly reachable. Continue only after the signed-out page returns the HumanWire repository.

---

### Task 6: Compose captions, title cards, and the deterministic final MP4

**Files:**
- Modify: `scripts/caspian_video/media.py`
- Modify: `scripts/caspian_video/__main__.py`
- Modify: `tests/humanwire/test_caspian_video_media.py`
- Create: `submission/caspian-video-captions.srt`
- Create locally only: `work/caspian-video/approved/presenter.mp4`
- Create locally only: `work/caspian-video/approved/stakeholders.mp4`
- Create locally only: `work/caspian-video/approved/closing-card.mp4`
- Create locally only: `work/caspian-video/final/humanwire-caspian-demo.mp4`

**Interfaces:**
- Consumes: validated manifest, approved generated assets, approved proof/product clips, seven narration MP3 sections, and FFmpeg.
- Produces: `compose_video(manifest: VideoManifest, work_root: Path, captions: Path, narration_dir: Path, repository_url: str, output: Path) -> Path` and the exact final MP4. `repository_url` must equal `https://github.com/marker2601/humanwire` and must pass a signed-out HTTPS reachability check before rendering.

- [ ] **Step 1: Add failing composition tests**

```python
from pathlib import Path

import pytest

from scripts.caspian_video.media import MediaPathError, build_compose_commands
from scripts.caspian_video.models import VideoManifest


def create_minimal_fixture_assets(manifest: VideoManifest, work_root: Path) -> None:
    for segment in manifest.segments:
        relative = Path(segment.source).relative_to("work/caspian-video")
        asset = work_root / relative
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b"fixture-media")


def test_compose_requires_every_manifest_asset(tmp_path: Path) -> None:
    manifest = VideoManifest.load(
        Path(__file__).resolve().parents[2] / "submission/caspian-video-manifest.json"
    )
    with pytest.raises(MediaPathError, match="missing approved asset"):
        build_compose_commands(manifest, tmp_path, tmp_path / "final.mp4")


def test_compose_normalizes_every_segment_to_1080p_30fps(tmp_path: Path) -> None:
    manifest = VideoManifest.load(
        Path(__file__).resolve().parents[2] / "submission/caspian-video-manifest.json"
    )
    create_minimal_fixture_assets(manifest, tmp_path)
    commands = build_compose_commands(manifest, tmp_path, tmp_path / "final.mp4")
    rendered = "\n".join(" ".join(command) for command in commands)
    assert "scale=1920:1080:force_original_aspect_ratio=decrease" in rendered
    assert "pad=1920:1080" in rendered
    assert "fps=30" in rendered
    assert "-c:v libx264" in rendered
    assert "-c:a aac" in rendered
```

- [ ] **Step 2: Run the tests to capture the missing composition behavior**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_media.py -k "compose" -v
```

Expected: tests fail because `build_compose_commands` is not implemented.

- [ ] **Step 3: Implement deterministic segment normalization and concat**

For each segment, render a temporary MP4 with:

```text
scale=1920:1080:force_original_aspect_ratio=decrease,
pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x020d1c,
fps=30,format=yuv420p
```

Add these fixed labels inside title-safe bounds:

- generated clips: `Visual guide`;
- Telegram clip: `Recorded Caspian run · Telegram`;
- email clip: `Same recorded Caspian run · Email`;
- public product clips: `Standard agents · no external messages`.

Create the closing card locally from a 1920×1080 navy color source with:

```text
HumanWire
One mandate. The right conversations. A decision-ready meeting.
secondsignal.vercel.app
github.com/marker2601/humanwire
```

Concat normalized clips with the demuxer. For each narration section, apply `adelay={segment.start_seconds * 1000}|{segment.start_seconds * 1000}` and `atrim=duration={segment.duration_seconds}`, mix all seven against a 105-second silent stereo bed with `amix=inputs=8:duration=longest:normalize=0`, burn in the SRT captions, and encode `libx264 -crf 18 -preset slow -c:a aac -b:a 192k -movflags +faststart`.

- [ ] **Step 4: Author exact captions**

Create `submission/caspian-video-captions.srt` from the seven script blocks. Each subtitle begins no earlier than its segment start and ends no later than its segment end. Use at most two lines per cue and no line longer than 42 characters. Include spoken words only; proof labels remain visual overlays.

- [ ] **Step 5: Run composition tests and build the final file**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_media.py -v
.\.venv\Scripts\python.exe -m scripts.caspian_video compose --manifest submission/caspian-video-manifest.json --captions submission/caspian-video-captions.srt --narration-dir work/caspian-video/generated/narration --repository-url https://github.com/marker2601/humanwire --output work/caspian-video/final/humanwire-caspian-demo.mp4
```

Expected: composition exits 0 and prints only the final path and safe duration summary.

- [ ] **Step 6: Commit composition code and captions**

```powershell
git add scripts/caspian_video tests/humanwire/test_caspian_video_media.py submission/caspian-video-captions.srt
git commit -m "feat: compose HumanWire Caspian demo video"
```

---

### Task 7: Verify, publish, and update the Caspian packet

**Files:**
- Modify: `scripts/caspian_video/media.py`
- Modify: `scripts/caspian_video/__main__.py`
- Modify: `tests/humanwire/test_caspian_video_media.py`
- Create: `submission/caspian-video-evidence.md`
- Modify: `submission/caspian.md`
- Modify: `submission/checklist.md`
- Modify: `submission/assets.md`
- Create locally only: `work/caspian-video/final/humanwire-caspian-thumbnail.jpg`

**Interfaces:**
- Consumes: final MP4 and a user-authenticated public video host.
- Produces: `verify_final_video(path: Path) -> FinalVideoReport`, a signed-out public video URL, and truthful submission records.

- [ ] **Step 1: Write failing final-media verification tests**

```python
import pytest

from scripts.caspian_video.media import FinalMediaError, parse_ffprobe


def valid_ffprobe_payload(*, duration: str) -> dict[str, object]:
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "pix_fmt": "yuv420p",
                "avg_frame_rate": "30/1",
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": duration, "tags": {}},
    }


def test_final_probe_requires_exact_submission_format() -> None:
    report = parse_ffprobe(
        valid_ffprobe_payload(duration="105.000000"),
        size_bytes=1_000_000,
        sha256="0" * 64,
    )
    assert report.width == 1920
    assert report.height == 1080
    assert report.fps == 30
    assert report.video_codec == "h264"
    assert report.audio_codec == "aac"
    assert report.duration_seconds == 105


@pytest.mark.parametrize("duration", ["89.9", "110.1", "180.1"])
def test_final_probe_rejects_out_of_contract_duration(duration: str) -> None:
    with pytest.raises(FinalMediaError):
        parse_ffprobe(
            valid_ffprobe_payload(duration=duration),
            size_bytes=1_000_000,
            sha256="0" * 64,
        )
```

- [ ] **Step 2: Implement strict ffprobe parsing**

Define the safe report type:

```python
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FinalMediaError(RuntimeError):
    """Fixed-message boundary for invalid final media."""


class FinalVideoReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    width: Literal[1920]
    height: Literal[1080]
    fps: Literal[30]
    video_codec: Literal["h264"]
    audio_codec: Literal["aac"]
    duration_seconds: Decimal = Field(ge=90, le=110)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
```

Invoke ffprobe with an argument vector:

```python
[
    "ffprobe", "-v", "error", "-show_streams", "-show_format",
    "-of", "json", str(path.resolve()),
]
```

Require one H.264 video stream, one AAC audio stream, 1920×1080, 30 fps, yuv420p, duration 90–110 seconds, and a nonempty file. Reject location, comment, description, synopsis, or custom metadata tags. Return a frozen `FinalVideoReport` containing only safe technical fields and SHA-256.

- [ ] **Step 3: Run automated verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m scripts.caspian_video verify --input work/caspian-video/final/humanwire-caspian-demo.mp4
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_models.py tests\humanwire\test_caspian_video_openrouter.py tests\humanwire\test_caspian_video_media.py -v
.\.venv\Scripts\python.exe -m ruff check scripts\caspian_video tests\humanwire\test_caspian_video_models.py tests\humanwire\test_caspian_video_openrouter.py tests\humanwire\test_caspian_video_media.py
git diff --check
```

Expected: all commands exit 0 and the verifier prints only duration, dimensions, codecs, size, and SHA-256.

- [ ] **Step 4: Perform complete human review**

Watch the final MP4 from start to finish with sound. Inspect frames at least every five seconds and every cut. Confirm:

1. Telegram and email are visibly real channel surfaces from one run;
2. preview and `GO` precede outreach;
3. evidence confirmation precedes approval;
4. the public product is labeled **Standard agents · no external messages**;
5. generated visuals are labeled **Visual guide** and never resemble live proof;
6. no secret or private coordinate appears;
7. captions match narration and are readable;
8. the final call to action is fully visible.

- [ ] **Step 5: Write the safe evidence note**

Create `submission/caspian-video-evidence.md` with:

- final duration, resolution, codecs, size, and SHA-256;
- OpenRouter model names and actual total cost, without generation/job/account IDs;
- `Telegram proof: PASS` and `Email proof: PASS` only after the final human review;
- `Generated visual disclosure: PASS`;
- `Private-coordinate frame scan: PASS`;
- the public video URL after upload;
- the date/time of signed-out playback verification.

Extract the safe thumbnail from the closing card and inspect it:

```powershell
ffmpeg -ss 00:01:41 -i work/caspian-video/final/humanwire-caspian-demo.mp4 -frames:v 1 -q:v 2 work/caspian-video/final/humanwire-caspian-thumbnail.jpg
```

Require 1920×1080, readable product/repository URLs, and no channel footage or private coordinates in the thumbnail.

- [ ] **Step 6: Upload only after showing the final local file to the user**

Open the final MP4 for the user. After the user approves the cut, use their authenticated browser to upload it to YouTube as **Public** with:

```text
Title: HumanWire — Coordination That Reaches a Decision
Description: HumanWire turns one mandate into targeted outreach, confirmed evidence, authority-bound approval, and a decision-ready meeting. The video includes one recorded consenting Caspian run across Telegram and email, plus the public Standard-agent workflow at https://secondsignal.vercel.app/.
```

Do not publish private evidence files, raw captures, or generated job receipts. Replay the hosted video while signed out and confirm it is public and complete.

- [ ] **Step 7: Update submission packet with the verified URL**

Replace the existing video URL field in `submission/caspian.md`, mark only the completed video and signed-out playback boxes in `submission/checklist.md`, and add the public URL plus final local filename to `submission/assets.md`. Do not mark GitHub, Devpost receipt, or live-provider fields complete unless independently verified.

- [ ] **Step 8: Run the final repository gate and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
git status --short
```

Expected: tests and lint exit 0; only the intended submission/tooling files are changed plus the pre-existing untracked `.superpowers/brainstorm/` directory.

Commit:

```powershell
git add submission scripts/caspian_video tests/humanwire/test_caspian_video_models.py tests/humanwire/test_caspian_video_openrouter.py tests/humanwire/test_caspian_video_media.py .gitignore
git commit -m "docs: finalize Caspian submission video"
```

---

## Implementation Order and Checkpoints

1. Tasks 1–2 create a safe, fully mocked pipeline and make no paid calls.
2. Task 3 pauses for the explicit USD $3.00 approval, then creates exactly two video jobs.
3. Task 4 adds locally testable capture/redaction tooling.
4. Task 5 requires the real consenting channel interaction and produces the judge-critical footage.
5. Task 6 assembles the approved assets without changing their proof chronology.
6. Task 7 verifies every technical/truth boundary, shows the local cut to the user, then uploads and updates the packet.

## Official API References

- [OpenRouter video generation](https://openrouter.ai/docs/guides/overview/multimodal/video-generation)
- [OpenRouter video model selection](https://openrouter.ai/docs/cookbook/video-generation/choose-video-model)
- [OpenRouter text-to-speech](https://openrouter.ai/docs/guides/overview/multimodal/tts)
