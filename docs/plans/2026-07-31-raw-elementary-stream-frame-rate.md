# Raw elementary stream frame rate implementation plan

> **Execution:** work task by task, one implementer and one reviewer carried
> across every task, with a review and a fix loop to convergence between them.
> A single fresh reviewer takes the whole branch at the end. Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** Give a raw H.264 elementary stream the frame rate it declares, so its
probe stops reporting a rate ffmpeg invented and its copy remuxes stop relying on
a deprecated mp4 muxer fallback.

**Architecture:** The probe reads the sequence parameter set's tick rate from
`r_frame_rate`, halves it (H.264 counts two ticks per frame), and uses it as
`declared_fps` for streams whose packets carry no timestamps. The transcode
command builder turns that rate into a `setts` bitstream filter expression on
every copy remux of such a source, so packets reach the muxer already
timestamped. Both changes are gated on `timing_measured` being `False`, which is
what keeps them away from timestamped files.

**Tech Stack:** Python 3.12, ffprobe and ffmpeg on `PATH`, pytest, basedpyright,
ruff, uv.

**Design reference:** `docs/specs/2026-07-31-raw-elementary-stream-frame-rate.md`.
Read it before starting. Where this plan and the spec disagree, stop and ask
rather than choosing.

## Global constraints

- Python floor is 3.12. Never raise it.
- Every module touched here is core: standard library only. Do not import numpy,
  av, typer, or cv2 into `src/mosaic_media/probe/` or
  `src/mosaic_media/transcode/`.
- basedpyright runs strict (`typeCheckingMode = "all"`) with ruff, over `src/`
  and `tests/` alike. No `# noqa`, no `# pyright: ignore`, no `# type: ignore`.
- No `typing.Any`, no `typing.Optional` (write `X | None`), no `typing.cast`.
- A parameter or field ranging over a closed set of strings is a named `Literal`
  alias, never bare `str`. This applies to test helpers too.
- No multi-line f-strings; assign to a variable first. Parenthesized adjacent
  string literals are assigned to a variable, never passed bare into a call.
- ASCII only in code and comments. American spelling. No unnecessary
  abbreviations in identifiers.
- Never write `libx264`, `libx264rgb`, `libx265`, `libx262`, or `libxvid` as a
  string literal in any `.py` file under `src/` or `tests/`.
  `tests/test_encoder_guard.py` parses Python string literals and will fail.
  Markdown and shell blocks are not scanned.
- Reuse the helpers a test module already has rather than adding a second one
  that does the same work. `tests/transcode/test_commands.py` has `command_for`
  and `arg_after`; every command assertion goes through them.
- Commit messages are plain English, present tense, no `feat:`/`fix:` prefixes,
  no `Co-Authored-By` trailers.
- Do not touch `README.md`. Separate work covers it after this branch.
- Full suite runs are serialized on an otherwise idle machine. Never run two at
  once.

## Before starting

Create the feature branch from `main`:

```bash
git -C /home/paul/ecodylic/mosaic_media checkout -b raw-elementary-stream-frame-rate
```

---

### Task 1: The probe reads the rate the elementary stream declares

Shipped in `2a55ec7`.

**Files:**
- Modify: `src/mosaic_media/probe/ffprobe.py`
- Modify: `tests/probe/test_identity.py` (`GOLDEN_HEADER` gains the new field)
- Test: `tests/probe/test_ffprobe.py`

**Interfaces produced, as built:**

```python
def parse_fraction(text: str) -> float: ...

def elementary_stream_fps(
    stream: dict[str, object], container: str, codec_name: str
) -> float: ...
```

and `Header.elementary_stream_fps: float`, read by Task 2.

- [x] **Step 1: A helper reading the rate the bitstream states**

`elementary_stream_fps` returns `r_frame_rate / 2` when the format name and the
codec name are both `h264` and the halved value is positive and at most
`1000.0`; `0.0` otherwise. H.264 counts two ticks per frame, so the sequence
parameter set's tick rate is twice the frame rate.

