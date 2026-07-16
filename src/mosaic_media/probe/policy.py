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
]

AnalysisReason = Literal[
    "variable_frame_rate",
    "unreliable_timing_metadata",
    "rotated",
    "non_square_pixels",
    "interlaced",
]

StreamTranscode = Literal["required", "recommended"]

# A hard reason means the browser's rendering disagrees with our coordinate or
# time model. A soft reason means the video plays correctly, but not well, or
# not everywhere.
HARD_STREAM_REASONS: frozenset[StreamReason] = frozenset(
    {
        "unsupported_container",
        "unsupported_codec",
        "variable_frame_rate",
        "rotated",
        "non_square_pixels",
        "non_zero_start_time",
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


DEFAULT_THRESHOLDS = Thresholds()
