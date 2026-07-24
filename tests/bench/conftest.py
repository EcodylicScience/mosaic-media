"""Performance regression gate: run conditions and the shared corpus fixture.

RUN CONDITION. These benchmarks are excluded from the default test run
(addopts = -m 'not bench') and must be run explicitly, serialized -- one
benchmark process at a time, on an otherwise idle machine:

    pytest -m bench -n0 -s

never parallel: -n0 forces a single worker and -s shows the per-workload
report. Wrap the invocation in whatever serialization mechanism the machine
provides to keep concurrent workloads off the cores. Measurements taken
alongside concurrent work swung by roughly 2x run to run, so the gate is
only meaningful on an idle machine.

Corpus generation is 1080p and expensive; it happens once per session and only
when a bench test requests the fixture, never in the default run.

Each workload probes its file once with probe_media in the untimed setup and
injects the resulting MediaFacts into the reader through support.make_reader,
so no benchmark re-measures inside the timed region -- consumers hold MediaFacts
and do not re-probe per open ('measurement is not re-derived').

REFERENCE CONFIGURATION. Thresholds are calibrated on a reference
configuration: 20 cores, Python 3.12.3, system ffmpeg 6.1.1, and the
dependency set pinned in uv.lock. Running the gate on a smaller or busier
machine is expected to fail thresholds; such a failure reports on the
machine, not on the code. The recorded medians beside each threshold are what
the reference measured, updated only at a deliberate recalibration -- never
edited to make a run pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.bench.support import BENCH_FPS, BENCH_FRAMES, BENCH_SIZE
from tests.helpers.corpus import generate_video


@pytest.fixture(scope="session")
def bench_corpus(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """The 1080p bench corpus, generated once per session.

    Three H.264 files sharing frame count and resolution: short GOP (12), long
    GOP (250), and a 90-degree rotation variant. GOP size is the dominant
    variable in seek cost; the rotation variant checks the reader matches
    OpenCV's rotated output on a full decode.
    """
    directory = tmp_path_factory.mktemp("bench_corpus")
    return {
        "gop12": generate_video(
            directory / "gop12.mp4",
            frames=BENCH_FRAMES,
            fps=BENCH_FPS,
            gop=12,
            size=BENCH_SIZE,
        ),
        "gop250": generate_video(
            directory / "gop250.mp4",
            frames=BENCH_FRAMES,
            fps=BENCH_FPS,
            gop=250,
            size=BENCH_SIZE,
        ),
        "rotation": generate_video(
            directory / "rotation.mp4",
            frames=BENCH_FRAMES,
            fps=BENCH_FPS,
            gop=12,
            size=BENCH_SIZE,
            rotation_degrees=90,
        ),
    }
