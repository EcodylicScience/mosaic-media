"""Building seek indexes the way a reader would, for tests that need one."""

from pathlib import Path

from mosaic_media.io.index import SeekIndex, build_seek_index
from mosaic_media.io.packets import scan_packets_in_process
from mosaic_media.probe.ffprobe import Packet


def index_of(packets: tuple[Packet, ...]) -> SeekIndex:
    """An in-process, container-default index -- what every caller here means."""
    return build_seek_index(packets, source="in_process", space="container_default")


def index_for(path: Path) -> SeekIndex:
    """The index a reader would build for `path`, scanning it the same way."""
    return index_of(scan_packets_in_process(path)[0])
