# Deliverable frame model implementation plan

> Execute task-by-task with a fresh implementer per task and a review between
> tasks. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every frame index inside `MediaFacts.frame_count` either return a
frame or raise an error naming the reason, without narrowing the frame model and
without adding a packet scan to any path that runs none today.

**Architecture:** The probe keeps counting packet timestamps and gains two
demux-visible counts. The reader recovers the two classes of frame a default
decode drops -- packets before the first keyframe, via the decoder's `show_all`
flag, and edit-list discard packets, via the demuxer's `ignore_editlist` option
gated on a measured count. The third class, packets carrying no coded picture, is
undetectable from the container and is handled by a caller-injected trusted codec
set that routes an untrusted source to a re-encode. Command construction refuses
a stream copy that would not deliver every frame.

**Tech Stack:** Python 3.12, PyAV (`av>=18,<19`), numpy, system ffmpeg and
ffprobe, pytest, basedpyright strict, ruff.

**Source spec:** `docs/specs/2026-08-01-deliverable-frame-model.md`. Read it
before Task 1; every task below implements a numbered change from it.

## Global constraints

- Layering: `probe/` is standard library only. `numpy` and `av` may be imported
  only from `io/`. `typer` only from `cli/`. `tests/test_import_guard.py`
  enforces this.
- Never import `mosaic` or `mosaic_api`. They are consumers.
- basedpyright runs strict over `src/ tests/ scripts/`. No `typing.Any`, no
  `typing.Optional` (write `X | None`), no `typing.cast`, no `# noqa`, no
  `# pyright: ignore`, no `# type: ignore`. A finding is a design signal; fix the
  design.
- A parameter, field, or return value ranging over a closed set of strings is a
  named `Literal` alias, never bare `str`. Test helpers hold to this exactly.
- Assign a parenthesized or multi-line message to a variable before raising or
  passing it; never pass adjacent string literals bare into a call.
- Never delete a test because a contract changed. Rewrite it to assert the new
  contract. Two tasks below depend on this.
- Test code obeys DRY like any other code. A second copy of a fixture, a setup
  block, or an assertion helper is real duplication; extract and migrate every
  caller in the same change. Repetition that IS the subject under test -- varied
  inputs across explicit cases -- is exempt.
- ASCII only, American spelling, no abbreviations in identifiers.
- No GPL encoder named in any `.py` under `src/` or `tests/`
  (`tests/test_encoder_guard.py` scans `*.py` only, so `tests/assets/README.md`
  may name one).
- Commit messages: plain English, no conventional-commit prefixes, no
  `Co-Authored-By`, no references to plans, tasks, or tooling.
- Corpus files are not redistributable and are never named in a committed
  artifact.
- Checks: `uv run ruff format`, `uv run ruff check`, `uv run basedpyright`,
  `uv run pytest tests/`, each over `src/ tests/ scripts/`. Full runs are
  offloaded to the configured task machine; never run `-m bench` on a
  development machine.
- **Every run-to-fail step is executed, and its real output recorded.** The step
  gives an exact selector, exact test names, and the failure each should show.
  Run it, read what pytest actually prints, and paste that into the task record
  instead of confirming the prediction. Where they disagree, the plan is wrong
  until proven otherwise: a predicted failure that is only confirmed is
  unverifiable, and a test that cannot fail leaves no trace in a green run.
  "No tests ran" means the selector is stale, not that the code is fine. Read
  `ERROR` as distinct from `FAILED`: an error means the test could not execute,
  which satisfies "did not pass" while proving nothing, so every expected-failure
  block wants failures and no errors.
- **Call sites are located, never counted.** Where a task changes a signature, a
  constructor, or any contract with more than one caller, this plan gives a
  search recipe instead of a number, and the recipe is what you run. A written
  count goes stale as soon as a test is added, and its failure mode is not a lint
  error but a wrong or missing update discovered later -- for a required
  keyword-only parameter, a `TypeError` at run time. Three counts in an earlier
  draft of this plan were wrong, one of them hiding a call that would have broken
  its own task's verification step. If a passage still states a number, treat the
  number as stale and search.

## Starting state of this branch

Branch `transcode-output-container-and-clock`. `HEAD` is
`8e53307 Add issue for frames the reader cannot deliver but the facts declare`.
**Nothing below is committed yet** -- it is uncommitted working-tree state, and
Task 1 inherits it:

- Modified `src/mosaic_media/transcode/commands.py`: adds
  `_MP4_STREAM_COPY_CODECS` (`:85`), splits the selector into `_select_operation`
  (`:171`, the escalation wrapper) over `_select_minimum_operation` (`:185`), and
  escalates a copy remux to an AV1 re-encode when mp4 cannot carry the source
  codec. Task 6 extends that wrapper; it does not rewrite it.
- Modified `tests/transcode/conftest.py`: fixtures `avi_starting_on_non_keyframes`
  and `lying_header_vp8_webm`, neither of them tracked. Task 3 commits this file
  when it moves the first fixture out, which tracks the second as a side effect
  and makes the file's formatting and docstring this branch's to keep correct.
- Modified `tests/transcode/test_commands.py`, `tests/transcode/test_convert.py`.
  **Two tests in these files are red and assert a superseded contract.** Task 6
  rewrites them; do not delete them.
- Untracked: `tests/assets/open_gop.mp4`, `tests/assets/hevc.mp4` and their recipes in
  `tests/assets/README.md`, plus the spec and this plan. Task 0 commits all four,
  and runs first for that reason.

## File structure

| File | Responsibility | Task |
| --- | --- | --- |
| `src/mosaic_media/probe/ffprobe.py` | `Packet.discard`; parse the `D` flag | 1 |
| `src/mosaic_media/probe/facts.py` | two new `MediaFacts` counts | 1 |
| `src/mosaic_media/probe/probe.py` | compute the counts | 1 |
| `src/mosaic_media/io/index.py` | `SeekIndex` provenance | 2 |
| `src/mosaic_media/io/packets.py` | gate parameter; populate `discard` | 2 |
| `src/mosaic_media/io/reader.py` | recovery options, index rejection, seek landing, sequential backstop | 3, 4, 5 |
| `src/mosaic_media/io/multi.py` | per-segment gate and provenance | 8 |
| `src/mosaic_media/probe/policy.py` | reason literal; trusted codec set | 6 |
| `src/mosaic_media/probe/verdict.py` | fire the reason | 6 |
| `src/mosaic_media/transcode/commands.py` | route the reason; deliverability escalation; the measured mp4 carriage set | 6, 7 |
| `tests/transcode/test_mp4_stream_copy.py` | mp4 carriage measured against the real muxer | 7 |
| `tests/helpers/media_fixtures.py` | the open-GOP, mid-stream and pre-roll fixtures, beside their siblings | 3 |
| `tests/helpers/indexes.py` | shared seek-index builders | 2 |
| `tests/helpers/scans.py` | shared packet-scan counting helper | 9 |

---

### Task 0: Track the documents and the fixture

Runs first, not last. Every task from 3 onward reaches
`tests/assets/open_gop.mp4` and `tests/assets/hevc.mp4` through `asset()`, which
raises `RuntimeError` naming the missing path when no file is there
(`tests/helpers/media_fixtures.py:46-48`). An untracked asset is present in this
working tree and absent from a fresh checkout, so a checkout of any intermediate
commit would error in collection while the suite passes here. The suite passing
locally only reflects the file sitting in the working tree. A spec and a plan are
also tracked when implementation starts, each with its `_INDEX.md` row in the
same commit that first tracks it.

**Files:**
- Add: `docs/specs/2026-08-01-deliverable-frame-model.md`,
  `docs/plans/2026-08-01-deliverable-frame-model.md`,
  `tests/assets/open_gop.mp4`, `tests/assets/hevc.mp4`
- Modify: `tests/assets/README.md`, `tests/test_encoder_guard.py`,
  `docs/specs/_INDEX.md`, `docs/plans/_INDEX.md`

- [ ] **Step 1: Add the index rows**

One row each in `docs/specs/_INDEX.md` and `docs/plans/_INDEX.md`: the tracked
filename, status `active`, `-` for the last-tracked hash, and a one-line
description. No doc is tracked without its row.

- [ ] **Step 2: Widen `tests/assets/README.md` past H.264**

The two new recipes are already appended, but the document still opens "H.264
clips the suite needs but can no longer generate." and reasons only about H.264
encoders. `hevc.mp4` makes that framing wrong. Replace the title line and the
paragraph under it with both codecs, and give the real reason each encoder is
excluded rather than one reason covering both:

- The GPL software encoders (`libx264`, `libx264rgb`, `libx265`) are excluded
  because the suite must run against the same LGPL FFmpeg the consumers deploy.
  Keep that modal form. The development build carries both -- this file's own
  provenance block records `--enable-gpl`, which is why its recipes run at all --
  so an indicative "the suite runs against" is false where the suite runs, and
  makes the source denylist in `tests/test_encoder_guard.py` look redundant when
  it is the only thing catching a call site that works locally and breaks on the
  deployment build.
- `libopenh264` is not GPL and is still not the way around that: it is built for
  real-time conferencing. Its rate control offers `off`, `quality`, `bitrate`,
  `buffer` and `timestamp` with no constant-quality target, it accepts 8-bit
  4:2:0 only (`yuv420p` and `yuvj420p`), and its profiles stop at High. Nothing
  the suite or the deployment path encodes wants an encoder shaped that way.
- Decoding is unaffected either way, because both decoders are native and LGPL,
  which is what makes a committed clip free to consume.

Do not write that no non-GPL software encoder exists for these codecs. One does
for H.264, and stating otherwise contradicts what the repository already records.

Check the hardware-wrapper encoder names against `ffmpeg -encoders` on the
machine before listing them; the existing paragraph lists the H.264 wrappers and
the HEVC ones are not currently named anywhere.

`tests/test_encoder_guard.py`'s module docstring scopes the same idea to one
codec at line 15 -- "Media that must genuinely be H.264 is committed under
`tests/assets/` instead" -- while its own opening paragraph already speaks of
both. Widen that sentence to H.264 and HEVC. It is the third and last site
carrying the narrow framing.

- [ ] **Step 3: Commit**

```bash
git add docs/specs/ docs/plans/ tests/assets/open_gop.mp4 tests/assets/hevc.mp4 \
    tests/assets/README.md tests/test_encoder_guard.py
git commit -m "Add the frame delivery design and its media fixtures"
```

---

### Task 1: Two measured counts on `MediaFacts`

Implements spec change 4.

**Files:**
- Modify: `src/mosaic_media/probe/ffprobe.py` (`Packet` at 76-95; the row parser at 416-464)
- Modify: `src/mosaic_media/probe/facts.py`
- Modify: `src/mosaic_media/probe/probe.py`
- Modify: `tests/probe/test_verdict.py` (the shared `CLEAN` baseline)
- Test: `tests/probe/test_probe.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Packet.discard: bool`; `MediaFacts.discard_flagged_packets: int`;
  `MediaFacts.leading_non_keyframe_frames: int`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/probe/test_probe.py`:

```python
def test_a_source_opening_on_a_keyframe_counts_no_undeliverable_packets(
    clips: dict[str, Path],
) -> None:
    facts = probe_media(clips["cfr_mp4"])
    assert facts.discard_flagged_packets == 0
    assert facts.leading_non_keyframe_frames == 0


def test_a_source_cut_mid_stream_counts_its_leading_frames(
    avi_starting_on_non_keyframes: Path,
) -> None:
    # The fixture drops the committed clip's leading keyframe, leaving 24
    # non-keyframes ahead of the keyframe at 25.
    facts = probe_media(avi_starting_on_non_keyframes)
    assert facts.leading_non_keyframe_frames == 24
    assert facts.discard_flagged_packets == 0
```

`avi_starting_on_non_keyframes` moves to `tests/helpers/media_fixtures.py` in
Task 3; until then it is only visible to `tests/transcode/`. Write the second
test now and expect it to error on the missing fixture; Task 3 step 1 makes it
resolve. Note that in the step-2 expectation rather than leaving it a surprise.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/probe/test_probe.py -k undeliverable_packets -v`
Expected: FAIL, `AttributeError: 'MediaFacts' object has no attribute
'discard_flagged_packets'`. The second test errors on the unresolved fixture
until Task 3.

