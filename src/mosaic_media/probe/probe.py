"""One full-file scan per video. Demultiplex only; no frame is decoded here."""

from pathlib import Path

from .boxes import moov_at_start
from .facts import MediaFacts
from .ffprobe import read_header, scan_packets
from .gop import measure_gop
from .policy import DEFAULT_THRESHOLDS, Thresholds
from .timing import measure_timing


def probe_media(path: Path, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> MediaFacts:
    """Measure everything about `path`. Raises `MediaProbeError` when the file
    carries no video stream, no packet timestamps, or ffprobe fails.

    `thresholds` reaches this function only for `drift_frame_periods`, which
    decides `constant_frame_rate`. Every other threshold is applied in `derive`.
    """
    header = read_header(path)
    packets, _source = scan_packets(path, header.video_position)
    timing = measure_timing(packets, thresholds.drift_frame_periods)
    gop = measure_gop(packets)

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
        duration=timing.duration,
        fps=timing.fps,
        frame_count=timing.frame_count,
        start_time=header.start_time,
        constant_frame_rate=timing.constant_frame_rate,
        max_instantaneous_fps=timing.max_instantaneous_fps,
        declared_duration=header.declared_duration,
        declared_fps=header.declared_fps,
        declared_frame_count=header.declared_frame_count,
        moov_at_start=moov_at_start(path),
        max_keyframe_interval_frames=gop.max_keyframe_interval_frames,
        max_gop_bytes=gop.max_gop_bytes,
    )
