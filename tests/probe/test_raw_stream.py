"""A raw H.264 elementary stream probes with honest, unmeasured timing.

No container means no packet timestamps: fps and duration are unmeasurable
placeholders, the frame count is the packet count, and the analysis verdict
routes the file to a timestamp-generating remux -- never to a re-encode, which
would need a measured rate to resample to.
"""

from pathlib import Path

from mosaic_media.probe.candidates import is_candidate_video
from mosaic_media.probe.policy import CHROME_149, DEFAULT_THRESHOLDS
from mosaic_media.probe.probe import probe_media
from mosaic_media.probe.verdict import derive


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
