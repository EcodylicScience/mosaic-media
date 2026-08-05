"""Two transcode targets, derived from measured facts.

This module performs no I/O: it reads the facts a probe already measured
and returns a verdict. It imports the probe's own vocabulary -- the timing
provenance alias and the predicate over it -- which reads nothing either.

Reasons are a set, not a single value, because the reason selects the command.
A constant-rate file with a lying header needs a `-c copy` remux with a corrected
timebase; a `moov` at the end needs `-movflags +faststart`; a genuinely
variable-rate file needs a real re-encode. Collapsing these into one reason
throws away the information that picks the cheap fix.
"""

from dataclasses import dataclass
from typing import Literal

from .facts import MediaFacts
from .ffprobe import timing_supplied_by_source
from .policy import (
    HARD_STREAM_REASONS,
    AnalysisReason,
    PlaybackProfile,
    StreamReason,
    StreamTranscode,
    Thresholds,
)

_FRAME_COUNT_TOLERANCE = 0.01
_FRAME_RATE_TOLERANCE = 0.05


@dataclass(frozen=True, slots=True)
class Verdict:
    playable: bool
    stream_transcode: StreamTranscode | None
    analysis_transcode: Literal["required"] | None
    stream_reasons: frozenset[StreamReason]
    analysis_reasons: frozenset[AnalysisReason]
    truncated: bool


def _timing_metadata_lies(facts: MediaFacts) -> bool:
    if facts.frame_count <= 0 or facts.fps <= 0.0:
        return False
    count_lies = (
        facts.declared_frame_count > 0
        and abs(facts.declared_frame_count - facts.frame_count) / facts.frame_count
        > _FRAME_COUNT_TOLERANCE
    )
    rate_lies = (
        facts.declared_fps > 0.0
        and abs(facts.declared_fps - facts.fps) / facts.fps > _FRAME_RATE_TOLERANCE
    )
    return count_lies or rate_lies


def derive(
    facts: MediaFacts, profile: PlaybackProfile, thresholds: Thresholds
) -> Verdict:
    """`playable` means the browser's rendering agrees with our coordinate and
    time model. A variable frame rate breaks the mapping from frame index to
    time; a non-zero start time breaks its origin; rotation and non-square pixels
    break the coordinate space itself.
    """
    stream: set[StreamReason] = set()
    analysis: set[AnalysisReason] = set()

    if facts.container not in profile.containers:
        stream.add("unsupported_container")
    if facts.codec_name not in profile.codecs:
        stream.add("unsupported_codec")
    if timing_supplied_by_source(facts.timing_source) and not facts.constant_frame_rate:
        # Variable frame rate is a measured claim; a stream whose timing the
        # file did not supply must not fire it, or a raw elementary stream would
        # be re-encoded when a timestamp-generating remux is the fix.
        stream.add("variable_frame_rate")
        analysis.add("variable_frame_rate")
    if facts.rotation_degrees != 0:
        stream.add("rotated")
        analysis.add("rotated")
    if not facts.square_pixels:
        stream.add("non_square_pixels")
        analysis.add("non_square_pixels")
    if facts.fps > 0.0:
        tolerance = thresholds.start_time_frame_periods / facts.fps
        if abs(facts.start_time) > tolerance:
            stream.add("non_zero_start_time")
    if not facts.progressive:
        stream.add("interlaced")
        analysis.add("interlaced")
    if facts.codec_name not in thresholds.frame_exact_codecs:
        analysis.add("unverified_frame_correspondence")

    if (
        facts.codec_name in profile.client_dependent_codecs
        or facts.pixel_format not in profile.baseline_pixel_formats
    ):
        stream.add("client_dependent_decode")
    if facts.moov_at_start is False:
        stream.add("moov_not_at_start")
    if facts.max_gop_bytes > thresholds.max_gop_bytes:
        stream.add("large_seek_payload")
    if facts.max_keyframe_interval_frames > thresholds.max_keyframe_interval_frames:
        stream.add("sparse_keyframes")

    truncated = (
        facts.declared_duration > 0.0
        and facts.duration / facts.declared_duration
        < thresholds.truncation_duration_ratio
    )
    # Truncation and a lying header look identical in the frame count. Only a
    # whole file's header can be said to lie about it.
    if not truncated and _timing_metadata_lies(facts):
        analysis.add("unreliable_timing_metadata")
    if facts.timing_source in ("absent", "synthesized"):
        # Timing the file did not supply. With none at all the frame-to-time
        # mapping is undefined until a remux generates real timestamps; with
        # timing the demultiplexer invented, every value measured from it
        # describes the invention rather than the file. `absent` is what must
        # not be dropped here: a raw stream stating no rate carries no other
        # analysis reason, so without this one the file reports as already
        # analysis-clean and the refusal that follows is never reached.
        analysis.add("unreliable_timing_metadata")
    if facts.timing_source == "synthesized" or (
        facts.coded_reordering_depth > 0
        and facts.timing_source in ("decode", "synthesized", "absent")
    ):
        # A stream copy cannot produce correct presentation timing here. With
        # invented timestamps the packet-to-picture mapping is unknown; with
        # reordering and no presentation timestamps the order is. A decoder
        # recovers both and a copy decodes nothing.
        analysis.add("presentation_timing_requires_decode")
        stream.add("presentation_timing_requires_decode")

    stream_reasons = frozenset(stream)
    if stream_reasons & HARD_STREAM_REASONS:
        stream_transcode: StreamTranscode | None = "required"
    elif stream_reasons:
        stream_transcode = "recommended"
    else:
        stream_transcode = None

    analysis_reasons = frozenset(analysis)
    return Verdict(
        playable=stream_transcode != "required",
        stream_transcode=stream_transcode,
        analysis_transcode="required" if analysis_reasons else None,
        stream_reasons=stream_reasons,
        analysis_reasons=analysis_reasons,
        truncated=truncated,
    )
