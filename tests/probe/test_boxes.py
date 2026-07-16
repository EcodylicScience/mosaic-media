from pathlib import Path

from mosaic_media.probe.boxes import moov_at_start


def test_faststart_file_reports_true(clips: dict[str, Path]) -> None:
    assert moov_at_start(clips["faststart_mp4"]) is True


def test_moov_at_end_reports_false(clips: dict[str, Path]) -> None:
    # ffmpeg's mp4 muxer writes moov after mdat unless +faststart is given.
    assert moov_at_start(clips["cfr_mp4"]) is False


def test_a_non_isobmff_container_reports_none(clips: dict[str, Path]) -> None:
    assert moov_at_start(clips["vp8_webm"]) is None
    assert moov_at_start(clips["mjpeg_avi"]) is None


def test_a_truncated_file_does_not_raise(truncated_faststart: Path) -> None:
    assert moov_at_start(truncated_faststart) is True
