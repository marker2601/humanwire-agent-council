# HumanWire Professional Video Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the rejected HumanWire submission video with a professional, product-first 80-second demo, publish it, and update the existing Devpost entry before the deadline.

**Architecture:** Keep authentic product pixels authoritative and compose them locally in Remotion. Use the existing OpenRouter credential only for two short silent generated visuals and one directed narration track, protected by an atomic cumulative USD $10 spend fence. Replace the public URL only after local and signed-out verification.

**Tech Stack:** Python 3.12, Pydantic v2, httpx, pytest, Node.js 20+, React, Remotion, FFmpeg/ffprobe, OpenRouter video and speech APIs, YouTube Studio, Devpost.

## Global Constraints

- Final duration: 78–82 seconds, 1920x1080, 30 fps, H.264 yuv420p, AAC stereo 48 kHz, faststart.
- Authentic product footage is never submitted to a generative model.
- Product footage occupies at least 70% of runtime and 72% of the frame when shown.
- `Standard agents · no external messages` remains visible during product footage.
- Generated characters are fictional visual guides or software-agent illustrations.
- No live Telegram/email delivery may be claimed.
- Prior ambiguous exposure counts as USD $1.00; all new reservations must keep aggregate exposure at or below USD $10.00.
- Every paid POST follows catalog, capability, path, and budget checks.
- Existing YouTube and Devpost data remain unchanged until the replacement passes.
- Binary media, credentials, provider bodies, job IDs, and review artifacts stay ignored.

---

### Task 1: Contracts and cumulative spend gate

**Files:**
- Create: `scripts/caspian_video_v2/__init__.py`
- Create: `scripts/caspian_video_v2/models.py`
- Test: `tests/humanwire/test_caspian_video_v2_models.py`

**Interfaces:**
- Consumes: canonical repository root and ignored `work/caspian-video-v2/`.
- Produces: `VideoJobSpec`, `NarrationSpec`, `SpendAuthorization`, `ProductionManifest`, and `safe_work_path()`.

- [ ] **Step 1: Write the failing model tests**

```python
def test_budget_counts_prior_exposure():
    auth = SpendAuthorization(cap_usd=Decimal("10.00"), prior_exposure_usd=Decimal("1.00"))
    assert auth.can_reserve(Decimal("8.99"), already_reserved=Decimal("0.01"))
    assert not auth.can_reserve(Decimal("9.01"), already_reserved=Decimal("0.00"))

def test_manifest_is_eighty_seconds_and_product_dominant():
    manifest = ProductionManifest.model_validate_json(MANIFEST.read_text())
    assert manifest.duration_seconds == 80
    assert manifest.product_seconds >= 56
```

- [ ] **Step 2: Run the tests and capture the missing-module RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_v2_models.py -v`

Expected: `ModuleNotFoundError: No module named 'scripts.caspian_video_v2'`.

- [ ] **Step 3: Implement strict frozen models**

```python
class SpendAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    cap_usd: Decimal = Decimal("10.00")
    prior_exposure_usd: Decimal = Decimal("1.00")

    def can_reserve(self, amount: Decimal, *, already_reserved: Decimal) -> bool:
        return amount > 0 and self.prior_exposure_usd + already_reserved + amount <= self.cap_usd

class VideoJobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    name: Literal["visual_guide_v2", "agent_flow_v2"]
    model: Literal["kwaivgi/kling-v3.0-std", "bytedance/seedance-2.0"]
    duration_seconds: Literal[6]
    resolution: Literal["720p"]
    aspect_ratio: Literal["16:9"]
    generate_audio: Literal[False] = False
    prompt: str
    first_frame: Path
    reserved_usd: Decimal
```

- [ ] **Step 4: Run the model tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_v2_models.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add scripts/caspian_video_v2 tests/humanwire/test_caspian_video_v2_models.py
git commit -m "feat: define professional video contracts"
```

### Task 2: Cost-gated OpenRouter client

