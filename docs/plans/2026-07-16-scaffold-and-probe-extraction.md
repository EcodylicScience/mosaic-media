# Scaffold and Probe Extraction Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with a fresh implementer per task and a review between tasks. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `mosaic-media` package and populate its standard-library-only core -- the probe subpackage, the thumbnail subpackage, and the ffmpeg/hardware capability probes -- as a verbatim, independently tested duplication of `mosaic_api`'s `media_probe`, leaving `mosaic_api` untouched.

**Architecture:** A `uv_build` package with import name `mosaic_media`. A top-level facade (`__init__.py`) is the only public import path. Below it sit three standard-library-only layers built in this plan: `probe/` (measurement and verdict), `thumbnail/` (still-image derivatives), and `hwaccel.py` (cached ffmpeg capability probes). The `[io]` (numpy) reader, `[cli]` (typer) app, and `transcode/` execution layer are declared in packaging but built by sibling plans.

**Tech Stack:** Python 3.12+, uv (package manager and build backend `uv_build`), system ffmpeg/ffprobe on PATH, ruff, basedpyright, pytest with pytest-xdist. No numpy, typer, or OpenCV in the core.

## Global Constraints

- Python floor is 3.12 (the lower of the two consumers), never 3.13.
- The core has zero runtime dependencies: `dependencies = []`; `probe`, `thumbnail`, and `hwaccel` import the standard library only.
- ASCII-only code: no unicode arrows, box-drawing, or bullets. Use `->`, `--`, `-`.
- American spelling everywhere (`behavior`, `color`, `gray`, `catalog`).
- No `typing.Any`, no `typing.Optional` (use `X | None`), no `typing.cast`, no `# noqa`, no `# pyright: ignore` -- fix the design instead.
- No multi-line f-strings: assign the message to a variable first, then use it.
- No bare adjacent string literals as call arguments: parenthesize and assign to a variable first.
- Full identifier names, no abbreviations.
- One-way layering: `cli` -> `io`/`transcode`/`thumbnail`/`probe`/`hwaccel`; `io` and `transcode` -> `probe` and `hwaccel`; `probe`/`thumbnail`/`hwaccel` import the standard library only. Cross-imports inside the core use package-relative imports (never `import mosaic_media...` absolutely) so the purity guard stays green.
- Commit messages are plain English with NO conventional-commit prefixes (`feat:`, `fix:`, ...) and NO `Co-Authored-By` trailers. No process language in commit text.
- Full test-suite runs go through the machine-local `heavy` wrapper (`heavy uv run pytest`); a single-file or single-test run is a plain `uv run pytest <path>`.

## Scope boundary (read before starting)

**Duplicate, do not move.** `mosaic_api/src/mosaic_api/media_probe/` and `mosaic_api/tests/media_probe/` stay exactly as they are. This plan only creates files under `/home/paul/ecodylic/mosaic_media/`. No edit to any `mosaic_api` file is part of this plan.

**Stays behind in `mosaic_api` (do not copy the module or its test):**

- `media_probe/media_types.py` and `tests/media_probe/test_media_types.py` -- HTTP `Content-Type` selection, an API concern.
- `media_probe/facts_io.py` and `tests/media_probe/test_facts_io.py` -- names the backend's persistence columns; the test also imports `mosaic_api.db.models`.
- `tests/media_probe/test_thresholds.py` -- exercises `mosaic_api.config.media_probe_thresholds`, an API config function.
- `duplicate_stems` (the storage-path-injectivity half of `sequence.py`) and its three tests in `test_sequence.py`.

Coverage for all four stays with them in `mosaic_api`; nothing is dropped from the source repository.

**Owned by sibling plans (do not build here; the Interfaces blocks below must not contradict them):**

- The `io/` subpackage (numpy reader, seek index, multi-video reader, writer). The frame-reader plan later adds a `pos` byte-offset field to `probe.ffprobe.Packet`; this plan copies `Packet` with its current three fields (`time`, `size`, `keyframe`) and must not add `pos`.
- The `transcode/` and `cli/` subpackages. `[project.scripts]` is therefore deliberately omitted from `pyproject.toml` in this plan (see the divergence note in Task 1); the cli plan adds it when `mosaic_media.cli` exists.
- `tests/bench/`. The `bench` dependency group and the `bench` pytest marker ARE part of this plan (packaging scaffold).

---

## Task 1: Scaffold the package

**Files:**

- Create `/home/paul/ecodylic/mosaic_media/pyproject.toml`
- Create `/home/paul/ecodylic/mosaic_media/src/mosaic_media/__init__.py` (empty placeholder; Task 10 replaces it with the facade)
- Create `/home/paul/ecodylic/mosaic_media/uv.lock` (generated)

**Interfaces:**

- Produces: distribution `mosaic-media`, import package `mosaic_media`, `requires-python = ">=3.12"`, `dependencies = []`, extras `io = ["numpy>=1.22"]` and `cli = ["typer>=0.12"]`, dependency groups `dev` and `bench`. Consumers wire this in later as `mosaic-media = { path = "../mosaic_media", editable = true }`.
- Consumes: nothing.

**DIVERGENCE FROM THE SPEC SKELETON (deliberate):** the spec's `pyproject.toml` includes `[project.scripts] mosaic-media = "mosaic_media.cli:app"`. This plan omits `[project.scripts]` entirely, because `mosaic_media.cli` does not exist yet; declaring an entry point to a missing module would break `uv build`/install resolution. The transcode-and-cli plan adds `[project.scripts]` when the `cli` module lands.

**Steps:**

- [ ] Write `/home/paul/ecodylic/mosaic_media/pyproject.toml` with exactly this content:

```toml
[build-system]
requires = ["uv_build>=0.10.0,<0.11.0"]
build-backend = "uv_build"

[project]
name = "mosaic-media"
version = "0.1.0"
description = "Media probing, transcode planning, and frame reading through system ffmpeg."
readme = "README.md"
requires-python = ">=3.12"
dependencies = []

[project.optional-dependencies]
io = ["numpy>=1.22"]
cli = ["typer>=0.12"]

[dependency-groups]
dev = ["basedpyright>=1.38.2", "ruff>=0.15.4", "pytest>=8.0", "pytest-xdist>=3.8.0"]
bench = ["opencv-python>=4.7", "numpy>=1.22"]

[tool.pytest.ini_options]
addopts = "-m 'not bench'"
markers = [
    "bench: performance regression gate against OpenCV; excluded by default, run explicitly and serialized with pytest -m bench -n0 -s on an otherwise idle machine",
]

[tool.basedpyright]
pythonVersion = "3.12"
typeCheckingMode = "all"
include = ["src", "tests"]
# numpy (the [io] extra) and opencv-python (the bench group) leak Any/Unknown
# through their public surfaces; that is not our code's problem.
reportAny = false
reportMissingTypeStubs = false
reportUnknownVariableType = false
reportUnknownMemberType = false
reportUnknownArgumentType = false
reportUnknownParameterType = false
reportUnknownLambdaType = false
# Ban explicit Any in our own annotations.
reportExplicitAny = "error"
```

  - Rationale for the dev pins: `basedpyright>=1.38.2` and `ruff>=0.15.4` are the current pins in the sibling `mosaic` repo (same 3.12 floor); `pytest>=8.0` matches both siblings; `pytest-xdist>=3.8.0` matches `mosaic_api`. No `[tool.ruff]` section: both siblings run ruff at its defaults, so this package does too (default line length, default rule set).
- [ ] Create the placeholder package file `/home/paul/ecodylic/mosaic_media/src/mosaic_media/__init__.py` containing a single line:

```python
"""mosaic-media: media probing, verdicts, and thumbnails through system ffmpeg."""
```

