"""One full-file scan per video. Demultiplex only; no frame is decoded here."""

from pathlib import Path

from .boxes import moov_at_start
from .facts import MediaFacts
from .ffprobe import (
    Packet,
    TimestampSource,
    prober_version,
    read_header,
    scan_packets,
)
from .gop import measure_gop
from .identity import IDENTITY_SCHEME, mint_identity
from .policy import DEFAULT_THRESHOLDS, Thresholds
from .timing import measure_timing


def _leading_non_keyframe_frames(
    packets: tuple[Packet, ...], source: TimestampSource
) -> int:
    """Frames preceding the first keyframe.

    A frame is a distinct presentation timestamp, the unit frame_count uses, so
    a container carrying several packets at one timestamp contributes one. A
    distinct timestamp counts as a keyframe timestamp when any packet bearing it
    is keyframe-flagged, matching build_seek_index, so this count and the index's
    keyframe ranks cannot disagree.

    A stream whose packets carry no timestamps is counted in packet order
    instead. Every such packet holds the same 0.0 placeholder, so a comparison
    of timestamps returns 0 no matter how many frames precede the first
    keyframe -- structurally, for every file of that class. Packet order is the
    only order such a stream has, and it carries the signal: one access unit per
    frame, delivered in the order the bitstream states them.

    Zero for a stream with no keyframe flags at all, in either counting order:
    it decodes from its first packet, so nothing precedes a keyframe.
    """
    if source == "none":
        for position, packet in enumerate(packets):
            if packet.keyframe:
                return position
        return 0
    keyframe_times = {packet.time for packet in packets if packet.keyframe}
    if not keyframe_times:
        return 0
    first_keyframe_time = min(keyframe_times)
    return len({packet.time for packet in packets if packet.time < first_keyframe_time})


def probe_media(path: Path, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> MediaFacts:
    """Measure everything about `path`. Raises `MediaProbeError` when the file
    carries no video stream, no packet timestamps, or ffprobe fails.

    `thresholds` reaches this function only for `drift_frame_periods`, which
    decides `constant_frame_rate`. Every other threshold is applied in `derive`.
    """
    header = read_header(path)
    packets, source = scan_packets(path, header.video_position)
    gop = measure_gop(packets)
    if source == "none":
        # A raw elementary stream carries no timestamps: there is nothing to
        # fit a grid over. The packet count is still a real frame count (one
        # access unit per frame); fps and duration are unmeasurable and stay
        # 0.0. The verdict routes such a file to a timestamp-generating remux
        # through unreliable_timing_metadata.
        duration = 0.0
        fps = 0.0
        frame_count = len(packets)
        constant_frame_rate = False
        max_instantaneous_fps: float | None = None
        timing_measured = False
        # No timestamps, so no step between them to measure.
        max_timestamp_gap_frame_periods = 0.0
        # avg_frame_rate here is the h264 demuxer's fixed default, read from
        # nothing in the file. The rate the bitstream itself states is the only
        # honest answer, and 0.0 when it states none.
        declared_fps = header.elementary_stream_fps
    else:
        timing = measure_timing(packets, thresholds.drift_frame_periods)
        duration = timing.duration
        fps = timing.fps
        frame_count = timing.frame_count
        constant_frame_rate = timing.constant_frame_rate
        max_instantaneous_fps = timing.max_instantaneous_fps
        timing_measured = True
        max_timestamp_gap_frame_periods = timing.max_timestamp_gap_frame_periods
        declared_fps = header.declared_fps

    identity = mint_identity(header, packets, timing_measured=timing_measured)

    return MediaFacts(
        container=header.container,
        codec_name=header.codec_name,
        pixel_format=header.pixel_format,
        color_range=header.color_range,
        color_primaries=header.color_primaries,
        color_transfer=header.color_transfer,
        width=header.width,
        height=header.height,
        rotation_degrees=header.rotation_degrees,
        square_pixels=header.square_pixels,
        progressive=header.progressive,
        has_audio=header.has_audio,
        video_stream_count=header.video_stream_count,
        duration=duration,
        fps=fps,
        frame_count=frame_count,
        start_time=header.start_time,
        constant_frame_rate=constant_frame_rate,
        max_instantaneous_fps=max_instantaneous_fps,
        declared_duration=header.declared_duration,
        declared_fps=declared_fps,
        declared_frame_count=header.declared_frame_count,
        moov_at_start=moov_at_start(path),
        max_keyframe_interval_frames=gop.max_keyframe_interval_frames,
        max_gop_bytes=gop.max_gop_bytes,
        discard_flagged_packets=sum(1 for packet in packets if packet.discard),
        leading_non_keyframe_frames=_leading_non_keyframe_frames(packets, source),
        max_timestamp_gap_frame_periods=max_timestamp_gap_frame_periods,
        timing_measured=timing_measured,
        video_uuid=identity.video_uuid,
        content_digest=identity.content_digest,
        identity_scheme=IDENTITY_SCHEME,
        prober_version=prober_version(),
    )
