"""Generated video corpus and ffmpeg-derived ground truth for the reader tests.

Every clip is built by the ffmpeg on PATH, a documented system dependency, so a
missing encoder is a failure, not a skip. Ground truth comes from ffmpeg's own
framemd5: hashing the decoded frame in the reader's output pixel format lets the
reader be checked frame-exact with no cv2 in the loop.
"""

import hashlib
import subprocess
import tempfile
from pathlib import Path

import numpy

from tests.helpers.media_fixtures import build


def generate_video(
    path: Path,
    *,
    frames: int,
    fps: float = 30.0,
    gop: int = 30,
    size: tuple[int, int] = (320, 240),
    codec: str = "libx264",
    rotation_degrees: int = 0,
) -> Path:
    """Generate a testsrc2 clip of exactly `frames` frames at `fps` with a
    keyframe every `gop` frames. A nonzero `rotation_degrees` is written as a
    display-matrix side data rotation via a copy re-mux, leaving coded
    dimensions unchanged."""
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = size
    # Ask for more than enough duration, then cap with -frames:v for an exact count.
    duration = frames / fps + 1.0
    lavfi_source = f"testsrc2=size={width}x{height}:rate={fps}:duration={duration}"
    encode_source = ["-f", "lavfi", "-i", lavfi_source]
    encode_arguments = (
        "-frames:v",
        str(frames),
        "-c:v",
        codec,
        "-pix_fmt",
        "yuv420p",
        "-g",
        str(gop),
    )
    if rotation_degrees == 0:
        return build(path, *encode_arguments, source=encode_source)
    with tempfile.TemporaryDirectory() as work:
        upright = Path(work) / "upright.mp4"
        _ = build(upright, *encode_arguments, source=encode_source)
        return build(
            path,
            "-c",
            "copy",
            source=["-display_rotation", str(rotation_degrees), "-i", str(upright)],
        )


def decode_md5s(path: Path, *, grayscale: bool = False) -> list[str]:
    """Per-frame md5 of the decoded frame, in presentation order, in the same
    pixel format the reader yields (bgr24, or gray when grayscale=True). Each
    digest equals hashlib.md5(frame.tobytes()).hexdigest() for the reader frame."""
    pixel_format = "gray" if grayscale else "bgr24"
    # Not build(): build discards stdout, and this call must read the framemd5
    # report off stdout. The clip-construction path reuses build; this ground-
    # truth path needs the output, so it runs its own capture.
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-v",
            "error",
            "-i",
            str(path),
            "-pix_fmt",
            pixel_format,
            "-f",
            "framemd5",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        message = f"framemd5 failed for {path}: {result.stderr.strip()}"
        raise RuntimeError(message)
    digests: list[str] = []
    for line in result.stdout.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        digests.append(line.rsplit(",", 1)[-1].strip())
    return digests


def frame_md5(frame: numpy.ndarray) -> str:
    """md5 of a numpy frame's C-contiguous bytes, matching decode_md5s digests."""
    return hashlib.md5(frame.tobytes()).hexdigest()
