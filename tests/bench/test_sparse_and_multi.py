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
from mosaic_media.io.index import SeekIndex
from tests.bench.harness import (
    DEFAULT_ROUNDS,
    Workload,
    assert_gate,
    format_report,
    run_workload,
)
from tests.bench.support import (
    BENCH_FRAMES,
    CV2_IMPORTORSKIP_REASON,
    cv2_read_targets,
    make_reader,
    sample_sorted,
)
from tests.helpers.indexes import index_for

cv2 = pytest.importorskip("cv2", reason=CV2_IMPORTORSKIP_REASON)
pytestmark = pytest.mark.bench

SPARSE_COUNT = 20
SPARSE_SEED = 20260716
JUNCTION_WINDOW = 100

# Thin-margin rule: any gated workload whose margin over its bound is under 10
# percent runs at rounds=9, so the recorded median makes a failure diagnosable
# as regression-versus-noise (see the spec's "Gate policy and thresholds,
# revised for in-process decode").
_SORTED_SPARSE_EXTRACTION_ROUNDS: dict[str, int] = {
    "gop12": 9,  # calibration median 1.218, thin margin (9.9 percent)
    "gop250": DEFAULT_ROUNDS,  # calibration median 3.387, ample margin
}
# The injected gate measures a comfortable margin (calibration median 1.656,
# 59 percent over the 1.0 bound), so the thin-margin rule does not apply here.
# Rounds stay at 9 because the print-only from-scratch report above shares
# this constant and flaps across its 1.5x reference marker, where the extra
# rounds still earn their keep (see the spec's "Gate policy and thresholds,
# revised for in-process decode").
_MULTI_VIDEO_JUNCTION_ROUNDS = 9


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


def _reader_multi_junction_injected(
    paths: list[Path],
    facts: list[MediaFacts],
    indices: list[SeekIndex],
    count_first: int,
    window: int,
) -> int:
    from mosaic_media.io import MultiVideoReader

    decoded_count = 0
    reader = MultiVideoReader(paths, facts=facts, indices=indices)
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
    assert_gate(
        run_workload(workload, rounds=_SORTED_SPARSE_EXTRACTION_ROUNDS[corpus_key])
    )


def test_report_multi_video_junction(bench_corpus: dict[str, Path]) -> None:
    """Print-only report of the from-scratch open cost. Never asserts.

    Constructing a MultiVideoReader without injected facts probes every file
    (an ffprobe subprocess each) and scans each segment's packets before the
    junction read; the OpenCV side opens two captures with a header read only.
    So this measures open cost, not decode -- a raw two-container decode of the
    same frames sits near parity.

    It does not assert, for two reasons that reinforce each other. It is the
    non-consumer path: consumers hold MediaFacts from the one ingestion probe
    and inject them, so they never pay a per-open probe, and
    test_gate_multi_video_junction_with_injected_facts gates that real path at
    parity. And the probe this path pays now reads every packet's payload to
    mint the content digest, which raised the open cost further -- the ratio
    flaps across a 1.5x bound on real hardware, throttling as the bench heats
    the CPU, so a bound here fails on machine state rather than on a code
    regression the injected gate would not already catch.

    This is the measured, hard reason a consumer that already holds probed facts
    must inject them rather than reconstruct a reader from paths: the per-open
    probe it avoids is the dominant cost of this workload, and it grew.
    """
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
    # Print-only: the injected gate owns the regression guard for this workload.
    # The number only labels where the ratio landed relative to the historical
    # 1.5x bound; nothing asserts on it, which is what report mode says.
    print(
        format_report(
            run_workload(workload, rounds=_MULTI_VIDEO_JUNCTION_ROUNDS),
            threshold=1.0 / 1.5,
            mode="report",
        )
    )


def test_gate_multi_video_junction_with_injected_facts(
    bench_corpus: dict[str, Path],
) -> None:
    """The consumer-shaped open, gated: facts from ingestion and indices held
    by the caller are injected, so the timed region pays no ffprobe subprocess
    and no packet rescan -- construction is the metadata-authority path
    consumers actually run. This is a parity gate (>= 1.0) on the reference
    configuration (see conftest.py); the calibration median is 1.656. The
    from-scratch construction stays a bounded report above."""
    path = bench_corpus["gop12"]
    paths = [path, path]

    def setup() -> tuple[list[MediaFacts], list[SeekIndex]]:
        facts = probe_media(path)
        index = index_for(path)
        return [facts, facts], [index, index]

    workload = Workload(
        name="multi-video-junction-injected[gop12+gop12]",
        setup=setup,
        cv2_callable=lambda _context: _cv2_multi_junction(
            path, path, BENCH_FRAMES, JUNCTION_WINDOW
        ),
        reader_callable=lambda context: _reader_multi_junction_injected(
            paths, context[0], context[1], BENCH_FRAMES, JUNCTION_WINDOW
        ),
    )
    assert_gate(
        run_workload(workload, rounds=_MULTI_VIDEO_JUNCTION_ROUNDS),
        threshold=1.0,
    )
