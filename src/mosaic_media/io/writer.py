"""BGR-frame video writer through a system-ffmpeg subprocess pipe. Requires numpy.

Raw bgr24 frames are piped to ffmpeg's stdin and encoded with libx264, or with
h264_nvenc when hwaccel is requested and the encoder is available. Absorbed from
mosaic's video_io: it was already pure ffmpeg, so only the capability probing
and error type change to match this package.
"""

import subprocess
from pathlib import Path

import numpy

from ..hwaccel import encoder_available, ffmpeg_available
from ..probe.errors import MediaProbeError


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
        if not ffmpeg_available():
            message = "ffmpeg not found on PATH; install ffmpeg to write frames"
            raise MediaProbeError(message)
        self._output_path: Path = Path(output_path).expanduser().resolve()
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._width: int = width
        self._height: int = height
        self._fps: float = fps
        self._frames_written: int = 0
        self._closed: bool = False

        use_nvenc = hwaccel and encoder_available("h264_nvenc")
        command = [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{width}x{height}",
            "-r",
            str(fps),
            "-i",
            "pipe:0",
        ]
        if use_nvenc:
            command += ["-c:v", "h264_nvenc", "-preset", preset, "-cq", str(crf)]
        else:
            command += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]
        command += ["-pix_fmt", "yuv420p", str(self._output_path)]

        self._process: subprocess.Popen[bytes] | None = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

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

    def write(self, frame: numpy.ndarray) -> None:
        if self._closed:
            message = "writer is closed"
            raise MediaProbeError(message)
        process = self._process
        if process is None or process.stdin is None:
            message = "ffmpeg process is not running"
            raise MediaProbeError(message)
        expected_shape = (self._height, self._width, 3)
        if frame.shape != expected_shape or frame.dtype != numpy.uint8:
            message = f"frame shape {tuple(frame.shape)} dtype {frame.dtype} does not match writer geometry {expected_shape} dtype uint8"
            raise MediaProbeError(message)
        try:
            _ = process.stdin.write(frame.tobytes())
        except BrokenPipeError as exc:
            # ffmpeg died and closed the read end of the pipe. Reap it for the
            # real exit code so the message says why rather than losing the
            # frame silently.
            try:
                returncode = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                returncode = None
            message = f"ffmpeg closed its input pipe while writing {self._output_path} (exit code {returncode})"
            raise MediaProbeError(message) from exc
        self._frames_written += 1

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except BrokenPipeError:
                # A dead ffmpeg leaves buffered stdin bytes with nowhere to go;
                # the nonzero exit below is the real diagnosis, not this flush.
                pass
        try:
            returncode = process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                returncode = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                return
        if returncode != 0:
            message = (
                f"ffmpeg exited with code {returncode} writing {self._output_path}"
            )
            raise MediaProbeError(message)

    def __enter__(self) -> "FFmpegVideoWriter":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()
