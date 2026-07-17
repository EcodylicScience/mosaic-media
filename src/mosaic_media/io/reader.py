"""Frame reading through in-process libav bindings (PyAV). Requires numpy and av.

The reader decodes in an open av container: sequential reads decode forward;
a seek resolves the target's preceding keyframe from the packet index, calls
container.seek to that keyframe's presentation timestamp with backward
resolution, verifies the decoded landing matches that keyframe, then counts
frames forward to the target, landing frame-exact. That removes OpenCV's
off-by-N CAP_PROP_POS_FRAMES class
of bugs by construction, and the codec table is a tested invariant (the codec
guard), not a trusted bundled binary. Rotation is applied in process through a
libav transpose filter graph, bit-exact against system-ffmpeg autorotation.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import av
import av.error
import numpy
from av.container import InputContainer
from av.filter.graph import Graph
from av.video.frame import VideoFrame
from av.video.stream import VideoStream

from ..probe.errors import MediaProbeError
from ..probe.facts import MediaFacts
from .index import SeekIndex, build_seek_index
from .packets import scan_packets_in_process

# idle: no container open. sequential: decoding forward from the window start.
# positioned: decoding forward from an explicit seek target.
_ReaderMode = Literal["idle", "sequential", "positioned"]

# Transpose direction per display rotation. 90 (cclock) is corpus-verified
# bit-exact against system-ffmpeg autorotation; the other quarter-turns use the
# analogous transpose, covered by the framemd5 suite when a fixture of that
# rotation exists. A 180-degree rotation composes two transposes or vflip+hflip;
# add it here when a 180 fixture lands. A rotation this mapping does not cover
# raises a clear MediaProbeError rather than emitting a wrong orientation.
_TRANSPOSE_BY_ROTATION: dict[int, str] = {90: "cclock", 270: "clock"}


@dataclass(frozen=True, slots=True)
class _Geometry:
    fps: float
    source_frame_count: int
    out_width: int
    out_height: int


class VideoReader:
    """Decode frames from one video through in-process libav bindings (PyAV).

    Injecting `facts` suppresses the metadata probe that sequential reads would
    otherwise run. Seeking and sparse reads (`seek` and `read_frames`)
    additionally need the packet index, which `facts` does not carry; inject
    `index` as well to suppress all probing.

    The source frame count is resolved in this order: `facts.frame_count` when
    facts are injected, then the stream's declared frame count when that is
    positive, then the length of the packet index.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        start_frame: int = 0,
        end_frame: int | None = None,
        frame_step: int = 1,
        resize: tuple[int, int] | None = None,
        grayscale: bool = False,
        hwaccel: bool = False,
        facts: MediaFacts | None = None,
        index: SeekIndex | None = None,
    ) -> None:
        # Set first so __del__ -> close() is safe even if a later line raises:
        # close() reads _closed and _container, so both must exist before the
        # path resolution below can raise.
        self._closed: bool = False
        self._container: InputContainer | None = None
        self._path: Path = Path(path).expanduser().resolve()
        self._start_frame: int = max(0, int(start_frame))
        self._end_frame: int | None = None if end_frame is None else int(end_frame)
        self._frame_step: int = max(1, int(frame_step))
        self._resize: tuple[int, int] | None = (
            None if resize is None else (int(resize[0]), int(resize[1]))
        )
        self._grayscale: bool = bool(grayscale)
        # hwaccel is retained for signature compatibility and is a no-op: decode
        # is always software. No consumer requests hardware decode today, and the
        # GPU download path's bit-exactness against the framemd5 goldens is
        # unverified. The implementation seam if that changes is
        # av.codec.hwaccel.HWAccel("cuda").
        self._want_hwaccel: bool = bool(hwaccel)
        self._facts: MediaFacts | None = facts
        self._index: SeekIndex | None = index
        self._geometry: _Geometry | None = None
        self._stream: VideoStream | None = None
        self._decode_iterator: Iterator[VideoFrame] | None = None
        # The first frame after a seek, decoded eagerly to verify the landing and
        # then held so _read_current returns it rather than a second decode.
        self._pending_frame: VideoFrame | None = None
        self._rotation_degrees: int = 0
        self._rotation_graph: Graph | None = None
        self._mode: _ReaderMode = "idle"
        self._decoder_pos: int = 0  # next absolute source frame the decoder emits
        self._target: int = 0  # next absolute frame read() returns
        self._last_index: int = 0  # index of the most recently returned frame

    # --- Container lifecycle ---

    def _ensure_container(self) -> tuple[InputContainer, VideoStream]:
        container = self._container
        stream = self._stream
        if container is not None and stream is not None:
            return container, stream
        try:
            container = av.open(str(self._path))
        except av.error.FFmpegError as exc:
            message = f"failed to open {self._path}: {exc}"
            raise MediaProbeError(message) from exc
        if not container.streams.video:
            container.close()
            message = f"no video stream in {self._path}"
            raise MediaProbeError(message)
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"  # frame threading; the decode loop drains on EOF
        self._container = container
        self._stream = stream
        return container, stream

    def _probe_rotation(self) -> int:
        # The av stream exposes no rotation getter before decode, so open a
        # short-lived container, read the first frame's rotation, and close it,
        # leaving the reader's own decode position untouched.
        try:
            with av.open(str(self._path)) as container:
                stream = container.streams.video[0]
                for frame in container.decode(stream):
                    return int(frame.rotation)
        except av.error.FFmpegError as exc:
            message = f"failed to decode {self._path}: {exc}"
            raise MediaProbeError(message) from exc
        return 0

    # --- Metadata resolution ---

    def _ensure_index(self) -> SeekIndex:
        if self._index is None:
            packets, _source = scan_packets_in_process(self._path)
            self._index = build_seek_index(packets)
        return self._index

    def _ensure_ready(self) -> _Geometry:
        if self._geometry is not None:
            return self._geometry
        if self._facts is not None:
            source_width = self._facts.width
            source_height = self._facts.height
            fps = self._facts.fps
            source_frame_count = self._facts.frame_count
            rotation_degrees = self._facts.rotation_degrees
        else:
            _container, stream = self._ensure_container()
            source_width = int(stream.width)
            source_height = int(stream.height)
            fps = float(stream.average_rate or 0)
            declared_frame_count = int(stream.frames)
            if declared_frame_count > 0:
                source_frame_count = declared_frame_count
            else:
                source_frame_count = self._ensure_index().frame_count
            rotation_degrees = self._probe_rotation()
        self._rotation_degrees = int(rotation_degrees)
        normalized_rotation = self._rotation_degrees % 360
        if (
            normalized_rotation != 0
            and normalized_rotation not in _TRANSPOSE_BY_ROTATION
        ):
            message = f"unsupported rotation {self._rotation_degrees} for {self._path}"
            raise MediaProbeError(message)
        if self._resize is not None:
            # A resize wins over the rotation swap; the reformat runs after the
            # transpose, so the output is exactly the requested (width, height).
            out_width, out_height = self._resize
        elif self._rotation_degrees % 180 == 90:
            # A quarter-turn source is emitted in displayed orientation: the
            # reader rotates each frame through the transpose graph, so displayed
            # width and height are the coded dimensions swapped. Reporting and
            # shaping in that orientation matches ffmpeg autorotation and cv2
            # auto-orientation; the byte count is unchanged (w*h*3 is symmetric),
            # so only the reported shape distinguishes the two.
            out_width, out_height = source_height, source_width
        else:
            out_width, out_height = source_width, source_height
        self._geometry = _Geometry(
            fps=fps,
            source_frame_count=source_frame_count,
            out_width=out_width,
            out_height=out_height,
        )
        return self._geometry

    def _window_end(self, geometry: _Geometry) -> int:
        """The exclusive upper frame bound of the reader's window, clamped to
        the source length. Sequential and positioned reads both stop here, and
        seek() rejects a target at or beyond it."""
        if self._end_frame is None:
            return geometry.source_frame_count
        return min(self._end_frame, geometry.source_frame_count)

    # --- Properties ---

    @property
    def width(self) -> int:
        return self._ensure_ready().out_width

    @property
    def height(self) -> int:
        return self._ensure_ready().out_height

    @property
    def fps(self) -> float:
        return self._ensure_ready().fps

    @property
    def frame_count(self) -> int:
        geometry = self._ensure_ready()
        end = self._window_end(geometry)
        start = min(self._start_frame, end)
        return len(range(start, end, self._frame_step))

    # --- Decode primitives ---

    def _decode_next(self) -> VideoFrame | None:
        """Pull the next frame from the decode iterator in presentation order,
        or None at a clean end of stream. A truncated or otherwise undecodable
        file raises FFmpegError here, which maps to MediaProbeError -- preserving
        the subprocess reader's truncated-file semantics."""
        pending = self._pending_frame
        if pending is not None:
            self._pending_frame = None
            return pending
        iterator = self._decode_iterator
        if iterator is None:
            return None
        try:
            return next(iterator)
        except StopIteration:
            return None
        except av.error.FFmpegError as exc:
            message = f"failed to decode {self._path}: {exc}"
            raise MediaProbeError(message) from exc

    def _build_rotation_graph(self) -> Graph:
        stream = self._stream
        if stream is None:
            message = f"cannot build the rotation graph before opening {self._path}"
            raise MediaProbeError(message)
        direction = _TRANSPOSE_BY_ROTATION[self._rotation_degrees % 360]
        graph = Graph()
        buffer = graph.add_buffer(template=stream)
        transpose = graph.add("transpose", direction)
        sink = graph.add("buffersink")
        buffer.link_to(transpose)
        transpose.link_to(sink)
        graph.configure()
        self._rotation_graph = graph
        return graph

    def _emit(self, geometry: _Geometry, frame: VideoFrame) -> numpy.ndarray:
        # Rotate in process when the source carries a display rotation, then
        # convert (and resize) to the reader's output pixel format. to_ndarray
        # returns a writable, C-contiguous, non-aliasing uint8 array wrapping the
        # reformatted frame's own buffer, so no extra copy is taken.
        graph = self._rotation_graph
        if graph is None and self._rotation_degrees % 360 != 0:
            graph = self._build_rotation_graph()
        if graph is not None:
            graph.vpush(frame)
            frame = graph.vpull()
        pixel_format = "gray" if self._grayscale else "bgr24"
        if self._resize is not None:
            # Bicubic matches system ffmpeg's -vf scale default. av's own default
            # is bilinear; leaving it unset regresses resized frames against the
            # scale goldens (bilinear drifts ~24 gray levels where bicubic lands
            # within one). Rotation and format-only reformats do not scale, so
            # they take no interpolation.
            reformatted = frame.reformat(
                width=geometry.out_width,
                height=geometry.out_height,
                format=pixel_format,
                interpolation="BICUBIC",
            )
            return reformatted.to_ndarray()
        return frame.to_ndarray(format=pixel_format)

    def _to_stream_offset(self, stream: VideoStream, keyframe_time: float) -> int:
        time_base = stream.time_base
        if time_base is None:
            message = f"video stream in {self._path} has no time base for seeking"
            raise MediaProbeError(message)
        return int(round(keyframe_time / float(time_base)))

    def _position_at(self, target: int) -> None:
        """Position the decoder so its next emitted frame lies at or before
        `target`. Reuse the live decoder when it is already positioned between the
        target's preceding keyframe and the target; otherwise seek to that
        keyframe's presentation timestamp with backward resolution."""
        index = self._ensure_index()
        keyframe_index, keyframe_time = index.preceding_keyframe(target)
        reusable = (
            self._container is not None
            and self._decode_iterator is not None
            and self._mode == "positioned"
            and keyframe_index <= self._decoder_pos <= target
        )
        if reusable:
            return
        geometry = self._ensure_ready()
        container, stream = self._ensure_container()
        offset = self._to_stream_offset(stream, keyframe_time)
        try:
            container.seek(offset, stream=stream, backward=True)
        except av.error.FFmpegError as exc:
            message = f"failed to seek {self._path} to frame {target}: {exc}"
            raise MediaProbeError(message) from exc
        self._decode_iterator = container.decode(stream)
        self._decoder_pos = keyframe_index
        self._pending_frame = None
        # A backward seek to the keyframe's own timestamp must land on that
        # keyframe. Decode it eagerly and verify its presentation time before
        # trusting the arithmetic frame count that discards forward to the
        # target; landing on a different keyframe would silently return the
        # wrong frame. The decoded keyframe is held for _read_current.
        first = self._decode_next()
        if first is not None:
            observed = float(first.time)
            if geometry.fps > 0 and abs(observed - keyframe_time) > 0.5 / geometry.fps:
                message = (
                    f"seek landing for {self._path} at frame {target}: expected "
                    f"keyframe time {keyframe_time} but decoded {observed}"
                )
                raise MediaProbeError(message)
            self._pending_frame = first

    def _read_current(self, geometry: _Geometry) -> numpy.ndarray | None:
        """Decode forward to `self._target` and return that frame, advancing the
        decoder position. None at end of stream."""
        while self._decoder_pos < self._target:
            if self._decode_next() is None:
                return None
            self._decoder_pos += 1
        frame = self._decode_next()
        if frame is None:
            return None
        self._decoder_pos += 1
        return self._emit(geometry, frame)

    # --- Public reads ---

    def read(self) -> tuple[bool, numpy.ndarray | None]:
        if self._closed:
            return False, None
        geometry = self._ensure_ready()
        window_end = self._window_end(geometry)
        if self._mode == "idle":
            self._target = self._start_frame
            self._mode = "sequential"
            if self._start_frame < window_end:
                self._start_reading()
        if self._target >= window_end:
            return False, None
        self._last_index = self._target
        frame = self._read_current(geometry)
        if frame is None:
            return False, None
        self._target += self._frame_step
        return True, frame

    def _start_reading(self) -> None:
        if self._start_frame > 0:
            # Position the first read through the seek path rather than decoding
            # the discarded prefix.
            self._position_at(self._start_frame)
        else:
            container, stream = self._ensure_container()
            self._decode_iterator = container.decode(stream)
            self._decoder_pos = 0

    def read_batch(self, batch_size: int) -> tuple[numpy.ndarray, numpy.ndarray]:
        geometry = self._ensure_ready()
        indices: list[int] = []
        frames: list[numpy.ndarray] = []
        for _ in range(batch_size):
            ok, frame = self.read()
            if not ok or frame is None:
                break
            indices.append(self._last_index)
            frames.append(frame)
        if not frames:
            if self._grayscale:
                empty = numpy.empty(
                    (0, geometry.out_height, geometry.out_width), dtype=numpy.uint8
                )
            else:
                empty = numpy.empty(
                    (0, geometry.out_height, geometry.out_width, 3),
                    dtype=numpy.uint8,
                )
            return numpy.empty(0, dtype=numpy.int64), empty
        return numpy.asarray(indices, dtype=numpy.int64), numpy.stack(frames)

    def seek(self, frame_index: int) -> None:
        if self._closed:
            message = "reader is closed"
            raise MediaProbeError(message)
        geometry = self._ensure_ready()
        target = int(frame_index)
        window_end = self._window_end(geometry)
        if target < self._start_frame or target >= window_end:
            message = (
                f"frame index {target} out of range [{self._start_frame}, {window_end})"
            )
            raise IndexError(message)
        self._position_at(target)
        self._mode = "positioned"
        self._target = target

    def read_frames(
        self, indices: Sequence[int]
    ) -> Iterator[tuple[int, numpy.ndarray]]:
        if self._closed:
            message = "reader is closed"
            raise MediaProbeError(message)
        geometry = self._ensure_ready()
        targets = sorted({int(index) for index in indices})
        if not targets:
            return
        index = self._ensure_index()
        for group in index.group_by_gop(targets):
            for target in group:
                self.seek(target)
                frame = self._read_current(geometry)
                if frame is None:
                    message = f"failed to decode frame {target} from {self._path}"
                    raise MediaProbeError(message)
                self._last_index = target
                # Leave the positioned cursor at the decoder's true next frame,
                # set before the yield so it holds whether the caller consumes
                # the whole generator or abandons it mid-iteration. A following
                # read() then returns that frame with the correct index instead
                # of mislabeling it as this sparse target.
                self._target = self._decoder_pos
                yield target, frame

    def __iter__(self) -> Iterator[tuple[int, numpy.ndarray]]:
        while True:
            ok, frame = self.read()
            if not ok or frame is None:
                break
            yield self._last_index, frame

    # --- Cleanup and context management ---

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            container = self._container
            self._container = None
            if container is not None:
                container.close()

    def __enter__(self) -> "VideoReader":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def __len__(self) -> int:
        return self.frame_count
