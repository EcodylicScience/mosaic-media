"""Injected policy. The package encodes no opinion about any one browser."""

from dataclasses import dataclass
from typing import Literal

StreamReason = Literal[
    "unsupported_container",
    "unsupported_codec",
    "variable_frame_rate",
    "rotated",
    "non_square_pixels",
    "non_zero_start_time",
    "client_dependent_decode",
    "interlaced",
    "moov_not_at_start",
    "large_seek_payload",
    "sparse_keyframes",
    "presentation_timing_requires_decode",
]

AnalysisReason = Literal[
    "variable_frame_rate",
    "unreliable_timing_metadata",
    "rotated",
    "non_square_pixels",
    "interlaced",
    "unverified_frame_correspondence",
    "presentation_timing_requires_decode",
]

StreamTranscode = Literal["required", "recommended"]

# A hard reason means the browser's rendering disagrees with our coordinate or
# time model. A soft reason means the video plays correctly, but not well, or
# not everywhere.
#
# Timing assigned to the wrong pictures breaks the mapping from frame index to
# time, which is what a hard reason means. Every source that fires it today also
# fires unsupported_container, so nothing in the corpus changes classification --
# which is why the membership is decided here rather than left to be noticed.
HARD_STREAM_REASONS: frozenset[StreamReason] = frozenset(
    {
        "unsupported_container",
        "unsupported_codec",
        "variable_frame_rate",
        "rotated",
        "non_square_pixels",
        "non_zero_start_time",
        "presentation_timing_requires_decode",
    }
)


@dataclass(frozen=True, slots=True)
class PlaybackProfile:
    """What a target browser opens and decodes. `containers` holds ffprobe
    `format_name` strings, never file extensions."""

    containers: frozenset[str]
    codecs: frozenset[str]
    client_dependent_codecs: frozenset[str]
    baseline_pixel_formats: frozenset[str]


# Measured against Chrome 149 on Linux with real <video> elements.
CHROME_149 = PlaybackProfile(
    containers=frozenset({"mov,mp4,m4a,3gp,3g2,mj2", "matroska,webm"}),
    codecs=frozenset({"h264", "hevc", "av1", "vp9", "vp8"}),
    client_dependent_codecs=frozenset({"hevc"}),
    baseline_pixel_formats=frozenset({"yuv420p", "yuvj420p"}),
)


# Codecs whose decoder emits exactly one frame per packet bearing a distinct
# presentation timestamp, once the reader's recovery options are in force. Every
# member is measured by the delivery test in the reader recovery suite; a codec
# is not admitted on decoder-family reasoning, because a wrong member's failure
# mode is the silent one this design exists to remove -- a neighboring frame
# returned as if it were the right one.
#
# Being unable to encode a codec here does not bar it: the h264 and hevc samples
# are committed under tests/assets/ and read with no added dependency, since both
# decoders are native and LGPL. Injected like every other policy in this module,
# so a codec this set omits is added by measuring it and supplying the wider
# set, without touching this package.
#
# Equal to CHROME_149.codecs today, and independent of it. One says a decoder
# emits a frame per packet, the other says a browser can play the stream; they
# coincide by accident and will diverge the first time a codec is trusted for
# decode but unsupported by the profile, or the reverse. Do not fold either into
# the other, and note that the two literals are currently byte-identical, so
# nothing that selects one by its contents can tell them apart.
FRAME_EXACT_CODECS: frozenset[str] = frozenset({"h264", "hevc", "av1", "vp9", "vp8"})


@dataclass(frozen=True, slots=True)
class Thresholds:
    """`max_gop_bytes` is the payload a seek may fetch before it costs 168 ms on
    a 25 Mbit/s link. `max_keyframe_interval_frames` bounds decode work; it has
    never fired on its own and exists for the low-bitrate, enormous-GOP case."""

    drift_frame_periods: float = 0.5
    max_gop_bytes: int = 524_288
    max_keyframe_interval_frames: int = 200
    truncation_duration_ratio: float = 0.95
    start_time_frame_periods: float = 0.5
    frame_exact_codecs: frozenset[str] = FRAME_EXACT_CODECS


DEFAULT_THRESHOLDS = Thresholds()
