"""Frame reading through in-process libav bindings (PyAV). Requires numpy and av.

This subpackage is not re-exported by the mosaic_media core facade: importing
mosaic_media must not pull numpy or av. Import mosaic_media.io explicitly.
"""

from .index import IndexSource, IndexSpace, SeekIndex, build_seek_index
from .multi import MultiVideoReader, VideoSegment
from .reader import VideoReader
from .writer import FFmpegVideoWriter

__all__ = [
    "FFmpegVideoWriter",
    "IndexSource",
    "IndexSpace",
    "MultiVideoReader",
    "SeekIndex",
    "VideoReader",
    "VideoSegment",
    "build_seek_index",
]
