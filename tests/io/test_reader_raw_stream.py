"""Sequential decode of a raw H.264 elementary stream, with facts injected.

A raw stream has no packet timestamps, so no seek index exists: sequential
reading with injected facts is the supported path, and anything that needs the
index refuses loudly rather than reporting zero frames. The analysis transcode
(a timestamp-generating remux) is the route to seekability.
"""

from pathlib import Path

import pytest

from mosaic_media.io.packets import scan_packets_in_process
from mosaic_media.io.reader import VideoReader
from mosaic_media.probe.errors import MediaProbeError
from mosaic_media.probe.probe import probe_media
from tests.helpers.corpus import decode_md5s, frame_md5


def test_sequential_read_with_facts_is_frame_exact(clips: dict[str, Path]) -> None:
    source = clips["raw_h264"]
    facts = probe_media(source)
    goldens = decode_md5s(source)
    assert len(goldens) == facts.frame_count
    produced: list[str] = []
    with VideoReader(source, facts=facts) as reader:
        for _index, frame in reader:
            produced.append(frame_md5(frame))
    assert produced == goldens


def test_seek_on_a_raw_stream_raises_clearly(clips: dict[str, Path]) -> None:
    source = clips["raw_h264"]
    facts = probe_media(source)
    with VideoReader(source, facts=facts) as reader:
        with pytest.raises(MediaProbeError, match="no packet timestamps"):
            reader.seek(10)


def test_the_io_packet_scan_refuses_a_raw_stream(clips: dict[str, Path]) -> None:
    with pytest.raises(MediaProbeError, match="no packet timestamps"):
        _ = scan_packets_in_process(clips["raw_h264"])
