# Reader conversion through the filter graph

`VideoReader._emit` converts every decoded frame with
`frame.to_ndarray(format=...)`, or `frame.reformat(...)` when the reader was
opened with a resize. Both build a fresh libswscale scaling context per frame.
This spec replaces them with one libavfilter graph, built once per reader, that
carries rotation, scaling, and pixel format together.

The change is faster on every read path and, on the resized color path, more
correct: the reader's resized bgr24 output currently differs from ffmpeg's
`-vf scale` by up to 76 levels per channel, and through the graph it matches
exactly.

## Why the current path is slow

The cost is repeated per-frame setup, not pixel work, and not decode.

Decode is already frame-threaded. `_ensure_container` sets
`stream.thread_type = "AUTO"` (`src/mosaic_media/io/reader.py:137`); the codec
context reports nine threads on an eight-core machine, and forcing `NONE` costs
roughly 3.7x. There is no headroom in decode threading.

Threading the conversion does not help either. A reused reformatter with
`threads=0` (automatic) is indistinguishable from one with threads unset, and
`threads=8` is slower than both -- thread setup exceeds the work. Setting
`Graph.threads` has no effect on the conversion filters at all. What follows
therefore claims no parallelism; it removes repeated setup.

Decode and conversion together, 900 frames at 1920x1080 to bgr24, one
interleaved run. Every row decodes identically and differs only in how it
converts, so the meaningful quantity is the difference between rows, not the
totals -- the totals include decode and are not a conversion cost:

| Conversion | Decode plus conversion | Saved against the current path |
| --- | --- | --- |
| `frame.to_ndarray(format="bgr24")` (current) | 1440 ms | - |
| one reused `VideoReformatter` | 1230 ms | 210 ms |
| persistent filter graph | 1120 ms | 320 ms |

Holding one reformatter across the read recovers about two thirds of what the
graph recovers, without changing a single filter or kernel. That is what
identifies repeated setup as the cost: scaling-context construction, plus the
destination frame each `reformat` call allocates, which the graph also avoids.

## The graph

One graph per reader, built once and reused for every frame:

```
buffer
  -> transpose cclock | hflip, vflip | transpose clock    when rotated
  -> scale <width>:<height>:flags=bicubic                 when resizing
  -> format bgr24 | gray
  -> buffersink
```

Stage order is fixed and load-bearing. The transpose runs first so a
quarter-turn source is emitted in displayed orientation; the scale runs after it
so a requested resize wins over the rotation dimension swap and the output is
exactly the requested `(width, height)`. That is the ordering `_emit` already
implements across its two calls, preserved here in one chain.

The rotation stage keeps the existing filter mapping (`_ROTATION_FILTERS`,
`reader.py:45-49`). The unsupported-rotation failure is unchanged and stays
where it already lives, in `_ensure_ready` (`reader.py:186-188`), which rejects
an unmapped rotation before any graph is built.

### The graph is built lazily, on first conversion

Build the graph on the first `_emit` call, not in `_ensure_ready`.

`_ensure_ready` does not open the container when `facts` are injected: it takes
the facts branch and returns without ever calling `_ensure_container`
(`reader.py:167-172`). The graph's buffer source is templated from the stream,
so a graph built there would have no stream to template from. Every consumer
path and the whole bench suite inject facts, so building eagerly breaks all of
them. By the first `_emit` the container is necessarily open, because a frame
has been decoded from it.

### Conversion happens in the graph, never again after it

The graph already emits the reader's output pixel format, so the array is taken
from the graph's output frame with `to_ndarray()` and **no** `format` argument.
Passing `format=` there would ask libswscale for a bgr24-to-bgr24 conversion and
rebuild a per-frame context, restoring the exact cost this change removes. The
output would be pixel-identical and every test would still pass, so nothing
catches this but the reader of the code.

### Graph construction failure

