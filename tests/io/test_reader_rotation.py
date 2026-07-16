from pathlib import Path

from mosaic_media.io.reader import VideoReader
from tests.helpers.corpus import decode_md5s, frame_md5, generate_video


def test_rotation_is_bit_exact_against_ffmpeg_autorotation(tmp_path: Path) -> None:
    # The reader's in-process transpose graph must match system-ffmpeg's
    # autorotated output frame-for-frame, so the displayed-orientation contract
    # and the framemd5 goldens hold with no cv2 in the loop.
    path = generate_video(
        tmp_path / "rot.mp4", frames=24, fps=30.0, gop=12, rotation_degrees=90
    )
    goldens = decode_md5s(path)
    produced: list[str] = []
    with VideoReader(path) as reader:
        assert reader.width == 240
        assert reader.height == 320
        for _index, frame in reader:
            assert frame.shape == (320, 240, 3)
            produced.append(frame_md5(frame))
    assert produced == goldens
