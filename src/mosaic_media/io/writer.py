"""BGR-frame video writer through in-process libav bindings (PyAV). Requires numpy and av.

Raw bgr24 frames are fed to av's libsvtav1 encoder, or to av1_nvenc when the
caller permits hardware AND a cached usability probe confirms the device
actually encodes -- listing an encoder is not proof it runs. Output stays
mp4/av1/yuv420p. Shape and dtype are validated per write; an open failure, an
unwritable format, or an encode error surfaces as MediaProbeError rather than a
silently incremented frame count.

AV1 rather than H.264 because the software H.264 encoders PyAV's bundled build
can reach are libx264 and libx264rgb, both GPL-2.0-or-later. PyAV links FFmpeg
into the calling process, so naming one here links GPL code into every consumer
of this package. A non-GPL software H.264 encoder exists -- libopenh264 -- but
that build does not carry it. AV1 is also what the transcode path targets, so
the writer and the transcode produce the same codec.
"""

import warnings
from fractions import Fraction
from pathlib import Path
from typing import Literal, Self

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

# x264's named presets, in the order libx264 defined them, mapped onto SVT-AV1's
# numeric scale (0 slowest, 13 fastest). Accepted only from the deprecated
# `preset` parameter; SVT-AV1's own values go to `av1_preset`.
X264Preset = Literal[
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
    "placebo",
]
_AV1_PRESET_FROM_X264: dict[X264Preset, int] = {
    "ultrafast": 13,
    "superfast": 12,
    "veryfast": 11,
    "faster": 10,
    "fast": 9,
    "medium": 8,
    "slow": 6,
    "slower": 4,
    "veryslow": 2,
    "placebo": 0,
}

# x264 rates 0-51, SVT-AV1 0-63, and the same number is a different picture on
# each. The offset lines up the defaults this writer shipped with (x264 23 ->
# AV1 30) and holds across the useful middle of both scales. Approximate by
# construction: measuring the real mapping is the subject of
# docs/issues/encoding-presets-unmeasured-against-quality-goals.md.
_X264_TO_AV1_CRF_OFFSET = 7

DEFAULT_AV1_CRF = 30
DEFAULT_AV1_PRESET = 8

# av1_nvenc rates 0-51 like x264 and names its presets p1 (slowest) to p7; the
# integer scale it also accepts means something else again, so a SVT-AV1 preset
# must never be forwarded to it. p4 is the encoder's own default.
_NVENC_PRESET = "p4"


def _av1_crf_from_x264(crf: int) -> int:
    return min(63, max(0, crf + _X264_TO_AV1_CRF_OFFSET))


def _nvenc_encoder_usable() -> bool:
    """Whether av1_nvenc actually opens on this machine. Cached: the probe
    constructs and opens a tiny encoder context once; a machine without an
    AV1-capable NVIDIA device raises even though the build lists the encoder."""
    global _nvenc_usable_cache
    if _nvenc_usable_cache is None:
        try:
            context = CodecContext.create("av1_nvenc", "w")
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
        crf: int | None = None,
        preset: X264Preset | None = None,
        hwaccel: bool = False,
        av1_crf: int = DEFAULT_AV1_CRF,
        av1_preset: int = DEFAULT_AV1_PRESET,
    ) -> None:
        """Write bgr24 frames to `output_path` as mp4/av1/yuv420p.

        `av1_crf` (0-63) and `av1_preset` (0 slowest to 13 fastest) are SVT-AV1's
        own scales. The defaults aim at the picture x264 crf 23 / preset medium
        gave, for the visualization output this writer produces; they are not
        measured, and choosing them from real footage is the subject of
        docs/issues/encoding-presets-unmeasured-against-quality-goals.md.

        `crf` and `preset` are the x264-scale parameters this writer accepted
        while it encoded H.264. They still mean x264's scales and are translated
        forward, so an existing caller keeps the picture it asked for; both are
        deprecated and warn.
        """
        # Set first so __del__ -> close() is safe even if a later line raises:
        # close() reads _closed and _container, so both must exist before the
        # argument resolution, the path resolution, and the container open below
        # can raise.
        self._closed: bool = False
        self._container: OutputContainer | None = None
        av1_crf, av1_preset = self._resolve_quality(crf, preset, av1_crf, av1_preset)
        self._output_path: Path = Path(output_path).expanduser().resolve()
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._width: int = width
        self._height: int = height
        self._fps: float = fps
        self._frames_written: int = 0
        self._encoder_name: str = (
            "av1_nvenc" if (hwaccel and _nvenc_encoder_usable()) else "libsvtav1"
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
        if self._encoder_name == "libsvtav1":
            stream.options = {"preset": str(av1_preset), "crf": str(av1_crf)}
        else:
            # av1_nvenc's scales are its own: cq runs 0-51 and its presets are
            # named. Forwarding SVT-AV1's numbers would land on a different
            # meaning in both fields.
            nvenc_cq = max(0, av1_crf - _X264_TO_AV1_CRF_OFFSET)
            stream.options = {"preset": _NVENC_PRESET, "cq": str(nvenc_cq)}
        self._av1_crf: int = av1_crf
        self._av1_preset: int = av1_preset
        self._container = container
        self._stream: VideoStream = stream

    @staticmethod
    def _resolve_quality(
        crf: int | None,
        preset: X264Preset | None,
        av1_crf: int,
        av1_preset: int,
    ) -> tuple[int, int]:
        """Fold the deprecated x264-scale arguments into SVT-AV1's scales.

        A caller that passed x264 values keeps the picture it asked for: the
        rate is offset onto SVT-AV1's range and the named preset is looked up.
        Passing both forms is a conflict rather than a precedence puzzle.
        """
        if crf is None and preset is None:
            return av1_crf, av1_preset
        if crf is not None and av1_crf != DEFAULT_AV1_CRF:
            message = "pass either crf (x264 scale, deprecated) or av1_crf, not both"
            raise MediaProbeError(message)
        if preset is not None and av1_preset != DEFAULT_AV1_PRESET:
            message = (
                "pass either preset (x264 scale, deprecated) or av1_preset, not both"
            )
            raise MediaProbeError(message)
        named = ", ".join(sorted(_AV1_PRESET_FROM_X264))
        if preset is not None and preset not in _AV1_PRESET_FROM_X264:
            message = f"unknown x264 preset {preset!r}; expected one of {named}"
            raise MediaProbeError(message)
        scales = "av1_crf (0-63) and av1_preset (0 slowest to 13 fastest)"
        deprecation = (
            f"FFmpegVideoWriter's crf and preset arguments are x264's scales and "
            f"are deprecated; this writer encodes AV1. Use {scales}."
        )
        warnings.warn(deprecation, DeprecationWarning, stacklevel=3)
        resolved_crf = av1_crf if crf is None else _av1_crf_from_x264(crf)
        resolved_preset = (
            av1_preset if preset is None else _AV1_PRESET_FROM_X264[preset]
        )
        return resolved_crf, resolved_preset

    @property
    def av1_crf(self) -> int:
        """The SVT-AV1 rate in force, after any x264-scale argument was folded in."""
        return self._av1_crf

    @property
    def av1_preset(self) -> int:
        """The SVT-AV1 preset in force, after any x264-scale argument was folded in."""
        return self._av1_preset

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

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()
