# Context managers annotate the concrete class rather than `Self`

## Problem

All three context managers in the io layer annotate `__enter__` with a quoted
concrete class name instead of `typing.Self`:

- `src/mosaic_media/io/reader.py`: `def __enter__(self) -> "VideoReader":`
- `src/mosaic_media/io/multi.py`: `def __enter__(self) -> "MultiVideoReader":`
- `src/mosaic_media/io/writer.py`: `def __enter__(self) -> "FFmpegVideoWriter":`

A subclass therefore loses its own type inside a `with` block. The bound name
narrows to the base class, so any attribute the subclass adds is invisible to
the type checker and the code fails `reportAttributeAccessIssue` under this
repository's strict configuration, with no suppression permitted.

This is not hypothetical. It has already imposed a cost once: a test needing to
observe decoder state subclasses `VideoReader` in
`tests/io/test_reader_recovery.py`, and had to abandon `with` for an explicit
`try`/`finally` because the annotation hid the subclass's own method. The
workaround carries a comment explaining why, which is the shape of a defect
being paid for rather than fixed.

`VideoReader` and `MultiVideoReader` are exported from `mosaic_media.io` and
both take injected state, so a consumer subclassing one to observe or adapt
behavior is a reasonable thing to do rather than an exotic one. The annotation
is wrong whether or not anyone outside this repository has hit it yet.

## Scope

Three annotations, one line each, plus a `typing.Self` import in each module.
`Self` requires Python 3.11 and this package's floor is 3.12, so no floor change
and no new dependency is involved.

Widening a return type from the concrete class to `Self` breaks no existing
caller: every current use binds the base class itself, for which `Self` resolves
to exactly what is annotated today.

Not in scope: `__exit__`, whose `None` return is correct; any other annotation
in these modules; and the `try`/`finally` in
`tests/io/test_reader_recovery.py`, which is correct as written and may be left
alone or restored to `with` when this is fixed.

## Why deferred

It surfaced during a task whose subject is frame delivery, and whose diff is
already the largest in its plan. Context-manager annotations have nothing to do
with that contract, and this repository's conventions permit relaxing a
do-not-touch boundary only narrowly, for exactly the wiring a change needs. The
`try`/`finally` works, so the change did not need it.

The finding is also worth recording rather than rediscovering: the first two
sites were found by hand and the third only by searching for the pattern, so a
future fix that starts from memory would likely miss one.

## What would close it

- All three `__enter__` methods return `Self`, imported from `typing`.
- `uv run basedpyright src/ tests/ scripts/` is clean.
- A test subclasses one of the three, adds a method, and uses it through a
  `with` block without a suppression -- proving the annotation change actually
  restores subclass typing rather than merely passing the existing suite.
- The `try`/`finally` in `tests/io/test_reader_recovery.py` is either restored
  to `with` or left with its comment updated, so it no longer describes a
  constraint that has been removed.
