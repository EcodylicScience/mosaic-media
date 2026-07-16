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

from tests.bench.harness import Workload, run_workload


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
    # Unsorted costs so this exercises the median, not the mean or an endpoint.
    cv2_costs = deque([3.0, 1.0, 5.0, 2.0, 4.0])  # median 3.0
    reader_costs = deque([1.0, 1.0, 1.0, 1.0, 1.0])  # median 1.0

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
