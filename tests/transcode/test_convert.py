"""Converter acceptance: the transcoded output re-probes clean on both verdicts."""

import subprocess
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
    TranscodeProgress,
    TranscodeResult,
    run_transcode,
)
from mosaic_media.transcode import convert as convert_module
from tests.helpers.media_fixtures import build

# libsvtav1 is a system-ffmpeg build option; skip the re-encode acceptance tests
# with an actionable message when it is absent. The copy-remux tests do not need it.
requires_svtav1 = pytest.mark.skipif(
    not hwaccel.encoder_available("libsvtav1"),
    reason=(
        "libsvtav1 encoder missing from system ffmpeg; install an ffmpeg built "
        "with --enable-libsvtav1 to run the AV1 re-encode acceptance tests"
    ),
)


def _audio_codec(path: Path) -> str:
    """The output's first audio stream codec, straight from ffprobe."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return result.stdout.strip()


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


def test_the_no_op_branch_records_the_source_video_uuid(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    # The no-op branch returns every optional field as None, but this one is not
    # optional: it describes the input, and the input facts are in hand here.
    source = clips["faststart_mp4"]
    result = transcode(source, tmp_path / "out.mp4", "analysis", ANALYSIS_ENCODING)
    assert result.performed is False
    assert result.source_video_uuid == probe_media(source).video_uuid
    assert result.source_video_uuid != ""


@requires_svtav1
def test_a_performed_transcode_records_the_source_video_uuid(
    corpus_vfr: Path, tmp_path: Path
) -> None:
    # The derivative's own facts are freshly probed and share nothing with the
    # source's, which is exactly why the edge has to be carried rather than
    # recomputed.
    source_uuid = probe_media(corpus_vfr).video_uuid
    result = transcode(corpus_vfr, tmp_path / "out.mp4", "analysis", ANALYSIS_ENCODING)
    assert result.performed is True
    assert result.source_video_uuid == source_uuid
    assert result.output_facts is not None
    assert result.output_facts.video_uuid != source_uuid


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


def test_it_refuses_a_non_mp4_file_destination(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    # The temp file's .mp4 suffix always selects the mp4 muxer, so a destination
    # naming any other container would misdescribe the bytes actually written.
    source = clips["cfr_mp4"]
    destination = tmp_path / "out.webm"
    with pytest.raises(TranscodeError, match="always produces an mp4 container"):
        _ = transcode(source, destination, "playback", PLAYBACK_ENCODING)
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


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


def test_rerunning_a_transcode_replaces_the_existing_output(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    # "Re-running the same transcode replaces its output atomically": seed the
    # destination with bytes that are not a video; the rerun must leave a
    # clean derivative and no temporary file, never the stale bytes.
    source = clips["cfr_mp4"]
    destination = tmp_path / "out.mp4"
    first = transcode(source, destination, "playback", PLAYBACK_ENCODING)
    assert first.performed
    _ = destination.write_bytes(b"stale bytes that are not a video")
    second = transcode(source, destination, "playback", PLAYBACK_ENCODING)
    assert second.performed
    assert second.output_path == destination.absolute()
    assert probe_media(destination).moov_at_start is True
    assert [entry.name for entry in tmp_path.iterdir()] == ["out.mp4"]


def test_playback_remux_preserves_the_audio_track(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    # audio_mp4 carries an aac track and a tail moov: the playback fix is the
    # faststart remux, and the audio stream must survive it end to end.
    source = clips["audio_mp4"]
    assert probe_media(source).has_audio is True
    result = transcode(source, tmp_path / "out.mp4", "playback", PLAYBACK_ENCODING)
    assert result.performed
    assert result.operation is Operation.REMUX_FASTSTART
    assert result.output_facts is not None
    assert result.output_facts.has_audio is True
    assert result.output_path is not None
    assert _audio_codec(result.output_path) == "aac"


@requires_svtav1
def test_reencode_carries_the_audio_track_as_aac(
    clips: dict[str, Path],
    tmp_path_factory: pytest.TempPathFactory,
    tmp_path: Path,
) -> None:
    # A rotated source with audio forces the AV1 re-encode path with
    # keep_audio: the audio must come out the other side as aac, proving the
    # -c:a argv end to end rather than by construction.
    root = tmp_path_factory.mktemp("rotated_audio")
    source = build(
        root / "rotated_audio.mp4",
        "-c",
        "copy",
        source=["-display_rotation", "90", "-i", str(clips["audio_mp4"])],
    )
    facts = probe_media(source)
    assert facts.rotation_degrees == 90
    assert facts.has_audio is True
    result = transcode(source, tmp_path / "out.mp4", "playback", PLAYBACK_ENCODING)
    assert result.performed
    assert result.operation is Operation.REENCODE_AV1
    assert result.output_facts is not None
    assert result.output_facts.has_audio is True
    assert result.output_path is not None
    assert _audio_codec(result.output_path) == "aac"


def test_raw_h264_remuxes_into_measured_timing(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    # A raw elementary stream fires unreliable_timing_metadata (timing is
    # absent, not variable), selecting the timestamp-generating remux. The
    # acceptance re-probe must then measure real, constant timing.
    source = clips["raw_h264"]
    source_facts = probe_media(source)
    assert source_facts.timing_measured is False
    result = transcode(source, tmp_path / "out.mp4", "analysis", ANALYSIS_ENCODING)
    assert result.performed
    assert result.operation is Operation.REMUX_TIMEBASE
    assert result.output_facts is not None
    assert result.output_facts.timing_measured is True
    assert result.output_facts.constant_frame_rate is True
    assert result.output_facts.frame_count == source_facts.frame_count
    assert result.output_verdict is not None
    assert result.output_verdict.analysis_transcode is None


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


@requires_svtav1
def test_transcode_progress_reports_a_monotonic_fraction(
    slow_reencode_source: Path, tmp_path: Path
) -> None:
    # A real AV1 re-encode of a source with a known duration drives on_progress
    # with a completion fraction that only ever climbs. The slow re-encode streams
    # several progress blocks before the last.
    facts = probe_media(slow_reencode_source)
    assert facts.duration > 0
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    updates: list[TranscodeProgress] = []
    result = run_transcode(
        slow_reencode_source,
        tmp_path / "out.mp4",
        "analysis",
        facts,
        verdict,
        profile=CHROME_149,
        thresholds=DEFAULT_THRESHOLDS,
        encoding=ANALYSIS_ENCODING,
        on_progress=updates.append,
    )
    assert result.performed
    assert result.operation is Operation.REENCODE_AV1
    fractions = [update.fraction for update in updates if update.fraction is not None]
    assert len(fractions) >= 2
    assert all(fraction >= 0.0 for fraction in fractions)
    assert fractions == sorted(fractions)


def test_transcode_progress_is_indeterminate_for_a_timestampless_source(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    # A raw elementary stream carries no timestamps, so it probes a duration of
    # 0.0 and no completion fraction is knowable. The run still reports: updates
    # arrive for a caller to drive an indeterminate display, and not one of them
    # invents a fraction from the unknown duration. Which raw readings accompany
    # them is not asserted, because it is a property of the ffmpeg build rather
    # than of this package: ffmpeg reports as N/A every reading it cannot
    # compute, and a copy remux of packets that reach the muxer without
    # timestamps is the case where it computes none of them. The determinate
    # half of the contract -- a known duration does yield a fraction, driven by
    # the out_time reading -- is pinned by the monotonic-fraction test above.
    source = clips["raw_h264"]
    facts = probe_media(source)
    assert facts.duration == 0.0
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    updates: list[TranscodeProgress] = []
    result = run_transcode(
        source,
        tmp_path / "out.mp4",
        "analysis",
        facts,
        verdict,
        profile=CHROME_149,
        thresholds=DEFAULT_THRESHOLDS,
        encoding=ANALYSIS_ENCODING,
        on_progress=updates.append,
    )
    assert result.performed
    assert result.operation is Operation.REMUX_TIMEBASE
    assert updates
    assert all(update.fraction is None for update in updates)


class _CancelAfterFirstProgress:
    """A cancel token that trips once the encode has reported any progress.

    Waiting for the first progress block proves the encoder is genuinely running
    when the cancel is requested, rather than canceling before it starts.
    """

    def __init__(self) -> None:
        self.saw_progress: bool = False

    def on_progress(self, _update: TranscodeProgress) -> None:
        self.saw_progress = True

    def cancel_check(self) -> bool:
        return self.saw_progress


@requires_svtav1
def test_a_canceled_transcode_raises_and_leaves_no_output(
    slow_reencode_source: Path, tmp_path: Path
) -> None:
    # A cancel token that trips mid-encode stops the child and raises a
    # TranscodeError that names the run canceled, not failed. The temporary
    # output is cleaned up on the raise, so nothing is left behind.
    facts = probe_media(slow_reencode_source)
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    canceler = _CancelAfterFirstProgress()
    with pytest.raises(TranscodeError, match="canceled"):
        _ = run_transcode(
            slow_reencode_source,
            tmp_path / "out.mp4",
            "analysis",
            facts,
            verdict,
            profile=CHROME_149,
            thresholds=DEFAULT_THRESHOLDS,
            encoding=ANALYSIS_ENCODING,
            on_progress=canceler.on_progress,
            cancel_check=canceler.cancel_check,
        )
    assert list(tmp_path.iterdir()) == []
