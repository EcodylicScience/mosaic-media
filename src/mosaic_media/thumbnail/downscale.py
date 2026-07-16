"""Downscale a still image to a JPEG at exact target dimensions.

The caller computes the target size from persisted metadata
(`thumbnail_dimensions`), so ffmpeg never has to express aspect math in a
filter string and the output size is deterministic and testable. Writes to
the exact destination the caller names; atomicity (temp + rename) is the
caller's concern because only the caller knows the cache layout.
"""

import subprocess
from pathlib import Path

from ..probe.errors import MediaProbeError

DOWNSCALE_TIMEOUT_SECONDS = 60


def thumbnail_dimensions(width: int, height: int, *, cap: int = 320) -> tuple[int, int]:
    """Target (width, height) with the long edge capped at `cap`.

    Never upscales; never returns a zero dimension.
    """
    long_edge = max(width, height)
    if long_edge <= cap:
        return (width, height)
    scale = cap / long_edge
    return (max(1, round(width * scale)), max(1, round(height * scale)))


def downscale_to_jpeg(
    source: Path, destination: Path, *, width: int, height: int
) -> None:
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(source.absolute()),
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:{height}",
        "-q:v",
        "3",
        "-y",
        str(destination.absolute()),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=DOWNSCALE_TIMEOUT_SECONDS
        )
    except FileNotFoundError as exc:
        message = f"ffmpeg binary not found on PATH: {exc}"
        raise MediaProbeError(message) from exc
    except subprocess.TimeoutExpired as exc:
        message = f"ffmpeg timed out downscaling {source}"
        raise MediaProbeError(message) from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown error"
        message = f"ffmpeg failed downscaling {source}: {detail}"
        raise MediaProbeError(message)
    if not destination.exists() or destination.stat().st_size == 0:
        message = f"ffmpeg produced no output at {destination}"
        raise MediaProbeError(message)
