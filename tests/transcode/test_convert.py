"""Converter acceptance: the transcoded output re-probes clean on both verdicts."""

import subprocess
from pathlib import Path

import numpy
import pytest

from mosaic_media.io.reader import VideoReader
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
    TranscodeCommand,
    TranscodeError,
    TranscodeProgress,
    TranscodeResult,
    build_command,
    run_transcode,
)
from mosaic_media.transcode import convert as convert_module
from tests.helpers.media_fixtures import build, requires_svtav1


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


def _declared_average_rate(path: Path) -> str:
    """The output's `avg_frame_rate` as ffprobe reports it, exactly."""
    argv = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=avg_frame_rate",
        "-of",
        "csv=p=0",
        str(path),
    ]
    completed = subprocess.run(argv, capture_output=True, text=True, check=True)
    return completed.stdout.strip()


def _decoded_times(path: Path) -> list[float]:
    """Presentation times of the decoded frames, in the decoder's output order.

    Deliberately unsorted, and read from frames rather than packets: a caller
    asserts that decode order already is presentation order, so a helper that
    sorted anywhere, or that read packet timestamps instead, would report a file
    whose pictures are mistimed as correct.
    """
    argv = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "frame=pts_time",
        "-of",
        "default=nw=1:nk=1",
        str(path),
    ]
    completed = subprocess.run(argv, capture_output=True, text=True, check=True)
    return [float(line) for line in completed.stdout.split()]


def _warnings_of(argv: tuple[str, ...]) -> str:
    """Run a built command at warning level and return what ffmpeg wrote.

    The built argv opens with the runner's own `-v error`, which hides the
    muxer's deprecation notice, so the level is replaced rather than prepended:
    ffmpeg takes the last occurrence, and a prepended flag would be overridden
    by the one already there. Replacing in place rather than rebuilding the
    leading flags keeps this independent of how many of them there are, and
    raises rather than misbehaving if `-v` ever stops being passed.
    """
    replaced = list(argv)
    replaced[replaced.index("-v") + 1] = "warning"
    completed = subprocess.run(replaced, capture_output=True, text=True, check=True)
    return completed.stderr


def _analysis_command(source: Path, destination: Path) -> TranscodeCommand:
    facts = probe_media(source)
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    command = build_command(
        verdict, facts, "analysis", source, destination, encoding=ANALYSIS_ENCODING
    )
    assert command is not None
    return command


