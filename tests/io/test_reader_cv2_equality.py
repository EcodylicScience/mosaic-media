from pathlib import Path

import numpy
import pytest

from mosaic_media.io.reader import VideoReader


def _cv2_all_frames(path: Path) -> list[numpy.ndarray]:
    import cv2

    capture = cv2.VideoCapture(str(path))
    frames: list[numpy.ndarray] = []
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frames.append(frame)
    finally:
        capture.release()
    return frames


def _max_channel_difference(a: numpy.ndarray, b: numpy.ndarray) -> int:
    return int(numpy.abs(a.astype(numpy.int16) - b.astype(numpy.int16)).max())


def test_sequential_matches_cv2(corpus_gop12: Path) -> None:
    pytest.importorskip("cv2")
    cv2_frames = _cv2_all_frames(corpus_gop12)
    with VideoReader(corpus_gop12) as reader:
        ours = [frame for _index, frame in reader]
    assert len(ours) == len(cv2_frames)
    for mine, theirs in zip(ours, cv2_frames):
        assert _max_channel_difference(mine, theirs) <= 2


def test_strided_matches_cv2(corpus_gop12: Path) -> None:
    pytest.importorskip("cv2")
    cv2_frames = _cv2_all_frames(corpus_gop12)
    expected = cv2_frames[::4]
    with VideoReader(corpus_gop12, frame_step=4) as reader:
        ours = [frame for _index, frame in reader]
    assert len(ours) == len(expected)
    for mine, theirs in zip(ours, expected):
        assert _max_channel_difference(mine, theirs) <= 2


def test_seek_matches_cv2(corpus_gop12: Path) -> None:
    pytest.importorskip("cv2")
    cv2_frames = _cv2_all_frames(corpus_gop12)
    with VideoReader(corpus_gop12) as reader:
        for target in (0, 7, 12, 15, 24, 40):
            reader.seek(target)
            ok, frame = reader.read()
            assert ok
            assert frame is not None
            assert _max_channel_difference(frame, cv2_frames[target]) <= 2
