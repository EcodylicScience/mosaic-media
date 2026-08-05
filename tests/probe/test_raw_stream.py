"""A raw elementary stream probes with honest, unmeasured timing.

No container means no packet timestamps: fps and duration are unmeasurable
placeholders, the frame count is the packet count, and the analysis verdict
routes the file to a timestamp-generating remux -- never to a re-encode, which
would need a measured rate to resample to.
"""

from pathlib import Path

import pytest

from mosaic_media.probe.candidates import is_candidate_video
from mosaic_media.probe.policy import CHROME_149, DEFAULT_THRESHOLDS
from mosaic_media.probe.probe import probe_media
from mosaic_media.probe.verdict import derive
from tests.helpers.media_fixtures import asset


def test_raw_h264_probes_with_unmeasured_timing(clips: dict[str, Path]) -> None:
    facts = probe_media(clips["raw_h264"])
    assert facts.timing_measured is False
    assert facts.container == "h264"
    assert facts.codec_name == "h264"
    assert facts.frame_count == 60
    assert facts.fps == 0.0
    assert facts.duration == 0.0
    assert facts.constant_frame_rate is False
    assert facts.max_instantaneous_fps is None


def test_raw_stream_declares_the_rate_its_bitstream_states(
    clips: dict[str, Path],
) -> None:
    # raw.h264 is 30 fps content. The h264 demuxer's avg_frame_rate answers 25
    # for every raw stream regardless of content, so 25 here means the demuxer
    # default survived.
    facts = probe_media(clips["raw_h264"])
    assert facts.timing_measured is False
    assert facts.declared_fps == 30.0


def test_a_raw_hevc_stream_carries_the_rate_its_bitstream_states(
    raw_hevc_clip: Path,
) -> None:
    # End to end through the header read, not only the payload helper. Without
    # this the widened derivation is pinned only against a synthetic dictionary.
    facts = probe_media(raw_hevc_clip)
    assert facts.declared_fps == pytest.approx(25.0)


def test_a_raw_hevc_stream_stating_no_rate_carries_none(tmp_path: Path) -> None:
    # The other end, on real media rather than a hand-built payload: a bitstream
    # with no timing block makes the demultiplexer report its own time base,
    # which is not a frame rate at all. Measured at 1200000/1, so the
    # plausibility ceiling rejects it even at one tick per frame -- which is what
    # keeps a divisor of one from turning an absent rate into an enormous one.
    path = asset("raw_no_declared_rate.hevc", tmp_path / "rate_less.hevc")
    facts = probe_media(path)
    assert facts.declared_fps == 0.0


def test_container_stream_still_declares_its_average_rate(
    clips: dict[str, Path],
) -> None:
    facts = probe_media(clips["cfr_mp4"])
    assert facts.timing_measured is True
    assert facts.declared_fps == 25.0


def test_raw_h264_analysis_verdict_selects_the_remux_not_a_reencode(
    clips: dict[str, Path],
) -> None:
    facts = probe_media(clips["raw_h264"])
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    assert "unreliable_timing_metadata" in verdict.analysis_reasons
    assert "variable_frame_rate" not in verdict.analysis_reasons
    assert verdict.analysis_transcode == "required"
    assert "unsupported_container" in verdict.stream_reasons
    assert verdict.stream_transcode == "required"


def test_raw_h264_is_a_candidate_video() -> None:
    assert is_candidate_video(Path("recording.h264"))
    assert is_candidate_video(Path("recording.H264"))