- [ ] **Step 3: Add `discard` to `Packet`**

In `ffprobe.py`, insert before the defaulted `data_hash`:

```python
    time: float
    size: int
    keyframe: bool
    pos: int
    discard: bool = False
    data_hash: str = ""
```

Defaulted `False` for the same reason `data_hash` is defaulted: the in-process
scan in `mosaic_media.io.packets` mirrors this dataclass and existing test
constructions omit it. Add to the class docstring:

```
    `discard` is the demuxer's "do not present" flag, set from a container edit
    list. The packet is real and its picture is decodable; the demuxer is saying
    the container asked for it not to be shown.
```

- [ ] **Step 4: Parse the flag**

`flags` is already in scope beside `keyframe = "K" in flags`:

```python
        keyframe = "K" in flags
        discard = "D" in flags
```

Pass `discard=discard` to all three `Packet(...)` constructions (pts, dts,
untimed). ffprobe emits a three-character flags column, so the test is
unambiguous.

- [ ] **Step 5: Add the counts to `MediaFacts`**

In `facts.py`, after `max_gop_bytes` and before `timing_measured`:

```python
    discard_flagged_packets: int
    leading_non_keyframe_frames: int
```

Docstring addition:

```
    `discard_flagged_packets` and `leading_non_keyframe_frames` count what a
    default decode would not turn into frames for reasons visible at
    demultiplex time: the first are packets the demuxer marked "do not
    present", the second are frames preceding the first keyframe, which have no
    reference picture. Both are recoverable by the reader, and both tell command
    construction that a stream copy would lose them. Neither is a defect on its
    own.

    The units differ and the names say so. `discard_flagged_packets` counts
    packets. `leading_non_keyframe_frames` counts frames in this model's sense,
    one per distinct presentation timestamp, the same unit `frame_count` uses --
    so a container carrying several packets at one timestamp contributes one,
    and the count cannot disagree with the seek index's keyframe ranks.

    `leading_non_keyframe_frames` is 0 for a stream whose packets carry no
    timestamps. Such a stream has no presentation order to count in, and its
    verdict routes it to a timestamp-generating remux through
    `unreliable_timing_metadata` regardless.
```

Required rather than defaulted, matching every other field: a default would let
a caller assert "no undeliverable packets" for a file nobody measured.

- [ ] **Step 6: Compute them in `probe_media`**

Extend the `.ffprobe` import at `probe.py:7` with `Packet`. Add a module-level
helper above `probe_media`:

```python
def _leading_non_keyframe_frames(packets: tuple[Packet, ...]) -> int:
    """Frames preceding the first keyframe, in presentation order.

    A frame is a distinct presentation timestamp, the unit frame_count uses, so
    a container carrying several packets at one timestamp contributes one. A
    distinct timestamp counts as a keyframe timestamp when any packet bearing it
    is keyframe-flagged, matching build_seek_index, so this count and the index's
    keyframe ranks cannot disagree.

    Zero for a stream with no keyframe flags, which decodes from its first
    packet, and zero for a timestampless stream, whose packets all carry the 0.0
    placeholder and therefore have no presentation order to count in.
    """
    keyframe_times = {packet.time for packet in packets if packet.keyframe}
    if not keyframe_times:
        return 0
    first_keyframe_time = min(keyframe_times)
    return len({packet.time for packet in packets if packet.time < first_keyframe_time})
```

Inside `probe_media`, pass both into the `MediaFacts(...)` construction using the
same `packets` tuple `measure_timing` and `measure_gop` receive, so all three see
one sequence:

```python
        discard_flagged_packets=sum(1 for packet in packets if packet.discard),
        leading_non_keyframe_frames=_leading_non_keyframe_frames(packets),
```

- [ ] **Step 7: Update every `MediaFacts` construction**

`tests/probe/test_verdict.py` holds the shared `CLEAN` baseline that the
transcode and sequence suites import; add both fields as `0` there. Then run
`uv run basedpyright src/ tests/ scripts/` and fix every remaining construction
it reports. Do not add a second baseline -- extend the one that exists.

- [ ] **Step 8: Run the probe suite**

Run: `uv run pytest tests/probe/ -v`
Expected: PASS, except the mid-stream test which still errors on its fixture
until Task 3.

- [ ] **Step 9: Commit**

```bash
git add src/mosaic_media/probe/ tests/probe/
git commit -m "Count the packets a default decode would not deliver"
```

---

### Task 2: Index provenance and a gated packet scan

Implements spec change 3's index half.

**Files:**
- Modify: `src/mosaic_media/io/index.py`, `src/mosaic_media/io/packets.py`,
  `src/mosaic_media/io/__init__.py`
- Modify (call sites): `src/mosaic_media/io/reader.py:175-179`,
  `src/mosaic_media/io/multi.py:177-182`
- Modify (tests): every construction site the recipe in step 5 lists. At the
  time of writing they fall in `tests/io/test_index.py`, `tests/io/test_multi.py`,
  `tests/io/test_reader_seek_landing.py` and
  `tests/bench/test_sparse_and_multi.py`, but run the recipe rather than trusting
  that list -- `tests/io/test_reader_seek_landing.py` carries both a
  `build_seek_index(...)` and a direct `SeekIndex(...)`, and an earlier draft of
  this plan counted only the second.

**Interfaces:**
- Consumes: `Packet.discard` from Task 1.
- Produces: `IndexSource = Literal["probe", "in_process"]`;
  `IndexSpace = Literal["container_default", "edit_list_ignored"]`;
  `SeekIndex.source`, `SeekIndex.space`;
  `build_seek_index(packets, *, source: IndexSource, space: IndexSpace)`;
  `scan_packets_in_process(path, *, ignore_edit_list: bool = False)`.

- [ ] **Step 1: Write the failing test**

In `tests/io/test_index.py`:

```python
def test_an_index_records_the_scanner_and_space_it_was_built_in() -> None:
    packets = (
        Packet(time=0.0, size=10, keyframe=True, pos=0),
        Packet(time=0.04, size=10, keyframe=False, pos=10),
    )
    index = build_seek_index(packets, source="in_process", space="container_default")
    assert index.source == "in_process"
    assert index.space == "container_default"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/io/test_index.py -k records_the_scanner -v`
Expected: FAIL, `TypeError: build_seek_index() got an unexpected keyword argument 'source'`

- [ ] **Step 3: Add provenance to `SeekIndex`**

In `index.py`:

```python
# Which scanner produced an index, and which timestamp space it holds. The two
# scanners disagree where libavformat synthesizes presentation timestamps that
# ffprobe reports as absent, and ignoring an edit list moves which picture sits
# at each timestamp, so an index is only usable by a decode opened the same way.
IndexSource = Literal["probe", "in_process"]
IndexSpace = Literal["container_default", "edit_list_ignored"]


@dataclass(frozen=True, slots=True)
class SeekIndex:
    frame_times: tuple[float, ...]
    keyframe_indices: tuple[int, ...]
    source: IndexSource
    space: IndexSpace
```

```python
def build_seek_index(
    packets: tuple[Packet, ...],
    *,
    source: IndexSource,
    space: IndexSpace,
) -> SeekIndex:
```

Keyword-only and required, so no call site acquires a wrong default silently.
Export `IndexSource` and `IndexSpace` from `io/__init__.py` beside `SeekIndex`
and `build_seek_index`: the injection seam is documented public API, and a caller
cannot construct a valid index without naming these types.

- [ ] **Step 4: Add the gate to the in-process scan**

In `packets.py`:

```python
def scan_packets_in_process(
    path: Path, *, ignore_edit_list: bool = False
) -> tuple[tuple[Packet, ...], TimestampSource]:
```

```python
    options = {"ignore_editlist": "1"} if ignore_edit_list else {}
    try:
        container = av.open(str(path), options=options)
```

and on both `Packet(...)` constructions:

```python
            discard=bool(packet.is_discard),
```

Extend the module docstring, and state the mechanism accurately. The demuxer
delivers every packet either way; a container edit list marks packets "do not
present" rather than withholding them, and it is the default DECODE that honors
the mark and emits fewer frames than this scan has timestamps. Measured on one
such source: 50 packets demultiplexed, 13 flagged, 37 frames decoded. So the
index is complete and the decode is short, and index rank N stops corresponding
to decoded frame N. The option clears the marks and also moves the timestamps,
since the edit list's shift is applied at demultiplex time; either way a source
carrying the flag must have its index built in the space its decode runs in, so
both are gated or neither is.

Do not write that the packets are dropped or never demultiplexed. That is the
opposite shape from the real defect, and a reader who believes it reasons about
sizing and validation backwards.

- [ ] **Step 5: Update every call site**

Locate them, do not count from this plan. A written count goes stale the moment
a test is added, and a missed site is not a lint failure but a `TypeError` at
run time, because step 3 makes `source` and `space` required and keyword-only:

```bash
grep -rn "build_seek_index(\|SeekIndex(" --include=*.py src/ tests/ scripts/
```

Every hit is a site except the definition of `build_seek_index`, the
`class SeekIndex` declaration, the import lines, and the type annotations that
name `SeekIndex` as a return or parameter type. Work the list top to bottom and
update all of them.

In `src/` the sites are inside `_ensure_index` and `MultiVideoReader`'s
per-segment index build. Pass `source="in_process"`. For the space, pass
`space="container_default"` in both for now; Task 3 supplies the reader's real
value and Task 8 supplies the multi reader's.

Every remaining site is in tests, and one file carries two of them -- a
`build_seek_index(...)` whose result feeds a direct `SeekIndex(...)` on the next
line, where only the second is obvious from a skim. Both need updating.

That many near-identical calls is duplication, not varied input, and it spans
several modules -- so the helper goes in `tests/helpers/`, not one test file.
Create `tests/helpers/indexes.py`:

```python
"""Building seek indexes the way a reader would, for tests that need one."""

from pathlib import Path

from mosaic_media.io.index import SeekIndex, build_seek_index
from mosaic_media.io.packets import scan_packets_in_process
from mosaic_media.probe.ffprobe import Packet


def index_of(packets: tuple[Packet, ...]) -> SeekIndex:
    """An in-process, container-default index -- what every caller here means."""
    return build_seek_index(packets, source="in_process", space="container_default")


def index_for(path: Path) -> SeekIndex:
    """The index a reader would build for `path`, scanning it the same way."""
    return index_of(scan_packets_in_process(path)[0])
```

Route every test-side site through these, including the inline
`build_seek_index(scan_packets_in_process(path)[0], ...)` constructions that
Task 3 and Task 4 would otherwise add -- in Task 3's provenance tests and in
Task 4's rewritten seek-landing tests. A test specifically about provenance
passes the arguments explicitly instead.

After the change, re-run the recipe above. Every remaining hit outside
`tests/helpers/indexes.py` and `src/` should be a provenance test passing its
arguments deliberately.

Task 8's `build_seek_index` is a source call site inside `io/multi.py` rather
than a test one, so these helpers cannot route it. This task updates it with the
placeholder space; Task 8 replaces that with the segment's real one.

- [ ] **Step 6: Run the io and bench-collection suites**

Run: `uv run pytest tests/io/ -v` and
`uv run pytest tests/bench --collect-only -q`
Expected: PASS and clean collection, `tests/io/test_reader_seek_landing.py`
included -- it passes because step 5 routed both of its sites through
`index_for`, not because it was left alone. Task 4 rewrites its contract.

- [ ] **Step 7: Commit**

```bash
git add src/mosaic_media/io/ tests/io/ tests/bench/
git commit -m "Record which scanner and timestamp space a seek index holds"
```

---

### Task 3: The reader recovers both demux-visible classes

Implements spec changes 1 and 2, and change 3's rejection.

**Files:**
- Modify: `src/mosaic_media/io/reader.py` (`__init__`, `_ensure_container`
  139-157, `_ensure_index` 175-179, class docstring 63-78)
- Modify: `tests/helpers/media_fixtures.py`, `tests/transcode/conftest.py`
- Test: `tests/io/test_reader_recovery.py` (create)

**Interfaces:**
- Consumes: Task 1's counts, Task 2's gate and provenance.
- Produces: no new public API. Fixtures `open_gop_clip` and
  `avi_starting_on_non_keyframes` move to `tests/helpers/media_fixtures.py`.

