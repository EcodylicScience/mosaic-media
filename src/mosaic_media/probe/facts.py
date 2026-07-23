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
    count).

    It is required rather than defaulting True, because True is the unsafe
    value: a caller that omitted it would assert measured timing for a file
    whose timing was never measured, and nothing downstream could tell that
    apart from a real measurement. An absent digest is at least detectably
    absent; an absent boolean is not.

    `video_uuid` and `content_digest` are the two derived identity values.
    `video_uuid` pins content and exact timing and is the only one that may be
    compared for identity or used to name anything. `content_digest` pins
    content alone, survives a container rewrite that preserves the elementary
    stream, and is the duplicate pathway's index key -- never an identity.

    Both are required rather than defaulted, so an absent identity is something
    a caller states rather than something that fills itself in. Absence is still
    a real state: a source the probe never saw -- an image-store recording, a
    directory of frames with no file to hash -- has no digest, and the caller
    building facts for one passes an empty string explicitly. The duplicate
    comparison reports such a pair as unminted rather than guessing.
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
    timing_measured: bool
    video_uuid: str
    content_digest: str