**Files:**
- Create: `scripts/caspian_video_v2/openrouter.py`
- Test: `tests/humanwire/test_caspian_video_v2_openrouter.py`
- Modify: `scripts/caspian_video_v2/models.py`

**Interfaces:**
- Consumes: strict specs, external `.env.video`, and repository root.
- Produces: `ProfessionalMediaClient.preflight()`, `generate_video()`, and `generate_narration()`.

- [ ] **Step 1: Write mocked security and side-effect tests**

```python
def test_paid_post_follows_atomic_reservation(tmp_path, transport):
    client = ProfessionalMediaClient(api_key=SecretStr("PRIVATE-SENTINEL"), transport=transport)
    receipt = client.generate_video(GUIDE_SPEC, authorization=AUTH, repository_root=tmp_path)
    ledger = json.loads((tmp_path / "work/caspian-video-v2/openrouter/jobs.json").read_text())
    assert ledger["visual_guide_v2"]["status"] == "completed"
    assert receipt.output_path == Path("work/caspian-video-v2/generated/visual-guide.mp4")

def test_provider_failure_has_no_secret_or_exception_graph(tmp_path, failing_transport):
    with pytest.raises(MediaGenerationError) as raised:
        failing_client.generate_video(GUIDE_SPEC, authorization=AUTH, repository_root=tmp_path)
    assert str(raised.value) == "Media generation failed"
    assert "PRIVATE-SENTINEL" not in repr(raised.value)
    assert raised.value.__cause__ is None and raised.value.__context__ is None
```

- [ ] **Step 2: Run the tests and capture the missing-client RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_v2_openrouter.py -v`

Expected: import failures for `ProfessionalMediaClient`.

- [ ] **Step 3: Implement the provider boundary**

```python
class ProfessionalMediaClient:
    def preflight(self, specs: Sequence[VideoJobSpec]) -> PreflightResult: ...
    def generate_video(
        self, spec: VideoJobSpec, *, authorization: SpendAuthorization, repository_root: Path
    ) -> MediaReceipt: ...
    def generate_narration(
        self, spec: NarrationSpec, *, authorization: SpendAuthorization, repository_root: Path
    ) -> MediaReceipt: ...
```

Reserve atomically before POST, stream with a size cap, poll against a monotonic deadline, validate cost and ffprobe output, and raise fresh fixed exceptions outside `except`.

- [ ] **Step 4: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_v2_models.py tests\humanwire\test_caspian_video_v2_openrouter.py -v`

Expected: PASS with mocked network only.

- [ ] **Step 5: Commit**

```powershell
git add scripts/caspian_video_v2 tests/humanwire/test_caspian_video_v2_openrouter.py
git commit -m "feat: generate professional video media"
```

### Task 3: Story, captions, and approved generated assets

**Files:**
- Create: `submission/caspian-video-v2-script.md`
- Create: `submission/caspian-video-v2-manifest.json`
- Create: `submission/caspian-video-v2-captions.srt`
- Test: `tests/humanwire/test_caspian_video_v2_content.py`
- Create ignored: `work/caspian-video-v2/generated/`

**Interfaces:**
- Consumes: approved storyboard and authentic `work/caspian-video/raw/public-product.mp4`.
- Produces: exact 80-second content contract plus approved visuals and narration.

- [ ] **Step 1: Write timeline and truth tests**

```python
def test_timeline_is_exact():
    manifest = ProductionManifest.model_validate_json(MANIFEST.read_text())
    assert [(s.id, s.start_seconds, s.duration_seconds) for s in manifest.segments] == [
        ("hook", 0, 6), ("request", 6, 8), ("minimum_path", 14, 12),
        ("conflict_to_revision", 26, 18), ("approval_to_meeting", 44, 12),
        ("replay_exports", 56, 12), ("gateway_truth", 68, 7), ("closing", 75, 5),
    ]

def test_truth_copy_is_present_and_external_claims_are_absent():
    text = CAPTIONS.read_text(encoding="utf-8")
    assert "Standard agents · no external messages" in text
    assert "live Telegram" not in text and "live email" not in text
```

