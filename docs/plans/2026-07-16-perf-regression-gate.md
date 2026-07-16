# Performance Regression Gate Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with a fresh implementer per task and a review between tasks. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the performance regression gate that proves
`mosaic_media.io.VideoReader` and `MultiVideoReader` decode at least as fast as
OpenCV on every consumer workflow in use today. Seven gated workload benchmarks
(one per consumer workflow) each assert `median(cv2)/median(reader) >= 1.0`,
plus a non-gating cold-random-seek report bounded at 2x OpenCV. `opencv-python`
is a test-only dependency in the `bench` group.

**Architecture:** One declarative measurement engine (`tests/bench/harness.py`)
runs interleaved cv2/reader rounds and gates on the ratio of medians. Every
workload is a `Workload(name, setup, cv2_callable, reader_callable)` sharing that
engine, so the seven workloads and the cold-seek report all reuse one runner. A
1080p H.264 corpus (GOP 12, GOP 250, and a rotation variant) is generated once
per session into a session-scoped temporary directory. Every benchmark is marked
`bench`, excluded from the default test run, and executed only serialized on an
otherwise idle machine.

**Tech Stack:** Python 3.12+, pytest with the `bench` marker,
`opencv-python>=4.7` and `numpy>=1.22` from the `bench` dependency group
(test-only, never runtime), system ffmpeg 6.1.1, and `tests/helpers/corpus.py`
for corpus generation. The engine itself imports only the standard library
(`statistics`, `time`, `dataclasses`).

## Global Constraints

- Benchmarks run ONLY serialized on an otherwise idle machine: on this machine `heavy uv run pytest -m bench -n0 -s`, never bare, never parallel (`-n0` forces one worker, `-s` shows the report, `heavy` serializes against other heavy jobs on the one global lock).
- The gate is `median(cv2_times) / median(reader_times) >= 1.0` per workload -- the reader must be at least as fast as OpenCV.
- `opencv-python` is test-only (the `bench` dependency group), never a runtime or dev dependency.
- ASCII only in code: `--`, `->`, `-` instead of box-drawing, arrows, or bullets.
- American spelling everywhere (`optimized`, `behavior`, `grayscale`, `color`).
- No `typing.Any`, no `typing.Optional` (use `X | None`), no `typing.cast`; no `# noqa`, no `# pyright: ignore`, no `# type: ignore` -- fix the design instead.
- No multi-line f-strings: assign a parenthesized adjacent-literal group to a variable first, then use it.
- Full identifier names, no unnecessary abbreviations.
- Commit messages in plain English: no conventional-commit prefixes (`feat:`, `fix:`, ...), no `Co-Authored-By` trailers, no process or tooling references in any committed artifact.

## Consumed interfaces (pinned)

These land in earlier efforts (scaffold-and-probe-extraction, frame-reader-io).
Consume them as written; do not redesign them here. If a benchmark reveals a
reader deficiency, that is a finding to report, not a change to make in this
plan (`src/` is out of scope).

- pyproject already provides: a `bench` dependency group containing
  `opencv-python>=4.7` and `numpy>=1.22`; pytest `addopts = "-m 'not bench'"`; a
  `bench` marker.
- `mosaic_media.io.VideoReader(path, *, start_frame=0, end_frame=None, frame_step=1, resize=None, grayscale=False, hwaccel=False, ...)`
  with properties `width`, `height`, `fps`, `frame_count`; methods
  `read() -> tuple[bool, numpy.ndarray | None]`,
  `read_batch(batch_size) -> tuple[numpy.ndarray, numpy.ndarray]`,
  `read_frames(indices) -> Iterator[tuple[int, numpy.ndarray]]` (sorted sparse,
  GOP-grouped), `seek(frame_index)`, `close()`; iterator and context-manager
  protocols. Construction also accepts injected probe facts -- the exact keyword
  is confirmed against the frame-reader-io plan's Interfaces block at
  implementation time (this plan assumes `facts=<MediaFacts>` and isolates it to
  a single call site in `support.make_reader`, so a differing keyword is a
  one-line change).
- `mosaic_media.io.MultiVideoReader(paths)` with `seek`, `read`, `total_frames`,
  and `close`.
- `mosaic_media.probe_media(path) -> MediaFacts`, and the `MediaFacts` type --
  both are exported from the top-level `mosaic_media` facade. The
  `mosaic_media.probe` subpackage's `__init__` is docstring-only, so import these
  two names from the facade (`from mosaic_media import MediaFacts, probe_media`),
  never from `mosaic_media.probe`.
- `tests/helpers/corpus.py`:
  `generate_video(path: Path, *, frames: int, fps: float = 30.0, gop: int = 30, size: tuple[int, int] = (320, 240), codec: str = "libx264", rotation_degrees: int = 0) -> Path`.

Imports follow the `tests.helpers.corpus` precedent established by the earlier
efforts (`from tests.helpers.corpus import generate_video`). Those efforts add an
`__init__.py` to every test directory so modules import as
`tests.<dir>.<module>`; this plan adds `tests/bench/__init__.py` for the same
reason, so `tests.bench.harness` and `tests.bench.support` import cleanly.

---

## Task 1: Benchmark engine and its unit test

Build the measurement engine first, driven by a unit test that proves the
median, interleave, and pass/fail logic with a fake clock. The engine imports
only the standard library, so this unit test runs in the default suite -- the
gate logic is verified even when the `bench` group is not installed.

