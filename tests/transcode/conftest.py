"""Defect fixtures for the transcode acceptance tests.

The tail-moov and rotated corpora reuse the shared `clips` fixture (`cfr_mp4`,
`rotated_mp4`). Four defect files the shared corpus lacks are generated here: a
genuinely variable-frame-rate clip, a lying-timing-header clip, an h264-in-avi
clip (a supported codec in an unopenable container), and a VP8 clip whose
header lies about its rate, in the container VP8 belongs to -- the case where
the analysis fix is a copy remux the mp4 muxer cannot carry.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.helpers.media_fixtures import asset, build


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
        "libsvtav1",
        "-pix_fmt",
        "yuv420p",
        source=["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=50:duration=2"],
    )


@pytest.fixture(scope="session")
def slow_reencode_source(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("slow_reencode")
    # A larger variable-frame-rate clip whose analysis re-encode is slow enough to
    # stream several -progress blocks and to stay running long enough to cancel
    # mid-encode. Like variable_frame_rate_mp4 it relabels presentation times with
    # setpts so the whole-file grid fit measures genuine drift; the extra frames
    # and resolution keep the SVT-AV1 pass above a second. SVT-AV1 buffers a large
    # lookahead before it emits any output, so a short clip flushes in a single
    # burst -- 150 frames is enough to stream progress after the pipeline fills.
    # `testsrc2` emits 150 frames at rate 30 over 5 s; the first 75 are spaced
    # 1/30 s and the remaining 75 are spaced 1/15 s.
    expression = "setpts='if(lt(N,75), N/30/TB, (2.5 + (N-75)/15)/TB)'"
    yield build(
        root / "slow_vfr.mp4",
        "-vf",
        expression,
        "-fps_mode",
        "passthrough",
        "-c:v",
        "libsvtav1",
        "-pix_fmt",
        "yuv420p",
        source=["-f", "lavfi", "-i", "testsrc2=size=512x384:rate=30:duration=5"],
    )


@pytest.fixture(scope="session")
def lying_header_mkv(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("lying_header")
    source = asset("cfr.mp4", root / "cfr.mp4")
    # Matroska stores the declared average rate in its own track header, taken
    # from the stream handed to the muxer rather than from the packets written.
    # `-itsscale` rescales the input timestamps and nothing else, so a `-c copy`
    # of this 25 fps clip at scale 1.25 leaves 25 fps in the header over packets
    # spaced at 20 -- a header that lies about the rate (a 25% discrepancy) over
    # otherwise-uniform, constant-rate timing. The stretched 50 ms period is
    # exact in Matroska's millisecond timebase, so nothing requantizes and the
    # whole-file grid fit still measures a constant rate. `-c copy -fflags
    # +genpts` into mp4 recomputes avg_frame_rate from the real packets and
    # clears unreliable_timing_metadata.
    yield build(
        root / "lying_header.mkv",
        "-c",
        "copy",
        source=["-itsscale", "1.25", "-i", str(source)],
    )


@pytest.fixture(scope="session")
def h264_in_avi(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("h264_avi")
    # h264 is a codec Chrome decodes; AVI is a container it cannot open. The
    # playback fix is a `-c copy` rewrap into mp4, not a re-encode. The committed
    # clip carries `-bf 0`, which keeps that copy free of B-frame reordering
    # trouble. The codec is the point of the case, so no substitute serves.
    yield asset("h264.avi", root / "h264.avi")


@pytest.fixture(scope="session")
def lying_header_vp8_webm(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A VP8 clip whose header lies about its rate, in the container VP8 belongs to.

    The analysis fix for a timing-metadata lie is a `-c copy` remux, and the
    converter always writes mp4 -- which has no tag for VP8, so that copy dies in
    the muxer before a header is written. The codec is the point of the case, so
    no substitute serves.

    A VP8 clip encoded straight from a filter source carries an honest header and
    fires no analysis reason at all. `-itsscale` rescales the input timestamps and
    nothing else, so a `-c copy` of this 25 fps clip at scale 1.25 leaves 25 fps
    in the header over packets spaced at 20 -- the same technique
    `lying_header_mkv` uses, and the same lie the corpus VP8 sources carry.
    """
    root = tmp_path_factory.mktemp("lying_header_vp8")
    source = build(root / "vp8.webm", "-c:v", "libvpx", "-pix_fmt", "yuv420p")
    yield build(
        root / "lying_header_vp8.webm",
        "-c",
        "copy",
        source=["-itsscale", "1.25", "-i", str(source)],
    )
