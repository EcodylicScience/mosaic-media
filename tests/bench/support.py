"""Shared helpers and constants for the perf regression gate.

No OpenCV is imported at module level, so this module is safe to import in the
default suite; cv2_read_targets imports cv2 inside its own body. The video
benchmarks that need OpenCV do the module-level importorskip themselves, using
CV2_IMPORTORSKIP_REASON so the message is written once.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import TYPE_CHECKING

from mosaic_media import MediaFacts

if TYPE_CHECKING:
    from mosaic_media.io import VideoReader

BENCH_FPS = 30.0
BENCH_FRAMES = 900  # 30 s at 30 fps -- a realistic recording length
BENCH_SIZE = (1920, 1080)  # 1080p, the resolution consumers actually process

CV2_IMPORTORSKIP_REASON = (
    "the perf gate needs OpenCV; install it with: uv sync --group bench"
)

__all__ = [
    "BENCH_FPS",
    "BENCH_FRAMES",
    "BENCH_SIZE",
    "CV2_IMPORTORSKIP_REASON",
    "cv2_read_targets",
    "make_reader",
    "reader_read_targets",
    "sample_shuffled",
    "sample_sorted",
]


def make_reader(path: Path, facts: MediaFacts, *, frame_step: int = 1) -> VideoReader:
    """Construct a VideoReader with injected probe facts.

    Every benchmark opens readers through this one helper, so the injection
    seam lives on a single line: the facts measured once by the corpus fixture
    are passed as `facts=`, which suppresses the reader's own metadata probe
    and keeps probing cost out of every timed region.
    """
    from mosaic_media.io import VideoReader

    return VideoReader(path, facts=facts, frame_step=frame_step)


def cv2_read_targets(path: Path, targets: list[int]) -> int:
    """Seek to each target with CAP_PROP_POS_FRAMES and read one frame.

    The OpenCV baseline shared by the monotonic-seek, sparse-extraction, and
    cold-seek workloads: one CAP_PROP_POS_FRAMES seek plus one read per target,
    re-decoding the GOP chain from the preceding keyframe every time. cv2 is
    imported inside the body so this module stays importable without OpenCV.
    Returns the number of frames read, so the gate cannot be passed by a
    no-op. cv2's read() never returns a None frame alongside ok=True, so only
    ok is checked here (matching the precedent in
    tests/io/test_reader_cv2_equality.py's _cv2_all_frames).
    """
    import cv2

    capture = cv2.VideoCapture(str(path))
    decoded_count = 0
    try:
        for target in targets:
            _ = capture.set(cv2.CAP_PROP_POS_FRAMES, target)
            ok, _frame = capture.read()
            if not ok:
                break
            decoded_count += 1
    finally:
        capture.release()
    return decoded_count


def reader_read_targets(path: Path, facts: MediaFacts, targets: list[int]) -> int:
    """Seek to each target with the reader and read one frame.

    The reader counterpart to cv2_read_targets, shared by the monotonic-seek
    and cold-seek workloads. The reader resolves each target's preceding
    keyframe from the packet index, and either continues decoding forward on
    its already-open container when the target lies ahead within that window,
    or seeks the container to the keyframe's presentation timestamp and
    decodes forward from there; the workload's target ordering (monotonic
    forward vs shuffled) decides which path dominates.
    """
    decoded_count = 0
    with make_reader(path, facts) as reader:
        for target in targets:
            reader.seek(target)
            ok, frame = reader.read()
            if not ok or frame is None:
                break
            decoded_count += 1
    return decoded_count


def sample_sorted(frame_count: int, count: int, seed: int) -> list[int]:
    """Deterministic sorted unique frame targets (sparse extraction pattern)."""
    return sorted(random.Random(seed).sample(range(frame_count), count))


def sample_shuffled(frame_count: int, count: int, seed: int) -> list[int]:
    """Deterministic non-monotonic frame targets (cold random seek pattern)."""
    return random.Random(seed).sample(range(frame_count), count)