**Files:**
- `tests/bench/__init__.py` (new, empty)
- `tests/bench/harness.py` (new)
- `tests/bench/test_harness.py` (new)

**Interfaces:**
- Consumes: nothing from `src/`. Pure standard library (`statistics`, `time`,
  `dataclasses`, `collections.abc.Callable`, `typing.Generic`, `typing.TypeVar`).
- Produces: `Workload`, `BenchResult`, `run_workload`, `time_series`,
  `format_report`, `assert_gate`, `assert_bounded`.

Steps:

- [ ] Create the empty package marker `tests/bench/__init__.py` so the bench
  modules import as `tests.bench.<module>`, matching the `__init__.py` the
  earlier efforts add to every test directory:

  ```bash
  touch tests/bench/__init__.py
  ```

- [ ] Write the unit test `tests/bench/test_harness.py` first. It is deliberately
  unmarked (no `bench` marker) and imports no OpenCV, so it runs in the default
  suite. The `_FakeClock` is advanced by the callables themselves, so the
  observed elapsed time equals each side's scripted cost regardless of call
  order -- which is exactly what proves the interleave assigns each timing to the
  correct side even though the round order alternates.

  ```python
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
          self._t = 0.0

      def now(self) -> float:
          return self._t

      def advance(self, delta: float) -> None:
          self._t += delta


  def test_median_and_interleave_separate_sides_correctly() -> None:
      clock = _FakeClock()
      # Unsorted costs so this exercises the median, not the mean or an endpoint.
      cv2_costs = deque([3.0, 1.0, 5.0, 2.0, 4.0])     # median 3.0
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
          run_workload(
              Workload("fake", lambda: None, lambda _c: "x", lambda _c: "x"),
              rounds=4,
          )


  def test_none_return_rejected() -> None:
      with pytest.raises(RuntimeError, match="returned None"):
          run_workload(
              Workload("fake", lambda: None, lambda _c: None, lambda _c: "x"),
              rounds=5,
          )
  ```

- [ ] Run the unit test and confirm it fails because the engine does not exist
  yet:

  ```bash
  uv run pytest tests/bench/test_harness.py -q
  ```

  Expected: a collection/import error (`ModuleNotFoundError: tests.bench.harness`)
  or `ERROR ... No module named`.

- [ ] Implement `tests/bench/harness.py` to satisfy the unit test:

  ```python
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
  from typing import Generic, TypeVar

  DEFAULT_ROUNDS = 5

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


  def format_report(result: BenchResult, *, threshold: float = 1.0) -> str:
      verdict = "PASS" if result.ratio >= threshold else "FAIL"
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
          f"  threshold {threshold:.2f}  -> {verdict}"
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


  def assert_bounded(result: BenchResult, *, max_slowdown: float = 2.0) -> None:
      """Print the report and assert the reader stays within max_slowdown x OpenCV.

      Non-gating parity. See the cold-seek test docstring for why isolated random
      single-frame seeks are bounded rather than gated at parity.
      """
      minimum_ratio = 1.0 / max_slowdown
      print(format_report(result, threshold=minimum_ratio))
      message = (
          f"{result.name}: exceeded the documented {max_slowdown:.0f}x cold-seek "
          f"bound -- cv2 median {result.cv2_median * 1000:.1f} ms, "
          f"reader median {result.reader_median * 1000:.1f} ms, "
          f"ratio {result.ratio:.3f} < {minimum_ratio:.3f}. "
          "STOP: report these numbers to the reviewer."
      )
      assert result.ratio >= minimum_ratio, message
  ```

- [ ] Run the unit test and confirm it passes:

  ```bash
  uv run pytest tests/bench/test_harness.py -q
  ```

  Expected: `4 passed`.

- [ ] Format and lint the two new files:

  ```bash
  uv run ruff format tests/bench/harness.py tests/bench/test_harness.py
  uv run ruff check tests/bench/harness.py tests/bench/test_harness.py
  ```

  Expected: `All checks passed!` (or a re-format, then clean).

- [ ] Commit this task with a plain-English message (no conventional-commit
  prefix, no trailers):

  ```bash
  git add tests/bench/__init__.py tests/bench/harness.py tests/bench/test_harness.py
  git commit -m "Add the benchmark measurement engine and its unit test"
  ```

---

## Task 2: Shared support module, corpus fixture, and corpus smoke test

Add the constants and helpers the workloads share, the session-scoped corpus
fixture, and a `bench`-marked smoke test that isolates corpus and probe wiring
from reader performance.

**Files:**
- `tests/bench/support.py` (new)
- `tests/bench/conftest.py` (new)
- `tests/bench/test_corpus_smoke.py` (new)

**Interfaces:**
- Consumes: `mosaic_media.probe_media`, `mosaic_media.MediaFacts` (the top-level
  facade); `mosaic_media.io.VideoReader`; `tests.helpers.corpus.generate_video`.
- Produces: `BENCH_FPS`, `BENCH_FRAMES`, `BENCH_SIZE`, `CV2_IMPORTORSKIP_REASON`,
  `make_reader`, `cv2_read_targets`, `reader_read_targets`, `sample_sorted`,
  `sample_shuffled`; the `bench_corpus` fixture.

Steps:

- [ ] Write `tests/bench/support.py`. No OpenCV is imported at module level, so
  the module is safe to import in the default suite; `cv2_read_targets` imports
  `cv2` inside its own body. `make_reader` is the single injection seam that
  names the injected-facts keyword. `cv2_read_targets` and `reader_read_targets`
  are the seek-one-target-then-read-one-frame loops shared by the monotonic-seek,
  sparse (cv2 side only), and cold-seek workloads.

  ```python
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

      Injection keyword: this passes facts=facts. Confirm the exact parameter
      name against the frame-reader-io plan's Interfaces block at implementation
      time; this is the single line to change if it differs, because every
      benchmark opens readers through this helper.
      """
      from mosaic_media.io import VideoReader

      return VideoReader(path, facts=facts, frame_step=frame_step)


  def cv2_read_targets(path: Path, targets: list[int]) -> int:
      """Seek to each target with CAP_PROP_POS_FRAMES and read one frame.

      The OpenCV baseline shared by the monotonic-seek, sparse-extraction, and
      cold-seek workloads: one CAP_PROP_POS_FRAMES seek plus one read per target,
      re-decoding the GOP chain from the preceding keyframe every time. cv2 is
      imported inside the body so this module stays importable without OpenCV.
      Returns the number of frames read (never None, so the gate cannot be
      passed by a no-op).
      """
      import cv2

      capture = cv2.VideoCapture(str(path))
      decoded_count = 0
      try:
          for target in targets:
              capture.set(cv2.CAP_PROP_POS_FRAMES, target)
              ok, frame = capture.read()
              if not ok or frame is None:
                  break
              decoded_count += 1
      finally:
          capture.release()
      return decoded_count


  def reader_read_targets(path: Path, facts: MediaFacts, targets: list[int]) -> int:
      """Seek to each target with the reader and read one frame.

      The reader counterpart to cv2_read_targets, shared by the monotonic-seek
      and cold-seek workloads. The reader chooses discard-versus-respawn per seek
      from the packet index; the workload's target ordering (monotonic forward
      vs shuffled) decides which path dominates.
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
  ```

- [ ] Write `tests/bench/conftest.py`. Its module docstring is the authoritative
  statement of the run condition. The corpus fixture is session-scoped and
  generates only when a benchmark requests it.

  ```python
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
              frames=BENCH_FRAMES, fps=BENCH_FPS, gop=12, size=BENCH_SIZE,
          ),
          "gop250": generate_video(
              directory / "gop250.mp4",
              frames=BENCH_FRAMES, fps=BENCH_FPS, gop=250, size=BENCH_SIZE,
          ),
          "rotation": generate_video(
              directory / "rotation.mp4",
              frames=BENCH_FRAMES, fps=BENCH_FPS, gop=12, size=BENCH_SIZE,
              rotation_degrees=90,
          ),
      }
  ```

- [ ] Write `tests/bench/test_corpus_smoke.py`. Marked `bench` because it
  generates the corpus. It confirms each file exists and probes to the expected
  shape, isolating corpus/probe failures from reader performance.

  ```python
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
              f"{corpus_key}: probed {facts.frame_count} frames, "
              f"expected {BENCH_FRAMES}"
          )
          assert facts.frame_count == BENCH_FRAMES, frame_count_message
          assert (facts.width, facts.height) == BENCH_SIZE
  ```

  Note: this is the one place that reads `MediaFacts` field names directly
  (`frame_count`, `width`, `height`). If the probe module names them
  differently, this test fails loudly here -- which is precisely the isolation
  this smoke test provides. Confirm the field names against `probe/facts.py` and
  adjust if needed. The workloads never touch `MediaFacts` fields; they pass the
  facts object through `make_reader` and take frame offsets from `BENCH_FRAMES`.

- [ ] Do not exclude `tests/bench` from the type checker. `opencv-python` 4.7+
  ships type stubs, so the bench tree type-checks when the `bench` group is
  present -- run basedpyright with the bench group synced (next step). This
  matches the pattern the earlier efforts established for their cv2-using tests
  and keeps basedpyright scope over all of `tests/`, per the repo rule. Make no
  edit to `[tool.basedpyright]`.

- [ ] Confirm the whole tree type-checks, including `tests/bench`, with the bench
  group so `cv2` and its stubs resolve:

  ```bash
  uv run --group bench basedpyright src/ tests/
  ```

  Expected: `0 errors, 0 warnings`.

- [ ] Run the corpus smoke test to confirm generation and probing work
  end-to-end. This generates the 1080p corpus, so it goes through `heavy`:

  ```bash
  heavy uv run pytest tests/bench/test_corpus_smoke.py -m bench -n0 -s
  ```

  Expected: `1 passed`. If corpus generation is slow, that is expected for 1080p.

- [ ] Commit this task with a plain-English message (no conventional-commit
  prefix, no trailers):

  ```bash
  git add tests/bench/support.py tests/bench/conftest.py tests/bench/test_corpus_smoke.py
  git commit -m "Add the bench corpus fixture, shared helpers, and a corpus smoke test"
  ```

---

## Task 3: Sequential full decode and strided decode

The first two gated workloads. Both mirror the tracking inference decode paths
in `mosaic/core/media/video_io.py` and `mosaic/tracking/pose_training/inference.py`.

**Files:**
- `tests/bench/test_sequential_decode.py` (new)

