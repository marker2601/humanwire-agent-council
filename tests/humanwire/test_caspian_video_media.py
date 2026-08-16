from pathlib import Path
from types import SimpleNamespace

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
    assert command[-2] == "-n"
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
    monkeypatch.setattr(media, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(media, "MEDIA_WORK_ROOT", work_root)

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
    monkeypatch.setattr(media, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(media, "MEDIA_WORK_ROOT", work_root)
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
    monkeypatch.setattr(media, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(media, "MEDIA_WORK_ROOT", work_root)
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
    monkeypatch.setattr(media, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(media, "MEDIA_WORK_ROOT", work_root)
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
    monkeypatch.setattr(media, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(media, "MEDIA_WORK_ROOT", work_root)

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
    monkeypatch.setattr(media, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(media, "MEDIA_WORK_ROOT", work_root)
    monkeypatch.setattr(command_line, "load_video_settings", lambda _path: pytest.fail("credentials loaded"))
    monkeypatch.setattr(
        command_line,
        "run_capture",
        lambda name, root: received.append((name, root)) or root / "raw" / f"{name}.mp4",
    )

    assert command_line.main(["capture", "--name", "capture-smoke"]) == 0
    assert received == [("capture-smoke", work_root.resolve())]


def test_run_capture_requires_the_trusted_repository_media_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: a caller redirects a supposedly local capture outside the repository."""
    from scripts.caspian_video import media

    repository_root = tmp_path / "repository"
    trusted_root = repository_root / "work" / "caspian-video"
    escaped_root = tmp_path / "escaped"
    calls: list[list[str]] = []
    monkeypatch.setattr(media, "REPOSITORY_ROOT", repository_root)
    monkeypatch.setattr(media, "MEDIA_WORK_ROOT", trusted_root)
    monkeypatch.setattr(media.subprocess, "run", lambda command, **_kwargs: calls.append(command))

    with pytest.raises(media.MediaPathError, match="^media path invalid$"):
        media.run_capture("capture-smoke", escaped_root)

    assert calls == []


def test_trim_rejects_a_media_root_that_resolves_outside_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: trim trusts a redirected global media root outside the repository."""
    from scripts.caspian_video import media

    repository_root = tmp_path / "repository"
    escaped_root = tmp_path / "escaped"
    source = escaped_root / "raw" / "channels.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    monkeypatch.setattr(media, "REPOSITORY_ROOT", repository_root)
    monkeypatch.setattr(media, "MEDIA_WORK_ROOT", escaped_root)
    monkeypatch.setattr(media.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("ffmpeg ran"))

    with pytest.raises(media.MediaPathError, match="^media path invalid$"):
        media.trim_clip(source, escaped_root / "trimmed" / "channels.mp4", start=0, duration=1)


def test_safe_media_path_discards_private_resolve_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: a failed resolution retains a private filesystem path in its exception graph."""
    from scripts.caspian_video import media

    sentinel = "PRIVATE-PATH-SENTINEL"
    real_resolve = media.Path.resolve

    def hostile_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        if path.name == "private.mp4":
            raise OSError(sentinel)
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(media.Path, "resolve", hostile_resolve)

    with pytest.raises(media.MediaPathError, match="^media path invalid$") as raised:
        media.safe_media_path(tmp_path, "private.mp4")

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert sentinel not in repr(raised.value)


def test_capture_discards_private_mkdir_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: output-directory failure retains its private operating-system exception."""
    from scripts.caspian_video import media

    sentinel = "PRIVATE-MKDIR-SENTINEL"
    work_root = tmp_path / "work" / "caspian-video"
    monkeypatch.setattr(media, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(media, "MEDIA_WORK_ROOT", work_root)

    def hostile_mkdir(*_args: object, **_kwargs: object) -> None:
        raise OSError(sentinel)

    monkeypatch.setattr(media.Path, "mkdir", hostile_mkdir)

    with pytest.raises(media.MediaPathError, match="^media path invalid$") as raised:
        media.run_capture("capture-smoke", work_root)

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert sentinel not in repr(raised.value)


def test_trusted_media_root_rejects_a_flagged_literal_work_component(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: canonical work media is redirected to an unignored repository sibling."""
    from scripts.caspian_video import media

    repository_root = tmp_path / "repository"
    literal_root = repository_root / "work" / "caspian-video"
    monkeypatch.setattr(media, "REPOSITORY_ROOT", repository_root)
    monkeypatch.setattr(media, "MEDIA_WORK_ROOT", literal_root)
    monkeypatch.setattr(media, "_is_redirected_path", lambda path: path == literal_root)

    with pytest.raises(media.MediaPathError, match="^media path invalid$") as raised:
        media._trusted_media_root()

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_trusted_media_root_rejects_a_real_symlink_to_an_unignored_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: resolving the canonical work path silently follows a sibling symlink."""
    from scripts.caspian_video import media

    repository_root = tmp_path / "repository"
    literal_root = repository_root / "work" / "caspian-video"
    sibling = repository_root / "unignored-sibling"
    sibling.mkdir(parents=True)
    literal_root.parent.mkdir(parents=True)
    try:
        literal_root.symlink_to(sibling, target_is_directory=True)
    except OSError:
        pytest.skip("Windows symlink creation is unavailable in this environment")
    monkeypatch.setattr(media, "REPOSITORY_ROOT", repository_root)
    monkeypatch.setattr(media, "MEDIA_WORK_ROOT", literal_root)

    with pytest.raises(media.MediaPathError, match="^media path invalid$"):
        media._trusted_media_root()


def test_redirected_path_detects_reparse_metadata_even_when_target_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: a dangling redirected component is treated as an absent safe directory."""
    from scripts.caspian_video import media

    component = tmp_path / "work" / "caspian-video"
    monkeypatch.setattr(media.Path, "exists", lambda _path: False)
    monkeypatch.setattr(
        media.Path,
        "lstat",
        lambda _path: SimpleNamespace(st_file_attributes=media.stat.FILE_ATTRIBUTE_REPARSE_POINT),
    )
    monkeypatch.setattr(media.Path, "is_symlink", lambda _path: False)
    monkeypatch.setattr(media.os.path, "isjunction", lambda _path: False)

    assert media._is_redirected_path(component) is True


def test_redirected_path_discards_private_lstat_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: a failed reparse inspection retains its private filesystem error."""
    from scripts.caspian_video import media

    sentinel = "PRIVATE-LSTAT-SENTINEL"
    monkeypatch.setattr(media.Path, "lstat", lambda _path: (_ for _ in ()).throw(OSError(sentinel)))

    with pytest.raises(media.MediaPathError, match="^media path invalid$") as raised:
        media._is_redirected_path(tmp_path / "work")

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert sentinel not in repr(raised.value)
