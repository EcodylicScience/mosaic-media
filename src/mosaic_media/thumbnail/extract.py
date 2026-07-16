"""Extract a video's first frame at full resolution.

This is a different artifact from the downscaled tile poster the upload client
writes to `<staging_dir>/<file_id>.poster.jpg`. Neither replaces the other.
"""

import subprocess
from pathlib import Path

from ..probe.errors import MediaProbeError

EXTRACT_TIMEOUT_SECONDS = 60


def extract_first_frame(source: Path, destination: Path) -> None:
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(source.absolute()),
        "-frames:v",
        "1",
        "-y",
        str(destination.absolute()),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=EXTRACT_TIMEOUT_SECONDS
        )
    except FileNotFoundError as exc:
        message = f"ffmpeg binary not found on PATH: {exc}"
        raise MediaProbeError(message) from exc
    except subprocess.TimeoutExpired as exc:
        message = f"ffmpeg timed out extracting the first frame from {source}"
        raise MediaProbeError(message) from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown error"
        message = f"ffmpeg failed extracting the first frame from {source}: {detail}"
        raise MediaProbeError(message)
    if not destination.exists() or destination.stat().st_size == 0:
        message = f"ffmpeg produced no output at {destination}"
        raise MediaProbeError(message)