The upper bound rejects the demuxer time base that a sequence parameter set
carrying no timing produces, measured at `1200000/1`. There is no lower bound.
One shipped as `1.0` and was removed in `0f398fe`: a stream coded at 0.5 fps
states `1/1`, halves to `0.5`, and was being discarded, so the bound bit exactly
at the rates a timelapse or long-observation recording uses. The surviving
comparison against zero is not a judgment about which rates are real -- it keeps
`0.0` meaning absent, which is the field's convention.

The format-name condition is what makes the field honest. `r_frame_rate` means
the container's own frame rate for a containerized stream, so halving it would
report half the truth -- measured `12.5` on a 25 fps mp4. Gating at the source
rather than at the caller means the field never holds half a real rate and no
reader has to honor a precondition. The codec-name condition is redundant
because only the raw H.264 demuxer answers to the format name `h264`; it is kept
deliberately, stating the precondition at the point of the halving and becoming
load-bearing again if anyone widens the format condition.

- [x] **Step 2: The fraction parser stops raising on an absent value**

`parse_fraction` tested only the numerator against `_ABSENT`, so `"N/A"`
partitioned into numerator `"N"` and denominator `"A"` and reached `float("A")`,
raising `ValueError`. It now tests the whole text first. The exposure covered
the pre-existing `avg_frame_rate` read too, so this closes a latent crash on
every caller.

It carries no leading underscore because the test module imports it, and
`reportPrivateUsage` is file-scoped. The name is `parse_fraction` rather than
`fraction` because `TranscodeProgress.fraction` already means a completion ratio
in this package.

- [x] **Step 3: `Header` gains the field**

`read_header` lifts both the format name and the codec name to locals above the
`return`, reads each once, and passes them to the helper.

- [x] **Step 4: The second `Header` construction**

`GOLDEN_HEADER` in `tests/probe/test_identity.py` gains
`elementary_stream_fps=0.0`. The golden identity vectors are unchanged, because
`content_digest_input` enumerates the fields it hashes explicitly and this field
is not among them.

- [x] **Step 5: Tests**

Twelve tests in `tests/probe/test_ffprobe.py`: the `"N/A"` fraction; an integer
rate; a fractional rate; a sub-one rate (`1/1` derives `0.5`, the case that
retired the lower bound); the demuxer time base `1200000/1` rejected by the
upper bound; an absent rate; a missing key; a zero denominator; a raw HEVC
stream rejected by the format condition; a containerized stream at helper level;
and two `read_header` assertions pinning the wiring against the committed corpus
-- `raw.h264` derives `30.0`, `cfr.mp4` reads `0.0`.

- [x] **Step 6: Verified**

`uv run pytest tests/probe/ -q` green, `uv run basedpyright src/ tests/` at 0
errors, `uv run ruff check src/ tests/` clean. Every containerized clip in the
corpus reads `0.0` and only `raw.h264` carries a derived rate.

---

### Task 2: The probe reports that rate for a stream with no packet timestamps

Shipped in `1b94ec7`.

**Files:**
- Modify: `src/mosaic_media/probe/probe.py`
- Modify: `src/mosaic_media/probe/facts.py`
- Test: `tests/probe/test_raw_stream.py`

**Interfaces:**
- Consumes: `Header.elementary_stream_fps` from Task 1.
- Produces: `MediaFacts.declared_fps` carrying the elementary stream rate when
  `timing_measured` is `False`. Task 3 reads it.

- [x] **Step 1: Write the failing tests**

Add to `tests/probe/test_raw_stream.py`:

```python
def test_raw_stream_declares_the_rate_its_bitstream_states(
    clips: dict[str, Path],
) -> None:
    # raw.h264 is 30 fps content. The h264 demuxer's avg_frame_rate answers 25
    # for every raw stream regardless of content, so 25 here means the demuxer
    # default survived.
    facts = probe_media(clips["raw_h264"])
    assert facts.timing_measured is False
    assert facts.declared_fps == 30.0


def test_container_stream_still_declares_its_average_rate(
    clips: dict[str, Path],
) -> None:
    facts = probe_media(clips["cfr_mp4"])
    assert facts.timing_measured is True
    assert facts.declared_fps == 25.0
```

- [x] **Step 2: Run the tests**

