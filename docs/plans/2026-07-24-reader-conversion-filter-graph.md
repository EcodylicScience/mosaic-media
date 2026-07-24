# Reader conversion through the filter graph: implementation plan

> **For implementers:** execute this plan task by task, with a fresh
> implementer per task and a review between tasks. Steps use checkbox
> (`- [ ]`) syntax for tracking. Do not start a task before its predecessor
> has been reviewed.

**Goal:** Replace `VideoReader._emit`'s per-frame `to_ndarray(format=...)` and
`reformat(...)` calls with a single libavfilter graph, built once per reader,
carrying rotation, scaling, and pixel format together.

**Architecture:** The reader already builds a persistent filter graph for
rotation and then converts the graph's output with a second, per-frame call.
That second call builds a fresh libswscale scaling context every frame, and on
the resized color path it does not reproduce ffmpeg's chroma-plane scaling. One
graph carrying every stage removes both problems: the repeated setup cost and
the chroma error. The graph is built lazily on first conversion, because the
reader does not open its container during geometry resolution when probe facts
are injected.

**Tech Stack:** Python 3.12, PyAV (`av`), numpy, pytest. System ffmpeg and
ffprobe on PATH supply the golden references.

**Design reference:** `docs/specs/2026-07-24-reader-conversion-filter-graph.md`.

## Global Constraints

- The package targets Python 3.12 or newer. Do not raise the floor.
- `numpy` and `av` belong to the `[io]` extra. This work touches only
  `src/mosaic_media/io/`, so no dependency moves and no new dependency is added.
- Never import `mosaic` or `mosaic_api`. Both consume this package.
- No `typing.Any`, no `typing.Optional` (use `X | None`), no `typing.cast`.
- No suppressions: no `# noqa`, no `# pyright: ignore`, no `# type: ignore`. A
  type error is a design signal; fix the design.
- A parameter, field, or return value ranging over a closed set of strings is a
  `Literal` alias, never bare `str`.
- ASCII only in code and comments. American spelling.
- No conventional-commit prefixes in commit messages (`feat:`, `fix:`, and the
  rest). Plain English. No co-authorship trailers.
- Do not change any performance gate threshold in this work.
- Verification commands, run from the repository root:
  - `uv run ruff format src/ tests/`
  - `uv run ruff check src/ tests/`
  - `uv run basedpyright src/ tests/`
  - `uv run pytest tests/`
- The full test suite and the performance gate are long, CPU-saturating runs.
  Both must be serialized through whatever serialization mechanism the machine
  provides, so that only one such run executes at a time; the wrapper command is
  a property of the machine, not of this repository, and the bare commands given
  in the steps below are the payload to wrap, not the whole invocation. The gate
  additionally requires an otherwise idle machine and must never run in
  parallel: `pytest -m bench -n0 -s`. Contended measurements swing roughly 2x
  run to run, which makes the gate meaningless.

## Branch

Create a feature branch before Task 1; this plan is not executed on `main`.

```bash
git switch -c reader-conversion-filter-graph
```

## File Structure

| File | Responsibility | Change |
| --- | --- | --- |
| `src/mosaic_media/io/reader.py` | Frame decoding and conversion | Replace the rotation-only graph and the per-frame conversion calls with one conversion graph |
| `tests/io/test_reader_frame_contract.py` | The writable, contiguous, non-aliasing output contract | Extend from the plain path to every conversion path, then correct its docstring once the buffer it describes changes |
| `tests/io/test_reader_resize_content.py` | Resize output against ffmpeg goldens | Tighten tolerances, add rotated cases, correct the stated rationale |
| `tests/helpers/corpus.py` | Golden generation helpers | Correct `scaled_frames`'s stated reason for returning pixels |
| `tests/bench/test_sequential_decode.py` | Sequential and strided gates | Correct the tier rationale; thresholds unchanged |
| `tests/bench/test_seek_workloads.py` | Seek gates | Correct the tier rationale; threshold unchanged |
| `tests/bench/test_sparse_and_multi.py` | Sparse and multi-video gates | Correct the tier rationale; threshold unchanged |
| `docs/specs/_INDEX.md`, `docs/plans/_INDEX.md` | Document indexes | One row each |

