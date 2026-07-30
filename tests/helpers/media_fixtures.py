"""Media fixtures, built by the ffmpeg on PATH or copied from `tests/assets/`.

ffmpeg is a documented system dependency, so a missing encoder is a failure,
not a skip. The encoders used here are the ones an LGPL build carries: libvpx,
mjpeg, aac, and the native `-c copy` remuxes.

H.264 clips are copied from `tests/assets/` rather than encoded. FFmpeg has no
native H.264 encoder -- libx264 is external and GPL-2.0-or-later -- and this
suite must run against the same LGPL build the consumers deploy. Decoding is
unaffected, so a committed clip costs nothing to consume. See
`tests/assets/README.md` for provenance and `tests/test_encoder_guard.py` for
the rule.

Copies land in the tmp root because tests remux and truncate from these clips;
the committed originals are never handed out directly.
"""

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import pytest

SOURCE = ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2"]
ASSETS = Path(__file__).parent.parent / "assets"

AssetName = Literal[
    "anamorphic.mp4",
    "audio.mp4",
    "cfr.mp4",
    "cfr_30fps.mp4",
    "faststart.mp4",
    "h264.avi",
    "h264_gop12.mp4",
    "long_gop.mp4",
    "raw.h264",
    "raw25.h264",
]


def asset(name: AssetName, destination: Path) -> Path:
    """Copy a committed clip to `destination`, which the caller may then mutate."""
    source = ASSETS / name
    if not source.is_file():
        message = f"missing committed asset {source}; see tests/assets/README.md"
        raise RuntimeError(message)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copyfile(source, destination)
    return destination


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
    made["cfr_mp4"] = asset("cfr.mp4", root / "cfr.mp4")
    made["cfr_30fps_mp4"] = asset("cfr_30fps.mp4", root / "cfr_30fps.mp4")
    made["faststart_mp4"] = asset("faststart.mp4", root / "faststart.mp4")
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
    # Pins the seven-column side-data behavior the packet scan's row guard
    # tolerates: MPEG-TS appends an empty side-data column after data_hash.
    made["mpegts_ts"] = build(
        root / "cfr.ts", "-c", "copy", source=["-i", str(made["cfr_mp4"])]
    )
    made["anamorphic_mp4"] = asset("anamorphic.mp4", root / "anamorphic.mp4")
    # `-c copy` is load-bearing. It writes side_data_list[].rotation and leaves the
    # coded dimensions at 320x240. Re-encoding instead bakes the rotation into the
    # pixels, yielding a 240x320 file with no side data and nothing to detect.
    made["rotated_mp4"] = build(
        root / "rotated.mp4",
        "-c",
        "copy",
        source=["-display_rotation", "90", "-i", str(made["cfr_mp4"])],
    )
    # Committed with its own coded video rather than muxed from cfr.mp4: a copied
    # video track would carry cfr.mp4's identity values, and the identity tests
    # require every fixture with genuinely distinct content to hash distinctly.
    made["audio_mp4"] = asset("audio.mp4", root / "audio.mp4")
    made["no_video"] = build(
        root / "audio_only.m4a",
        "-c:a",
        "aac",
        source=["-f", "lavfi", "-i", "sine=frequency=440:duration=2"],
    )
    # A raw H.264 elementary stream: no container, no packet timestamps. 60
    # frames at a nominal 30 fps; -bf 0 keeps a later -c copy remux free of
    # B-frame reordering trouble, matching how tracking boxes record.
    made["raw_h264"] = asset("raw.h264", root / "raw.h264")
    return made


@pytest.fixture(scope="session")
def rewrites(
    clips: dict[str, Path], tmp_path_factory: pytest.TempPathFactory
) -> dict[str, Path]:
    """The same coded content rewritten every way the identity values care about.

    `origin` is the reference. `faststart`, `repack`, and `tagged` are
    same-container rewrites that leave packet timestamps byte-identical.
    `matroska` and `roundtrip` change the container, which quantizes timestamps
    to milliseconds; the round trip inherits that quantization rather than
    undoing it. `transport` additionally reframes the elementary stream to
    Annex B, which changes the coded bytes themselves.

    The 30 fps source is load-bearing, not incidental. A 25 fps frame period is
    exactly 40 ms, so Matroska's millisecond timebase requantizes nothing and
    every timestamp survives a repack byte-identical. On a 25 fps origin the
    test that the content digest survives a container change therefore goes
    vacuous -- it cannot fail no matter how broken the implementation is -- and
    the test that the uuid moves on a container change fails against a correct
    implementation, because the uuid genuinely does not move. 30 fps gives a
    33.333 ms period, which does requantize, and both tests then mean what they
    say.
    """
    root = tmp_path_factory.mktemp("rewrites")
    origin = clips["cfr_30fps_mp4"]
    made: dict[str, Path] = {"origin": origin}
    made["faststart"] = build(
        root / "faststart.mp4",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        source=["-i", str(origin)],
    )
    made["repack"] = build(
        root / "repack.mp4", "-c", "copy", source=["-i", str(origin)]
    )
    made["tagged"] = build(
        root / "tagged.mp4",
        "-c",
        "copy",
        "-metadata",
        "title=changed",
        source=["-i", str(origin)],
    )
    made["matroska"] = build(
        root / "repack.mkv", "-c", "copy", source=["-i", str(origin)]
    )
    made["roundtrip"] = build(
        root / "roundtrip.mp4",
        "-c",
        "copy",
        source=["-i", str(made["matroska"])],
    )
    made["transport"] = build(
        root / "repack.ts", "-c", "copy", source=["-i", str(origin)]
    )
    # -itsscale rewrites presentation timestamps without touching a coded byte:
    # the same pictures at a different rate. This is the retime case.
    made["retimed"] = build(
        root / "retimed.mp4",
        "-c",
        "copy",
        source=["-itsscale", "1.25", "-i", str(origin)],
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
def h264_gop12_clip(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """48 frames, 30 fps, keyframe every 12 -- the shape of `corpus_gop12`, but
    H.264 rather than AV1.

    Only the OpenCV parity tests use it. OpenCV's bundled FFmpeg cannot
    software-decode AV1, so it cannot read the AV1 corpus at all; a codec both
    decoders handle is what makes that comparison possible. Committed rather
    than encoded for the reason every H.264 asset here is.
    """
    root = tmp_path_factory.mktemp("h264_gop12")
    yield asset("h264_gop12.mp4", root / "h264_gop12.mp4")


@pytest.fixture(scope="session")
def long_gop_clip(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("long_gop")
    yield asset("long_gop.mp4", root / "long_gop.mp4")