def test_raw_remux_declares_the_source_rate_exactly(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    # Before the declared rate reached the command, the mp4 muxer synthesized
    # its own and landed on 1000000/33333, which is 30.0003 rather than 30.
    result = transcode(
        clips["raw_h264"], tmp_path / "out.mp4", "analysis", ANALYSIS_ENCODING
    )
    assert result.performed
    assert result.output_path is not None
    assert _declared_average_rate(result.output_path) == "30/1"


def test_raw_playback_rewrap_declares_the_source_rate_exactly(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    # A raw stream fires unsupported_container for playback, which selects
    # REMUX_CONTAINER rather than REMUX_TIMEBASE. That argv carried no timestamp
    # source at all, so its output declared an invented rate too.
    result = transcode(
        clips["raw_h264"], tmp_path / "out.mp4", "playback", PLAYBACK_ENCODING
    )
    assert result.performed
    assert result.operation is Operation.REMUX_CONTAINER
    assert result.output_path is not None
    assert _declared_average_rate(result.output_path) == "30/1"


def test_fractional_raw_remux_survives_the_float_round_trip(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    source = clips["raw_fractional_rate_h264"]
    assert probe_media(source).declared_fps == 30000 / 1001
    result = transcode(source, tmp_path / "out.mp4", "analysis", ANALYSIS_ENCODING)
    assert result.performed
    assert result.output_path is not None
    assert _declared_average_rate(result.output_path) == "30000/1001"


def test_raw_remux_leaves_no_unset_timestamps(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    command = _analysis_command(clips["raw_h264"], tmp_path / "out.mp4")
    assert "Timestamps are unset" not in _warnings_of(command.argv)


def test_the_unset_timestamp_check_can_fail(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    # Guard on the test above: strip the timestamp filter and the muxer's
    # deprecation notice must reappear, or that assertion proves nothing.
    command = _analysis_command(clips["raw_h264"], tmp_path / "out.mp4")
    assert "-bsf:v" in command.argv
    index = command.argv.index("-bsf:v")
    without = command.argv[:index] + command.argv[index + 2 :]
    assert "Timestamps are unset" in _warnings_of(without)


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


@requires_svtav1
def test_a_source_starting_on_non_keyframes_rewraps_to_a_zero_start_time(
    avi_starting_on_non_keyframes: Path, tmp_path: Path
) -> None:
    # Guard: the source's own start time is zero, so a non-zero one in the output
    # is introduced by the rewrap rather than carried in from the source.
    source_facts = probe_media(avi_starting_on_non_keyframes)
    assert source_facts.start_time == 0.0
    assert (
        "unsupported_container"
        in derive(source_facts, CHROME_149, DEFAULT_THRESHOLDS).stream_reasons
    )

    result = transcode(
        avi_starting_on_non_keyframes,
        tmp_path / "out.mp4",
        "playback",
        PLAYBACK_ENCODING,
    )
    assert result.performed
    assert result.operation is Operation.REENCODE_AV1
    assert result.output_facts is not None
    # Pinned as a value, not merely as "the transcode returned": the acceptance
    # probe rejects a non-zero start time, so a bare success assertion would pass
    # for an output whose clock happened to land inside the threshold.
    assert result.output_facts.start_time == 0.0
    assert result.output_verdict is not None
    assert "non_zero_start_time" not in result.output_verdict.stream_reasons


@requires_svtav1
def test_a_vp8_source_transcodes_for_analysis(
    lying_header_vp8_webm: Path, tmp_path: Path
) -> None:
    # mp4 cannot carry vp8, so the copy remux the timing reason selects dies in
    # the muxer and the source can never be prepared for analysis. The operation
    # escalates to a re-encode, and the output must clear the reason it ran for.
    source = lying_header_vp8_webm
    source_verdict = derive(probe_media(source), CHROME_149, DEFAULT_THRESHOLDS)
    assert "unreliable_timing_metadata" in source_verdict.analysis_reasons

    result = transcode(source, tmp_path / "out.mp4", "analysis", ANALYSIS_ENCODING)
    assert result.performed
    assert result.operation is Operation.REENCODE_AV1
    assert result.output_path is not None
    assert result.output_path.is_file()
    assert result.output_verdict is not None
    assert result.output_verdict.analysis_transcode is None


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
    assert source_facts.timing_source == "absent"
    result = transcode(source, tmp_path / "out.mp4", "analysis", ANALYSIS_ENCODING)
    assert result.performed
    assert result.operation is Operation.REMUX_TIMEBASE
    assert result.output_facts is not None
    assert result.output_facts.timing_source == "presentation"
    assert result.output_facts.constant_frame_rate is True
    assert result.output_facts.frame_count == source_facts.frame_count
    assert result.output_verdict is not None
    assert result.output_verdict.analysis_transcode is None


@requires_svtav1
def test_an_untimed_source_cut_mid_stream_re_encodes_and_keeps_every_frame(
    raw_starting_on_non_keyframes: Path, tmp_path: Path
) -> None:
    # The remux above is a stream copy, and a copy drops the frames ahead of the
    # first keyframe. Measured on this source while its leading count read 0:
    # the analysis derivative came back with 25 frames of the source's 49, down
    # the same copy path the deliverability escalation exists to prevent. The
    # escalation only ever fired for a timed source, because the count it reads
    # was structurally 0 for every untimed one.
    source_facts = probe_media(raw_starting_on_non_keyframes)
    assert source_facts.timing_source == "absent"
    assert source_facts.leading_non_keyframe_frames == 24
    result = transcode(
        raw_starting_on_non_keyframes,
        tmp_path / "out.mp4",
        "analysis",
        ANALYSIS_ENCODING,
    )
    assert result.performed
    assert result.operation is Operation.REENCODE_AV1
    assert result.output_facts is not None
    assert result.output_facts.frame_count == source_facts.frame_count == 49
    # The derivative is what analysis reads, so it must carry no leading frames
    # of its own and need no further transcode.
    assert result.output_facts.leading_non_keyframe_frames == 0
    assert result.output_verdict is not None
    assert result.output_verdict.analysis_transcode is None


def test_the_reader_delivers_an_untimed_mid_stream_cut_without_a_transcode(
    raw_starting_on_non_keyframes: Path,
) -> None:
    # The counting change moves the transcode decision, not the reader: the
    # reader emits the leading frames because it sets the decoder flag when a
    # segment begins at the start of the stream, which it derives structurally
    # rather than from this count.
    facts = probe_media(raw_starting_on_non_keyframes)
    with VideoReader(raw_starting_on_non_keyframes, facts=facts) as reader:
        delivered = sum(1 for _index, _frame in reader)
    assert delivered == facts.frame_count == 49


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
    # 0.0 and no completion fraction is knowable: the fraction is computed
    # against the source duration, and an unmeasurable one leaves nothing to
    # divide by. The remux does set the output's timestamps, which is why the
    # absent fraction is attributable to the source duration alone. The run
    # still reports: updates arrive for a caller to drive an indeterminate
    # display, and not one of them invents a fraction from the unknown duration.
    # Which raw readings accompany them is not asserted, because it is a
    # property of the ffmpeg build rather than of this package. The determinate
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


@requires_svtav1
def test_a_reencode_materializes_every_frame_a_cut_source_carries(
    avi_starting_on_non_keyframes: Path, tmp_path: Path
) -> None:
    source_facts = probe_media(avi_starting_on_non_keyframes)
    result = transcode(
        avi_starting_on_non_keyframes,
        tmp_path / "out.mp4",
        "playback",
        PLAYBACK_ENCODING,
    )
    assert result.operation is Operation.REENCODE_AV1
    assert result.output_facts is not None
    assert result.output_facts.start_time == 0.0
    assert result.output_path is not None
    with VideoReader(result.output_path, facts=result.output_facts) as reader:
        frames = [frame for _index, frame in reader]
    # Delivery is asserted against the derivative's own facts: a constant-rate
    # resample is not a frame-for-frame copy, and those facts are authoritative
    # for it.
    assert len(frames) == result.output_facts.frame_count
    assert len(frames) >= source_facts.frame_count
    # Counts alone cannot tell a materialized frame from a duplicate. The
    # re-encode resamples to a constant rate, which fills the timeline whether
    # or not the source's leading pictures survived the decode -- dropping them
    # yields the full frame count with the gap padded by repeats of the first
    # keyframe.
    #
    # Compared as content rather than as digests. Under the padding every frame
    # in this window is the same picture, and digests still differ wherever the
    # lossy encode fails to reproduce it bit-exactly, which makes equal bytes a
    # measure of encoder determinism rather than of repetition. Consecutive mean
    # absolute difference over this fixture: at most 0.002 when the window is
    # padded, at least 2.2 when the pictures are materialized, because the
    # source generator changes every frame. The floor sits between them, two
    # orders of magnitude above the padding and a factor of four below the
    # content, so neither an encoder that dithers the repeats nor one that
    # compresses the real frames harder moves the verdict.
    leading_window = source_facts.leading_non_keyframe_frames + 1
    for position in range(leading_window - 1):
        earlier = frames[position].astype(numpy.int32)
        later = frames[position + 1].astype(numpy.int32)
        difference = float(numpy.mean(numpy.abs(earlier - later)))
        message = f"frames {position} and {position + 1} repeat: {difference}"
        assert difference > 0.5, message


@requires_svtav1
def test_a_reordered_raw_stream_comes_back_in_presentation_order(
    reordered_raw_h264_clip: Path, tmp_path: Path
) -> None:
    # The defect this reason exists for, measured end to end. A copy remux of
    # this source writes timestamps from the packet index, which is decode order,
    # so the fourth picture carries a lower timestamp than the second. The
    # acceptance re-probe cannot see it, because the timestamps are uniform and
    # complete either way -- only their assignment to pictures is wrong.
    result = transcode(
        reordered_raw_h264_clip, tmp_path / "out.mp4", "analysis", ANALYSIS_ENCODING
    )
    assert result.output_path is not None
    times = _decoded_times(result.output_path)
    assert times == sorted(times)
