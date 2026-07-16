"""Frame reading through a system-ffmpeg subprocess pipe. Requires numpy.

The subprocess architecture -- one persistent ffmpeg process for sequential
reads, respawned with an input -ss for a discontinuous seek -- follows the
established practice of moviepy's FFMPEG_VideoReader (MIT) and imageio-ffmpeg
(BSD-2). This reader improves on both by seeking against an exact packet index:
the preceding keyframe of a target frame is known, so a seek respawns at that
keyframe and discards a known number of frames, landing frame-exact. That
removes OpenCV's off-by-N CAP_PROP_POS_FRAMES class of bugs by construction and
lets a file be decoded by the same system ffmpeg that probed it, with no bundled
codec table in the loop.
"""

import fcntl
import io
import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy

from mosaic_media.hwaccel import ffmpeg_available, nvdec_available
from mosaic_media.probe.errors import MediaProbeError
from mosaic_media.probe.facts import MediaFacts
from mosaic_media.probe.ffprobe import read_header, scan_packets

from .index import SeekIndex, build_seek_index

# Linux fcntl.F_SETPIPE_SZ. Hard-coded so the module imports on platforms whose
# fcntl lacks the constant; the fcntl call is guarded and best-effort anyway.
_F_SETPIPE_SZ = 1031
_PIPE_BYTES = 1024 * 1024

# idle: no process running. sequential: one persistent process draining frames
# in presentation order. positioned: a process respawned at a seek keyframe.
_ReaderMode = Literal["idle", "sequential", "positioned"]


@dataclass(frozen=True, slots=True)
class _Geometry:
    source_width: int
    source_height: int
    fps: float
    source_frame_count: int
    out_width: int
    out_height: int
    channels: int
    frame_nbytes: int


