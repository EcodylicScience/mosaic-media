"""Gated workloads: sorted-sparse frame extraction and multi-video junction read.

- sorted-sparse-extraction mirrors save_frames_as_png: extract a set of sorted
  target frames. OpenCV seeks and reads once per target, re-decoding the GOP
  chain each time; the reader groups targets by GOP via the packet index and
  decodes each GOP once (read_frames).
- multi-video-junction mirrors MultiVideoReader consumers (render_stream): read
  across the boundary between two files. The OpenCV baseline opens two captures
  and stitches them manually; the reader presents one global frame space.
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
    sample_sorted,
)

cv2 = pytest.importorskip("cv2", reason=CV2_IMPORTORSKIP_REASON)
pytestmark = pytest.mark.bench

SPARSE_COUNT = 20
SPARSE_SEED = 20260716
JUNCTION_WINDOW = 100


# The cv2 sparse baseline is the shared seek-one-read-one loop cv2_read_targets;
# _reader_sparse stays separate on purpose: using read_frames (GOP-grouped, one
# decode pass per GOP) instead of a per-target seek-plus-read IS the workload
# under test here, so it must not fold into reader_read_targets.
def _reader_sparse(path: Path, facts: MediaFacts, targets: list[int]) -> int:
    decoded_count = 0
    with make_reader(path, facts) as reader:
        for _index, _frame in reader.read_frames(targets):
            decoded_count += 1
    return decoded_count


def _cv2_multi_junction(
    path_first: Path, path_second: Path, count_first: int, window: int
) -> int:
    decoded_count = 0
    capture_first = cv2.VideoCapture(str(path_first))
    try:
        capture_first.set(cv2.CAP_PROP_POS_FRAMES, count_first - window)
        for _ in range(window):
            ok, frame = capture_first.read()
            if not ok or frame is None:
                break
            decoded_count += 1
    finally:
        capture_first.release()
    capture_second = cv2.VideoCapture(str(path_second))
    try:
        for _ in range(window):
            ok, frame = capture_second.read()
            if not ok or frame is None:
                break
            decoded_count += 1
    finally:
        capture_second.release()
    return decoded_count


def _reader_multi_junction(paths: list[Path], count_first: int, window: int) -> int:
    from mosaic_media.io import MultiVideoReader

    decoded_count = 0
    reader = MultiVideoReader(paths)
    try:
        reader.seek(count_first - window)
        for _ in range(2 * window):
            ok, frame = reader.read()
            if not ok or frame is None:
                break
            decoded_count += 1
    finally:
        reader.close()
    return decoded_count


@pytest.mark.parametrize("corpus_key", ["gop12", "gop250"])
def test_gate_sorted_sparse_extraction(
    bench_corpus: dict[str, Path], corpus_key: str
) -> None:
    path = bench_corpus[corpus_key]

    def setup() -> tuple[MediaFacts, list[int]]:
        return (
            probe_media(path),
            sample_sorted(BENCH_FRAMES, SPARSE_COUNT, SPARSE_SEED),
        )

    workload = Workload(
        name=f"sorted-sparse-extraction[{corpus_key}]",
        setup=setup,
        cv2_callable=lambda context: cv2_read_targets(path, context[1]),
        reader_callable=lambda context: _reader_sparse(path, context[0], context[1]),
    )
    assert_gate(run_workload(workload))


def test_gate_multi_video_junction(bench_corpus: dict[str, Path]) -> None:
    path = bench_corpus["gop12"]
    paths = [path, path]
    workload = Workload(
        name="multi-video-junction[gop12+gop12]",
        setup=lambda: None,
        cv2_callable=lambda _context: _cv2_multi_junction(
            path, path, BENCH_FRAMES, JUNCTION_WINDOW
        ),
        reader_callable=lambda _context: _reader_multi_junction(
            paths, BENCH_FRAMES, JUNCTION_WINDOW
        ),
    )
    assert_gate(run_workload(workload))
