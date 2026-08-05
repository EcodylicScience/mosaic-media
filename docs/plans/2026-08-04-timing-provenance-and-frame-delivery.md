# Timing provenance and frame delivery implementation plan

**Goal:** Stop treating timing the demultiplexer invented as timing the file
supplied, route the sources that cannot be copy-remuxed correctly to a re-encode,
refuse a source that states no rate anywhere, and catch a decoder that emits two
pictures at one timestamp.

**Architecture:** `MediaFacts.timing_measured`, a boolean, is replaced by
`timing_source`, a four-valued `Literal` computed from the timestamp source and an
explicit list of formats measured as inventing their timing. One new verdict
reason, `presentation_timing_requires_decode`, says a stream copy cannot produce
correct timing for a source and selects a re-encode on both targets. A source with
neither timestamps nor a stated rate is refused rather than given the muxer's
approximation. The frame reader gains an equality condition on consecutive decoded
timestamps.

**Tech Stack:** Python 3.12, uv, system ffmpeg and ffprobe, PyAV in the `[io]`
layer, pytest, basedpyright, ruff.

**Source spec:** `docs/specs/2026-08-04-timing-provenance-and-frame-delivery.md`.
Read it before starting. Tasks 1 through 8 and Task 10 implement that spec and
none departs from it; if one cannot be implemented as written, stop and report
rather than improvising.

**Task 9 is deliberately outside the spec.** It closes
`docs/issues/test-stubs-drift-from-the-signatures-they-stand-in-for.md`, which
carries no design content -- its issue document states the closing property in
full -- so it needed no spec and got none. It rides on this branch by explicit
decision, touches only test stand-in signatures, and shares no file with any
other task. Read that issue rather than the spec before starting it, and judge
the branch's convergence against the spec over the other nine tasks.

## Global constraints

- Python floor is 3.12. Never raise it.
- Import direction is one-way: never import `mosaic_api` or `mosaic`.
- Layers: `core` (probe, verdict, ffmpeg command construction) is standard library
  only; `[io]` may add numpy and `av`; `[cli]` may add typer. Only
  `mosaic_media.cli` imports typer; only `[io]` modules import numpy or `av`.
- The probe never decodes.
- No copyleft-only encoder named anywhere in `src/` or `tests/`: not `libx264`,
  `libx264rgb`, `libx265`, `libx262`, `libxvid`. `tests/test_encoder_guard.py`
  enforces this by scanning string literals in `.py` files under both trees.
- Known string sets are `Literal` aliases, never bare `str`. This applies to test
  helpers too.
- No `typing.Any`, no `typing.Optional` (use `X | None`), no `typing.cast`, no
  `# noqa`, no `# pyright: ignore`, no `# type: ignore`. Fix the design instead.
- No multi-line f-strings. Parenthesized adjacent string literals are assigned to a
  variable, never passed bare as a call argument.
- ASCII only in code and comments. American spelling. No unnecessary abbreviations
  in identifiers.
- Commit messages are plain English: no conventional-commit prefixes, no
  `Co-Authored-By`, no references to this plan, its tasks, or any tooling.
- Verification command for a touched file set:
  `uv run ruff format <paths> && uv run ruff check <paths> && uv run basedpyright <paths>`.
  Full suite runs go through `heavy`: `heavy uv run pytest tests/`.
- Never run `pytest -m bench` except through `heavy` with `-n0 -s`, and not as part
  of this plan.

## File structure

| File | Responsibility | Tasks |
| --- | --- | --- |
| `src/mosaic_media/transcode/errors.py` | New. Owns `TranscodeError` so both `commands.py` and `convert.py` import it without a cycle. | 1 |
| `src/mosaic_media/probe/ffprobe.py` | `TimingSource` alias, the invented-timing list, `timing_source_for`, `timing_supplied_by_source`, the widened elementary-stream rate, `coded_reordering_depth` on `Header`. | 2, 3, 4 |
| `src/mosaic_media/probe/facts.py` | `MediaFacts` loses `timing_measured`, gains `timing_source` and `coded_reordering_depth`. | 3, 4 |
| `src/mosaic_media/probe/probe.py` | Computes both new facts. | 3, 4 |
| `src/mosaic_media/probe/identity.py` | Hash parameter renamed; `compare_for_duplicate` rekeyed. | 4 |
| `src/mosaic_media/probe/policy.py` | The new reason in both vocabularies and in the hard stream set. | 6 |
| `src/mosaic_media/probe/verdict.py` | The two rekeys, and the new reason's firing conditions. | 4, 6 |
| `src/mosaic_media/transcode/commands.py` | `timestamp_fps` rekeyed, the new reason in both re-encode sets, the refusal. | 4, 6, 7 |
| `src/mosaic_media/transcode/convert.py` | Imports `TranscodeError`; documented raise list gains the refusal. | 1, 7 |
| `src/mosaic_media/io/reader.py` | The collapse check. | 8 |
| `tests/helpers/media_fixtures.py` | New fixtures, and the committed asset name. | 2, 5, 7 |

Task 4 touches six source files at once. That is not a decomposition failure: it
removes a field with five readers, so splitting the field change from the rekeys
-- the one split that suggests itself -- leaves the tree unbuildable at the first
commit. A smaller split does exist and is deliberately not taken: the alias, the
container list and the two pure functions could land alone, but that commit adds
symbols nothing yet uses and buys no earlier verification. Tasks 8 and 9 share no
file with any other task and may be done in any order relative to them.

---

### Task 1: Move `TranscodeError` out of `convert.py`

The refusal in Task 7 is raised from `commands.py`. `TranscodeError` is defined in
`convert.py`, which already imports `commands.py` at line 50, so raising it from
`commands.py` would be a circular import. This task is a pure relocation with no
behavior change, done first so Task 7 has somewhere to raise from.

**Files:**
- Create: `src/mosaic_media/transcode/errors.py`
- Modify: `src/mosaic_media/transcode/convert.py` (remove the class definition, import it instead)
- Modify: `src/mosaic_media/transcode/__init__.py` (re-export from the new module)
- Test: `tests/transcode/test_errors.py` (new)

**Interfaces:**
- Produces: `mosaic_media.transcode.errors.TranscodeError`, a `RuntimeError` subclass. Still importable as `mosaic_media.transcode.TranscodeError`, unchanged for every existing caller.

- [ ] **Step 1: Write the failing test**

Create `tests/transcode/test_errors.py`:

```python
"""The transcode error type is importable from both modules that raise it."""

from mosaic_media.transcode import TranscodeError as PackageExport
from mosaic_media.transcode.commands import TranscodeError as FromCommands
from mosaic_media.transcode.convert import TranscodeError as FromConvert
from mosaic_media.transcode.errors import TranscodeError


def test_the_error_is_one_class_reached_by_every_path() -> None:
    # The command builder refuses a source that states no rate anywhere, and the
    # converter raises on a run that failed. Both need the same type, and the
    # converter already imports the command builder, so the type cannot live in
    # the converter without making that import circular.
    assert FromCommands is TranscodeError
    assert FromConvert is TranscodeError
    assert PackageExport is TranscodeError


def test_the_error_is_a_runtime_error() -> None:
    assert issubclass(TranscodeError, RuntimeError)
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest tests/transcode/test_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mosaic_media.transcode.errors'`.

- [ ] **Step 3: Create the error module**

Create `src/mosaic_media/transcode/errors.py`:

```python
"""The transcode failure type, in its own module so both the command builder and
the converter can raise it.

The converter imports the command builder, so a type defined in the converter and
raised by the builder would be a circular import. This mirrors
`mosaic_media.probe.errors`, which exists for the same reason.
"""


class TranscodeError(RuntimeError):
    """A transcode could not be built, failed to run, or produced output that was
    not clean for its target."""
```

- [ ] **Step 4: Import it in the converter**

In `src/mosaic_media/transcode/convert.py`, delete the `class TranscodeError`
definition and add to the relative imports, in alphabetical position:

```python
from .errors import TranscodeError
```

Leave every `raise TranscodeError(...)` site untouched.

- [ ] **Step 5: Re-export it from the package**

In `src/mosaic_media/transcode/__init__.py`, change the `TranscodeError` import to
come from `.errors` rather than `.convert`. Leave the `__all__` entry as it is:
consumers import from the package and must see no change.

- [ ] **Step 6: Run the test and the transcode suite**

Run: `uv run pytest tests/transcode/ -v`
Expected: PASS, including the new file. No other test changes.

- [ ] **Step 7: Verify types and formatting**

Run: `uv run ruff format src/mosaic_media/transcode/ tests/transcode/ && uv run ruff check src/mosaic_media/transcode/ tests/transcode/ && uv run basedpyright src/mosaic_media/transcode/ tests/transcode/`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/mosaic_media/transcode/errors.py src/mosaic_media/transcode/convert.py src/mosaic_media/transcode/__init__.py tests/transcode/test_errors.py
git commit -m "Give the transcode error its own module