**Interfaces:**
- Consumes: `VideoReader.read`, `VideoReader(frame_step=...)`, context-manager
  protocol; `cv2.VideoCapture.read`. Uses `Workload`, `run_workload`,
  `assert_gate` from the engine; `make_reader` and `CV2_IMPORTORSKIP_REASON` from
  support; `probe_media` and `MediaFacts` from the `mosaic_media` facade.

Workload mapping:
- `sequential-full-decode` mirrors the tracking inference main path: decode
  every frame in order (the `FFmpegFrameReader` / `extract_candidate_features`
  read loop).
- `strided-decode` mirrors inference batch stepping
  (`extract_candidate_features` with `candidate_step`): OpenCV has no native
  stride, so the consumer decodes every frame and discards the ones it does not
  want (`(frame_idx - start_frame) % step`); the reader uses `frame_step` to
  avoid transferring the discarded frames over the pipe.

Steps:

- [ ] Write `tests/bench/test_sequential_decode.py`:

  ```python
  """Gated workloads: sequential full decode and strided decode.

  - sequential-full-decode mirrors the tracking inference main path
    (mosaic FFmpegFrameReader / extract_candidate_features read loop): decode
    every frame in order.
  - strided-decode mirrors inference batch stepping (extract_candidate_features
    with candidate_step): OpenCV has no native stride, so the consumer decodes
    every frame and discards the ones it does not want; the reader uses
    frame_step to avoid transferring the discarded frames.
  """
  from __future__ import annotations

  from pathlib import Path

  import pytest

  from mosaic_media import MediaFacts, probe_media
  from tests.bench.harness import Workload, assert_gate, run_workload
  from tests.bench.support import CV2_IMPORTORSKIP_REASON, make_reader

  cv2 = pytest.importorskip("cv2", reason=CV2_IMPORTORSKIP_REASON)
  pytestmark = pytest.mark.bench

  STRIDE = 5


  def _cv2_sequential(path: Path) -> int:
      capture = cv2.VideoCapture(str(path))
      count = 0
      try:
          while True:
              ok, frame = capture.read()
              if not ok or frame is None:
                  break
              count += 1
      finally:
          capture.release()
      return count


  def _reader_sequential(path: Path, facts: MediaFacts) -> int:
      count = 0
      with make_reader(path, facts) as reader:
          while True:
              ok, frame = reader.read()
              if not ok or frame is None:
                  break
              count += 1
      return count


  def _cv2_strided(path: Path, step: int) -> int:
      capture = cv2.VideoCapture(str(path))
      kept = 0
      index = 0
      try:
          while True:
              ok, frame = capture.read()
              if not ok or frame is None:
                  break
              if index % step == 0:
                  kept += 1
              index += 1
      finally:
          capture.release()
      return kept


  def _reader_strided(path: Path, facts: MediaFacts, step: int) -> int:
      kept = 0
      with make_reader(path, facts, frame_step=step) as reader:
          while True:
              ok, frame = reader.read()
              if not ok or frame is None:
                  break
              kept += 1
      return kept


  @pytest.mark.parametrize("corpus_key", ["gop12", "gop250", "rotation"])
  def test_gate_sequential_full_decode(
      bench_corpus: dict[str, Path], corpus_key: str
  ) -> None:
      path = bench_corpus[corpus_key]
      workload = Workload(
          name=f"sequential-full-decode[{corpus_key}]",
          setup=lambda: probe_media(path),
          cv2_callable=lambda _facts: _cv2_sequential(path),
          reader_callable=lambda facts: _reader_sequential(path, facts),
      )
      assert_gate(run_workload(workload))


  @pytest.mark.parametrize("corpus_key", ["gop12", "gop250"])
  def test_gate_strided_decode(bench_corpus: dict[str, Path], corpus_key: str) -> None:
      path = bench_corpus[corpus_key]
      workload = Workload(
          name=f"strided-decode-step{STRIDE}[{corpus_key}]",
          setup=lambda: probe_media(path),
          cv2_callable=lambda _facts: _cv2_strided(path, STRIDE),
          reader_callable=lambda facts: _reader_strided(path, facts, STRIDE),
      )
      assert_gate(run_workload(workload))
  ```

- [ ] Run this file's workloads once, serialized, and record the observed ratio
  for each parametrization:

  ```bash
  heavy uv run pytest tests/bench/test_sequential_decode.py -m bench -n0 -s
  ```

  Expected output shape (numbers are hardware-dependent; the point is the report
  lines and the PASS verdict):

  ```
  [bench] sequential-full-decode[gop12]
    cv2    median   3180.4 ms  (rounds ms: 3210.1, 3180.4, 3175.9, 3190.2, 3160.8)
    reader median   2840.7 ms  (rounds ms: 2861.0, 2840.7, 2830.4, 2852.1, 2835.9)
    ratio cv2/reader = 1.120  threshold 1.00  -> PASS
  ...
  5 passed
  ```

  If any parametrization prints `-> FAIL` and the test fails, apply the STOP
  instruction in Task 8 -- do not adjust the benchmark.

- [ ] Format and lint:

  ```bash
  uv run ruff format tests/bench/test_sequential_decode.py
  uv run ruff check tests/bench/test_sequential_decode.py
  ```

