"""Every reason-to-operation mapping, built from Verdict and MediaFacts values directly."""

from dataclasses import replace
from pathlib import Path
from typing import TypedDict

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


class TimestampLessOverrides(TypedDict):
    """The exact keys TIMESTAMP_LESS carries.

    Typed rather than `dict[str, object]` so that unpacking it into
    `command_for` stays checkable: against an open key type the checker cannot
    rule out that the unpack supplies `allow_hardware`, and the call fails on
    `object` not being assignable to `bool`.
    """

    timing_measured: bool
    fps: float
    duration: float
    constant_frame_rate: bool


# A source whose packets carry no timestamps. The measured values are cleared
# alongside the flag because probe_media sets them to placeholders whenever
# timing is unmeasured; facts mixing timing_measured=False with measured values
# model a state the probe never mints.
TIMESTAMP_LESS: TimestampLessOverrides = {
    "timing_measured": False,
    "fps": 0.0,
    "duration": 0.0,
    "constant_frame_rate": False,
}


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


def test_timestamp_less_source_sets_timestamps_on_the_analysis_remux() -> None:
    command = command_for(
        "analysis", ANALYSIS_ENCODING, **TIMESTAMP_LESS, declared_fps=30.0
    )
    assert command is not None
    assert command.operation is Operation.REMUX_TIMEBASE
    assert arg_after(command.argv, "-bsf:v") == "setts=ts=N/30.000000/TB"
    # setts supplies the timestamps, so nothing is left for genpts to generate.
    assert "+genpts" not in command.argv


def test_timestamp_less_source_sets_timestamps_on_the_playback_rewrap() -> None:
    command = command_for(
        "playback",
        PLAYBACK_ENCODING,
        **TIMESTAMP_LESS,
        declared_fps=30.0,
        container="avi",
    )
    assert command is not None
    assert command.operation is Operation.REMUX_CONTAINER
    assert arg_after(command.argv, "-bsf:v") == "setts=ts=N/30.000000/TB"


def test_fractional_rate_renders_at_six_decimals() -> None:
    command = command_for(
        "analysis", ANALYSIS_ENCODING, **TIMESTAMP_LESS, declared_fps=30000 / 1001
    )
    assert command is not None
    assert arg_after(command.argv, "-bsf:v") == "setts=ts=N/29.970030/TB"


def test_timestamp_less_source_without_a_rate_keeps_the_generated_timestamps() -> None:
    command = command_for(
        "analysis", ANALYSIS_ENCODING, **TIMESTAMP_LESS, declared_fps=0.0
    )
    assert command is not None
    assert "-bsf:v" not in command.argv
    assert "+genpts" in command.argv


def test_lying_header_source_never_sets_timestamps() -> None:
    # A lying header selects REMUX_TIMEBASE too, and there declared_fps is the
    # very rate the remux exists to correct. Writing it into the timestamps
    # would make the lie the file's truth.
    command = command_for(
        "analysis", ANALYSIS_ENCODING, declared_fps=1000.0, declared_frame_count=0
    )
    assert command is not None
    assert command.operation is Operation.REMUX_TIMEBASE
    assert "-bsf:v" not in command.argv
    assert "+genpts" in command.argv


def test_measured_timing_playback_rewrap_never_sets_timestamps() -> None:
    command = command_for("playback", PLAYBACK_ENCODING, container="avi")
    assert command is not None
    assert command.operation is Operation.REMUX_CONTAINER
    assert "-bsf:v" not in command.argv


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


def test_a_copy_that_would_drop_leading_packets_reencodes_instead() -> None:
    # A stream copy drops a source's leading non-keyframes, so the derivative
    # loses them and its clock keeps their offset. Only a re-encode can
    # materialize them.
    command = command_for(
        "playback", PLAYBACK_ENCODING, container="avi", leading_non_keyframe_frames=24
    )
    assert command is not None
    assert command.operation is Operation.REENCODE_AV1
    assert arg_after(command.argv, "-flags2") == "+showall"


def test_a_faststart_copy_that_would_drop_leading_packets_reencodes_instead() -> None:
    # The escalation covers every copy operation, including the faststart remux
    # whose reason is soft -- moov_not_at_start is absent from
    # HARD_STREAM_REASONS, so this is the case where "one rule, no exception"
    # costs something. The container-remux case above cannot reach it: an
    # unsupported container selects REMUX_CONTAINER before moov placement is
    # consulted, so with container="avi" the faststart branch never runs.
    command = command_for(
        "playback",
        PLAYBACK_ENCODING,
        moov_at_start=False,
        leading_non_keyframe_frames=1,
    )
    assert command is not None
    assert command.operation is Operation.REENCODE_AV1
    assert command.reasons == frozenset({"moov_not_at_start"})
    assert arg_after(command.argv, "-flags2") == "+showall"
    assert "libsvtav1" in command.argv
    # Escalating must not drop the fix for the reason that selected the
    # operation in the first place: the output still has to carry its moov at
    # the front, or the derivative is re-encoded and still unplayable.
    assert arg_after(command.argv, "-movflags") == "+faststart"


def test_a_copy_that_would_drop_discard_packets_reencodes_instead() -> None:
    command = command_for(
        "analysis",
        ANALYSIS_ENCODING,
        declared_fps=1000.0,
        declared_frame_count=0,
        discard_flagged_packets=3,
    )
    assert command is not None
    assert command.operation is Operation.REENCODE_AV1
    assert arg_after(command.argv, "-ignore_editlist") == "1"


def test_the_output_clock_is_never_shifted_to_hide_a_dropped_prefix() -> None:
    # `-avoid_negative_ts make_zero` shifts the clock instead of keeping the
    # packets, hiding the loss rather than avoiding it, and rebases every stream
    # against the earliest timestamp in any of them -- for a source carrying AAC,
    # the encoder delay ahead of its first audio sample -- moving a clean file's
    # video off zero. Pinning its absence keeps a later simplification from
    # reaching for it.
    command = command_for("playback", PLAYBACK_ENCODING, container="avi")
    assert command is not None
    assert "-avoid_negative_ts" not in command.argv


def test_an_unverified_codec_selects_a_reencode() -> None:
    command = command_for("analysis", ANALYSIS_ENCODING, codec_name="indeo5")
    assert command is not None
    assert command.reasons == frozenset({"unverified_frame_correspondence"})
    assert command.operation is Operation.REENCODE_AV1


def test_a_copy_the_target_container_cannot_carry_reencodes_instead() -> None:
    # mp4 has no tag for vp8, so a copy remux into it fails in the muxer before a
    # header is written. The converter always writes mp4, so the minimum operation
    # that produces an output passing the analysis verdict is a re-encode, not the
    # copy the reason alone would select.
    command = command_for(
        "analysis",
        ANALYSIS_ENCODING,
        codec_name="vp8",
        container="matroska,webm",
        declared_fps=1000.0,
        declared_frame_count=0,
    )
    assert command is not None
    assert command.reasons == frozenset({"unreliable_timing_metadata"})
    assert command.operation is Operation.REENCODE_AV1
    assert "libsvtav1" in command.argv


def test_a_copy_the_target_container_can_carry_stays_a_copy() -> None:
    # The escalation above must not swallow the copy remux it sits next to: h264
    # goes into mp4 unchanged, so the cheap operation stays selected.
    command = command_for(
        "analysis",
        ANALYSIS_ENCODING,
        codec_name="h264",
        declared_fps=1000.0,
        declared_frame_count=0,
    )
    assert command is not None
    assert command.operation is Operation.REMUX_TIMEBASE
    assert arg_after(command.argv, "-c") == "copy"


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