The command builder needs to raise it, and the converter that defines it today
already imports the builder, so the type has to sit below both."
```

---

### Task 2: Derive the elementary stream rate for HEVC as well as H.264

`elementary_stream_fps` is gated on the format and codec both being `h264`, so
`declared_fps` reads zero for every raw HEVC stream whatever its bitstream states.
Task 7 refuses a source whose `timing_source` is `absent` and whose `declared_fps`
is zero, which without this task would refuse the entire codec rather than the
"states no rate" class.

H.264 counts two ticks per frame; HEVC counts one. Measured on a raw HEVC stream
copied from the committed 25 frames per second asset, the tick rate reads `25/1`,
so a divisor of one gives 25.0 where two would give 12.5.

**Files:**
- Modify: `src/mosaic_media/probe/ffprobe.py` (`elementary_stream_fps` and its constants)
- Modify: `tests/probe/test_ffprobe.py` (one assertion inverts)
- Modify: `tests/helpers/media_fixtures.py` (new fixture)
- Test: `tests/probe/test_raw_stream.py` (new end-to-end assertion)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `elementary_stream_fps(stream: dict[str, object], container: str, codec_name: str) -> float`, signature unchanged, now non-zero for `hevc` as well as `h264`. A session-scoped fixture `raw_hevc_clip` returning a `Path`.

- [ ] **Step 1: Both measurements are already taken; commit the asset they used**

Both were run before this task was dispatched, so they are recorded here as
results rather than as instructions. Do not repeat the hunt.

**The divisor is one.** A rate-stating raw HEVC stream, copied from the committed
HEVC asset, reports `r_frame_rate` of `25/1` for a 25 frames per second source.
A divisor of one gives 25.0; a divisor of two would give 12.5. H.264 states two
ticks per frame, HEVC one, and this is that difference measured.

**The ceiling still rejects at a divisor of one.** A raw HEVC stream whose
bitstream carries no timing reports `1200000/1` -- the demuxer time base echoed
back, the same value H.264 reports for the same case. Divided by one that is
1200000, far above the plausibility ceiling of 1000.0, so the ceiling holds and
needs no codec-specific value.

Getting a valid rate-less HEVC stream is the part worth recording. The metadata
bitstream filter cannot produce one: `hevc_metadata=tick_rate=0` writes a file
that will not probe at all (`Invalid data found when processing input`), and the
filter exposes no other timing option. The encoder can, through a parameter that
suppresses the timing block while leaving the stream valid. The file is committed
rather than generated at test time, which is this repository's route for media an
encoder must produce:

```bash
ffmpeg -v error -y -f lavfi -i "testsrc2=size=128x96:rate=25" -frames:v 25 \
    -c:v <encoder> -x265-params "log-level=none:vui-timing-info=0" \
    -f hevc tests/assets/raw_no_declared_rate.hevc
```

That file already exists in the working tree, generated by this command and
verified: 14588 bytes, 25 frames decoded, `r_frame_rate` of `1200000/1`, no
packet timestamps, and `elementary_stream_fps` of 0.0 today because the gate
still excludes HEVC. Stage and commit it with this task's other changes, and add
a row to `tests/assets/README.md` carrying that exact command -- as every other
committed asset there does. The encoder guard scans string literals in `.py`
files under `src/` and `tests/` only, so naming the encoder in Markdown is in
bounds; naming it in a Python file is not.

Add `"raw_no_declared_rate.hevc"` to the closed `AssetName` literal in
`tests/helpers/media_fixtures.py`, or the `asset` helper cannot reach it and the
call does not type-check.

Verify both properties before you rely on it, because a corrupt stream reports
the same implausible tick rate and a check on the rate alone would accept one:

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate \
    -of csv=p=0 tests/assets/raw_no_declared_rate.hevc
ffprobe -v error -select_streams v:0 -count_frames \
    -show_entries stream=nb_read_frames -of csv=p=0 \
    tests/assets/raw_no_declared_rate.hevc
```

Expect `1200000/1` and `25`.

- [ ] **Step 2: Add the raw HEVC fixture**

In `tests/helpers/media_fixtures.py`, beside the other session-scoped clip
fixtures:

```python
@pytest.fixture(scope="session")
def raw_hevc_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A raw HEVC elementary stream that states its rate in its own bitstream.

    Copied from the committed HEVC asset rather than encoded: a stream copy needs
    no encoder, and the encoders that would produce HEVC directly are the ones
    this package does not name.
    """
    root = tmp_path_factory.mktemp("raw_hevc")
    return build(
        root / "raw.hevc",
        "-c",
        "copy",
        source=["-i", str(asset("hevc.mp4", root / "hevc.mp4"))],
    )
```

- [ ] **Step 3: Write the failing tests**

In `tests/probe/test_ffprobe.py`, replace
`test_elementary_stream_fps_is_absent_for_a_raw_hevc_stream` with:

```python
def test_elementary_stream_fps_reads_one_tick_per_frame_for_a_raw_hevc_stream() -> None:
    # HEVC states one tick per frame where H.264 states two, so the H.264 divisor
    # does not carry over: halving here would report half the real rate. The rate
    # matters because a raw stream with none is refused rather than remuxed, and
    # a codec whose rate is never derived would be refused entirely.
    payload = stream_payload(r_frame_rate="30/1")
    assert elementary_stream_fps(payload, "hevc", "hevc") == 30.0


def test_elementary_stream_fps_rejects_the_demuxer_time_base_for_hevc() -> None:
    # The same guard H.264 has. A bitstream carrying no timing makes libavformat
    # report the demuxer time base, which is not a frame rate at all.
    payload = stream_payload(r_frame_rate="1200000/1")
    assert elementary_stream_fps(payload, "hevc", "hevc") == 0.0
```

In `tests/probe/test_raw_stream.py`, add both ends of the widening -- the rate a
bitstream states, and the absence of one:

```python
def test_a_raw_hevc_stream_carries_the_rate_its_bitstream_states(
    raw_hevc_clip: Path,
) -> None:
    # End to end through the header read, not only the payload helper. Without
    # this the widened derivation is pinned only against a synthetic dictionary.
    facts = probe_media(raw_hevc_clip)
    assert facts.declared_fps == pytest.approx(25.0)


def test_a_raw_hevc_stream_stating_no_rate_carries_none(tmp_path: Path) -> None:
    # The other end, on real media rather than a hand-built payload: a bitstream
    # with no timing block makes the demultiplexer report its own time base,
    # which is not a frame rate at all. Measured at 1200000/1, so the
    # plausibility ceiling rejects it even at one tick per frame -- which is what
    # keeps a divisor of one from turning an absent rate into an enormous one.
    path = asset("raw_no_declared_rate.hevc", tmp_path / "rate_less.hevc")
    facts = probe_media(path)
    assert facts.declared_fps == 0.0
```

That second test needs `asset` imported from `tests.helpers.media_fixtures`.

- [ ] **Step 4: Run them and confirm they fail**

Run: `uv run pytest tests/probe/test_ffprobe.py tests/probe/test_raw_stream.py -v`
Expected: the two `elementary_stream_fps` tests FAIL returning `0.0`; the raw HEVC
test FAILS with `declared_fps` of `0.0`.

- [ ] **Step 5: Widen the derivation**

In `src/mosaic_media/probe/ffprobe.py`, replace `_H264_TICKS_PER_FRAME` and the
gate in `elementary_stream_fps`:

```python
# Ticks per frame by (format name, codec name), for the raw demuxers whose
# r_frame_rate carries the bitstream's own tick rate. The convention is
# codec-specific: H.264 states two ticks per frame, HEVC one. Restricted to the
# raw demuxers, because r_frame_rate means something else for a container --
# there it is the container's own frame rate, and dividing it would report a
# fraction of the true rate (measured 12.5 on a 25 fps mp4).
_ELEMENTARY_STREAM_TICKS_PER_FRAME: dict[tuple[str, str], float] = {
    ("h264", "h264"): 2.0,
    ("hevc", "hevc"): 1.0,
}
```

and the body:

```python
    ticks_per_frame = _ELEMENTARY_STREAM_TICKS_PER_FRAME.get((container, codec_name))
    if ticks_per_frame is None:
        return 0.0
    tick_rate = parse_fraction(str(stream.get("r_frame_rate", "0/1")))
    rate = tick_rate / ticks_per_frame
    if not 0.0 < rate <= _MAXIMUM_PLAUSIBLE_FPS:
        return 0.0
    return rate
```

Update the docstring: it currently says "H.264 elementary stream" and names the
halving; it now covers both codecs and names the per-codec tick convention.

- [ ] **Step 6: Run the tests again**

Run: `uv run pytest tests/probe/ -v`
Expected: PASS.

- [ ] **Step 7: Verify types and formatting**

Run: `uv run ruff format src/mosaic_media/probe/ tests/probe/ tests/helpers/ && uv run ruff check src/mosaic_media/probe/ tests/probe/ tests/helpers/ && uv run basedpyright src/mosaic_media/probe/ tests/probe/ tests/helpers/`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/mosaic_media/probe/ffprobe.py tests/probe/test_ffprobe.py tests/probe/test_raw_stream.py tests/helpers/media_fixtures.py
git commit -m "Read the declared rate from a raw HEVC bitstream too

HEVC states one tick per frame where H.264 states two, so the rate is the tick
rate unchanged. Without this the field is zero for every raw HEVC stream whatever
its bitstream says."
```

---

### Task 3: Record the coded reordering depth

The verdict in Task 6 needs to know whether a bitstream is coded with reordering.
`ffprobe` reports it as `has_b_frames` on the stream. Measured: 0 for both
committed raw fixtures and for the split AV1 stream, 2 for ordinary containerized
sources and for a raw stream coded with bidirectional prediction, 1 for a default
MPEG-2 encode.

This task adds the measurement only. Nothing reads it until Task 6.

**Files:**
- Modify: `src/mosaic_media/probe/ffprobe.py` (`Header` gains a field, read from the stream dictionary)
- Modify: `src/mosaic_media/probe/facts.py` (`MediaFacts` gains a field)
- Modify: `src/mosaic_media/probe/probe.py` (passes it through)
- Modify: `tests/probe/test_verdict.py` and `tests/transcode/test_commands.py` (hand-built facts gain the field)
- Test: `tests/probe/test_probe.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Header.coded_reordering_depth: int` and `MediaFacts.coded_reordering_depth: int`. Zero means no reordering; it is a real measurement there, not a placeholder.

- [ ] **Step 1: Write the failing test**

In `tests/probe/test_probe.py`:

```python
def test_the_coded_reordering_depth_separates_reordered_bitstreams(
    clips: dict[str, Path], open_gop_clip: Path
) -> None:
    # The bitstream's own reordering depth, which decides whether a stream copy
    # can recover presentation order. A raw stream carries no timestamps, so a
    # copy synthesizes them from the packet index -- decode order -- and that is
    # only presentation order when nothing is reordered.
    assert probe_media(clips["raw_h264"]).coded_reordering_depth == 0
    assert probe_media(clips["raw_fractional_rate_h264"]).coded_reordering_depth == 0
    assert probe_media(open_gop_clip).coded_reordering_depth == 2
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest tests/probe/test_probe.py -k coded_reordering -v`
Expected: FAIL with `AttributeError: 'MediaFacts' object has no attribute 'coded_reordering_depth'`.

- [ ] **Step 3: Add the field to the header**

In `src/mosaic_media/probe/ffprobe.py`, add to `Header` after `declared_frame_count`:

```python
    coded_reordering_depth: int
