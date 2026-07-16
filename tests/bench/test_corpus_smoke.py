"""Smoke test: the bench corpus generates and probes cleanly.

Marked bench because it generates the 1080p corpus. Run before the gated
workloads to isolate corpus and probe wiring failures from reader performance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mosaic_media import probe_media
from tests.bench.support import BENCH_FRAMES, BENCH_SIZE

pytestmark = pytest.mark.bench


def test_corpus_generates_and_probes(bench_corpus: dict[str, Path]) -> None:
    for corpus_key in ("gop12", "gop250", "rotation"):
        path = bench_corpus[corpus_key]
        assert path.exists(), f"{corpus_key} corpus file missing: {path}"
        facts = probe_media(path)
        frame_count_message = (
            f"{corpus_key}: probed {facts.frame_count} frames, expected {BENCH_FRAMES}"
        )
        assert facts.frame_count == BENCH_FRAMES, frame_count_message
        assert (facts.width, facts.height) == BENCH_SIZE
