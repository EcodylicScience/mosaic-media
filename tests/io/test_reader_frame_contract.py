"""Decoded frames are writable, C-contiguous, non-aliasing buffers.

Consumers draw overlays directly onto returned frames. The array wraps the
converted frame's own buffer, so numpy's OWNDATA flag is False by design; the
contract that matters is writability, C-contiguity, and that consecutive
reads never alias one another -- mutating one returned frame must not be able
to corrupt another.

Every conversion path is covered, not just the plain one. Rotation and scaling
pad the line size, so contiguity is the guarantee most at risk on exactly the
paths a plain-path-only test leaves unchecked.
"""

from pathlib import Path

import numpy
import pytest

from mosaic_media.io.reader import VideoReader
from tests.helpers.corpus import generate_video


def _assert_contract(first: numpy.ndarray, second: numpy.ndarray) -> None:
    for frame in (first, second):
        assert frame.flags["WRITEABLE"]
        assert frame.flags["C_CONTIGUOUS"]
    assert not numpy.may_share_memory(first, second)
    untouched = second.copy()
    first[:] = 0
    assert numpy.array_equal(second, untouched)


@pytest.mark.parametrize(
    ("resize", "grayscale"),
    [
        (None, False),
        (None, True),
        ((160, 120), False),
        ((160, 120), True),
    ],
)
def test_frames_are_writable_c_contiguous_and_non_aliasing(
    corpus_gop12: Path,
    resize: tuple[int, int] | None,
    grayscale: bool,
) -> None:
    with VideoReader(corpus_gop12, resize=resize, grayscale=grayscale) as reader:
        ok_first, first = reader.read()
        ok_second, second = reader.read()
    assert ok_first
    assert ok_second
    assert first is not None
    assert second is not None
    _assert_contract(first, second)


@pytest.mark.parametrize("rotation_degrees", [90, 180, 270])
def test_rotated_frames_hold_the_contract(
    tmp_path: Path, rotation_degrees: int
) -> None:
    path = generate_video(
        tmp_path / f"rot{rotation_degrees}.mp4",
        frames=6,
        fps=30.0,
        gop=12,
        rotation_degrees=rotation_degrees,
    )
    with VideoReader(path) as reader:
        ok_first, first = reader.read()
        ok_second, second = reader.read()
    assert ok_first
    assert ok_second
    assert first is not None
    assert second is not None
    _assert_contract(first, second)