```

and populate it in `read_header` beside the other stream reads:

```python
        coded_reordering_depth=int(_number(stream.get("has_b_frames"), 0.0)),
```

- [ ] **Step 4: Add the field to the facts**

In `src/mosaic_media/probe/facts.py`, add after `leading_non_keyframe_frames`:

```python
    coded_reordering_depth: int
```

and to the class docstring, as its own paragraph:

```
`coded_reordering_depth` is how many pictures the bitstream may hold back before
presenting one, which is what decides whether decode order is presentation order.
Zero is a measurement rather than an absence: it says the bitstream reorders
nothing, so a stream copy that synthesizes timestamps from the packet index
labels the right pictures. It is read from the coded stream and is meaningful for
every source, but only matters where the timestamps are not presentation
timestamps -- a container carrying real ones already knows the order.
```

- [ ] **Step 5: Pass it through the probe**

In `src/mosaic_media/probe/probe.py`, add to the `MediaFacts(...)` construction:

```python
        coded_reordering_depth=header.coded_reordering_depth,
```

- [ ] **Step 6: Update the hand-built facts**

`tests/probe/test_verdict.py` holds `CLEAN`, the only hand-built `MediaFacts` in
this repository outside the probe itself. Add `coded_reordering_depth=0` to it.

`tests/transcode/test_commands.py` builds nothing -- it imports `CLEAN` from
`tests.probe.test_verdict` and says in a comment why -- but that comment names
the baseline's field count, so update the number there.

The backend also constructs `MediaFacts`, in its row-to-facts conversion, and is
broken by this new required field from here until its own migration lands --
which Task 10 records but does not perform, because that migration is run by an
operator against a live database. So the window does not close within this branch,
and a reader checking the backend after Task 10 should expect it still open. That
is safe: the checkouts are separate and nothing in this repository imports it.

- [ ] **Step 7: Run the probe and transcode suites**

Run: `uv run pytest tests/probe/ tests/transcode/ -v`
Expected: PASS.

- [ ] **Step 8: Verify and commit**

Run: `uv run ruff format src/ tests/ && uv run ruff check src/ tests/ && uv run basedpyright src/ tests/`

```bash
git add -A src/mosaic_media/probe tests/probe tests/transcode
git commit -m "Measure how far a bitstream reorders its pictures

Decode order is presentation order only when nothing is held back, and a source
whose timestamps come from the packet index depends on that being true."
```

---

### Task 4: Replace `timing_measured` with `timing_source`, and rekey every reader

The boolean answers "did the packets carry timestamps", where three questions
matter: did the file supply them, are they presentation or decode timestamps, and
are there any at all. A demultiplexer that invents timestamps makes the boolean
read true for a file whose timing is fabricated.

This task changes the fact **and every reader of it, in one commit**. The field
has five readers, and removing it while any of them still names it leaves a
commit where every call to `derive` raises. Splitting the schema change from the
rekeys would be more reviewable and would not build, so the split is not
available.

**Files:**
- Modify: `src/mosaic_media/probe/ffprobe.py` (alias, list, two functions)
- Modify: `src/mosaic_media/probe/facts.py` (field and docstring)
- Modify: `src/mosaic_media/probe/probe.py` (computes it, passes the hash boolean)
- Modify: `src/mosaic_media/probe/identity.py` (parameter rename, and `compare_for_duplicate`)
- Modify: `src/mosaic_media/probe/verdict.py` (the variable-rate gate and the unreliable-timing branch)
- Modify: `src/mosaic_media/transcode/commands.py` (`timestamp_fps` rekeyed to `absent`)
- Modify: eight test files listed in Step 9
- Test: `tests/probe/test_timing_source.py` (new)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `TimingSource = Literal["presentation", "decode", "synthesized", "absent"]` in `ffprobe.py`
  - `timing_source_for(source: TimestampSource, container: str) -> TimingSource`
  - `timing_supplied_by_source(timing_source: TimingSource) -> bool`
  - `MediaFacts.timing_source: TimingSource`, replacing `timing_measured: bool`
  - `video_uuid_input(content_bytes: bytes, timing_supplied_by_source: bool, packets: tuple[Packet, ...]) -> bytes` and `mint_identity(..., *, timing_supplied_by_source: bool)` -- renamed parameter, unchanged type and hashed bytes

- [ ] **Step 1: Write the failing test**

Create `tests/probe/test_timing_source.py`:

```python
"""Which formats supply their own timing, and which have it invented for them."""

from pathlib import Path

import pytest

from mosaic_media.probe.ffprobe import (
    TimestampSource,
    TimingSource,
    timing_source_for,
    timing_supplied_by_source,
)
from mosaic_media.probe.probe import probe_media
from tests.helpers.media_fixtures import (
    requires_av1_frame_split,
    requires_svtav1,
)

# Verified by putting non-uniform timing through each format and reading it back:
# timing a file supplies survives, timing a demultiplexer invents is replaced by a
# uniform grid. The recipe is recorded beside _INVENTED_TIMING_CONTAINERS in
# mosaic_media/probe/ffprobe.py, which is where it stays reachable.
CLASSIFICATIONS: list[tuple[TimestampSource, str, TimingSource]] = [
    ("pts", "mov,mp4,m4a,3gp,3g2,mj2", "presentation"),
    ("pts", "matroska,webm", "presentation"),
    ("pts", "mpegts", "presentation"),
    ("pts", "m4v", "presentation"),
    ("pts", "ivf", "presentation"),
    ("dts", "avi", "decode"),
    ("pts", "obu", "synthesized"),
    ("dts", "mpegvideo", "synthesized"),
    ("pts", "yuv4mpegpipe", "synthesized"),
    ("pts", "h263", "synthesized"),
    ("pts", "jpeg_pipe", "synthesized"),
    ("none", "h264", "absent"),
    ("none", "hevc", "absent"),
]


@pytest.mark.parametrize(("source", "container", "expected"), CLASSIFICATIONS)
def test_each_measured_format_classifies_as_measured(
    source: TimestampSource, container: str, expected: TimingSource
) -> None:
    assert timing_source_for(source, container) == expected


def test_an_invented_timing_format_wins_over_its_timestamp_source() -> None:
    # MPEG-2 video reaches the probe through the decode-timestamp fallback, the
    # same path a genuine audio video interleave file takes. Reading the source
    # first would call its invented timing file-supplied.
    assert timing_source_for("dts", "mpegvideo") == "synthesized"
    assert timing_source_for("dts", "avi") == "decode"


@pytest.mark.parametrize(
    ("timing_source", "supplied"),
    [
        ("presentation", True),
        ("decode", True),
        ("synthesized", False),
        ("absent", False),
    ],
)
def test_only_file_supplied_timing_counts_as_supplied(
    timing_source: TimingSource, supplied: bool
) -> None:
    assert timing_supplied_by_source(timing_source) is supplied


@requires_svtav1
@requires_av1_frame_split
def test_a_bare_av1_stream_has_its_timing_invented(
    av1_split_clips: dict[str, Path],
) -> None:
    # The measured file behind the classification: a bare stream with no timestamp
    # layer, whose demultiplexer manufactures one per packet.
    assert probe_media(av1_split_clips["obu"]).timing_source == "synthesized"
    assert probe_media(av1_split_clips["matroska"]).timing_source == "presentation"
```

Both markers are required: `av1_split_clips` needs the encoder and the bitstream
filter, and neither is guaranteed by an arbitrary ffmpeg build. A test without
them fails where the suite is designed to skip.

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest tests/probe/test_timing_source.py -v`
Expected: FAIL with `ImportError: cannot import name 'TimingSource'`.

- [ ] **Step 3: Add the alias, the list and the two functions**

In `src/mosaic_media/probe/ffprobe.py`, below `TimestampSource`:

```python
TimingSource = Literal["presentation", "decode", "synthesized", "absent"]

# Formats whose demultiplexer manufactures timestamps the file does not carry.
# Verified one at a time, by writing a source with non-uniform timing through the
# format and reading it back: timing the file supplies survives, timing the
# demultiplexer invents comes back as a uniform grid.
#
#   FILTER="setpts='if(gt(N,9),PTS+3/TB/25,PTS)'"
#   ffmpeg -f lavfi -i "testsrc2=size=128x96:rate=25" -frames:v 20 -vf "$FILTER" \
#       -fps_mode passthrough -c:v CODEC -f MUXER sample.EXT
#   ffprobe -select_streams v:0 -show_entries packet=pts_time -of csv=p=0 sample.EXT
#
# The source carries a three-frame gap after the tenth picture, so a format that
# supplies its own timing reads "... 0.360000 0.520000 ..." and one that does not
# reads "... 0.360000 0.400000 ...". -fps_mode passthrough is load-bearing:
# without it the encoder retimes the frames before the muxer sees them, every
# format reads back uniform, and the measurement says nothing.
#
# h263 is the sharpest case: its demuxer does not merely flatten the spacing but
# reports a rate the file never had. Measured on the recipe clip above, 29.66
# frames per second for a 25 frames per second source. The suite's own h263
# fixture is a different clip and measures a different wrong rate, which is why
# the test asserts a bound rather than a value.
#
# This is a denylist, so a format that has never been put through the measurement
# is trusted. That is the unsafe direction, and it is the reason to extend this
# list by measuring rather than by reasoning about what a container is: m4v and
# ivf are bare streams that nonetheless carry real per-picture timing, and avi
# carries genuine timing through the decode-timestamp path.
_INVENTED_TIMING_CONTAINERS = frozenset(
    {"obu", "mpegvideo", "yuv4mpegpipe", "h263", "jpeg_pipe"}
)


def timing_source_for(source: TimestampSource, container: str) -> TimingSource:
    """Where the timing came from, which is what says whether it can be trusted.

    The order is load-bearing. A format on the invented-timing list is
    `synthesized` whatever its timestamp source, because some of them arrive
    through the decode-timestamp fallback -- the same path a genuine container
    takes -- and reading the source first would call their invented timing
    file-supplied.
    """
    if source == "none":
        return "absent"
    if container in _INVENTED_TIMING_CONTAINERS:
        return "synthesized"
    return "presentation" if source == "pts" else "decode"


def timing_supplied_by_source(timing_source: TimingSource) -> bool:
    """Whether the file itself supplied the timing.

    The one place this membership test is written. It is hashed into `video_uuid`
    and read by the duplicate comparison, so a second copy of it would drift and
    take identity or a duplicate verdict with it.
    """
    return timing_source in ("presentation", "decode")
```