- [ ] Resolve and materialize the environment. Run each in its own call (no compound `cd`; the working directory is already the repo root):
  - `uv lock`
  - `uv sync --group dev`
  - Expected: `uv.lock` written; a `.venv` created with `mosaic-media` installed editable plus `basedpyright`, `ruff`, `pytest`, `pytest-xdist`. (This step needs PyPI access.)
- [ ] Smoke-test the tooling against the empty package:
  - `uv run python -c "import mosaic_media"` -- expected: exit 0, no output.
  - `uv run ruff check src/` -- expected: `All checks passed!`
  - `uv run basedpyright src/` -- expected: `0 errors, 0 warnings, 0 notes`.
  - (The full `src/ tests/` form runs from Task 2 onward, once `tests/` exists.)
- [ ] Commit: `git add -A && git commit -m "Scaffold the mosaic-media package"`

---

## Task 2: Stand up the test harness

**Files:**

- Create `/home/paul/ecodylic/mosaic_media/tests/__init__.py` (empty)
- Create `/home/paul/ecodylic/mosaic_media/tests/conftest.py`
- Create `/home/paul/ecodylic/mosaic_media/tests/helpers/__init__.py` (empty)
- Create `/home/paul/ecodylic/mosaic_media/tests/helpers/media_fixtures.py` (verbatim copy)
- Create `/home/paul/ecodylic/mosaic_media/tests/probe/__init__.py` (empty)
- Create `/home/paul/ecodylic/mosaic_media/tests/thumbnail/__init__.py` (empty)

**Interfaces:**

- Produces: session-scoped pytest fixtures `clips: dict[str, Path]`, `truncated_faststart: Path`, `long_gop_clip: Path`, generated by the system ffmpeg. Registered as a plugin so any test module can request them.
- Consumes: nothing (the helper has no `mosaic_api` or `mosaic_media` imports).

**Steps:**

- [ ] Create the four empty package files (`tests/__init__.py`, `tests/helpers/__init__.py`, `tests/probe/__init__.py`, `tests/thumbnail/__init__.py`), each zero bytes. The `tests/*/__init__.py` files are required because `tests/__init__.py` makes `tests` a package, so pytest imports test modules as `tests.probe.test_*` / `tests.thumbnail.test_*`, and `test_sequence.py` uses the relative import `from .test_verdict import CLEAN`.
- [ ] Copy the fixture helper verbatim (it imports only `subprocess`, `collections.abc`, `pathlib`, `pytest` -- no code change needed):
  - `cp /home/paul/ecodylic/mosaic_api/tests/helpers/media_fixtures.py /home/paul/ecodylic/mosaic_media/tests/helpers/media_fixtures.py`
- [ ] Write `/home/paul/ecodylic/mosaic_media/tests/conftest.py` with exactly:

```python
# The generated media fixtures (clips, truncated_faststart, long_gop_clip) are
# registered as a plugin from the top-level conftest, because pytest honors
# pytest_plugins only there.
pytest_plugins = ["tests.helpers.media_fixtures"]
```

- [ ] Verify the harness collects cleanly with no tests yet:
  - `uv run pytest tests/` -- expected: `no tests ran` (exit code 5), and importantly no collection error.
  - `uv run ruff check src/ tests/` -- expected: `All checks passed!`
  - `uv run basedpyright src/ tests/` -- expected: `0 errors, 0 warnings, 0 notes`.
- [ ] Commit: `git add -A && git commit -m "Add the test harness and generated media fixtures"`

---

## Task 3: Copy the ffprobe foundation (errors, ffprobe)

**Files:**

- Create `/home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/__init__.py` (module docstring only)
- Create `/home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/errors.py` (verbatim)
- Create `/home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/ffprobe.py` (verbatim)
- Test `/home/paul/ecodylic/mosaic_media/tests/probe/test_ffprobe.py`
- Test `/home/paul/ecodylic/mosaic_media/tests/probe/test_stream_selection.py`

**Interfaces:**

- Produces:
  - `mosaic_media.probe.errors.MediaProbeError(RuntimeError)`
  - `mosaic_media.probe.ffprobe.Header` (frozen dataclass; fields: `container, codec_name, pixel_format, color_range, color_primaries, color_transfer: str`; `width, height, rotation_degrees: int`; `square_pixels, progressive, has_audio: bool`; `video_stream_count, video_position: int`; `start_time, declared_duration, declared_fps: float`; `declared_frame_count: int`)
  - `mosaic_media.probe.ffprobe.Packet` (frozen dataclass; fields `time: float`, `size: int`, `keyframe: bool`) -- NOTE: the frame-reader-io plan adds a `pos: int` field later; this plan keeps the three current fields exactly.
  - `mosaic_media.probe.ffprobe.TimestampSource = Literal["pts", "dts"]`
  - `mosaic_media.probe.ffprobe.SelectedVideoStream` (frozen dataclass; `stream: dict[str, object]`, `video_position: int`, `video_stream_count: int`)
  - `select_video_stream(streams: list[object]) -> SelectedVideoStream | None`
  - `read_header(path: Path) -> Header`
  - `scan_packets(path: Path, video_position: int) -> tuple[tuple[Packet, ...], TimestampSource]`
- Consumes: nothing outside the standard library. `ffprobe.py` already imports `from .errors import MediaProbeError` (package-relative; unchanged by the copy).

**Steps:**

- [ ] Copy the two test files, then rewrite their import roots:
  - `cp /home/paul/ecodylic/mosaic_api/tests/media_probe/test_ffprobe.py /home/paul/ecodylic/mosaic_media/tests/probe/test_ffprobe.py`
  - `cp /home/paul/ecodylic/mosaic_api/tests/media_probe/test_stream_selection.py /home/paul/ecodylic/mosaic_media/tests/probe/test_stream_selection.py`
  - `sed -i 's#mosaic_api\.media_probe\.#mosaic_media.probe.#g' /home/paul/ecodylic/mosaic_media/tests/probe/test_ffprobe.py`
  - `sed -i 's#mosaic_api\.media_probe\.#mosaic_media.probe.#g' /home/paul/ecodylic/mosaic_media/tests/probe/test_stream_selection.py`
  - After this the imports read `from mosaic_media.probe.errors import MediaProbeError` and `from mosaic_media.probe.ffprobe import read_header, scan_packets` / `select_video_stream`.
- [ ] Run the tests before the modules exist -- expected RED:
  - `uv run pytest tests/probe/test_ffprobe.py tests/probe/test_stream_selection.py`
  - Expected: collection error `ModuleNotFoundError: No module named 'mosaic_media.probe'`.
- [ ] Create `/home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/__init__.py` with a docstring only (the facade is the public path; submodules are imported directly):

```python
"""Measurement and verdict: the standard-library-only probe core.

Imported through the `mosaic_media` facade or by submodule path
(`mosaic_media.probe.ffprobe`, `mosaic_media.probe.verdict`, ...).
"""
```

- [ ] Copy the two source modules verbatim (no edits -- their only non-stdlib imports are package-relative):
  - `cp /home/paul/ecodylic/mosaic_api/src/mosaic_api/media_probe/errors.py /home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/errors.py`
  - `cp /home/paul/ecodylic/mosaic_api/src/mosaic_api/media_probe/ffprobe.py /home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/ffprobe.py`
- [ ] Run the tests again -- expected GREEN:
  - `uv run pytest tests/probe/test_ffprobe.py tests/probe/test_stream_selection.py`
  - Expected: all pass (roughly 15 tests; they build real ffmpeg clips via the `clips` fixture).
- [ ] Commit: `git add -A && git commit -m "Add the ffprobe header and packet-scan modules to mosaic_media"`

---

## Task 4: Copy timing (with the justification rewrite), gop, boxes

**Files:**

