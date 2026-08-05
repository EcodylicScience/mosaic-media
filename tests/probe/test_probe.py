from pathlib import Path

import pytest

from mosaic_media.probe.errors import MediaProbeError
from mosaic_media.probe.probe import probe_media


def test_probe_assembles_header_timing_gop_and_boxes(clips: dict[str, Path]) -> None:
    facts = probe_media(clips["cfr_mp4"])
    assert facts.codec_name == "h264"
    assert facts.constant_frame_rate
    assert facts.frame_count == 50
    assert facts.fps == pytest.approx(25.0, rel=0.02)
    assert facts.moov_at_start is False
    assert facts.max_gop_bytes > 0
    assert facts.video_stream_count == 1


def test_probe_reports_moov_at_start_for_a_faststart_file(
    clips: dict[str, Path],
) -> None:
    assert probe_media(clips["faststart_mp4"]).moov_at_start is True


def test_probe_reports_none_for_moov_on_a_matroska_file(clips: dict[str, Path]) -> None:
    assert probe_media(clips["vp8_webm"]).moov_at_start is None


def test_probe_reads_rotation(clips: dict[str, Path]) -> None:
    facts = probe_media(clips["rotated_mp4"])
    assert facts.rotation_degrees == 90
    assert (facts.width, facts.height) == (320, 240)


def test_probe_of_a_file_with_no_video_stream_raises(clips: dict[str, Path]) -> None:
    with pytest.raises(MediaProbeError, match="no video stream"):
        _ = probe_media(clips["no_video"])


def test_probe_of_a_truncated_file_measures_a_short_duration(
    truncated_faststart: Path,
) -> None:
    facts = probe_media(truncated_faststart)
    assert facts.duration < facts.declared_duration * 0.95


def test_a_long_gop_file_exceeds_the_frames_guard(long_gop_clip: Path) -> None:
    assert probe_media(long_gop_clip).max_keyframe_interval_frames > 200


def test_a_source_opening_on_a_keyframe_counts_no_undeliverable_packets(
    clips: dict[str, Path],
) -> None:
    facts = probe_media(clips["cfr_mp4"])
    assert facts.discard_flagged_packets == 0
    assert facts.leading_non_keyframe_frames == 0


def test_a_source_cut_mid_stream_counts_its_leading_frames(
    avi_starting_on_non_keyframes: Path,
) -> None:
    # The fixture drops the committed clip's leading keyframe, leaving 24
    # non-keyframes ahead of the keyframe at 25.
    facts = probe_media(avi_starting_on_non_keyframes)
    assert facts.leading_non_keyframe_frames == 24
    assert facts.discard_flagged_packets == 0


def test_an_untimed_source_cut_mid_stream_counts_its_leading_frames(
    raw_starting_on_non_keyframes: Path,
) -> None:
    # Every packet of a raw elementary stream carries the same placeholder
    # timestamp, so counting distinct timestamps below the first keyframe time
    # returns 0 for every file of the class however many frames precede it.
    # Packet order carries the signal instead. The same cut as the AVI above,
    # so the same 24.
    facts = probe_media(raw_starting_on_non_keyframes)
    assert facts.timing_source == "absent"
    assert facts.leading_non_keyframe_frames == 24
    assert facts.frame_count == 49


def test_an_untimed_source_opening_on_a_keyframe_still_counts_none(
    clips: dict[str, Path],
) -> None:
    # The counting order changed for this whole class, so the committed raw
    # streams are what say the change is confined to the shape it was made for.
    for name in ("raw_h264", "raw_fractional_rate_h264"):
        facts = probe_media(clips[name])
        assert facts.timing_source == "absent", name
        assert facts.leading_non_keyframe_frames == 0, name


def test_the_coded_reordering_depth_separates_reordered_bitstreams(
    clips: dict[str, Path], open_gop_clip: Path
) -> None:
    # The bitstream's own reordering depth, which decides whether a stream copy
    # can recover presentation order. A raw stream carries no timestamps, so a
    # copy synthesizes them from the packet index -- decode order -- and that is
    # only presentation order when nothing is reordered.
    assert probe_media(clips["raw_h264"]).coded_reordering_depth == 0
    assert probe_media(clips["raw_fractional_rate_h264"]).coded_reordering_depth == 0
    assert probe_media(open_gop_clip).coded_reordering_depth == 2