```bash
uv run pytest tests/probe/test_raw_stream.py -k declares -v
```

Expected: `test_raw_stream_declares_the_rate_its_bitstream_states` FAILS with
`assert 25.0 == 30.0`. `test_container_stream_still_declares_its_average_rate`
is a regression guard and PASSES already; it must keep passing.

- [x] **Step 3: Select the rate in probe_media**

In `src/mosaic_media/probe/probe.py`, the `if source == "none":` branch sets the
placeholder values. Add one assignment to each side.

In the `if source == "none":` branch, after `timing_measured = False`:

```python
        # avg_frame_rate here is the h264 demuxer's fixed default, read from
        # nothing in the file. The rate the bitstream itself states is the only
        # honest answer, and 0.0 when it states none.
        declared_fps = header.elementary_stream_fps
```

In the `else:` branch, alongside the other measured assignments:

```python
        declared_fps = header.declared_fps
```

Then change the `MediaFacts(...)` construction from `declared_fps=header.declared_fps,`
to:

```python
        declared_fps=declared_fps,
```

- [x] **Step 4: Rewrite the MediaFacts docstring**

In `src/mosaic_media/probe/facts.py`, replace this paragraph verbatim:

```
    `declared_*` are the header's claims, retained only to be compared against
    measurement. `declared_fps` is `avg_frame_rate`, which is what OpenCV reads;
    `r_frame_rate` is neither the average nor an upper bound and is not stored.
```

with:

```
    `declared_*` are the header's claims, retained only to be compared against
    measurement. `declared_fps` is `avg_frame_rate`, which is what OpenCV reads,
    for every stream whose packets carry timestamps. For one whose packets carry
    none, `avg_frame_rate` is a demuxer default read from nothing in the file,
    and `declared_fps` instead carries the rate the elementary stream states in
    its own bitstream, or 0.0 when it states none. `timing_measured` is what
    tells the two apart.
```