- Create `/home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/timing.py` (verbatim copy, then the stdlib-only comment rewritten)
- Create `/home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/gop.py` (verbatim)
- Create `/home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/boxes.py` (verbatim)
- Test `/home/paul/ecodylic/mosaic_media/tests/probe/test_timing.py`
- Test `/home/paul/ecodylic/mosaic_media/tests/probe/test_gop.py`
- Test `/home/paul/ecodylic/mosaic_media/tests/probe/test_boxes.py`

**Interfaces:**

- Produces:
  - `mosaic_media.probe.timing.Timing` (frozen dataclass; `fps: float`, `frame_count: int`, `duration: float`, `constant_frame_rate: bool`, `max_instantaneous_fps: float | None`, `max_drift_frame_periods: float`)
  - `measure_timing(packets: tuple[Packet, ...], drift_frame_periods: float) -> Timing`
  - `is_truncated(measured_duration: float, declared_duration: float, ratio: float) -> bool`
  - `mosaic_media.probe.gop.GopStats` (frozen dataclass; `max_gop_bytes: int`, `max_keyframe_interval_frames: int`) and `measure_gop(packets: tuple[Packet, ...]) -> GopStats`
  - `mosaic_media.probe.boxes.moov_at_start(path: Path) -> bool | None`
- Consumes: `mosaic_media.probe.ffprobe.Packet` (via `from .ffprobe import Packet`), `mosaic_media.probe.errors.MediaProbeError`.

**Steps:**

- [ ] Copy the three test files and rewrite their import roots:
  - `cp /home/paul/ecodylic/mosaic_api/tests/media_probe/test_timing.py /home/paul/ecodylic/mosaic_media/tests/probe/test_timing.py`
  - `cp /home/paul/ecodylic/mosaic_api/tests/media_probe/test_gop.py /home/paul/ecodylic/mosaic_media/tests/probe/test_gop.py`
  - `cp /home/paul/ecodylic/mosaic_api/tests/media_probe/test_boxes.py /home/paul/ecodylic/mosaic_media/tests/probe/test_boxes.py`
  - `sed -i 's#mosaic_api\.media_probe\.#mosaic_media.probe.#g' /home/paul/ecodylic/mosaic_media/tests/probe/test_timing.py /home/paul/ecodylic/mosaic_media/tests/probe/test_gop.py /home/paul/ecodylic/mosaic_media/tests/probe/test_boxes.py`
- [ ] Run before the modules exist -- expected RED:
  - `uv run pytest tests/probe/test_timing.py tests/probe/test_gop.py tests/probe/test_boxes.py`
  - Expected: `ModuleNotFoundError: No module named 'mosaic_media.probe.timing'` (and `.gop`, `.boxes`).
- [ ] Copy the three source modules verbatim:
  - `cp /home/paul/ecodylic/mosaic_api/src/mosaic_api/media_probe/timing.py /home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/timing.py`
  - `cp /home/paul/ecodylic/mosaic_api/src/mosaic_api/media_probe/gop.py /home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/gop.py`
  - `cp /home/paul/ecodylic/mosaic_api/src/mosaic_api/media_probe/boxes.py /home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/boxes.py`
- [ ] Rewrite the expired standard-library-only justification in `timing.py`. The comment "keeps it extractable" expired the moment this package was extracted; replace it with the durable CLI reason from the spec. Apply this exact edit to `/home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/timing.py`:

  Replace this text (the tail of the `measure_timing` docstring):

```
    same arithmetic in 1 ms, but it would buy under one percent of the whole
    probe while adding a dependency this package does not declare and does not
    need. Standard library only is what keeps it extractable.
```

  with exactly:

```
    same arithmetic in 1 ms, but it would buy under one percent of the whole
    probe while adding a dependency this package does not declare and does not
    need. The core stays standard library only for the CLI's sake: the
    transcode runner has to start on a machine that has ffmpeg and nothing
    else -- a minimal container, or a tracking box without the analysis stack.
```

- [ ] Run again -- expected GREEN:
  - `uv run pytest tests/probe/test_timing.py tests/probe/test_gop.py tests/probe/test_boxes.py`
  - Expected: all pass (roughly 24 tests).
- [ ] Commit: `git add -A && git commit -m "Add the timing, gop, and box-order probe modules to mosaic_media"`

---

## Task 5: Copy facts, policy, verdict

**Files:**

- Create `/home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/facts.py` (verbatim)
- Create `/home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/policy.py` (verbatim)
- Create `/home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/verdict.py` (verbatim)
- Test `/home/paul/ecodylic/mosaic_media/tests/probe/test_verdict.py`

**Interfaces:**

- Produces:
  - `mosaic_media.probe.facts.MediaFacts` (frozen dataclass; the 25-field measurement result -- unchanged field set from `mosaic_api`)
  - `mosaic_media.probe.policy.StreamReason`, `AnalysisReason`, `StreamTranscode` (Literals); `HARD_STREAM_REASONS: frozenset[StreamReason]`; `PlaybackProfile` (frozen dataclass; `containers, codecs, client_dependent_codecs, baseline_pixel_formats: frozenset[str]`); `CHROME_149: PlaybackProfile`; `Thresholds` (frozen slots dataclass; `drift_frame_periods: float = 0.5`, `max_gop_bytes: int = 524_288`, `max_keyframe_interval_frames: int = 200`, `truncation_duration_ratio: float = 0.95`, `start_time_frame_periods: float = 0.5`); `DEFAULT_THRESHOLDS: Thresholds`
  - `mosaic_media.probe.verdict.Verdict` (frozen dataclass; `playable: bool`, `stream_transcode: StreamTranscode | None`, `analysis_transcode: Literal["required"] | None`, `stream_reasons: frozenset[StreamReason]`, `analysis_reasons: frozenset[AnalysisReason]`, `truncated: bool`); `derive(facts: MediaFacts, profile: PlaybackProfile, thresholds: Thresholds) -> Verdict`
- Consumes: `verdict.py` imports `from .facts import MediaFacts` and `from .policy import (...)` (package-relative, unchanged). `test_verdict.py` defines the shared `CLEAN` `MediaFacts` fixture value reused by Task 7's `test_sequence.py`.

**Steps:**

- [ ] Copy the test file and rewrite its import roots (this also rewrites the function-local `from mosaic_api.media_probe.policy import Thresholds` on line 202):
  - `cp /home/paul/ecodylic/mosaic_api/tests/media_probe/test_verdict.py /home/paul/ecodylic/mosaic_media/tests/probe/test_verdict.py`
  - `sed -i 's#mosaic_api\.media_probe\.#mosaic_media.probe.#g' /home/paul/ecodylic/mosaic_media/tests/probe/test_verdict.py`
- [ ] Run before the modules exist -- expected RED:
  - `uv run pytest tests/probe/test_verdict.py`
  - Expected: `ModuleNotFoundError: No module named 'mosaic_media.probe.facts'`.
- [ ] Copy the three source modules verbatim:
  - `cp /home/paul/ecodylic/mosaic_api/src/mosaic_api/media_probe/facts.py /home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/facts.py`
  - `cp /home/paul/ecodylic/mosaic_api/src/mosaic_api/media_probe/policy.py /home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/policy.py`
  - `cp /home/paul/ecodylic/mosaic_api/src/mosaic_api/media_probe/verdict.py /home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/verdict.py`
- [ ] Run again -- expected GREEN:
  - `uv run pytest tests/probe/test_verdict.py`
  - Expected: all pass (roughly 20 test cases including the parametrized container-by-codec matrix).
- [ ] Commit: `git add -A && git commit -m "Add the facts, playback policy, and verdict modules to mosaic_media"`

---

## Task 6: Copy probe and candidates

**Files:**

- Create `/home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/probe.py` (verbatim)
- Create `/home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/candidates.py` (verbatim)
- Test `/home/paul/ecodylic/mosaic_media/tests/probe/test_probe.py`

**Interfaces:**

