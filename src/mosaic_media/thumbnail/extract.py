"""Extract a video's first frame at full resolution.

This is a different artifact from the downscaled tile poster the upload client
writes to `<staging_dir>/<file_id>.poster.jpg`. Neither replaces the other.
"""

from pathlib import Path

from ..ffmpeg import require_output, run_to_completion
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
    _ = run_to_completion(
        command,
        timeout=EXTRACT_TIMEOUT_SECONDS,
        action=f"extracting the first frame from {source}",
        error_type=MediaProbeError,
    )
    require_output(destination, binary=command[0], error_type=MediaProbeError)
