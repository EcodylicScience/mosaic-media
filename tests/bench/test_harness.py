"""Unit test for the benchmark engine itself.

Runs in the default suite -- it needs neither OpenCV nor the video corpus,
only a fake clock -- so the gate's median, interleave, and pass/fail logic is
always verified, even when the bench dependency group is not installed. It is
unmarked, so `-m bench` deselects it and `-m 'not bench'` (the default)
selects it.
"""

from __future__ import annotations

from collections import deque

import pytest

from tests.bench.harness import (
    BenchResult,
    Workload,
    assert_gate,
    format_report,
    run_workload,
)


class _FakeClock:
    """A clock advanced explicitly by the callables under test.

    The harness reads `now` at the start and end of each timed callable; the
    callable advances the clock by its scripted cost in between, so the
    observed elapsed time is exactly that cost, independent of call order.
    """

    def __init__(self) -> None:
        self._t: float = 0.0

    def now(self) -> float:
        return self._t

    def advance(self, delta: float) -> None:
        self._t += delta


def test_median_and_interleave_separate_sides_correctly() -> None:
    clock = _FakeClock()
    # Asymmetric costs: the cv2 median is 3.0 while the mean is 22.1, so a
    # mean substituted for the median fails loudly, and dropping any round
    # (a stray warmup) shifts the median off 3.0. Distinct reader costs make
    # a positional side assignment produce different lists than the
    # identity-based one, which the exact list assertions below pin.
    cv2_costs = deque([3.0, 1.0, 100.0, 4.0, 2.5])  # median 3.0, mean 22.1
    reader_costs = deque([0.5, 1.5, 1.0, 2.5, 0.25])  # median 1.0

    def cv2_callable(_context: object) -> str:
        clock.advance(cv2_costs.popleft())
        return "cv2"

    def reader_callable(_context: object) -> str:
        clock.advance(reader_costs.popleft())
        return "reader"

    workload = Workload(
        name="fake",
        setup=lambda: None,
        cv2_callable=cv2_callable,
        reader_callable=reader_callable,
    )
    result = run_workload(workload, rounds=5, timer=clock.now)

    assert result.cv2_times == [3.0, 1.0, 100.0, 4.0, 2.5]
    assert result.reader_times == [0.5, 1.5, 1.0, 2.5, 0.25]
    assert result.cv2_median == 3.0
    assert result.reader_median == 1.0
    assert result.ratio == 3.0
    assert result.passes()


def test_gate_fails_when_reader_slower() -> None:
    clock = _FakeClock()

    def cv2_callable(_context: object) -> str:
        clock.advance(1.0)
        return "cv2"

    def reader_callable(_context: object) -> str:
        clock.advance(2.0)
        return "reader"

    result = run_workload(
        Workload("fake", lambda: None, cv2_callable, reader_callable),
        rounds=5,
        timer=clock.now,
    )
    assert result.ratio == 0.5
    assert not result.passes()


def test_rounds_below_five_rejected() -> None:
    with pytest.raises(ValueError, match="at least 5 rounds"):
        _ = run_workload(
            Workload("fake", lambda: None, lambda _c: "x", lambda _c: "x"),
            rounds=4,
        )


def test_none_return_rejected() -> None:
    with pytest.raises(RuntimeError, match="returned None"):
        _ = run_workload(
            Workload("fake", lambda: None, lambda _c: None, lambda _c: "x"),
            rounds=5,
        )


def _result(ratio: float) -> BenchResult:
    """A result whose cv2/reader ratio is exactly `ratio`."""
    return BenchResult(name="fake", cv2_times=[ratio], reader_times=[1.0])


def test_gate_mode_reports_pass_and_fail_against_a_threshold() -> None:
    met = format_report(_result(1.5), threshold=1.0)
    missed = format_report(_result(0.5), threshold=1.0)
    assert "threshold 1.00  -> PASS" in met
    assert "threshold 1.00  -> FAIL" in missed


def test_assert_gate_raises_below_threshold_and_passes_above() -> None:
    assert_gate(_result(1.5), threshold=1.0)  # ratio above threshold: no raise
    with pytest.raises(AssertionError, match="reader slower than OpenCV"):
        assert_gate(_result(0.5), threshold=1.0)


def test_report_mode_never_says_fail() -> None:
    # A print-only report asserts on nothing, so a missed number must not read
    # as a failed run in the log -- that is a false alarm for whoever reads it.
    met = format_report(_result(1.5), threshold=1.0, mode="report")
    missed = format_report(_result(0.5), threshold=1.0, mode="report")
    assert "reference 1.00  -> OK" in met
    assert "reference 1.00  -> WARN" in missed
    for rendered in (met, missed):
        assert "FAIL" not in rendered
        assert "threshold" not in rendered
