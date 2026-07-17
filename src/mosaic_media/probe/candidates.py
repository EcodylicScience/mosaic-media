"""Which files are candidate videos. Candidacy is the only thing an extension
decides; the container decides everything else."""

from pathlib import Path

VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".mp4",
        ".m4v",
        ".mov",
        ".avi",
        ".mkv",
        ".webm",
        ".mts",
        ".m2ts",
        ".mpg",
        ".mpeg",
        ".wmv",
        # A raw H.264 elementary stream: no container, no timestamps. Probed
        # with timing_measured=False and routed to a timestamp-generating
        # remux by the analysis verdict.
        ".h264",
    }
)


def is_candidate_video(path: Path) -> bool:
    """True when `path` has a video extension, matched case-insensitively."""
    return path.suffix.lower() in VIDEO_EXTENSIONS
