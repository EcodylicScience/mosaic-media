"""Verdict to ffmpeg argv. Pure construction, no I/O.

Exception: when the caller passes `allow_hardware=True`, the encoder-argument
selection consults `hwaccel.encoder_available`, a cached ffmpeg capability
probe that spawns a subprocess on a cold cache.

The command selects the minimum operation that clears a target's reasons, never a
blanket re-encode. A header that lies about timing needs a `-c copy` remux that
regenerates timestamps; a `moov` at the tail needs `-movflags +faststart`; a
supported stream trapped in an unopenable container needs a `-c copy` container
rewrap; only a defect that breaks the pixel grid or the frame clock -- variable
frame rate, rotation, non-square pixels, interlacing -- or a stream a browser
cannot decode needs a real AV1 re-encode.

Policy is injected. `EncodingParameters` and the two shipped defaults are
media-domain encoder settings; the browser policy lives in the `PlaybackProfile`
the caller passes to `derive`. This module holds no opinion about any browser.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from .. import hwaccel
from ..probe.facts import MediaFacts
from ..probe.policy import AnalysisReason, StreamReason
from ..probe.verdict import Verdict

Target = Literal["analysis", "playback"]

_BASE: tuple[str, ...] = ("ffmpeg", "-hide_banner", "-v", "error", "-y")


class Operation(StrEnum):
    REMUX_FASTSTART = "remux_faststart"
    REMUX_TIMEBASE = "remux_timebase"
    REMUX_CONTAINER = "remux_container"
    REENCODE_AV1 = "reencode_av1"


# Reasons a copy remux cannot fix: the pixel grid or the frame clock is wrong, or
# the stream itself cannot be decoded or streamed economically. Each forces a real
# AV1 re-encode. Names are the real StreamReason / AnalysisReason literals.
_REENCODE_STREAM_REASONS: frozenset[StreamReason] = frozenset(
    {
        "unsupported_codec",
        "variable_frame_rate",
        "rotated",
        "non_square_pixels",
        "non_zero_start_time",
        "client_dependent_decode",
        "interlaced",
        "large_seek_payload",
        "sparse_keyframes",
    }
)
_REENCODE_ANALYSIS_REASONS: frozenset[AnalysisReason] = frozenset(
    {
        "variable_frame_rate",
        "rotated",
        "non_square_pixels",
        "interlaced",
    }
)


@dataclass(frozen=True, slots=True)
class EncodingParameters:
    """Injected AV1 encoder settings.

    `quality` is the constant-quality target on the 0-63 scale shared by SVT-AV1
    `-crf` and NVENC `-cq`; lower is higher quality. `cpu_preset` is the SVT-AV1
    speed preset (0 slowest, 13 fastest); `nvenc_preset` is the NVENC speed preset
    (`p1` slowest, `p7` fastest). `keyframe_interval` bounds the GOP so a seek
    fetches a bounded payload; None leaves the encoder default. `keep_audio`
    re-encodes an audio track to AAC when the source has one; False drops audio.
    """

    quality: int
    cpu_preset: int
    nvenc_preset: str
    pixel_format: str
    keyframe_interval: int | None
    keep_audio: bool


# The analysis derivative is measured, not watched: encoder-default GOP, no
# audio, and quality that errs toward fidelity. For a file the analysis
# verdict re-encodes, the derivative replaces the original as tracker input,
# so quantization loss propagates into that file's downstream measurements
# (clean files are never re-encoded and keep their originals); crf 14 sits
# inside the conventionally near-lossless band,
# below the "visually transparent" range (crf ~18-24), at roughly twice the
# storage of the earlier crf 20 by the ~6-crf-per-bitrate-doubling rule of
# thumb. A too-conservative value costs re-derivable storage; a too-lossy one
# silently degrades analysis results. The final value comes from corpus
# measurement (docs/issues/encoding-presets-unmeasured-against-quality-goals.md).
ANALYSIS_ENCODING = EncodingParameters(
    quality=14,
    cpu_preset=6,
    nvenc_preset="p5",
    pixel_format="yuv420p",
    keyframe_interval=None,
    keep_audio=False,
)

# The playback derivative is streamed and scrubbed: a capped GOP for seeking,
# audio preserved. crf 26 targets the upper visually-transparent range rather
# than the web-streaming point (crf ~28-38); whether it reaches the visually
# lossless goal is unmeasured, and the derivative is re-derivable, so the
# final value also comes from the corpus measurement tracked in the issue
# above.
PLAYBACK_ENCODING = EncodingParameters(
    quality=26,
    cpu_preset=8,
    nvenc_preset="p5",
    pixel_format="yuv420p",
    keyframe_interval=50,
    keep_audio=True,
)


@dataclass(frozen=True, slots=True)
class TranscodeCommand:
    """A fully formed ffmpeg invocation plus the intent behind it."""

    argv: tuple[str, ...]
    operation: Operation
    target: Target
    reasons: frozenset[str]
    output_path: Path


def _target_reasons(verdict: Verdict, target: Target) -> frozenset[str]:
    if target == "analysis":
        return frozenset(verdict.analysis_reasons)
    return frozenset(verdict.stream_reasons)


def _select_operation(verdict: Verdict, target: Target) -> Operation | None:
    if target == "analysis":
        if verdict.analysis_reasons & _REENCODE_ANALYSIS_REASONS:
            return Operation.REENCODE_AV1
        if "unreliable_timing_metadata" in verdict.analysis_reasons:
            return Operation.REMUX_TIMEBASE
        return None
    if verdict.stream_reasons & _REENCODE_STREAM_REASONS:
        return Operation.REENCODE_AV1
    if "unsupported_container" in verdict.stream_reasons:
        return Operation.REMUX_CONTAINER
    if "moov_not_at_start" in verdict.stream_reasons:
        return Operation.REMUX_FASTSTART
    return None


def _copy_remux_argv(
    source: Path,
    destination: Path,
    *,
    input_flags: tuple[str, ...] = (),
    timestamp_fps: float = 0.0,
) -> tuple[str, ...]:
    # A source whose packets carry no timestamps leaves the mp4 muxer to
    # synthesize them, which it warns is deprecated and which lands on an
    # approximation of the rate rather than the rate itself. setts computes each
    # timestamp from the frame index at the rate the stream declares, so the
    # packets arrive timestamped and the fallback is never entered.
    #
    # A stream whose sequence parameter set carries no timing states no rate, so
    # there is nothing to set and the fallback still runs for it. That case
    # loses its timestamps outright when the fallback is removed, which is a
    # visible failure rather than a silently invented rate.
    #
    # The timestamp is computed from the packet index, which is decode order.
    # That is presentation order only for a bitstream coded without frame
    # reordering. A raw stream carrying B-frames therefore receives presentation
    # times shuffled against its pictures, and the acceptance re-probe cannot
    # detect it: the values are uniform, complete, and start at zero, so the
    # output measures as constant-rate and correct while only their assignment
    # to pictures is wrong. Correcting that means re-encoding such a source
    # rather than copying it.
    timestamp_args: tuple[str, ...] = ()
    if timestamp_fps > 0.0:
        timestamp_args = ("-bsf:v", f"setts=ts=N/{timestamp_fps:.6f}/TB")
    return (
        *_BASE,
        *input_flags,
        "-i",
        str(source),
        "-c",
        "copy",
        *timestamp_args,
        "-movflags",
        "+faststart",
        str(destination),
    )


def _encoder_args(
    encoding: EncodingParameters, *, allow_hardware: bool
) -> tuple[str, ...]:
    if allow_hardware and hwaccel.encoder_available("av1_nvenc"):
        return (
            "-c:v",
            "av1_nvenc",
            "-preset",
            encoding.nvenc_preset,
            "-cq",
            str(encoding.quality),
        )
    return (
        "-c:v",
        "libsvtav1",
        "-preset",
        str(encoding.cpu_preset),
        "-crf",
        str(encoding.quality),
    )


def _video_filters(facts: MediaFacts) -> str | None:
    filters: list[str] = []
    if not facts.progressive:
        filters.append("yadif")
    if not facts.square_pixels:
        # Bake the sample aspect ratio into square pixels; keep dimensions even.
        filters.append("scale=trunc(iw*sar/2)*2:trunc(ih/2)*2")
        filters.append("setsar=1")
    if not filters:
        return None
    return ",".join(filters)


def _reencode_argv(
    source: Path,
    destination: Path,
    facts: MediaFacts,
    encoding: EncodingParameters,
    *,
    allow_hardware: bool,
) -> tuple[str, ...]:
    argv: list[str] = [*_BASE, "-i", str(source)]
    chain = _video_filters(facts)
    if chain is not None:
        argv.extend(["-vf", chain])
    # Constant frame rate at the measured average resamples a variable source.
    # Rotation is baked by ffmpeg's default autorotation on re-encode, which also
    # clears the display-matrix side data; no explicit transpose is needed. An
    # unmeasured rate (a raw elementary stream) falls back to declared_fps, which
    # for such a source is the rate its bitstream states; with neither, the
    # resample is omitted and the muxer keeps the input timing.
    fps_value = facts.fps if facts.fps > 0.0 else facts.declared_fps
    if fps_value > 0.0:
        argv.extend(["-r", f"{fps_value:.6f}", "-fps_mode", "cfr"])
    argv.extend(_encoder_args(encoding, allow_hardware=allow_hardware))
    argv.extend(["-pix_fmt", encoding.pixel_format])
    if encoding.keyframe_interval is not None:
        argv.extend(["-g", str(encoding.keyframe_interval)])
    if encoding.keep_audio and facts.has_audio:
        argv.extend(["-c:a", "aac"])
    else:
        argv.append("-an")
    argv.extend(["-movflags", "+faststart", str(destination)])
    return tuple(argv)


def build_command(
    verdict: Verdict,
    facts: MediaFacts,
    target: Target,
    source: Path,
    destination: Path,
    *,
    encoding: EncodingParameters,
    allow_hardware: bool = False,
) -> TranscodeCommand | None:
    """The minimum ffmpeg command that clears `target`'s reasons, or None when the
    file is already clean for that target."""
    operation = _select_operation(verdict, target)
    if operation is None:
        return None
    # Only a source whose timing was never measured may have its timestamps
    # written from declared_fps. On a measured file that field can be the header
    # lie the remux exists to correct, and writing it in would make the lie the
    # file's truth.
    timestamp_fps = 0.0 if facts.timing_measured else facts.declared_fps
    if operation is Operation.REENCODE_AV1:
        argv = _reencode_argv(
            source, destination, facts, encoding, allow_hardware=allow_hardware
        )
    elif operation is Operation.REMUX_TIMEBASE:
        argv = _copy_remux_argv(
            source,
            destination,
            input_flags=() if timestamp_fps > 0.0 else ("-fflags", "+genpts"),
            timestamp_fps=timestamp_fps,
        )
    else:
        # REMUX_FASTSTART and REMUX_CONTAINER share the copy-remux argv; the
        # operation kind records which reason selected it. REMUX_FASTSTART is
        # unreachable for a timestamp-less source: moov_at_start returns None
        # for a stream whose first box is not ftyp, and the reason fires only on
        # False. That, not any assumption that a container implies timestamps.
        argv = _copy_remux_argv(source, destination, timestamp_fps=timestamp_fps)
    return TranscodeCommand(
        argv=argv,
        operation=operation,
        target=target,
        reasons=_target_reasons(verdict, target),
        output_path=destination,
    )
