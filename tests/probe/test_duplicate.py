import dataclasses
from pathlib import Path

from mosaic_media import compare_for_duplicate, timing_tolerance
from mosaic_media.probe.probe import probe_media


def test_a_container_rewrite_reports_duplicate(rewrites: dict[str, Path]) -> None:
    left = probe_media(rewrites["origin"])
    for name in ("faststart", "repack", "tagged", "matroska", "roundtrip"):
        result = compare_for_duplicate(left, probe_media(rewrites[name]))
        assert result.verdict == "duplicate", name


def test_a_retime_reports_different_timing(rewrites: dict[str, Path]) -> None:
    left = probe_media(rewrites["origin"])
    result = compare_for_duplicate(left, probe_media(rewrites["retimed"]))
    assert result.verdict == "different_timing"
    assert result.fps_delta is not None
    assert result.fps_tolerance is not None


def test_an_unrelated_file_reports_distinct(clips: dict[str, Path]) -> None:
    result = compare_for_duplicate(
        probe_media(clips["cfr_mp4"]), probe_media(clips["vp8_webm"])
    )
    assert result.verdict == "distinct"
    assert result.fps_delta is None
    assert result.duration_delta is None
    assert result.fps_tolerance is None
    assert result.duration_tolerance is None


def test_an_untimed_side_reports_timing_unknown(clips: dict[str, Path]) -> None:
    # A raw elementary stream has fps and duration as 0.0 placeholders, so a
    # tolerance test on them is meaningless. The guard must fire before the
    # division by duration, which would otherwise be a division by zero.
    untimed = probe_media(clips["raw_h264"])
    result = compare_for_duplicate(untimed, untimed)
    assert result.verdict == "timing_unknown"
    assert result.fps_delta is None
    assert result.duration_delta is None


def test_a_zero_duration_timed_side_reports_timing_unknown(
    clips: dict[str, Path],
) -> None:
    # The timing guard has two halves: timing the file did not supply, and a
    # non-positive duration. This pins the second on its own, stating the
    # provenance explicitly so nothing but the duration can fire the guard.
    #
    # The hazard the second half once covered is gone. `timing_source` is a
    # required `Literal` with no default, so facts can no longer claim supplied
    # timing by leaving the field out. The duration clause stays because the
    # function is total over every `MediaFacts` a caller can construct, and
    # supplied timing with a zero duration is one of them -- without the clause
    # the tolerance derivation divides by zero.
    probed = probe_media(clips["cfr_mp4"])
    zero_duration = dataclasses.replace(
        probed, timing_source="presentation", duration=0.0
    )
    assert zero_duration.content_digest != ""
    result = compare_for_duplicate(zero_duration, zero_duration)
    assert result.verdict == "timing_unknown"
    assert result.fps_delta is None
    assert result.duration_delta is None


def test_two_files_whose_timing_was_invented_compare_as_timing_unknown(
    clips: dict[str, Path],
) -> None:
    # The comparison needs timing that measures the file. Timing a demultiplexer
    # manufactured describes the invention, so two such files can agree on it
    # while being different recordings.
    probed = probe_media(clips["cfr_mp4"])
    left = dataclasses.replace(probed, timing_source="synthesized")
    right = dataclasses.replace(probed, timing_source="synthesized")
    assert compare_for_duplicate(left, right).verdict == "timing_unknown"


def test_a_distinct_digest_outranks_an_untimed_side(clips: dict[str, Path]) -> None:
    # The digest check runs before the timing guard, and the order is reachable:
    # two provably different files report "distinct" rather than the far less
    # actionable "timing_unknown" the untimed side would otherwise produce.
    result = compare_for_duplicate(
        probe_media(clips["cfr_mp4"]), probe_media(clips["raw_h264"])
    )
    assert result.verdict == "distinct"