- [ ] **Step 1: Move both fixtures beside their siblings, and add the third**

`h264_gop12_clip` and `long_gop_clip` already live in
`tests/helpers/media_fixtures.py` beside `asset` and `AssetName`. Put these two
there too rather than in a conftest, and delete the copy in
`tests/transcode/conftest.py` -- one definition, every suite.

Then correct that conftest's docstring count against what the file actually
defines after the move, rather than against any number written here. Committing
it to remove one fixture commits the whole file, including
`lying_header_vp8_webm`, which was uncommitted working-tree state and which the
docstring's enumeration does not mention. Count the fixtures and name them
all.

`tests/helpers/media_fixtures.py`'s own docstring says fixtures are built by the
ffmpeg on PATH or copied from `tests/assets/`. `avi_starting_on_non_keyframes` is
built with PyAV, so extend that sentence rather than leaving it wrong.

The same docstring's second paragraph scopes the committed clips to H.264 alone
("H.264 clips are copied from `tests/assets/` rather than encoded"). This task
adds an HEVC one, so widen that paragraph to both codecs, keeping its existing
shape: the decoders are native and LGPL, so a committed clip costs nothing to
consume, and `tests/assets/README.md` carries the reasoning. State no claim about
encoder licensing there -- the paragraph's job is to say why the clips are
committed, and the argument for that lives in one place.

Add three single-clip fixtures wrapping entries the `clips` dict already builds,
so Task 6's codec parametrization can resolve every member by fixture name --
`corpus_gop12` is a session fixture rather than a `clips` key, and mixing the two
lookup styles in one parametrization is what makes `clips["corpus_gop12"]` a
`KeyError`:

```python
@pytest.fixture(scope="session")
def vp8_webm_clip(clips: dict[str, Path]) -> Path:
    return clips["vp8_webm"]


@pytest.fixture(scope="session")
def vp9_webm_clip(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """VP9, so every member of the shipped trusted codec set is measured.

    libvpx-vp9 is non-GPL and present in the FFmpeg this suite runs against, so
    this member is generated rather than committed.
    """
    root = tmp_path_factory.mktemp("vp9")
    yield build(root / "vp9.webm", "-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p")


@pytest.fixture(scope="session")
def hevc_clip(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """HEVC, committed for the reason every H.264 asset here is.

    Its decoder is native and LGPL, so the suite reads it with no extra
    dependency. It backs the trusted-codec delivery test and the mp4 carriage
    measurement, so both sets are measured on this codec rather than assuming it.
    """
    root = tmp_path_factory.mktemp("hevc")
    yield asset("hevc.mp4", root / "hevc.mp4")


@pytest.fixture(scope="session")
def cfr_mp4_clip(clips: dict[str, Path]) -> Path:
    return clips["cfr_mp4"]
```

Add the discard-flagged fixture, which no existing fixture provides and without
which the `ignore_editlist` gate has no test anywhere:

```python
@pytest.fixture(scope="session")
def preroll_mp4(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """An mp4 whose edit list marks its leading packets "do not present".

    A `-c copy` cut at 0.2 s writes an edit list that skips the pre-roll rather
    than re-encoding it, so the demuxer delivers five discard-flagged packets at
    negative timestamps: `-0.200000,KD_` followed by four `_D_`. That is the
    shape `ignore_editlist` exists for, and the only fixture in the suite that
    fires the gate. Built from a committed asset, so no encoder is involved.
    """
    root = tmp_path_factory.mktemp("preroll")
    source = asset("cfr.mp4", root / "cfr.mp4")
    yield build(
        root / "preroll.mp4", "-c", "copy", source=["-ss", "0.2", "-i", str(source)]
    )
```

Add `"open_gop.mp4"` and `"hevc.mp4"` to the `AssetName` literal, which is a
closed set of names and must stay one. Then:

```python
@pytest.fixture(scope="session")
def open_gop_clip(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """50 frames, 25 fps, GOP 12, open GOP with B-frames.

    Every keyframe after the first is followed in decode order by pictures that
    precede it in presentation order -- the shape a decoder suppresses after a
    seek. Committed rather than encoded for the reason every H.264 asset here is.
    """
    root = tmp_path_factory.mktemp("open_gop")
    yield asset("open_gop.mp4", root / "open_gop.mp4")
```

Move `avi_starting_on_non_keyframes` verbatim from
`tests/transcode/conftest.py`, keeping its docstring.

- [ ] **Step 2: Write the failing tests**

Create `tests/io/test_reader_recovery.py`:

```python
"""The reader delivers frames a default decode drops."""

from pathlib import Path

import numpy

from mosaic_media.io.reader import VideoReader
from mosaic_media.probe.probe import probe_media


def test_show_all_is_a_no_op_on_a_source_opening_on_a_keyframe(
    clips: dict[str, Path],
) -> None:
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    with VideoReader(path, facts=facts) as reader:
        delivered = sum(1 for _index, _frame in reader)
    assert delivered == facts.frame_count


def test_show_all_is_a_no_op_on_an_open_gop_source(open_gop_clip: Path) -> None:
    # Open GOP is the shape that could refute the unconditional flag: every
    # keyframe after the first is followed in decode order by pictures that
    # precede it in presentation order, and a decoder suppresses those after a
    # seek. Measured: 50 frames either way, identical timestamps and pixels.
    facts = probe_media(open_gop_clip)
    with VideoReader(open_gop_clip, facts=facts) as reader:
        delivered = sum(1 for _index, _frame in reader)
    assert delivered == facts.frame_count == 50


def test_a_source_cut_mid_stream_delivers_its_leading_frames(
    avi_starting_on_non_keyframes: Path,
) -> None:
    facts = probe_media(avi_starting_on_non_keyframes)
    assert facts.leading_non_keyframe_frames == 24
    with VideoReader(avi_starting_on_non_keyframes, facts=facts) as reader:
        delivered = sum(1 for _index, _frame in reader)
    assert delivered == facts.frame_count == 49


def test_a_source_with_an_edit_list_delivers_its_pre_roll(preroll_mp4: Path) -> None:
    # The only fixture that fires the ignore_editlist gate. The demuxer
    # delivers all five either way; without the gate the decode honors their
    # "do not present" mark and emits five fewer frames than the facts count.
    facts = probe_media(preroll_mp4)
    assert facts.discard_flagged_packets == 5
    with VideoReader(preroll_mp4, facts=facts) as reader:
        delivered = sum(1 for _index, _frame in reader)
    assert delivered == facts.frame_count


def test_the_edit_list_gate_shifts_only_the_source_that_needs_it(
    preroll_mp4: Path, clips: dict[str, Path]
) -> None:
    # Applying ignore_editlist everywhere would move a benign edit list's origin
    # by its composition offset. Pin it behaviorally on both sides, on content
    # rather than on a flag: the gated source's frame 5 is the picture a default
    # decode calls frame 0, because the recovered pre-roll fills the five slots
    # ahead of it; the ungated source's frame 0 is unmoved.
    gated_facts = probe_media(preroll_mp4)
    assert gated_facts.discard_flagged_packets == 5
    default_first = _first_frame_without_the_gate(preroll_mp4)
    with VideoReader(preroll_mp4, facts=gated_facts) as reader:
        gated = [frame.copy() for _index, frame in reader]
    assert len(gated) == gated_facts.frame_count
    assert numpy.array_equal(gated[5], default_first)
    assert not numpy.array_equal(gated[0], default_first)

    ungated_path = clips["cfr_30fps_mp4"]
    ungated_facts = probe_media(ungated_path)
    assert ungated_facts.discard_flagged_packets == 0
    with VideoReader(ungated_path, facts=ungated_facts) as reader:
        _index, ungated_first = next(iter(reader))
    assert numpy.array_equal(
        ungated_first, _first_frame_without_the_gate(ungated_path)
    )
```

with one helper beside the tests, which decodes without going through the reader
so it is unaffected by the gate under test:

```python
def _first_frame_without_the_gate(path: Path) -> numpy.ndarray:
    """The first frame a default open decodes, bypassing the reader's options."""
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        for frame in container.decode(stream):
            return frame.to_ndarray(format="bgr24")
    message = f"no frame decoded from {path}"
    raise MediaProbeError(message)


def test_an_index_from_the_wrong_scanner_is_rejected(clips: dict[str, Path]) -> None:
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    honest = index_for(path)
    with VideoReader(path, facts=facts, index=replace(honest, source="probe")) as reader:
        with pytest.raises(MediaProbeError, match="probe scanner"):
            reader.seek(10)


def test_an_index_from_the_wrong_timestamp_space_is_rejected(
    clips: dict[str, Path],
) -> None:
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    honest = index_for(path)
    mismatched = replace(honest, space="edit_list_ignored")
    with VideoReader(path, facts=facts, index=mismatched) as reader:
        with pytest.raises(MediaProbeError, match="edit_list_ignored timestamp space"):
            reader.seek(10)


def test_open_gop_seeks_land_frame_exact(open_gop_clip: Path) -> None:
    # A seek to the nearest keyframe at or before the target in DECODE order
    # returns an open-GOP leading picture at the right timestamp with the wrong
    # pixels, because its references live in the previous GOP. Measured on this
    # clip: targets 10 and 11 land wrong that way. The reader resolves the
    # preceding keyframe in PRESENTATION order and decodes the chain, so every
    # target must match a sequential read.
    facts = probe_media(open_gop_clip)
    with VideoReader(open_gop_clip, facts=facts) as reader:
        truth = [frame.copy() for _index, frame in reader]
    for target in (0, 1, 9, 10, 11, 12, 13, 24, 30, len(truth) - 1):
        with VideoReader(open_gop_clip, facts=facts) as reader:
            reader.seek(target)
            ok, frame = reader.read()
        assert ok
        assert frame is not None
        assert numpy.array_equal(frame, truth[target]), f"target {target} misread"
```

Import exactly what each task's tests use, and no more -- ruff's `F401` fails a
commit on an unused import, and every later task extends this same file:

| task | adds to this file's imports |
| --- | --- |
| 3 | `numpy`, `av`, `Path`, `VideoReader`, `probe_media` |
| 3 (provenance tests) | `pytest`, `dataclasses.replace`, `MediaProbeError`, `index_for` |
| 3 (decoder flag pin) | `Flags2` from `av.codec.context` |
| 5 | nothing new |
| 6 | `DEFAULT_THRESHOLDS` |
| 9 | `count_packet_scans`, `mosaic_media.io.reader as reader_module` |

`av` is for `_first_frame_without_the_gate`, which decodes outside the reader so
the option under test cannot affect the control it compares against.

Task 4's rewrite of `tests/io/test_reader_seek_landing.py` adds `replace`,
`numpy`, `probe_media`, `index_for` and `VideoStream`, and **removes**
`SeekIndex`, `build_seek_index` and `scan_packets_in_process`, which `index_for`
replaces -- ruff `F401` fails the commit otherwise. Task 8's addition to
`tests/io/test_multi.py` needs `probe_media`.

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/io/test_reader_recovery.py -v`

Expected: **five red, four green**, of nine.

| red test | fails as |
| --- | --- |
| `test_a_source_cut_mid_stream_delivers_its_leading_frames` | delivers 25 of 49 |
| `test_a_source_with_an_edit_list_delivers_its_pre_roll` | delivers 45 of 50 |
| `test_the_edit_list_gate_shifts_only_the_source_that_needs_it` | delivers 45, wants 50 |
| `test_an_index_from_the_wrong_scanner_is_rejected` | `DID NOT RAISE` |
| `test_an_index_from_the_wrong_timestamp_space_is_rejected` | `DID NOT RAISE` |

| green test | why it is here |
| --- | --- |
| `test_show_all_is_a_no_op_on_a_source_opening_on_a_keyframe` | already true; kept true |
| `test_show_all_is_a_no_op_on_an_open_gop_source` | already true; kept true |
| `test_open_gop_seeks_land_frame_exact` | already true; guards presentation-order resolution |
| `test_show_all_is_set_only_for_a_segment_starting_at_the_stream_start` | asserts decoder state against the scoping rule, so no later change to landing tolerance can mask a regression |

Paste the real output into the task record rather than confirming the
prediction. A count or a name that does not match means this table is stale, and
a stale table hides a test that cannot fail behind a run that looks correct.

- [ ] **Step 4: Resolve the gate lazily, at first container open**

Not in `__init__`: a factless `VideoReader(...)` must stay constructible without
I/O, or `tests/io/test_no_video_stream.py:22` raises inside its `with` expression
rather than at `read()`, and every other factless construction across
`tests/io/` pays a demux it never uses. To see the population this protects:

```bash
grep -rn "VideoReader(" --include=*.py tests/io/ | grep -v "facts="
```

In `__init__` add only state:

```python
        self._ignore_edit_list: bool = False
        self._index_space: IndexSpace = "container_default"
        self._recovery_resolved: bool = False