Three bench modules and one test helper carry a rationale naming the per-frame
reformat this change deletes. Every such site is corrected in the same branch;
a comment describing a mechanism the code no longer has is worse than no
comment, because it reads as current.

---

### Task 1: Track the design documents

The spec and this plan exist on disk but are untracked. They enter git as
implementation begins, each with its index row in the same commit.

**Files:**
- Modify: `docs/specs/_INDEX.md`
- Modify: `docs/plans/_INDEX.md`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Add the spec's index row**

In `docs/specs/_INDEX.md`, add this row to the bottom of the table:

```markdown
| `2026-07-24-reader-conversion-filter-graph.md` | active | - | Move the frame reader's pixel format conversion, scaling, and rotation into one persistent libavfilter graph built per reader, replacing the per-frame reformat calls. Removes repeated scaling-context construction and fixes a chroma-plane scaling divergence that put the resized bgr24 output up to 76 levels away from ffmpeg's `-vf scale`. |
```

- [ ] **Step 2: Add the plan's index row**

In `docs/plans/_INDEX.md`, add this row to the bottom of the table:

```markdown
| `2026-07-24-reader-conversion-filter-graph.md` | active | - | Task-by-task implementation of the reader conversion graph: extend the frame contract to every conversion path, move conversion into the graph, tighten the resize tolerances, and correct the gate tier rationale. |
```

- [ ] **Step 3: Commit**

```bash
git add docs/specs/2026-07-24-reader-conversion-filter-graph.md \
        docs/plans/2026-07-24-reader-conversion-filter-graph.md \
        docs/specs/_INDEX.md docs/plans/_INDEX.md
git commit -m "Add the design for the reader conversion filter graph"
```

---

### Task 2: Extend the frame contract to every conversion path

The contract test constructs a plain reader only, so contiguity is pinned on the
one path where the graph happens to preserve it for free. Broaden the test
first, on unchanged code, so it guards Task 3 rather than being written to fit
it.

**Files:**
- Modify: `tests/io/test_reader_frame_contract.py`

**Interfaces:**
- Consumes: `VideoReader` from `mosaic_media.io.reader`; the `corpus_gop12`
  session fixture from `tests/conftest.py`; `generate_video` from
  `tests/helpers/corpus.py`.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Replace the test module body**

Replace the whole of `tests/io/test_reader_frame_contract.py` with:

```python
"""Decoded frames are writable, C-contiguous, non-aliasing buffers.

Consumers draw overlays directly onto returned frames. The array wraps the
converted frame's own buffer, so numpy's OWNDATA flag is False by design; the
contract that matters is writability, C-contiguity, and that consecutive reads
never alias one another -- mutating one returned frame must not be able to
corrupt another.

Every conversion path is covered, not just the plain one. Rotation and scaling
pad the line size, so contiguity is the guarantee most at risk on exactly the
paths a plain-path-only test leaves unchecked.
"""

from pathlib import Path

import numpy
import pytest

from mosaic_media.io.reader import VideoReader
from tests.helpers.corpus import generate_video


def _assert_contract(first: numpy.ndarray, second: numpy.ndarray) -> None:
    for frame in (first, second):
        assert frame.flags["WRITEABLE"]
        assert frame.flags["C_CONTIGUOUS"]
    assert not numpy.may_share_memory(first, second)
    untouched = second.copy()
    first[:] = 0
    assert numpy.array_equal(second, untouched)


@pytest.mark.parametrize(
    ("resize", "grayscale"),
    [
        (None, False),
        (None, True),
        ((160, 120), False),
        ((160, 120), True),
    ],
)
def test_frames_are_writable_c_contiguous_and_non_aliasing(
    corpus_gop12: Path,
    resize: tuple[int, int] | None,
    grayscale: bool,
) -> None:
    with VideoReader(corpus_gop12, resize=resize, grayscale=grayscale) as reader:
        ok_first, first = reader.read()
        ok_second, second = reader.read()
    assert ok_first
    assert ok_second
    assert first is not None
    assert second is not None
    _assert_contract(first, second)


@pytest.mark.parametrize("rotation_degrees", [90, 180, 270])
def test_rotated_frames_hold_the_contract(
    tmp_path: Path, rotation_degrees: int
) -> None:
    path = generate_video(
        tmp_path / f"rot{rotation_degrees}.mp4",
        frames=6,
        fps=30.0,
        gop=12,
        rotation_degrees=rotation_degrees,
    )
    with VideoReader(path) as reader:
        ok_first, first = reader.read()
        ok_second, second = reader.read()
    assert ok_first
    assert ok_second
    assert first is not None
    assert second is not None
    _assert_contract(first, second)
```

