"""The single exception type media_probe raises."""


class MediaProbeError(RuntimeError):
    """A file could not be probed: no video stream, or ffprobe failed."""
