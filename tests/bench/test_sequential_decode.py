"""Gated workloads: sequential full decode and strided decode.

- sequential-full-decode mirrors the tracking inference main path
  (mosaic FFmpegFrameReader / extract_candidate_features read loop): decode
  every frame in order.
- strided-decode mirrors inference batch stepping (extract_candidate_features
  with candidate_step): OpenCV has no native stride, so the consumer decodes
  every frame and discards the ones it does not want; the reader uses
  frame_step to avoid transferring the discarded frames.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mosaic_media import MediaFacts, probe_media
from tests.bench.harness import Workload, assert_gate, run_workload
from tests.bench.support import CV2_IMPORTORSKIP_REASON, make_reader

cv2 = pytest.importorskip("cv2", reason=CV2_IMPORTORSKIP_REASON)
pytestmark = pytest.mark.bench

STRIDE = 5

# Tier CARVE (>= 0.9): the owned-BGR structural copy. av reformats yuv into a
# bgr24 frame plus an ndarray copy out of it, ~0.4-0.5 ms/frame at 1080p,
# where cv2 converts into the returned array in one operation (see the
# spec's "Gate policy and thresholds, revised for in-process decode"). The
# rotation variant decodes through the libav transpose filter graph and
# stays a full-tier >= 1.0 gate, not the carve tier.
_SEQUENTIAL_FULL_DECODE_THRESHOLD: dict[str, float] = {
    "gop12": 0.9,  # stabilization median 0.956
    "gop250": 0.9,  # stabilization median 0.949
    "rotation": 1.0,  # stabilization median 1.242
}


def _cv2_sequential(path: Path) -> int:
    capture = cv2.VideoCapture(str(path))
    count = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            count += 1
    finally:
        capture.release()
    return count


def _reader_sequential(path: Path, facts: MediaFacts) -> int:
    count = 0
    with make_reader(path, facts) as reader:
        while True:
            ok, frame = reader.read()
            if not ok or frame is None:
                break
            count += 1
    return count


def _cv2_strided(path: Path, step: int) -> int:
    capture = cv2.VideoCapture(str(path))
    kept = 0
    index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            if index % step == 0:
                kept += 1
            index += 1
    finally:
        capture.release()
    return kept


def _reader_strided(path: Path, facts: MediaFacts, step: int) -> int:
    kept = 0
    with make_reader(path, facts, frame_step=step) as reader:
        while True:
            ok, frame = reader.read()
            if not ok or frame is None:
                break
            kept += 1
    return kept


@pytest.mark.parametrize("corpus_key", ["gop12", "gop250", "rotation"])
def test_gate_sequential_full_decode(
    bench_corpus: dict[str, Path], corpus_key: str
) -> None:
    path = bench_corpus[corpus_key]
    workload = Workload(
        name=f"sequential-full-decode[{corpus_key}]",
        setup=lambda: probe_media(path),
        cv2_callable=lambda _facts: _cv2_sequential(path),
        reader_callable=lambda facts: _reader_sequential(path, facts),
    )
    assert_gate(
        run_workload(workload),
        threshold=_SEQUENTIAL_FULL_DECODE_THRESHOLD[corpus_key],
    )


@pytest.mark.parametrize("corpus_key", ["gop12", "gop250"])
def test_gate_strided_decode(bench_corpus: dict[str, Path], corpus_key: str) -> None:
    path = bench_corpus[corpus_key]
    workload = Workload(
        name=f"strided-decode-step{STRIDE}[{corpus_key}]",
        setup=lambda: probe_media(path),
        cv2_callable=lambda _facts: _cv2_strided(path, STRIDE),
        reader_callable=lambda facts: _reader_strided(path, facts, STRIDE),
    )
    assert_gate(run_workload(workload))
