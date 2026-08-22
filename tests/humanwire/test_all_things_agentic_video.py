from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_google_video_manifest_is_judge_ready() -> None:
    payload = json.loads(
        (ROOT / "submission/all-things-agentic-video-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["width"] == 1920
    assert payload["height"] == 1080
    assert payload["fps"] == 30
    assert payload["duration_seconds"] == 112
    assert sum(item["duration_frames"] for item in payload["segments"]) == 3360
    assert sum(
        item["duration_frames"]
        for item in payload["segments"]
        if item["source_kind"] == "live_google_product"
    ) == 2532
    assert payload["product_footage_ratio"] >= 0.75
    assert payload["claims"]["model"] == "Gemini 3.5 Flash"
    assert payload["claims"]["bonus_models"] == ["Veo 3.1 Fast", "Lyria 3 Pro"]
    assert payload["claims"]["framework"] == "Google ADK 2.7"
    assert payload["claims"]["external_messages"] is False
    assert payload["release"]["voice"] == "en-US-Chirp3-HD-Aoede"
    assert payload["release"]["voice_audition_approved"] is True
    assert payload["release"]["final_mix_approved"] is True


def test_google_video_component_preserves_truth_and_real_product_focus() -> None:
    source = (
        ROOT
        / "scripts/caspian_video_v2/remotion/src/GoogleHumanWireVideo.tsx"
    ).read_text(encoding="utf-8")
    root = (ROOT / "scripts/caspian_video_v2/remotion/src/Root.tsx").read_text(
        encoding="utf-8"
    )
    assert 'TRUTH_COPY = "Live Google run · no external stakeholder messages"' in source
    assert 'staticFile("google/raw/decisionos-release-00040.mp4")' in source
    assert 'staticFile("google/veo-google-hook.mp4")' in source
    assert "Google Veo 3.1 visual guide" in source
    assert 'staticFile("google/audio/lyria-score-225.mp3")' in source
    assert "volume={0.12}" in source
    assert "Gemini 3.5 Flash" in source
    assert "Google ADK 2.7" in source
    assert "humanwire-decisionos-00040-g92" in source
    assert "humanwire-decisionos-wjjhjrgnyq-uc.a.run.app" in source
    assert "Firebase Auth + App Check" in source
    assert "Firestore durable state" in source
    assert "ChapterPulse" in source
    assert "ProductFocus" in source
    assert '[1350, "04-stakeholders.mp3"]' in source
    assert '[2340, "06-audit.mp3"]' in source
    assert "durationInFrames={3360}" in root
    assert 'id="HumanWireGoogleSubmission"' in root


def test_google_video_captions_are_authored_and_release_safe() -> None:
    source = (
        ROOT
        / "scripts/caspian_video_v2/remotion/src/components/GoogleCaptionLayer.tsx"
    ).read_text(encoding="utf-8")
    assert "const cues:" in source
    assert "const cues: ReadonlyArray<readonly [number, number, string]> = [];" not in source
    assert "maxWidth: 980" in source
    assert "fontSize: 34" in source
    assert "whiteSpace: \"pre-line\"" in source

    manifest = json.loads(
        (ROOT / "submission/all-things-agentic-video-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    cues = json.loads(
        (ROOT / "submission/all-things-agentic-caption-cues.json").read_text(
            encoding="utf-8"
        )
    )
    assert cues[0][0] == 0
    assert all(start < end <= 3360 for start, end, _copy in cues)
    assert all(left[1] <= right[0] for left, right in pairwise(cues))
    assert all(len(copy.splitlines()) <= 2 for _start, _end, copy in cues)
    assert all(
        len(line) <= 42
        for _start, _end, copy in cues
        for line in copy.splitlines()
    )
    caption_words = " ".join(copy.replace("\n", " ") for _, _, copy in cues)
    assert caption_words == manifest["narration_text"]


def test_google_video_script_matches_the_approved_release_contract() -> None:
    script = (ROOT / "submission/all-things-agentic-video-script.md").read_text(
        encoding="utf-8"
    )
    assert "Release contract: 1:52" in script
    assert "75.4%" in script
    assert "Big decisions need more than AI" in script
    assert "explicit human authority" in script
    assert "Gemini 3.5 Flash" in script
    assert "Google ADK 2.7" in script
    assert "Cloud Run" in script
    assert "Firestore" in script
    assert "Firebase Authentication" in script
    assert "human approval required" in script
    assert "fewer coordination meetings" in script
