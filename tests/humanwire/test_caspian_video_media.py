from pathlib import Path

import pytest

from scripts.caspian_video.media import MediaPathError, build_capture_command, safe_media_path


def test_capture_command_uses_argument_vector_and_ignored_root(tmp_path: Path) -> None:
    """Break caught: capture is shell-built or writes outside its owned output path."""
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
    """Break caught: caller-supplied media paths can leave the owned work root."""
    with pytest.raises(MediaPathError):
        safe_media_path(tmp_path, value)


def test_redaction_uses_opaque_cover_not_reversible_blur() -> None:
    """Break caught: a privacy cover is replaced with a reversible blur filter."""
    from scripts.caspian_video.media import cover_filter

    assert cover_filter(x=10, y=20, width=300, height=40) == (
        "drawbox=x=10:y=20:w=300:h=40:color=0x081522@1:t=fill"
    )


def test_run_capture_writes_only_a_new_raw_mp4_with_an_argument_vector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: capture overwrites media or runs its command through a shell."""
    from scripts.caspian_video import media

    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(
        media.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )
    work_root = tmp_path / "work" / "caspian-video"

    output = media.run_capture("capture-smoke", work_root)

    assert output == (work_root / "raw" / "capture-smoke.mp4").resolve()
    assert calls == [
        (
            media.build_capture_command(output, fps=30),
            {"check": True, "shell": False},
        )
    ]


def test_run_capture_refuses_to_overwrite_an_existing_raw_mp4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: a second capture silently destroys an earlier recording."""
    from scripts.caspian_video import media

    work_root = tmp_path / "work" / "caspian-video"
    existing = work_root / "raw" / "capture-smoke.mp4"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"existing")
    monkeypatch.setattr(media.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("capture ran"))

    with pytest.raises(media.MediaPathError, match="^media output exists$") as raised:
        media.run_capture("capture-smoke", work_root)

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_trim_and_cover_remove_source_audio_and_use_opaque_regions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: processing retains channel audio or applies a non-opaque redaction."""
    from scripts.caspian_video import media

    work_root = tmp_path / "work" / "caspian-video"
    source = work_root / "raw" / "channels.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    trimmed = work_root / "trimmed" / "channels.mp4"
    redacted = work_root / "redacted" / "channels.mp4"
    calls: list[list[str]] = []
    monkeypatch.setattr(media, "MEDIA_WORK_ROOT", work_root.resolve())
    monkeypatch.setattr(
        media.subprocess, "run", lambda command, **_kwargs: calls.append(command)
    )

    assert media.trim_clip(source, trimmed, start=1.5, duration=4.0) == trimmed.resolve()
    assert media.cover_regions(
        source,
        redacted,
        regions=(media.CoverRegion(x=10, y=20, width=300, height=40),),
    ) == redacted.resolve()

    assert "-an" in calls[0]
    assert "-an" in calls[1]
    assert "drawbox=x=10:y=20:w=300:h=40:color=0x081522@1:t=fill" in calls[1]
    assert all("blur" not in " ".join(command).lower() for command in calls)


def test_cover_regions_rejects_more_than_twenty_rectangles_before_running_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: an oversized redaction plan creates an unreviewable filter chain."""
    from scripts.caspian_video import media

    work_root = tmp_path / "work" / "caspian-video"
    source = work_root / "raw" / "channels.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    monkeypatch.setattr(media, "MEDIA_WORK_ROOT", work_root.resolve())
    monkeypatch.setattr(media.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("ffmpeg ran"))
    regions = tuple(media.CoverRegion(x=index, y=0, width=1, height=1) for index in range(21))

    with pytest.raises(media.MediaPathError, match="^redaction regions invalid$"):
        media.cover_regions(source, work_root / "redacted" / "channels.mp4", regions)


def test_cover_region_rejects_non_integer_json_coordinates() -> None:
    """Break caught: numeric-looking JSON strings become unreviewed screen coordinates."""
    from pydantic import ValidationError

    from scripts.caspian_video.media import CoverRegion

    with pytest.raises(ValidationError):
        CoverRegion.model_validate({"x": "10", "y": 20, "width": 300, "height": 40})


def test_load_cover_regions_rejects_non_array_and_preserves_a_fixed_exception_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: malformed stored redactions escape with coordinate or path details."""
    from scripts.caspian_video import media

    work_root = tmp_path / "work" / "caspian-video"
    regions_file = work_root / "redactions.json"
    regions_file.parent.mkdir(parents=True)
    regions_file.write_text('{"x": -1}', encoding="utf-8")
    monkeypatch.setattr(media, "MEDIA_WORK_ROOT", work_root.resolve())

    with pytest.raises(media.MediaPathError, match="^redaction regions invalid$") as raised:
        media.load_cover_regions(regions_file)

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_capture_cli_never_loads_video_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: local capture inherits the provider-command credential side effect."""
    from scripts.caspian_video import __main__ as command_line
    from scripts.caspian_video import media

    work_root = tmp_path / "work" / "caspian-video"
    received: list[tuple[str, Path]] = []
    monkeypatch.setattr(media, "MEDIA_WORK_ROOT", work_root.resolve())
    monkeypatch.setattr(command_line, "load_video_settings", lambda _path: pytest.fail("credentials loaded"))
    monkeypatch.setattr(
        command_line,
        "run_capture",
        lambda name, root: received.append((name, root)) or root / "raw" / f"{name}.mp4",
    )

    assert command_line.main(["capture", "--name", "capture-smoke"]) == 0
    assert received == [("capture-smoke", work_root.resolve())]
