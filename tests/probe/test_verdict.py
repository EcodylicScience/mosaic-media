from dataclasses import replace

import pytest

from mosaic_media.probe.facts import MediaFacts
from mosaic_media.probe.ffprobe import TimingSource
from mosaic_media.probe.identity import IDENTITY_SCHEME
from mosaic_media.probe.policy import CHROME_149, DEFAULT_THRESHOLDS
from mosaic_media.probe.verdict import Verdict, derive

CLEAN = MediaFacts(
    container="mov,mp4,m4a,3gp,3g2,mj2",
    codec_name="h264",
    pixel_format="yuv420p",
    color_range="tv",
    color_primaries="bt709",
    color_transfer="bt709",
    width=1920,
    height=1080,
    rotation_degrees=0,
    square_pixels=True,
    progressive=True,
    has_audio=False,
    video_stream_count=1,
    duration=10.0,
    fps=25.0,
    frame_count=250,
    start_time=0.0,
    constant_frame_rate=True,
    max_instantaneous_fps=None,
    declared_duration=10.0,
    declared_fps=25.0,
    declared_frame_count=250,
    moov_at_start=True,
    max_keyframe_interval_frames=25,
    max_gop_bytes=100_000,
    discard_flagged_packets=0,
    leading_non_keyframe_frames=0,
    coded_reordering_depth=0,
    max_timestamp_gap_frame_periods=1.0,
    timing_source="presentation",
    video_uuid="6ba7b810-9dad-81d1-80b4-00c04fd430c8",
    content_digest="0123456789abcdef0123456789abcdef",
    identity_scheme=IDENTITY_SCHEME,
    prober_version="0.0.0-test Lavf0.0.0",
)


def verdict_for(**overrides: object) -> Verdict:
    return derive(replace(CLEAN, **overrides), CHROME_149, DEFAULT_THRESHOLDS)


def test_a_clean_file_needs_nothing() -> None:
    result = verdict_for()
    assert result.playable
    assert result.stream_transcode is None
    assert result.analysis_transcode is None
    assert result.stream_reasons == frozenset()
    assert result.analysis_reasons == frozenset()
    assert not result.truncated


def test_playable_is_exactly_not_stream_required() -> None:
    for overrides in (
        {"container": "avi"},
        {"codec_name": "mjpeg"},
        {"constant_frame_rate": False, "max_instantaneous_fps": 30.0},
        {"rotation_degrees": 90},
        {"square_pixels": False},
        {"start_time": 0.3},
    ):
        result = verdict_for(**overrides)
        assert result.stream_transcode == "required", overrides
        assert not result.playable, overrides


def test_h264_in_avi_is_unplayable_on_the_container_alone() -> None:
    # wtVSopn4...avi is h264 and Chrome cannot open an AVI container at all.
    result = verdict_for(container="avi")
    assert result.stream_reasons == frozenset({"unsupported_container"})
    assert result.analysis_transcode is None


def test_vp8_in_webm_is_playable() -> None:
    result = verdict_for(container="matroska,webm", codec_name="vp8")
    assert result.playable
    assert result.stream_transcode is None


def test_hevc_is_playable_but_never_less_than_recommended() -> None:
    result = verdict_for(codec_name="hevc")
    assert result.playable
    assert result.stream_transcode == "recommended"
    assert "client_dependent_decode" in result.stream_reasons


def test_a_non_baseline_pixel_format_is_client_dependent() -> None:
    result = verdict_for(pixel_format="yuv420p10le")
    assert result.playable
    assert "client_dependent_decode" in result.stream_reasons


def test_interlaced_is_soft_for_streaming_and_required_for_analysis() -> None:
    # Interlaced h264 plays in Chrome; it merely looks combed. Combing does not
    # break the coordinate space, so it is not a hard reason. It does corrupt
    # tracking, so the analysis derivative must deinterlace.
    result = verdict_for(progressive=False)
    assert result.playable
    assert result.stream_transcode == "recommended"
    assert result.analysis_transcode == "required"
    assert "interlaced" in result.analysis_reasons


def test_moov_at_end_is_soft() -> None:
    result = verdict_for(moov_at_start=False)
    assert result.playable
    assert result.stream_transcode == "recommended"
    assert result.stream_reasons == frozenset({"moov_not_at_start"})


