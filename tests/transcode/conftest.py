"""Defect fixtures for the transcode acceptance tests.

The tail-moov and rotated corpora reuse the shared `clips` fixture (`cfr_mp4`,
`rotated_mp4`). Three defect files the shared corpus lacks are generated here: a
genuinely variable-frame-rate clip, a lying-timing-header clip, and an
h264-in-avi clip (a supported codec in an unopenable container).
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.helpers.media_fixtures import build


@pytest.fixture(scope="session")
def variable_frame_rate_mp4(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("vfr")
    # A single encode whose presentation timestamps switch period midway: the
    # first 50 frames are spaced 0.02 s and the remaining 50 are spaced 0.04 s.
    # `-fps_mode passthrough` keeps those timestamps instead of resampling to a
    # constant rate, so the whole-file grid fit measures genuine drift. `testsrc2`
    # emits 100 frames at rate 50 over 2 s; `setpts` relabels their presentation
    # times. `N` is the frame index and `TB` the output timebase.
    expression = "setpts='if(lt(N,50), N/50/TB, (1 + (N-50)/25)/TB)'"
    yield build(
        root / "vfr.mp4",
        "-vf",
        expression,
        "-fps_mode",
        "passthrough",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        source=["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=50:duration=2"],
    )


@pytest.fixture(scope="session")
def lying_header_mkv(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("lying_header")
    # A raw H.264 elementary stream whose SPS VUI advertises 25 fps.
    raw = build(
        root / "raw25.h264",
        "-c:v",
        "libx264",
        "-bf",
        "0",
        "-pix_fmt",
        "yuv420p",
        "-f",
        "h264",
        source=["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=3"],
    )
    # Mux that stream into Matroska at 30 fps: the packet timestamps run at 30
    # while avg_frame_rate keeps the advertised 25 -- a header that lies about the
    # rate (a 16.7% discrepancy) over otherwise-uniform, constant-rate timing.
    # `-c copy -fflags +genpts` into mp4 recomputes avg_frame_rate from the real
    # packets (to within 0.3% of 30) and clears unreliable_timing_metadata.
    yield build(
        root / "lying_header.mkv",
        "-c",
        "copy",
        source=["-r", "30", "-i", str(raw)],
    )


@pytest.fixture(scope="session")
def h264_in_avi(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("h264_avi")
    # h264 is a codec Chrome decodes; AVI is a container it cannot open. The
    # playback fix is a `-c copy` rewrap into mp4, not a re-encode. `-bf 0` keeps
    # the copy to mp4 free of B-frame reordering trouble.
    yield build(
        root / "h264.avi",
        "-c:v",
        "libx264",
        "-bf",
        "0",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "25",
        source=["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=2"],
    )
