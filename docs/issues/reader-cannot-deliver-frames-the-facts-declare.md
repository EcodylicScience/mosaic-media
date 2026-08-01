# The reader cannot deliver frames the probe says exist

## Problem

`VideoReader` fails to return frames at positions well inside the range
`MediaFacts.frame_count` declares. Two distinct behaviors, both reproducible:

**Seek raises.** `seek()` fails with

```
MediaProbeError: failed to seek <source> to frame <n>: [Errno 1] Operation not permitted
```

for a frame index the facts admit. It is position-dependent rather than
file-dependent: on two sources frame 0 fails while the midpoint succeeds, and on
two others the midpoint fails while frame 0 succeeds. So a file is not simply
unreadable -- part of it is reachable and part is not.

**Seek succeeds and the read returns nothing.** On a fifth source, seeking to
the last two frames succeeds and `read()` then returns `(False, None)` with no
error. `frame_count` is 2000 and index 1999 is accepted by the range check --
`seek(2000)` correctly raises `IndexError: frame index 2000 out of range
[0, 2000)` -- so the count promises two frames more than the file yields.

Both matter more than a failed read of one frame, because a caller sampling *n*
positions across a file has no way to distinguish "this frame does not exist"
from "this file is unreadable". The facts are the contract, and the reader is
not honoring it.

## Reproducing without the files

The sources are not redistributable. Each row is a probe fingerprint; the
`frame 0` and `midpoint` columns record what `seek()` then `read()` did.

| Container | Codec | Dimensions | Frames | Declared | fps | start_time | frame 0 | midpoint |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `mov,mp4,m4a,3gp,3g2,mj2` | `h264` | 1280 x 960 | 594 | 594 | 15.0000001265 | 0 | **raises** | ok |
| `avi` | `h264` | 2048 x 2080 | 286256 | 286256 | 30.000300003 | 0 | **raises** | ok |
| `asf` | `wmv2` | 256 x 256 | 449 | **0** | 33.3333333333 | 0.06 | ok | **raises** |
| `asf` | `wmv2` | 1600 x 1200 | 3399 | **0** | 10.0016188612 | 0.297 | ok | **raises** |
| `avi` | `indeo5` | 360 x 288 | 2000 | 2000 | 25 | 0 | ok | ok, but the last two frames read empty |

Two properties worth noticing in that table, neither yet shown to be the cause:
the two files whose seek raises at frame 0 are the only ones whose codec is
`h264`, and the two whose seek raises at the midpoint are the only ones whose
container declares a frame count of 0 and a non-zero `start_time`.

The reproducer is five lines against any matching file:

```python
facts = probe_media(path)
with VideoReader(path, facts=facts) as reader:
    for target in (0, facts.frame_count // 2, facts.frame_count - 1):
        reader.seek(target)
        ok, frame = reader.read()
```

## Scope

`VideoReader.seek` and `read` in `src/mosaic_media/io/`, and the relationship
between `MediaFacts.frame_count` and what the reader can actually address. Not
the probe's frame counting on its own: for four of the five sources the count
agrees with the declared count, and the failure is in reaching a frame rather
than in counting one.

Whether the two groupings above are the real dividing lines is unverified -- five
files is enough to see a pattern and not enough to trust it. Anyone picking this
up should widen the sample before designing a fix around either.

The `Operation not permitted` text comes from the underlying library rather than
from this package, so part of the work is establishing what condition it is
actually reporting; the errno is unlikely to mean what it says.

## Why deferred

Found while extracting frames in bulk from a varied corpus for the first time.
Diagnosing a seek path across four container/codec pairings is its own piece of
work, separate from the run that surfaced it.

## What would close it

For any source the probe accepts, every frame index inside `frame_count` either
returns a frame or raises an error naming the reason. A test covering the
fingerprints above asserts that, and asserts specifically that a source whose
last frames do not decode reports a `frame_count` that excludes them, so a
caller sampling the declared range is never handed an index that cannot work.