- [ ] Commit this task with a plain-English message (no conventional-commit
  prefix, no trailers):

  ```bash
  git add tests/bench/test_sequential_decode.py
  git commit -m "Add sequential and strided decode gate benchmarks"
  ```

---

## Task 4: Seek-to-start then sequential, and monotonic strided seeks

**Files:**
- `tests/bench/test_seek_workloads.py` (new)

**Interfaces:**
- Consumes: `VideoReader.seek`, `VideoReader.read`, context-manager protocol;
  `cv2.VideoCapture.set(cv2.CAP_PROP_POS_FRAMES, ...)` and `read`. The
  seek-then-sequential workload keeps its own read loop (a single seek then a
  sequential run); the monotonic workload drives both sides through support's
  shared `cv2_read_targets` / `reader_read_targets`, with the arithmetic targets
  built in setup. Imports `probe_media`, `MediaFacts` from the `mosaic_media`
  facade and `CV2_IMPORTORSKIP_REASON` from support.

Workload mapping:
- `seek-then-sequential` mirrors `video_stream.py`'s `_FrameStream`: seek to the
  start frame once (`if self._start > 0: self._reader.seek(self._start)`), then
  read sequentially. Read 200 frames from mid-file.
- `monotonic-strided-seeks` mirrors the pose visualization loop in
  `tracking/pose_training/inference.py`, which for each successive result
  computes a target frame `start_frame + result_index * frame_step`, seeks the
  capture to it via `CAP_PROP_POS_FRAMES`, and reads one frame. A small
  monotonically increasing step keeps the target within the current
  keyframe-to-position window, so the reader's read-and-discard path engages
  rather than respawning ffmpeg per seek. Step 3 over 50 seeks: on GOP 250 the
  whole span stays inside one GOP (heavy discard-path use); on GOP 12 it crosses
  a keyframe every few seeks (a mix of discard and respawn) -- both are
  informative.

Steps:

- [ ] Write `tests/bench/test_seek_workloads.py`:

  ```python
  """Gated workloads: seek-to-start then sequential, and monotonic strided seeks.

  - seek-then-sequential mirrors video_stream.py's _FrameStream: seek to the
    start frame once, then read sequentially.
  - monotonic-strided-seeks mirrors the pose visualization loop in
    tracking/pose_training/inference.py, which for each successive result seeks
    the capture to start_frame + result_index * frame_step via
    CAP_PROP_POS_FRAMES and reads one frame. A small monotonically increasing
    step keeps consecutive targets inside the current keyframe-to-position
    window, so the reader discards forward on the live process rather than
    respawning per seek.
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
      make_reader,
      reader_read_targets,
  )

  cv2 = pytest.importorskip("cv2", reason=CV2_IMPORTORSKIP_REASON)
  pytestmark = pytest.mark.bench

  SEEK_START_READ_COUNT = 200
  MONOTONIC_SEEKS = 50
  MONOTONIC_STEP = 3


  def _cv2_seek_then_sequential(path: Path, start: int, count: int) -> int:
      capture = cv2.VideoCapture(str(path))
      read = 0
      try:
          capture.set(cv2.CAP_PROP_POS_FRAMES, start)
          while read < count:
              ok, frame = capture.read()
              if not ok or frame is None:
                  break
              read += 1
      finally:
          capture.release()
      return read


  def _reader_seek_then_sequential(
      path: Path, facts: MediaFacts, start: int, count: int
  ) -> int:
      read = 0
      with make_reader(path, facts) as reader:
          reader.seek(start)
          while read < count:
              ok, frame = reader.read()
              if not ok or frame is None:
                  break
              read += 1
      return read


  @pytest.mark.parametrize("corpus_key", ["gop12", "gop250"])
  def test_gate_seek_then_sequential(
      bench_corpus: dict[str, Path], corpus_key: str
  ) -> None:
      path = bench_corpus[corpus_key]
      start = BENCH_FRAMES // 2
      workload = Workload(
          name=f"seek-then-sequential[{corpus_key}]",
          setup=lambda: probe_media(path),
          cv2_callable=lambda _facts: _cv2_seek_then_sequential(
              path, start, SEEK_START_READ_COUNT
          ),
          reader_callable=lambda facts: _reader_seek_then_sequential(
              path, facts, start, SEEK_START_READ_COUNT
          ),
      )
      assert_gate(run_workload(workload))


  @pytest.mark.parametrize("corpus_key", ["gop12", "gop250"])
  def test_gate_monotonic_strided_seeks(
      bench_corpus: dict[str, Path], corpus_key: str
  ) -> None:
      path = bench_corpus[corpus_key]
      start = BENCH_FRAMES // 4
      # The monotonic targets are a plain arithmetic progression; compute them
      # once in setup so the shared seek-one-read-one loops (cv2_read_targets /
      # reader_read_targets) drive both sides.
      targets = [start + index * MONOTONIC_STEP for index in range(MONOTONIC_SEEKS)]

      def setup() -> MediaFacts:
          return probe_media(path)

      workload = Workload(
          name=f"monotonic-strided-seeks[{corpus_key}]",
          setup=setup,
          cv2_callable=lambda _facts: cv2_read_targets(path, targets),
          reader_callable=lambda facts: reader_read_targets(path, facts, targets),
      )
      assert_gate(run_workload(workload))
  ```