```

Add the resolver, called at the top of `_ensure_container`:

```python
    def _resolve_recovery_options(self) -> None:
        """Decide the decoder and demuxer options before the container opens.

        Injected facts answer this without a scan, which is what keeps the
        injected-facts path free of the demux pass it does not run today. Without
        facts the scan runs here and its index is built immediately, so no packet
        tuple outlives this method and `_ensure_index` has nothing to repeat.
        """
        if self._recovery_resolved:
            return
        if self._facts is not None:
            self._ignore_edit_list = self._facts.discard_flagged_packets > 0
            self._recovery_resolved = True
            if self._ignore_edit_list:
                self._index_space = "edit_list_ignored"
            return
        packets, _source = scan_packets_in_process(self._path)
        self._ignore_edit_list = any(packet.discard for packet in packets)
        if self._ignore_edit_list:
            self._index_space = "edit_list_ignored"
            # A gated source must be indexed in the gated space, so the ungated
            # scan just paid cannot be reused.
            packets, _source = scan_packets_in_process(
                self._path, ignore_edit_list=True
            )
        # Build the index here rather than keeping the packets for a later
        # _ensure_index. A sequential read of a container that declares its frame
        # count never reaches _ensure_index (reader.py:196-199), so holding the
        # tuple would retain every packet of the file for the reader's lifetime
        # to serve a call that never comes. The index is a fraction of its size.
        if self._index is None:
            self._index = build_seek_index(
                packets, source="in_process", space=self._index_space
            )
        self._recovery_resolved = True
```

Drop `_scanned_packets` from `__init__`; nothing holds a packet tuple past this
method.

- [ ] **Step 5: Apply the options where the container opens**

In `_ensure_container`, after the early return and before `av.open`:

```python
        self._resolve_recovery_options()
        options = {"ignore_editlist": "1"} if self._ignore_edit_list else {}
        try:
            container = av.open(str(self._path), options=options)
```

The frames-preserving decoder flag does NOT go here. Setting it at container
open applies it to every decode segment, and after a backward seek the segment
begins at the seek target rather than at the stream start -- so the flag emits
the previous group's leading pictures, decoded against references the seek
discarded. Those pictures carry the timestamps of real frames, so they occupy
the index ranks the frame model assigns to them, and a caller reading at one of
those indices would get content decoded from nothing. Measured on
`tests/assets/open_gop.mp4`: a seek to the keyframe at 0.480 decodes 0.400,
0.440, 0.480 with the flag against 0.480, 0.520, 0.560 without it, and the two
extra pictures do not match a sequential read at their own timestamps.

Add instead a helper that decides per segment, and call it where each segment
begins:

```python
    def _apply_show_all(self, stream: VideoStream, *, at_stream_start: bool) -> None:
        """Emit packets preceding the first keyframe of this decode segment
        rather than discarding them, but only when the segment begins at the
        start of the stream -- where a source's own leading non-keyframes live.
        They decode to the decoder's own output rather than to pictures, because
        the reference they difference against is not in the file -- but that
        output is what the file contains, and dropping it leaves indices the
        frame model declares unreachable.

        A segment that begins elsewhere, after a backward seek, must decode with
        the flag clear. It would otherwise emit the previous group's leading
        pictures, decoded against references the seek discarded -- and those
        pictures carry the timestamps of real frames, so they occupy the index
        ranks the frame model assigns to them. A caller reading at one of those
        indices would get content decoded from nothing.
        """
        if at_stream_start:
            stream.codec_context.flags2 |= Flags2.show_all
        else:
            stream.codec_context.flags2 &= ~Flags2.show_all
```

Two call sites, both after the container and stream are in hand:

- in `_start_reading`, on the branch that decodes from the container start
  rather than positioning, with `at_stream_start=True`;
- in `_position_at`, after `keyframe_index` is resolved and before the seek,
  with `at_stream_start=keyframe_index == 0`.

Index 0 is where a source's leading non-keyframes live: a stream whose keyframe
flags begin later resolves every target below them to index 0 through
`preceding_keyframe`'s fallback, so the flag is set exactly when the segment can
contain them.

Import `Flags2` from `av.codec.context` and `Packet` from `..probe.ffprobe` at
module top.

This supersedes the spec's unconditional application. The reason it changed, and
the measurements behind it, are in
`docs/specs/2026-08-01-deliverable-frame-model-amendments.md`.

- [ ] **Step 6: Build and validate the index in the reader's own space**

```python
    def _ensure_index(self) -> SeekIndex:
        # Above both branches, not inside the first. The space an injected index
        # is validated against is only known once the options are resolved, and
        # _position_at reaches here (reader.py:359) before _ensure_container
        # (:370) -- so with facts injected the resolver has not otherwise run,
        # and the comparison below would use __init__'s placeholder. A gated
        # reader handed an ungated index would then pass validation and decode
        # in one timestamp space against an index built in the other.
        self._resolve_recovery_options()
        if self._index is None:
            # Only reached with facts injected: the factless path built the index
            # while resolving its options, because it had scanned already.
            packets, _source = scan_packets_in_process(
                self._path, ignore_edit_list=self._ignore_edit_list
            )
            self._index = build_seek_index(
                packets, source="in_process", space=self._index_space
            )
        elif (
            self._index.source != "in_process"
            or self._index.space != self._index_space
        ):
            message = (
                f"seek index for {self._path} was built by the "
                f"{self._index.source} scanner in the {self._index.space} "
                f"timestamp space, but this reader decodes in the "
                f"{self._index_space} space with the in-process scanner; "
                "resolving across the two returns the wrong frame"
            )
            raise MediaProbeError(message)
        return self._index
```

No packet tuple outlives the method that scanned it. The factless path builds
its index inside `_resolve_recovery_options` while the packets are in hand; this
method only covers the injected-facts path, which reaches it solely from a seek
or a sparse read.

Hoisting the resolver adds no scan anywhere. On the factless path every
`_ensure_index` call site already sits downstream of `_ensure_container`
(`reader.py:191`, and `_position_at` through `seek` or `_start_reading`), so the
resolver has run and `_recovery_resolved` short-circuits it. On the
injected-facts path it is a field computation over facts already in hand.

- [ ] **Step 7: Update the class docstring**

Replace the sentence about `index` suppressing all probing:

```
    Injecting `facts` suppresses the metadata probe that sequential reads would
    otherwise run. Seeking and sparse reads additionally need the packet index,
    which `facts` does not carry; inject `index` as well to suppress all probing.
    An injected index must have been built by `scan_packets_in_process` in this
    source's own timestamp space, which `build_seek_index` records; one built
    otherwise is rejected rather than resolved into. A reader constructed without
    facts runs one packet scan when it first opens the container, because the
    source's measured counts select the decoder and demuxer options.
```

- [ ] **Step 8: Run the io suite**

Run: `uv run pytest tests/io/ tests/probe/ -v`
Expected: PASS, including Task 1's mid-stream count test, whose fixture now
resolves.

- [ ] **Step 9: Commit**

```bash
git add src/mosaic_media/io/reader.py tests/io/ tests/helpers/ tests/transcode/conftest.py
git commit -m "Deliver the frames a default decode drops"
```

---

### Task 4: Seek landing accepts an earlier keyframe

Implements the seek half of spec change 7.

**Files:**
- Modify: `src/mosaic_media/io/reader.py:354-394` (`_position_at`)
- Modify: `tests/io/test_reader_seek_landing.py` -- **this file exists (30
  lines) and asserts the contract this task changes.** Rewrite its test and its
  module docstring; do not create or overwrite the file.

**Interfaces:**
- Consumes: Task 2's index.
- Produces: no new public API.

- [ ] **Step 1: Rewrite the existing test to the new contract**

The current `test_seek_landing_mismatch_raises` injects a `SeekIndex` naming a
keyframe the container does not have, seeks to it, and asserts the resulting
before-landing raises. Under this task a before-landing is legitimate, so that
assertion inverts: the reader must decode forward from where it landed and return
the *correct* frame. Replace the module docstring and the test:

```python
"""The seek path resolves where the container actually landed.

A backward seek is not guaranteed to reach the keyframe the index named: some
containers index keyframes more coarsely than the stream carries them, and land
earlier. Landing earlier is correct and the reader decodes forward from it.
Landing later means the target's own references were skipped, so counting
forward would return a frame decoded from the wrong prefix, and that raises.
"""


def test_a_landing_before_the_requested_keyframe_returns_the_right_frame(
    clips: dict[str, Path],
) -> None:
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    with VideoReader(path, facts=facts) as reader:
        truth = [frame.copy() for _index, frame in reader]
    # An index naming a keyframe the container does not have forces a landing
    # earlier than requested, which is the case under test.
    real = index_for(path)
    coarse = replace(real, keyframe_indices=(0, 6))
    with VideoReader(path, facts=facts, index=coarse) as reader:
        reader.seek(6)
        ok, frame = reader.read()
    assert ok
    assert frame is not None
    assert numpy.array_equal(frame, truth[6])


