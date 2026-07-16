"""Gated workloads: seek-to-start then sequential, and monotonic strided seeks.

- seek-then-sequential mirrors video_stream.py's _FrameStream: seek to the
  start frame once, then read sequentially.
- monotonic-strided-seeks mirrors the pose visualization loop in
  tracking/pose_training/inference.py, which for each successive result seeks
  the capture to start_frame + result_index * frame_step via
  CAP_PROP_POS_FRAMES and reads one frame. A small monotonically increasing
  step keeps consecutive targets inside the current keyframe-to-position
  window, so the reader discards forward on the live process rather than
  respawning per seek.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mosaic_media import MediaFacts, probe_media
from tests.bench.harness import Workload, assert_gate, run_workload
from tests.bench.support import (
    BENCH_FRAMES,
    CV2_IMPORTORSKIP_REASON,
    cv2_read_targets,
    make_reader,
    reader_read_targets,
)

cv2 = pytest.importorskip("cv2", reason=CV2_IMPORTORSKIP_REASON)
pytestmark = pytest.mark.bench

SEEK_START_READ_COUNT = 200
MONOTONIC_SEEKS = 50
MONOTONIC_STEP = 3


def _cv2_seek_then_sequential(path: Path, start: int, count: int) -> int:
    capture = cv2.VideoCapture(str(path))
    read = 0
    try:
        capture.set(cv2.CAP_PROP_POS_FRAMES, start)
        while read < count:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            read += 1
    finally:
        capture.release()
    return read


def _reader_seek_then_sequential(
    path: Path, facts: MediaFacts, start: int, count: int
) -> int:
    read = 0
    with make_reader(path, facts) as reader:
        reader.seek(start)
        while read < count:
            ok, frame = reader.read()
            if not ok or frame is None:
                break
            read += 1
    return read


@pytest.mark.parametrize("corpus_key", ["gop12", "gop250"])
def test_gate_seek_then_sequential(
    bench_corpus: dict[str, Path], corpus_key: str
) -> None:
    path = bench_corpus[corpus_key]
    start = BENCH_FRAMES // 2
    workload = Workload(
        name=f"seek-then-sequential[{corpus_key}]",
        setup=lambda: probe_media(path),
        cv2_callable=lambda _facts: _cv2_seek_then_sequential(
            path, start, SEEK_START_READ_COUNT
        ),
        reader_callable=lambda facts: _reader_seek_then_sequential(
            path, facts, start, SEEK_START_READ_COUNT
        ),
    )
    assert_gate(run_workload(workload))


@pytest.mark.parametrize("corpus_key", ["gop12", "gop250"])
def test_gate_monotonic_strided_seeks(
    bench_corpus: dict[str, Path], corpus_key: str
) -> None:
    path = bench_corpus[corpus_key]
    start = BENCH_FRAMES // 4
    # The monotonic targets are a plain arithmetic progression; compute them
    # once in setup so the shared seek-one-read-one loops (cv2_read_targets /
    # reader_read_targets) drive both sides.
    targets = [start + index * MONOTONIC_STEP for index in range(MONOTONIC_SEEKS)]

    def setup() -> MediaFacts:
        return probe_media(path)

    workload = Workload(
        name=f"monotonic-strided-seeks[{corpus_key}]",
        setup=setup,
        cv2_callable=lambda _facts: cv2_read_targets(path, targets),
        reader_callable=lambda facts: reader_read_targets(path, facts, targets),
    )
    assert_gate(run_workload(workload))
