"""Generated media fixtures. Every clip is built by the ffmpeg on PATH, which is
a documented system dependency, so a missing encoder is a failure, not a skip."""

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

SOURCE = ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2"]


def build(destination: Path, *arguments: str, source: list[str] | None = None) -> Path:
    command = ["ffmpeg", "-hide_banner", "-v", "error", "-y"]
    command.extend(source if source is not None else SOURCE)
    command.extend(arguments)
    command.append(str(destination))
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        message = (
            f"fixture build failed for {destination.name}: {result.stderr.strip()}"
        )
        raise RuntimeError(message)
    return destination


@pytest.fixture(scope="session")
def clips(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("media_probe_clips")
    made: dict[str, Path] = {}
    made["cfr_mp4"] = build(
        root / "cfr.mp4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "25"
    )
    made["cfr_30fps_mp4"] = build(
        root / "cfr_30fps.mp4",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "30",
        source=["-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2"],
    )
    made["faststart_mp4"] = build(
        root / "faststart.mp4",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "25",
        "-movflags",
        "+faststart",
    )
    made["vp8_webm"] = build(root / "vp8.webm", "-c:v", "libvpx", "-pix_fmt", "yuv420p")
    made["mjpeg_avi"] = build(
        root / "mjpeg.avi", "-c:v", "mjpeg", "-pix_fmt", "yuvj420p"
    )
    # Remuxing into AVI drops the presentation timestamps: every packet reports
    # `pts_time=N/A` and only `dts_time` survives. This is the file the DTS
    # fallback exists for. An AVI encoded directly from a filter source keeps its
    # PTS, so it cannot exercise the fallback and must not be used to claim it.
    made["no_pts_avi"] = build(
        root / "no_pts.avi", "-c", "copy", source=["-i", str(made["cfr_mp4"])]
    )
    made["anamorphic_mp4"] = build(
        root / "anamorphic.mp4",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-vf",
        "setsar=10/11",
    )
    # `-c copy` is load-bearing. It writes side_data_list[].rotation and leaves the
    # coded dimensions at 320x240. Re-encoding instead bakes the rotation into the
    # pixels, yielding a 240x320 file with no side data and nothing to detect.
    made["rotated_mp4"] = build(
        root / "rotated.mp4",
        "-c",
        "copy",
        source=["-display_rotation", "90", "-i", str(made["cfr_mp4"])],
    )
    made["audio_mp4"] = build(
        root / "audio.mp4",
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        source=[*SOURCE, "-f", "lavfi", "-i", "sine=frequency=440:duration=2"],
    )
    made["no_video"] = build(
        root / "audio_only.m4a",
        "-c:a",
        "aac",
        source=["-f", "lavfi", "-i", "sine=frequency=440:duration=2"],
    )
    # A raw H.264 elementary stream: no container, no packet timestamps. 60
    # frames at a nominal 30 fps; -bf 0 keeps a later -c copy remux free of
    # B-frame reordering trouble, matching how tracking boxes record.
    made["raw_h264"] = build(
        root / "raw.h264",
        "-c:v",
        "libx264",
        "-bf",
        "0",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "12",
        "-f",
        "h264",
        source=["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=2"],
    )
    return made


@pytest.fixture(scope="session")
def truncated_faststart(
    clips: dict[str, Path], tmp_path_factory: pytest.TempPathFactory
) -> Path:
    source = clips["faststart_mp4"]
    destination = tmp_path_factory.mktemp("truncated") / "truncated.mp4"
    payload = source.read_bytes()
    _ = destination.write_bytes(payload[: len(payload) * 55 // 100])
    return destination


@pytest.fixture(scope="session")
def long_gop_clip(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("long_gop")
    yield build(
        root / "long_gop.mp4",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "250",
        source=["-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=12"],
    )
