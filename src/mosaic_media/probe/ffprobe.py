"""ffprobe subprocess wrappers. Standard library only.

Two calls per file: one JSON header read, and one demultiplex-only packet scan.
No frame is decoded.
"""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .errors import MediaProbeError

HEADER_TIMEOUT_SECONDS = 60
SCAN_TIMEOUT_SECONDS = 900

TimestampSource = Literal["pts", "dts"]

_ABSENT = ("", "N/A")


@dataclass(frozen=True, slots=True)
class Header:
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
    video_position: int
    start_time: float
    declared_duration: float
    declared_fps: float
    declared_frame_count: int


@dataclass(frozen=True, slots=True)
class Packet:
    time: float
    size: int
    keyframe: bool
    pos: int


def _run(command: list[str], timeout: int, action: str) -> str:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as exc:
        message = f"ffprobe binary not found on PATH: {exc}"
        raise MediaProbeError(message) from exc
    except subprocess.TimeoutExpired as exc:
        message = f"ffprobe timed out {action}"
        raise MediaProbeError(message) from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown error"
        message = f"ffprobe failed {action}: {detail}"
        raise MediaProbeError(message) from None
    return result.stdout


def _fraction(text: str) -> float:
    numerator, _, denominator = text.partition("/")
    if numerator in _ABSENT:
        return 0.0
    if not denominator:
        return float(numerator)
    divisor = float(denominator)
    return 0.0 if divisor == 0.0 else float(numerator) / divisor


def _number(value: object, default: float) -> float:
    if value is None:
        return default
    text = str(value)
    return default if text in _ABSENT else float(text)


def _rotation(stream: dict[str, object]) -> int:
    """Read rotation from display-matrix side data and normalize modulo 360.

    ffprobe reports it signed, commonly -90. A `rotate` stream tag is never
    consulted: no current ffmpeg writes one.
    """
    side_data = stream.get("side_data_list")
    if not isinstance(side_data, list):
        return 0
    for entry in side_data:
        if isinstance(entry, dict) and "rotation" in entry:
            return int(round(_number(entry["rotation"], 0.0))) % 360
    return 0


def _is_attached_picture(stream: dict[str, object]) -> bool:
    disposition = stream.get("disposition")
    if not isinstance(disposition, dict):
        return False
    return disposition.get("attached_pic") == 1


@dataclass(frozen=True, slots=True)
class SelectedVideoStream:
    stream: dict[str, object]
    video_position: int
    video_stream_count: int


def select_video_stream(streams: list[object]) -> SelectedVideoStream | None:
    """The first video stream that is not embedded cover art, or None.

    `video_position` counts every video stream, cover art included, because that
    is how `ffprobe -select_streams v:N` addresses them. A phone or action camera
    that carries a cover image ahead of its recording therefore still gets its
    packets scanned from the right stream.

    `video_stream_count` counts only the real streams. Two of those leave "which
    one is the video" without a defined answer, and every downstream consumer
    would have to guess; the caller rejects such a file rather than choosing.

    Matroska does not mark a cover image with the `attached_pic` disposition, so
    one appears here as a second real video stream and the file is rejected. That
    is the safe direction: the alternative is silently probing the cover.
    """
    video_streams = [
        stream
        for stream in streams
        if isinstance(stream, dict) and stream.get("codec_type") == "video"
    ]
    real = [
        (position, stream)
        for position, stream in enumerate(video_streams)
        if not _is_attached_picture(stream)
    ]
    if not real:
        return None
    position, stream = real[0]
    return SelectedVideoStream(
        stream=stream, video_position=position, video_stream_count=len(real)
    )


