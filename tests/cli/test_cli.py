"""CLI smoke tests via typer's CliRunner. Error messages are matched against the
combined output, which is version-robust across click's stderr handling."""

import json
from pathlib import Path

from typer.testing import CliRunner, Result

from mosaic_media.cli import app
from mosaic_media.probe.probe import probe_media

runner = CliRunner()


def combined(result: Result) -> str:
    """stdout and stderr together, tolerant of click's version differences.

    Typer vendors its own click fork and re-exports the testing surface, so
    `Result` comes from `typer.testing` rather than the (now optional,
    possibly absent) `click` package. `Result.output` is stdout-only and error
    text lands on `Result.stderr`; some click versions merge the streams and
    raise on `Result.stderr` instead. Reading both defensively matches error
    text on either.
    """
    text = result.output
    try:
        stderr = result.stderr
    except ValueError:
        stderr = ""
    return text + (stderr or "")


def test_probe_prints_parseable_json_with_expected_keys(clips: dict[str, Path]) -> None:
    result = runner.invoke(app, ["probe", str(clips["cfr_mp4"])])
    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert set(document) == {"facts", "verdict"}
    assert document["facts"]["codec_name"] == "h264"
    assert document["facts"]["constant_frame_rate"] is True
    assert isinstance(document["verdict"]["stream_reasons"], list)
    assert isinstance(document["verdict"]["analysis_reasons"], list)


def test_probe_of_a_missing_file_exits_nonzero_with_a_message() -> None:
    result = runner.invoke(app, ["probe", "/nonexistent/definitely_missing.mp4"])
    assert result.exit_code == 1
    assert "probe failed" in combined(result)


def test_transcode_of_a_clean_file_reports_a_no_op(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    destination = tmp_path / "out.mp4"
    result = runner.invoke(
        app,
        [
            "transcode",
            str(clips["faststart_mp4"]),
            "--target",
            "playback",
            "--output",
            str(destination),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "nothing to do" in result.output
    assert not destination.exists()


def test_transcode_happy_path_writes_the_derivative(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    # cfr_mp4 carries a tail moov, so the playback target performs the
    # faststart remux: the app must write the derivative and name the
    # operation, not just report no-ops and errors.
    destination = tmp_path / "out.mp4"
    result = runner.invoke(
        app,
        [
            "transcode",
            str(clips["cfr_mp4"]),
            "--target",
            "playback",
            "--output",
            str(destination),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "wrote" in result.output
    assert "remux_faststart" in result.output
    assert destination.exists()
    assert probe_media(destination).moov_at_start is True


def test_transcode_of_a_missing_file_exits_nonzero(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "transcode",
            "/nonexistent/missing.mp4",
            "--target",
            "analysis",
            "--output",
            str(tmp_path / "out.mp4"),
        ],
    )
    assert result.exit_code == 1
    assert "probe failed" in combined(result)


def test_transcode_requires_an_output_option(clips: dict[str, Path]) -> None:
    result = runner.invoke(
        app, ["transcode", str(clips["faststart_mp4"]), "--target", "playback"]
    )
    assert result.exit_code == 2
    assert "--output" in combined(result)