- [ ] Run and record the observed ratios:

  ```bash
  heavy uv run pytest tests/bench/test_seek_workloads.py -m bench -n0 -s
  ```

  Expected: four report blocks, each ending `-> PASS`, then `4 passed`. On any
  `-> FAIL`, apply the Task 8 STOP instruction.

- [ ] Format and lint:

  ```bash
  uv run ruff format tests/bench/test_seek_workloads.py
  uv run ruff check tests/bench/test_seek_workloads.py
  ```

- [ ] Commit this task with a plain-English message (no conventional-commit
  prefix, no trailers):

  ```bash
  git add tests/bench/test_seek_workloads.py
  git commit -m "Add seek-to-start and monotonic strided seek gate benchmarks"
  ```

---

## Task 5: Sorted-sparse extraction and multi-video junction read

**Files:**
- `tests/bench/test_sparse_and_multi.py` (new)

**Interfaces:**
- Consumes: `VideoReader.read_frames(indices) -> Iterator[tuple[int, ndarray]]`;
  `MultiVideoReader(paths)` with `seek`, `read`, `close`; `cv2.VideoCapture`.

Workload mapping:
- `sorted-sparse-extraction` mirrors `save_frames_as_png`: extract a set of
  sorted target frames. OpenCV seeks (`CAP_PROP_POS_FRAMES`) and reads once per
  target, re-decoding the GOP chain each time; the reader groups targets by GOP
  via the packet index and decodes each GOP once (`read_frames`). 20 sorted
  random targets.
- `multi-video-junction` mirrors `MultiVideoReader` consumers (`render_stream`):
  read across the boundary between two files. The OpenCV baseline opens two
  captures and stitches them by hand at the junction; the reader presents one
  global frame space. Both open fresh each round, so the reader's per-open
  packet scan is inside the timed region -- the honest from-scratch comparison.
  The two segments reuse the GOP-12 file (the second segment starts at global
  frame `BENCH_FRAMES`); the junction read decodes 100 frames on each side of the
  boundary, so decoding dominates the two opens.

Steps:

- [ ] Write `tests/bench/test_sparse_and_multi.py`:

  ```python
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
  from tests.bench.harness import Workload, assert_gate, run_workload
  from tests.bench.support import (
      BENCH_FRAMES,
      CV2_IMPORTORSKIP_REASON,
      cv2_read_targets,
      make_reader,
      sample_sorted,
  )

  cv2 = pytest.importorskip("cv2", reason=CV2_IMPORTORSKIP_REASON)
  pytestmark = pytest.mark.bench

  SPARSE_COUNT = 20
  SPARSE_SEED = 20260716
  JUNCTION_WINDOW = 100


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
      assert_gate(run_workload(workload))


  def test_gate_multi_video_junction(bench_corpus: dict[str, Path]) -> None:
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
      assert_gate(run_workload(workload))
  ```

- [ ] Run and record the observed ratios:

  ```bash
  heavy uv run pytest tests/bench/test_sparse_and_multi.py -m bench -n0 -s
  ```

  Expected: three report blocks, each ending `-> PASS`, then `3 passed`. On any
  `-> FAIL`, apply the Task 8 STOP instruction.

- [ ] Format and lint:

  ```bash
  uv run ruff format tests/bench/test_sparse_and_multi.py
  uv run ruff check tests/bench/test_sparse_and_multi.py
  ```

- [ ] Commit this task with a plain-English message (no conventional-commit
  prefix, no trailers):

  ```bash
  git add tests/bench/test_sparse_and_multi.py
  git commit -m "Add sparse extraction and multi-video junction gate benchmarks"
  ```

---

## Task 6: Metadata read (gated) and probe cost (non-gating report)

**Files:**
- `tests/bench/test_metadata.py` (new)

**Interfaces:**
- Consumes: `VideoReader` properties `width`, `height`, `fps`, `frame_count`,
  `close`, construction WITH injected facts; `probe_media`;
  `cv2.VideoCapture.get(cv2.CAP_PROP_*)`. Uses `time_series` from the engine.

Workload mapping:
- `metadata-open` mirrors `get_video_metadata`: open, read
  width/height/fps/frame count, close. The reader is constructed WITH injected
  facts (consumers hold `MediaFacts`; "measurement is not re-derived"), so it
  answers metadata without re-probing -- this respects the invariant and does
  not benchmark `probe_media` as if consumers re-probe per open.
- `probe-cost` is reported separately and NOT gated: the probe is a one-time
  per-file-lifetime cost by design -- the measurement travels forward with the
  file and consumers do not re-probe per open. It is reported for visibility
  only.

Steps:

- [ ] Write `tests/bench/test_metadata.py`. The probe-cost loop binds `path` as a
  lambda default argument so it captures the current file, not the loop variable.

  ```python
  """Gated workload: metadata/open overhead; plus a non-gating probe-cost report.

  - metadata-open mirrors get_video_metadata: open, read width/height/fps/frame
    count, close. The reader is constructed WITH injected facts (consumers hold
    MediaFacts; 'measurement is not re-derived'), so it answers metadata without
    re-probing.
  - probe-cost is reported separately and NOT gated: the probe is a one-time
    per-file-lifetime cost by design -- the measurement travels forward with the
    file and consumers do not re-probe per open. Benchmarking probe_media as if
    consumers re-probe every open would contradict that invariant, so it is
    reported for visibility only.
  """
  from __future__ import annotations

  import statistics
  from pathlib import Path

  import pytest

  from mosaic_media import MediaFacts, probe_media
  from tests.bench.harness import Workload, assert_gate, run_workload, time_series
  from tests.bench.support import CV2_IMPORTORSKIP_REASON, make_reader

  cv2 = pytest.importorskip("cv2", reason=CV2_IMPORTORSKIP_REASON)
  pytestmark = pytest.mark.bench


  def _cv2_metadata(path: Path) -> int:
      capture = cv2.VideoCapture(str(path))
      try:
          width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
          height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
          fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
          frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
      finally:
          capture.release()
      return width + height + int(fps) + frame_count


  def _reader_metadata(path: Path, facts: MediaFacts) -> int:
      reader = make_reader(path, facts)
      try:
          total = reader.width + reader.height + int(reader.fps) + reader.frame_count
      finally:
          reader.close()
      return total


  @pytest.mark.parametrize("corpus_key", ["gop12", "gop250", "rotation"])
  def test_gate_metadata_open(bench_corpus: dict[str, Path], corpus_key: str) -> None:
      path = bench_corpus[corpus_key]
      workload = Workload(
          name=f"metadata-open[{corpus_key}]",
          setup=lambda: probe_media(path),
          cv2_callable=lambda _facts: _cv2_metadata(path),
          reader_callable=lambda facts: _reader_metadata(path, facts),
      )
      assert_gate(run_workload(workload))


  def test_report_probe_cost(bench_corpus: dict[str, Path]) -> None:
      """Report the one-time probe cost per file. Non-gating by design."""
      for corpus_key in ("gop12", "gop250", "rotation"):
          path = bench_corpus[corpus_key]
          times = time_series(lambda captured=path: probe_media(captured))
          median_ms = statistics.median(times) * 1000.0
          message = (
              f"[bench] probe-cost[{corpus_key}] median {median_ms:.1f} ms "
              "(one-time per file lifetime; measurement travels forward, "
              "consumers do not re-probe per open)"
          )
          print(message)
  ```

- [ ] Run and record. The metadata gate should pass comfortably because the
  reader with injected facts does no work at open, while cv2 reads the container
  header:

  ```bash
  heavy uv run pytest tests/bench/test_metadata.py -m bench -n0 -s
  ```

  Expected: three `metadata-open` report blocks ending `-> PASS`, three
  `[bench] probe-cost[...]` lines, then `4 passed`. The probe-cost lines are
  informational and never fail. On any metadata `-> FAIL`, apply the Task 8 STOP
  instruction.

- [ ] Format and lint:

  ```bash
  uv run ruff format tests/bench/test_metadata.py
  uv run ruff check tests/bench/test_metadata.py
  ```

- [ ] Commit this task with a plain-English message (no conventional-commit
  prefix, no trailers):

  ```bash
  git add tests/bench/test_metadata.py
  git commit -m "Add metadata-open gate benchmark and probe-cost report"
  ```

---

## Task 7: Cold single random seek report (non-gating)

**Files:**
- `tests/bench/test_cold_seek.py` (new)

**Interfaces:**
- Consumes: `assert_bounded`, `run_workload`, `Workload` from the engine;
  `cv2_read_targets`, `reader_read_targets`, `sample_shuffled`, and
  `CV2_IMPORTORSKIP_REASON` from support (both sides run through the shared
  seek-one-read-one loops); `probe_media`, `MediaFacts` from the `mosaic_media`
  facade.

This is the only non-gating video benchmark. It reports 20 uniform random
single-frame seeks on both GOP files against a documented bound of at most 2x
OpenCV, and fails only if that bound is exceeded. The targets are non-monotonic
(shuffled), so each seek is effectively isolated -- consecutive targets jump
around, forcing the reader to respawn ffmpeg rather than reuse a warm decode
position.

Steps:

- [ ] Write `tests/bench/test_cold_seek.py`. The docstring states the
  bounded-not-gated rationale, as required.

  ```python
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
          reader_callable=lambda context: reader_read_targets(path, context[0], context[1]),
      )
      assert_bounded(run_workload(workload), max_slowdown=2.0)
  ```

- [ ] Run and record the observed ratios. This is expected to print a ratio
  below 1.0 (the reader is slower on isolated cold seeks) but at or above 0.5
  (within the 2x bound), so it PASSES:

  ```bash
  heavy uv run pytest tests/bench/test_cold_seek.py -m bench -n0 -s
  ```

  Expected: two report blocks. A representative one:

  ```
  [bench] cold-random-seek[gop12]
    cv2    median     52.3 ms  (rounds ms: ...)
    reader median     81.7 ms  (rounds ms: ...)
    ratio cv2/reader = 0.640  threshold 0.50  -> PASS
  ...
  2 passed
  ```

  If the ratio drops below 0.50 (`-> FAIL`, reader more than 2x slower than
  OpenCV), apply the Task 8 STOP instruction -- that is a real regression signal.

- [ ] Format and lint:

  ```bash
  uv run ruff format tests/bench/test_cold_seek.py
  uv run ruff check tests/bench/test_cold_seek.py
  ```

- [ ] Commit this task with a plain-English message (no conventional-commit
  prefix, no trailers):

  ```bash
  git add tests/bench/test_cold_seek.py
  git commit -m "Add the cold random seek report"
  ```

---