- Produces:
  - `mosaic_media.probe.probe.probe_media(path: Path, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> MediaFacts`
  - `mosaic_media.probe.candidates.VIDEO_EXTENSIONS: frozenset[str]` and `is_candidate_video(path: Path) -> bool`
- Consumes: `probe.py` imports `from .boxes ...`, `from .facts ...`, `from .ffprobe ...`, `from .gop ...`, `from .policy ...`, `from .timing ...` (all package-relative, unchanged).
- Note: `test_candidates.py` imports through the facade (`from mosaic_media import is_candidate_video`) and is therefore copied in Task 10, after the facade exists.

**Steps:**

- [ ] Copy the probe test and rewrite its import roots:
  - `cp /home/paul/ecodylic/mosaic_api/tests/media_probe/test_probe.py /home/paul/ecodylic/mosaic_media/tests/probe/test_probe.py`
  - `sed -i 's#mosaic_api\.media_probe\.#mosaic_media.probe.#g' /home/paul/ecodylic/mosaic_media/tests/probe/test_probe.py`
- [ ] Run before the modules exist -- expected RED:
  - `uv run pytest tests/probe/test_probe.py`
  - Expected: `ModuleNotFoundError: No module named 'mosaic_media.probe.probe'`.
- [ ] Copy the two source modules verbatim:
  - `cp /home/paul/ecodylic/mosaic_api/src/mosaic_api/media_probe/probe.py /home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/probe.py`
  - `cp /home/paul/ecodylic/mosaic_api/src/mosaic_api/media_probe/candidates.py /home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/candidates.py`
- [ ] Run again -- expected GREEN:
  - `uv run pytest tests/probe/test_probe.py`
  - Expected: 7 tests pass (they probe generated clips end to end).
- [ ] Commit: `git add -A && git commit -m "Add the probe composition and candidate-extension modules to mosaic_media"`

---

## Task 7: Split sequence.py (video-property half only)

**Files:**

- Create `/home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/sequence.py` (verbatim copy, then the storage half removed)
- Test `/home/paul/ecodylic/mosaic_media/tests/probe/test_sequence.py`

**Interfaces:**