def test_a_landing_after_the_requested_keyframe_raises(
    clips: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # An index claiming a keyframe earlier than the container will land on makes
    # the decoder start past the requested position, so the frames before it were
    # never decoded and counting forward would misread.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    real = index_for(path)

    def seek_past_the_named_keyframe(
        _self: VideoReader, stream: VideoStream, _time: float
    ) -> int:
        # Land the container one keyframe later than the index named, without
        # touching the index -- which is the condition under test. The unused
        # parameters carry underscores: reportUnusedParameter is live, and the
        # plan bans every suppression.
        return int(round(real.frame_times[30] / float(stream.time_base)))

    monkeypatch.setattr(VideoReader, "_to_stream_offset", seek_past_the_named_keyframe)
    with VideoReader(path, facts=facts, index=real) as reader:
        with pytest.raises(MediaProbeError, match="or earlier but decoded"):
            reader.seek(5)


def test_a_landing_matching_no_index_entry_raises(clips: dict[str, Path]) -> None:
    # The tolerance is half the index spacing, so acceptance windows tile the
    # timeline: inside the index's span a landing always resolves to the nearest
    # entry, by design, and the raise is the out-of-span backstop. Shifting every
    # time by a full period rather than a half is what pushes the landing before
    # the first entry, where it matches nothing at all -- a half-period shift is
    # accepted, because period * 0.5 and the tolerance are the same double.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    real = index_for(path)
    period = 1.0 / facts.fps
    shifted = replace(
        real, frame_times=tuple(time + period for time in real.frame_times)
    )
    with VideoReader(path, facts=facts, index=shifted) as reader:
        with pytest.raises(MediaProbeError, match="matches no entry in its seek index"):
            reader.seek(10)
```

Both `match=` values are load-bearing. Without the first, the test passes today,
because an after-landing outside the half-frame tolerance already raises with a
different message. Monkeypatching `_to_stream_offset` rather than
`preceding_keyframe` keeps the index honest and avoids seeking to a negative
timestamp, whose failure mode is a different error entirely.

- [ ] **Step 2: Run to verify the expected failures**

Run: `uv run pytest tests/io/test_reader_seek_landing.py -v`

Expected: all three FAIL.

| test | fails as |
| --- | --- |
| `test_a_landing_before_the_requested_keyframe_returns_the_right_frame` | `MediaProbeError: seek landing ... expected keyframe time ...` |
| `test_a_landing_after_the_requested_keyframe_raises` | `match=` finds no "or earlier but decoded" in the current message |
| `test_a_landing_matching_no_index_entry_raises` | `match=` finds no "matches no entry in its seek index" |

Three failures, no errors. Paste the real output; do not confirm the prediction.

- [ ] **Step 3: Replace the landing check**

In `_position_at`:

```python
        first = self._decode_next()
        if first is not None:
            observed = float(first.time)
            tolerance = 0.5 / geometry.fps if geometry.fps > 0 else 0.0
            if geometry.fps > 0 and observed > keyframe_time + tolerance:
                message = (
                    f"seek landing for {self._path} at frame {target}: expected "
                    f"keyframe time {keyframe_time} or earlier but decoded "
                    f"{observed}"
                )
                raise MediaProbeError(message)
            # Landing earlier is legitimate: container seek granularity is
            # coarser than the keyframe list on some formats. Resolve where the
            # decoder actually is, then count forward from there.
            self._decoder_pos = self._index_rank_at(observed, tolerance)
            self._pending_frame = first
```

Add the resolver:

```python
    def _index_rank_at(self, observed: float, tolerance: float) -> int:
        """The index rank of the frame the decoder just emitted.

        The index and the decode are built by one scanner in one timestamp
        space, so their times agree exactly and the tolerance is margin, not
        noise coverage. A time matching no entry means the index does not
        describe this decode, which is a defect rather than a seek that missed.
        """
        index = self._ensure_index()
        position = bisect.bisect_left(index.frame_times, observed - tolerance)
        if position < len(index.frame_times) and (
            abs(index.frame_times[position] - observed) <= tolerance
        ):
            return position
        message = (
            f"seek landing for {self._path} decoded a frame at {observed}, "
            "which matches no entry in its seek index"
        )
        raise MediaProbeError(message)
```

`bisect` is not currently imported in `reader.py`; add it at module top. When
`fps <= 0` the tolerance is 0.0 and the comparison demands exact equality, which
is correct on the one-scanner path: both sides are `float(pts * time_base)` from
the same time base.

- [ ] **Step 4: Run the io suite**

Run: `uv run pytest tests/io/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mosaic_media/io/reader.py tests/io/test_reader_seek_landing.py
git commit -m "Accept a seek landing before the keyframe the index named"
```

---

### Task 5: A sequential read that ends short says so

Implements the sequential half of spec change 7.

**Files:**
- Modify: `src/mosaic_media/io/reader.py` (`__init__`, `read` 411-428, `seek`)
- Test: `tests/io/test_reader_recovery.py`

**Interfaces:**
- Consumes: nothing beyond Task 3.
- Produces: no new public API.

- [ ] **Step 1: Write the failing test**

```python
def test_a_read_ending_before_its_window_raises(clips: dict[str, Path]) -> None:
    # Facts declaring more frames than the file delivers stand in for a source
    # whose codec is not on the trusted set: the reader reports the shortfall
    # rather than stopping silently inside its own window.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    overstated = replace(facts, frame_count=facts.frame_count + 5)
    with VideoReader(path, facts=overstated) as reader:
        with pytest.raises(MediaProbeError, match="delivered 60 of 65"):
            for _index, _frame in reader:
                pass


def test_the_shortfall_is_counted_from_where_a_seek_left_the_cursor(
    clips: dict[str, Path],
) -> None:
    # After a seek the counter restarts, so the reported expectation must be the
    # frames remaining from the seek target -- not the whole window, which the
    # reader never attempted.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    overstated = replace(facts, frame_count=facts.frame_count + 5)
    with VideoReader(path, facts=overstated) as reader:
        reader.seek(50)
        with pytest.raises(MediaProbeError, match="delivered 10 of 15"):
            while True:
                ok, _frame = reader.read()
                if not ok:
                    break
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/io/test_reader_recovery.py -k ending_before -v` and
`-k counted_from_where`
Expected: both FAIL with `DID NOT RAISE` -- iteration and reading both stop
silently.

- [ ] **Step 3: Count deliveries against the origin they started from**

In `__init__`:

```python
        self._delivered: int = 0
        self._count_origin: int = 0  # frame index the delivery count starts at
```

In `read`, where the sequential mode is entered, set
`self._count_origin = self._start_frame`. In `seek`, after the range check, set
`self._delivered = 0` and `self._count_origin = target`.

Then in `read`:

```python
        frame = self._read_current(geometry)
        if frame is None:
            expected = len(
                range(self._count_origin, window_end, self._frame_step)
            )
            if self._delivered < expected:
                message = (
                    f"{self._path} delivered {self._delivered} of {expected} "
                    "frames its facts declare; the source carries packets that "
                    "decode to no frame, and its analysis verdict requires a "
                    "transcode before it can be read"
                )
                raise MediaProbeError(message)
            return False, None
        self._delivered += 1
        self._target += self._frame_step
        return True, frame
```

One integer comparison at end of stream. It touches no index and runs no scan, so
no path that runs no packet scan today runs one now.

`read_frames` sets `self._target` directly after each yield and does not go
through `read`; it already raises on a frame it cannot decode
(`reader.py:494-496`), so it needs no change.

- [ ] **Step 4: Run the io suite**

Run: `uv run pytest tests/io/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mosaic_media/io/reader.py tests/io/test_reader_recovery.py
git commit -m "Report a read that ends inside its window"
```

---

### Task 6: The trusted codec set and the deliverability escalation

Implements spec change 5 with the routing its reason needs, spec change 6's
deliverability half, and the rewrite of the two superseded tests.

One task rather than two because they share a test file. The trusted set's
routing test and the two red tests both live in
`tests/transcode/test_commands.py`, so a commit adding the first without
rewriting the others leaves its own test file red at `HEAD`. Narrowing the
`git add` cannot separate them. The two changes are independent in the source --
one adds an analysis reason, the other escalates a copy -- and are executed as
distinct steps below; only the commit is shared.

**Files:**
- Modify: `src/mosaic_media/probe/policy.py`, `src/mosaic_media/probe/verdict.py`,
  `src/mosaic_media/transcode/commands.py` (`_select_operation` 171-183,
  `_reencode_argv` 281), `src/mosaic_media/__init__.py`
- Test: `tests/probe/test_verdict.py`, `tests/transcode/test_commands.py`,
  `tests/transcode/test_convert.py`, `tests/io/test_reader_recovery.py`

**Interfaces:**
- Consumes: Task 1's counts; the existing `_select_operation` wrapper; the
  `vp8_webm_clip`, `vp9_webm_clip`, `cfr_mp4_clip` and `hevc_clip` fixtures
  from Task 3, plus the pre-existing `corpus_gop12`.
- Produces: `AnalysisReason` gains `"unverified_frame_correspondence"`;
  `FRAME_EXACT_CODECS`; `Thresholds.frame_exact_codecs`.

- [ ] **Step 1: Write the failing tests**

In `tests/probe/test_verdict.py`:

```python
def test_a_codec_outside_the_trusted_set_needs_an_analysis_transcode() -> None:
    verdict = derive(replace(CLEAN, codec_name="indeo5"), CHROME_149, DEFAULT_THRESHOLDS)
    assert "unverified_frame_correspondence" in verdict.analysis_reasons
    assert verdict.analysis_transcode == "required"


def test_a_trusted_codec_needs_no_analysis_transcode() -> None:
    verdict = derive(replace(CLEAN, codec_name="h264"), CHROME_149, DEFAULT_THRESHOLDS)
    assert "unverified_frame_correspondence" not in verdict.analysis_reasons
```

In `tests/transcode/test_commands.py` -- the routing half, without which the
reason produces no command at all:

```python
def test_an_unverified_codec_selects_a_reencode() -> None:
    command = command_for("analysis", ANALYSIS_ENCODING, codec_name="indeo5")
    assert command is not None
    assert command.reasons == frozenset({"unverified_frame_correspondence"})
    assert command.operation is Operation.REENCODE_AV1
```

In `tests/io/test_reader_recovery.py` -- the membership claim, so the comment on
`FRAME_EXACT_CODECS` states something the suite checks:

```python
# One list, two tests. The guard below derives the expected set from it, so a
# member added without a delivery case fails there -- which is what makes the
# comment on FRAME_EXACT_CODECS true rather than aspirational.
_MEASURED_CODECS = [
    ("vp8_webm_clip", "vp8"),
    ("vp9_webm_clip", "vp9"),
    ("cfr_mp4_clip", "h264"),
    ("hevc_clip", "hevc"),
    ("corpus_gop12", "av1"),
]


@pytest.mark.parametrize(("fixture_name", "expected_codec"), _MEASURED_CODECS)
def test_every_trusted_codec_delivers_one_frame_per_packet(
    request: pytest.FixtureRequest, fixture_name: str, expected_codec: str
) -> None:
    # Every member of the shipped default, measured. Resolved by fixture name
    # because the AV1 corpus is its own session fixture, not a `clips` key.
    path = request.getfixturevalue(fixture_name)
    facts = probe_media(path)
    assert facts.codec_name == expected_codec
    assert facts.codec_name in DEFAULT_THRESHOLDS.frame_exact_codecs
    with VideoReader(path, facts=facts) as reader:
        delivered = sum(1 for _index, _frame in reader)
    assert delivered == facts.frame_count


def test_the_trusted_set_is_exactly_what_the_suite_measures() -> None:
    # Derived, not restated: the shipped default must equal the set the tests
    # above actually exercise, so neither can drift from the other.
    assert DEFAULT_THRESHOLDS.frame_exact_codecs == frozenset(
        codec for _fixture_name, codec in _MEASURED_CODECS
    )
```

- [ ] **Step 2: Rewrite the two superseded tests**

Both were written against an approach this design rejects and are red on the
branch. Rewrite them to the new contract; do not delete them.

`tests/transcode/test_commands.py`, `test_a_copy_remux_normalizes_the_output_start_time`
asserts `-copyinkf` in the argv. Copying initial non-keyframes is the rejected
alternative -- it leaves a derivative whose leading frames do not decode. Replace
the whole test with the escalation it became:

```python
def test_a_copy_that_would_drop_leading_packets_reencodes_instead() -> None:
    # A stream copy drops a source's leading non-keyframes, so the derivative
    # loses them and its clock keeps their offset. Only a re-encode can
    # materialize them.
    command = command_for(
        "playback", PLAYBACK_ENCODING, container="avi", leading_non_keyframe_frames=24
    )
    assert command is not None
    assert command.operation is Operation.REENCODE_AV1
    assert arg_after(command.argv, "-flags2") == "+showall"


def test_a_copy_that_would_drop_discard_packets_reencodes_instead() -> None:
    command = command_for(
        "analysis",
        ANALYSIS_ENCODING,
        declared_fps=1000.0,
        declared_frame_count=0,
        discard_flagged_packets=3,
    )
    assert command is not None
    assert command.operation is Operation.REENCODE_AV1
    assert arg_after(command.argv, "-ignore_editlist") == "1"
```

Keep `test_the_output_clock_is_never_shifted_to_hide_a_dropped_prefix` as it
stands -- it pins the absence of `-avoid_negative_ts`, which remains correct.

`tests/transcode/test_convert.py`,
`test_a_source_starting_on_non_keyframes_rewraps_to_a_zero_start_time` asserts
`Operation.REMUX_CONTAINER`. Change that assertion to
`Operation.REENCODE_AV1`, add `@requires_svtav1`, and keep every other
assertion, including `output_facts.start_time == 0.0`, which is still the point
of the test.

- [ ] **Step 3: Run to verify they fail**

Three selectors, because the work spans three suites. Run all three and record
each.

```bash
uv run pytest tests/probe/test_verdict.py tests/transcode/test_commands.py -k "unverified or trusted" -v
uv run pytest tests/transcode/ -k "would_drop or starting_on_non_keyframes" -v
uv run pytest tests/io/test_reader_recovery.py -k "trusted_codec or trusted_set" -v
```

The first selector matches on "trusted" as well as "unverified": two of its three
tests carry neither the word "unverified" nor a module name containing it, so
`-k unverified` alone collects one of the three and silently skips the pass this
step exists to record. No pre-existing test in either file matches either word.

Expected, counted in collected items rather than test functions:

| test | items | outcome |
| --- | --- | --- |
| `test_a_codec_outside_the_trusted_set_needs_an_analysis_transcode` | 1 | FAILS -- the reason is never added |
| `test_a_trusted_codec_needs_no_analysis_transcode` | 1 | PASSES, and must keep passing; the reason fires for nothing yet |
| `test_an_unverified_codec_selects_a_reencode` | 1 | FAILS -- no command selects a re-encode |
| `test_a_copy_that_would_drop_leading_packets_reencodes_instead` | 1 | FAILS -- still a copy remux, no input flags |
| `test_a_copy_that_would_drop_discard_packets_reencodes_instead` | 1 | FAILS -- same |
| `test_a_source_starting_on_non_keyframes_rewraps_to_a_zero_start_time` | 1 | FAILS -- still `REMUX_CONTAINER` |
| `test_every_trusted_codec_delivers_one_frame_per_packet` | 5 | FAILS on `Thresholds` having no `frame_exact_codecs`, once per codec |
| `test_the_trusted_set_is_exactly_what_the_suite_measures` | 1 | FAILS the same way |

Twelve items: eleven failures and one pass, no errors. Three from the first
selector, three from the second, six from the third. Paste the real output rather
than confirming the prediction. An `ERROR` means the test could not execute,
which proves nothing; a missing name means the selector is stale.

Two rows depend on the AV1 encoder and read differently without it. Step 2 adds
`@requires_svtav1` to `test_a_source_starting_on_non_keyframes_rewraps_to_a_zero_start_time`,
so it reports SKIPPED rather than FAILED there; and the third selector's
`corpus_gop12` raises in fixture setup, turning its six items into errors. Record
that outcome as what it is rather than forcing it into the table.

- [ ] **Step 4: Add the literal and the policy**

In `policy.py`, extend `AnalysisReason` with
`"unverified_frame_correspondence"`, then:

```python
# Codecs whose decoder emits exactly one frame per packet bearing a distinct
# presentation timestamp, once the reader's recovery options are in force. Every
# member is measured by the delivery test in the reader recovery suite; a codec
# is not admitted on decoder-family reasoning, because a wrong member's failure
# mode is the silent one this design exists to remove -- a neighboring frame
# returned as if it were the right one.
#
# Being unable to encode a codec here does not bar it: the h264 and hevc samples
# are committed under tests/assets/ and read with no added dependency, since both
# decoders are native and LGPL. Injected like every other policy in this module,
# so a consumer measuring a codec this set omits adds it without touching this
# package.
#
# Equal to CHROME_149.codecs today, and independent of it. One says a decoder
# emits a frame per packet, the other says a browser can play the stream; they
# coincide by accident and will diverge the first time a codec is trusted for
# decode but unsupported by the profile, or the reverse. Do not fold either into
# the other.
FRAME_EXACT_CODECS: frozenset[str] = frozenset(
    {"h264", "hevc", "av1", "vp9", "vp8"}
)
```

Add to `Thresholds`, defaulted so no call site breaks:

```python
    frame_exact_codecs: frozenset[str] = FRAME_EXACT_CODECS
```

Export `FRAME_EXACT_CODECS` from `src/mosaic_media/__init__.py`'s `__all__`,
beside `CHROME_149` and `DEFAULT_THRESHOLDS`.

- [ ] **Step 5: Fire the reason**

In `derive`, beside the other analysis reasons:

```python
    if facts.codec_name not in thresholds.frame_exact_codecs:
        analysis.add("unverified_frame_correspondence")
```

- [ ] **Step 6: Route the reason to a re-encode**

In `commands.py`, add the literal to `_REENCODE_ANALYSIS_REASONS` (`:58-65`):

```python
_REENCODE_ANALYSIS_REASONS: frozenset[AnalysisReason] = frozenset(
    {
        "variable_frame_rate",
        "rotated",
        "non_square_pixels",
        "interlaced",
        "unverified_frame_correspondence",
    }
)
```

Without this the reason fires, `analysis_transcode` reads `"required"`, and
`_select_minimum_operation` matches no branch and returns `None` -- so
`run_transcode` reports a no-op for exactly the files the design targets. The
timestamp remux is not an alternative: it copies the untrusted codec forward and
the output fires the same reason.

- [ ] **Step 7: Extend the escalation**

```python
def _select_operation(
    verdict: Verdict, facts: MediaFacts, target: Target
) -> Operation | None:
    operation = _select_minimum_operation(verdict, target)
    if operation is Operation.REENCODE_AV1 or operation is None:
        return operation
    if facts.codec_name not in _MP4_STREAM_COPY_CODECS:
        # The codec the copy would preserve cannot be muxed into the mp4 the
        # converter writes.
        return Operation.REENCODE_AV1
    if facts.discard_flagged_packets > 0 or facts.leading_non_keyframe_frames > 0:
        # A copy carries the source's packets, so a source whose packets do not
        # all decode yields a derivative whose packets do not all decode. Only a
        # re-encode materializes them. Muxability and deliverability are
        # independent reasons to escalate; a copy must survive both.
        return Operation.REENCODE_AV1
    return operation
```

The constant is still `_MP4_STREAM_COPY_CODECS` here. Task 7 renames it, and
runs after this task.

- [ ] **Step 8: Carry the recovery options into the re-encode**

Give `_reencode_argv` the input-flag insertion point `_copy_remux_argv` already
has:

```python
    # The decoder emits frames before the first keyframe only when asked, and the
    # demuxer keeps edit-list packets only when told to ignore the edit list.
    # Without both, the re-encode reproduces the source's own dropped frames.
    input_flags: list[str] = ["-flags2", "+showall"]
    if facts.discard_flagged_packets > 0:
        input_flags.extend(["-ignore_editlist", "1"])
    argv: list[str] = [*_BASE, *input_flags, "-i", str(source)]
```

`+showall` is unconditional here, and the reason is the transcode's decode
shape rather than anything the reader does: ffmpeg decodes the input once,
sequentially, from the start of the file, and never seeks. Its only decode
segment therefore begins at the stream start, which is exactly the segment where
the flag has legitimate work to do, so there is nothing to gate. The reader
gates it per segment only because the reader seeks.

`-ignore_editlist` stays gated, because on a source with a benign edit list it
moves the presentation origin.

- [ ] **Step 9: Run the affected suites**

Run: `uv run pytest tests/probe/ tests/transcode/ tests/io/ -v`
Expected: PASS, including both rewritten tests. Seven corpus codecs newly require
an analysis transcode; no H.264, HEVC or AV1 fixture changes verdict, so no
existing verdict test moves.

- [ ] **Step 10: Commit**

```bash
git add src/mosaic_media/probe/ src/mosaic_media/transcode/commands.py src/mosaic_media/__init__.py tests/
git commit -m "Re-encode when the derivative would not carry every frame"
```

---

### Task 7: The mp4 carriage set, measured

Implements spec change 6's muxability half.

`_MP4_STREAM_COPY_CODECS` decides whether a copy remux reaches the muxer at all,
and the comment above it says membership "is measured against the real muxer by
`tests/transcode/test_mp4_stream_copy.py`, never assumed". No such file exists,
on disk or anywhere in history. The measurement behind the set was really taken,
but it was never carried into the repository, so the claim is false as written
and the set cannot be rechecked after an ffmpeg upgrade.

This task lands the measurement and admits `hevc` on it. `hevc` sits outside the
set only because no sample existed to measure it with, and Task 0 committed one.

**Files:**
- Modify: `src/mosaic_media/transcode/commands.py` (the comment and constant at
  70-95, and the use at `:177`)
- Create: `tests/transcode/test_mp4_stream_copy.py`

**Interfaces:**
- Consumes: the `hevc_clip` and `vp9_webm_clip` fixtures from Task 3, plus the
  pre-existing `clips` and `corpus_gop12`.
- Produces: `MP4_STREAM_COPY_CODECS`, the same constant plainly named. The new
  test imports it, and `reportPrivateUsage` is file-scoped: a symbol used
  outside its declaring module carries no leading underscore.

- [ ] **Step 1: Rename the constant**

`_MP4_STREAM_COPY_CODECS` -> `MP4_STREAM_COPY_CODECS` at its definition
(`commands.py:85`) and its one use (`:177`). Those are the only two references
in the tree. It is not added to `src/mosaic_media/__init__.py`'s `__all__`: the
name loses its underscore because a test imports it, not because it becomes part
of the package's public surface.

`_REENCODE_STREAM_REASONS` and `_REENCODE_ANALYSIS_REASONS` keep their
underscores. Nothing outside `commands.py` imports either.

- [ ] **Step 2: Write the failing tests**

Create `tests/transcode/test_mp4_stream_copy.py`:

```python
"""Which codecs the mp4 muxer carries through a stream copy, measured.

The converter always writes mp4, so a copy remux preserving a codec the muxer
refuses dies before a header is written. MP4_STREAM_COPY_CODECS is the allowlist
that prevents that, and a wrong member is a transcode that fails. Every codec the
suite can sample is muxed here against the real muxer, and the set is compared
against the outcome rather than restated beside it.
"""

import subprocess
from pathlib import Path

import pytest

from mosaic_media.transcode.commands import MP4_STREAM_COPY_CODECS
from tests.helpers.media_fixtures import build

# The codec name as ffprobe reports it, the encoder arguments that produce a
# sample of it, and the container that sample needs. Every encoder named is
# native or otherwise non-GPL, which is what lets the suite generate these at
# all. Codecs a fixture already provides are absent from this table and taken
# from that fixture below; a second encode of the same clip is duplication that
# drifts the moment the fixture's own parameters change.
#
# The refusals are the corpus codecs that actually reach this selector, not
# arbitrary negatives: msmpeg4v2, wmv2 and rpza are what the hardening corpus
# carries outside the trusted set. indeo5 is decode-only in this FFmpeg, so no
# sample of it can be produced and it is not measured here.
_GENERATED_SAMPLES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("mpeg4", ("-c:v", "mpeg4", "-pix_fmt", "yuv420p"), "avi"),
    ("mpeg2video", ("-c:v", "mpeg2video", "-pix_fmt", "yuv420p"), "mpg"),
    ("mpeg1video", ("-c:v", "mpeg1video", "-pix_fmt", "yuv420p"), "mpg"),
    ("msmpeg4v2", ("-c:v", "msmpeg4v2", "-pix_fmt", "yuv420p"), "avi"),
    ("wmv2", ("-c:v", "wmv2", "-pix_fmt", "yuv420p"), "asf"),
    ("rpza", ("-c:v", "rpza", "-pix_fmt", "yuv420p"), "mov"),
)


