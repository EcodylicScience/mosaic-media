from pathlib import Path

import numpy

from mosaic_media.io.reader import VideoReader
from tests.helpers.corpus import decode_md5s, frame_md5


def test_strided_read_equals_golden_subset(corpus_gop12: Path) -> None:
    goldens = decode_md5s(corpus_gop12)
    expected = [(i, goldens[i]) for i in range(0, len(goldens), 5)]
    with VideoReader(corpus_gop12, frame_step=5) as reader:
        produced = [(index, frame_md5(frame)) for index, frame in reader]
    assert produced == expected


def test_start_and_end_window(corpus_gop12: Path) -> None:
    goldens = decode_md5s(corpus_gop12)
    expected = [(i, goldens[i]) for i in range(10, 30)]
    with VideoReader(corpus_gop12, start_frame=10, end_frame=30) as reader:
        produced = [(index, frame_md5(frame)) for index, frame in reader]
    assert produced == expected


def test_start_end_and_step_combined(corpus_gop12: Path) -> None:
    goldens = decode_md5s(corpus_gop12)
    expected = [(i, goldens[i]) for i in range(6, 40, 3)]
    with VideoReader(corpus_gop12, start_frame=6, end_frame=40, frame_step=3) as reader:
        produced = [(index, frame_md5(frame)) for index, frame in reader]
    assert produced == expected


def test_grayscale_equals_gray_golden_and_shape(corpus_gop12: Path) -> None:
    goldens = decode_md5s(corpus_gop12, grayscale=True)
    produced: list[str] = []
    with VideoReader(corpus_gop12, grayscale=True) as reader:
        for _index, frame in reader:
            assert frame.ndim == 2
            assert frame.shape == (reader.height, reader.width)
            assert frame.dtype == numpy.uint8
            produced.append(frame_md5(frame))
    assert produced == goldens


def test_resize_changes_output_shape_only(corpus_gop12: Path) -> None:
    with VideoReader(corpus_gop12, resize=(160, 120)) as reader:
        assert reader.width == 160
        assert reader.height == 120
        ok, frame = reader.read()
    assert ok
    assert frame is not None
    assert frame.shape == (120, 160, 3)


def test_resize_and_grayscale_together(corpus_gop12: Path) -> None:
    with VideoReader(corpus_gop12, resize=(160, 120), grayscale=True) as reader:
        ok, frame = reader.read()
    assert ok
    assert frame is not None
    assert frame.shape == (120, 160)
