"""Every array these readers hand out is a writable, C-contiguous, non-aliasing
buffer.

Consumers draw overlays directly onto returned frames and pass them to code that
assumes contiguous memory. Where the converted frame's line size already matches
its row length the array wraps that buffer directly; where the graph padded it,
the array is an owned contiguous copy. The contract is identical either way, and
it is what these tests pin: writability, C-contiguity, and that two separately
returned arrays never alias -- mutating one must not be able to corrupt another.

Every entry point that returns an array is covered, not only read(). All of them
convert at one site today, so a single implementation upholds all of them; the
reason to assert each is that nothing otherwise stops a later change from adding
a path that bypasses that site. A quarter-turn at 1920x1080 once returned a
non-contiguous array for exactly that reason, uncaught because the only test
asserting contiguity ran at a size whose rows happened to align.

The 320x240 fixture is deliberate and sufficient: at that size the scaled and
quarter-turn outputs are padded (rows of 480 and 720 bytes in line sizes of 576
and 768), so the copy path is exercised without paying for 1080p clips. The
plain and 180-degree cases are unpadded there, which is equally worth covering --
they are where the no-copy path runs. This module does not assert the absence
of a copy: whether a given output size is padded is a property of the libav
build, not of this package, so nothing here pins it either way.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Literal

import numpy
import pytest

from mosaic_media.io.multi import MultiVideoReader
from mosaic_media.io.reader import VideoReader
from tests.helpers.corpus import generate_video

EntryPoint = Literal["read", "iteration", "read_frames", "read_batch"]

ENTRY_POINTS: tuple[EntryPoint, ...] = (
    "read",
    "iteration",
    "read_frames",
    "read_batch",
)


def _assert_buffer(frame: numpy.ndarray) -> None:
    assert frame.flags["WRITEABLE"]
    assert frame.flags["C_CONTIGUOUS"]


def _assert_independent(first: numpy.ndarray, second: numpy.ndarray) -> None:
    assert not numpy.may_share_memory(first, second)
    untouched = second.copy()
    first[:] = 0
    assert numpy.array_equal(second, untouched)


def _via_read(reader: VideoReader) -> tuple[numpy.ndarray, numpy.ndarray]:
    ok_first, first = reader.read()
    ok_second, second = reader.read()
    assert ok_first
    assert ok_second
    assert first is not None
    assert second is not None
    return first, second


def _via_iteration(reader: VideoReader) -> tuple[numpy.ndarray, numpy.ndarray]:
    iterator = iter(reader)
    _first_index, first = next(iterator)
    _second_index, second = next(iterator)
    return first, second


def _via_read_frames(reader: VideoReader) -> tuple[numpy.ndarray, numpy.ndarray]:
    produced = [frame for _index, frame in reader.read_frames([2, 9])]
    assert len(produced) == 2
    return produced[0], produced[1]


def _via_read_batch(reader: VideoReader) -> tuple[numpy.ndarray, numpy.ndarray]:
    # This entry point cannot catch a conversion-site regression: read_batch
    # stacks its frames, and numpy.stack always materializes a fresh contiguous
    # array whether or not its inputs were contiguous. Removing the copy from
    # the conversion site leaves every read_batch case here green while the
    # other three entry points fail. It is covered anyway, because stacking is
    # itself a contract the batch API owes its callers, and because a future
    # read_batch that stopped stacking would need this assertion waiting.
    _first_indices, first = reader.read_batch(2)
    _second_indices, second = reader.read_batch(2)
    assert first.shape[0] == 2
    assert second.shape[0] == 2
    return first, second


_COLLECTORS: dict[
    EntryPoint, Callable[[VideoReader], tuple[numpy.ndarray, numpy.ndarray]]
] = {
    "read": _via_read,
    "iteration": _via_iteration,
    "read_frames": _via_read_frames,
    "read_batch": _via_read_batch,
}


@pytest.fixture(scope="module")
def rotated_clips(tmp_path_factory: pytest.TempPathFactory) -> dict[int, Path]:
    """One clip per mapped display rotation, generated once for this module."""
    root = tmp_path_factory.mktemp("frame_contract_rotated")
    return {
        degrees: generate_video(
            root / f"rot{degrees}.mp4",
            frames=12,
            fps=30.0,
            gop=12,
            rotation_degrees=degrees,
        )
        for degrees in (90, 180, 270)
    }


@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
@pytest.mark.parametrize(
    ("resize", "grayscale"),
    [
        (None, False),
        (None, True),
        ((160, 120), False),
        ((160, 120), True),
    ],
)
def test_every_entry_point_returns_contract_buffers(
    corpus_gop12: Path,
    entry_point: EntryPoint,
    resize: tuple[int, int] | None,
    grayscale: bool,
) -> None:
    with VideoReader(corpus_gop12, resize=resize, grayscale=grayscale) as reader:
        first, second = _COLLECTORS[entry_point](reader)
    _assert_buffer(first)
    _assert_buffer(second)
    _assert_independent(first, second)


@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
@pytest.mark.parametrize("rotation_degrees", [90, 180, 270])
def test_every_entry_point_returns_contract_buffers_when_rotated(
    rotated_clips: dict[int, Path],
    entry_point: EntryPoint,
    rotation_degrees: int,
) -> None:
    with VideoReader(rotated_clips[rotation_degrees]) as reader:
        first, second = _COLLECTORS[entry_point](reader)
    _assert_buffer(first)
    _assert_buffer(second)
    _assert_independent(first, second)


def test_multi_video_reader_returns_contract_buffers(corpus_gop12: Path) -> None:
    with MultiVideoReader([corpus_gop12, corpus_gop12]) as reader:
        ok_first, first = reader.read()
        ok_second, second = reader.read()
    assert ok_first
    assert ok_second
    assert first is not None
    assert second is not None
    _assert_buffer(first)
    _assert_buffer(second)
    _assert_independent(first, second)


def test_multi_video_reader_holds_the_contract_across_a_segment_boundary(
    corpus_gop12: Path,
) -> None:
    # The junction is where a segment's reader is closed and the next one
    # constructed, so it is where a fresh conversion graph first emits.
    with MultiVideoReader([corpus_gop12, corpus_gop12]) as reader:
        boundary = reader.segments[1].start_frame
        reader.seek(boundary - 1)
        ok_last, last = reader.read()
        ok_first, first = reader.read()
    assert ok_last
    assert ok_first
    assert last is not None
    assert first is not None
    _assert_buffer(last)
    _assert_buffer(first)
    _assert_independent(last, first)
