"""Converter acceptance: the transcoded output re-probes clean on both verdicts."""

from pathlib import Path

import pytest

from mosaic_media import hwaccel
from mosaic_media.probe.errors import MediaProbeError
from mosaic_media.probe.facts import MediaFacts
from mosaic_media.probe.policy import CHROME_149, DEFAULT_THRESHOLDS
from mosaic_media.probe.probe import probe_media
from mosaic_media.probe.verdict import Verdict, derive
from mosaic_media.transcode import (
    ANALYSIS_ENCODING,
    PLAYBACK_ENCODING,
    EncodingParameters,
    Operation,
    Target,
    TranscodeError,
    TranscodeResult,
    run_transcode,
)
from mosaic_media.transcode import convert as convert_module

# libsvtav1 is a system-ffmpeg build option; skip the re-encode acceptance tests
# with an actionable message when it is absent. The copy-remux tests do not need it.
requires_svtav1 = pytest.mark.skipif(
    not hwaccel.encoder_available("libsvtav1"),
    reason=(
        "libsvtav1 encoder missing from system ffmpeg; install an ffmpeg built "
        "with --enable-libsvtav1 to run the AV1 re-encode acceptance tests"
    ),
)


def transcode(
    source: Path, output: Path, target: Target, encoding: EncodingParameters
) -> TranscodeResult:
    facts = probe_media(source)
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    return run_transcode(
        source,
        output,
        target,
        facts,
        verdict,
        profile=CHROME_149,
        thresholds=DEFAULT_THRESHOLDS,
        encoding=encoding,
    )


@requires_svtav1
def test_variable_frame_rate_source_reencodes_to_constant_rate(
    variable_frame_rate_mp4: Path, tmp_path: Path
) -> None:
    # Guard the fixture: if generation ever stops producing VFR, fail loudly
    # rather than pass a vacuous no-op.
    assert probe_media(variable_frame_rate_mp4).constant_frame_rate is False
    result = transcode(
        variable_frame_rate_mp4, tmp_path / "out.mp4", "analysis", ANALYSIS_ENCODING
    )
    assert result.performed
    assert result.operation is Operation.REENCODE_AV1
    assert result.output_path is not None
    assert result.output_path.exists()
    assert result.output_facts is not None
    assert result.output_facts.constant_frame_rate is True
    assert result.output_verdict is not None
    assert "variable_frame_rate" not in result.output_verdict.analysis_reasons
    assert result.output_verdict.analysis_transcode is None