- [ ] **Step 4: Replace the field**

In `src/mosaic_media/probe/facts.py`, replace `timing_measured: bool` with:

```python
    timing_source: TimingSource
```

importing `TimingSource` from `.ffprobe`. Rewrite the three docstring paragraphs
that describe `timing_measured`: they currently say the flag is false for a stream
whose packets carry no timestamps and that it is required because true is the
unsafe value. The replacement says the field records where the timing came from,
that `absent` leaves `fps`, `duration` and `constant_frame_rate` as placeholders
rather than measurements while `frame_count` stays real, that `synthesized` means
the demultiplexer manufactured the timestamps so every value measured from them
describes the invention rather than the file, and that it is required rather than
defaulted because any default asserts a provenance nothing measured.

- [ ] **Step 5: Compute it in the probe**

In `src/mosaic_media/probe/probe.py`, after the timing branch:

```python
    timing = timing_source_for(source, header.container)
```

Keep the placeholder branch keyed on `source == "none"`, exactly as it is: a
synthesized source has timestamps, and measuring them yields the only rate
available, which the timestamp-writing path would otherwise lose. Replace the
`timing_measured=timing_measured` argument to `MediaFacts` with
`timing_source=timing`, and the `mint_identity` call with:

```python
    identity = mint_identity(
        header, packets, timing_supplied_by_source=timing_supplied_by_source(timing)
    )
```

Delete the now-unused local `timing_measured` assignments in both branches.

- [ ] **Step 6: Rename the identity parameter and rekey the duplicate comparison**

In `src/mosaic_media/probe/identity.py`, rename the parameter of
`video_uuid_input` and the keyword of `mint_identity` from `timing_measured` to
`timing_supplied_by_source`. The type stays `bool` and the hashed bytes are
unchanged, so no already-minted value moves except for a file whose classification
changed. Add to `video_uuid_input`'s docstring one sentence saying the boolean is
whether the file supplied the timing, and that it stays a boolean rather than the
four-valued fact so that a file whose classification did not change keeps its
identity.

Then rekey `compare_for_duplicate` in the same file:

```python
    if not (
        timing_supplied_by_source(left.timing_source)
        and timing_supplied_by_source(right.timing_source)
    ) or left.duration <= 0.0:
```

The zero-duration clause beside it is a separate guard keeping the function total
over any facts a caller can construct, and it is untouched. Update the comment
that reasons about the boolean's default-true hazard: a required `Literal` has no
default, so a row that predates the field cannot silently claim provenance.

- [ ] **Step 7: Rekey the two verdict branches**

In `src/mosaic_media/probe/verdict.py`, replace the variable-rate gate:

```python
    if timing_supplied_by_source(facts.timing_source) and not facts.constant_frame_rate:
```

Keep the comment beside it, changing "an unmeasured stream" to "a stream whose
timing the file did not supply". Then replace the unreliable-timing branch:

```python
    if facts.timing_source in ("absent", "synthesized"):
        # Timing the file did not supply. With none at all the frame-to-time
        # mapping is undefined until a remux generates real timestamps; with
        # timing the demultiplexer invented, every value measured from it
        # describes the invention rather than the file. `absent` is what must
        # not be dropped here: a raw stream stating no rate carries no other
        # analysis reason, so without this one the file reports as already
        # analysis-clean and the refusal in a later task is never reached.
        analysis.add("unreliable_timing_metadata")
```

This rekey is why the task cannot be split. Leaving it for a following task means
removing the field while `verdict.py` still names it, which puts a commit on the
branch where every call to `derive` raises `AttributeError` and the whole probe,
transcode and reader suite is red.

- [ ] **Step 8: Write the tests for the two rekeys**

In `tests/probe/test_verdict.py` -- which imports `replace`, `pytest`,
`MediaFacts`, `CHROME_149`, `DEFAULT_THRESHOLDS` and `derive` already, and needs
`TimingSource` added to its imports:

```python
def test_a_source_whose_timing_was_invented_is_not_analysis_clean() -> None:
    # The demultiplexer manufactured these timestamps, so every value measured
    # from them describes the invention. Reporting the file analysis-ready is how
    # a file the reader refuses gets called clean.
    facts = replace(CLEAN, timing_source="synthesized")
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    assert "unreliable_timing_metadata" in verdict.analysis_reasons
    assert verdict.analysis_transcode == "required"


@pytest.mark.parametrize("timing_source", ["absent", "synthesized"])
def test_an_unsupplied_rate_never_fires_the_variable_rate_reason(
    timing_source: TimingSource,
) -> None:
    # constant_frame_rate is a placeholder on such a source, never a measurement.
    # Firing variable_frame_rate on it would select a re-encode where a
    # timestamp-generating remux is the fix.
    facts = replace(CLEAN, timing_source=timing_source, constant_frame_rate=False)
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    assert "variable_frame_rate" not in verdict.analysis_reasons
    assert "variable_frame_rate" not in verdict.stream_reasons
```

In `tests/probe/test_duplicate.py`, which builds its facts by probing real clips
and calls `dataclasses.replace` rather than importing `replace`, follow that
module's own idiom -- probe a clip, then vary the provenance:

```python
def test_two_files_whose_timing_was_invented_compare_as_timing_unknown(
    clips: dict[str, Path],
) -> None:
    # The comparison needs timing that measures the file. Timing a demultiplexer
    # manufactured describes the invention, so two such files can agree on it
    # while being different recordings.
    probed = probe_media(clips["cfr_mp4"])
    left = dataclasses.replace(probed, timing_source="synthesized")
    right = dataclasses.replace(probed, timing_source="synthesized")
    assert compare_for_duplicate(left, right).verdict == "timing_unknown"
```

- [ ] **Step 9: Update every test that names the field**

Eight test files name `timing_measured`: four carry real expectation changes and
four are mechanical. A ninth changes without naming it, and is listed last for
that reason.

Real changes:

- `tests/transcode/test_convert.py` asserts `output_facts.timing_measured is True`
  at three sites. Each becomes `output_facts.timing_source == "presentation"`.
- `tests/probe/test_duplicate.py` is built around the boolean's default-true
  hazard: a row reconstructed from persisted columns reported true whatever was
  probed. A required `Literal` has no default, so the premise changes -- the test
  now constructs the provenance explicitly and its comment says why the hazard is
  gone.
- `tests/probe/test_raw_stream.py` asserts the field and its module docstring says
  such a file routes "never to a re-encode". Both change: the assertion to
  `timing_source == "absent"`, and the docstring to say that a raw stream carrying
  no timestamps routes to a timestamp-generating remux while a stream whose timing
  is invented routes to a re-encode.
- `tests/probe/test_identity.py` builds its identity input as
  `timing_measured=source != "none"`. It calls `timing_supplied_by_source(
  timing_source_for(source, container))` instead, so it cannot disagree with the
  probe.
- `tests/io/test_reader_recovery.py` asserts
  `derive(facts, CHROME_149, DEFAULT_THRESHOLDS).analysis_transcode is None` for
  the bare AV1 stream, in
  `test_a_source_declaring_more_frames_than_it_decodes_raises`. **That assertion
  inverts here, not in a later task.** The file's container is `obu`, which this
  task puts on the invented-timing list, so the moment `timing_source` lands the
  file classifies `synthesized`, `unreliable_timing_metadata` fires, and the
  transcode target reads `required`. Change the assertion to
  `== "required"` and rewrite the comment above it: the probe cannot see the
  packet-to-picture divergence itself, but it does see that the timing was
  invented, and routes the file on that ground; the reader's raise is the backstop
  for a source that reaches it anyway.

  This file contains no reference to `timing_measured`, which is why it is not
  found by grepping for the field. It is the only existing fixture whose container
  is on the list, so nothing else in the corpus flips.

Mechanical: `tests/probe/test_verdict.py`, `tests/transcode/test_commands.py`
(including the `TimestampLessOverrides` typed dictionary, whose
`timing_measured: bool` key becomes `timing_source: TimingSource`),
`tests/probe/test_probe.py`, `tests/probe/test_identity_probe.py`.

Three source comments also name the field and go stale:
`src/mosaic_media/probe/candidates.py`, `src/mosaic_media/probe/ffprobe.py`, and
`src/mosaic_media/probe/facts.py`. Update the wording in each.

- [ ] **Step 10: Rekey `timestamp_fps` so a synthesized source can never be written from an invented rate**

In `src/mosaic_media/transcode/commands.py`, replace:

```python
    timestamp_fps = 0.0 if facts.timing_measured else facts.declared_fps
```

with:

```python
    # Only a source carrying no timestamps at all may have them written from a
    # declared rate. On a measured file that field can be the header lie the
    # remux exists to correct, and on a source whose timing was invented it is
    # the demultiplexer's own default -- writing either in would make it the
    # file's truth.
    timestamp_fps = facts.declared_fps if facts.timing_source == "absent" else 0.0
```

This is behavior-preserving for every source that reaches it today, because the
only sources with `timing_source == "absent"` are exactly those that had
`timing_measured == False`.