- [ ] **Step 2: Run the extended test on unchanged code**

```bash
uv run pytest tests/io/test_reader_frame_contract.py -v
```

Expected: all seven cases PASS. They must pass before the reader changes -- that
is what makes them a guard. If any fails here, stop and report it: the contract
is already broken on that path and Task 3 is not the cause.

- [ ] **Step 3: Check formatting, linting, and types**

```bash
uv run ruff format src/ tests/
uv run ruff check src/ tests/
uv run basedpyright src/ tests/
```

Expected: all clean.

- [ ] **Step 4: Commit**

```bash
git add tests/io/test_reader_frame_contract.py
git commit -m "Check the frame buffer contract on every conversion path"
```

---

### Task 3: Move conversion into the filter graph

**Files:**
- Modify: `src/mosaic_media/io/reader.py`
- Modify: `tests/io/test_reader_resize_content.py`
- Modify: `tests/helpers/corpus.py`
- Modify: `tests/io/test_reader_frame_contract.py` (docstring only; the tests
  themselves stay untouched)

**Interfaces:**
- Consumes: `_Geometry` (fields `fps`, `source_frame_count`, `out_width`,
  `out_height`), `_ROTATION_FILTERS`, `MediaProbeError`, and the `_stream`,
  `_resize`, `_grayscale`, `_rotation_degrees`, `_path` attributes, all already
  present in `src/mosaic_media/io/reader.py`.
- Produces: `VideoReader._build_conversion_graph(self, geometry: _Geometry) -> Graph`
  and the renamed attribute `self._conversion_graph: Graph | None`. `_emit`
  keeps its existing signature
  `(self, geometry: _Geometry, frame: VideoFrame) -> numpy.ndarray`.

- [ ] **Step 1: Write the failing rotated-resize tests**

Replace the whole of `tests/io/test_reader_resize_content.py` with:

