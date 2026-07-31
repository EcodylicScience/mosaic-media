"""The reader matches OpenCV's decode for the formats both can read.

The comparison allows a per-channel difference of at most 2. That slack is
swscale-version rounding in the yuv-to-bgr color conversion: different ffmpeg and
OpenCV builds round the same coefficients slightly differently, so a correct
decode can still differ by one or two levels per channel between builds. Every
defect class this suite exists to catch -- an off-by-N seek, a BGR/RGB channel
swap, a wrong returned frame, a BT.601/709 matrix mixup -- moves whole regions of
the frame by far more than two levels, so a tolerance of 2 separates them cleanly
from conversion noise. Exact-byte duty is carried elsewhere, by the framemd5
goldens that hash the decoded frame in the reader's own pixel format.

These read `h264_gop12_clip`, not the AV1 corpus the rest of the reader tests
use: OpenCV's bundled FFmpeg cannot software-decode AV1, so it cannot supply a
comparison at all. The clip has the corpus's shape -- 48 frames, 30 fps,
keyframe every 12 -- in a codec both decoders read.
"""

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


def _cv2_frame_count(path: Path) -> float:
    import cv2

    capture = cv2.VideoCapture(str(path))
    try:
        return float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        capture.release()


def test_opencv_decodes_no_av1_frame_and_says_nothing(corpus_gop12: Path) -> None:
    """The premise this module and the performance gate both rest on.

    OpenCV opens an AV1 file, reports a frame count, and then decodes nothing.
    Neither failure raises, so a caller trusting either signal reads an empty
    file as a valid one -- a length check on it passes.

    This goes red the moment a released wheel can decode AV1. That is the
    signal, not a breakage: the H.264 clip these comparisons use and the
    performance gate's H.264 corpus exist only because of this, and both can be
    retired when it stops being true.
    """
    pytest.importorskip("cv2")

    assert _cv2_frame_count(corpus_gop12) > 0
    assert _cv2_all_frames(corpus_gop12) == []


def _max_channel_difference(a: numpy.ndarray, b: numpy.ndarray) -> int:
    return int(numpy.abs(a.astype(numpy.int16) - b.astype(numpy.int16)).max())


def test_sequential_matches_cv2(h264_gop12_clip: Path) -> None:
    pytest.importorskip("cv2")
    cv2_frames = _cv2_all_frames(h264_gop12_clip)
    with VideoReader(h264_gop12_clip) as reader:
        ours = [frame for _index, frame in reader]
    assert len(ours) == len(cv2_frames)
    for mine, theirs in zip(ours, cv2_frames):
        assert _max_channel_difference(mine, theirs) <= 2


def test_strided_matches_cv2(h264_gop12_clip: Path) -> None:
    pytest.importorskip("cv2")
    cv2_frames = _cv2_all_frames(h264_gop12_clip)
    expected = cv2_frames[::4]
    with VideoReader(h264_gop12_clip, frame_step=4) as reader:
        ours = [frame for _index, frame in reader]
    assert len(ours) == len(expected)
    for mine, theirs in zip(ours, expected):
        assert _max_channel_difference(mine, theirs) <= 2


def test_seek_matches_cv2(h264_gop12_clip: Path) -> None:
    pytest.importorskip("cv2")
    cv2_frames = _cv2_all_frames(h264_gop12_clip)
    with VideoReader(h264_gop12_clip) as reader:
        for target in (0, 7, 12, 15, 24, 40):
            reader.seek(target)
            ok, frame = reader.read()
            assert ok
            assert frame is not None
            assert _max_channel_difference(frame, cv2_frames[target]) <= 2
