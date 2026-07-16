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
    }
)


def is_candidate_video(path: Path) -> bool:
    """True when `path` has a video extension, matched case-insensitively."""
    return path.suffix.lower() in VIDEO_EXTENSIONS
