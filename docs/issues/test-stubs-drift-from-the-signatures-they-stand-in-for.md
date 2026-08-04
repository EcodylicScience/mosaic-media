# Test stubs drift from the signatures they stand in for

## Problem

A test that replaces a real callable installs a stand-in written against the
signature of the day. The original then grows a parameter, renames one, or
changes a default, and nothing connects the two: the type checker sees a
function assigned to an attribute, and the suite sees whatever the stub does.
The stub keeps passing until production code happens to use the part that moved.

Two failure modes, both silent until they are not:

- **A delegating stub stops measuring what it was installed to measure.** It is
  installed to count calls, record arguments, or forward to the real function.
  When the caller passes a parameter the stub does not carry, the call raises
  `TypeError` from inside a test that never intended to exercise argument
  binding. The failure names the argument shape rather than the behavior under
  test, and the measurement the stub existed to take is not taken at all.
- **A raising stub reports the wrong cause.** It is installed to assert that a
  path is never reached, and it raises `AssertionError` with a message saying
  so. When the caller passes a parameter the stub does not carry, Python raises
  `TypeError` at the call boundary instead, so the test still fails but reports
  an argument-shape problem where the real finding is that a forbidden path ran.

Both are worse than a plain break, because the message points away from the
defect. A wildcard (`**_keywords`) does not fix either one: it makes the stub
accept calls the real function would reject, so a dropped or misspelled
argument is absorbed rather than surfaced, and the stub diverges further from
the original the longer it survives.

## Scope

Signature parity only: parameter names, kinds (positional-only, keyword-only),
and defaults. What a stub asserts, returns, or records is out of scope and is
each test's own business.

The population is every callable installed over another callable in the test
suite -- through `monkeypatch.setattr`, a fixture that swaps an attribute, a
class whose `__call__` stands in for a function, or a subclass overriding a
production method -- whose parameters do not mirror the original's. It is not a
fixed list: the set grows whenever a stub is added and changes whenever an
original's signature does, which is exactly why the closing criteria below are
stated as a property rather than as a checklist of sites.

Locate the population with:

```
grep -rn "monkeypatch.setattr" tests/
```

Each hit names the object and attribute being replaced. Read the original's
`def` in `src/`, or the standard library's, and compare parameter by parameter.
Stand-ins installed some other way are found by searching the same way for the
fixture or assignment that performs the swap; the `grep` finds the common case,
not every case.

Confirmed instances at the time of writing, each verified against its original:

- `tests/probe/test_ffprobe.py` -- the `run_to_completion` stand-ins take
  `(_command, **_keywords)` where `mosaic_media/ffmpeg.py`'s original takes
  keyword-only `timeout`, `action` and `error_type`. The wildcard accepts calls
  the original rejects.
- `tests/transcode/test_convert.py` -- `unprobeable_output` makes its second
  parameter required where `probe_media`'s `thresholds` has a default, so a
  single-argument call raises rather than reaching the stub's own logic. The
  `derive` stand-ins in the same file rename all three parameters, so a
  keyword call cannot bind.
- `tests/test_hwaccel.py` -- the `shutil.which` stand-ins omit `mode` and
  `path`, and `shutil.which` is replaced process-wide. The `subprocess.run`
  stand-ins accept a narrow subset of that function's parameters and rename its
  first.
- `tests/io/test_reader_seek_landing.py` -- the `_to_stream_offset` stand-in
  renames two of three parameters.
- `tests/io/test_reader_errors.py` -- the `Path.expanduser` stand-in renames its
  receiver.

The subclass form carries no instances above, and that absence is a property of
the form rather than a hole in the list. A subclass overriding a production
method stands in for it exactly as an attribute swap does, but the type checker
compares the override against the base under
`reportIncompatibleMethodOverride`, which this repository's basedpyright
configuration enables. Parity is then enforced permanently and at no
maintenance cost, in both directions: an override that adds a required
parameter is rejected, and so is an override left untouched when the base
method grows one. The `@override` decorator declares the intent but is not what
produces the check. `tests/io/test_reader_recovery.py` uses this form.

## Why deferred

The stubs in `tests/io/test_multi.py` were corrected where a change to the code
they stand over exposed them, which is the pattern that keeps finding these one
at a time. The rest are not reachable by any current caller, so nothing in the
suite is wrong today; the work reaches the ffprobe, convert, hwaccel,
seek-landing and reader-error suites, carries no behavior change and builds no
shared mechanism, and does not belong inside a change whose subject is the
reader or the transcode.

Deferring it has a cost worth stating: each of these is found by tripping over
it, and the tripping is what this document exists to stop.

## What would close it

- Every stand-in mirrors its original's signature: the same parameter names,
  the same positional-only and keyword-only kinds, and the same defaults.
- Every `**_keywords` wildcard is expanded into the original's real parameters,
  so a dropped or misspelled argument fails at the call rather than being
  absorbed.
- A delegating stand-in forwards every parameter it accepts, so the behavior it
  measures is the behavior the original would have produced.
- Where a stand-in can be a subclass override rather than an attribute swap, it
  is written that way. The checker then holds it to the method it stands over,
  which nothing does for any other form: those are mirrored by hand and drift
  again as soon as the original moves.
