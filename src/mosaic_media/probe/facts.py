"""What one full-file scan measures about a video. No policy, no verdict."""

from dataclasses import dataclass

# This dependency runs one way and must stay that way: the facts read the
# probe's vocabulary, and `ffprobe` never imports this module.
from .ffprobe import TimingSource


@dataclass(frozen=True, slots=True)
class MediaFacts:
    """Measured properties of a single video file.

    `declared_*` are the header's claims, retained only to be compared against
    measurement. `declared_fps` is `avg_frame_rate`, which is what OpenCV reads,
    for every stream whose packets carry timestamps. For one whose packets carry
    none, `avg_frame_rate` is a demuxer default read from nothing in the file,
    and `declared_fps` instead carries the rate the elementary stream states in
    its own bitstream, or 0.0 when no bitstream rate is derived. `timing_source`
    is what tells the two apart.

    `timing_source` records where the timing came from. `presentation` and
    `decode` are both timing the file supplied, the second read from decode
    timestamps because presentation ones were absent for every packet.

    `absent` is a stream whose packets carry no timestamps at all (a raw
    elementary stream such as a bare `.h264` file). Then `fps` and `duration`
    read 0.0 and `constant_frame_rate` reads False as placeholders, not
    measurements; `frame_count` is still real (the packet count).

    `synthesized` is a format whose demultiplexer manufactured the timestamps
    the file does not carry. There are timestamps to measure, so the timing
    values are arithmetic rather than placeholders, but every one of them
    describes the invention rather than the file.

    It is required rather than defaulted, because any default asserts a
    provenance nothing measured. An absent digest is at least detectably
    absent; a provenance that filled itself in is not.

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

    `identity_scheme` and `prober_version` say which regime minted those two
    values: the declared scheme version, and the ffprobe build whose demuxer
    output the digest is defined against. Neither is hashed -- they are
    provenance, so folding them in would re-mint every value on an ffmpeg patch
    release. They are what tells a re-mint under a new scheme apart from a
    file whose content actually changed. Required like the identity
    values, and empty for the same reason: a source the probe never saw was
    minted by nothing.

    `discard_flagged_packets` and `leading_non_keyframe_frames` count what a
    default decode would not turn into frames for reasons visible at
    demultiplex time: the first are packets the demuxer marked "do not
    present", the second are frames preceding the first keyframe, which have no
    reference picture. Both are recoverable by the reader, and both tell command
    construction that a stream copy would lose them. Neither is a defect on its
    own.

    The units differ and the names say so. `discard_flagged_packets` counts
    packets. `leading_non_keyframe_frames` counts frames in this model's sense,
    one per distinct presentation timestamp, the same unit `frame_count` uses --
    so a container carrying several packets at one timestamp contributes one,
    and the count cannot disagree with the seek index's keyframe ranks.

    `leading_non_keyframe_frames` counts such a stream in packet order, which
    is the only order it has: its packets all carry one placeholder timestamp,
    so counting distinct timestamps below the first keyframe returns 0 for
    every file of the class, however many frames precede that keyframe. The
    count has to be real there, because the remux the verdict already routes
    such a stream to through `unreliable_timing_metadata` is a stream copy, and
    a copy drops exactly the frames this counts.

    `coded_reordering_depth` is how many pictures the bitstream may hold back
    before presenting one, which is what decides whether decode order is
    presentation order. Zero is a measurement rather than an absence: it says
    the bitstream reorders nothing, so a stream copy that synthesizes timestamps
    from the packet index labels the right pictures. It is read from the coded
    stream and is meaningful for every source, but only matters where the
    timestamps are not presentation timestamps -- a container carrying real ones
    already knows the order.

    `max_timestamp_gap_frame_periods` is the widest step between neighboring
    presentation timestamps, in frame periods -- 1.0 for a file whose timestamps
    are exactly uniform, more where a container quantizes them to a coarse tick
    or the rate genuinely varies. A reader that reaches a frame by counting
    decoded frames uses it to tell a missing frame from ordinary spacing: it is
    the widest two neighbors may legitimately sit apart, so anything wider is a
    frame the decoder did not produce.

    It is not derivable from `constant_frame_rate` or from the grid fit's drift.
    Constant-rate classification bounds each timestamp's deviation from its slot
    on the global grid, which bounds the neighbor step only at 1 + 2 * drift --
    2.0 periods under the shipped threshold, which is exactly the spacing one
    missing frame produces, so the bound cannot separate them. The measurement
    can: a file quantized to a coarse tick measures about 1.66 where a uniform
    one measures 1.0.

    0.0 for a stream whose packets carry no timestamps, alongside the other
    timing placeholders, since there are no timestamps to step between.
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
    discard_flagged_packets: int
    leading_non_keyframe_frames: int
    coded_reordering_depth: int
    max_timestamp_gap_frame_periods: float
    timing_source: TimingSource
    video_uuid: str
    content_digest: str
    identity_scheme: str
    prober_version: str
