"""What one full-file scan measures about a video. No policy, no verdict."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MediaFacts:
    """Measured properties of a single video file.

    `declared_*` are the header's claims, retained only to be compared against
    measurement. `declared_fps` is `avg_frame_rate`, which is what OpenCV reads;
    `r_frame_rate` is neither the average nor an upper bound and is not stored.

    `timing_measured` is False for a stream whose packets carry no timestamps
    at all (a raw elementary stream such as a bare `.h264` file). Then `fps`
    and `duration` read 0.0 and `constant_frame_rate` reads False as
    placeholders, not measurements; `frame_count` is still real (the packet
    count). It defaults True so facts persisted before the field existed
    round-trip unchanged.
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
    timing_measured: bool = True
