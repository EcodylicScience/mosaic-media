# Duplicated ffmpeg subprocess runners across core modules

## Problem

Four core modules each carry their own run-ffmpeg-to-completion helper with
the same shape (build argv, `subprocess.run` with capture and timeout, map
FileNotFoundError / TimeoutExpired / nonzero exit to a domain error with
stderr detail):

- `probe/ffprobe.py::_run` (raises `MediaProbeError`)
- `thumbnail/extract.py` and `thumbnail/downscale.py` (each inline, raising
  `MediaProbeError`)
- `transcode/convert.py::_run_ffmpeg` (raises `TranscodeError`)

`hwaccel.py` has a related but distinct contract (returns bool, never
raises) and the `io/` reader and writer are streaming `Popen` lifecycles,
not run-to-completion -- those three are justified keeps.

## Scope

Affected: the four run-to-completion sites above, all in the stdlib-only
core layer. A shared leaf (for example `mosaic_media/_ffmpeg.py`) would hold
one runner parameterized by timeout, action description, and error type.

Not affected: `hwaccel.py` (different contract), `io/reader.py` and
`io/writer.py` (streaming pipes), and test helpers (`tests/helpers` already
consolidated on `media_fixtures.build`).

## Why deferred

`probe/` and `thumbnail/` are verbatim copies of `mosaic_api/media_probe/`
during the duplication window, and the extraction acceptance criterion is
that the copied modules and their tests match the originals apart from
import paths, keeping per-file diffs trivial while both copies exist.
Absorbing their runners into a shared module would break that diffability
for a modest deduplication win. The window closes when `mosaic_api` and
`mosaic` are rewired onto this package and the `mosaic_api/media_probe/`
copy is deleted.

## What would close it

After the consumer migration lands: a core leaf module owning one
run-to-completion helper; `probe/ffprobe.py`, both `thumbnail/` modules,
and `transcode/convert.py` calling it with their own timeouts and error
types; no behavior change (existing probe, thumbnail, and transcode tests
pass unmodified); the import-direction guard still green (the leaf imports
only the standard library).