def _mp4_carries(sample: Path, destination: Path) -> bool:
    """Whether `-c copy` muxes `sample` into mp4 without the muxer refusing it."""
    command = [
        "ffmpeg",
        "-hide_banner",
        "-v",
        "error",
        "-y",
        "-i",
        str(sample),
        "-c",
        "copy",
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    return result.returncode == 0


@pytest.fixture(scope="session")
def mp4_carriage(
    tmp_path_factory: pytest.TempPathFactory,
    clips: dict[str, Path],
    hevc_clip: Path,
    vp9_webm_clip: Path,
    corpus_gop12: Path,
) -> dict[str, bool]:
    """Every codec the suite can sample, muxed into mp4 with `-c copy`.

    Six samples come from fixtures the session already builds, and the rest are
    encoded here because nothing else in the suite needs them.
    """
    root = tmp_path_factory.mktemp("mp4_carriage")
    samples: dict[str, Path] = {
        "h264": clips["cfr_mp4"],
        "hevc": hevc_clip,
        "av1": corpus_gop12,
        "vp9": vp9_webm_clip,
        "vp8": clips["vp8_webm"],
        "mjpeg": clips["mjpeg_avi"],
    }
    for codec_name, arguments, extension in _GENERATED_SAMPLES:
        samples[codec_name] = build(root / f"{codec_name}.{extension}", *arguments)
    return {
        codec_name: _mp4_carries(sample, root / f"{codec_name}_copy.mp4")
        for codec_name, sample in samples.items()
    }


@pytest.mark.parametrize("codec_name", sorted(MP4_STREAM_COPY_CODECS))
def test_every_member_of_the_copy_set_is_carried_by_the_mp4_muxer(
    codec_name: str, mp4_carriage: dict[str, bool]
) -> None:
    # A member the muxer refuses is the expensive error of the two: the copy
    # remux the selector picks for it dies before a header is written, and the
    # source can never be prepared for its target.
    assert codec_name in mp4_carriage, f"no sample measures {codec_name}"
    assert mp4_carriage[codec_name]


def test_the_copy_set_is_exactly_what_the_muxer_carries(
    mp4_carriage: dict[str, bool],
) -> None:
    # Derived, not restated: a codec measured as refused cannot sit in the set,
    # and one measured as carried cannot be quietly left out of it. This is what
    # makes the comment on MP4_STREAM_COPY_CODECS true rather than aspirational.
    carried = frozenset(
        codec_name for codec_name, ok in mp4_carriage.items() if ok
    )
    assert MP4_STREAM_COPY_CODECS == carried
```

The set equality is over the codecs the suite can sample, which is the whole
allowlist plus four measured refusals. A member with no sample fails the
parametrized test with a message naming it, rather than passing silently.

`corpus_gop12` is requested unguarded, so this file errors rather than skips on a
machine without the AV1 encoder. That is deliberate and matches `tests/io/`,
which uses the same fixture the same way; the `requires_svtav1` convention in
`tests/transcode/test_convert.py` guards tests that *encode* AV1 as the operation
under test, which this file does not. Dropping the av1 row instead is not an
option -- `av1` is in the set, so the equality test needs its measurement.

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/transcode/test_mp4_stream_copy.py -v`

Expected: **one FAILED, the rest passed, no errors.**

| test | outcome |
| --- | --- |
| `test_every_member_of_the_copy_set_is_carried_by_the_mp4_muxer` | passes for all seven current members -- the set is right about what it already contains |
| `test_the_copy_set_is_exactly_what_the_muxer_carries` | FAILS: the measured carried set contains `hevc` and the constant does not |

The rename in step 1 is what keeps this a failure rather than a collection
error. Paste the real output into the task record. If the parametrized test
reports a refusal, this table is wrong and the constant is wrong with it: report
that rather than editing the expectation.

- [ ] **Step 4: Admit `hevc` and rewrite the comment**

```python
# Codecs the mp4 muxer carries through a stream copy. The converter always writes
# mp4, so a copy remux preserving a codec absent from this set dies in the muxer
# before a header is written -- "Could not find tag for codec ... not currently
# supported in container" -- and the source can never be prepared for its target.
# Such a copy escalates to a re-encode instead, which is the minimum operation
# that still produces output passing the target's verdict.
#
# An allowlist rather than a denylist, because the two errors cost differently:
# a codec missing from the set buys one unnecessary re-encode, while a codec
# wrongly present buys a transcode that fails. Membership is measured against the
# real muxer by tests/transcode/test_mp4_stream_copy.py, which muxes a real
# sample of every codec the suite can produce and derives this set from the
# outcome, so nothing joins it without a sample that carries.
MP4_STREAM_COPY_CODECS: frozenset[str] = frozenset(
    {
        "h264",
        "hevc",
        "av1",
        "vp9",
        "mpeg4",
        "mjpeg",
        "mpeg2video",
        "mpeg1video",
    }
)
```

The paragraph explaining `hevc`'s absence goes with it; there is no absence left
to explain.

- [ ] **Step 5: Run the transcode suite**

Run: `uv run pytest tests/transcode/ -v`
Expected: PASS. The only behavior that moves is an HEVC source whose analysis
verdict selects a timestamp remux: it now takes that remux rather than escalating
to a re-encode. No existing test asserts the escalated outcome for HEVC -- the
escalation tests use `vp8`, which the muxer genuinely refuses.

- [ ] **Step 6: Commit**

```bash
git add src/mosaic_media/transcode/commands.py tests/transcode/test_mp4_stream_copy.py
git commit -m "Measure which codecs mp4 carries through a stream copy"
```

---

### Task 8: The multi-video reader carries the same gate

Completes spec change 3 for `MultiVideoReader`, which Task 2 left on a
placeholder.

Depends on Tasks 2 and 3 only. Task 7 sits between it and Task 6 and touches
neither `io/multi.py` nor the seek path, so it neither blocks this task nor is
blocked by it.

**Files:**
- Modify: `src/mosaic_media/io/multi.py:177-192`
- Test: `tests/io/test_multi.py`

**Interfaces:**
- Consumes: Tasks 2 and 3.
- Produces: no new public API.

- [ ] **Step 1: Write the failing test**

```python
def test_a_segment_with_an_edit_list_is_readable_through_the_multi_reader(
    preroll_mp4: Path, clips: dict[str, Path]
) -> None:
    # A segment whose gate fires must have its index built in the gated space, or
    # the per-segment reader rejects it and the segment becomes unreadable. The
    # fixture must be the discard-flagged one: a source cut mid-stream carries no
    # edit list, so its gate never fires and the placeholder space matches by
    # accident.
    paths = [preroll_mp4, clips["cfr_mp4"]]
    facts = [probe_media(path) for path in paths]
    assert facts[0].discard_flagged_packets == 5
    # Seek rather than iterate. A sequential read never reaches the provenance
    # check: _open_segment only calls reader.seek when local_seek is truthy
    # (multi.py:194), and VideoReader._start_reading decodes from 0 without
    # touching _ensure_index. Only a seek routes through _position_at.
    with MultiVideoReader(paths, facts=facts) as reader:
        reader.seek(3)
        ok, frame = reader.read()
    assert ok
    assert frame is not None


def test_a_segment_with_an_edit_list_delivers_every_frame(
    preroll_mp4: Path, clips: dict[str, Path]
) -> None:
    # Driven with read(), as every other test of this class is: MultiVideoReader
    # implements no iteration protocol, and its read() returns (ok, frame) rather
    # than the (index, frame) pair VideoReader.__iter__ yields.
    #
    # Green before this task and after. A sequential read never reaches the
    # provenance check -- which is precisely why the test above seeks -- so this
    # pins delivery, not the gate.
    paths = [preroll_mp4, clips["cfr_mp4"]]
    facts = [probe_media(path) for path in paths]
    delivered = 0
    with MultiVideoReader(paths, facts=facts) as reader:
        while True:
            ok, _frame = reader.read()
            if not ok:
                break
            delivered += 1
    assert delivered == sum(fact.frame_count for fact in facts)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/io/test_multi.py -k with_an_edit_list -v`

Expected: **one red, one green.**

| test | outcome before this task |
| --- | --- |
| `test_a_segment_with_an_edit_list_is_readable_through_the_multi_reader` | FAILS -- index-provenance `MediaProbeError` from Task 3 step 6: the segment index was built in `container_default` while its reader decodes in `edit_list_ignored` |
| `test_a_segment_with_an_edit_list_delivers_every_frame` | PASSES -- a sequential read never reaches the provenance check, so it pins delivery rather than the gate |

Paste the real output into the task record. This selector and these names are
exact; if pytest reports "no tests ran" the selector is stale, not the code.

- [ ] **Step 3: Derive the gate per segment**

`MultiVideoReader` holds each segment's facts, so it knows the gate without a
scan. Where it builds a segment index:

```python
        ignore_edit_list = self._facts[segment_index].discard_flagged_packets > 0
        packets, _source = scan_packets_in_process(
            self._segments[segment_index].path, ignore_edit_list=ignore_edit_list
        )
        built = build_seek_index(
            packets,
            source="in_process",
            space="edit_list_ignored" if ignore_edit_list else "container_default",
        )
```

Use the accessor the surrounding code already uses for per-segment facts rather
than introducing a second one; `_open_segment` (`multi.py:190`) is the precedent.

`multi.py`'s module docstring says a segment without an injected index "builds it
from an in-process packet scan on first use"; extend it to say that scan runs in
the segment's own timestamp space, and that an injected index built in a
different one is rejected by the per-segment reader.

An injected per-segment index needs no new handling: `_segment_index` returns the
cached injection before the gate runs, and the reader's own provenance check then
accepts or rejects it -- which is the protocol the spec describes, since a caller
can tell the gate fired from `discard_flagged_packets` on the facts it holds.

- [ ] **Step 4: Run the io suite**

Run: `uv run pytest tests/io/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mosaic_media/io/multi.py tests/io/test_multi.py
git commit -m "Build each segment index in its own timestamp space"
```

---

### Task 9: Acceptance and non-regression pins

**Files:**
- Create: `tests/helpers/scans.py`
- Modify: `tests/io/test_multi.py` (migrate its scan-counting helper)
- Test: `tests/io/test_reader_recovery.py`, `tests/transcode/test_convert.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `count_packet_scans` in `tests/helpers/scans.py`.

- [ ] **Step 1: Extract the scan-counting helper**

`tests/io/test_multi.py:146-169` already counts `scan_packets_in_process` calls
by monkeypatching the module attribute. Task 9 needs the same behavior for the
reader, so extract it once rather than writing a second copy, and migrate the
existing caller in the same change:

```python
"""Counting packet scans, for tests that assert a path does not probe."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

import pytest

from mosaic_media.probe.ffprobe import Packet, TimestampSource


@contextmanager
def count_packet_scans(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[[], int]]:
    """Count `scan_packets_in_process` calls made through `module` while active.

    Patches the name where it is looked up rather than where it is defined, so
    each caller module is counted independently.
    """
    calls = 0
    original = module.scan_packets_in_process

    def counting(
        path: Path, *, ignore_edit_list: bool = False
    ) -> tuple[tuple[Packet, ...], TimestampSource]:
        nonlocal calls
        calls += 1
        return original(path, ignore_edit_list=ignore_edit_list)

    monkeypatch.setattr(module, "scan_packets_in_process", counting)
    yield lambda: calls
```

Rewrite `tests/io/test_multi.py`'s existing count to call this and delete its
local copy. Its `failing_scan` helper (`tests/io/test_multi.py:257-259`) is a
near-neighbor of the same idea; migrate it here too rather than leaving one
behind -- `assert scans() == 0` subsumes what it asserts.

- [ ] **Step 2: Pin that the injected-facts path runs no packet scan**

```python
def test_injected_facts_sequential_read_runs_no_packet_scan(
    clips: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The performance gate measures this path against OpenCV across seventeen
    # payloads, five of which run no packet scan today. A scan added here would
    # not fail a correctness test; it would fail the gate on a machine this suite
    # never runs on. Pin it where it is cheap to see.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    with count_packet_scans(reader_module, monkeypatch) as scans:
        with VideoReader(path, facts=facts) as reader:
            delivered = sum(1 for _index, _frame in reader)
        assert delivered == facts.frame_count
        assert scans() == 0


def test_injected_facts_metadata_access_opens_no_container(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    # What the metadata-open payloads measure. Point the reader at a path with no
    # file behind it: geometry still resolves from the facts, and any container
    # open -- including the rotation probe's second one -- would raise
    # "failed to open" here instead.
    facts = probe_media(clips["cfr_30fps_mp4"])
    with VideoReader(tmp_path / "absent.mp4", facts=facts) as reader:
        assert (reader.width, reader.height, reader.fps, reader.frame_count) == (
            facts.width,
            facts.height,
            facts.fps,
            facts.frame_count,
        )
```

Asserting on `reader._container` instead would fail the strict type check --
`_container` is a protected attribute of a class, and the `reportPrivateUsage`
carve-out for tests covers importing an underscore-prefixed *module*, not
reaching into an instance. The absent path proves the same thing through public
surface, and additionally covers `_probe_rotation`'s separate open, which a
`_container` assertion would miss.

- [ ] **Step 3: Pin the derivative delivers one frame per source packet**

In `tests/transcode/test_convert.py`:

```python
@requires_svtav1
def test_a_source_cut_mid_stream_reencodes_to_full_delivery(
    avi_starting_on_non_keyframes: Path, tmp_path: Path
) -> None:
    source_facts = probe_media(avi_starting_on_non_keyframes)
    result = transcode(
        avi_starting_on_non_keyframes, tmp_path / "out.mp4", "playback", PLAYBACK_ENCODING
    )
    assert result.operation is Operation.REENCODE_AV1
    assert result.output_facts is not None
    assert result.output_facts.start_time == 0.0
    assert result.output_path is not None
    with VideoReader(result.output_path, facts=result.output_facts) as reader:
        delivered = sum(1 for _index, _frame in reader)
    assert delivered == result.output_facts.frame_count
    assert delivered >= source_facts.frame_count
```

Assert delivery against the derivative's own facts, not equality with the
source's: a constant-rate resample is not a frame-for-frame copy, and the
derivative's facts are authoritative for it.

- [ ] **Step 4: Run the full suite and the checks**

```bash
uv run ruff format src/ tests/ scripts/
uv run ruff check src/ tests/ scripts/
uv run basedpyright src/ tests/ scripts/
```

Then offload the suite to the configured task machine and read its log in a
separate command. Expected: all clean. Fix findings; never suppress.

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "Pin frame delivery and the scan-free read path"
```

---

### Task 10: Consumer coordination

Two new required `MediaFacts` fields are one logical change across three
repositories. This task produces no code here.

- [ ] **Step 1: Write the handoff issue**

Create `docs/issues/consumers-need-the-frame-delivery-counts.md` recording:
`discard_flagged_packets` and `leading_non_keyframe_frames` are new required
fields; the toolkit stores full facts as JSON in its media index so they need no
flat column, and its existing re-probe path is the migration route; the backend
stores facts as flat columns and needs a migration plus a `FACT_FIELDS` entry.
Note that a re-probe re-mints no `video_uuid` or `content_digest`, because
identity hashes packets and the header and never `MediaFacts`. Note also that the
toolkit's stale-facts error names the identity fields specifically and should
widen to cover a row stale on any measurement.

- [ ] **Step 2: Add its `_INDEX.md` row and commit**

```bash
git add docs/issues/
git commit -m "Record the consumer migration the new probe counts require"
```

---

### Task 11: Archive what this work closes

The last commit on the branch before the merge, so nothing it archives is still
in flight.

**Files:**
- Modify: `docs/specs/2026-08-01-deliverable-frame-model.md`,
  `docs/specs/_INDEX.md`, `docs/plans/_INDEX.md`, `docs/issues/_INDEX.md`
- Rename on disk: the spec, the amendments document, this plan, and the issue
  files this work closes

- [ ] **Step 1: Fold the confirmed amendments into the spec**

Before anything is archived, because archiving the spec with an amendment
outstanding discards it.

`docs/specs/2026-08-01-deliverable-frame-model-amendments.md` holds design
changes found during implementation and deliberately kept out of the spec until
implementation confirmed them. Each amendment carries a **Status:** line and an
**Amends:** line. For every amendment whose status reads confirmed, rewrite each
spec passage its **Amends:** line names, so the spec describes what was built.
For any amendment the implementation refuted, delete it from the spec's
description and record in the amendments document that it was refuted and why;
do not silently drop it.

Work from the **Amends:** lines rather than from a search for changed text. An
amendment can amend a passage that is not wrong on its face -- a table row, a
verification bullet, a cross-reference -- and those are the ones a search for
stale wording will not reach.

An amendment's sections that name no spec passage stay in the amendments
document rather than moving into the spec. That is the intended division: the
spec carries the conclusions, the archived amendment carries the measurements
behind them, retrievable at the hash its index row records.

The one exception is a structural property of the code that a rewritten passage
depends on. Move that into the passage, without its provenance and without its
measurements. Amendment 1 has one: the artifact reaches a caller through the
reusable-decoder branch, which returns before any landing verification, so no
landing tolerance can contain it and the per-segment scoping cannot later be
relaxed into a landing-check gate. Folded without that, change 1 states the rule
and nothing states why the obvious alternative fails.

Then check the fold rather than assuming it: for each amendment, confirm the
spec no longer states the superseded claim anywhere, including in tables and
verification bullets, not only in the prose section the amendment's first
sentence names.

Record the commit this branch is on before you begin, as `<pre-fold-commit>`;
step 5 needs it.

Commit the fold on its own, before anything is archived:

```bash
git add docs/specs/
git commit -m "Describe the reader's decoder flag scope as built"
```

This commit is not optional and its order is not incidental. The archiving
commit removes the spec from tracking, so an uncommitted fold never enters
history at all -- and the hash the next step records, which becomes the index
row's only retrieval key once the document is untracked, would resolve to the
spec still carrying the superseded claim. The folded spec would survive only as
a gitignored file on whichever machine ran the archive. Write the message to
describe what the spec now says, not that a fold happened.

- [ ] **Step 2: Confirm the archive suffixes are ignored**

`*.implemented.md`, `*.superseded.md` and `*.closed.md` must be in
`.gitignore` (they are, at `.gitignore:19-21`) so a renamed doc stays on disk
without re-entering tracking.

- [ ] **Step 3: Archive the issues this work closes**

Only after the suite is green. Record `git rev-parse HEAD` first -- the archiving
commit's parent is the last commit that tracks each doc, and a commit cannot
contain its own hash.

```bash
git rm --cached docs/issues/reader-cannot-deliver-frames-the-facts-declare.md
git rm --cached docs/issues/playback-transcode-introduces-non-zero-start-time.md
git rm --cached docs/issues/context-managers-annotate-the-concrete-class-not-self.md
git rm --cached docs/issues/sparse-reads-undercount-the-sequential-delivery-count.md
mv docs/issues/reader-cannot-deliver-frames-the-facts-declare.md \
   docs/issues/reader-cannot-deliver-frames-the-facts-declare.closed.md
mv docs/issues/playback-transcode-introduces-non-zero-start-time.md \
   docs/issues/playback-transcode-introduces-non-zero-start-time.closed.md
mv docs/issues/context-managers-annotate-the-concrete-class-not-self.md \
   docs/issues/context-managers-annotate-the-concrete-class-not-self.closed.md
mv docs/issues/sparse-reads-undercount-the-sequential-delivery-count.md \
   docs/issues/sparse-reads-undercount-the-sequential-delivery-count.closed.md
```

Prepend the closed-doc disclaimer to each, and flip the row of each issue
untracked above from `active` to `closed` with the recorded hash. Leave every
other row alone: the index carries active rows for open work this branch does
not close, and marking one closed while its document stays tracked is the state
the archiving convention forbids in either direction.

Each closed row's description gains a `Resolved:` clause saying what landed.
Once a document is untracked its row is the only discoverable record of it, and
a row left describing the defect in the present tense describes code this branch
changed.

Do not commit yet -- step 4 adds this work package's own spec, amendments
document and plan to the same commit.

`reader-cannot-deliver-frames-the-facts-declare` and
`playback-transcode-introduces-non-zero-start-time` proposed mechanisms that
turned out wrong, so their disclaimer points at the spec, and a future reader
does not act on the superseded diagnosis.

`sparse-reads-undercount-the-sequential-delivery-count` is also one this branch
opened and closed, and needs no pointer either: its own document records both
what closed it and which half of its deferral reasoning held.

`context-managers-annotate-the-concrete-class-not-self` is one this branch
opened and closed. The io context managers annotated `__enter__` with a quoted
concrete class rather than `Self`, so a
subclass lost its own type inside a `with` block and any method it added failed
the strict type check. All three modules, and the subclass that paid the cost,
are this branch's own diff. The commit returning `Self` from all three satisfies
the issue's closing criteria, so its proposed fix is the one that shipped and it
needs no pointer to a superseded diagnosis.

- [ ] **Step 4: Archive the work package's own documents, and commit**

The spec, the amendments document and this plan are implemented once the branch
is green, and leave tracking in the same commit as the issues:

```bash
git rm --cached docs/specs/2026-08-01-deliverable-frame-model.md
git rm --cached docs/specs/2026-08-01-deliverable-frame-model-amendments.md
git rm --cached docs/plans/2026-08-01-deliverable-frame-model.md
mv docs/specs/2026-08-01-deliverable-frame-model.md \
   docs/specs/2026-08-01-deliverable-frame-model.implemented.md
mv docs/specs/2026-08-01-deliverable-frame-model-amendments.md \
   docs/specs/2026-08-01-deliverable-frame-model-amendments.implemented.md
mv docs/plans/2026-08-01-deliverable-frame-model.md \
   docs/plans/2026-08-01-deliverable-frame-model.implemented.md
```

`git rm --cached` untracks without deleting; the rename then takes the file to a
suffix `.gitignore` keeps out of tracking. Renaming alone leaves the original
path tracked and deleted, which is the state that looks archived and is not.

Flip the row of each document untracked above in `docs/specs/_INDEX.md` and
`docs/plans/_INDEX.md` from `active` to `implemented` with the recorded parent
hash. Leave every other row alone, for the reason step 3 gives: another work
package may have a spec or plan of its own in flight by the time this branch
merges, and its row is not this task's to move.

Then commit everything this task untracked and every index it edited, as one
commit:

```bash
git add docs/specs/_INDEX.md docs/plans/_INDEX.md docs/issues/_INDEX.md
git commit -m "Archive the frame delivery documents and the issues they close"
```

One archiving commit carries every document this task untracked and every index
recording where they went. Confirm it before moving on: `git status --short`
must show no `D` entry for any document this task untracked, and
`git ls-files docs/` must list none of them.

The amendments document is archived beside the spec it folded into, not deleted:
its measurements are the record of why the spec says what it says, and the spec
carries the conclusions without the evidence.

An archiving run that leaves any of these rows reading `active`, or any of these
documents tracked, is incomplete.

- [ ] **Step 5: Verify the archive touched no code**

```bash
git diff <pre-fold-commit> -- src/ tests/
```

Expected: empty. Code and tests must be byte-identical to the state before this
task began.

The base is `<pre-fold-commit>`, recorded in step 1, not the commit before the
archiving commit. Step 1 now has its own commit, so an archive-relative base
would place this check after the fold and it would pass by construction --
seeing nothing of the one step in this task that edits a file for its content
rather than renaming it. This is the only guard on that step.

---

## Self-review

**Spec coverage.** Change 1 -> Task 3 step 5. Change 2 -> Task 3 steps 4-5.
Change 3 -> Task 2, Task 3 step 6, Task 8. Change 4 -> Task 1. Change 5 -> Task 6
(literal, policy, verdict, and the routing into `_REENCODE_ANALYSIS_REASONS`).
Change 6 -> Task 6 (deliverability) and Task 7 (muxability). Change 7 -> Task 4
(seek) and Task 5 (sequential). The performance constraint -> Task 3 step 4 and
Task 9 step 2. Spec verification items: index-provenance rejection -> Task 3
step 6 and Task 8 step 2; unresolvable landing -> Task 4 step 3; both `show_all`
shapes -> Task 3 step 2; mp4 carriage measured member by member -> Task 7.

**Both verification shapes exist and are pre-flighted.** `open_gop.mp4` is in
`tests/assets/` with its recipe in that directory's README; the mid-stream shape
is the generated `avi_starting_on_non_keyframes`. Both measured:
`show_all` is a no-op on the open-GOP clip (50 frames either way, identical
timestamps and pixels), and reader seeks land frame-exact on it at every
sampled target, including the two leading pictures where a decode-order nearest
keyframe returns the right timestamp with the wrong pixels. The second result is
a test rather than a note because presentation-order keyframe resolution is what
makes open GOP work, and an optimization switching to decode-order would pass
every other test in the suite.

**Type consistency.** `IndexSource` and `IndexSpace` are defined in Task 2 and
used under those names in Tasks 3 and 8. `Packet.discard`,
`discard_flagged_packets` and `leading_non_keyframe_frames` are defined in
Task 1 and used in Tasks 3, 6 and 8. `FRAME_EXACT_CODECS` and
`Thresholds.frame_exact_codecs` are defined in Task 6 and read in Task 6's
membership test. `Flags2.show_all` appears only in Task 3; Task 6 uses the
command-line spelling `-flags2 +showall`.

**Duplication.** Three extractions rather than parallel copies: every test-side
`build_seek_index` and `SeekIndex` construction, across every module that holds
one, routes through `index_of` / `index_for` in `tests/helpers/indexes.py`
(Task 2 step 5, which locates them by search rather than by count);
the scan-counting context manager is extracted to
`tests/helpers/scans.py` and its existing caller in `tests/io/test_multi.py`
migrated in the same change (Task 9 step 1); and both moved fixtures land in
`tests/helpers/media_fixtures.py` beside their siblings with no copy left behind
(Task 3 step 1). Task 7 encodes no sample a fixture already provides: `vp8`,
`vp9` and `mjpeg` come from the existing clips, and only the codecs nothing else
needs are built there. No task adds a second `MediaFacts` baseline -- Task 1 step 7
extends the existing `CLEAN`.

**Tests rewritten, never deleted.** Task 4 step 1 rewrites
`test_seek_landing_mismatch_raises` whose contract inverts, and Task 6 step 2
rewrites the two branch tests written against the rejected `-copyinkf` approach.