```python
"""The reader's resize path produces bicubic-scaled content matching ffmpeg.

The reader scales with bicubic interpolation to match system ffmpeg's `-vf
scale` default; libav's own reformat default is bilinear. These tests check the
resized frames against ffmpeg's scale output, upright and rotated.

Both the grayscale and the color path are held to a tolerance of 2, and both
measure 0 or 1 against the golden. The tolerance is small but deliberately
nonzero: scaling is arithmetic, and its result could differ by a rounding step
between the bundled libav the reader decodes with and the system ffmpeg the
goldens come from. It stays far below the bilinear regression, which drifts
about 24 gray levels.

Scaling inside the reader's filter graph is what makes the color path exact.
Driving libswscale through VideoFrame.reformat instead diverges from ffmpeg on
the chroma planes -- luma stays bit-identical, chroma reaches 20 to 27 -- and
that error is amplified once it crosses into BGR, reaching 57 upright and 76
rotated across the 48-frame corpus_gop12 clip. The color conversion on its own
is exact in both libraries, so the cause is chroma plane scaling, not a library
version difference and not the conversion.
"""

from pathlib import Path

import numpy
import pytest

from mosaic_media.io.reader import VideoReader
from tests.helpers.corpus import generate_video, scaled_frames

TOLERANCE = 2


def _max_channel_difference(a: numpy.ndarray, b: numpy.ndarray) -> int:
    return int(numpy.abs(a.astype(numpy.int16) - b.astype(numpy.int16)).max())


def test_grayscale_resize_is_bicubic_against_ffmpeg_scale(corpus_gop12: Path) -> None:
    goldens = scaled_frames(corpus_gop12, 160, 120, grayscale=True)
    with VideoReader(corpus_gop12, resize=(160, 120), grayscale=True) as reader:
        produced = [frame for _index, frame in reader]
    assert len(produced) == len(goldens)
    for frame, golden in zip(produced, goldens):
        assert frame.shape == (120, 160)
        assert _max_channel_difference(frame, golden) <= TOLERANCE


def test_bgr_resize_is_bicubic_against_ffmpeg_scale(corpus_gop12: Path) -> None:
    goldens = scaled_frames(corpus_gop12, 160, 120)
    with VideoReader(corpus_gop12, resize=(160, 120)) as reader:
        produced = [frame for _index, frame in reader]
    assert len(produced) == len(goldens)
    for frame, golden in zip(produced, goldens):
        assert frame.shape == (120, 160, 3)
        assert _max_channel_difference(frame, golden) <= TOLERANCE


@pytest.mark.parametrize("rotation_degrees", [90, 180, 270])
@pytest.mark.parametrize("grayscale", [False, True])
def test_rotated_resize_is_bicubic_against_ffmpeg_scale(
    tmp_path: Path, rotation_degrees: int, grayscale: bool
) -> None:
    # ffmpeg autorotates on decode, so the -vf scale golden is the rotated and
    # then scaled frame -- exactly the composition the reader emits.
    path = generate_video(
        tmp_path / f"rot{rotation_degrees}_{grayscale}.mp4",
        frames=12,
        fps=30.0,
        gop=12,
        rotation_degrees=rotation_degrees,
    )
    goldens = scaled_frames(path, 160, 120, grayscale=grayscale)
    with VideoReader(path, resize=(160, 120), grayscale=grayscale) as reader:
        produced = [frame for _index, frame in reader]
    assert len(produced) == len(goldens)
    expected_shape = (120, 160) if grayscale else (120, 160, 3)
    for frame, golden in zip(produced, goldens):
        assert frame.shape == expected_shape
        assert _max_channel_difference(frame, golden) <= TOLERANCE
```

- [ ] **Step 2: Run the tests to verify they fail for the right reason**

```bash
uv run pytest tests/io/test_reader_resize_content.py -v
```

Expected: 8 cases collected, 4 passed, 4 failed. The four grayscale cases pass
(they measure 1, inside the tolerance of 2). The four color cases fail --
`test_bgr_resize_is_bicubic_against_ffmpeg_scale` and the three rotated color
cases -- each on the tolerance assertion.

The number pytest prints is not the clip's worst difference. The assertion sits
inside the per-frame loop, so pytest reports the first offending frame, and the
rotated cases here use 12-frame clips while the whole-clip maxima quoted
elsewhere (57 upright, 76 rotated) come from the 48-frame corpus. Expect printed
values roughly in the 45 to 70 range, such as `assert 48 <= 2`.

Confirm the failure is the assertion comparing against `TOLERANCE`, not an
error, an import failure, or a shape mismatch. If a color case passes here, stop
and report it: the defect the change targets is not present and the plan's
premise is wrong.

- [ ] **Step 3: Rename the graph attribute**

In `src/mosaic_media/io/reader.py`, in `__init__`, replace:

```python
        self._rotation_graph: Graph | None = None
```

with:

```python
        self._conversion_graph: Graph | None = None
```

- [ ] **Step 4: Replace the graph builder**

Replace the whole `_build_rotation_graph` method with:

```python
    def _build_conversion_graph(self, geometry: _Geometry) -> Graph:
        """Build the reader's one conversion graph: rotation, then scaling, then
        the output pixel format.

        Stage order is load-bearing. The transpose runs first so a quarter-turn
        source is emitted in displayed orientation, and the scale runs after it
        so a requested resize wins over the rotation dimension swap and the
        output is exactly the requested size.

        Scaling here rather than through VideoFrame.reformat is what keeps the
        color path exact: reformat drives libswscale with different chroma plane
        handling and lands up to 76 levels per channel away from ffmpeg's `-vf
        scale`, while this filter is what ffmpeg itself runs.
        """
        stream = self._stream
        if stream is None:
            message = f"cannot build the conversion graph before opening {self._path}"
            raise MediaProbeError(message)
        stages: list[tuple[str, str | None]] = list(
            _ROTATION_FILTERS.get(self._rotation_degrees % 360, ())
        )
        if self._resize is not None:
            # Bicubic matches system ffmpeg's scale default; libav's own default
            # is bilinear, which drifts about 24 gray levels off the goldens.
            scale_arguments = (
                f"{geometry.out_width}:{geometry.out_height}:flags=bicubic"
            )
            stages.append(("scale", scale_arguments))
        stages.append(("format", "gray" if self._grayscale else "bgr24"))
        graph = Graph()
        previous = graph.add_buffer(template=stream)
        for name, argument in stages:
            node = graph.add(name) if argument is None else graph.add(name, argument)
            previous.link_to(node)
            previous = node
        sink = graph.add("buffersink")
        previous.link_to(sink)
        try:
            graph.configure()
        except av.error.FFmpegError as exc:
            message = f"failed to build the conversion graph for {self._path}: {exc}"
            raise MediaProbeError(message) from exc
        self._conversion_graph = graph
        return graph
```

- [ ] **Step 5: Replace `_emit`**

Replace the whole `_emit` method with:

```python
    def _emit(self, geometry: _Geometry, frame: VideoFrame) -> numpy.ndarray:
        # The graph is built here rather than during geometry resolution because
        # _ensure_ready does not open the container when probe facts are
        # injected, and the buffer source is templated from the stream. By the
        # first emit a frame has been decoded, so the container is open.
        graph = self._conversion_graph
        if graph is None:
            graph = self._build_conversion_graph(geometry)
        graph.vpush(frame)
        converted = graph.vpull()
        # to_ndarray takes no format argument: the graph already emits the
        # output pixel format, and passing one would ask libswscale for a no-op
        # conversion and rebuild a scaling context for every frame.
        # ascontiguousarray is a no-op when the line size already matches and
        # copies when the graph padded it, which rotation and scaling do.
        return numpy.ascontiguousarray(converted.to_ndarray())
```

- [ ] **Step 6: Update the module docstring**

In `src/mosaic_media/io/reader.py`, in the module docstring, replace these four
lines exactly as they appear (lines 11-14, ending the first paragraph):

```
codec guard), not a trusted bundled binary. Rotation is applied in process
through a libav filter graph (a transpose for the quarter-turns, hflip plus
vflip for 180), golden-verified bit-exact against system-ffmpeg autorotation
for every mapped rotation.
```

with:

```
codec guard), not a trusted bundled binary. Rotation, scaling, and the output
pixel format are applied in process through one libav filter graph per reader (a
transpose for the quarter-turns, hflip plus vflip for 180), golden-verified
against system ffmpeg: bit-exact for rotation, and within a rounding step for
scaling, where driving libswscale directly instead diverges on the chroma
planes.
```

- [ ] **Step 7: Correct the geometry comment that names the reformat**

In `src/mosaic_media/io/reader.py`, in `_ensure_ready`, the comment above the
resize branch (around line 190) reads:

```python
            # A resize wins over the rotation swap; the reformat runs after the
            # transpose, so the output is exactly the requested (width, height).
```

There is no reformat after this change. Replace it with:

```python
            # A resize wins over the rotation swap; the scale filter runs after
            # the transpose, so the output is exactly the requested
            # (width, height).
```

- [ ] **Step 8: Correct the golden helper's stated rationale**

