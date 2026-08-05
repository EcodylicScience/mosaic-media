"""ffmpeg and hardware-acceleration capability probing. Standard library only.

Absorbed from an existing video I/O layer so the frame reader (NVDEC), the
writer (NVENC), and the transcode converter share one probe rather than three
copies. It sits at the package top level, not inside transcode/, because the
io layer must reach it without importing transcode.

Each result is cached: a capability does not change while the process runs, and
each check spawns an ffmpeg subprocess.
"""

import shutil
import subprocess

_PROBE_TIMEOUT_SECONDS = 5

_ffmpeg_ok: bool | None = None
_nvdec_ok: bool | None = None
_encoder_ok: dict[str, bool] = {}


def _probe(command: list[str]) -> "subprocess.CompletedProcess[str] | None":
    """Run a capability probe, or None when ffmpeg could not be run at all.

    A probe answers a yes-or-no question, so every way of failing to run one --
    a missing binary, a timeout, a signal -- is simply a no. Nothing here
    raises, which is why these probes stay out of the shared runner in
    `mosaic_media.ffmpeg`: they have no error to inject and no failure to word.
    """
    try:
        return subprocess.run(
            command, capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.SubprocessError):
        return None


def ffmpeg_available() -> bool:
    """True when an ffmpeg binary is on PATH. Cached."""
    global _ffmpeg_ok
    if _ffmpeg_ok is None:
        _ffmpeg_ok = shutil.which("ffmpeg") is not None
    return _ffmpeg_ok


def nvdec_available() -> bool:
    """True when ffmpeg can initialize a CUDA device for decoding. Cached.

    `ffmpeg -hwaccels` lists cuda whenever the binary was compiled with the
    hwaccel, even on a host with no GPU and no CUDA runtime; a stock Ubuntu
    ffmpeg reports it on a machine that cannot decode a single frame on the
    device. Actually initializing a CUDA device is the only reliable check, so
    this runs a tiny null decode with `-init_hw_device cuda` and treats a zero
    exit as available. Any device-load or initialization failure exits non-zero.
    """
    global _nvdec_ok
    if _nvdec_ok is None:
        if not ffmpeg_available():
            _nvdec_ok = False
        else:
            result = _probe(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-init_hw_device",
                    "cuda",
                    "-f",
                    "lavfi",
                    "-i",
                    "nullsrc=s=64x64:d=0.05",
                    "-frames:v",
                    "1",
                    "-f",
                    "null",
                    "-",
                ]
            )
            _nvdec_ok = result is not None and result.returncode == 0
    return _nvdec_ok


def _encoder_listed(name: str, encoders_output: str) -> bool:
    """True when `name` equals the encoder-name column of a listed encoder.

    `ffmpeg -encoders` prints one encoder per line: capability flags, the
    encoder name, then a free-text description. Comparing the name column
    exactly keeps a short query such as "av1" from matching description text
    like "(codec av1)" on a build that has no such encoder.
    """
    for line in encoders_output.splitlines():
        columns = line.split()
        if len(columns) >= 2 and columns[1] == name:
            return True
    return False


def encoder_available(name: str) -> bool:
    """True when ffmpeg lists `name` among its encoders. Cached per name.

    `encoder_available("h264_nvenc")` is the NVENC probe that layer ran; the AV1
    transcode asks for `av1_nvenc`, and the CPU fallback for `libsvtav1`.
    """
    cached = _encoder_ok.get(name)
    if cached is not None:
        return cached
    if not ffmpeg_available():
        _encoder_ok[name] = False
        return False
    result = _probe(["ffmpeg", "-encoders"])
    available = result is not None and _encoder_listed(name, result.stdout)
    _encoder_ok[name] = available
    return available