- [x] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/probe/ -q
```

Expected: all pass.

- [x] **Step 6: Type-check and lint**

```bash
uv run ruff format src/mosaic_media/probe/ tests/probe/
uv run ruff check src/mosaic_media/probe/ tests/probe/
uv run basedpyright src/mosaic_media/probe/ tests/probe/
```

Expected: all checks passed, 0 errors.

- [x] **Step 7: Commit**

```bash
git add src/mosaic_media/probe/probe.py src/mosaic_media/probe/facts.py tests/probe/test_raw_stream.py
git commit -m "Report the bitstream's own rate for a stream with no packet timestamps"
```

---

### Task 3: Every copy remux of a timestamp-less source sets its own timestamps

Shipped in `82eb948`. The rate decision was collapsed to one call site and the
decode-order precondition recorded in `0f398fe`.

**Files:**
- Modify: `src/mosaic_media/transcode/commands.py`
- Test: `tests/transcode/test_commands.py`

**Interfaces:**
- Consumes: `MediaFacts.timing_measured` and `MediaFacts.declared_fps` from
  Task 2; `command_for` and `arg_after`, already in `test_commands.py`.
- Produces: the argv forms Task 5 asserts end to end.

- [x] **Step 1: The copy remux builder accepts a rate**

`_copy_remux_argv` gained `timestamp_fps: float = 0.0`. A positive value emits
`-bsf:v setts=ts=N/<rate>/TB` after `-c copy`, rendered at six decimals; zero
emits nothing. setts computes each timestamp from the packet index at the given
rate, so the packets reach the muxer already timestamped and its deprecated
synthesis fallback is never entered. A stream whose sequence parameter set
carries no timing states no rate, so nothing is set and the fallback still runs
for it -- that case loses its timestamps outright if the fallback is ever
removed, which is a visible failure rather than a silently invented rate.

- [x] **Step 2: The rate is gated on unmeasured timing, not on the rate itself**

`build_command` computes `timestamp_fps = 0.0 if facts.timing_measured else
facts.declared_fps` once, above the operation branches.

The gate is on `timing_measured` because `REMUX_TIMEBASE` is not the raw-stream
operation. `_select_operation` picks it for any `unreliable_timing_metadata`,
and `verdict.py` raises that reason from two places: a stream with no packet
timestamps, and `_timing_metadata_lies`, a timestamped container whose header
rate disagrees with its measured rate. On the second, `declared_fps` is by
definition the lie the remux exists to correct. A gate written on
`declared_fps > 0.0` alone would write that lie into the timestamps as fact,
and it would have corrupted precisely the files this operation repairs while
every then-existing test still passed, because they assert that the reason
clears rather than what the timestamps say.

- [x] **Step 3: One call site decides the rate**

The `REMUX_TIMEBASE` branch shipped as a ternary choosing between two
`_copy_remux_argv` calls, which tested `timestamp_fps > 0.0` a second time when
the builder already tested it. `0f398fe` collapsed it to a single call whose
`input_flags` is `()` when a rate is present and `("-fflags", "+genpts")` when
it is not. The argv is byte-identical to the two-call form on both branches.

- [x] **Step 4: The trailing branch documents the real mechanism**

`REMUX_FASTSTART` and `REMUX_CONTAINER` share the copy-remux argv and receive
`timestamp_fps` through the same trailing `else`. `REMUX_FASTSTART` is
unreachable for a timestamp-less source because `moov_at_start` returns `None`
for a stream whose first box is not `ftyp`, and the reason fires only on
`False`. The comment states that mechanism rather than the weaker claim that a
container implies timestamps, which nothing verifies.

- [x] **Step 5: The decode-order precondition is recorded**

`setts` numbers packets as they arrive, which is decode order, and that equals
presentation order only for a bitstream coded without frame reordering. A raw
stream carrying B-frames therefore receives presentation times shuffled against
its pictures, and the acceptance re-probe cannot detect it: the values are
uniform, complete, and start at zero, so the output measures as constant-rate
and correct while only their assignment to pictures is wrong.

This is not a regression -- the muxer fallback did the same and worse -- and it
was not fixed here, because correcting it means routing such a source to a
re-encode, which costs a schema change and a new verdict reason. It is tracked
separately. `0f398fe` records the precondition in `_copy_remux_argv`, where the
command is built, so no later reader takes the exactness claim as unconditional.

- [x] **Step 6: Tests**

Six tests in `tests/transcode/test_commands.py`, over a `TIMESTAMP_LESS`
override bundle that clears `fps`, `duration`, and `constant_frame_rate`
alongside the flag, because `probe_media` never mints facts that mix
`timing_measured=False` with measured values.

The bundle is typed by a `TimestampLessOverrides` TypedDict rather than
`dict[str, object]`. Against an open key type the checker cannot rule out that
unpacking the bundle supplies `command_for`'s keyword-only `allow_hardware`, and
the call fails on `object` not being assignable to `bool` -- four errors, one
per call site. The TypedDict gives the unpack exact keys and resolves it without
suppression, without widening `command_for`, and without a second facts factory.

Three of the six failed before the change and three were regression guards that
passed throughout. `test_lying_header_source_never_sets_timestamps` is the one
that discriminates between the two possible gates: its facts carry
`timing_measured=True` with `declared_fps=1000.0`, so a `declared_fps`-only gate
would emit `setts=ts=N/1000.000000/TB` and fail both its assertions.

- [x] **Step 7: Verified**

`uv run pytest tests/transcode/ -q` green, `uv run basedpyright src/ tests/` at
0 errors, `uv run ruff check src/ tests/` clean.

---

### Task 4: Commit a fractional-rate clip

Shipped in `4773c03`. The fixtures module docstring and the distinct-uuids entry
followed in `c801af4`; the encoder paragraph was corrected again in `0f398fe`.

**Files:**
- Create: `tests/assets/raw_fractional_rate.h264`
- Modify: `tests/assets/README.md`
- Modify: `tests/helpers/media_fixtures.py`
- Modify: `tests/probe/test_identity_probe.py`

**Interfaces:**
- Produces: `clips["raw_fractional_rate_h264"]`, read by Task 5.

- [x] **Step 1: The clip**

26433 bytes, generated by the `raw.h264` recipe with `rate=30000/1001`. It
probes to `r_frame_rate=60000/1001` and 60 packets, and through the package to
`timing_measured=False`, `declared_fps=29.97002997002997`, `frame_count=60`.

`30000/1001` is the case the integer-rate clips do not exercise: the rate must
survive a float round trip exactly, from `r_frame_rate / 2` through a `float`
field and back out into the argv at six decimals as `29.970030`.

- [x] **Step 2: Registered in the fixtures**

`"raw_fractional_rate.h264"` is the last member of the `AssetName` alias, and
`made["raw_fractional_rate_h264"]` sits beside `made["raw_h264"]` in the `clips`
fixture.

- [x] **Step 3: The recipe is recorded**

`tests/assets/README.md` carries the generating command beside the other
recipes, with the reason the clip exists.

- [x] **Step 4: The encoder listing is corrected**

The README claimed every H.264 encoder besides `libx264` and `libx264rgb` is a
hardware wrapper, and concluded there is no way to produce H.264 without a GPU.
The premise was false: `libopenh264` is a software H.264 encoder under a non-GPL
license, and the deployment image carries it (`--enable-libopenh264` and
`--enable-version3`, with no `--enable-gpl`).

The conclusion survives for a reason the README did not give: the Ubuntu system
FFmpeg the suite runs against on both development machines is built without
`libopenh264`, verified locally as `6.1.1-3ubuntu5` with no such flag and no
such encoder listed. The paragraph now states that no *software* H.264 encoder
is available on both, so a machine without a suitable GPU cannot produce these
clips -- the qualifier matters, because the hardware wrappers are themselves
non-GPL and present on both. `0f398fe` restored it after the first rewrite
dropped it and left the paragraph contradicting its own acknowledgment of those
wrappers.

The "Regenerating" section was narrowed in the same way: *these recipes* need
`--enable-gpl` because they name a GPL encoder, which is not the same as the
general claim that the deployment image cannot regenerate H.264 at all.

- [x] **Step 5: The fixtures module stops giving a second answer**

`tests/helpers/media_fixtures.py` repeated the same false inference the README
shed -- from "no native encoder" plus "libx264 is GPL" to "an LGPL build cannot
encode H.264". `c801af4` replaced it with a one-sentence statement that no H.264
encoder is available on both builds, and a pointer to the README for the listing
and to `tests/test_encoder_guard.py` for the rule. Removing the duplication
rather than restating the corrected paragraph is what stops the two from
diverging again.

- [x] **Step 6: The clip joins the distinct-uuids test**

`c801af4` added `"raw_fractional_rate_h264"` as the eighth entry in
`test_genuinely_distinct_fixtures_have_distinct_uuids`. It is the corpus's
strongest collision probe: one command produced it and `raw.h264`, differing
only in `rate=`, so they share encoder settings, GOP, geometry, and packet
count. If `content_digest` ever stopped covering the bytes that differ, that is
the pair that would collide first. Measured, the two mint unrelated identity
values.

- [x] **Step 7: Verified**

Probe suite green, encoder guard green, 0 basedpyright errors.

---

### Task 5: Prove the remux end to end

Shipped in `36835ed`. The log-level helper was made independent of the base
argv's length in `82e536c`.

**Files:**
- Modify: `tests/transcode/test_convert.py`

**Interfaces:**
- Consumes: everything from Tasks 1 through 4.

- [x] **Step 1: Five end-to-end tests**

Three read the output's declared rate back with ffprobe: the analysis remux and
the playback rewrap of `raw.h264` both declare exactly `30/1`, and the
fractional clip declares exactly `30000/1001`.

None of them asserts on measured `fps`, deliberately. `probe_media` reads packet
times from ffprobe's six-decimal `pts_time`, so a remux whose timestamps are
exactly `N * 40000` ticks at a `1/1200000` time base -- perfectly uniform and
exactly 30 fps -- still measures `29.9999949`. An equality assertion there would
fail for a reason unrelated to this work. The declared rate is what this branch
changes and what it pins.

The other two cover the muxer's deprecation notice: one asserts the notice is
absent from the built command, and one strips `-bsf:v` from that same command
and asserts it reappears.

- [x] **Step 2: The deprecation check is proved capable of failing**

The second of that pair is a negative control. Both arms were run by hand: with
the filter, ffmpeg writes nothing; without it, it writes `Timestamps are unset
in a packet for stream 0. This is deprecated ...`. Without that demonstration
the passing assertion would be no evidence at all.

It also guards the gate itself. `assert "-bsf:v" in command.argv` precedes the
index lookup, so if Task 3's gate ever stopped emitting the filter this test
fails by name rather than raising `ValueError` from `tuple.index`.

- [x] **Step 3: The log level is replaced, not prepended**

The built argv opens with the runner's own `-v error`, which hides the notice.
Prepending a level does not work: ffmpeg takes the last occurrence of
`-v`/`-loglevel`, so a prepended flag is overridden by the one already there and
the check silently passes against unmodified code.

`_warnings_of` shipped splicing at `argv[5:]`, which was correct but encoded
`_BASE`'s length; a flag inserted before the last base element would have been
dropped while `-y` was duplicated, invisibly. `82e536c` replaced the level in
place instead, via `replaced[replaced.index("-v") + 1] = "warning"`. That is
independent of the base's length and contents, raises loudly if `-v` ever
disappears, and cannot collide with `-bsf:v`, which is a different string.
`_BASE` is not imported: it is private, and the typing rules would force
renaming it public in production for a test's convenience.

- [x] **Step 4: A comment the change invalidated**

`test_transcode_progress_is_indeterminate_for_a_timestampless_source` explained
its expectation as "a copy remux of packets that reach the muxer without
timestamps is the case where it computes none of them". After Task 3 the packets
do arrive timestamped, and the test passes for a different reason:
`_progress_from_block` gates on `duration > 0`, and a raw source's duration
stays 0.0 because it is unmeasurable. The comment now attributes the absent
fraction to the source duration alone and says explicitly that the remux does
set the output's timestamps. Its assertions are unchanged.

- [x] **Step 5: Verified**

`uv run pytest tests/transcode/ -q` green at 48 tests, ruff clean, 0
basedpyright errors. The pre-existing
`test_raw_h264_remuxes_into_measured_timing` still passes, so the remux still
yields `timing_measured=True`, `constant_frame_rate=True`, and the source's
frame count.

---

### Task 6: Correct the issue's index row

Shipped in `4bbf715`, extended in `82e536c`.

**Files:**
- Modify: `docs/issues/_INDEX.md`

The spec and the plan were already tracked, with their `_INDEX.md` rows, from
the commit that started implementation; they are not committed again here. Only
the issue's row changes.

- [x] **Step 1: The row records both falsified claims**

`docs/issues/raw-stream-remux-relies-on-a-deprecated-muxer-fallback.md` leaves
git tracking when it is archived, so its `_INDEX.md` row is the only thing that
keeps it discoverable and is where a correction has to live. The document is
wrong twice.

It attributed the muxer fallback to FFmpeg 7.0 dropping demuxer-side `-fflags
+genpts`. Measured, the notice fires and the output is byte-identical on 6.1.1,
7.1 and 8.1 alike, so there is no version boundary and the fallback always did
the work.

It also states the remuxed file "carries real 25 fps timestamps". It never did,
and the rate was not 25: the fallback declared `1000000/33333`, about 30.0003,
before this branch, and the remux declares exactly `30/1` after it. The 25 most
likely came from a fixture no longer in the corpus.

The row's status stays `active` and its last-tracked hash stays `-`. The
archiving step flips the status and fills the hash; doing either here would
claim the document is archived while it is still tracked.

- [x] **Step 2: Verified**

The table still parses: every row, including the header and separator, carries
four cells, and `git diff --numstat` shows one line changed.

---

## Notes for whoever archives this work

Archiving is not a task above because a second piece of work follows on this
branch before the merge. When both are done, one atomic commit: `git rm --cached`
the spec, the plan, and the issue; rename them on disk to `.implemented.md`,
`.implemented.md`, and `.closed.md`; prepend the closed disclaimer to the issue;
flip all three index rows to their archived status with the last-tracked hash set
to `git rev-parse HEAD` taken before that commit; and commit the removals and the
index edits together.
