"""Verdict to ffmpeg argv. Pure construction, no I/O.

Exception: when the caller passes `allow_hardware=True`, the encoder-argument
selection consults `hwaccel.encoder_available`, a cached ffmpeg capability
probe that spawns a subprocess on a cold cache.

The command selects the minimum operation that clears a target's reasons, never a
blanket re-encode. A header that lies about timing needs a `-c copy` remux that
regenerates timestamps; a `moov` at the tail needs `-movflags +faststart`; a
supported stream trapped in an unopenable container needs a `-c copy` container
rewrap; a defect that breaks the pixel grid or the frame clock -- variable frame
rate, rotation, non-square pixels, interlacing -- a stream a browser cannot
decode, or a codec whose frame correspondence is unverified needs a real AV1
re-encode.

A copy is escalated to a re-encode when it would not survive: the mp4 the
converter writes must be able to carry the codec, and the source's packets must
all decode. A copy carries packets forward untouched, so a source that loses
frames to an edit list or to leading non-keyframes yields a derivative that loses
them too.

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


# Reasons a copy remux cannot fix: the pixel grid or the frame clock is wrong, the
# stream itself cannot be decoded or streamed economically, or the codec is one
# whose decoder is not measured to emit a frame per packet -- a copy would carry
# that codec forward and the output would fire the same reason. Each forces a real
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
        "unverified_frame_correspondence",
    }
)

# Codecs the mp4 muxer carries through a stream copy. The converter always writes
# mp4, so a copy remux preserving a codec absent from this set dies in the muxer
# before a header is written -- "Could not find tag for codec ... not currently
# supported in container" -- and the source can never be prepared for its target.
# Such a copy escalates to a re-encode instead, which is the minimum operation
# that still produces output passing the target's verdict.
#
# An allowlist rather than a denylist, because the two errors cost differently:
# a codec missing from the set buys one unnecessary re-encode, while a codec
# wrongly present buys a transcode that fails. Membership is measured against the
# real muxer by tests/transcode/test_mp4_stream_copy.py, which muxes a real
# sample of every codec the suite can produce and derives this set from the
# outcome, so nothing joins it without a sample that carries.
#
# Plainly named because that test imports it, and not exported from the package.
# It looks like FRAME_EXACT_CODECS -- both are frozensets of codec names, both
# apparently about which codecs are acceptable -- and it is the opposite kind of
# fact. That one is injected policy a consumer may widen after measuring a codec
# this package has not; this one is a property of libavformat, changing when
# ffmpeg changes rather than when a consumer's preferences do, and no consumer
# can widen it because the muxer decides. Their safety properties are opposite
# too: widening the trusted set degrades gracefully, while widening this one
# asserts mp4 carries something it does not, which is the failing transcode
# named above.
#
# There is a real consumer question this set appears to answer and does not: the
# escalation carries no reason, so a caller cannot distinguish a re-encode the
# verdict demanded from one the muxer forced. If that distinction is ever needed,
# the answer is a reason on the command, not an exported constant a consumer
# re-derives the selector from.
MP4_STREAM_COPY_CODECS: frozenset[str] = frozenset(
    {
        "h264",
        "hevc",
        "av1",
        "vp9",
        "mpeg4",
        "mjpeg",
        "mpeg2video",
        "mpeg1video",
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


def _select_operation(
    verdict: Verdict, facts: MediaFacts, target: Target
) -> Operation | None:
    operation = _select_minimum_operation(verdict, target)
    if operation is Operation.REENCODE_AV1 or operation is None:
        return operation
    if facts.codec_name not in MP4_STREAM_COPY_CODECS:
        # The codec the copy would preserve cannot be muxed into the mp4 the
        # converter writes.
        return Operation.REENCODE_AV1
    if facts.discard_flagged_packets > 0 or facts.leading_non_keyframe_frames > 0:
        # A copy carries the source's packets, so a source whose packets do not
        # all decode yields a derivative whose packets do not all decode. Only a
        # re-encode materializes them. Muxability and deliverability are
        # independent reasons to escalate; a copy must survive both.
        return Operation.REENCODE_AV1
    return operation


def _select_minimum_operation(verdict: Verdict, target: Target) -> Operation | None:
    """The lightest operation that clears `target`'s reasons, before the output
    container's ability to carry the source codec is taken into account."""
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
    # The decoder emits frames before the first keyframe only when asked, and the
    # demuxer keeps edit-list packets only when told to ignore the edit list.
    # Without both, the re-encode reproduces the source's own dropped frames.
    input_flags: list[str] = ["-flags2", "+showall"]
    if facts.discard_flagged_packets > 0:
        input_flags.extend(["-ignore_editlist", "1"])
    argv: list[str] = [*_BASE, *input_flags, "-i", str(source)]
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
    file is already clean for that target.

    "Minimum" is bounded by what the output container accepts: a copy remux whose
    codec mp4 cannot carry is escalated to a re-encode, because a command the
    muxer refuses is not an operation at all.
    """
    operation = _select_operation(verdict, facts, target)
    if operation is None:
        return None
    # Only a source carrying no timestamps at all may have them written from a
    # declared rate. On a measured file that field can be the header lie the
    # remux exists to correct, and on a source whose timing was invented it is
    # the demultiplexer's own default -- writing either in would make it the
    # file's truth.
    timestamp_fps = facts.declared_fps if facts.timing_source == "absent" else 0.0
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
