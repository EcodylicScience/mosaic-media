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

# The encode probe allocates an encoder session on the device, paying a cold CUDA
# context creation the listing probe never touches; a host enumerating several
# GPUs takes seconds over it. The ceiling is generous because exceeding it reads
# as unusable and drops a permitted run onto the CPU encoder, and because a
# transcode that consults this probe then runs for minutes.
_ENCODE_PROBE_TIMEOUT_SECONDS = 30

# The encode probe's frame size. NVENC declares a minimum encode resolution per
# codec and AV1's is the largest of the family, so a frame sized for the decode
# probe is refused by a device that encodes AV1 perfectly well -- a false
# negative on exactly the hardware the probe exists to find. 256x256 clears every
# published minimum and still encodes in a single frame.
_ENCODE_PROBE_SIZE = "256x256"

_ffmpeg_ok: bool | None = None
_nvdec_ok: bool | None = None
_encoder_ok: dict[str, bool] = {}
_encoder_usable_ok: dict[str, bool] = {}


def _probe(
    command: list[str], *, timeout: float = _PROBE_TIMEOUT_SECONDS
) -> "subprocess.CompletedProcess[str] | None":
    """Run a capability probe, or None when ffmpeg could not be run at all.

    A probe answers a yes-or-no question, so every way of failing to run one --
    a missing binary, a timeout, a signal -- is simply a no. Nothing here
    raises, which is why these probes stay out of the shared runner in
    `mosaic_media.ffmpeg`: they have no error to inject and no failure to word.
    """
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
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


def encoder_usable(name: str) -> bool:
    """True when ffmpeg can open `name` on this machine and encode a frame with
    it. Cached per name.

    The usability companion to `encoder_available`, which answers only whether the
    build lists the encoder. A listing is a build-time fact: a distribution ffmpeg
    compiled with NVENC lists `av1_nvenc` on every machine it is installed on,
    including a GPU generation with no AV1 encoder at all, and there the encoder
    fails at startup with "No capable devices found" rather than falling back.
    Encoding one frame is the only reliable check -- the principle
    `nvdec_available` states for the decode side.

    A separate function rather than a change to `encoder_available`, because the
    two questions have separate askers: a caller choosing between a hardware and a
    software encoder needs usability, and a caller asking whether a build carries a
    CPU encoder at all should not pay a device probe for the answer.

    The encoder is opened the way a transcode opens it -- no `-init_hw_device`,
    since an NVENC encoder fed frames from system memory creates its own device --
    so this answers for the invocation the caller will make. Initializing a CUDA
    device answers a different question and answers it wrongly here: a card whose
    CUDA runtime is healthy and whose silicon has no AV1 encoder passes that check
    and fails this one.

    A machine whose encoder sessions are all in use answers False and stays False
    for the life of the process. That is deliberate: this result selects an
    argument vector, and a capability that changed midway would emit two different
    commands within one job.
    """
    cached = _encoder_usable_ok.get(name)
    if cached is not None:
        return cached
    if not encoder_available(name):
        # Not compiled in: there is no encoder to open and no probe to run.
        _encoder_usable_ok[name] = False
        return False
    result = _probe(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"nullsrc=s={_ENCODE_PROBE_SIZE}:d=0.1",
            "-frames:v",
            "1",
            # Naming the encoder is load-bearing: the null muxer's default video
            # codec is wrapped_avframe, so without it the command exits zero
            # having opened no encoder at all.
            "-c:v",
            name,
            "-pix_fmt",
            "yuv420p",
            "-f",
            "null",
            "-",
        ],
        timeout=_ENCODE_PROBE_TIMEOUT_SECONDS,
    )
    usable = result is not None and result.returncode == 0
    _encoder_usable_ok[name] = usable
    return usable
