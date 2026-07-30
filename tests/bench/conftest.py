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

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.bench.support import BENCH_FPS, BENCH_FRAMES, BENCH_SIZE
from tests.helpers.corpus import generate_video

# The gate measures the reader against an OpenCV baseline, and OpenCV's bundled
# FFmpeg cannot software-decode AV1. The corpus is therefore H.264, which forces
# a GPL-only encoder and makes this suite the one place the package requires an
# ffmpeg the deployment deliberately does not ship. It is excluded from the
# default run, never distributed, and exempted from the encoder guard for
# exactly that reason; see tests/test_encoder_guard.py.
#
# 900 frames at 1080p is far too large to commit, so the clips are generated,
# and a machine without the encoder cannot run the gate at all.
BENCH_CODEC = "libx264"


def _bench_encoder_available() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    listing = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if listing.returncode != 0:
        return False
    return any(
        len(columns) >= 2 and columns[1] == BENCH_CODEC
        for columns in (line.split() for line in listing.stdout.splitlines())
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """End the session when the gate is selected but its encoder is absent.

    A skip would report a green run that measured nothing, and a failure would
    read as a regression in the code rather than a missing tool. Neither is
    true, so the session stops and says which it is.
    """
    del config
    selected = [item for item in items if item.get_closest_marker("bench") is not None]
    if not selected or _bench_encoder_available():
        return
    reason = (
        f"the performance gate needs an ffmpeg that encodes {BENCH_CODEC}, and the "
        f"one on PATH does not. Its corpus is H.264 because the gate's baseline is "
        f"OpenCV, which cannot decode AV1. Run the gate on a machine with a GPL "
        f"ffmpeg build, or drop -m bench."
    )
    pytest.exit(reason, returncode=pytest.ExitCode.USAGE_ERROR)


@pytest.fixture(scope="session")
def bench_corpus(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """The 1080p bench corpus, generated once per session.

    Three H.264 files sharing frame count and resolution (H.264, not the
    package default, because the gate baselines against OpenCV): short GOP (12), long
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
            codec=BENCH_CODEC,
        ),
        "gop250": generate_video(
            directory / "gop250.mp4",
            frames=BENCH_FRAMES,
            fps=BENCH_FPS,
            gop=250,
            size=BENCH_SIZE,
            codec=BENCH_CODEC,
        ),
        "rotation": generate_video(
            directory / "rotation.mp4",
            frames=BENCH_FRAMES,
            fps=BENCH_FPS,
            gop=12,
            size=BENCH_SIZE,
            codec=BENCH_CODEC,
            rotation_degrees=90,
        ),
    }
