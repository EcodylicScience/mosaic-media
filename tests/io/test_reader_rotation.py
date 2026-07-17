from pathlib import Path

import pytest

from mosaic_media.io.reader import VideoReader
from tests.helpers.corpus import decode_md5s, frame_md5, generate_video


@pytest.mark.parametrize(
    ("rotation_degrees", "width", "height"),
    [(90, 240, 320), (180, 320, 240), (270, 240, 320)],
)
def test_rotation_is_bit_exact_against_ffmpeg_autorotation(
    tmp_path: Path, rotation_degrees: int, width: int, height: int
) -> None:
    # The reader's in-process filter graph (a transpose for the quarter-turns,
    # hflip plus vflip for 180) must match system-ffmpeg's autorotated output
    # frame-for-frame, so the displayed-orientation contract and the framemd5
    # goldens hold with no cv2 in the loop.
    path = generate_video(
        tmp_path / f"rot{rotation_degrees}.mp4",
        frames=24,
        fps=30.0,
        gop=12,
        rotation_degrees=rotation_degrees,
    )
    goldens = decode_md5s(path)
    produced: list[str] = []
    with VideoReader(path) as reader:
        assert reader.width == width
        assert reader.height == height
        for _index, frame in reader:
            assert frame.shape == (height, width, 3)
            produced.append(frame_md5(frame))
    assert produced == goldens