- [ ] **Step 2: Run tests and capture missing-file RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_v2_content.py -v`

Expected: failures for missing manifest, script, and captions.

- [ ] **Step 3: Write exact tracked content**

Write 175–195 narration words covering request, minimum path, conflict, focused interview, confirmed evidence, revision, approval, availability, meeting, replay, exports, and Caspian. Keep captions at two lines and 42 characters per line.

- [ ] **Step 4: Run GET-only preflight, then authorized generation**

Generate one Kling guide, one Seedance agent-flow shot, and one Gemini narration. Permit one replacement for a rejected asset only while aggregate exposure stays at or below USD $10.00.

- [ ] **Step 5: Validate generated media**

Require ffprobe success, full decode, first/middle/final visual inspection, Whisper transcript, 135–160 WPM narration, approximately -16 LUFS, true peak below -1.5 dBTP, and clean silence edges.

- [ ] **Step 6: Run tests and commit tracked contracts**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_v2_content.py -v
git add submission/caspian-video-v2-script.md submission/caspian-video-v2-manifest.json submission/caspian-video-v2-captions.srt tests/humanwire/test_caspian_video_v2_content.py
git commit -m "docs: lock professional video story"
```

### Task 4: Remotion product-first composition

**Files:**
- Create: `scripts/caspian_video_v2/remotion/package.json`
- Create: `scripts/caspian_video_v2/remotion/tsconfig.json`
- Create: `scripts/caspian_video_v2/remotion/src/index.ts`
- Create: `scripts/caspian_video_v2/remotion/src/Root.tsx`
- Create: `scripts/caspian_video_v2/remotion/src/HumanWireVideo.tsx`
- Create: `scripts/caspian_video_v2/remotion/src/components/ProductStage.tsx`
- Create: `scripts/caspian_video_v2/remotion/src/components/AgentOverlay.tsx`
- Create: `scripts/caspian_video_v2/remotion/src/components/CaptionLayer.tsx`
- Create: `scripts/caspian_video_v2/remotion/src/components/TruthBadge.tsx`
- Test: `tests/humanwire/test_caspian_video_v2_remotion.py`

**Interfaces:**
- Consumes: locked manifest, approved ignored media, authentic product clips, captions, and narration.
- Produces: deterministic 2,400-frame composition `HumanWireProfessional`.

- [ ] **Step 1: Write structural tests**

```python
def test_composition_is_product_dominant_and_truthful():
    root = (REMOTION / "src/Root.tsx").read_text()
    video = (REMOTION / "src/HumanWireVideo.tsx").read_text()
    assert 'id="HumanWireProfessional"' in root
    assert "2400" in root
    assert "Standard agents · no external messages" in video
    assert video.count("<ProductStage") >= 6
```

- [ ] **Step 2: Run tests and capture missing-project RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_v2_remotion.py -v`

Expected: missing-file failures.

- [ ] **Step 3: Implement focused components**

`ProductStage` accepts `src`, `startFrom`, `endAt`, `crop`, and `callouts`, rendering authentic pixels plus separate overlays. `TruthBadge` is mandatory for each product segment. `AgentOverlay` labels generated characters `Software agent` or `Visual guide`.

- [ ] **Step 4: Install pinned dependencies and check**

Run `npm install --ignore-scripts`, `npm run typecheck`, and `npm test` inside the Remotion directory.

Expected: lockfile created, TypeScript clean, component tests green.

- [ ] **Step 5: Render and inspect a 960x540 draft**

Extract every segment boundary and every two seconds. Reject illegible product text, caption collisions, static shots over three seconds, generated defects, or missing truth copy.

- [ ] **Step 6: Commit**

```powershell
git add scripts/caspian_video_v2/remotion tests/humanwire/test_caspian_video_v2_remotion.py
git commit -m "feat: compose professional HumanWire demo"
```

### Task 5: Master render and quality verification

**Files:**
- Create: `scripts/caspian_video_v2/verify.py`
- Test: `tests/humanwire/test_caspian_video_v2_verify.py`
- Create ignored: `work/caspian-video-v2/final/humanwire-professional-demo.mp4`
- Create ignored: `work/caspian-video-v2/review/`

**Interfaces:**
- Consumes: rendered candidate and locked v2 contracts.
- Produces: `VerificationReport` and a release-ready SHA-256.

- [ ] **Step 1: Write failing verifier tests**

```python
def test_wrong_duration_or_atom_order_is_rejected(probe_fixture):
    with pytest.raises(VideoVerificationError):
        verify_master(probe_fixture(duration=77.9, atoms=["ftyp", "mdat", "moov"]))

