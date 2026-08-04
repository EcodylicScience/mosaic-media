"""Two transcode targets, derived from measured facts. No I/O.

Reasons are a set, not a single value, because the reason selects the command.
A constant-rate file with a lying header needs a `-c copy` remux with a corrected
timebase; a `moov` at the end needs `-movflags +faststart`; a genuinely
variable-rate file needs a real re-encode. Collapsing these into one reason
throws away the information that picks the cheap fix.
"""

from dataclasses import dataclass
from typing import Literal

from .facts import MediaFacts
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
    if facts.timing_measured and not facts.constant_frame_rate:
        # Variable frame rate is a measured claim; an unmeasured stream must
        # not fire it, or a raw elementary stream would be re-encoded when a
        # timestamp-generating remux is the fix.
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
    if not facts.timing_measured:
        # No timestamps at all: fps, duration, and the frame-to-time mapping
        # are undefined until a remux generates real ones.
        analysis.add("unreliable_timing_metadata")

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