class VideoReader:
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
        if not ffmpeg_available():
            message = "ffmpeg not found on PATH; install ffmpeg to read frames"
            raise MediaProbeError(message)
        self._path: Path = Path(path).expanduser().resolve()
        self._start_frame: int = max(0, int(start_frame))
        self._end_frame: int | None = None if end_frame is None else int(end_frame)
        self._frame_step: int = max(1, int(frame_step))
        self._resize: tuple[int, int] | None = (
            None if resize is None else (int(resize[0]), int(resize[1]))
        )
        self._grayscale: bool = bool(grayscale)
        self._want_hwaccel: bool = bool(hwaccel)
        self._facts: MediaFacts | None = facts
        self._index: SeekIndex | None = index
        self._geometry: _Geometry | None = None
        self._scratch: numpy.ndarray | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._mode: _ReaderMode = "idle"
        self._emitted: int = 0  # sequential: count of frames returned so far
        self._decoder_pos: int = 0  # positioned: next absolute source frame emitted
        self._target: int = 0  # positioned: next absolute frame read() returns
        self._last_index: int = 0  # index of the most recently returned frame
        self._closed: bool = False

    # --- Metadata resolution ---

    def _ensure_index(self) -> SeekIndex:
        if self._index is None:
            header = read_header(self._path)
            packets, _source = scan_packets(self._path, header.video_position)
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
            header = read_header(self._path)
            source_width = header.width
            source_height = header.height
            fps = header.declared_fps
            rotation_degrees = header.rotation_degrees
            if header.declared_frame_count > 0:
                source_frame_count = header.declared_frame_count
            else:
                source_frame_count = self._ensure_index().frame_count
        if self._resize is not None:
            # The scale filter runs after ffmpeg's automatic display-matrix
            # rotation, so a resize produces exactly the requested dimensions.
            out_width, out_height = self._resize
        elif rotation_degrees % 180 == 90:
            # ffmpeg autorotates by default and cv2 (>= 4.5) auto-orients too,
            # so a quarter-turn source is emitted with display width and height
            # swapped relative to the coded (source_width, source_height). The
            # reader reports and shapes frames in that displayed orientation to
            # match both decoders; the byte count is unchanged (w*h*3 is
            # symmetric), so only the reported shape distinguishes the two.
            out_width, out_height = source_height, source_width
        else:
            out_width, out_height = source_width, source_height
        channels = 1 if self._grayscale else 3
        self._geometry = _Geometry(
            source_width=source_width,
            source_height=source_height,
            fps=fps,
            source_frame_count=source_frame_count,
            out_width=out_width,
            out_height=out_height,
            channels=channels,
            frame_nbytes=out_width * out_height * channels,
        )
        return self._geometry

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
        end = (
            geometry.source_frame_count
            if self._end_frame is None
            else min(self._end_frame, geometry.source_frame_count)
        )
        start = min(self._start_frame, end)
        return len(range(start, end, self._frame_step))

    # --- Process lifecycle ---

    def _spawn(self, seek_timestamp: float | None, use_select: bool) -> None:
        geometry = self._ensure_ready()
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
        if self._want_hwaccel and nvdec_available():
            command += ["-hwaccel", "cuda"]
        if seek_timestamp is not None:
            command += ["-ss", f"{seek_timestamp:.6f}"]
        command += ["-i", str(self._path)]
        filters: list[str] = []
        if use_select:
            expression = self._select_expression(geometry.source_frame_count)
            if expression is not None:
                filters.append(f"select={expression}")
        if self._resize is not None:
            filters.append(f"scale={geometry.out_width}:{geometry.out_height}")
        if filters:
            command += ["-vf", ",".join(filters)]
        pixel_format = "gray" if self._grayscale else "bgr24"
        command += [
            "-fps_mode",
            "passthrough",
            "-f",
            "rawvideo",
            "-pix_fmt",
            pixel_format,
            "pipe:1",
        ]
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        if process.stdout is not None:
            try:
                _ = fcntl.fcntl(process.stdout.fileno(), _F_SETPIPE_SZ, _PIPE_BYTES)
            except OSError:
                pass
        self._process = process

    def _select_expression(self, source_frame_count: int) -> str | None:
        parts: list[str] = []
        if self._start_frame > 0:
            parts.append(f"gte(n\\,{self._start_frame})")
        if self._end_frame is not None and self._end_frame < source_frame_count:
            parts.append(f"lt(n\\,{self._end_frame})")
        if self._frame_step > 1:
            parts.append(f"not(mod(n-{self._start_frame}\\,{self._frame_step}))")
        return "*".join(parts) if parts else None

    def _close_process(self) -> None:
        process = self._process
        self._process = None
        self._mode = "idle"
        self._emitted = 0
        if process is None:
            return
        if process.stdout is not None:
            process.stdout.close()
        try:
            process.kill()
        except ProcessLookupError:
            return
        try:
            _ = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    def _reap_after_eof(self) -> None:
        """Confirm ffmpeg exited cleanly after a read reached end of stream.

        A read returns no frame both at a genuine end of stream and when ffmpeg
        aborts on a truncated or otherwise undecodable file, and the two are
        distinguishable only by the process exit status. Reap the process and
        raise when it exited non-zero; a zero exit is a normal stop.
        """
        process = self._process
        if process is None:
            return
        try:
            returncode = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return
        if returncode != 0:
            message = f"ffmpeg exited with code {returncode} decoding {self._path}"
            raise MediaProbeError(message)

    # --- Low-level frame reads ---

    def _stdout(self) -> io.BufferedReader | None:
        # subprocess.Popen(stdout=PIPE) yields a BufferedReader at runtime;
        # typeshed widens it to IO[bytes], which does not declare readinto.
        # Narrow it here so the read loop calls readinto without a suppression.
        process = self._process
        if process is None or not isinstance(process.stdout, io.BufferedReader):
            return None
        return process.stdout

    @staticmethod
    def _read_exact(stream: io.BufferedReader, view: memoryview) -> bool:
        total = 0
        size = len(view)
        while total < size:
            read = stream.readinto(view[total:])
            if not read:
                return False
            total += read
        return True

    def _grab(self, geometry: _Geometry) -> numpy.ndarray | None:
        stream = self._stdout()
        if stream is None:
            return None
        if self._grayscale:
            frame = numpy.empty(
                (geometry.out_height, geometry.out_width), dtype=numpy.uint8
            )
        else:
            frame = numpy.empty(
                (geometry.out_height, geometry.out_width, 3), dtype=numpy.uint8
            )
        if not self._read_exact(stream, memoryview(frame).cast("B")):
            return None
        return frame

    def _skip_one(self, geometry: _Geometry) -> bool:
        stream = self._stdout()
        if stream is None:
            return False
        if self._scratch is None:
            self._scratch = numpy.empty(geometry.frame_nbytes, dtype=numpy.uint8)
        return self._read_exact(stream, memoryview(self._scratch))

    # --- Public reads ---

    def read(self) -> tuple[bool, numpy.ndarray | None]:
        if self._closed:
            return False, None
        geometry = self._ensure_ready()
        if self._mode == "positioned":
            return self._read_positioned(geometry)
        return self._read_sequential(geometry)

    def _read_sequential(
        self, geometry: _Geometry
    ) -> tuple[bool, numpy.ndarray | None]:
        if self._mode == "idle":
            self._spawn(seek_timestamp=None, use_select=True)
            self._mode = "sequential"
        end = (
            geometry.source_frame_count
            if self._end_frame is None
            else min(self._end_frame, geometry.source_frame_count)
        )
        index = self._start_frame + self._emitted * self._frame_step
        if index >= end:
            return False, None
        frame = self._grab(geometry)
        if frame is None:
            self._reap_after_eof()
            return False, None
        self._last_index = index
        self._emitted += 1
        return True, frame

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
        geometry = self._ensure_ready()
        target = int(frame_index)
        if target < 0 or target >= geometry.source_frame_count:
            message = (
                f"frame index {target} out of range [0, {geometry.source_frame_count})"
            )
            raise IndexError(message)
        index = self._ensure_index()
        keyframe_index, keyframe_time = index.preceding_keyframe(target)
        live = self._process is not None and self._mode == "positioned"
        reusable = live and keyframe_index <= self._decoder_pos <= target
        if not reusable:
            self._close_process()
            self._spawn(seek_timestamp=keyframe_time, use_select=False)
            self._decoder_pos = keyframe_index
        self._mode = "positioned"
        self._target = target

    def _read_current(self, geometry: _Geometry) -> numpy.ndarray | None:
        while self._decoder_pos < self._target:
            if not self._skip_one(geometry):
                return None
            self._decoder_pos += 1
        frame = self._grab(geometry)
        if frame is None:
            return None
        self._decoder_pos += 1
        return frame

    def _read_positioned(
        self, geometry: _Geometry
    ) -> tuple[bool, numpy.ndarray | None]:
        if self._target >= geometry.source_frame_count:
            return False, None
        self._last_index = self._target
        frame = self._read_current(geometry)
        if frame is None:
            self._reap_after_eof()
            return False, None
        self._target += self._frame_step
        return True, frame

    def read_frames(
        self, indices: Sequence[int]
    ) -> Iterator[tuple[int, numpy.ndarray]]:
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
            self._close_process()

    def __enter__(self) -> "VideoReader":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def __len__(self) -> int:
        return self.frame_count
