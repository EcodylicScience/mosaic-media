"""Which files are candidate videos. Candidacy is the only thing an extension
decides; the container decides everything else."""

from pathlib import Path

VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {
        # A suffix's presence in a demuxer's registered extension list does not
        # decide membership here, in either direction. The ogg demuxer
        # registers only "ogg", yet .ogv is a candidate; .mts, .m2ts, .mpg,
        # .mpeg and .wmv are candidates whose demuxers register no extension at
        # all. What decides membership is whether a file of that format has
        # been handed to this package and measured end to end; a format's
        # alternate spellings enter together.
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
        # Ogg: only .ogv is a candidate. The family the muxers name on this
        # build is .ogv (Ogg Video, video/ogg, theora + vorbis), .ogg (Ogg,
        # application/ogg, carrying either kind but audio by convention since
        # 2007), and .oga, .spx and .opus, all audio/ogg with no default video
        # codec. There is no ogx muxer, so .ogx is invisible to this
        # classification and stays out.
        ".ogv",
    }
)


def is_candidate_video(path: Path) -> bool:
    """True when `path` has a video extension, matched case-insensitively."""
    return path.suffix.lower() in VIDEO_EXTENSIONS