def test_soft_reasons_are_still_reported_when_a_hard_reason_is_present() -> None:
    # Every reason is computed independently; a hard reason does not suppress a
    # soft one. The reason set selects the ffmpeg command, so a rotated file
    # whose moov also trails must still record both, even though the rotation
    # already forces a re-encode. Gating the soft checks on "no hard reason"
    # would silently drop moov_not_at_start here.
    result = verdict_for(rotation_degrees=90, moov_at_start=False)
    assert result.stream_transcode == "required"
    assert "rotated" in result.stream_reasons
    assert "moov_not_at_start" in result.stream_reasons


def test_large_seek_payload_fires_alone_on_a_high_bitrate_short_gop_file() -> None:
    # hex_3.mp4.bk: keyframes every 24 frames, 3.89 MiB per seek.
    result = verdict_for(max_keyframe_interval_frames=24, max_gop_bytes=4_079_000)
    assert result.stream_transcode == "recommended"
    assert result.stream_reasons == frozenset({"large_seek_payload"})


def test_the_reference_av1_encode_is_below_the_byte_threshold() -> None:
    # hex_3_av1.mp4: 0.29 MiB, keyframes every 50 frames at 50 fps.
    result = verdict_for(
        codec_name="av1",
        fps=50.0,
        max_keyframe_interval_frames=50,
        max_gop_bytes=304_236,
    )
    assert result.stream_transcode is None


def test_a_lying_header_at_unchanged_duration_is_a_metadata_problem() -> None:
    # Behavioral Despair...webm is constant-rate; avg_frame_rate reads 1000/1 and
    # OpenCV reports 1000 fps. Sourced from r_frame_rate this check never fires.
    result = verdict_for(declared_fps=1000.0, declared_frame_count=0)
    assert result.stream_transcode is None
    assert result.playable
    assert result.analysis_transcode == "required"
    assert result.analysis_reasons == frozenset({"unreliable_timing_metadata"})


def test_a_lying_frame_count_at_unchanged_duration_is_a_metadata_problem() -> None:
    # M-Movie0019.avi claims 195929 frames against a measured 23030.
    result = verdict_for(
        container="avi",
        codec_name="mjpeg",
        declared_frame_count=195_929,
        frame_count=23_030,
    )
    assert "unreliable_timing_metadata" in result.analysis_reasons
    assert not result.truncated


def test_a_vp8_style_dedup_gap_is_reported_as_unreliable_metadata() -> None:
    # 9603 packets, 9070 visible frames: the invisible alt-ref frames share a
    # timestamp with their successor. A consumer reading nb_frames is misled.
    result = verdict_for(frame_count=9070, declared_frame_count=9603)
    assert "unreliable_timing_metadata" in result.analysis_reasons
    assert result.playable


def test_a_source_whose_timing_was_invented_is_not_analysis_clean() -> None:
    # The demultiplexer manufactured these timestamps, so every value measured
    # from them describes the invention. Reporting the file analysis-ready is how
    # a file the reader refuses gets called clean.
    facts = replace(CLEAN, timing_source="synthesized")
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    assert "unreliable_timing_metadata" in verdict.analysis_reasons
    assert verdict.analysis_transcode == "required"


@pytest.mark.parametrize("timing_source", ["absent", "synthesized"])
def test_an_unsupplied_rate_never_fires_the_variable_rate_reason(
    timing_source: TimingSource,
) -> None:
    # constant_frame_rate is a placeholder on such a source, never a measurement.
    # Firing variable_frame_rate on it would select a re-encode where a
    # timestamp-generating remux is the fix.
    facts = replace(CLEAN, timing_source=timing_source, constant_frame_rate=False)
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    assert "variable_frame_rate" not in verdict.analysis_reasons
    assert "variable_frame_rate" not in verdict.stream_reasons


def test_a_truncated_file_is_flagged_and_is_not_a_metadata_problem() -> None:
    result = verdict_for(
        duration=5.04, declared_duration=10.0, declared_frame_count=250, frame_count=126
    )
    assert result.truncated
    assert "unreliable_timing_metadata" not in result.analysis_reasons


def test_a_start_time_inside_half_a_frame_period_is_tolerated() -> None:
    # Girrafe...mkv reports -0.007 s against a 0.033 s frame period.
    result = verdict_for(fps=30.0, start_time=-0.007)
    assert result.playable
    assert "non_zero_start_time" not in result.stream_reasons


def test_variable_frame_rate_is_required_for_both_targets() -> None:
    result = verdict_for(constant_frame_rate=False, max_instantaneous_fps=30.0)
    assert result.stream_transcode == "required"
    assert result.analysis_transcode == "required"