`tests/helpers/corpus.py`, in the `scaled_frames` docstring, explains why it
returns pixels rather than digests with "the bundled-versus-system swscale skew
forces a tolerance comparison on the color path, which needs the pixels". That
rationale is refuted -- the color conversion measures 0 between the two
libraries -- and after this change the color path measures 0 against the golden
too. Replace that clause so the docstring reads:

```python
    """Per-frame ground truth for the reader's resize path: system ffmpeg's
    `-vf scale=width:height` output, in presentation order, in the reader's
    output pixel format (bgr24, or gray when grayscale=True). ffmpeg's scale
    default is bicubic, so this is the reference the reader's bicubic resize is
    checked against. Returned as raw pixel arrays -- not md5 digests -- because
    scaling is arithmetic that may differ by a rounding step between the
    bundled libav and the system ffmpeg, so the comparison is a tolerance
    rather than an equality, which needs the pixels."""
```

- [ ] **Step 9: Correct the frame contract docstring**

`tests/io/test_reader_frame_contract.py` describes the buffer the returned array
wraps. Two of its statements stop being true once conversion moves into the
graph: the array is no longer always a view (`ascontiguousarray` copies wherever
the graph padded the line size, and an owned copy reports `OWNDATA` as True),
and the line-size padding it attributes to rotation and scaling is a property of
the graph rather than of the code it described when written.

Replace the module docstring with:

```python
"""Decoded frames are writable, C-contiguous, non-aliasing buffers.

Consumers draw overlays directly onto returned frames. Where the converted
frame's line size already matches its width the array wraps that buffer
directly; where the graph padded it, the array is an owned contiguous copy. The
contract is the same either way, and it is what these tests pin: writability,
C-contiguity, and that consecutive reads never alias one another -- mutating one
returned frame must not be able to corrupt another.

Every conversion path is covered, not just the plain one. The graph pads the
line size for scaled output and for the quarter-turn rotations, so contiguity is
the guarantee most at risk on exactly the paths a plain-path-only test leaves
unchecked.
"""
```

Change nothing else in the file: the tests themselves must stay exactly as they
are. They passed before the reader change and must pass after it unaltered,
which is the entire reason they were written first.

- [ ] **Step 10: Run the resize tests to verify they now pass**

```bash
uv run pytest tests/io/test_reader_resize_content.py -v
```

Expected: all eight cases PASS.

- [ ] **Step 11: Run the reader and frame contract suites**

```bash
uv run pytest tests/io/ -v
```

Expected: all PASS. The rotation goldens, the grayscale framemd5 golden in
`tests/io/test_reader_strided.py`, the cv2 equality suite, and the seek, sparse,
and variable-rate suites must all stay green unchanged. Do not edit any of them
to accommodate the change: if one fails, the change is wrong, not the test.

- [ ] **Step 12: Check formatting, linting, and types**

```bash
uv run ruff format src/ tests/
uv run ruff check src/ tests/
uv run basedpyright src/ tests/
```

Expected: all clean. Fix mechanical typing issues in code you wrote. If a type
error reveals a design problem rather than a missing annotation, stop and report
it instead of working around it.

- [ ] **Step 13: Run the full suite**

Run the full suite serialized through whatever serialization mechanism the
machine provides, in the foreground:

```bash
uv run pytest tests/
```

Expected: all PASS.

- [ ] **Step 14: Commit**

```bash
git add src/mosaic_media/io/reader.py tests/io/test_reader_resize_content.py \
        tests/io/test_reader_frame_contract.py \
        tests/helpers/corpus.py
git commit -m "Convert decoded frames through one filter graph per reader"
```

**Note on coverage:** two guards in `_build_conversion_graph` are unreachable
through the public API -- the `stream is None` check, because `_emit` only runs
after a frame has been decoded, and the `graph.configure()` wrapper, because an
unmapped rotation is rejected in `_ensure_ready` and the scale and format
arguments are always well-formed. Both are required and both stay. No test
covers them, and that is expected, not an omission.

---

### Task 4: Correct the gate tier rationale and report the new ratios

