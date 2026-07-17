"""The reader decodes AV1 and VP8 frame-exact against system ffmpeg.

AV1 is the transcode target for this stack, and VP8/webm is a corpus format;
both decode through av's bundled codec table rather than system ffmpeg, so these
suites check that path against ffmpeg's framemd5 ground truth. AV1 decode is
spec-deterministic, so av's dav1d and system ffmpeg's libdav1d agree bit-for-bit.
"""

from pathlib import Path

import numpy
import pytest

from mosaic_media.io.reader import VideoReader
from mosaic_media.probe.probe import probe_media
from tests.helpers.corpus import decode_md5s, frame_md5, generate_video


@pytest.fixture(scope="module")
def av1_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("av1_clip")
    return generate_video(
        root / "av1.mp4", frames=36, fps=30.0, gop=12, codec="libsvtav1"
    )


def test_av1_sequential_read_equals_framemd5_golden(av1_clip: Path) -> None:
    goldens = decode_md5s(av1_clip)
    with VideoReader(av1_clip) as reader:
        produced = [frame_md5(frame) for _index, frame in reader]
    assert produced == goldens


def test_av1_mid_file_seek_is_frame_exact(av1_clip: Path) -> None:
    # Frame 20 sits inside the GOP starting at keyframe 12, so the seek lands on
    # keyframe 12 and decodes forward eight frames.
    goldens = decode_md5s(av1_clip)
    with VideoReader(av1_clip) as reader:
        reader.seek(20)
        ok, frame = reader.read()
    assert ok
    assert frame is not None
    assert frame_md5(frame) == goldens[20]


def test_vp8_webm_frame_count_and_first_frame(clips: dict[str, Path]) -> None:
    webm = clips["vp8_webm"]
    facts = probe_media(webm)
    goldens = decode_md5s(webm)
    with VideoReader(webm) as reader:
        frames = [frame for _index, frame in reader]
    assert len(frames) == facts.frame_count
    assert frames[0].shape == (240, 320, 3)
    assert frames[0].dtype == numpy.uint8
    assert frame_md5(frames[0]) == goldens[0]
