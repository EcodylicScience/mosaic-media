"""Tests for the PNG -> JPEG thumbnail downscale helper."""

import subprocess
from pathlib import Path

import pytest

from mosaic_media.probe.errors import MediaProbeError
from mosaic_media.thumbnail import downscale_to_jpeg, thumbnail_dimensions

# Mirrors the `cap` default in `thumbnail_dimensions`. That default is part of
# the contract, so the tests pin it rather than reading it back off the function.
DEFAULT_CAP = 320


def _make_png(path: Path, *, width: int, height: int) -> None:
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c=red:s={width}x{height}",
        "-frames:v",
        "1",
        "-y",
        str(path),
    ]
    _ = subprocess.run(command, check=True, capture_output=True, timeout=30)


def _probe_dimensions(path: Path) -> tuple[int, int]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0",
        str(path),
    ]
    result = subprocess.run(
        command, check=True, capture_output=True, text=True, timeout=30
    )
    width_text, height_text = result.stdout.strip().split(",")
    return int(width_text), int(height_text)


def _assert_sizing_contract(
    width: int, height: int, result: tuple[int, int], *, cap: int
) -> None:
    """Assert what `thumbnail_dimensions` guarantees, whatever its rounding rule.

    Deliberately silent about how the short edge rounds: a rounding change must
    fail the one test that pins it and no other, or a failure cannot say which
    guarantee broke.

    Valid only for an input over `cap` whose short edge scales to at least one
    pixel and whose edges do not both round to the same value. Clamped and
    near-square inputs have their own tests; putting one here fails the ratio or
    orientation assertion for reasons that have nothing to do with a defect.
    """
    assert max(result) == cap
    assert min(result) >= 1
    assert (result[0] >= result[1]) == (width >= height)
    # A pixel of rounding on the short edge moves the long-to-short ratio by at
    # most cap / short_edge ** 2, so that is the whole tolerance the contract
    # allows. Both ratios are taken long-over-short, because the tolerance is
    # derived for that orientation and is far too loose for its reciprocal.
    tolerance = cap / min(result) ** 2
    scaled_ratio = max(result) / min(result)
    source_ratio = max(width, height) / min(width, height)
    assert abs(scaled_ratio - source_ratio) < tolerance


@pytest.mark.parametrize(("width", "height"), [(1024, 570), (570, 1024)])
def test_thumbnail_dimensions_caps_the_long_edge_in_either_orientation(
    width: int, height: int
) -> None:
    result = thumbnail_dimensions(width, height)
    _assert_sizing_contract(width, height, result, cap=DEFAULT_CAP)


def test_thumbnail_dimensions_honors_a_non_default_cap() -> None:
    width, height, cap = 1024, 570, 160
    result = thumbnail_dimensions(width, height, cap=cap)
    _assert_sizing_contract(width, height, result, cap=cap)


def test_thumbnail_dimensions_never_upscales() -> None:
    assert thumbnail_dimensions(300, 200) == (300, 200)
    assert thumbnail_dimensions(320, 320) == (320, 320)


def test_thumbnail_dimensions_never_zero() -> None:
    # An aspect ratio whose short edge scales to 0.032, well under the half pixel
    # that would round it away. The pair is asserted whole because the clamp
    # leaves nothing to rounding: max and min alone would accept a transposed
    # result and lose the orientation the original literal covered.
    assert thumbnail_dimensions(10000, 1) == (DEFAULT_CAP, 1)


def test_thumbnail_dimensions_rounds_the_short_edge_to_nearest() -> None:
    """Pin the current rounding rule, which the contract assertions leave open.

    Both directions are covered so a switch to either neighbor fails here: the
    first case rounds down where ceiling would round up, the second rounds up
    where flooring would round down.
    """
    # 570 * 320 / 1024 is 178.125.
    assert thumbnail_dimensions(1024, 570) == (DEFAULT_CAP, 178)
    # 572 * 320 / 1024 is 178.75.
    assert thumbnail_dimensions(1024, 572) == (DEFAULT_CAP, 179)
    # Portrait, so the short edge is pinned in both orientations rather than
    # resting on the contract assertions, whose tolerance spans several pixels.
    assert thumbnail_dimensions(570, 1024) == (178, DEFAULT_CAP)


def test_downscale_produces_jpeg_at_the_requested_dimensions(tmp_path: Path) -> None:
    source = tmp_path / "frame.png"
    _make_png(source, width=640, height=360)
    destination = tmp_path / "frame.thumb.jpg"
    width, height = thumbnail_dimensions(640, 360)
    downscale_to_jpeg(source, destination, width=width, height=height)
    assert destination.exists()
    assert _probe_dimensions(destination) == (width, height)


def test_downscale_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(MediaProbeError):
        downscale_to_jpeg(
            tmp_path / "absent.png",
            tmp_path / "absent.thumb.jpg",
            width=100,
            height=100,
        )
