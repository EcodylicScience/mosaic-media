"""Non-gating report: cold isolated random single-frame seeks.

Reported against a documented bound of at most 2x OpenCV; NOT gated at parity.
No consumer performs isolated random single-frame seeks -- every seek call site
in the toolkit today is monotonic forward (see the gated seek workloads). The
~35 ms ffmpeg process-spawn floor makes strict parity with OpenCV's in-process
seek unreachable on short-GOP files without the rejected packet-feed daemon.
The spec therefore documents a 2x bound rather than parity: this test prints
the numbers and FAILS ONLY if the reader exceeds 2x OpenCV, which is a real
regression signal; being merely slower than OpenCV within the bound is expected
and passes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mosaic_media import MediaFacts, probe_media
from tests.bench.harness import Workload, assert_bounded, run_workload
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
def test_report_cold_random_seek(
    bench_corpus: dict[str, Path], corpus_key: str
) -> None:
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
    assert_bounded(run_workload(workload), max_slowdown=2.0)
