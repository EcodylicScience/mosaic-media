"""The seek path verifies where the container actually landed.

A backward seek to a keyframe's timestamp must decode that keyframe first. The
reader checks the decoded landing time against the indexed keyframe time before
counting frames forward, so a seek that lands on the wrong keyframe raises
instead of silently returning a frame from the wrong position.
"""

from pathlib import Path

import pytest

from mosaic_media.io.index import SeekIndex, build_seek_index
from mosaic_media.io.packets import scan_packets_in_process
from mosaic_media.io.reader import VideoReader
from mosaic_media.probe.errors import MediaProbeError


def test_seek_landing_mismatch_raises(corpus_gop12: Path) -> None:
    # Inject an index that claims frame 6 (a P-frame) is a keyframe. A backward
    # seek to its timestamp lands on the real keyframe 0, so the decoded landing
    # time differs from the claimed keyframe time by a whole group. Without the
    # landing check the reader would trust the arithmetic and return frame 0's
    # pixels labeled as frame 6; the check turns that into an explicit error.
    packets, _source = scan_packets_in_process(corpus_gop12)
    real = build_seek_index(packets)
    corrupt = SeekIndex(frame_times=real.frame_times, keyframe_indices=(0, 6))
    with VideoReader(corpus_gop12, index=corrupt) as reader:
        with pytest.raises(MediaProbeError):
            reader.seek(6)
