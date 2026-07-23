import json
from pathlib import Path
from typing import get_args

from typer.testing import CliRunner

from mosaic_media.cli import app
from mosaic_media.cli.application import COMPARE_EXIT_CODES
from mosaic_media.probe.identity import DuplicateVerdict, timing_tolerance
from mosaic_media.probe.probe import probe_media
from tests.cli.support import combined

runner = CliRunner()


def test_compare_exits_zero_on_a_duplicate(rewrites: dict[str, Path]) -> None:
    result = runner.invoke(
        app, ["compare", str(rewrites["origin"]), str(rewrites["faststart"])]
    )
    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["verdict"] == "duplicate"


def test_compare_exits_three_on_distinct(clips: dict[str, Path]) -> None:
    result = runner.invoke(
        app, ["compare", str(clips["cfr_mp4"]), str(clips["vp8_webm"])]
    )
    assert result.exit_code == 3, result.output
    assert json.loads(result.output)["verdict"] == "distinct"


def test_compare_exits_four_on_different_timing(rewrites: dict[str, Path]) -> None:
    result = runner.invoke(
        app, ["compare", str(rewrites["origin"]), str(rewrites["retimed"])]
    )
    assert result.exit_code == 4, result.output
    assert json.loads(result.output)["verdict"] == "different_timing"


def test_compare_exits_five_on_timing_unknown(clips: dict[str, Path]) -> None:
    result = runner.invoke(
        app, ["compare", str(clips["raw_h264"]), str(clips["raw_h264"])]
    )
    assert result.exit_code == 5, result.output
    assert json.loads(result.output)["verdict"] == "timing_unknown"


def test_compare_exits_one_when_the_right_file_fails_to_probe(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    missing = tmp_path / "not_a_video.mp4"
    _ = missing.write_bytes(b"not a video")
    result = runner.invoke(app, ["compare", str(clips["cfr_mp4"]), str(missing)])
    assert result.exit_code == 1
    assert "probe failed" in combined(result)


def test_compare_exits_one_when_the_left_file_fails_to_probe(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    missing = tmp_path / "not_a_video.mp4"
    _ = missing.write_bytes(b"not a video")
    result = runner.invoke(app, ["compare", str(missing), str(clips["cfr_mp4"])])
    assert result.exit_code == 1
    assert "probe failed" in combined(result)


def test_compare_honors_an_explicit_fps_tolerance(rewrites: dict[str, Path]) -> None:
    duplicate = runner.invoke(
        app, ["compare", str(rewrites["origin"]), str(rewrites["matroska"])]
    )
    assert duplicate.exit_code == 0, duplicate.output
    strict = runner.invoke(
        app,
        [
            "compare",
            str(rewrites["origin"]),
            str(rewrites["matroska"]),
            "--fps-tolerance",
            "1e-12",
        ],
    )
    assert strict.exit_code == 4, strict.output
    assert json.loads(strict.output)["fps_tolerance"] == 1e-12


def test_compare_honors_an_explicit_duration_tolerance(
    rewrites: dict[str, Path],
) -> None:
    duplicate = runner.invoke(
        app, ["compare", str(rewrites["origin"]), str(rewrites["matroska"])]
    )
    assert duplicate.exit_code == 0, duplicate.output
    strict = runner.invoke(
        app,
        [
            "compare",
            str(rewrites["origin"]),
            str(rewrites["matroska"]),
            "--duration-tolerance",
            "1e-12",
        ],
    )
    assert strict.exit_code == 4, strict.output
    assert json.loads(strict.output)["duration_tolerance"] == 1e-12


def test_compare_derives_tolerances_from_the_left_file(
    rewrites: dict[str, Path],
) -> None:
    origin_facts = probe_media(rewrites["origin"])
    retimed_facts = probe_media(rewrites["retimed"])

    forward = runner.invoke(
        app, ["compare", str(rewrites["origin"]), str(rewrites["retimed"])]
    )
    assert forward.exit_code == 4, forward.output
    expected_forward_tolerance = timing_tolerance(
        origin_facts.fps, origin_facts.duration
    )
    assert json.loads(forward.output)["fps_tolerance"] == expected_forward_tolerance

    reversed_order = runner.invoke(
        app, ["compare", str(rewrites["retimed"]), str(rewrites["origin"])]
    )
    assert reversed_order.exit_code == 4, reversed_order.output
    expected_reversed_tolerance = timing_tolerance(
        retimed_facts.fps, retimed_facts.duration
    )
    assert (
        json.loads(reversed_order.output)["fps_tolerance"]
        == expected_reversed_tolerance
    )


def test_every_verdict_has_a_distinct_exit_code_outside_the_reserved_ones() -> None:
    codes = COMPARE_EXIT_CODES
    assert set(get_args(DuplicateVerdict)) == set(codes)
    assert len(set(codes.values())) == len(codes)
    # 1 is the probe-failure code the other commands use and 2 is click's
    # usage-error code, so a verdict on either would be ambiguous.
    assert 1 not in codes.values()
    assert 2 not in codes.values()
