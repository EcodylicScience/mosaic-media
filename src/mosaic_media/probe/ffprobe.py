"""ffprobe subprocess wrappers. Standard library only.

Two calls per file: one JSON header read, and one packet scan that
demultiplexes the whole stream and reads each packet's payload to hash it. No
frame is decoded -- the payload hash covers the demuxer-delivered bytes, not a
decoded frame. One further call, `prober_version`, reads the running
ffprobe's own version once per process rather than once per probed file.
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from ..ffmpeg import run_to_completion
from .errors import MediaProbeError

HEADER_TIMEOUT_SECONDS = 60
# The scan reads every packet's payload to compute its hash, measured at
# between 1.5x and 2x the unhashed scan on a 481 MB, 216000-packet file
# (0.97 s against 1.74 s to 1.85 s across two serialized runs that disagreed
# by 25 percent). 900 seconds is two orders of magnitude above that.
SCAN_TIMEOUT_SECONDS = 900
VERSION_TIMEOUT_SECONDS = 30

# The per-packet payload hash algorithm. Chosen on the identity collision
# budget, not on cryptographic strength: the digests fold one hash per packet,
# so accidental aliasing needs every packet to collide. Changing this changes
# every minted digest.
PAYLOAD_HASH_ALGORITHM = "CRC32"

TimestampSource = Literal["pts", "dts", "none"]

_ABSENT = ("", "N/A")
_LIBAVFORMAT = "libavformat"

# H.264 counts two ticks per frame, so the tick rate the sequence parameter set
# states is twice the frame rate. The convention is codec-specific: a raw HEVC
# stream reports one tick per frame, and halving that would be wrong by half.
_H264_TICKS_PER_FRAME = 2.0

# Above this the value is not a frame rate at all: a sequence parameter set that
# carries no timing makes libavformat report the demuxer time base instead,
# measured at 1200000/1, which this rejects. There is no matching lower bound,
# because any positive rate is a real one -- a timelapse or long-observation
# recording is coded at a fraction of a frame per second, and 1.0 would discard
# it. The lower comparison against zero is not a judgment about which rates are
# real; it keeps 0.0 meaning absent, which is this field's convention.
_MAXIMUM_PLAUSIBLE_FPS = 1000.0


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
    elementary_stream_fps: float
    declared_frame_count: int


@dataclass(frozen=True, slots=True)
class Packet:
    """One demultiplexed packet.

    `data_hash` is the demuxer-delivered payload hash ffprobe reports, an
    `ALGO:hexdigest` string. It is what the identity digests hash, rather than
    bytes read at `pos`: byte offsets are container-relative and in Matroska
    address the SimpleBlock header, not the payload.

    It defaults to the empty string because the in-process scan in
    `mosaic_media.io.packets` mirrors this one and does not populate it.
    Identity is minted once by the probe at ingestion and never by the reader,
    so the io layer has no reason to pay for the payload read.

    `discard` is the demuxer's "do not present" flag, set from a container edit
    list. The packet is real and its picture is decodable; the demuxer is saying
    the container asked for it not to be shown.
    """

    time: float
    size: int
    keyframe: bool
    pos: int
    discard: bool = False
    data_hash: str = ""


def parse_fraction(text: str) -> float:
    """The value of a `num/den` rational as ffprobe writes it, or 0.0 when the
    field is absent or its denominator is zero."""
    if text in _ABSENT:
        return 0.0
    numerator, _, denominator = text.partition("/")
    if numerator in _ABSENT:
        return 0.0
    if not denominator:
        return float(numerator)
    divisor = float(denominator)
    return 0.0 if divisor == 0.0 else float(numerator) / divisor


def elementary_stream_fps(
    stream: dict[str, object], container: str, codec_name: str
) -> float:
    """The frame rate an H.264 elementary stream states in its own bitstream, or
    0.0 when it states none.

    Such a stream has no container to declare a rate, and the h264 demuxer
    answers `avg_frame_rate` with a fixed default that is read from nothing.
    `r_frame_rate` carries the sequence parameter set's tick rate, which is the
    only rate the file itself states.

    Restricted to the raw demuxer, whose format name is `h264`, because
    `r_frame_rate` means something else for a container: there it is the
    container's own frame rate, and halving it would report half the true rate
    (measured 12.5 on a 25 fps mp4). Gating here rather than at the caller keeps
    the field from ever holding half a real rate.
    """
    if container != "h264" or codec_name != "h264":
        return 0.0
    tick_rate = parse_fraction(str(stream.get("r_frame_rate", "0/1")))
    rate = tick_rate / _H264_TICKS_PER_FRAME
    if not 0.0 < rate <= _MAXIMUM_PLAUSIBLE_FPS:
        return 0.0
    return rate


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
    raw = run_to_completion(
        command,
        timeout=HEADER_TIMEOUT_SECONDS,
        action=f"reading the header of {path}",
        error_type=MediaProbeError,
    )
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
    container = str(fmt.get("format_name", ""))
    codec_name = str(stream.get("codec_name", "")).lower()

    return Header(
        container=container,
        codec_name=codec_name,
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
        # This field is avg_frame_rate and nothing else: it is what OpenCV
        # reads, and the disagreement between it and measurement is the whole
        # point of it. The field below reads r_frame_rate for a different
        # purpose and is not a substitute for this one.
        declared_fps=parse_fraction(str(stream.get("avg_frame_rate", "0/1"))),
        elementary_stream_fps=elementary_stream_fps(stream, container, codec_name),
        declared_frame_count=0
        if frame_count_text is None or str(frame_count_text) in _ABSENT
        else int(str(frame_count_text)),
    )


@lru_cache(maxsize=1)
def prober_version() -> str:
    """The ffprobe build that mints identity, as `"<program> <libavformat ident>"`.

    Recorded on every probe as provenance, never hashed. The digest is defined
    against libavformat's output rather than raw file bytes, so libavformat is
    the component whose change is a format break; the program version rides
    along because it is what an operator reads off their own machine. It is
    taken to be a single token, which holds for every distribution and snapshot
    build seen so far.

    Cached for the process: this is a property of the binary, not of the file,
    and a subprocess per probed file would be a real cost for a constant. The
    consequence is that an ffprobe upgraded under a long-lived process is not
    observed until it restarts, so the recorded value is a claim about the
    binary as of process start.
    """
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_program_version",
        "-show_library_versions",
        "-of",
        "json",
    ]
    stdout = run_to_completion(
        command,
        timeout=VERSION_TIMEOUT_SECONDS,
        action="reading its own version",
        error_type=MediaProbeError,
    )
    try:
        decoded = json.loads(stdout or "{}")
    except json.JSONDecodeError as exc:
        message = f"ffprobe returned invalid JSON for its own version: {exc}"
        raise MediaProbeError(message) from exc
    payload = decoded if isinstance(decoded, dict) else {}

    program_version = payload.get("program_version")
    raw_program = (
        program_version.get("version") if isinstance(program_version, dict) else None
    )
    program = raw_program if isinstance(raw_program, str) else ""
    if not program:
        message = "ffprobe reported no program version"
        raise MediaProbeError(message)

    library_versions = payload.get("library_versions")
    libraries = library_versions if isinstance(library_versions, list) else []
    raw_ident = next(
        (
            library.get("ident")
            for library in libraries
            if isinstance(library, dict) and library.get("name") == _LIBAVFORMAT
        ),
        None,
    )
    libavformat_ident = raw_ident if isinstance(raw_ident, str) else ""
    if not libavformat_ident:
        message = f"ffprobe reported no {_LIBAVFORMAT} ident in its version output"
        raise MediaProbeError(message)
    return f"{program} {libavformat_ident}"


def scan_command(path: Path, video_position: int) -> list[str]:
    """The packet-scan invocation, shared with the test that pins its column order."""
    return [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        f"v:{video_position}",
        "-show_data_hash",
        PAYLOAD_HASH_ALGORITHM,
        "-show_entries",
        "packet=pts_time,dts_time,size,pos,flags,data_hash",
        "-of",
        "csv=p=0",
        str(path.absolute()),
    ]


def scan_packets(
    path: Path, video_position: int
) -> tuple[tuple[Packet, ...], TimestampSource]:
    """Demultiplex the whole file, returning packets in decode order.

    Reads every packet's payload as it demultiplexes, via `-show_data_hash`,
    and carries the result forward as `Packet.data_hash`. No frame is decoded:
    the hash covers the demuxer-delivered payload bytes, not a decoded frame.

    Prefers `pts_time`. Falls back to `dts_time` only when PTS is absent for
    every packet, which is what AVI commonly does. Without the fallback an
    unmeasurable file is misread as a variable-rate one.

    A stream where every packet lacks both timestamps -- a raw elementary
    stream such as a bare `.h264` file -- returns its packets with source
    `"none"`: the sizes, keyframe flags, and byte offsets are real, but `time`
    is a 0.0 placeholder that no timing or seeking consumer may read.
    `probe_media` skips the grid fit for such a stream and marks the facts
    `timing_measured=False`; the io packet scan refuses the file instead.

    Parsing the comma-separated rows is the costliest part of this module: on a
    file of 286256 packets it takes 281 ms, roughly three times the grid fit that
    consumes its output, and most of it goes into building one frozen instance
    per packet. It remains a fifth of what the ffprobe call itself costs, so it
    has never been worth optimizing. If it ever is, the standard library offers
    more of the win than numpy would -- fewer requested fields, or parallel
    `array` buffers instead of objects -- and neither costs a dependency.
    """
    command = scan_command(path, video_position)
    raw = run_to_completion(
        command,
        timeout=SCAN_TIMEOUT_SECONDS,
        action=f"scanning the packets of {path}",
        error_type=MediaProbeError,
    )

    pts_packets: list[Packet] = []
    dts_packets: list[Packet] = []
    untimed_packets: list[Packet] = []
    rows_without_payload_hash = 0
    for line in raw.splitlines():
        # ffprobe emits the requested entries in its own natural order:
        # pts_time, dts_time, size, pos, flags, data_hash. Byte offset (pos) is
        # N/A on containers that do not expose it; it is carried for io
        # consumers and is not used by the timestamp-based seek path, so -1 is a
        # safe unknown. MPEG-TS appends a seventh empty side-data column, which
        # does not move any index below it.
        columns = line.split(",")
        if len(columns) < 6:
            if len(columns) >= 5:
                rows_without_payload_hash += 1
            continue
        size_text, pos_text, flags = columns[2], columns[3], columns[4]
        data_hash = columns[5]
        if not size_text.isdigit():
            continue
        size = int(size_text)
        pos = int(pos_text) if pos_text.lstrip("-").isdigit() else -1
        keyframe = "K" in flags
        discard = "D" in flags
        if columns[0] not in _ABSENT:
            pts_packets.append(
                Packet(
                    time=float(columns[0]),
                    size=size,
                    keyframe=keyframe,
                    pos=pos,
                    discard=discard,
                    data_hash=data_hash,
                )
            )
        if columns[1] not in _ABSENT:
            dts_packets.append(
                Packet(
                    time=float(columns[1]),
                    size=size,
                    keyframe=keyframe,
                    pos=pos,
                    discard=discard,
                    data_hash=data_hash,
                )
            )
        if columns[0] in _ABSENT and columns[1] in _ABSENT:
            untimed_packets.append(
                Packet(
                    time=0.0,
                    size=size,
                    keyframe=keyframe,
                    pos=pos,
                    discard=discard,
                    data_hash=data_hash,
                )
            )

    if pts_packets:
        return tuple(pts_packets), "pts"
    if dts_packets:
        return tuple(dts_packets), "dts"
    if untimed_packets:
        return tuple(untimed_packets), "none"
    if rows_without_payload_hash:
        # Every row arrived without the payload-hash column. An ffprobe that
        # does not know -show_data_hash exits non-zero and never reaches here;
        # this is the quieter failure where the flag is accepted but the
        # data_hash entry is dropped, since ffprobe ignores an unrecognized
        # -show_entries name rather than failing. Naming it beats the generic
        # "no packets" message, which would send a reader looking at the file.
        message = (
            f"ffprobe returned packets without payload hashes for {path}: "
            "the installed ffprobe does not report data_hash"
        )
        raise MediaProbeError(message)
    message = f"no packets in the video stream of {path}"
    raise MediaProbeError(message)
