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
        # Raw elementary streams: no container, and so no packet timestamps.
        # The analysis verdict routes such a file to a remux that generates
        # them. Every suffix the two raw demuxers register is taken, in the
        # order they register them, because each one denotes a raw video
        # stream: these demuxers carry no audio-only, subtitle, still-image or
        # manifest spelling the way the container demuxers do. That is a
        # property of these two, not a rule for every demuxer.
        ".h26l",
        ".h264",
        ".264",
        ".avc",
        ".hevc",
        ".h265",
        ".265",
    }
)


def is_candidate_video(path: Path) -> bool:
    """True when `path` has a video extension, matched case-insensitively."""
    return path.suffix.lower() in VIDEO_EXTENSIONS