def test_product_and_truth_samples_are_required(report_fixture):
    assert verify_frame_contract(report_fixture(product_ratio=.72, truth_badge_ratio=1.0))
```

- [ ] **Step 2: Run tests and capture missing-verifier RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_v2_verify.py -v`

Expected: import failure for `scripts.caspian_video_v2.verify`.

- [ ] **Step 3: Implement verification**

Check codec properties, duration, faststart, all 2,400 decoded frames, random seeks, boundaries, captions, product-area samples, truth samples, transcript, loudness, clipping, tracked media, secrets, forbidden claims, and SHA-256.

- [ ] **Step 4: Render and verify 1080p**

Render with pinned Remotion, run `verify.py`, and require one clean MP4 plus a sanitized safe-metrics report.

- [ ] **Step 5: Run professional and first-time-audience review**

Inspect the complete contact sheet, every caption midpoint, both generated clips frame-by-frame, all product milestones, and the CTA. Confirm the seven audience questions in the design are answerable from the video alone.

- [ ] **Step 6: Run gates and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\humanwire\test_caspian_video_v2_*.py -q
.\.venv\Scripts\ruff.exe check scripts/caspian_video_v2 tests/humanwire/test_caspian_video_v2_*.py
git diff --check
git add scripts/caspian_video_v2/verify.py tests/humanwire/test_caspian_video_v2_verify.py
git commit -m "test: verify professional HumanWire video"
```

### Task 6: Publish and update the submitted project

**Files:**
- Modify: `submission/caspian-video-evidence.md`
- Modify: `submission/checklist.md`
- Create ignored: `.superpowers/sdd/2026-08-16-humanwire-professional-video/task-6-report.md`

**Interfaces:**
- Consumes: verified master, authenticated YouTube Studio, and existing HumanWire Devpost project.
- Produces: new public YouTube URL and updated submitted Devpost entry.

- [ ] **Step 1: Upload the verified master**

Use title `HumanWire — AI coordination that reaches a decision`, truthful description, public visibility, and no fabricated provider claims. Keep the existing video unchanged.

- [ ] **Step 2: Verify YouTube signed out**

Require HTTP 200, playable 78–82 second duration, correct title, completed 1080p processing, and audible narration.

- [ ] **Step 3: Update and resubmit Devpost**

Change only the project video URL, preserve the existing project identity and copy, and resubmit the existing entry rather than creating a duplicate.

- [ ] **Step 4: Verify Devpost signed out**

Require the HumanWire page, new video, product URL, repository URL, and submitted state to be publicly visible with no draft warning.

- [ ] **Step 5: Record safe evidence and commit**

```powershell
git add submission/caspian-video-evidence.md submission/checklist.md
git commit -m "docs: record professional Caspian video"
git push origin codex/humanwire:main
```

- [ ] **Step 6: Final completion check**

Confirm the worktree contains only preserved `.superpowers/brainstorm/`, public repository tracks no media or secrets, all three public URLs return 200, and Devpost remains submitted before the deadline.
