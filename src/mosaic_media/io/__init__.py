"""Frame reading through system ffmpeg. Requires the [io] extra (numpy).

This subpackage is not re-exported by the mosaic_media core facade: importing
mosaic_media must not pull numpy. Import mosaic_media.io explicitly.
"""

from .index import SeekIndex, build_seek_index

__all__ = ["SeekIndex", "build_seek_index"]