def test_tail_moov_source_remuxes_to_faststart(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    source = clips["cfr_mp4"]
    assert probe_media(source).moov_at_start is False
    result = transcode(source, tmp_path / "out.mp4", "playback", PLAYBACK_ENCODING)
    assert result.performed
    assert result.operation is Operation.REMUX_FASTSTART
    assert result.output_facts is not None
    assert result.output_facts.moov_at_start is True
    assert result.output_verdict is not None
    assert result.output_verdict.stream_transcode is None
    assert result.residual_recommended is False


@requires_svtav1
def test_rotated_source_bakes_rotation_in_an_av1_reencode(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    source = clips["rotated_mp4"]
    assert probe_media(source).rotation_degrees == 90
    result = transcode(source, tmp_path / "out.mp4", "playback", PLAYBACK_ENCODING)
    assert result.performed
    assert result.operation is Operation.REENCODE_AV1
    assert result.output_facts is not None
    assert result.output_facts.rotation_degrees == 0
    assert result.output_verdict is not None
    assert result.output_verdict.stream_transcode is None
    assert result.residual_recommended is False


def test_a_clean_playback_source_is_a_no_op(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    destination = tmp_path / "out.mp4"
    result = transcode(
        clips["faststart_mp4"], destination, "playback", PLAYBACK_ENCODING
    )
    assert result.performed is False
    assert result.output_path is None
    assert not destination.exists()


def test_a_clean_analysis_source_is_a_no_op(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    destination = tmp_path / "out.mp4"
    result = transcode(
        clips["faststart_mp4"], destination, "analysis", ANALYSIS_ENCODING
    )
    assert result.performed is False
    assert result.output_path is None
    assert not destination.exists()


def test_output_directory_derives_a_filename_from_the_source_stem(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    source = clips["cfr_mp4"]
    result = transcode(source, tmp_path, "playback", PLAYBACK_ENCODING)
    assert result.performed
    assert result.output_path is not None
    assert result.output_path == (tmp_path / f"{source.stem}.mp4").absolute()
    assert result.output_path.exists()


def test_an_explicit_file_output_is_written_verbatim(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    source = clips["cfr_mp4"]
    destination = tmp_path / "chosen_name.mp4"
    result = transcode(source, destination, "playback", PLAYBACK_ENCODING)
    assert result.output_path == destination.absolute()
    assert destination.exists()


def test_it_refuses_to_overwrite_the_source(clips: dict[str, Path]) -> None:
    source = clips["cfr_mp4"]
    with pytest.raises(TranscodeError, match="refusing to overwrite"):
        _ = transcode(source, source, "playback", PLAYBACK_ENCODING)


def test_lying_header_source_remuxes_with_a_corrected_timebase(
    lying_header_mkv: Path, tmp_path: Path
) -> None:
    # Guard: the fixture must actually trip the timing-metadata lie, or the test
    # proves nothing about the remux.
    source_verdict = derive(
        probe_media(lying_header_mkv), CHROME_149, DEFAULT_THRESHOLDS
    )
    assert "unreliable_timing_metadata" in source_verdict.analysis_reasons
    result = transcode(
        lying_header_mkv, tmp_path / "out.mp4", "analysis", ANALYSIS_ENCODING
    )
    assert result.performed
    assert result.operation is Operation.REMUX_TIMEBASE
    assert result.output_verdict is not None
    assert "unreliable_timing_metadata" not in result.output_verdict.analysis_reasons
    assert result.output_verdict.analysis_transcode is None


def test_h264_in_avi_rewraps_into_a_supported_container(
    h264_in_avi: Path, tmp_path: Path
) -> None:
    # Guard: h264-in-avi must fire exactly the container reason.
    source_verdict = derive(probe_media(h264_in_avi), CHROME_149, DEFAULT_THRESHOLDS)
    assert "unsupported_container" in source_verdict.stream_reasons
    result = transcode(h264_in_avi, tmp_path / "out.mp4", "playback", PLAYBACK_ENCODING)
    assert result.performed
    assert result.operation is Operation.REMUX_CONTAINER
    assert result.output_facts is not None
    assert result.output_facts.container == "mov,mp4,m4a,3gp,3g2,mj2"
    assert result.output_facts.codec_name == "h264"
    assert result.output_verdict is not None
    assert result.output_verdict.stream_transcode is None


def test_a_still_red_playback_output_is_a_terminal_failure(
    clips: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force the output re-probe to report an unplayable verdict; the gate must
    # raise and leave no output behind (a retry is never the answer).
    def still_required(
        _facts: MediaFacts, _profile: object, _thresholds: object
    ) -> Verdict:
        return Verdict(
            playable=False,
            stream_transcode="required",
            analysis_transcode=None,
            stream_reasons=frozenset({"unsupported_codec"}),
            analysis_reasons=frozenset(),
            truncated=False,
        )

    monkeypatch.setattr(convert_module, "derive", still_required)
    source = clips["cfr_mp4"]
    facts = probe_media(source)
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    destination = tmp_path / "out.mp4"
    with pytest.raises(TranscodeError, match="still cannot play"):
        _ = run_transcode(
            source,
            destination,
            "playback",
            facts,
            verdict,
            profile=CHROME_149,
            thresholds=DEFAULT_THRESHOLDS,
            encoding=PLAYBACK_ENCODING,
        )
    assert list(tmp_path.iterdir()) == []


def test_a_still_red_analysis_output_is_a_terminal_failure(
    lying_header_mkv: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def still_required(
        _facts: MediaFacts, _profile: object, _thresholds: object
    ) -> Verdict:
        return Verdict(
            playable=True,
            stream_transcode=None,
            analysis_transcode="required",
            stream_reasons=frozenset(),
            analysis_reasons=frozenset({"variable_frame_rate"}),
            truncated=False,
        )

    monkeypatch.setattr(convert_module, "derive", still_required)
    facts = probe_media(lying_header_mkv)
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    with pytest.raises(TranscodeError, match="still needs an analysis transcode"):
        _ = run_transcode(
            lying_header_mkv,
            tmp_path / "out.mp4",
            "analysis",
            facts,
            verdict,
            profile=CHROME_149,
            thresholds=DEFAULT_THRESHOLDS,
            encoding=ANALYSIS_ENCODING,
        )
    assert list(tmp_path.iterdir()) == []


def test_an_unprobeable_output_is_a_terminal_failure(
    clips: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force the output re-probe to raise MediaProbeError, as it would when ffmpeg
    # writes bytes the prober cannot read. `run_transcode` promises TranscodeError
    # for every terminal failure, so this one must be translated, not propagated
    # raw, and the temporary output must still be cleaned up.
    source = clips["cfr_mp4"]

    def unprobeable_output(path: Path, _thresholds: object) -> MediaFacts:
        if path == source:
            return probe_media(path)
        message = f"simulated unreadable output at {path}"
        raise MediaProbeError(message)

    monkeypatch.setattr(convert_module, "probe_media", unprobeable_output)
    facts = probe_media(source)
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    with pytest.raises(TranscodeError, match="could not be probed"):
        _ = run_transcode(
            source,
            tmp_path / "out.mp4",
            "playback",
            facts,
            verdict,
            profile=CHROME_149,
            thresholds=DEFAULT_THRESHOLDS,
            encoding=PLAYBACK_ENCODING,
        )
    assert list(tmp_path.iterdir()) == []


def test_a_residual_recommended_playback_output_is_surfaced_not_failed(
    clips: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Output that plays but still trips a soft reason is not a failure; the result
    # records it so a caller can report the transcode was optional.
    def still_recommended(
        _facts: MediaFacts, _profile: object, _thresholds: object
    ) -> Verdict:
        return Verdict(
            playable=True,
            stream_transcode="recommended",
            analysis_transcode=None,
            stream_reasons=frozenset({"moov_not_at_start"}),
            analysis_reasons=frozenset(),
            truncated=False,
        )

    monkeypatch.setattr(convert_module, "derive", still_recommended)
    source = clips["cfr_mp4"]
    facts = probe_media(source)
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    result = run_transcode(
        source,
        tmp_path / "out.mp4",
        "playback",
        facts,
        verdict,
        profile=CHROME_149,
        thresholds=DEFAULT_THRESHOLDS,
        encoding=PLAYBACK_ENCODING,
    )
    assert result.performed
    assert result.residual_recommended is True
    assert result.output_path is not None
    assert result.output_path.exists()