The two tests that guard it are
`test_a_lying_header_is_a_copy_remux_with_regenerated_timestamps` and
`test_lying_header_source_never_sets_timestamps` in
`tests/transcode/test_commands.py`. Both assert `-fflags +genpts` for a measured
file, both must stay green **unchanged**, and neither is edited by this plan. If
either fails, the rekey changed the measured-file branch and the expression is
wrong -- stop and report rather than editing the test.

- [ ] **Step 11: Confirm the golden identity vectors did not move**

Run: `uv run pytest tests/probe/test_identity.py -v`
Expected: PASS with no change to any expected digest. If a golden vector moved,
stop and report: the hashed bytes were supposed to be untouched.

- [ ] **Step 12: Run the full suite**

Run: `heavy uv run pytest tests/`
Expected: PASS. Let it block in the foreground.

- [ ] **Step 13: Verify and commit**

Run: `uv run ruff format src/ tests/ && uv run ruff check src/ tests/ && uv run basedpyright src/ tests/`

```bash
git add -A src tests
git commit -m "Record where a file's timing came from, not merely that it had some

A demultiplexer manufactures timestamps for a format that carries none, and the
probe read the manufactured answer as a measurement. The fact now says whether
the file supplied the timing, whether those are presentation or decode
timestamps, or whether there are none."
```

---

### Task 5: Add the fixtures the verdict work is measured against

Every fixture this package needs, in one task, so the next task is about behavior
rather than about generating media. Each is asserted here against its
classification, which Task 4 already makes available.

**Files:**
- Modify: `tests/helpers/media_fixtures.py`
- Test: `tests/probe/test_timing_source.py`

**Interfaces:**
- Consumes: `MediaFacts.timing_source` from Task 4.
- Produces: session-scoped fixtures `natural_obu_clip`, `reordered_raw_h264_clip`, and `invented_timing_clips`, the last a `dict[str, Path]` keyed `"mpegvideo"`, `"yuv4mpegpipe"`, `"h263"`, `"jpeg_pipe"`. There is no fixture named for the MPEG-2 stream on its own; it is a key in that mapping.

- [ ] **Step 1: Add the fixtures**

In `tests/helpers/media_fixtures.py`, beside the other session-scoped clips.
Check `build`'s signature at the top of that file before writing these: it takes
the destination first, then ffmpeg arguments, and an optional `source` list that
replaces the default synthetic input.

```python
@pytest.fixture(scope="session")
def natural_obu_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A bare AV1 stream from an ordinary encode.

    Distinct from the frame-split stream, which carries a second defect -- more
    packets than pictures -- that would confound a test about provenance alone.
    This one has a packet per picture, so nothing but the provenance is wrong.
    """
    root = tmp_path_factory.mktemp("natural_obu")
    encoded = build(root / "av1.mp4", "-c:v", "libsvtav1", "-pix_fmt", "yuv420p")
    return build(root / "natural.obu", "-c", "copy", source=["-i", str(encoded)])


@pytest.fixture(scope="session")
def reordered_raw_h264_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A raw H.264 stream coded with reordering, carrying no timestamps.

    Copied from the committed open-GOP asset rather than encoded: a stream copy
    needs no encoder, and the ones that would produce a reordered H.264 stream
    directly are the ones this package does not name. Measured on the result:
    reordering depth 2, no packet timestamps, a declared rate of 25.
    """
    root = tmp_path_factory.mktemp("reordered_raw")
    return build(
        root / "reordered.h264",
        "-c",
        "copy",
        source=["-i", str(asset("open_gop.mp4", root / "open_gop.mp4"))],
    )


@pytest.fixture(scope="session")
def invented_timing_clips(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """One file per format whose demultiplexer manufactures timestamps.

    None of these encoders carries a copyleft obligation. Two arguments are
    load-bearing and were found by the muxer refusing the file without them: the
    uncompressed stream must be told a planar pixel format, because the muxer
    accepts only yuv444p, yuv422p, yuv420p, yuv411p and gray8 while the synthetic
    source is rgb24; and H.263 accepts only a fixed set of dimensions, so the
    frames are scaled to the smallest of them.
    """
    root = tmp_path_factory.mktemp("invented_timing")
    made: dict[str, Path] = {}
    made["mpegvideo"] = build(root / "raw.m2v", "-c:v", "mpeg2video", "-f", "mpeg2video")
    made["yuv4mpegpipe"] = build(
        root / "raw.y4m", "-c:v", "rawvideo", "-pix_fmt", "yuv420p"
    )
    made["h263"] = build(
        root / "raw.h263", "-vf", "scale=128x96", "-c:v", "h263", "-f", "h263"
    )
    made["jpeg_pipe"] = build(root / "raw.mjpeg", "-c:v", "mjpeg", "-f", "mjpeg")
    return made
```

- [ ] **Step 2: Write the classification tests**

Append to `tests/probe/test_timing_source.py`:

```python
def test_every_invented_timing_format_classifies_as_synthesized(
    invented_timing_clips: dict[str, Path],
) -> None:
    for name, path in invented_timing_clips.items():
        assert probe_media(path).timing_source == "synthesized", name


def test_an_h263_stream_is_measured_at_a_rate_the_source_never_had(
    invented_timing_clips: dict[str, Path],
) -> None:
    # The sharpest instance of why invented timing cannot be trusted even when it
    # looks uniform: the demultiplexer does not merely flatten the spacing, it
    # reports a rate the file never carried, and reports it as constant.
    facts = probe_media(invented_timing_clips["h263"])
    assert facts.fps > 29.0
    assert facts.constant_frame_rate


def test_an_mpeg2_elementary_stream_is_reordered_as_well_as_invented(
    invented_timing_clips: dict[str, Path],
) -> None:
    # A default encode holds one picture back, so this fixture carries both
    # halves of the next task's reason: invented timing and a positive reordering
    # depth. Asserted here so a change to either is caught where it is measured.
    facts = probe_media(invented_timing_clips["mpegvideo"])
    assert facts.timing_source == "synthesized"
    assert facts.coded_reordering_depth == 1


@requires_svtav1
def test_an_ordinary_bare_av1_stream_has_its_timing_invented(
    natural_obu_clip: Path,
) -> None:
    # The provenance defect on its own. One packet per picture, so the frame
    # count is right and nothing but the provenance is wrong -- which is what
    # makes this the fixture that isolates the change.
    facts = probe_media(natural_obu_clip)
    assert facts.timing_source == "synthesized"
    assert facts.frame_count == len(decode_md5s(natural_obu_clip))


def test_a_reordered_raw_stream_carries_no_timing_and_reorders(
    reordered_raw_h264_clip: Path,
) -> None:
    facts = probe_media(reordered_raw_h264_clip)
    assert facts.timing_source == "absent"
    assert facts.coded_reordering_depth == 2
    assert facts.declared_fps == pytest.approx(25.0)
```

Add one import to that module for these tests:

```python
from tests.helpers.corpus import decode_md5s
```

`requires_svtav1` is already imported by the block Task 4 created, so it needs no
new import here. It is applied because `natural_obu_clip` encodes with that codec
and a build without it must skip rather than fail. The bitstream-filter marker is
not needed -- this fixture does not split frames.

- [ ] **Step 3: Run them**

Run: `uv run pytest tests/probe/test_timing_source.py -v`
Expected: PASS. Every assertion is about behavior Task 4 already landed, so
nothing here should need an implementation step. If a fixture fails to build,
read the ffmpeg error rather than adjusting the assertion: the arguments above
were chosen against real muxer rejections.

- [ ] **Step 4: Run the full suite**

Run: `heavy uv run pytest tests/`
Expected: PASS. Let it block in the foreground.

This task edits the fixture module every suite imports, so an error there is
global rather than local to the tests added here. A task that only ran its own
file would commit a broken collection for every other suite.

- [ ] **Step 5: Verify and commit**

Run: `uv run ruff format tests/ && uv run ruff check tests/ && uv run basedpyright tests/`

```bash
git add -A tests
git commit -m "Add a fixture per format whose timing is manufactured

One file for each format the classification calls synthesized, plus a bare AV1
stream with a packet per picture and a reordered raw stream, so the verdict work
is measured against real media rather than hand-built facts."
```

---

### Task 6: Route sources a stream copy cannot time correctly to a re-encode

**Files:**
- Modify: `src/mosaic_media/probe/policy.py` (both vocabularies, the hard stream set)
- Modify: `src/mosaic_media/probe/verdict.py` (firing conditions)
- Modify: `src/mosaic_media/transcode/commands.py` (both re-encode sets)
- Modify: `tests/io/test_reader_recovery.py` (one assertion inverts)
- Test: `tests/probe/test_verdict.py`, `tests/transcode/test_commands.py`, `tests/transcode/test_convert.py`

**Interfaces:**
- Consumes: `MediaFacts.timing_source` and `MediaFacts.coded_reordering_depth` from Tasks 3 and 4; the fixtures from Task 5.
- Produces: the reason literal `"presentation_timing_requires_decode"` in both `AnalysisReason` and `StreamReason`, and in `HARD_STREAM_REASONS`.


- [ ] **Step 1: Write the failing tests**

In `tests/probe/test_verdict.py`:

```python
def test_an_invented_timing_source_cannot_be_copy_remuxed() -> None:
    # A copy carries the packets forward, and the packet-to-picture mapping is
    # exactly what no measurement over packets can establish. Only a decode can.
    facts = replace(CLEAN, timing_source="synthesized")
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    assert "presentation_timing_requires_decode" in verdict.analysis_reasons
    assert "presentation_timing_requires_decode" in verdict.stream_reasons
    assert verdict.playable is False


@pytest.mark.parametrize("timing_source", ["decode", "synthesized", "absent"])
def test_reordering_without_presentation_timestamps_requires_a_decode(
    timing_source: TimingSource,
) -> None:
    # A decoder recovers presentation order from the bitstream's picture order.
    # A copy does not decode, so it labels the pictures in the order they arrive.
    # The measured values are cleared alongside an unsupplied provenance, because
    # the probe sets them to placeholders whenever the file supplied no timing.
    # Facts mixing an unsupplied source with measured values model a state the
    # probe never mints, which this suite forbids elsewhere for the same reason.
    cleared = (
        {"fps": 0.0, "duration": 0.0, "constant_frame_rate": False}
        if timing_source == "absent"
        else {}
    )
    facts = replace(
        CLEAN, timing_source=timing_source, coded_reordering_depth=2, **cleared
    )
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    assert "presentation_timing_requires_decode" in verdict.analysis_reasons


def test_reordering_with_presentation_timestamps_is_left_alone() -> None:
    # The ordinary containerized case, which is most of the corpus. Real
    # presentation timestamps already carry the order, so firing here would
    # re-encode files that are correct.
    facts = replace(CLEAN, timing_source="presentation", coded_reordering_depth=2)
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    assert "presentation_timing_requires_decode" not in verdict.analysis_reasons
    assert "presentation_timing_requires_decode" not in verdict.stream_reasons
```

In `tests/transcode/test_commands.py`, through that module's own `command_for`
helper, which builds the facts from `CLEAN`, derives the verdict, and calls
`build_command` with its real argument order. Do not call `build_command`
directly: its signature is
`(verdict, facts, target, source, destination, *, encoding, allow_hardware=False)`,
and every test in that file goes through the helper.

```python
@pytest.mark.parametrize(
    ("target", "encoding"),
    [("analysis", ANALYSIS_ENCODING), ("playback", PLAYBACK_ENCODING)],
)
def test_a_source_needing_a_decode_takes_a_re_encode_on_both_targets(
    target: Target, encoding: EncodingParameters
) -> None:
    # Both copy remuxes reach the defect: the analysis target through the
    # timebase remux and the playback target through the container remux. A
    # reason present in only one set leaves the other derivative mistimed.
    command = command_for(target, encoding, timing_source="synthesized", container="obu")
    assert command is not None
    assert command.operation is Operation.REENCODE_AV1
```

`ANALYSIS_ENCODING` and `PLAYBACK_ENCODING` are the encoding constants; there is
no symbol named `PRESET`. Each target takes its own.

In `tests/probe/test_timing_source.py`, add the verdict assertions to the fixtures
Task 5 already classified. Keep them separate from that task's classification
tests: those measure what the probe reports, these measure what the verdict does
with it.

That module has not needed the verdict machinery until now, so add:

```python
from mosaic_media.probe.policy import CHROME_149, DEFAULT_THRESHOLDS
from mosaic_media.probe.verdict import derive
```

```python
@requires_svtav1
def test_an_ordinary_bare_av1_stream_is_routed_to_a_decode(
    natural_obu_clip: Path,
) -> None:
    # The provenance defect on its own. Task 5 pinned that this file classifies
    # as synthesized and that its frame count is right; what matters here is that
    # the verdict acts on it.
    verdict = derive(probe_media(natural_obu_clip), CHROME_149, DEFAULT_THRESHOLDS)
    assert verdict.analysis_transcode == "required"
    assert "presentation_timing_requires_decode" in verdict.analysis_reasons


def test_an_mpeg2_elementary_stream_is_routed_to_a_decode(
    invented_timing_clips: dict[str, Path],
) -> None:
    verdict = derive(
        probe_media(invented_timing_clips["mpegvideo"]), CHROME_149, DEFAULT_THRESHOLDS
    )
    assert "presentation_timing_requires_decode" in verdict.analysis_reasons
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/probe/test_verdict.py tests/probe/test_timing_source.py tests/transcode/test_commands.py -v`
Expected: FAIL. The reason literal does not exist yet, so the type checker will
also object; that is expected at this step.

- [ ] **Step 3: Add the reason to both vocabularies**

In `src/mosaic_media/probe/policy.py`, add `"presentation_timing_requires_decode"`
to `StreamReason`, to `AnalysisReason`, and to `HARD_STREAM_REASONS`. Add beside
the hard set:

```python
# Timing assigned to the wrong pictures breaks the mapping from frame index to
# time, which is what a hard reason means. Every source that fires it today also
# fires unsupported_container, so nothing in the corpus changes classification --
# which is why the membership is decided here rather than left to be noticed.
```

- [ ] **Step 4: Fire it in the verdict**

In `src/mosaic_media/probe/verdict.py`, after the unreliable-timing branch:

```python
    if facts.timing_source == "synthesized" or (
        facts.coded_reordering_depth > 0
        and facts.timing_source in ("decode", "synthesized", "absent")
    ):
        # A stream copy cannot produce correct presentation timing here. With
        # invented timestamps the packet-to-picture mapping is unknown; with
        # reordering and no presentation timestamps the order is. A decoder
        # recovers both and a copy decodes nothing.
        analysis.add("presentation_timing_requires_decode")
        stream.add("presentation_timing_requires_decode")
```

- [ ] **Step 5: Select a re-encode for it on both targets**

In `src/mosaic_media/transcode/commands.py`, add
`"presentation_timing_requires_decode"` to `_REENCODE_ANALYSIS_REASONS` and to
`_REENCODE_STREAM_REASONS`.

- [ ] **Step 6: Name the reason in the reader recovery assertion**

Task 4 already inverted
`test_a_source_declaring_more_frames_than_it_decodes_raises` from
`analysis_transcode is None` to `== "required"`, because putting `obu` on the
invented-timing list is what changed that verdict. This step only adds the reason
now carrying it:

```python
    assert "presentation_timing_requires_decode" in verdict.analysis_reasons
```

beside the existing `analysis_transcode == "required"`.

The companion `test_a_source_whose_hidden_frames_share_a_timestamp_reads_clean`
asserts the Matroska sibling verdicts clean. That is correct and stays: Matroska
is not on the list, so its provenance is `presentation` and neither reason fires.

- [ ] **Step 7: Add the end-to-end reordering test**

In `tests/transcode/test_convert.py`:

```python
@requires_svtav1
def test_a_reordered_raw_stream_comes_back_in_presentation_order(
    reordered_raw_h264_clip: Path, tmp_path: Path
) -> None:
    # The defect this reason exists for, measured end to end. A copy remux of
    # this source writes timestamps from the packet index, which is decode order,
    # so the fourth picture carries a lower timestamp than the second. The
    # acceptance re-probe cannot see it, because the timestamps are uniform and
    # complete either way -- only their assignment to pictures is wrong.
    result = transcode(
        reordered_raw_h264_clip, tmp_path / "out.mp4", "analysis", ANALYSIS_ENCODING
    )
    assert result.output_path is not None
    times = decoded_times(result.output_path)
    assert times == sorted(times)
```

Go through that module's own `transcode` helper, which takes
`(source, output, target, encoding)` and probes and derives internally. Do not
call `run_transcode` directly: its signature is
`(source, output, target, facts, verdict, *, profile, thresholds, encoding, ...)`,
so a call omitting `facts` and `verdict` raises before anything runs.

`output_path` is `Path | None`, so it is narrowed before use; every existing test
in that file does the same.

No `decoded_times` helper exists yet; add one beside that module's other ffprobe
helpers, following `_declared_average_rate`'s idiom:

```bash
ffprobe -v error -select_streams v:0 -show_entries frame=pts_time -of default=nw=1:nk=1
```

**It returns the times in the decoder's output order and never sorts them.** That
is the whole assertion: `times == sorted(times)` tests that decode order already
is presentation order, so a helper that sorted anywhere, or that read packet
timestamps instead of frame timestamps, would pass on the defective file too.
Measured on the copy-remuxed derivative of this fixture, read this way: 17
non-monotonic pairs. Read sorted: zero.

- [ ] **Step 8: Run the full suite**

Run: `heavy uv run pytest tests/`
Expected: PASS. Let it block in the foreground.

- [ ] **Step 9: Verify and commit**

Run: `uv run ruff format src/ tests/ && uv run ruff check src/ tests/ && uv run basedpyright src/ tests/`

```bash
git add -A src tests
git commit -m "Re-encode a source whose timing a stream copy cannot recover

Invented timestamps hide the packet-to-picture mapping and reordering without
presentation timestamps hides the order. A decoder recovers both; a copy decodes
nothing, so it carried the defect into the derivative and the acceptance re-probe
passed it."
```

---

### Task 7: Refuse a source that states no rate anywhere

**Files:**
- Modify: `src/mosaic_media/transcode/commands.py` (`build_command` raises)
- Modify: `src/mosaic_media/transcode/convert.py` (documented raise list)
- Modify: `tests/transcode/test_commands.py` (one contract change)
- Add: a committed asset under `tests/assets/`

**Interfaces:**
- Consumes: `TranscodeError` from Task 1, `MediaFacts.timing_source` from Task 4, the widened `declared_fps` from Task 2.
- Produces: no new symbols.

- [ ] **Step 1: The asset already exists; no new one is needed**

Task 2 committed `tests/assets/raw_no_declared_rate.hevc`, and that file is
already exactly what this refusal needs. Measured on it after Task 2 and Task 4:
no packet timestamps, so `timing_source` is `absent`; and a bitstream stating no
rate, so `declared_fps` is 0.0 once the plausibility ceiling rejects the demuxer
time base it reports. Both halves of the refusal predicate, in one committed file.

**Do not generate an H.264 equivalent.** It was the original intention and there
is no route to one. Measured: every x264 parameter tried leaves the timing block
in place, so a raw H.264 encode reports its tick rate of `50/1` for a 25 frames
per second source whatever is asked of it, and the metadata bitstream filter does
not strip it either -- `h264_metadata=tick_rate=0` applied to an already-annexb
stream still reports `50/1` and changes nothing. The parameter that works for
HEVC has no H.264 counterpart.

That is a limitation worth stating rather than hiding, because the issue this
closes was written about H.264 streams from tracking hardware. What the fixture
pins is the behavior, and the behavior is codec-independent: the refusal keys on
the timing provenance and the declared rate, never on the codec. So the rule is
pinned by real media, and the specific codec the issue observed it on is not. Say
so in the test's comment rather than leaving a reader to assume an H.264 fixture
exists somewhere.

