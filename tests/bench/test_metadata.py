"""Gated workload: metadata/open overhead; plus a non-gating probe-cost report.

- metadata-open mirrors get_video_metadata: open, read width/height/fps/frame
  count, close. The reader is constructed WITH injected facts (consumers hold
  MediaFacts; 'measurement is not re-derived'), so it answers metadata without
  re-probing.
- probe-cost is reported separately and NOT gated: the probe is a one-time
  per-file-lifetime cost by design -- the measurement travels forward with the
  file and consumers do not re-probe per open. Benchmarking probe_media as if
  consumers re-probe every open would contradict that invariant, so it is
  reported for visibility only.
"""

from __future__ import annotations

import statistics
from pathlib import Path

import pytest

from mosaic_media import MediaFacts, probe_media
from tests.bench.harness import Workload, assert_gate, run_workload, time_series
from tests.bench.support import CV2_IMPORTORSKIP_REASON, make_reader

cv2 = pytest.importorskip("cv2", reason=CV2_IMPORTORSKIP_REASON)
pytestmark = pytest.mark.bench


def _cv2_metadata(path: Path) -> int:
    capture = cv2.VideoCapture(str(path))
    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    finally:
        capture.release()
    return width + height + int(fps) + frame_count


def _reader_metadata(path: Path, facts: MediaFacts) -> int:
    reader = make_reader(path, facts)
    try:
        total = reader.width + reader.height + int(reader.fps) + reader.frame_count
    finally:
        reader.close()
    return total


@pytest.mark.parametrize("corpus_key", ["gop12", "gop250", "rotation"])
def test_gate_metadata_open(bench_corpus: dict[str, Path], corpus_key: str) -> None:
    path = bench_corpus[corpus_key]
    workload = Workload(
        name=f"metadata-open[{corpus_key}]",
        setup=lambda: probe_media(path),
        cv2_callable=lambda _facts: _cv2_metadata(path),
        reader_callable=lambda facts: _reader_metadata(path, facts),
    )
    assert_gate(run_workload(workload))


def test_report_probe_cost(bench_corpus: dict[str, Path]) -> None:
    """Report the one-time probe cost per file. Non-gating by design."""
    for corpus_key in ("gop12", "gop250", "rotation"):
        path = bench_corpus[corpus_key]
        times = time_series(lambda captured=path: probe_media(captured))
        median_ms = statistics.median(times) * 1000.0
        message = (
            f"[bench] probe-cost[{corpus_key}] median {median_ms:.1f} ms "
            "(one-time per file lifetime; measurement travels forward, "
            "consumers do not re-probe per open)"
        )
        print(message)
