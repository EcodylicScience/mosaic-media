"""Which formats supply their own timing, and which have it invented for them."""

from pathlib import Path

import pytest

from mosaic_media.probe.ffprobe import (
    INVENTED_TIMING_CONTAINERS,
    TimestampSource,
    TimingSource,
    timing_source_for,
    timing_supplied_by_source,
)
from mosaic_media.probe.policy import CHROME_149, DEFAULT_THRESHOLDS
from mosaic_media.probe.probe import probe_media
from mosaic_media.probe.verdict import derive
from tests.helpers.corpus import decode_md5s
from tests.helpers.media_fixtures import (
    requires_av1_frame_split,
    requires_svtav1,
)

# Verified by putting non-uniform timing through each format and reading it back:
# timing a file supplies survives, timing a demultiplexer invents is replaced by a
# uniform grid. The recipe is recorded beside INVENTED_TIMING_CONTAINERS in
# mosaic_media/probe/ffprobe.py, which is where it stays reachable.
CLASSIFICATIONS: list[tuple[TimestampSource, str, TimingSource]] = [
    ("pts", "mov,mp4,m4a,3gp,3g2,mj2", "presentation"),
    ("pts", "matroska,webm", "presentation"),
    ("pts", "mpegts", "presentation"),
    ("pts", "m4v", "presentation"),
    ("pts", "ivf", "presentation"),
    ("dts", "avi", "decode"),
    ("pts", "obu", "synthesized"),
    ("dts", "mpegvideo", "synthesized"),
    ("pts", "yuv4mpegpipe", "synthesized"),
    ("pts", "h263", "synthesized"),
    ("pts", "jpeg_pipe", "synthesized"),
    ("none", "h264", "absent"),
    ("none", "hevc", "absent"),
]


@pytest.mark.parametrize(("source", "container", "expected"), CLASSIFICATIONS)
def test_each_format_classifies_by_where_its_timing_came_from(
    source: TimestampSource, container: str, expected: TimingSource
) -> None:
    assert timing_source_for(source, container) == expected


def test_an_invented_timing_format_wins_over_its_timestamp_source() -> None:
    # MPEG-2 video reaches the probe through the decode-timestamp fallback, the
    # same path a genuine audio video interleave file takes. Reading the source
    # first would call its invented timing file-supplied.
    assert timing_source_for("dts", "mpegvideo") == "synthesized"
    assert timing_source_for("dts", "avi") == "decode"


@pytest.mark.parametrize(
    ("timing_source", "supplied"),
    [
        ("presentation", True),
        ("decode", True),
        ("synthesized", False),
        ("absent", False),
    ],
)
def test_only_file_supplied_timing_counts_as_supplied(
    timing_source: TimingSource, supplied: bool
) -> None:
    assert timing_supplied_by_source(timing_source) is supplied


@requires_svtav1
@requires_av1_frame_split
def test_a_bare_av1_stream_has_its_timing_invented(
    av1_split_clips: dict[str, Path],
) -> None:
    # The measured file behind the classification: a bare stream with no timestamp
    # layer, whose demultiplexer manufactures one per packet.
    assert probe_media(av1_split_clips["obu"]).timing_source == "synthesized"
    assert probe_media(av1_split_clips["matroska"]).timing_source == "presentation"


def test_every_invented_timing_format_classifies_as_synthesized(
    invented_timing_clips: dict[str, Path],
) -> None:
    # One fixture per format on the denylist, mechanically rather than by
    # listing them here: a format added to the list without a fixture is an
    # entry nothing measures, which is the failure the list's own comment warns
    # about. `obu` is excluded because its fixture is the bare AV1 stream, which
    # needs an encoder marker this test does not carry.
    assert set(invented_timing_clips) == INVENTED_TIMING_CONTAINERS - {"obu"}
    for name, path in invented_timing_clips.items():
        assert probe_media(path).timing_source == "synthesized", name


def test_an_h263_stream_is_measured_at_a_rate_the_source_never_had(
    invented_timing_clips: dict[str, Path],
) -> None:
    # The sharpest instance of why invented timing cannot be trusted even when it
    # looks uniform: the demultiplexer does not merely flatten the spacing, it
    # reports a rate the file never carried, and reports it as constant. A bound
    # rather than a value, because a differently built clip measures a different
    # wrong rate.
    facts = probe_media(invented_timing_clips["h263"])
    assert facts.fps > 29.0
    assert facts.constant_frame_rate


def test_an_mpeg2_elementary_stream_is_reordered_as_well_as_invented(
    invented_timing_clips: dict[str, Path],
) -> None:
    # A default encode holds one picture back, so this fixture carries both
    # halves of the next task's reason: invented timing and a positive reordering
    # depth. Asserted here so a change to either is caught where it is measured.
    facts = probe_media(invented_timing_clips["mpegvideo"])
    assert facts.timing_source == "synthesized"
    assert facts.coded_reordering_depth == 1


@requires_svtav1
def test_an_ordinary_bare_av1_stream_has_its_timing_invented(
    natural_obu_clip: Path,
) -> None:
    # The provenance defect on its own. One packet per picture, so the frame
    # count is right and nothing but the provenance is wrong -- which is what
    # makes this the fixture that isolates the change.
    facts = probe_media(natural_obu_clip)
    assert facts.timing_source == "synthesized"
    assert facts.frame_count == len(decode_md5s(natural_obu_clip))


def test_a_reordered_raw_stream_carries_no_timing_and_reorders(
    reordered_raw_h264_clip: Path,
) -> None:
    facts = probe_media(reordered_raw_h264_clip)
    assert facts.timing_source == "absent"
    assert facts.coded_reordering_depth == 2
    assert facts.declared_fps == pytest.approx(25.0)


def test_an_mpeg2_elementary_stream_is_routed_to_a_decode(
    invented_timing_clips: dict[str, Path],
) -> None:
    # Reason only, deliberately. This fixture already selects a re-encode on both
    # targets through `unsupported_codec` and `unverified_frame_correspondence`,
    # so an operation assertion here would pass whether or not this task landed.
    verdict = derive(
        probe_media(invented_timing_clips["mpegvideo"]), CHROME_149, DEFAULT_THRESHOLDS
    )
    assert "presentation_timing_requires_decode" in verdict.analysis_reasons