## Task 8: Full gate run and wiring verification

Verify the pyproject wiring the earlier efforts provide, then run the whole gate
serialized and confirm every gate is green.

**Files:**
- none created; verification and the full run only.

**Interfaces:**
- Consumes: the whole `tests/bench/` tree; the `bench` marker, `bench`
  dependency group, and `addopts = "-m 'not bench'"` from pyproject.

Steps:

- [ ] Verify the pyproject wiring is present (do not recreate it; it is provided
  by the scaffold effort). Confirm each of these:
  - `[dependency-groups] bench` contains `opencv-python>=4.7` and `numpy>=1.22`.
  - `[tool.pytest.ini_options] addopts` includes `-m 'not bench'`.
  - the `bench` marker is registered under `markers`.

  ```bash
  uv run python -c "import tomllib, pathlib; data = tomllib.loads(pathlib.Path('pyproject.toml').read_text()); print('bench group:', data['dependency-groups']['bench']); print('addopts:', data['tool']['pytest']['ini_options']['addopts'])"
  ```

  Expected: the bench group lists opencv-python and numpy; addopts contains
  `-m 'not bench'`.

- [ ] Confirm the bench dependency group is installed:

  ```bash
  uv sync --group bench
  ```

- [ ] Confirm the default suite is unaffected: the harness unit test runs, the
  bench workloads are excluded, and no bench corpus is generated:

  ```bash
  uv run pytest tests/bench -q
  ```

  Expected: `test_harness.py`'s `4 passed`, and the `bench`-marked modules
  reported as deselected (the run does not generate the 1080p corpus).

- [ ] Run the entire gate, serialized, on an otherwise idle machine, and confirm
  every gated workload prints `-> PASS`:

  ```bash
  heavy uv run pytest -m bench -n0 -s
  ```

  Expected: report blocks for all seven gated workloads across their
  parametrizations (sequential x3, strided x2, seek-then-sequential x2,
  monotonic-strided-seeks x2, sorted-sparse x2, multi-video-junction x1,
  metadata-open x3), each ending `-> PASS`; the probe-cost lines; the two
  cold-random-seek report blocks ending `-> PASS`; the corpus smoke test; and a
  final `N passed` with zero failures. Expect the full session to take several
  minutes -- it decodes 1080p corpus repeatedly and is meant to be run
  explicitly, not in a fast feedback loop.

- [ ] If any gated workload fails the gate (or the cold-seek bound is exceeded),
  reproduce this instruction exactly:

  > STOP. Do not tune the threshold, shorten the workload, reduce the corpus,
  > add a warm-up round, or otherwise adjust the benchmark to make it pass. A
  > failing gate means the reader is slower than OpenCV on a real consumer
  > workflow, and a failing cold-seek bound means the reader exceeded its
  > documented 2x ceiling. Record the workload name and the printed cv2 median,
  > reader median, and ratio, and report them to the reviewer. A reader
  > deficiency is a finding for the reviewer, not a task in this plan -- `src/`
  > is out of scope here.

- [ ] Final full-project checks (with the bench group synced so `tests/bench`
  imports and its `cv2` stubs resolve for the type checker):

  ```bash
  uv run ruff format src/ tests/
  uv run ruff check src/ tests/
  uv run --group bench basedpyright src/ tests/
  heavy uv run pytest tests/
  ```

  Expected: ruff clean; basedpyright `0 errors` over all of `src/` and `tests/`
  including `tests/bench`; and the default `heavy uv run pytest tests/` green
  (bench excluded by `addopts`, the harness unit test included).

- [ ] Commit any residual formatting changes from the final checks with a
  plain-English message (no conventional-commit prefix, no trailers). The
  `git diff --quiet` gate skips the commit when the tree is clean and, unlike
  `|| true`, does not mask a real commit failure:

  ```bash
  git diff --quiet || git commit -am "Format the benchmark suite"
  ```

---

## Self-review

1. **All seven spec workloads plus the cold-seek report have tasks.** Sequential
   full decode and strided decode (Task 3); seek-to-start then sequential and
   monotonic strided seeks (Task 4); sorted-sparse extraction and multi-video
   junction (Task 5); metadata read (Task 6, plus the non-gating probe-cost
   report); cold single random seek (Task 7, non-gating, fails only above 2x).
   The engine and its fake-timer unit test are Task 1; corpus, support, and the
   smoke test are Task 2; the full serialized run with the verbatim STOP
   instruction is Task 8.

2. **Placeholder scan.** Every code block is complete: no `...`, no `TODO`, no
   pseudo-code. The single deliberately-flagged line is the injected-facts
   keyword in `support.make_reader` (`facts=facts`), isolated to one call site
   with an explicit "confirm against the frame-reader-io plan" note, as the brief
   requires.

3. **Signature consistency with the pinned contracts.** `VideoReader` is opened
   only through `support.make_reader` (injected facts, optional `frame_step`);
   workloads use `read`, `read_frames`, `seek`, `close`, properties
   `width/height/fps/frame_count`, and the context-manager protocol exactly as
   pinned. `MultiVideoReader(paths)` uses `seek`, `read`, `close`. `probe_media`
   returns `MediaFacts`, read only in the smoke test. `run_workload` gates on
   `median(cv2)/median(reader) >= 1.0` over interleaved rounds (>= 5), matching
   the spec.
