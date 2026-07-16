"""Every reason-to-operation mapping, built from Verdict and MediaFacts values directly."""

from dataclasses import replace
from pathlib import Path

import pytest

from mosaic_media.probe.policy import CHROME_149, DEFAULT_THRESHOLDS
from mosaic_media.probe.verdict import derive
from mosaic_media.transcode import commands as commands_module
from mosaic_media.transcode.commands import (
    ANALYSIS_ENCODING,
    PLAYBACK_ENCODING,
    EncodingParameters,
    Operation,
    Target,
    TranscodeCommand,
    build_command,
)

# The 25-field clean baseline lives once, in the copied probe suite; reuse it
# instead of restating it (repo precedent: tests/probe/test_sequence.py imports
# CLEAN the same way).
from tests.probe.test_verdict import CLEAN

SOURCE = Path("/tmp/in.mkv")
DESTINATION = Path("/tmp/out.mp4")


def command_for(
    target: Target,
    encoding: EncodingParameters,
    *,
    allow_hardware: bool = False,
    **overrides: object,
) -> TranscodeCommand | None:
    facts = replace(CLEAN, **overrides)
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    return build_command(
        verdict,
        facts,
        target,
        SOURCE,
        DESTINATION,
        encoding=encoding,
        allow_hardware=allow_hardware,
    )


def arg_after(argv: tuple[str, ...], flag: str) -> str:
    index = argv.index(flag)
    return argv[index + 1]


def test_a_clean_file_needs_no_analysis_command() -> None:
    assert command_for("analysis", ANALYSIS_ENCODING) is None


def test_a_clean_file_needs_no_playback_command() -> None:
    assert command_for("playback", PLAYBACK_ENCODING) is None


def test_a_lying_header_is_a_copy_remux_with_regenerated_timestamps() -> None:
    command = command_for(
        "analysis", ANALYSIS_ENCODING, declared_fps=1000.0, declared_frame_count=0
    )
    assert command is not None
    assert command.operation is Operation.REMUX_TIMEBASE
    assert command.reasons == frozenset({"unreliable_timing_metadata"})
    assert "-c" in command.argv
    assert arg_after(command.argv, "-c") == "copy"
    assert arg_after(command.argv, "-fflags") == "+genpts"
    assert "libsvtav1" not in command.argv
    assert command.argv[-1] == str(DESTINATION)


def test_a_tail_moov_is_a_faststart_remux() -> None:
    command = command_for("playback", PLAYBACK_ENCODING, moov_at_start=False)
    assert command is not None
    assert command.operation is Operation.REMUX_FASTSTART
    assert command.reasons == frozenset({"moov_not_at_start"})
    assert arg_after(command.argv, "-c") == "copy"
    assert arg_after(command.argv, "-movflags") == "+faststart"
    assert "libsvtav1" not in command.argv


def test_an_unsupported_container_with_a_supported_codec_is_a_container_remux() -> None:
    command = command_for("playback", PLAYBACK_ENCODING, container="avi")
    assert command is not None
    assert command.operation is Operation.REMUX_CONTAINER
    assert command.reasons == frozenset({"unsupported_container"})
    assert arg_after(command.argv, "-c") == "copy"
    assert "libsvtav1" not in command.argv


def test_variable_frame_rate_drives_an_analysis_reencode() -> None:
    command = command_for(
        "analysis",
        ANALYSIS_ENCODING,
        constant_frame_rate=False,
        max_instantaneous_fps=30.0,
    )
    assert command is not None
    assert command.operation is Operation.REENCODE_AV1
    assert "variable_frame_rate" in command.reasons
    assert "libsvtav1" in command.argv
    assert arg_after(command.argv, "-fps_mode") == "cfr"
    assert "-r" in command.argv
    assert "-an" in command.argv


def test_variable_frame_rate_playback_reencode_keeps_audio_and_caps_gop() -> None:
    command = command_for(
        "playback",
        PLAYBACK_ENCODING,
        constant_frame_rate=False,
        max_instantaneous_fps=30.0,
        has_audio=True,
    )
    assert command is not None
    assert command.operation is Operation.REENCODE_AV1
    assert arg_after(command.argv, "-c:a") == "aac"
    assert arg_after(command.argv, "-g") == "50"


def test_rotation_drives_an_av1_reencode() -> None:
    command = command_for("playback", PLAYBACK_ENCODING, rotation_degrees=90)
    assert command is not None
    assert command.operation is Operation.REENCODE_AV1
    assert "rotated" in command.reasons
    assert "libsvtav1" in command.argv


def test_non_square_pixels_add_a_setsar_filter() -> None:
    command = command_for("analysis", ANALYSIS_ENCODING, square_pixels=False)
    assert command is not None
    assert command.operation is Operation.REENCODE_AV1
    chain = arg_after(command.argv, "-vf")
    assert "setsar=1" in chain
    assert "scale=" in chain


def test_interlacing_adds_a_deinterlace_filter() -> None:
    command = command_for("analysis", ANALYSIS_ENCODING, progressive=False)
    assert command is not None
    assert command.operation is Operation.REENCODE_AV1
    assert "interlaced" in command.reasons
    assert "yadif" in arg_after(command.argv, "-vf")


def test_hardware_selects_nvenc_when_allowed_and_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(commands_module.hwaccel, "encoder_available", lambda name: True)
    command = command_for(
        "playback", PLAYBACK_ENCODING, allow_hardware=True, rotation_degrees=90
    )
    assert command is not None
    assert "av1_nvenc" in command.argv
    assert "-cq" in command.argv
    assert "libsvtav1" not in command.argv


def test_hardware_is_ignored_when_not_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands_module.hwaccel, "encoder_available", lambda name: True)
    command = command_for(
        "playback", PLAYBACK_ENCODING, allow_hardware=False, rotation_degrees=90
    )
    assert command is not None
    assert "libsvtav1" in command.argv
    assert "av1_nvenc" not in command.argv


def test_analysis_and_playback_targets_are_independent() -> None:
    # A tail moov is a playback concern and not an analysis one.
    assert command_for("analysis", ANALYSIS_ENCODING, moov_at_start=False) is None
    playback = command_for("playback", PLAYBACK_ENCODING, moov_at_start=False)
    assert playback is not None
    assert playback.operation is Operation.REMUX_FASTSTART
