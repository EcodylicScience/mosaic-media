"""BGR-frame video writer through in-process libav bindings (PyAV). Requires numpy and av.

Raw bgr24 frames are fed to av's libx264 encoder, or to h264_nvenc when the
caller permits hardware AND a cached usability probe confirms the device
actually encodes -- listing an encoder is not proof it runs. Output stays
mp4/h264/yuv420p. Shape and dtype are validated per write; an open failure, an
unwritable format, or an encode error surfaces as MediaProbeError rather than a
silently incremented frame count.
"""

from fractions import Fraction
from pathlib import Path

import av
import av.error
import numpy
from av.codec import CodecContext
from av.container import OutputContainer
from av.video.codeccontext import VideoCodecContext
from av.video.frame import VideoFrame
from av.video.stream import VideoStream

from ..probe.errors import MediaProbeError

_nvenc_usable_cache: bool | None = None


def _nvenc_encoder_usable() -> bool:
    """Whether h264_nvenc actually opens on this machine. Cached: the probe
    constructs and opens a tiny encoder context once; a GPU-less machine raises
    even though the wheel lists the encoder."""
    global _nvenc_usable_cache
    if _nvenc_usable_cache is None:
        try:
            context = CodecContext.create("h264_nvenc", "w")
            if isinstance(context, VideoCodecContext):
                context.width = 16
                context.height = 16
                context.pix_fmt = "yuv420p"
            context.time_base = Fraction(1, 30)
            context.open()
            _nvenc_usable_cache = True
        except av.error.FFmpegError:
            _nvenc_usable_cache = False
    return _nvenc_usable_cache


class FFmpegVideoWriter:
    def __init__(
        self,
        output_path: Path | str,
        width: int,
        height: int,
        fps: float = 30.0,
        crf: int = 23,
        preset: str = "medium",
        hwaccel: bool = False,
    ) -> None:
        # Set first so __del__ -> close() is safe even if a later line raises:
        # close() reads _closed and _container, so both must exist before the
        # path resolution and container open below can raise.
        self._closed: bool = False
        self._container: OutputContainer | None = None
        self._output_path: Path = Path(output_path).expanduser().resolve()
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._width: int = width
        self._height: int = height
        self._fps: float = fps
        self._frames_written: int = 0
        self._encoder_name: str = (
            "h264_nvenc" if (hwaccel and _nvenc_encoder_usable()) else "libx264"
        )
        try:
            container = av.open(str(self._output_path), mode="w")
        except (ValueError, av.error.FFmpegError) as exc:
            # av reports an unmappable output format as a builtin ValueError before
            # libav is engaged; runtime failures come through FFmpegError.
            message = f"failed to open {self._output_path} for writing: {exc}"
            raise MediaProbeError(message) from exc
        stream = container.add_stream(
            self._encoder_name, rate=Fraction(fps).limit_denominator(1000000)
        )
        if not isinstance(stream, VideoStream):
            container.close()
            message = f"expected a video stream for encoder {self._encoder_name}"
            raise MediaProbeError(message)
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        if self._encoder_name == "libx264":
            stream.options = {"preset": preset, "crf": str(crf)}
        else:
            stream.options = {"preset": preset, "cq": str(crf)}
        self._container = container
        self._stream: VideoStream = stream

    @property
    def output_path(self) -> Path:
        return self._output_path

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def frames_written(self) -> int:
        return self._frames_written

    @property
    def encoder_name(self) -> str:
        return self._encoder_name

    def write(self, frame: numpy.ndarray) -> None:
        if self._closed:
            message = "writer is closed"
            raise MediaProbeError(message)
        container = self._container
        if container is None:
            message = "writer is closed"
            raise MediaProbeError(message)
        expected_shape = (self._height, self._width, 3)
        if frame.shape != expected_shape or frame.dtype != numpy.uint8:
            message = (
                f"frame shape {tuple(frame.shape)} dtype {frame.dtype} does not "
                f"match writer geometry {expected_shape} dtype uint8"
            )
            raise MediaProbeError(message)
        video_frame = VideoFrame.from_ndarray(frame, format="bgr24")
        try:
            for packet in self._stream.encode(video_frame):
                container.mux(packet)
        except av.error.FFmpegError as exc:
            message = f"failed to encode a frame to {self._output_path}: {exc}"
            raise MediaProbeError(message) from exc
        self._frames_written += 1

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        container = self._container
        self._container = None
        if container is None:
            return
        # Close the container even when the encoder flush raises, so a failed
        # finalize does not leak the output file handle. The mapping still
        # surfaces a flush or close FFmpegError as MediaProbeError.
        try:
            try:
                for packet in self._stream.encode(None):
                    container.mux(packet)
            finally:
                container.close()
        except av.error.FFmpegError as exc:
            message = f"failed to finalize {self._output_path}: {exc}"
            raise MediaProbeError(message) from exc

    def __enter__(self) -> "FFmpegVideoWriter":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()
