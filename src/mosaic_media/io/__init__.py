"""Frame reading through system ffmpeg. Requires the [io] extra (numpy).

This subpackage is not re-exported by the mosaic_media core facade: importing
mosaic_media must not pull numpy. Import mosaic_media.io explicitly.
"""

from .index import SeekIndex, build_seek_index
from .multi import MultiVideoReader, VideoSegment
from .reader import VideoReader
from .writer import FFmpegVideoWriter

__all__ = [
    "FFmpegVideoWriter",
    "MultiVideoReader",
    "SeekIndex",
    "VideoReader",
    "VideoSegment",
    "build_seek_index",
]