- Produces (the multi-video uniformity surface the `io` plan's `multi.py` consumes):
  - `mosaic_media.probe.sequence.VideoProperties` (Protocol with read-only `fps: float`, `width: int`, `height: int`, `frame_count: int`, `duration: float`)
  - `MeasuredVideoProperties` (frozen dataclass; same five fields)
  - `measured_or_none(fps: float | None, width: int | None, height: int | None, frame_count: int | None, duration: float | None) -> MeasuredVideoProperties | None`
  - `PropertyMismatch` (frozen slots dataclass; `field: str`, `first: float`, `other: float`)
  - `uniform_properties(videos: Sequence[VideoProperties]) -> PropertyMismatch | None`
  - `canonical_fps(videos: Sequence[VideoProperties]) -> float`
- Consumes: the standard library only (`collections.abc.Sequence`, `dataclasses.dataclass`, `typing.Protocol`).
- Does NOT produce `duplicate_stems` -- that stays in `mosaic_api`'s `sequence.py` (storage-path injectivity, a backend concern). The three `duplicate_stems` tests stay in `mosaic_api`'s `test_sequence.py`; they are not copied here.

**Steps:**

- [ ] Copy the test file, rewrite the import root, and drop the `duplicate_stems` import and its three tests.
  - `cp /home/paul/ecodylic/mosaic_api/tests/media_probe/test_sequence.py /home/paul/ecodylic/mosaic_media/tests/probe/test_sequence.py`
  - Edit the import block. Replace:

```python
from mosaic_api.media_probe.sequence import (
    canonical_fps,
    duplicate_stems,
    uniform_properties,
)
```

  with:

```python
from mosaic_media.probe.sequence import canonical_fps, uniform_properties
```

  - Delete the three `duplicate_stems` test functions at the end of the file (they test a symbol this package does not have). Remove exactly this block:

```python
def test_a_same_stem_pair_is_ambiguous() -> None:
    # cam0.mp4 and cam0.avi produce the same thumbnail path and both claim
    # cam0.pose. The pose sidecar is authored externally against a video stem, so
    # renaming the thumbnail would move the bug from visible to invisible.
    assert duplicate_stems(["cam0.mp4", "cam0.avi"]) == frozenset({"cam0"})


def test_distinct_stems_are_unambiguous() -> None:
    assert duplicate_stems(["cam0.mp4", "cam1.mp4", "video 2.avi"]) == frozenset()


def test_stems_are_compared_case_insensitively() -> None:
    assert duplicate_stems(["Cam0.mp4", "cam0.avi"]) == frozenset({"cam0"})
```

  - The retained tests keep `from .test_verdict import CLEAN` unchanged (Task 5 provided `test_verdict.py`).
- [ ] Run before the module exists -- expected RED:
  - `uv run pytest tests/probe/test_sequence.py`
  - Expected: `ModuleNotFoundError: No module named 'mosaic_media.probe.sequence'`.
- [ ] Copy the source module verbatim, then remove the storage half:
  - `cp /home/paul/ecodylic/mosaic_api/src/mosaic_api/media_probe/sequence.py /home/paul/ecodylic/mosaic_media/src/mosaic_media/probe/sequence.py`
  - Edit 1 -- drop the two imports that only `duplicate_stems` uses. Replace:

```python
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
```

  with:

```python
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
```

  - Edit 2 -- remove the `duplicate_stems` function (the last function in the file). Replace:

```python
    return total_frames / total_duration


def duplicate_stems(filenames: Sequence[str]) -> frozenset[str]:
    """Stems shared by two or more filenames, lowercased.

    With a single extension the thumbnail and pose sidecar paths are injective.
    With eleven they are not.
    """
    counts = Counter(Path(name).stem.lower() for name in filenames)
    return frozenset(stem for stem, count in counts.items() if count > 1)
```

  with:

```python
    return total_frames / total_duration
```

- [ ] Run again -- expected GREEN:
  - `uv run pytest tests/probe/test_sequence.py`
  - Expected: 7 tests pass.
- [ ] Confirm no dangling reference to the dropped symbols:
  - `uv run ruff check src/mosaic_media/probe/sequence.py` -- expected: `All checks passed!` (proves `Counter` and `Path` are not left as unused imports and `duplicate_stems` is gone).
- [ ] Commit: `git add -A && git commit -m "Add the sequence uniformity helpers to mosaic_media, leaving the storage half behind"`

---

## Task 8: Create the thumbnail subpackage

**Files:**

- Create `/home/paul/ecodylic/mosaic_media/src/mosaic_media/thumbnail/__init__.py`
- Create `/home/paul/ecodylic/mosaic_media/src/mosaic_media/thumbnail/extract.py` (content of `media_probe/thumbnail.py`, one import adjusted)
- Create `/home/paul/ecodylic/mosaic_media/src/mosaic_media/thumbnail/downscale.py` (content of `media_probe/downscale.py`, one import adjusted)
- Test `/home/paul/ecodylic/mosaic_media/tests/thumbnail/test_extract.py` (from `test_thumbnail.py`)
- Test `/home/paul/ecodylic/mosaic_media/tests/thumbnail/test_downscale.py`

**Interfaces:**

- Produces:
  - `mosaic_media.thumbnail.extract_first_frame(source: Path, destination: Path) -> None`
  - `mosaic_media.thumbnail.downscale_to_jpeg(source: Path, destination: Path, *, width: int, height: int) -> None`
  - `mosaic_media.thumbnail.thumbnail_dimensions(width: int, height: int, *, cap: int = 320) -> tuple[int, int]`
- Consumes: `mosaic_media.probe.errors.MediaProbeError` -- reached by a package-relative import `from ..probe.errors import MediaProbeError` (the only change from the source, replacing the original `from .errors import MediaProbeError`). This keeps the module standard-library-pure under the static purity guard, which ignores relative imports.

**Steps:**

- [ ] Copy the two test files to their new names and rewrite their import roots:
  - `cp /home/paul/ecodylic/mosaic_api/tests/media_probe/test_thumbnail.py /home/paul/ecodylic/mosaic_media/tests/thumbnail/test_extract.py`
  - `cp /home/paul/ecodylic/mosaic_api/tests/media_probe/test_downscale.py /home/paul/ecodylic/mosaic_media/tests/thumbnail/test_downscale.py`
  - In `test_extract.py`, apply:
    - `sed -i 's#mosaic_api\.media_probe\.errors#mosaic_media.probe.errors#' /home/paul/ecodylic/mosaic_media/tests/thumbnail/test_extract.py`
    - `sed -i 's#mosaic_api\.media_probe\.thumbnail#mosaic_media.thumbnail#' /home/paul/ecodylic/mosaic_media/tests/thumbnail/test_extract.py`
    - Result: `from mosaic_media.probe.errors import MediaProbeError` and `from mosaic_media.thumbnail import extract_first_frame`.
  - In `test_downscale.py`, apply:
    - `sed -i 's#mosaic_api\.media_probe\.errors#mosaic_media.probe.errors#' /home/paul/ecodylic/mosaic_media/tests/thumbnail/test_downscale.py`
    - Then rewrite the multi-line downscale import. Replace:

```python
from mosaic_api.media_probe.downscale import (
    downscale_to_jpeg,
    thumbnail_dimensions,
)
```

  with:

```python
from mosaic_media.thumbnail import downscale_to_jpeg, thumbnail_dimensions
```

- [ ] Run before the subpackage exists -- expected RED:
  - `uv run pytest tests/thumbnail/test_extract.py tests/thumbnail/test_downscale.py`
  - Expected: `ModuleNotFoundError: No module named 'mosaic_media.thumbnail'`.
- [ ] Copy the two source modules under their new names:
  - `cp /home/paul/ecodylic/mosaic_api/src/mosaic_api/media_probe/thumbnail.py /home/paul/ecodylic/mosaic_media/src/mosaic_media/thumbnail/extract.py`
  - `cp /home/paul/ecodylic/mosaic_api/src/mosaic_api/media_probe/downscale.py /home/paul/ecodylic/mosaic_media/src/mosaic_media/thumbnail/downscale.py`
- [ ] Adjust the one cross-package import in each (errors now lives in `probe/`, a sibling subpackage):
  - `sed -i 's#^from \.errors import MediaProbeError#from ..probe.errors import MediaProbeError#' /home/paul/ecodylic/mosaic_media/src/mosaic_media/thumbnail/extract.py`
  - `sed -i 's#^from \.errors import MediaProbeError#from ..probe.errors import MediaProbeError#' /home/paul/ecodylic/mosaic_media/src/mosaic_media/thumbnail/downscale.py`
- [ ] Write the subpackage facade `/home/paul/ecodylic/mosaic_media/src/mosaic_media/thumbnail/__init__.py` with exactly:

```python
"""Still-image derivatives produced through system ffmpeg. Standard library only."""

from .downscale import downscale_to_jpeg, thumbnail_dimensions
from .extract import extract_first_frame

__all__ = [
    "downscale_to_jpeg",
    "extract_first_frame",
    "thumbnail_dimensions",
]
```

- [ ] Run again -- expected GREEN:
  - `uv run pytest tests/thumbnail/test_extract.py tests/thumbnail/test_downscale.py`
  - Expected: all pass (roughly 11 tests; they build clips and PNGs and re-probe output dimensions).
- [ ] Commit: `git add -A && git commit -m "Add the thumbnail extract and downscale subpackage to mosaic_media"`

---

## Task 9: Add hwaccel.py (ffmpeg and hardware capability probes)

**Files:**

- Create `/home/paul/ecodylic/mosaic_media/src/mosaic_media/hwaccel.py`
- Test `/home/paul/ecodylic/mosaic_media/tests/test_hwaccel.py`

**Interfaces:**

- Produces (later plans depend on this contract verbatim -- the transcode converter, the io reader/writer):
  - `mosaic_media.hwaccel.ffmpeg_available() -> bool`
  - `mosaic_media.hwaccel.nvdec_available() -> bool`
  - `mosaic_media.hwaccel.encoder_available(name: str) -> bool` -- a generic cached ffmpeg `-encoders` query. `encoder_available("h264_nvenc")` reproduces mosaic's old `_nvenc_available`; the AV1 transcode later asks for `encoder_available("av1_nvenc")` and the CPU fallback for `encoder_available("libsvtav1")`.
- Consumes: the standard library only (`shutil`, `subprocess`). Sits at the package top level, not inside `transcode/`, because `io` must reach it without importing `transcode`. Absorbed from `mosaic`'s `core/media/video_io.py` (`_ffmpeg_available`, `_nvdec_available`, `_nvenc_available`), which the toolkit migration later deletes in favor of this.

**Steps:**

- [ ] Write the unit test first `/home/paul/ecodylic/mosaic_media/tests/test_hwaccel.py` with exactly:

```python
"""Unit tests for the cached ffmpeg capability probes.

shutil and subprocess are stubbed so these run on a machine without ffmpeg and
pin the caching and output-parsing behavior directly.
"""

import shutil
import subprocess

import pytest

from mosaic_media import hwaccel


@pytest.fixture(autouse=True)
def reset_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the module-level capability caches before each case.

    setattr through monkeypatch both resets each cache and restores it
    afterwards, so no test leaks a cached probe result into the next.
    """
    monkeypatch.setattr(hwaccel, "_ffmpeg_ok", None)
    monkeypatch.setattr(hwaccel, "_nvdec_ok", None)
    monkeypatch.setattr(hwaccel, "_encoder_ok", {})


class FakeRun:
    """A subprocess.run stand-in that returns fixed stdout and records calls."""

    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.commands: list[list[str]] = []

    def __call__(
        self,
        command: list[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=self.stdout, stderr="")


def present(name: str) -> str | None:
    return "/usr/bin/ffmpeg"


def absent(name: str) -> str | None:
    return None


def test_ffmpeg_available_is_true_when_on_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", present)
    assert hwaccel.ffmpeg_available() is True


def test_ffmpeg_available_is_false_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", absent)
    assert hwaccel.ffmpeg_available() is False


def test_ffmpeg_availability_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def counting_which(name: str) -> str | None:
        nonlocal calls
        calls += 1
        return "/usr/bin/ffmpeg"

    monkeypatch.setattr(shutil, "which", counting_which)
    assert hwaccel.ffmpeg_available() is True
    assert hwaccel.ffmpeg_available() is True
    assert calls == 1


def test_nvdec_available_reads_the_hwaccels_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", present)
    monkeypatch.setattr(subprocess, "run", FakeRun("Hardware acceleration methods:\ncuda\nvaapi\n"))
    assert hwaccel.nvdec_available() is True


def test_nvdec_available_is_false_without_cuda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", present)
    monkeypatch.setattr(subprocess, "run", FakeRun("Hardware acceleration methods:\nvaapi\n"))
    assert hwaccel.nvdec_available() is False


def test_nvdec_skips_the_subprocess_without_ffmpeg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRun("cuda")
    monkeypatch.setattr(shutil, "which", absent)
    monkeypatch.setattr(subprocess, "run", fake)
    assert hwaccel.nvdec_available() is False
    assert fake.commands == []


def test_nvdec_probe_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun("cuda\n")
    monkeypatch.setattr(shutil, "which", present)
    monkeypatch.setattr(subprocess, "run", fake)
    assert hwaccel.nvdec_available() is True
    assert hwaccel.nvdec_available() is True
    assert len(fake.commands) == 1


def test_encoder_available_finds_named_encoders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", present)
    monkeypatch.setattr(subprocess, "run", FakeRun(" V..... av1_nvenc x\n V..... libsvtav1 y\n"))
    assert hwaccel.encoder_available("av1_nvenc") is True
    assert hwaccel.encoder_available("libsvtav1") is True


def test_encoder_available_is_false_for_a_missing_encoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", present)
    monkeypatch.setattr(subprocess, "run", FakeRun(" V..... libx264 H.264\n"))
    assert hwaccel.encoder_available("av1_nvenc") is False


def test_encoder_probe_is_cached_per_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRun(" V..... av1_nvenc x\n V..... h264_nvenc y\n")
    monkeypatch.setattr(shutil, "which", present)
    monkeypatch.setattr(subprocess, "run", fake)
    assert hwaccel.encoder_available("av1_nvenc") is True
    assert hwaccel.encoder_available("av1_nvenc") is True
    assert len(fake.commands) == 1
    assert hwaccel.encoder_available("h264_nvenc") is True
    assert len(fake.commands) == 2


def test_encoder_available_is_false_without_ffmpeg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRun("av1_nvenc")
    monkeypatch.setattr(shutil, "which", absent)
    monkeypatch.setattr(subprocess, "run", fake)
    assert hwaccel.encoder_available("av1_nvenc") is False
    assert fake.commands == []


def test_a_subprocess_failure_is_reported_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(
        command: list[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, timeout if timeout is not None else 5.0)

    monkeypatch.setattr(shutil, "which", present)
    monkeypatch.setattr(subprocess, "run", boom)
    assert hwaccel.nvdec_available() is False
    assert hwaccel.encoder_available("av1_nvenc") is False
```

- [ ] Run it before the module exists -- expected RED:
  - `uv run pytest tests/test_hwaccel.py`
  - Expected: `ImportError`/`ModuleNotFoundError` for `mosaic_media.hwaccel`.
- [ ] Write `/home/paul/ecodylic/mosaic_media/src/mosaic_media/hwaccel.py` with exactly:

```python
"""ffmpeg and hardware-acceleration capability probing. Standard library only.

Absorbed from mosaic's video_io so the frame reader (NVDEC), the writer (NVENC),
and the transcode converter share one probe rather than three copies. It sits at
the package top level, not inside transcode/, because the io layer must reach it
without importing transcode.

Each result is cached: a capability does not change while the process runs, and
each check spawns an ffmpeg subprocess.
"""

import shutil
import subprocess

_PROBE_TIMEOUT_SECONDS = 5

_ffmpeg_ok: bool | None = None
_nvdec_ok: bool | None = None
_encoder_ok: dict[str, bool] = {}


def ffmpeg_available() -> bool:
    """True when an ffmpeg binary is on PATH. Cached."""
    global _ffmpeg_ok
    if _ffmpeg_ok is None:
        _ffmpeg_ok = shutil.which("ffmpeg") is not None
    return _ffmpeg_ok


def nvdec_available() -> bool:
    """True when ffmpeg advertises CUDA/NVDEC hardware decoding. Cached."""
    global _nvdec_ok
    if _nvdec_ok is None:
        if not ffmpeg_available():
            _nvdec_ok = False
        else:
            try:
                result = subprocess.run(
                    ["ffmpeg", "-hwaccels"],
                    capture_output=True,
                    text=True,
                    timeout=_PROBE_TIMEOUT_SECONDS,
                )
                _nvdec_ok = "cuda" in result.stdout.lower()
            except (OSError, subprocess.SubprocessError):
                _nvdec_ok = False
    return _nvdec_ok


def encoder_available(name: str) -> bool:
    """True when ffmpeg lists `name` among its encoders. Cached per name.

    `encoder_available("h264_nvenc")` is the NVENC probe mosaic's video_io ran;
    the AV1 transcode asks for `av1_nvenc`, and the CPU fallback for `libsvtav1`.
    """
    cached = _encoder_ok.get(name)
    if cached is not None:
        return cached
    if not ffmpeg_available():
        _encoder_ok[name] = False
        return False
    try:
        result = subprocess.run(
            ["ffmpeg", "-encoders"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
        available = name in result.stdout
    except (OSError, subprocess.SubprocessError):
        available = False
    _encoder_ok[name] = available
    return available
```

  - Note: mosaic's original caught a bare `Exception`; this narrows to `(OSError, subprocess.SubprocessError)` -- the only failures `shutil.which` and `subprocess.run` can raise here (`FileNotFoundError` is an `OSError`; `TimeoutExpired` is a `SubprocessError`) -- to satisfy the linters rather than suppress them.
- [ ] Run again -- expected GREEN:
  - `uv run pytest tests/test_hwaccel.py`
  - Expected: 12 tests pass.
- [ ] Commit: `git add -A && git commit -m "Add cached ffmpeg and hardware capability probes to mosaic_media"`

---

## Task 10: Write the facade and add the candidates test

**Files:**

- Modify `/home/paul/ecodylic/mosaic_media/src/mosaic_media/__init__.py` (replace the placeholder with the full facade)
- Test `/home/paul/ecodylic/mosaic_media/tests/probe/test_candidates.py`

**Interfaces:**

- Produces: the public import surface `mosaic_media`. The export list is `mosaic_api.media_probe.__init__.__all__` minus `media_type_for_container` (stays in `mosaic_api`) and `duplicate_stems` (stays in `mosaic_api`) -- 24 names -- with each import repointed at the `probe` / `thumbnail` subpackages. `hwaccel` is not re-exported here (it is reached by submodule path `mosaic_media.hwaccel`, matching its as-derived contract; the facade export set is derived strictly from `media_probe`'s facade).
- Consumes: `mosaic_media.probe.*` and `mosaic_media.thumbnail`. The facade must not import `io` or `cli`, so `import mosaic_media` never pulls numpy or typer.

**Steps:**

- [ ] Replace the contents of `/home/paul/ecodylic/mosaic_media/src/mosaic_media/__init__.py` with exactly:

```python
"""mosaic-media: media probing, verdicts, and thumbnails through system ffmpeg.

The public import path. Consumers import from `mosaic_media`, not from the
subpackages. This facade and the `probe`, `thumbnail`, and `hwaccel` modules are
standard library only; the frame reader (`[io]`, numpy) and the command line app
(`[cli]`, typer) are separate optional layers and are not re-exported here, so
`import mosaic_media` never pulls numpy or typer.
"""

from .probe.candidates import VIDEO_EXTENSIONS, is_candidate_video
from .probe.errors import MediaProbeError
from .probe.facts import MediaFacts
from .probe.policy import (
    CHROME_149,
    DEFAULT_THRESHOLDS,
    HARD_STREAM_REASONS,
    AnalysisReason,
    PlaybackProfile,
    StreamReason,
    StreamTranscode,
    Thresholds,
)
from .probe.probe import probe_media
from .probe.sequence import (
    MeasuredVideoProperties,
    PropertyMismatch,
    VideoProperties,
    canonical_fps,
    measured_or_none,
    uniform_properties,
)
from .probe.verdict import Verdict, derive
from .thumbnail import downscale_to_jpeg, extract_first_frame, thumbnail_dimensions

__all__ = [
    "CHROME_149",
    "DEFAULT_THRESHOLDS",
    "HARD_STREAM_REASONS",
    "VIDEO_EXTENSIONS",
    "AnalysisReason",
    "MeasuredVideoProperties",
    "MediaFacts",
    "MediaProbeError",
    "PlaybackProfile",
    "PropertyMismatch",
    "StreamReason",
    "StreamTranscode",
    "Thresholds",
    "Verdict",
    "VideoProperties",
    "canonical_fps",
    "derive",
    "downscale_to_jpeg",
    "extract_first_frame",
    "is_candidate_video",
    "measured_or_none",
    "probe_media",
    "thumbnail_dimensions",
    "uniform_properties",
]
```

- [ ] Copy the candidates test (which imports through the facade) and rewrite its import root:
  - `cp /home/paul/ecodylic/mosaic_api/tests/media_probe/test_candidates.py /home/paul/ecodylic/mosaic_media/tests/probe/test_candidates.py`
  - `sed -i 's#from mosaic_api.media_probe import#from mosaic_media import#' /home/paul/ecodylic/mosaic_media/tests/probe/test_candidates.py`
  - Result: `from mosaic_media import is_candidate_video`.
- [ ] Verify the facade imports and the candidates test pass:
  - `uv run python -c "import mosaic_media; print(sorted(mosaic_media.__all__))"` -- expected: exit 0, prints the 24 names.
  - `uv run pytest tests/probe/test_candidates.py` -- expected: 4 tests pass.
- [ ] Commit: `git add -A && git commit -m "Add the mosaic_media public facade and candidate-extension test"`

---

## Task 11: Adapt the standard-library-purity test

**Files:**

- Create `/home/paul/ecodylic/mosaic_media/tests/probe/test_purity.py`

**Interfaces:**

- Produces: a static guard that every stdlib-only core module (`probe/`, `thumbnail/`, `hwaccel.py`) imports only the standard library and never imports dynamically. Complements Task 12's runtime guard: this reads source (catching a dependency hidden inside an unexecuted function body); Task 12 poisons `sys.meta_path` (catching a transitive import at load time).
- Consumes: `ast`, `sys`, `pathlib` (standard library). Reads source files by path; imports no product code.

**Steps:**

- [ ] Write `/home/paul/ecodylic/mosaic_media/tests/probe/test_purity.py` with exactly:

```python
"""The stdlib-only core stays importable with nothing but the standard library.

A static companion to tests/test_import_guard.py: that test proves the modules
import cleanly with numpy, typer, and cv2 poisoned; this one reads the source and
rejects any absolute import of a third-party root, catching a dependency hidden
inside a function body that the runtime guard would only see once that function
runs. The core is kept dependency-free for the CLI's sake -- the transcode runner
must start on a machine that has ffmpeg and nothing else.
"""

import ast
import sys
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[2] / "src" / "mosaic_media"
STDLIB_ONLY_TARGETS: tuple[Path, ...] = (
    CORE_ROOT / "probe",
    CORE_ROOT / "thumbnail",
    CORE_ROOT / "hwaccel.py",
)


def source_files() -> list[Path]:
    files: list[Path] = []
    for target in STDLIB_ONLY_TARGETS:
        if target.is_dir():
            files.extend(sorted(target.rglob("*.py")))
        else:
            files.append(target)
    return files


def imported_roots(source: str) -> set[str]:
    """Top-level module names this source imports absolutely.

    Relative imports carry `level > 0` and are inside the package, so they are
    not reported.
    """
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module is not None:
                roots.add(node.module.split(".")[0])
    return roots


def dynamic_import_calls(source: str) -> set[str]:
    """Names of dynamic-import mechanisms this source reaches for.

    `imported_roots` reads the import statements an abstract syntax tree makes
    visible. A module name assembled at runtime is invisible to it, and
    `importlib` is itself in the standard library, so a dependency smuggled in
    through `importlib.import_module("numpy")` would pass unnoticed. Nothing here
    needs to import anything dynamically, so reaching for the mechanism at all is
    the violation.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "importlib":
                    found.add("importlib")
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module is not None and node.module.split(".")[0] == "importlib":
                found.add("importlib")
        elif isinstance(node, ast.Name) and node.id == "__import__":
            found.add("__import__")
    return found


def test_the_core_imports_only_the_standard_library() -> None:
    modules = source_files()
    assert modules, "stdlib-only core modules not found"
    offenders: dict[str, set[str]] = {}
    for module in modules:
        outside = imported_roots(module.read_text()) - sys.stdlib_module_names
        if outside:
            offenders[module.name] = outside
    message = f"the stdlib-only core must import only the standard library, found: {offenders}"
    assert offenders == {}, message


def test_the_core_never_imports_dynamically() -> None:
    modules = source_files()
    assert modules, "stdlib-only core modules not found"
    offenders: dict[str, set[str]] = {}
    for module in modules:
        dynamic = dynamic_import_calls(module.read_text())
        if dynamic:
            offenders[module.name] = dynamic
    message = f"the stdlib-only core must not import dynamically, found: {offenders}"
    assert offenders == {}, message
```

- [ ] Run it -- expected GREEN:
  - `uv run pytest tests/probe/test_purity.py`
  - Expected: 2 tests pass. (Every `probe`/`thumbnail`/`hwaccel` module imports only `json`, `struct`, `subprocess`, `shutil`, `dataclasses`, `pathlib`, `typing`, `collections` absolutely; the `thumbnail` -> `probe.errors` edge is a relative import and is not reported.)
- [ ] Commit: `git add -A && git commit -m "Guard the standard-library-only core with a static purity test"`

---

## Task 12: Add the import guard test and its shared poison-finder helper

**Files:**

- Create `/home/paul/ecodylic/mosaic_media/tests/test_import_guard.py`

**Interfaces:**

- Produces: `_run_guarded(body: str, *, forbidden_root: str) -> subprocess.CompletedProcess[str]` -- the single, parameterized subprocess runner for every "import X with root Y poisoned" check in this package. It runs `body` (import statements under test) in a fresh interpreter whose `sys.meta_path` carries a finder raising `AssertionError` on any import whose top-level name equals `forbidden_root`, and returns the completed process so callers assert on `returncode`/`stderr`. Also produces three guard tests over the core import body, one per forbidden root (`numpy`, `typer`, `cv2`).
- Consumes: `subprocess`, `sys` (standard library). Runs the child under `sys.executable`, which resolves the editable-installed `mosaic_media`.
- **Shared seam for later plans:** this file is the one home for the poison-finder idiom. Two later plans append their own guard tests here rather than re-inventing it -- the frame-reader plan adds an io-without-numpy check, and the cli plan adds a cli-without-typer check. Both consume only earlier plans' outputs, so they call `_run_guarded` with their own `body` and `forbidden_root`; those appends are expected and must not duplicate the helper.

**Steps:**

- [ ] Write `/home/paul/ecodylic/mosaic_media/tests/test_import_guard.py` with exactly:

```python
"""Layered modules import without a forbidden dependency present.

`_run_guarded` is the shared seam for every "import X with root Y poisoned"
check in this package. It runs the import statements under test in a fresh
interpreter whose sys.meta_path carries a finder that raises AssertionError on
any import whose top-level name is the poisoned root. Subprocess isolation is
essential: a module already imported into this test process would be served from
sys.modules and never consult the finder, masking a violation. AssertionError --
not ImportError -- so a `try/except ImportError` optional-import guard inside a
module under test cannot swallow the violation. The core is kept dependency-free
on purpose: the transcode CLI must start on a machine that has ffmpeg and nothing
else.

Two later plans append guard tests to this file: the frame reader is checked to
import without numpy through the core path (io-without-numpy), and the command
line app to load without typer where it should not need it (cli-without-typer).
Both call `_run_guarded`; the helper is the single home for the poison-finder
idiom, and those appends are expected.
"""

import subprocess
import sys

_POISON_FINDER = '''
import sys
from importlib.abc import MetaPathFinder


class Poison(MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split(".")[0] == FORBIDDEN_ROOT:
            raise AssertionError(
                "a module under test imported the forbidden dependency: " + fullname
            )
        return None


sys.meta_path.insert(0, Poison())
'''

_CORE_IMPORTS = '''
import mosaic_media
import mosaic_media.hwaccel
import mosaic_media.probe
import mosaic_media.probe.boxes
import mosaic_media.probe.candidates
import mosaic_media.probe.errors
import mosaic_media.probe.facts
import mosaic_media.probe.ffprobe
import mosaic_media.probe.gop
import mosaic_media.probe.policy
import mosaic_media.probe.probe
import mosaic_media.probe.sequence
import mosaic_media.probe.timing
import mosaic_media.probe.verdict
import mosaic_media.thumbnail
import mosaic_media.thumbnail.downscale
import mosaic_media.thumbnail.extract
'''


def _run_guarded(
    body: str, *, forbidden_root: str
) -> subprocess.CompletedProcess[str]:
    """Import `body` in a fresh interpreter with `forbidden_root` poisoned.

    A meta-path finder raising AssertionError on the poisoned root is installed
    before `body` runs, so any import of that root -- direct or transitive --
    aborts the child with a non-zero exit. Runs in a subprocess on purpose: a
    module already resident in this test process's sys.modules would never
    consult the finder, masking a violation. Returns the completed process so
    the caller can assert on returncode and stderr.
    """
    program = "FORBIDDEN_ROOT = " + repr(forbidden_root) + "\n" + _POISON_FINDER + body
    return subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_the_core_imports_without_numpy() -> None:
    result = _run_guarded(_CORE_IMPORTS, forbidden_root="numpy")
    assert result.returncode == 0, result.stderr


def test_the_core_imports_without_typer() -> None:
    result = _run_guarded(_CORE_IMPORTS, forbidden_root="typer")
    assert result.returncode == 0, result.stderr


def test_the_core_imports_without_cv2() -> None:
    result = _run_guarded(_CORE_IMPORTS, forbidden_root="cv2")
    assert result.returncode == 0, result.stderr
```

  - How the child program is assembled: `_run_guarded` prepends `FORBIDDEN_ROOT = <repr>` to `_POISON_FINDER` (which reads that name) and then the `body`. `repr(forbidden_root)` yields a safe Python string literal, and no f-string is used anywhere, so the multi-line-f-string rule is not in play.
- [ ] Run it -- expected GREEN:
  - `uv run pytest tests/test_import_guard.py`
  - Expected: 3 tests pass. (No core module imports numpy, typer, or cv2 at load time, so each poisoned child interpreter exits 0.)
- [ ] Commit: `git add -A && git commit -m "Add a shared poisoned-import guard helper and core layering tests"`

---

## Task 13: Full verification

**Files:** none (verification only).

**Interfaces:**

- Consumes: everything produced above.
- Produces: a green package -- formatter, linter, type checker, and full test suite all pass.

**Steps:**

- [ ] Format (should be a no-op on verbatim-copied files, which were already ruff-formatted upstream):
  - `uv run ruff format src/ tests/`
  - Expected: reports files left unchanged, or reformats only the newly authored files with no content-meaning change.
- [ ] Lint:
  - `uv run ruff check src/ tests/`
  - Expected: `All checks passed!`
- [ ] Type-check (scope includes `tests/`, per the repository invariant):
  - `uv run basedpyright src/ tests/`
  - Expected: `0 errors, 0 warnings, 0 notes`.
- [ ] Full test suite through the machine-local lock (this is a whole-suite run, so it goes through `heavy`; it builds ffmpeg fixtures and runs the subprocess import guard):
  - `heavy uv run pytest`
  - Expected: all tests pass, `bench`-marked tests deselected (there are none yet). Trust the final `heavy-task: exit-status=0` line.
- [ ] If the working tree has staged changes from the format step, commit: `git add -A && git commit -m "Format and finalize the probe extraction"`. Otherwise no commit is needed.

---

## Self-review

### 1. Spec-requirement coverage (every in-scope requirement maps to a task)

- Scaffolding / `pyproject.toml` per spec skeleton, `uv_build`, floor 3.12, `dependencies = []`, `io`/`cli` extras, `dev`/`bench` groups, pytest `addopts`/markers, ruff+basedpyright config, `uv lock`/`uv sync` -> Task 1. `[project.scripts]` deliberately omitted (divergence recorded in Task 1). Dev pins set to current sibling versions.
- Probe copy of the 10 modules (`errors, ffprobe, timing, gop, boxes, facts, probe, candidates, policy, verdict`) verbatim with filenames unchanged -> Tasks 3-6 (all 10 present; imports verified package-relative, so verbatim copy is correct).
- `sequence.py` split (only `uniform_properties`, `canonical_fps`, `VideoProperties`, plus `MeasuredVideoProperties`, `measured_or_none`, `PropertyMismatch` verified as their dependencies; `duplicate_stems` and its `Counter`/`Path` imports left behind) -> Task 7.
- `thumbnail/` subpackage (`extract.py`, `downscale.py`, `__init__.py` re-exporting the three names) -> Task 8.
- `hwaccel.py` at top level, stdlib only, exact public contract `ffmpeg_available`/`nvdec_available`/`encoder_available(name)`, with subprocess-mocked unit tests -> Task 9.
- `timing.py` justification rewrite with the exact replacement comment quoted -> Task 4.
- Facade `__init__.py` derived from `media_probe.__init__` minus `media_type_for_container` and `duplicate_stems`, full content shown -> Task 10.
- Tests copied into `tests/probe/` (and `test_thumbnail.py`/`test_downscale.py` to `tests/thumbnail/test_extract.py`/`test_downscale.py`), unmodified apart from import paths; helpers brought over; `test_purity.py` adapted -> Tasks 2, 3-8, 10, 11. Non-copied tests (`test_media_types`, `test_facts_io`, `test_thresholds`, and the three `duplicate_stems` cases) documented in the scope boundary with the reason each stays behind.
- Import guard with `sys.meta_path` poisoning in a fresh subprocess, exposed through the single shared `_run_guarded(body, *, forbidden_root)` helper so the later reader and cli plans append their own guard tests instead of re-inventing the idiom; full code shown -> Task 12.
- README: no change -> honored (no task touches it).

No gap found.

### 2. Placeholder scan

No "TBD", "similar to Task N", "add error handling", or code-free implementation steps. Every module/test body is either shown in full (`hwaccel.py`, the facade, both `__init__.py` files, `test_purity.py`, `test_import_guard.py`, `test_hwaccel.py`, `conftest.py`) or produced by an exact `cp` plus exact `sed`/replace edits (all verbatim copies), and every check has an exact command and expected output.

### 3. Type/signature consistency across tasks

- `Packet` is declared with exactly three fields (`time`, `size`, `keyframe`) in Task 3, and the io plan's later addition of `pos` is explicitly flagged as out of scope -- no task adds it.
- `hwaccel` public names in Task 9's Interfaces match the module body and the test exactly (`ffmpeg_available`, `nvdec_available`, `encoder_available`), and the private cache names (`_ffmpeg_ok`, `_nvdec_ok`, `_encoder_ok`) match between the module and the test's reset fixture.
- The facade `__all__` (24 names) equals `media_probe.__all__` (26) minus `duplicate_stems` and `media_type_for_container`, and every listed name is imported in the same file. `hwaccel` is intentionally not in the facade (reached by submodule path), consistent with the spec's derivation instruction.
- `thumbnail` public surface (`extract_first_frame`, `downscale_to_jpeg`, `thumbnail_dimensions`) is consistent across Task 8's `__init__.py`, the facade import, and the copied tests.
- The `thumbnail -> ..probe.errors` relative import is consistent with the purity test (which ignores relative imports) and the import guard (no forbidden root).
- The `_run_guarded(body: str, *, forbidden_root: str) -> subprocess.CompletedProcess[str]` contract in Task 12 is a load-bearing signature: the later reader and cli plans consume it verbatim, so its parameter names, single-root poison semantics, and return type are fixed here and must not drift.

No inconsistency found.