def test_an_explicit_tolerance_flips_the_verdict(rewrites: dict[str, Path]) -> None:
    left = probe_media(rewrites["origin"])
    right = probe_media(rewrites["matroska"])
    assert compare_for_duplicate(left, right).verdict == "duplicate"
    strict = compare_for_duplicate(left, right, fps_tolerance=1e-12)
    assert strict.verdict == "different_timing"
    assert strict.fps_tolerance == 1e-12
    # Zero is a tolerance, not a request for the derived default. A falsy
    # override demands exactness; treating it as absent is the classic defect in
    # this shape of parameter.
    exact = compare_for_duplicate(left, right, fps_tolerance=0.0)
    assert exact.verdict == "different_timing"
    assert exact.fps_tolerance == 0.0
    # The comparison is inclusive: a delta exactly equal to its tolerance is
    # still a duplicate.
    assert strict.fps_delta is not None
    boundary = compare_for_duplicate(left, right, fps_tolerance=strict.fps_delta)
    assert boundary.verdict == "duplicate"


def test_an_explicit_duration_tolerance_flips_the_verdict(
    rewrites: dict[str, Path],
) -> None:
    # The duration half of the comparison needs its own override. Exercising
    # only the fps one leaves the duration term unpinned, and a comparison that
    # ignored duration outright would still look correct.
    left = probe_media(rewrites["origin"])
    right = probe_media(rewrites["matroska"])
    assert compare_for_duplicate(left, right).verdict == "duplicate"
    strict = compare_for_duplicate(left, right, duration_tolerance=1e-12)
    assert strict.verdict == "different_timing"
    assert strict.duration_tolerance == 1e-12


def test_facts_without_a_digest_report_unminted_not_duplicate(
    clips: dict[str, Path],
) -> None:
    # The regression the empty-digest guard exists for. Two unrelated
    # recordings, each with no minted digest but matching timing -- the shape of
    # every fact row persisted before these fields existed, and of facts
    # hand-built for a source the probe never saw. Without that guard both
    # compare equal on the empty digest, reach the tolerance comparison, and
    # come back "duplicate".
    probed = probe_media(clips["cfr_mp4"])
    left = dataclasses.replace(probed, content_digest="", video_uuid="")
    right = dataclasses.replace(
        probed, content_digest="", video_uuid="", width=1920, height=1080
    )
    assert left.timing_source == "presentation"
    assert right.timing_source == "presentation"
    assert left.fps == right.fps
    assert left.duration == right.duration
    result = compare_for_duplicate(left, right)
    assert result.verdict == "unminted"
    assert result.fps_delta is None
    assert result.duration_delta is None


def test_one_side_without_a_digest_reports_unminted(clips: dict[str, Path]) -> None:
    # Asymmetric case: a freshly probed file compared against a stored row that
    # predates the fields. Order must not matter.
    probed = probe_media(clips["cfr_mp4"])
    stale = dataclasses.replace(probed, content_digest="", video_uuid="")
    assert compare_for_duplicate(probed, stale).verdict == "unminted"
    assert compare_for_duplicate(stale, probed).verdict == "unminted"


def test_a_duplicate_carries_its_deltas_and_applied_tolerances(
    rewrites: dict[str, Path],
) -> None:
    # Deltas are carried on both compared verdicts, not only the failing one, so
    # a caller can report how close a duplicate was.
    left = probe_media(rewrites["origin"])
    result = compare_for_duplicate(left, probe_media(rewrites["matroska"]))
    assert result.verdict == "duplicate"
    assert result.fps_delta is not None
    assert result.duration_delta is not None
    # Omitting a tolerance must use the derived value, not some other default.
    assert result.fps_tolerance == timing_tolerance(left.fps, left.duration)
    assert result.duration_tolerance == timing_tolerance(left.duration, left.duration)


def test_timing_tolerance_scales_inversely_with_duration() -> None:
    short = timing_tolerance(30.0, 1.0)
    long = timing_tolerance(30.0, 60.0)
    assert short == 60.0 * long
    # Pins the calibration itself, not only its shape. Every ratio above
    # survives a tenfold drift in either constant; this figure does not.
    assert short == 0.12
    # Comparing duration against itself yields the flat four-quantum figure.
    assert timing_tolerance(7.5, 7.5) == timing_tolerance(100.0, 100.0)
