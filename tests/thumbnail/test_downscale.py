"""Tests for the PNG -> JPEG thumbnail downscale helper."""

import subprocess
from pathlib import Path

import pytest

from mosaic_media.probe.errors import MediaProbeError
from mosaic_media.thumbnail import downscale_to_jpeg, thumbnail_dimensions


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


def test_thumbnail_dimensions_caps_long_edge() -> None:
    assert thumbnail_dimensions(1024, 570) == (320, 178)
    assert thumbnail_dimensions(570, 1024) == (178, 320)


def test_thumbnail_dimensions_never_upscales() -> None:
    assert thumbnail_dimensions(300, 200) == (300, 200)
    assert thumbnail_dimensions(320, 320) == (320, 320)


def test_thumbnail_dimensions_never_zero() -> None:
    assert thumbnail_dimensions(10000, 1) == (320, 1)


def test_downscale_produces_jpeg_with_exact_dimensions(tmp_path: Path) -> None:
    source = tmp_path / "frame.png"
    _make_png(source, width=640, height=360)
    destination = tmp_path / "frame.thumb.jpg"
    width, height = thumbnail_dimensions(640, 360)
    downscale_to_jpeg(source, destination, width=width, height=height)
    assert destination.exists()
    assert _probe_dimensions(destination) == (320, 180)


def test_downscale_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(MediaProbeError):
        downscale_to_jpeg(
            tmp_path / "absent.png",
            tmp_path / "absent.thumb.jpg",
            width=100,
            height=100,
        )
