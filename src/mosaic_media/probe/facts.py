"""What one full-file scan measures about a video. No policy, no verdict."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MediaFacts:
    """Measured properties of a single video file.

    `declared_*` are the header's claims, retained only to be compared against
    measurement. `declared_fps` is `avg_frame_rate`, which is what OpenCV reads;
    `r_frame_rate` is neither the average nor an upper bound and is not stored.
    """

    container: str
    codec_name: str
    pixel_format: str
    color_range: str
    color_primaries: str
    color_transfer: str
    width: int
    height: int
    rotation_degrees: int
    square_pixels: bool
    progressive: bool
    has_audio: bool
    video_stream_count: int
    duration: float
    fps: float
    frame_count: int
    start_time: float
    constant_frame_rate: bool
    max_instantaneous_fps: float | None
    declared_duration: float
    declared_fps: float
    declared_frame_count: int
    moov_at_start: bool | None
    max_keyframe_interval_frames: int
    max_gop_bytes: int