def test_rotation_and_non_square_pixels_are_required_for_both_targets() -> None:
    # Chrome reports a rotated 320x240 clip as 240x320 and renders it turned,
    # while ffprobe and OpenCV both report 320x240. Pose keypoints live in coded
    # coordinates: there must be exactly one coordinate space.
    for overrides in ({"rotation_degrees": 90}, {"square_pixels": False}):
        result = verdict_for(**overrides)
        assert result.stream_transcode == "required", overrides
        assert result.analysis_transcode == "required", overrides


def test_thresholds_are_injected_not_baked_in() -> None:
    from mosaic_media.probe.policy import Thresholds

    strict = Thresholds(max_gop_bytes=1)
    result = derive(CLEAN, CHROME_149, strict)
    assert result.stream_transcode == "recommended"


@pytest.mark.parametrize(
    ("container", "codec", "playable"),
    [
        ("mov,mp4,m4a,3gp,3g2,mj2", "h264", True),
        ("mov,mp4,m4a,3gp,3g2,mj2", "hevc", True),
        ("mov,mp4,m4a,3gp,3g2,mj2", "av1", True),
        ("mov,mp4,m4a,3gp,3g2,mj2", "mpeg4", False),
        ("mov,mp4,m4a,3gp,3g2,mj2", "mjpeg", False),
        ("mov,mp4,m4a,3gp,3g2,mj2", "prores", False),
        ("mov,mp4,m4a,3gp,3g2,mj2", "rpza", False),
        ("matroska,webm", "h264", True),
        ("matroska,webm", "vp8", True),
        ("matroska,webm", "vp9", True),
        ("matroska,webm", "hevc", True),
        ("avi", "h264", False),
        ("avi", "indeo5", False),
        ("avi", "msmpeg4v2", False),
        ("asf", "wmv3", False),
        ("asf", "wmv2", False),
        ("mpeg", "mpeg1video", False),
    ],
)
def test_the_container_by_codec_matrix(
    container: str, codec: str, playable: bool
) -> None:
    assert verdict_for(container=container, codec_name=codec).playable is playable


def test_a_codec_outside_the_trusted_set_needs_an_analysis_transcode() -> None:
    verdict = derive(
        replace(CLEAN, codec_name="indeo5"), CHROME_149, DEFAULT_THRESHOLDS
    )
    assert "unverified_frame_correspondence" in verdict.analysis_reasons
    assert verdict.analysis_transcode == "required"


def test_a_trusted_codec_needs_no_analysis_transcode() -> None:
    verdict = derive(replace(CLEAN, codec_name="h264"), CHROME_149, DEFAULT_THRESHOLDS)
    assert "unverified_frame_correspondence" not in verdict.analysis_reasons


def test_an_invented_timing_source_cannot_be_copy_remuxed() -> None:
    # A copy carries the packets forward, and the packet-to-picture mapping is
    # exactly what no measurement over packets can establish. Only a decode can.
    facts = replace(CLEAN, timing_source="synthesized")
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    assert "presentation_timing_requires_decode" in verdict.analysis_reasons
    assert "presentation_timing_requires_decode" in verdict.stream_reasons
    assert verdict.playable is False


@pytest.mark.parametrize("timing_source", ["decode", "synthesized", "absent"])
def test_reordering_without_presentation_timestamps_requires_a_decode(
    timing_source: TimingSource,
) -> None:
    # A decoder recovers presentation order from the bitstream's picture order.
    # A copy does not decode, so it labels the pictures in the order they arrive.
    #
    # The measured values are cleared alongside an unsupplied provenance, because
    # the probe sets them to placeholders whenever the file supplied no timing.
    # Facts mixing an unsupplied source with measured values model a state the
    # probe never mints, which this suite forbids elsewhere for the same reason.
    cleared = (
        {"fps": 0.0, "duration": 0.0, "constant_frame_rate": False}
        if timing_source == "absent"
        else {}
    )
    facts = replace(
        CLEAN, timing_source=timing_source, coded_reordering_depth=2, **cleared
    )
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    assert "presentation_timing_requires_decode" in verdict.analysis_reasons


def test_reordering_with_presentation_timestamps_is_left_alone() -> None:
    # The ordinary containerized case, which is most of the corpus. Real
    # presentation timestamps already carry the order, so firing here would
    # re-encode files that are correct.
    facts = replace(CLEAN, timing_source="presentation", coded_reordering_depth=2)
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    assert "presentation_timing_requires_decode" not in verdict.analysis_reasons
    assert "presentation_timing_requires_decode" not in verdict.stream_reasons