- [ ] **Step 2: Write the failing test**

In `tests/transcode/test_commands.py`, replace
`test_timestamp_less_source_without_a_rate_keeps_the_generated_timestamps` with:

```python
@pytest.mark.parametrize(
    ("target", "encoding"),
    [("analysis", ANALYSIS_ENCODING), ("playback", PLAYBACK_ENCODING)],
)
def test_a_source_stating_no_rate_anywhere_is_refused(
    target: Target, encoding: EncodingParameters
) -> None:
    # Neither timestamps nor a rate the file states. There is nothing to write
    # and nothing to re-encode to: an output rate would be an invention exactly
    # as the muxer's own fallback was. Refusing names the reason instead.
    with pytest.raises(TranscodeError, match="states no frame rate"):
        command_for(
            target, encoding, timing_source="absent", declared_fps=0.0, container="h264"
        )
```

and add, so the refusal is bounded rather than swallowing the common case:

```python
def test_a_raw_stream_stating_a_rate_is_not_refused() -> None:
    command = command_for(
        "analysis",
        ANALYSIS_ENCODING,
        timing_source="absent",
        declared_fps=30.0,
        container="h264",
    )
    assert command is not None
```

`TranscodeError` is not currently imported in that module; add it.

In `tests/probe/test_raw_stream.py`, add the committed asset end to end. That
module currently imports only `Path`, the candidate check, the policy constants,
`probe_media` and `derive`; this test additionally needs `pytest`,
`TranscodeError`, `build_command`, `ANALYSIS_ENCODING` and the `asset` helper.

```python
def test_the_committed_rate_less_stream_is_refused(tmp_path: Path) -> None:
    # The fixture is HEVC because that is the codec a valid rate-less stream can
    # be produced for: no x264 parameter omits the timing block and the metadata
    # bitstream filter does not strip it. What is pinned here is codec-independent
    # -- the refusal keys on the timing provenance and the declared rate, never on
    # the codec -- so the rule is pinned by real media even though the H.264 case
    # that first showed it is not.
    path = asset("raw_no_declared_rate.hevc", tmp_path / "rate_less.hevc")
    facts = probe_media(path)
    assert facts.timing_source == "absent"
    assert facts.declared_fps == 0.0
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    with pytest.raises(TranscodeError, match="states no frame rate"):
        build_command(
            verdict,
            facts,
            "analysis",
            path,
            tmp_path / "out.mp4",
            encoding=ANALYSIS_ENCODING,
        )
```


- [ ] **Step 3: Run them and confirm they fail**

Run: `uv run pytest tests/transcode/test_commands.py tests/probe/test_raw_stream.py -v`
Expected: FAIL -- no exception raised, a command is returned.

- [ ] **Step 4: Raise the refusal**

In `src/mosaic_media/transcode/commands.py`, import `TranscodeError` from
`.errors` and add to `build_command`, immediately after the operation is selected
and before any argv is built:

```python
    if facts.timing_source == "absent" and facts.declared_fps <= 0.0:
        # No timestamps and no rate the file states: this source has no timing at
        # all, and neither a remux nor a re-encode can supply one without
        # inventing it. The muxer's own fallback did exactly that, at an
        # approximation nobody chose, and refusing is the honest outcome. The
        # refusal precedes every reason, including the one that would otherwise
        # select a re-encode.
        message = (
            f"{source} states no frame rate in its container or its bitstream, "
            "so no timing can be written for it without inventing one"
        )
        raise TranscodeError(message)
```

Placement note: `_resolve_output` creates nothing and the destination directory
and temporary file are made later in `run_transcode`, so the raise leaves no
partial state.

- [ ] **Step 5: Document the raise**

In `src/mosaic_media/transcode/convert.py`, `run_transcode`'s docstring
enumerates its raise conditions. Add the refusal to that list.

- [ ] **Step 6: Run the full suite**

Run: `heavy uv run pytest tests/`
Expected: PASS. Let it block in the foreground.

- [ ] **Step 7: Verify and commit**

Run: `uv run ruff format src/ tests/ && uv run ruff check src/ tests/ && uv run basedpyright src/ tests/`

```bash
git add -A src tests
git commit -m "Refuse a source that states no frame rate anywhere

Neither the container nor the bitstream carries one, so any timing written for it
is invented. The muxer's deprecated fallback invented it silently; naming the
refusal is the honest answer."
```

---

### Task 8: Raise when the decoder emits two pictures at one timestamp

Where the reader holds facts, builds no index and never seeks, neither delivery
check reaches a collapse: a missing frame widens the spacing between decoded
frames and a duplicated one closes it to zero, and only the first is a gap.

**Files:**
- Modify: `src/mosaic_media/io/reader.py` (`_check_decode_gap`, and the class docstring)
- Test: `tests/io/test_reader_recovery.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. This task is independent of the schema change and may be implemented before it if convenient.
- Produces: no new symbols.

- [ ] **Step 1: Write the failing test**

In `tests/io/test_reader_recovery.py`:

```python
class _ReaderEmittingOneRankTwice(VideoReader):
    """A reader whose decoder emits one presentation rank twice.

    The mirror of `_ReaderSkippingPresentationRanks`: there the decode falls
    behind the frame model, here it runs ahead. No file of this shape can be
    written -- the mp4 muxer refuses two packets sharing a decode timestamp
    outright -- so the decoder stands in for one.
    """

    duplicated_rank: int = 10
    _already_duplicated: bool = False
    _held: VideoFrame | None = None

    @override
    def _decode_next(self) -> VideoFrame | None:
        if self._held is not None:
            frame = self._held
            self._held = None
            return frame
        frame = super()._decode_next()
        if frame is None:
            return None
        if not self._already_duplicated and self._rank_of(frame) == self.duplicated_rank:
            self._already_duplicated = True
            self._held = frame
        return frame

    def _rank_of(self, frame: VideoFrame) -> int:
        return round(float(frame.time) * self.fps)


