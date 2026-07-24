"""Tests for the shared subprocess runner and its failure vocabulary.

The runner is exercised with `sys.executable` rather than ffmpeg: its contract
is about exit codes, timeouts, and a missing binary, none of which need a real
media command, and a scripted child makes each failure deterministic.
"""

import sys
from pathlib import Path

import pytest

from mosaic_media.ffmpeg import (
    failed_message,
    not_found_message,
    require_output,
    run_to_completion,
    timed_out_message,
)


class _CallerError(RuntimeError):
    """Stands in for MediaProbeError or TranscodeError at the injection point."""


def test_stdout_is_returned_on_a_clean_exit() -> None:
    command = [sys.executable, "-c", "print('probe output')"]
    assert (
        run_to_completion(
            command, timeout=30, action="running", error_type=_CallerError
        )
        == "probe output\n"
    )


def test_a_missing_binary_raises_the_injected_error_naming_the_binary() -> None:
    command = ["mosaic-media-absent-binary", "-version"]
    with pytest.raises(
        _CallerError, match="^mosaic-media-absent-binary binary not found on PATH"
    ):
        _ = run_to_completion(
            command, timeout=30, action="running", error_type=_CallerError
        )


def test_a_timeout_raises_the_injected_error_naming_the_action() -> None:
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    with pytest.raises(_CallerError, match="timed out scanning the packets"):
        _ = run_to_completion(
            command,
            timeout=0.1,
            action="scanning the packets",
            error_type=_CallerError,
        )


def test_a_nonzero_exit_raises_the_injected_error_carrying_stderr() -> None:
    script = "import sys; sys.stderr.write('no such file'); sys.exit(3)"
    command = [sys.executable, "-c", script]
    with pytest.raises(_CallerError, match="failed running: no such file"):
        _ = run_to_completion(
            command, timeout=30, action="running", error_type=_CallerError
        )


def test_a_nonzero_exit_without_stderr_still_carries_a_detail() -> None:
    command = [sys.executable, "-c", "raise SystemExit(3)"]
    with pytest.raises(_CallerError, match="failed running: unknown error"):
        _ = run_to_completion(
            command, timeout=30, action="running", error_type=_CallerError
        )


def test_require_output_accepts_a_non_empty_file(tmp_path: Path) -> None:
    destination = tmp_path / "thumb.jpg"
    _ = destination.write_bytes(b"\xff\xd8\xff")
    require_output(destination, binary="ffmpeg", error_type=_CallerError)


def test_require_output_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(_CallerError, match="produced no output at"):
        require_output(
            tmp_path / "absent.jpg", binary="ffmpeg", error_type=_CallerError
        )


def test_require_output_rejects_an_empty_file(tmp_path: Path) -> None:
    destination = tmp_path / "thumb.jpg"
    _ = destination.write_bytes(b"")
    with pytest.raises(_CallerError, match="produced no output at"):
        require_output(destination, binary="ffmpeg", error_type=_CallerError)


def test_not_found_message_names_the_binary() -> None:
    exc = FileNotFoundError(2, "No such file or directory")
    message = not_found_message("ffprobe", exc)
    assert message.startswith("ffprobe binary not found on PATH: ")


def test_timed_out_message_omits_the_limit_when_the_caller_has_none() -> None:
    message = timed_out_message("ffprobe", "reading the header of /clip.mp4")
    assert message == "ffprobe timed out reading the header of /clip.mp4"


def test_timed_out_message_names_the_limit_when_the_caller_tracks_one() -> None:
    message = timed_out_message("ffmpeg", "transcoding /clip.mp4", timeout=3600.0)
    assert message == "ffmpeg timed out after 3600s transcoding /clip.mp4"


def test_failed_message_strips_the_stderr_detail() -> None:
    message = failed_message("ffmpeg", "downscaling /frame.png", "  boom  \n")
    assert message == "ffmpeg failed downscaling /frame.png: boom"


def test_failed_message_falls_back_when_stderr_is_blank() -> None:
    message = failed_message("ffmpeg", "downscaling /frame.png", "   \n")
    assert message == "ffmpeg failed downscaling /frame.png: unknown error"
