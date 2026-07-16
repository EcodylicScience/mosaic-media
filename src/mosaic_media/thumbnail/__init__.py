"""Still-image derivatives produced through system ffmpeg. Standard library only."""

from .downscale import downscale_to_jpeg, thumbnail_dimensions
from .extract import extract_first_frame

__all__ = [
    "downscale_to_jpeg",
    "extract_first_frame",
    "thumbnail_dimensions",
]