The carve tier's stated reason is a per-frame reformat that no longer exists.
The thresholds themselves are not touched here: a faster reader raises every
ratio, and re-deriving the numbers is a decision made on measurements, not a
step folded into the change that moved them.

**Files:**
- Modify: `tests/bench/test_sequential_decode.py`

**Interfaces:**
- Consumes: nothing from earlier tasks beyond the reader change itself.
- Produces: nothing.

- [ ] **Step 1: Correct the tier rationale at all three sites**

Three bench modules explain a threshold by the per-frame reformat this change
deletes. All three are corrected here; leaving any one behind makes the suite
disagree with itself about what the carve tier means, and
`test_sparse_and_multi.py` names the sequential tier explicitly, so the two
would contradict each other outright.

In `tests/bench/test_sequential_decode.py`, replace the comment block above
`_SEQUENTIAL_FULL_DECODE_THRESHOLD` with:

```python
# Tier CARVE (>= 0.9) for gop12 and gop250 (see the spec's "Gate policy and
# thresholds, revised for in-process decode"). These two were measured when the
# reader converted each frame with its own reformat call, building a scaling
# context per frame. The reader now converts through one filter graph per
# reader, so the cost the carve allowed for is gone and the measured ratios sit
# well above these numbers. They are retained until re-derived from a fresh
# measurement rather than raised alongside the change that made them slack. The
# rotation key is a full-tier >= 1.0 gate, not the carve: its variant already
# converted through a filter graph before this change.
```

Keep the dictionary and its per-key values exactly as they are, including the
recorded stabilization medians in the trailing comments.

In `tests/bench/test_seek_workloads.py`, replace the comment block above
`_SEEK_THEN_SEQUENTIAL_THRESHOLD` with:

```python
# Tier CARVE (>= 0.9): measured against a per-frame reformat that no longer
# exists, now that the reader converts through one filter graph per reader (see
# the spec's "Gate policy and thresholds, revised for in-process decode").
# Retained until re-derived from a fresh measurement. Stabilization medians:
# gop12 1.011, gop250 0.986.
```

In `tests/bench/test_sparse_and_multi.py`, in the docstring of
`test_gate_multi_video_junction_with_injected_facts`, replace the sentence "The
0.9 threshold is the owned-BGR-copy carve, the same tier as sequential decode,
which this workload is once the open cost is out of the way." with:

```python
    The 0.9 threshold is the same carve tier as sequential decode, which this
    workload is once the open cost is out of the way; both were measured
    against a per-frame reformat the reader no longer performs.
```

Keep the rest of that docstring, including the sentence about the from-scratch
construction staying a bounded report, exactly as it is.

- [ ] **Step 2: Check formatting, linting, and types**

```bash
uv run ruff format src/ tests/
uv run ruff check src/ tests/
uv run basedpyright src/ tests/
```

Expected: all clean.

- [ ] **Step 3: Measure the gate and report the ratios**

The gate needs an otherwise idle machine and must not run in parallel. Run it
serialized, in the foreground:

```bash
uv run pytest -m bench -n0 -s
```

Record the printed `ratio cv2/reader` for every gated workload -- sequential,
strided, seek, sparse, metadata, and the multi-video junction gate -- and also
for the two non-gating reports, cold random seek and the from-scratch
multi-video junction, which are bounded rather than gated. Report those numbers.
Do not change a threshold, and do not change a test to make a gate pass: if a
gate fails, report the numbers and stop.

- [ ] **Step 4: Commit**

```bash
git add tests/bench/test_sequential_decode.py tests/bench/test_seek_workloads.py \
        tests/bench/test_sparse_and_multi.py
git commit -m "Correct the gate tier rationale for graph conversion"
```

---

## Definition of done

- The full suite passes.
- `uv run ruff check src/ tests/` and `uv run basedpyright src/ tests/` are
  clean, with no suppressions added anywhere.
- No gate threshold changed; measured ratios for every gated workload reported.
- No test weakened or deleted to accommodate the change.
- `src/mosaic_media/io/reader.py` contains no `reformat` call and no
  `to_ndarray(format=...)` call.
