"""Gated workload: cold isolated random single-frame seeks.

No consumer performs isolated random single-frame seeks -- every seek call
site in the toolkit today is monotonic forward (see the gated seek
workloads). A shuffled target sequence defeats the reader's discard-forward
reuse (see support.reader_read_targets): each seek resolves the target's
preceding keyframe from the packet index and calls container.seek to it, so
every target pays a fresh keyframe seek plus decode-forward rather than
continuing an already-open decode position.

This is the one workload gated below parity (0.9) rather than at 1.0. On the
reference configuration (see conftest.py) the reader measures faster than
OpenCV on both corpus variants (calibration medians gop12 1.327, gop250
1.061), but a bootstrap over the observed rounds shows the gop250 case
failing a 1.0 gate in roughly one run in forty (2.53 percent of resampled
median-of-5 statistics) while failing none at 0.9. The 0.9 floor is set from
that measured variance, not from the reader being slow.
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
    reader_read_targets,
    sample_shuffled,
)

# No module-level cv2 reference: both sides run through the shared
# cv2_read_targets / reader_read_targets loops. The importorskip stays at
# module level so the whole file is skipped cleanly when OpenCV is absent.
pytest.importorskip("cv2", reason=CV2_IMPORTORSKIP_REASON)
pytestmark = pytest.mark.bench

COLD_SEEK_COUNT = 20
COLD_SEEK_SEED = 424242


@pytest.mark.parametrize("corpus_key", ["gop12", "gop250"])
def test_gate_cold_random_seek(bench_corpus: dict[str, Path], corpus_key: str) -> None:
    path = bench_corpus[corpus_key]

    def setup() -> tuple[MediaFacts, list[int]]:
        return (
            probe_media(path),
            sample_shuffled(BENCH_FRAMES, COLD_SEEK_COUNT, COLD_SEEK_SEED),
        )

    workload = Workload(
        name=f"cold-random-seek[{corpus_key}]",
        setup=setup,
        cv2_callable=lambda context: cv2_read_targets(path, context[1]),
        reader_callable=lambda context: reader_read_targets(
            path, context[0], context[1]
        ),
    )
    assert_gate(run_workload(workload), threshold=0.9)