`graph.configure()` raises `av.error.FFmpegError`. Wrap it and raise
`MediaProbeError`, as every other libav failure in this module does. Today only
rotated readers build a graph; after this change every reader does, so an
unwrapped libav error would escape from a path that currently cannot produce
one.

## Output contiguity

The reader returns C-contiguous arrays on every path today -- plain, grayscale,
resized, and rotated. Graph output does not preserve that: libavfilter pads the
line size, so transposed and scaled frames come back non-contiguous, and
contiguity of unscaled output depends on whether the width happens to align.

The graph result is therefore passed through `numpy.ascontiguousarray`. It is a
no-op returning the same array when the stride already matches, and copies when
it does not. Both outcomes satisfy the frame contract; the measurements below
include the copy wherever one is taken.

The remaining two guarantees hold through the graph without help. Consecutive
reads do not alias: the array keeps the frame's buffer referenced, so
libavfilter's pool cannot recycle a buffer still held by a caller, and mutating
one returned frame leaves the next intact. Returned arrays remain writable, so
consumers can still draw overlays onto them.

## Seek behavior

The graph is not rebuilt on seek. A backward container seek pushes frames whose
presentation timestamps precede ones already sent; the buffer source accepts
them, emits correct frames, and logs nothing. Positioned reads, sparse reads,
and the reader's keyframe landing verification are unaffected -- none of them
touch conversion.

## Measured effect

Conditions: eight cores, one benchmark process at a time under the machine's
serialization lock, stages interleaved within each round with the order rotated
per round, medians taken over the rounds preceding measurable thermal drift.
That discipline is required rather than tidy: on this machine an unchanged
workload drifted from 1729 ms to 2707 ms as the processor fell from 4100 MHz to
2305 MHz within a single run, a 1.57x spread.

Ratios are quoted only within a single interleaved run; figures from different
runs are not comparable, including the two tables in this spec. Whole-reader
sequential full decode, 900 frames at 1920x1080, resize to 480x270, measured
twice independently:

| Read path | Gain, first run | Gain, second run |
| --- | --- | --- |
| bgr24 | 1.4x | 1.4x |
| grayscale | 1.9x | 2.3x |
| bgr24, resized | 2.2x | 2.6x |
| grayscale, resized | 2.6x | 2.3x |

One significant decimal is the precision the machine supports; the two runs
agree on direction and rough magnitude and disagree by more than that on the
individual paths. Resize gains most despite paying the contiguity copy, because
a 1920x1080-to-480x270 bicubic context is the most expensive one to build and
the current path builds it once per frame.

## Correctness: the resize chroma error

The current resized bgr24 output is wrong against ffmpeg, and the graph fixes
it. Worst per-channel difference from the `-vf scale` goldens, over all 48
frames of the `corpus_gop12` fixture, 320x240 scaled to 160x120:

| Source | Format | Current | Graph |
| --- | --- | --- | --- |
| upright | bgr24 | 57 | **0** |
| rotated 90 | bgr24 | 76 | **0** |
| rotated 180 | bgr24 | 58 | **0** |
| rotated 270 | bgr24 | 75 | **0** |
| any | gray | 1 | 1 |

### The cause is chroma-plane scaling, not color conversion

Measured stage by stage on the same fixture:

| Operation | Worst against ffmpeg |
| --- | --- |
| `reformat(format="bgr24")`, no scaling | 0 |
| `reformat(..., format="yuv420p", BICUBIC)` | luma 0, chroma 20 and 27 |
| filter `scale=160:120:flags=bicubic` | luma 0, chroma 0 and 0 |

Three things follow. The color conversion is exact in both libraries, so there
is no version skew between PyAV's bundled libav and the system ffmpeg. The
divergence appears in the scale step alone, before any color conversion, and is
confined to the chroma planes -- the luma bicubic kernel is bit-identical. And
the filter reproduces ffmpeg exactly where `reformat` does not, because ffmpeg's
own `-vf scale` is that filter.