def test_a_decoder_emitting_one_rank_twice_raises(
    clips: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Measured before this check: 60 delivered of 60 declared with no error, and
    # 49 of the labels carrying the wrong picture. The frame model counts
    # distinct presentation timestamps and the reader advances one index per
    # decoded picture, so two pictures at one timestamp means every later index
    # names the picture one position ahead of the one the model assigns it.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    with count_packet_scans(reader_module, monkeypatch) as scans:
        with _ReaderEmittingOneRankTwice(path, facts=facts) as reader:
            with pytest.raises(MediaProbeError, match="frame 11:"):
                for _index, _frame in reader:
                    pass
        assert scans() == 0


```

Do **not** add a coarse-timescale test.
`test_a_coarse_timescale_source_reads_clean` already sweeps `QUANTIZED_RATES` with
start offsets and bounded windows, which is stronger than anything this task
would add, and it is the test that catches a collapse check placed wrongly ahead
of the spacing guards. Extend its docstring instead, to say that it now also
covers the collapse condition: those files are the ones most able to put two
pictures on one tick, so a check that fired on them would be worse than the defect
it catches.

- [ ] **Step 2: Run them and confirm the first fails**

Run: `uv run pytest tests/io/test_reader_recovery.py -k "one_rank_twice or coarse_timescale_source" -v`
Expected: the collapse test FAILS by delivering 60 frames and raising nothing;
the coarse-timescale test PASSES already.

- [ ] **Step 3: Add the collapse condition**

In `src/mosaic_media/io/reader.py`, inside `_check_decode_gap`, move the reading
of `frame.time` and the update of `_previous_decoded_time` above the two guards
that read `max_timestamp_gap_frame_periods`, and insert the equality test between
them. The frame rate guard stays where it is and keeps its comment: it is what
makes a frame's own time safe to read, because a source with no measured rate
carries frame times of `None` and two absent times compare equal.

```python
        if geometry.fps <= 0:
            return
        # Read after that condition, never before. A frame carries no time when
        # its packet carried none, and `av` types that as a float it does not
        # always hold; the sources it happens on are exactly the ones a positive
        # frame rate excludes.
        observed = float(frame.time)
        previous = self._previous_decoded_time
        self._previous_decoded_time = observed
        if previous is None:
            return
        if observed == previous:
            message = (
                f"{self._path} frame {self._target}: the decoder delivered two "
                f"pictures at {observed}, where the frame model counts one per "
                "distinct timestamp; every later index names the picture after "
                "the one it should, and it must be transcoded before it can be "
                "read per frame"
            )
            raise MediaProbeError(message)
        if facts.max_timestamp_gap_frame_periods <= 0.0:
            return
        threshold = facts.max_timestamp_gap_frame_periods + 0.5
        if threshold >= 2.0:
            return
        gap = (observed - previous) * geometry.fps
```

The read and the update move together. Moving the comparison without the
assignment would leave the previous time permanently unset on every path where
the spacing guards return, and the check would be silently dead.

- [ ] **Step 4: Extend the method docstring**

`_check_decode_gap` documents why the spacing threshold comes from the file and
why it declines above two frame periods. Add a paragraph saying the method now
covers both ends of the same measured spacing: a widened one is a frame the
decoder did not produce, and a collapsed one is a picture the frame model does not
count. Say that the condition is exact equality rather than a non-positive
spacing, because a backwards step is a different defect -- the decode order and
the model's sorted-timestamp order disagreeing, which the verdict routes to a
re-encode -- and that the collapse test sits ahead of the spacing guards because a
collapse is detectable whatever the file's own spacing.

Update the class docstring's delivery paragraph, which currently says consecutive
decoded frames are checked "for the gap a missing frame leaves", to say they are
checked for both the gap a missing frame leaves and the collapse a duplicated one
does.

- [ ] **Step 5: Run the reader suite**

Run: `uv run pytest tests/io/ -v`
Expected: PASS, including
`test_the_delivery_check_stays_silent_across_a_healthy_source`,
`test_an_unmeasured_spacing_declines_rather_than_checking`, and
`test_an_untimed_source_carries_no_frame_rate_to_reach_the_spacing_guard`. If any
of those three fails, stop and report: the placement is wrong.

- [ ] **Step 6: Run the full suite**

Run: `heavy uv run pytest tests/`
Expected: PASS. Let it block in the foreground.

- [ ] **Step 7: Verify and commit**

Run: `uv run ruff format src/ tests/ && uv run ruff check src/ tests/ && uv run basedpyright src/ tests/`

```bash
git add -A src/mosaic_media/io tests/io
git commit -m "Raise when two decoded pictures share one timestamp

The frame model counts one frame per distinct timestamp, so a decoder emitting
two pictures at one has run ahead of every index after it. The spacing check
could not see it: a missing frame widens the step and a duplicated one closes it
to zero."
```

---

### Task 9: Make every test stand-in mirror the signature it stands over

A stand-in installed over a real callable is written against the signature of the
day, and nothing connects the two afterwards. A delegating stand-in stops
measuring what it was installed to measure and raises about the argument shape
instead; a raising one meant to prove a path is never reached reports that same
argument-shape error rather than its own cause. Both point away from the defect.

Scoped to signature parity -- parameter names, kinds, and defaults. What a
stand-in asserts, returns or records is each test's own business and is not
touched.

**Files:**
- Modify: `tests/probe/test_ffprobe.py`, `tests/transcode/test_convert.py`, `tests/test_hwaccel.py`, `tests/io/test_reader_seek_landing.py`, `tests/io/test_reader_errors.py`

**Interfaces:**
- Consumes: nothing. Independent of every other task.
- Produces: no new symbols.

- [ ] **Step 1: Locate the population**

Run: `grep -rn "monkeypatch.setattr" tests/`

Each hit names the object and attribute being replaced. Read the original's
definition in `src/`, or in the standard library, and compare parameter by
parameter. Stand-ins installed another way -- a fixture that swaps an attribute, a
class whose `__call__` stands in for a function -- are found by searching for the
assignment that performs the swap; the grep finds the common case, not every case.

- [ ] **Step 2: Correct each confirmed instance**

- `tests/probe/test_ffprobe.py`: the `run_to_completion` stand-ins take
  `(_command, **_keywords)` where the original in `src/mosaic_media/ffmpeg.py`
  takes keyword-only `timeout`, `action` and `error_type`. Expand the wildcard
  into those three real parameters, with the original's defaults.
- `tests/transcode/test_convert.py`: `unprobeable_output` makes its second
  parameter required where `probe_media`'s `thresholds` has a default, so a
  single-argument call raises before reaching the stand-in's own logic. Give it
  the default. The `derive` stand-ins in the same file rename all three
  parameters, so a keyword call cannot bind; restore the original names.
- `tests/test_hwaccel.py`: the `shutil.which` stand-ins omit `mode` and `path`;
  add them with the standard library's defaults. The `subprocess.run` stand-ins
  accept a narrow subset and rename the first parameter; restore the name and
  accept the parameters the call sites actually pass.
- `tests/io/test_reader_seek_landing.py`: the `_to_stream_offset` stand-in renames
  two of three parameters.
- `tests/io/test_reader_errors.py`: the `Path.expanduser` stand-in renames its
  receiver.

Never widen a stand-in with `**_keywords` to make a call bind. A wildcard accepts
calls the original would reject, so a dropped or misspelled argument is absorbed
rather than surfaced, and the stand-in drifts further the longer it survives.

- [ ] **Step 3: Prefer a subclass override where the shape allows**

Where a stand-in replaces a method on a class the test can subclass, write it as a
subclass override instead of an attribute swap. The type checker then holds it to
the base method under `reportIncompatibleMethodOverride`, which this repository
enables, so parity is enforced permanently and in both directions: an override
adding a required parameter is rejected, and so is one left untouched when the
base grows a parameter. `tests/io/test_reader_recovery.py` uses this form already.
Nothing else does this for an attribute swap, which is mirrored by hand and drifts
again as soon as the original moves.

- [ ] **Step 4: Run the affected suites**

Run: `uv run pytest tests/probe/test_ffprobe.py tests/transcode/test_convert.py tests/test_hwaccel.py tests/io/test_reader_seek_landing.py tests/io/test_reader_errors.py -v`
Expected: PASS. Any test that now fails is a stand-in that was absorbing a real
mismatch; report it rather than restoring the wildcard.

- [ ] **Step 5: Run the full suite**

Run: `heavy uv run pytest tests/`
Expected: PASS. Let it block in the foreground.

- [ ] **Step 6: Verify and commit**

Run: `uv run ruff format tests/ && uv run ruff check tests/ && uv run basedpyright tests/`

```bash
git add -A tests
git commit -m "Make each test stand-in mirror the signature it stands over

A stand-in written against the signature of the day keeps passing until the
original moves, and then fails about the argument shape rather than about the
behavior the test was measuring."
```

---

### Task 10: Record the schema change for the backend

The backend's fact persistence is its own schema, needing a migration an operator
runs against a live database, which is why it is not done here. It already has a
tracked issue for exactly this shape --
`docs/issues/media-facts-gains-delivery-counts-and-timestamp-spacing.md` in that
repository, covering three fields the preceding work package added. This package's
fields land in the same migration and touch the same sites, so they belong in that
issue as an appended section rather than in a second document.

Measured, so the section states facts rather than predictions:

- `timing_measured` has nine source sites there: the `mediafacts` model, the
  nullable staging column on `uploadsessionfile`, `FACT_FIELDS`, the `FactRow`
  protocol, `facts_to_columns`, three lines of `columns_to_facts`, and the adopt
  clone. That is the same shape the issue already describes for `max_gop_bytes`,
  which it names as the site-finding anchor.
- **No consumer branches on an individual verdict reason.** The response schemas
  type them as `list[StreamReason]` and `list[AnalysisReason]`, imported from
  this package, and sort them through without a per-reason case; the sequence
  import metadata types them as plain string lists. A new reason literal is
  therefore additive, and the reordering issue's requirement that consumers handle
  it rather than fall through is met with no change.
- The analysis toolkit is a no-op. It never names `timing_measured`, and it
  reconstructs facts through `row_to_facts`, already converting a stale row's
  `TypeError` into an error naming the remedy: re-probe the media index. A removed
  or added field surfaces there as that message, which is the designed behavior
  for exactly this case.

**Files:**
- Modify: `/home/paul/ecodylic/mosaic_api/docs/issues/media-facts-gains-delivery-counts-and-timestamp-spacing.md`
- Modify: that repository's `docs/issues/_INDEX.md` row for it

**Interfaces:**
- Consumes: the final field set from Tasks 3, 4 and 6. Run this task last, so the section describes what landed rather than what was planned.
- Produces: nothing this repository depends on.

- [ ] **Step 1: Read the existing issue in full**

Read
`/home/paul/ecodylic/mosaic_api/docs/issues/media-facts-gains-delivery-counts-and-timestamp-spacing.md`.
Run git in that repository as
`git -C /home/paul/ecodylic/mosaic_api <command>`, never by changing directory.
Note that it is on `main` there, one commit ahead of its own remote and unpushed.

- [ ] **Step 2: Append a section for this package's fields**

Add a section covering: `timing_measured` leaving the field list and
`timing_source` and `coded_reordering_depth` joining it; the `mediafacts` column
types (a short string for the provenance, a NOT NULL integer for the depth) and
their nullable counterparts on `uploadsessionfile`; and that the adopt clone
copies both.

State the backfill position, which is the same one the existing document takes
and reaches the same conclusion for a different reason. The boolean does not map
to the literal: true covers `presentation`, `decode` and `synthesized`, and the
new verdict reason turns on telling the first two apart, so any fill produces a
wrong verdict for some rows rather than an incomplete one. The depth admits no
backfill either, since zero is a legitimate measurement meaning the bitstream
reorders nothing. As with the three fields already described, the revision
refuses a populated table rather than backfilling.

Record what does **not** change, since the existing document's Scope section is
organized that way: no wire schema field, because reasons are typed lists sorted
through rather than enumerated; no `videosequence` column, by the rule already
written beside those columns; and no identity remapping, because the hashed bytes
are unchanged for every format except the two whose provenance moved, and no row
holds one of those.

- [ ] **Step 3: Note the identity consequence explicitly**

The provenance boolean is hashed into `video_uuid`, so a file whose
classification changed re-mints. That is exactly two formats, and no stored row
holds either, so no identity in any corpus moves. Say so in the section rather
than leaving a reader to work out whether a re-probe changes a key: the existing
document already promises that a file probed before and re-probed after is
indistinguishable in the store, and that promise still holds.

- [ ] **Step 4: Update the issue's index row**

The row's one-line description covers three fields. Extend it to cover the full
set, keeping the status `active`.

- [ ] **Step 5: Commit in that repository**

Commit on `main` there, alongside the existing unpushed commit. Do not push.

```bash
git -C /home/paul/ecodylic/mosaic_api add docs/issues/media-facts-gains-delivery-counts-and-timestamp-spacing.md docs/issues/_INDEX.md
git -C /home/paul/ecodylic/mosaic_api commit -m "Record the provenance and reordering fields in the media facts migration"
```

---

## Definition of done

- The full suite passes: `heavy uv run pytest tests/`.
- `uv run ruff format src/ tests/ scripts/`, `uv run ruff check src/ tests/ scripts/`
  and `uv run basedpyright src/ tests/ scripts/` are all clean, with no suppression
  added anywhere.
- The performance gate is unaffected and is not re-run as part of this plan; the
  reader change adds no packet scan, and
  `test_injected_facts_sequential_read_runs_no_packet_scan` still passes.
- No golden identity vector moved.
- Each of the four issues this branch closes -- the three the spec names, plus
  the test stand-in drift Task 9 closes -- has its "What would close it" bullets
  met, verified bullet by bullet before archiving.
- The two tests pinning `-fflags +genpts` for a lying header pass **unedited**.
