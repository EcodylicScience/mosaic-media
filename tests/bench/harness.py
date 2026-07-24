"""Performance regression gate engine.

One declarative structure -- Workload -- and one runner drive every gated
benchmark. The runner interleaves the OpenCV baseline and the reader within
each round (never all-of-one-then-all-of-other), times each side with
time.perf_counter, and gates on median(cv2)/median(reader) >= 1.0: the reader
must be at least as fast as OpenCV on every consumer workflow.

The engine imports only the standard library, so its own unit test
(test_harness.py) runs in the default suite without the bench dependency
group. The video benchmarks that use it are marked bench and excluded from
the default run.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

DEFAULT_ROUNDS = 5

# Whether a measurement is asserted on. A gate fails the run when it is missed;
# a report only records where the ratio landed.
ReportMode = Literal["gate", "report"]

# (met, missed) verdict words per mode.
_VERDICTS: dict[ReportMode, tuple[str, str]] = {
    "gate": ("PASS", "FAIL"),
    "report": ("OK", "WARN"),
}

# What the compared-against number is called per mode. A report's number is a
# reference marker, not a threshold: nothing asserts on it.
_BOUND_LABELS: dict[ReportMode, str] = {
    "gate": "threshold",
    "report": "reference",
}

SetupT = TypeVar("SetupT")


@dataclass(frozen=True)
class Workload(Generic[SetupT]):
    """One benchmark comparison.

    setup runs once, untimed, before the rounds; it performs preparation that
    must never sit inside the timed region -- probing a file for injected
    facts (consumers hold MediaFacts and do not re-measure), sampling target
    frames. Its result is handed to both callables.

    cv2_callable and reader_callable each perform exactly one full pass of the
    workflow being measured, and each must return a value, so a dead-code
    no-op cannot silently pass the gate.
    """

    name: str
    setup: Callable[[], SetupT]
    cv2_callable: Callable[[SetupT], object]
    reader_callable: Callable[[SetupT], object]


@dataclass(frozen=True)
class BenchResult:
    name: str
    cv2_times: list[float]
    reader_times: list[float]

    @property
    def cv2_median(self) -> float:
        return statistics.median(self.cv2_times)

    @property
    def reader_median(self) -> float:
        return statistics.median(self.reader_times)

    @property
    def ratio(self) -> float:
        if self.reader_median <= 0.0:
            message = (
                f"{self.name}: reader median is 0 s; the workload is too small "
                "to time reliably -- increase its size"
            )
            raise RuntimeError(message)
        return self.cv2_median / self.reader_median

    def passes(self, threshold: float = 1.0) -> bool:
        return self.ratio >= threshold


def _time_once(
    func: Callable[[SetupT], object],
    context: SetupT,
    timer: Callable[[], float],
) -> float:
    start = timer()
    result = func(context)
    elapsed = timer() - start
    if result is None:
        message = (
            "benchmark callable returned None; it must return a value so the "
            "measured work cannot be optimized away or silently skipped"
        )
        raise RuntimeError(message)
    return elapsed


def run_workload(
    workload: Workload[SetupT],
    *,
    rounds: int = DEFAULT_ROUNDS,
    timer: Callable[[], float] = time.perf_counter,
) -> BenchResult:
    """Time both sides over interleaved rounds and return the raw timings.

    Within each round both callables run once; the order alternates round to
    round so neither side systematically benefits from cache warmth. At least
    five rounds are required -- the gate takes the median, and fewer than five
    is too noisy to trust.
    """
    if rounds < 5:
        raise ValueError("the gate requires at least 5 rounds")
    context = workload.setup()
    cv2_times: list[float] = []
    reader_times: list[float] = []
    for round_index in range(rounds):
        if round_index % 2 == 0:
            cv2_times.append(_time_once(workload.cv2_callable, context, timer))
            reader_times.append(_time_once(workload.reader_callable, context, timer))
        else:
            reader_times.append(_time_once(workload.reader_callable, context, timer))
            cv2_times.append(_time_once(workload.cv2_callable, context, timer))
    return BenchResult(
        name=workload.name, cv2_times=cv2_times, reader_times=reader_times
    )


def time_series(
    func: Callable[[], object],
    *,
    rounds: int = DEFAULT_ROUNDS,
    timer: Callable[[], float] = time.perf_counter,
) -> list[float]:
    """Time one callable over rounds, returning per-round seconds.

    Used for non-gating informational measurements (probe cost), where there
    is no OpenCV baseline to compare against.
    """
    return [_time_once(lambda _context: func(), None, timer) for _ in range(rounds)]


def _format_milliseconds(times: list[float]) -> str:
    return ", ".join(f"{value * 1000:.1f}" for value in times)


def format_report(
    result: BenchResult, *, threshold: float = 1.0, mode: ReportMode = "gate"
) -> str:
    """Render one measurement. `mode` selects the vocabulary, not the numbers.

    A gated workload asserts on its threshold, so PASS and FAIL describe what
    the run did. A print-only report asserts on nothing, and calling its
    outcome FAIL reads as a failed run in a log where nothing failed -- so it
    reports OK or WARN, against a reference rather than a threshold, because
    no run is failed by missing it.
    """
    met, missed = _VERDICTS[mode]
    verdict = met if result.ratio >= threshold else missed
    header = f"[bench] {result.name}"
    cv2_line = (
        f"  cv2    median {result.cv2_median * 1000:9.1f} ms"
        f"  (rounds ms: {_format_milliseconds(result.cv2_times)})"
    )
    reader_line = (
        f"  reader median {result.reader_median * 1000:9.1f} ms"
        f"  (rounds ms: {_format_milliseconds(result.reader_times)})"
    )
    ratio_line = (
        f"  ratio cv2/reader = {result.ratio:.3f}"
        f"  {_BOUND_LABELS[mode]} {threshold:.2f}  -> {verdict}"
    )
    return "\n".join([header, cv2_line, reader_line, ratio_line])


def assert_gate(result: BenchResult, *, threshold: float = 1.0) -> None:
    """Print the report (always, so -s shows it) then assert the reader met the gate."""
    print(format_report(result, threshold=threshold))
    message = (
        f"{result.name}: reader slower than OpenCV -- "
        f"cv2 median {result.cv2_median * 1000:.1f} ms, "
        f"reader median {result.reader_median * 1000:.1f} ms, "
        f"ratio {result.ratio:.3f} < {threshold:.2f}. "
        "STOP: report these numbers to the reviewer; do not tune the threshold."
    )
    assert result.ratio >= threshold, message
