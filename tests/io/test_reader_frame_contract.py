"""Decoded frames are writable, C-contiguous, non-aliasing buffers.

Consumers draw overlays directly onto returned frames. The array wraps the
reformatted frame's own buffer, so numpy's OWNDATA flag is False by design;
the contract that matters is writability, C-contiguity, and that consecutive
reads never alias one another -- mutating one returned frame must not be able
to corrupt another.
"""

from pathlib import Path

import numpy

from mosaic_media.io.reader import VideoReader


def test_frames_are_writable_c_contiguous_and_non_aliasing(
    corpus_gop12: Path,
) -> None:
    with VideoReader(corpus_gop12) as reader:
        ok_first, first = reader.read()
        ok_second, second = reader.read()
    assert ok_first
    assert ok_second
    assert first is not None
    assert second is not None
    for frame in (first, second):
        assert frame.flags["WRITEABLE"]
        assert frame.flags["C_CONTIGUOUS"]
    assert not numpy.may_share_memory(first, second)
    untouched = second.copy()
    first[:] = 0
    assert numpy.array_equal(second, untouched)