def read_header(path: Path) -> Header:
    """Read every stream and the format block in one ffprobe call.

    Selects the first video stream that is not embedded cover art. Raises when
    there is no such stream.
    """
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path.absolute()),
    ]
    raw = _run(command, HEADER_TIMEOUT_SECONDS, f"reading the header of {path}")
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        message = f"ffprobe returned invalid JSON for {path}: {exc}"
        raise MediaProbeError(message) from exc

    streams = payload.get("streams")
    streams = streams if isinstance(streams, list) else []
    selected = select_video_stream(streams)
    if selected is None:
        message = f"no video stream in {path}"
        raise MediaProbeError(message)
    stream = selected.stream
    video_position = selected.video_position

    fmt = payload.get("format")
    fmt = fmt if isinstance(fmt, dict) else {}

    sample_aspect_ratio = str(stream.get("sample_aspect_ratio", ""))
    field_order = str(stream.get("field_order", ""))
    frame_count_text = stream.get("nb_frames")

    return Header(
        container=str(fmt.get("format_name", "")),
        codec_name=str(stream.get("codec_name", "")).lower(),
        pixel_format=str(stream.get("pix_fmt", "")),
        color_range=str(stream.get("color_range", "unknown")),
        color_primaries=str(stream.get("color_primaries", "unknown")),
        color_transfer=str(stream.get("color_transfer", "unknown")),
        width=int(_number(stream.get("width"), 0.0)),
        height=int(_number(stream.get("height"), 0.0)),
        rotation_degrees=_rotation(stream),
        # "0:1" is ffprobe reporting the ratio as unspecified, not as square.
        # Unknown is assumed square: almost all content is, and a file that
        # omits the ratio while genuinely being anamorphic goes unflagged.
        square_pixels=sample_aspect_ratio in ("", "N/A", "0:1", "1:1"),
        # An absent field order is read as progressive. An interlaced file
        # that fails to signal itself therefore skips the deinterlace the
        # analysis derivative would otherwise apply, and its combed frames
        # reach the tracker.
        progressive=field_order in ("", "N/A", "progressive"),
        has_audio=any(
            isinstance(item, dict) and item.get("codec_type") == "audio"
            for item in streams
        ),
        video_stream_count=selected.video_stream_count,
        video_position=video_position,
        start_time=_number(stream.get("start_time"), 0.0),
        declared_duration=_number(fmt.get("duration"), 0.0),
        # avg_frame_rate, never r_frame_rate: this is what OpenCV reads, and the
        # disagreement between it and measurement is the whole point of the field.
        declared_fps=_fraction(str(stream.get("avg_frame_rate", "0/1"))),
        declared_frame_count=0
        if frame_count_text is None or str(frame_count_text) in _ABSENT
        else int(str(frame_count_text)),
    )


def scan_packets(
    path: Path, video_position: int
) -> tuple[tuple[Packet, ...], TimestampSource]:
    """Demultiplex the whole file, returning packets in decode order.

    Prefers `pts_time`. Falls back to `dts_time` only when PTS is absent for
    every packet, which is what AVI commonly does. Without the fallback an
    unmeasurable file is misread as a variable-rate one.

    Parsing the comma-separated rows is the costliest part of this module: on a
    file of 286256 packets it takes 281 ms, roughly three times the grid fit that
    consumes its output, and most of it goes into building one frozen instance
    per packet. It remains a fifth of what the ffprobe call itself costs, so it
    has never been worth optimizing. If it ever is, the standard library offers
    more of the win than numpy would -- fewer requested fields, or parallel
    `array` buffers instead of objects -- and neither costs a dependency.
    """
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        f"v:{video_position}",
        "-show_entries",
        "packet=pts_time,dts_time,size,pos,flags",
        "-of",
        "csv=p=0",
        str(path.absolute()),
    ]
    raw = _run(command, SCAN_TIMEOUT_SECONDS, f"scanning the packets of {path}")

    pts_packets: list[Packet] = []
    dts_packets: list[Packet] = []
    for line in raw.splitlines():
        # ffprobe emits the requested entries in its own natural order:
        # pts_time, dts_time, size, pos, flags. Byte offset (pos) is N/A on
        # containers that do not expose it; it is carried for io consumers and
        # is not used by the timestamp-based seek path, so -1 is a safe unknown.
        columns = line.split(",")
        if len(columns) < 5:
            continue
        size_text, pos_text, flags = columns[2], columns[3], columns[4]
        if not size_text.isdigit():
            continue
        size = int(size_text)
        pos = int(pos_text) if pos_text.lstrip("-").isdigit() else -1
        keyframe = "K" in flags
        if columns[0] not in _ABSENT:
            pts_packets.append(
                Packet(time=float(columns[0]), size=size, keyframe=keyframe, pos=pos)
            )
        if columns[1] not in _ABSENT:
            dts_packets.append(
                Packet(time=float(columns[1]), size=size, keyframe=keyframe, pos=pos)
            )

    if pts_packets:
        return tuple(pts_packets), "pts"
    if dts_packets:
        return tuple(dts_packets), "dts"
    message = f"no packet timestamps in {path}"
    raise MediaProbeError(message)
