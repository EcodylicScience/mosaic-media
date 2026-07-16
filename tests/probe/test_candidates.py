from pathlib import Path

from mosaic_media import is_candidate_video


def test_uppercase_extension_is_a_candidate() -> None:
    assert is_candidate_video(Path("DMS10248.MP4"))


def test_double_extension_uses_the_last_suffix() -> None:
    assert not is_candidate_video(Path("hex_3.mp4.bk"))


def test_non_video_extension_is_not_a_candidate() -> None:
    assert not is_candidate_video(Path("index.csv"))


def test_every_extension_the_intake_accepts_is_a_candidate() -> None:
    # Mirrors VIDEO_EXTENSIONS in mosaic_app/src/uploads/store/grouping.ts.
    intake = [
        "mp4",
        "m4v",
        "mov",
        "avi",
        "mkv",
        "webm",
        "mts",
        "m2ts",
        "mpg",
        "mpeg",
        "wmv",
    ]
    for extension in intake:
        assert is_candidate_video(Path(f"clip.{extension}"))
