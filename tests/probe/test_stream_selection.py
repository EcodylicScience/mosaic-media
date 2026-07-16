"""Which video stream a file's packets are read from.

No generated fixture can exercise this: the mp4 muxer relocates an attached
picture to the end of the file, and Matroska does not mark a cover image with
the `attached_pic` disposition at all. The selection is pure, so it is pinned
here directly.
"""

from mosaic_media.probe.ffprobe import select_video_stream


def video(**overrides: object) -> dict[str, object]:
    stream: dict[str, object] = {"codec_type": "video", "codec_name": "h264"}
    stream.update(overrides)
    return stream


def cover_art() -> dict[str, object]:
    return video(codec_name="png", disposition={"attached_pic": 1})


def audio() -> dict[str, object]:
    return {"codec_type": "audio", "codec_name": "aac"}


def test_a_lone_video_stream_is_selected_at_position_zero() -> None:
    selected = select_video_stream([video()])
    assert selected is not None
    assert selected.video_position == 0
    assert selected.video_stream_count == 1


def test_cover_art_ahead_of_the_recording_shifts_the_position() -> None:
    # `ffprobe -select_streams v:N` counts every video stream, cover art
    # included. Addressing v:0 here would scan the still image.
    selected = select_video_stream([cover_art(), video()])
    assert selected is not None
    assert selected.stream["codec_name"] == "h264"
    assert selected.video_position == 1
    assert selected.video_stream_count == 1


def test_cover_art_after_the_recording_leaves_the_position_at_zero() -> None:
    selected = select_video_stream([video(), cover_art()])
    assert selected is not None
    assert selected.video_position == 0
    assert selected.video_stream_count == 1


def test_audio_streams_do_not_shift_the_position() -> None:
    selected = select_video_stream([audio(), video(), audio()])
    assert selected is not None
    assert selected.video_position == 0
    assert selected.video_stream_count == 1


def test_two_real_video_streams_are_counted_so_the_caller_can_reject_them() -> None:
    # Which one is the video has no defined answer, and a Matroska cover image
    # arrives here as a second real stream because it carries no attached_pic
    # disposition.
    selected = select_video_stream([video(), video(codec_name="hevc")])
    assert selected is not None
    assert selected.video_stream_count == 2


def test_a_file_with_only_cover_art_has_no_video_stream() -> None:
    assert select_video_stream([cover_art(), audio()]) is None


def test_a_file_with_no_video_stream_returns_none() -> None:
    assert select_video_stream([audio()]) is None


def test_non_object_entries_are_ignored() -> None:
    selected = select_video_stream(["not a stream", 7, video()])
    assert selected is not None
    assert selected.video_position == 0
