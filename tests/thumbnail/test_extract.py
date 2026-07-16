from pathlib import Path

import pytest

from mosaic_media.probe.errors import MediaProbeError
from mosaic_media.thumbnail import extract_first_frame


@pytest.mark.parametrize("clip", ["cfr_mp4", "vp8_webm", "mjpeg_avi"])
def test_extract_writes_a_non_empty_jpeg(
    clip: str, clips: dict[str, Path], tmp_path: Path
) -> None:
    destination = tmp_path / "thumb.jpg"
    extract_first_frame(clips[clip], destination)
    assert destination.exists()
    assert destination.read_bytes()[:3] == b"\xff\xd8\xff"
    assert destination.stat().st_size > 0


def test_a_source_with_no_video_stream_raises(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    with pytest.raises(MediaProbeError):
        extract_first_frame(clips["no_video"], tmp_path / "thumb.jpg")


def test_a_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(MediaProbeError):
        extract_first_frame(tmp_path / "absent.mp4", tmp_path / "thumb.jpg")


def test_a_missing_destination_directory_raises(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    with pytest.raises(MediaProbeError):
        extract_first_frame(clips["cfr_mp4"], tmp_path / "absent" / "thumb.jpg")


def test_an_existing_destination_is_overwritten(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    destination = tmp_path / "thumb.jpg"
    _ = destination.write_bytes(b"stale contents")
    extract_first_frame(clips["cfr_mp4"], destination)
    assert destination.read_bytes()[:3] == b"\xff\xd8\xff"