So `VideoFrame.reformat` and the `scale` filter configure libswscale
differently for chroma-plane scaling. Only the filter matches ffmpeg. Splitting
`reformat` into a scale call and a convert call is not an equivalent fix and
does not remove the error; the graph is what removes it. The chroma error is
then amplified to 57-76 when it crosses into BGR, which is why the grayscale
path -- which uses only the luma plane -- was already correct and is unchanged
at 1.

The existing rationale in `tests/io/test_reader_resize_content.py:14-19`
attributes the difference to "bundled-versus-system swscale major skew ...
regardless of the kernel". That is refuted by the first row above and must be
replaced with the chroma-scaling explanation, not merely reworded.

## Test changes

- Resize content tolerances tighten. Grayscale stays at 2 against a measured 1.
  The bgr24 tolerance drops from 64 to 2 against a measured 0 -- small enough to
  reject the structural error by a wide margin, nonzero because chroma scaling
  is arithmetic whose result could differ by a rounding step between library
  builds, unlike the exact pixel permutation the rotation goldens assert. This
  is a deliberate accepted exposure: the old tolerance was slack enough to
  absorb an ffmpeg or PyAV version bump and the new one is not, so a future bump
  may surface here first. That is the intended tradeoff -- a tolerance wide
  enough to hide a 76-level error is not a test.
- The corrected chroma-scaling explanation replaces the version-skew rationale
  in that module's docstring.
- Resize content gains rotated cases. The rotation-plus-resize composition is
  currently untested and is where the largest error sat; cover it against the
  autorotated `-vf scale` goldens for all three rotations.
- The frame contract test (`tests/io/test_reader_frame_contract.py`) extends to
  the resized, grayscale, and rotated paths. It asserts writability,
  C-contiguity, and non-aliasing only on the plain path today, while contiguity
  is exactly what the graph puts at risk.
- The carve-tier rationale in `tests/bench/test_sequential_decode.py:27-32`
  describes the mechanism this change deletes: it explains the tier by "av
  reformats yuv into a bgr24 frame plus an ndarray copy out of it" and
  distinguishes the rotation variant as the one going "through the libav
  transpose filter graph". After this change there is no `reformat` and every
  variant goes through a graph. Correct that comment in the same branch.
- These suites must stay green unchanged, and are the evidence for the claims
  above: the rotation goldens
  (`tests/io/test_reader_rotation.py`, bit-exactness of the composed rotation),
  the grayscale framemd5 golden
  (`tests/io/test_reader_strided.py:33`, which already pins non-resized
  grayscale output frame by frame), and the seek, sparse, and variable-rate
  suites, which are the evidence that graph reuse across a backward seek is
  sound.

## Performance gate

The implementation does not change any gate threshold. The gate compares the
reader against OpenCV, and a reader that got faster raises every ratio,
including past the carve tier that exists for the conversion cost this change
removes. Re-deriving those thresholds is a separate decision made on reported
numbers, not an implementation step -- the gate's own failure message forbids
tuning thresholds, and that applies to a passing gate as much as a failing one.
The branch reports the measured post-change ratios for every gated workload.

Two gate-adjacent facts. The OpenCV baseline itself did not move: measured
interleaved on the same clip, opencv-python 4.13.0.92 and 5.0.0.93 decode at the
same speed, within-pair median ratio 1.013. And `MultiVideoReader` pays graph
construction once per segment, because `_open_segment`
(`src/mosaic_media/io/multi.py:185-193`) closes and recreates the `VideoReader`
for each segment; that construction measured about 0.66 ms at 1080p, comparable
to a single frame's conversion. The seek, sparse, and multi-video gates read few
frames per open, so the branch confirms those gates by measurement rather than
by the argument that a faster reader cannot regress a ratio.

## Out of scope

- `hwaccel` stays the documented no-op it is today (`reader.py:99-104`). Decode
  remains software.
- `MultiVideoReader` needs no code change; it reads through `VideoReader` and
  inherits the graph.
- The probe, the transcode, and the writer are untouched.
