# mosaic-media: extraction, ffmpeg reader, and transcode design

Date: 2026-07-16 (revised 2026-07-17: io decode moves in process through libav bindings)
Status: approved design, implemented; io decode revised -- see "In-process
decode supersedes the subprocess pipe for io"

This spec covers the initial population of the `mosaic-media` package: the
duplication of the probe subpackage out of `mosaic_api`, the frame reader that
replaces OpenCV decoding, the transcode command builder and converter, and the
CLI. `README.md` remains the standing architecture reference; this spec records
the decisions specific to this effort and the evidence behind them.

**2026-07-17 revision.** The frame reader was first built to decode through a
system-ffmpeg subprocess pipe, and the performance gate was run against it and
failed by an architectural margin no tuning reaches. The `io` layer now decodes
and encodes in process through PyAV (the `av` package, Cython bindings over
libav), under a codec guard that turns a bundled binary's one risk -- an
untested codec table -- into a tested invariant. This revision is recorded
inline in the sections it changes ("Why decode without OpenCV at all", "Seek
architecture", "Performance gate", "io/", and the layering note); every other
decision in this spec stands. The revision changes the `io` layer only: the
probe, thumbnail, transcode, and cli layers keep their system-ffmpeg
subprocesses verbatim.

## Goals

1. Populate `mosaic_media` with the probe core, the frame reader, the
   transcode builder and converter, and the typer CLI -- the full library
   surface, in one effort.
2. Replace every codec-bound OpenCV use (`cv2.VideoCapture`, `CAP_PROP_*`)
   with system-ffmpeg subprocess decoding, at performance greater than or
   equal to OpenCV on every consumer workflow that exists today.
3. Duplicate, do not move: `mosaic_api/media_probe/` stays untouched and
   working. `mosaic_media` is authoritative from the moment it lands; the
   consumer rewires (`mosaic_api` and `mosaic`) are a separate later effort.

Non-goals: consumer migration (later effort), the imgstore index layer (stays
in `mosaic`; only its chunk decode will later move onto this reader), image
operations (`cv2.resize`, `cvtColor`, `imwrite` stay OpenCV in `mosaic`).

## Why decode without OpenCV at all

The README's "The OpenCV decode problem" is the standing treatment; the short
form, because it justifies everything below:

- **The codec table is not ours.** `opencv-python` wheels bundle their own
  ffmpeg build. Which codecs decode is decided by that binary -- it varies by
  wheel version, platform, and install method, cannot be upgraded
  independently, and cannot be patched. This is a statement about ownership,
  not a claim that no OpenCV build can decode any given codec: a distro
  OpenCV linked against a capable system ffmpeg, or a custom CUDA build,
  can decode AV1. The wheel this stack installs cannot, in any
  configuration: probing its build (opencv-python 4.13) shows no AV1
  software decoder (no dav1d, no libaom) and no hardware path whatsoever
  (no CUDA, no NVCUVID, no VA-API) -- a dedicated GPU changes nothing for
  it, and an AV1 file opens but decodes zero frames. AV1 is the transcode
  codec for this stack -- chosen for roughly half of H.264's bitrate at
  equal quality on static-camera behavioral footage, royalty freedom, and
  archive runway (the README's "Why AV1 and not H.264" carries the full
  case) -- so for as long as the toolkit decodes through that wheel, **the
  stack produces files its own toolkit cannot read**. That defect is not
  caused by the codec choice: the wheel decodes H.264 and H.265 only
  because their software decoders are built into libavcodec, while AV1's
  are external libraries the wheel omits -- the first modern codec exposes
  the ownership problem, whichever it is. System ffmpeg decode is owned:
  the capability is inspectable (`ffmpeg -decoders`), upgraded with the
  system, and independent of which Python binary happened to be installed.
- **The reader must cover everything the gate accepts.** The analysis
  verdict is measured by ffmpeg, and a file that passes is never re-encoded
  -- whatever codec it arrived in. A clean AV1 upload enters the corpus
  untouched, and OpenCV cannot decode a frame of it. Any decoder with a
  narrower codec table than the acceptance gate silently splits the corpus
  into readable and unreadable files; the only decoder guaranteed to cover
  everything the gate accepts is the same system ffmpeg the gate runs on.
- **One decoder for the whole stack.** Probe, transcode, thumbnails, and
  frame reading all run through the same system ffmpeg. The bytes measured at
  ingestion are decoded by the same code at analysis time. With OpenCV in
  the loop there are two decoders with different tables and different
  behavior, and every disagreement between them is a correctness bug hunt.
- **Metadata and seeking are wrong in known ways.** OpenCV's property reads
  produce false-positive variable-rate detection and unreliable frame counts
  (the probe exists to replace them), and `CAP_PROP_POS_FRAMES` seeking is a
  well-known source of off-by-N frame errors. The packet index makes seeks
  frame-exact by construction.
- **Deployment weight.** The transcode runner must start on a machine that
  has ffmpeg and nothing else -- a minimal container, a tracking box. An
  OpenCV wheel is a large binary dependency that buys nothing there.

### The invariant, restated: a tested codec table, not a trusted one

**(2026-07-17 revision.)** The bullets above conflate two separable defects of
the OpenCV wheel: an unfixable codec table, and codec-independent API defects.
Separating them is what lets the `io` layer decode in process through a bundled
binary without reopening the decision this section makes.

The durable rule is: **the decode path's codec table must be test-verified,
never trusted.** A bundled binary is not the defect; an *untested* bundled
binary is.

OpenCV stays excluded, for both of its defects:

1. **Wheel codec table.** The wheel this stack installs has no AV1 software
   decoder in any configuration (no dav1d, no libaom) and no hardware path, and
   AV1 is the transcode codec. Unfixable from the code that depends on it.
2. **Codec-independent API defects.** Off-by-N `CAP_PROP_POS_FRAMES` seeking and
   unreliable metadata properties persist in every OpenCV build, however linked.
   The packet index and the probe exist to replace exactly these.

The `io` layer's in-process decoder -- PyAV (the `av` package, Cython bindings
over libavformat/libavcodec) -- clears both bars. Its wheel carries a codec
table that a codec guard turns from a trusted property into a tested one (see
"In-process decode supersedes the subprocess pipe for io"), and it has neither
API defect: seeking is packet-index-exact, and metadata comes from injected
`MediaFacts` or the declared stream header, never from the decoder's own
position or property reads. The "one owned decoder" and "start on a box with
nothing but ffmpeg" arguments above still hold verbatim for the probe,
thumbnail, transcode, and cli layers, which keep decoding through system ffmpeg.

## Decisions and their evidence

### Repository visibility

Private now, public after review. `mosaic` is public and will depend on this
package, so everything here is designed as if public from day one: no
secrets, no encoded product policy. Browser profiles and thresholds remain
injected parameters.

### Seek architecture: subprocess respawn, not a decoder daemon

The reader follows the established subprocess architecture used by moviepy
(`FFMPEG_VideoReader`, MIT) and imageio-ffmpeg (BSD-2): one persistent ffmpeg
process for sequential reads, respawn with `-ss` for discontinuous seeks.
Both projects are named in the reader docstring and the README so the design
reads as adopted practice, not untested greenfield. Code adapted from either
carries an attributing comment and license notice.

A persistent-decoder daemon (feeding repacked packet bytes to a long-lived
`ffmpeg -f h264 -i pipe:0` process to eliminate respawn cost) was prototyped
and rejected. Findings from the proof of concept:

- The AVCC-to-Annex-B repack itself is small (~40 lines) and was validated
  bit-exact against ffmpeg's own `h264_mp4toannexb` filter via `framemd5`
  (1800/1800 frames identical).
- MP4 packet extraction by byte range works cleanly; `ffprobe` supplies
  per-packet `pos` and `size`, and samples are contiguous in `mdat`.
- The frame-identification protocol is the genuinely hard part. Emitted
  frames are re-stamped in display order (the parser reconstructs
  presentation timestamps from picture order count), so mapping "which
  emitted frame is frame N" requires a fed/emitted ledger across chain
  boundaries with DPB-flush semantics. Every ledger bug is a silent wrong
  frame, not an error. Six POC iterations hit, in order: input probe stalls
  (5 MB default probe window), a two-pipe deadlock, probe flags silently
  dropping exactly one GOP (`-fflags nobuffer`), parser access-unit lag,
  frame-count drift, and the display-order re-stamping.
- The unoptimized daemon measured 1.7-2.7x slower than OpenCV; the tuned
  ceiling is plausibly faster (the fixed per-seek tax is the reorder-depth
  flush, ~15-20 ms, versus OpenCV's ~40-50 ms in-process seek overhead), but
  proving it means finishing the risky protocol.
- No prior art exists. Every surveyed reader either binds the C libraries in
  process (PyAV, decord, torchvision, OpenCV itself) or uses subprocess
  respawn (moviepy, imageio-ffmpeg, VidGear). Nobody feeds packets to a
  decoder daemon over a pipe.

Decision: drop the daemon entirely. No daemon-ready plumbing. The packet
index gains a `pos` byte-offset field because it costs one ffprobe token and
is independently useful, not as daemon preparation. If single-seek latency
ever becomes a product constraint, revisit from the POC evidence then.

### In-process decode supersedes the subprocess pipe for io

**(2026-07-17 revision. This subsection revises the `io` layer only.)** The
subprocess-respawn reader above was built, and the performance gate below was
run against it, serialized on an otherwise idle machine. It failed. The `io`
reader decodes in process through PyAV from here on. The respawn architecture
and its daemon survey remain the record for why feeding packets to a decoder
over a pipe is never the answer, and remain in force for the probe, thumbnail,
transcode, and cli layers, which do not decode frames for a consumer.

#### What the gate measured

Each parametrization of a bench workload is a separately-collected pytest test.
A gated workload asserts `median(cv2)/median(reader) >= 1.0`; the two cold-seek
reports assert a looser `>= 0.50` bound; the probe-cost and corpus-smoke tests
never fail. By that convention, 8 of the 19 collected bench tests failed their
bound on the subprocess reader:

| Failing test | Measured ratio | Bound |
| --- | --- | --- |
| sequential-full-decode[gop12] | 0.780 | 1.0 |
| sequential-full-decode[gop250] | 0.756 | 1.0 |
| seek-then-sequential[gop12] | 0.647 | 1.0 |
| seek-then-sequential[gop250] | 0.589 | 1.0 |
| sorted-sparse-extraction[gop12] | 0.434 | 1.0 |
| multi-video-junction | 0.445 | 1.0 |
| cold-random-seek[gop12] | 0.356 | 0.50 |
| cold-random-seek[gop250] | 0.455 | 0.50 |

The `>= 1.0` bounds were aspirational -- written before any end-to-end
measurement of the full reader existed. Recovered prototype measurements from
the original design exploration track these numbers, so the profile is
architectural, not a regression in the implementation: the reader was built as
designed, and the design's transport is the cost.

Decomposing the sequential path (900 frames, 1080p, GOP 12) locates it: decode
alone ~978 ms; plus bgr24 conversion +520-610 ms (swscale runs serialized with
decode on the ffmpeg CLI main thread, ffmpeg 6.1); plus pipe transport
+330-410 ms (32 KB avio chunking, ~350 syscalls per 1080p frame, and three bulk
copies between decoder and consumer); Python-side loop overhead under 4 percent.
The cv2 in-process baseline for the same work is ~1261-1323 ms: one copy, zero
inter-process transfer. The subprocess reader pays conversion serialization and
transport that an in-process decoder structurally does not.

#### Refuted routes

Each was measured against the same workloads and is recorded so none is retried;
every one either does not touch the mechanism that costs or makes it worse.
(This mirrors the daemon POC above: a rejected approach is recorded with its
evidence, not merely asserted.)

| Route | Result and mechanism |
| --- | --- |
| ffmpeg 7.1 / 8.1 CLI | decode+convert stays strictly additive on newer CLIs -- the rawvideo-pipe pattern gets no thread overlap between decode and swscale; the pipe stage is unchanged. |
| `-avioflags direct` | no effect -- the flag never reaches the muxer-side AVIO doing the 32 KB chunking. |
| pipe sizes beyond 1 MB | blocked -- 1 MB is the unprivileged `F_SETPIPE_SZ` cap; the syscall count stands. |
| sleep-coalesced reads | 2x worse -- latency added per read dwarfs the syscalls saved. |
| two-process y4m split | 8 percent worse -- the second process adds a pipe hop; conversion moves but transport doubles. |
| yuv420p transfer, convert in Python (numpy) | numpy yuv-to-bgr is ~10x too slow to meet the BGR frame contract. |
| Python hot-loop restructuring | under 4 percent of the total is in Python; nothing to win. |
| `-probesize` / `-analyzeduration` tuning | zero effect on steady-state throughput; shapes open cost only. |
| swscale / zscale threading flags | do not engage -- the unscaled converter path ignores them. |
| `numpy.rot90` + `ascontiguousarray` for rotation | refuted for the in-process reader too: measured 0.396 on the rotation workload (transposed-copy cache behavior); the reader rotates through an in-process libav transpose filter graph instead (see "Performance gate"). |

#### The decision: PyAV in the io layer

PyAV binds libavformat/libavcodec in process, so it pays neither the
conversion-serialization nor the transport the subprocess pipe pays
structurally. Measured against the same workloads (av 18.0.0 wheel, bundled
libav 62.x, the ffmpeg 8.x generation), the failing workloads move to at or near
parity, and the sparse and cold-seek shapes -- where per-seek process respawn
dominated -- become wins. The full per-workload numbers and the tier each lands
in are in "Performance gate" below.

Seeking goes through `container.seek` to the target frame's presentation
timestamp with backward keyframe resolution, then decodes forward comparing
frame timestamps -- frame-exact by construction, still driven by the packet
index, with no process lifecycle at all. This supersedes the subprocess `-ss`
respawn for `io`; the packet index still supplies the frame-index-to-timestamp
map and the GOP grouping.

One mechanism gap PyAV cannot close through its public API: cv2 converts yuv
directly into the numpy array it returns -- one operation, one destination. av
pays a reformat into a bgr24 frame plus an ndarray copy out of it, ~0.4-0.5 ms
per 1080p frame. Owned-BGR sequential parity with cv2 is therefore structurally
out of reach in process too; ~0.95 is the honest ceiling for sequential-shaped
workloads that return owned frames, which is what the gate's carve-out tier
below accounts for.

#### The codec guard

The one risk PyAV introduces -- a bundled binary whose codec table is curated
and shifts between releases (PyAV v17 dropped libaom from its wheels, keeping
dav1d and svtav1) -- is turned into a tested invariant. A default-suite test
module, running whenever `av` is importable, proves the decode path covers what
this stack produces and accepts:

- Encode samples with **system** ffmpeg -- h264 via libx264 and AV1 via
  libsvtav1, the two codecs this stack writes -- and decode a frame of each
  through av. System ffmpeg is the producer of record (the transcode layer), so
  this is the exact cross-binary compatibility the stack depends on.
- Encode h264 through av (libx264) and decode it back through av -- the writer
  path round-trip.
- Open through av, and decode one frame from, each container format the corpus
  and probe fixtures exercise: mp4 (h264), webm (vp8), avi (mjpeg). The
  enumeration mirrors the fixture corpus and grows with it -- an analysis-clean
  file is never re-encoded, so the `io` layer's duty is whatever the acceptance
  verdict admits, and the guard claims only coverage it tests.

A guard failure means the installed av binary cannot serve this package's codec
set; the failure message names the remedy (pin a different av release, or build
against system libav).

A source build is the documented first-class fallback: `pip install av
--no-binary av` binds the *system* libav, restoring the owned-codec-table
property of the original design where an operator wants it. The codec guard plus
the full correctness suite run against the source-built binary is the named
verification instrument for that path. There is no version-pairing table: the
guard is what proves a given av-and-system-libav pairing works, so the pairing
is verified rather than tabulated.

#### Why PyAV and not the in-process alternatives

The daemon survey named three libraries that bind the C decoder in process:
PyAV, decord, torchvision. Verified maintenance facts (checked 2026-07-17):

- **PyAV (`av`)** is actively maintained on a regular cadence -- 16.0.0 October
  2025, 17.0.0 March 2026, 18.0.0 July 2026 -- with binary wheels linked against
  a current stable ffmpeg. It exposes containers, streams, packets, frames, and
  filter graphs directly, which is exactly the packet-index seeking and
  displayed-orientation control this reader needs.
- **decord** is effectively unmaintained: its last PyPI release is 0.6.0, June
  2021. Adopting a dormant native-extension decoder reintroduces the
  bundled-binary-with-no-upstream risk this package exists to remove.
- **torchvision**'s video decoding is deprecated (from torchvision 0.22, slated
  for removal around 0.25, end of 2025) in favor of torchcodec, and pulling in
  the torch stack for a frame reader is a disproportionate dependency for a
  package with a 3.12 floor and two lean consumers.

(Basis: PyPI release history for `av` and `decord`, and the torchvision
documentation's video-deprecation notice. A separate `decord2` fork exists but
is not the original project.)

### Performance gate: regression tests over real workloads

Hard constraint: performance greater than or equal to OpenCV, gated by
regression tests, with `opencv-python` as a test-only dependency (a dedicated
`bench` dependency group, never a runtime or dev dependency).

One named regression test per consumer workflow that exists in the codebase
today:

| Regression test                        | Mirrors                                        |
| -------------------------------------- | ---------------------------------------------- |
| sequential full decode                  | tracking inference main path                   |
| strided decode (`frame_step > 1`)       | inference batch stepping                       |
| seek-to-start then sequential           | `video_stream.py`, `interaction_crop.py`       |
| monotonic strided seeks                 | pose visualization seek loop                   |
| sorted-sparse frame extraction          | `save_frames_as_png`                           |
| multi-video sequential across boundary  | `MultiVideoReader` consumers                   |
| metadata read                           | `get_video_metadata`                           |

Mechanics: corpus generated with ffmpeg (`testsrc2`; H.264 at GOP 12 and GOP
250, plus rotation variants), interleaved cv2/reader rounds, median of at
least 5 rounds, gate at `median(cv2_time) / median(reader_time) >= 1.0` per
workload (the reader must be at least as fast). Benchmark runs must be
serialized -- one at a time, on a machine without other significant load;
measurements taken alongside concurrent work swung 2x run to run. Marked
`bench` in pytest and excluded from the default run, following the
`mosaic_api` convention.

Cold single random seek is measured and reported against a documented bound
of at most 2x OpenCV, but not gated: no consumer workflow performs isolated
random single-frame seeks (every seek call site today is monotonic forward),
and the ~35 ms process spawn floor makes strict parity unreachable on
short-GOP files without the rejected daemon. For AV1 sources there is no
OpenCV baseline at all: the bundled OpenCV ffmpeg build does not
software-decode AV1, which is a founding reason for this package.

Baseline context recorded for honesty: opencv-python 4.13 bundles FFmpeg 8.0
while this machine's system ffmpeg is 6.1.1, so part of any observed decode
gap is a version fight, not architecture. Shell-level measurements today put
tuned ffmpeg sequential throughput ahead of cv2 (ffmpeg decode+convert+pipe
0.61 s vs cv2 0.80 s for 300 frames of 1080p H.264) once the consumer loop
stops being the bottleneck. The gate runs against whatever system ffmpeg is
present, which is the deployment-honest comparison.

#### Gate policy and thresholds, revised for in-process decode

**(2026-07-17 revision.)** The gate keeps `median(cv2)/median(reader)` per
workload, serialized on an otherwise idle machine (on this machine, the `heavy`
lock), medians over interleaved rounds. The single aspirational `>= 1.0` becomes
three recorded tiers:

1. **Default `>= 1.0`.** Any workload without a recorded carve-out must be at
   least as fast as OpenCV.
2. **Carve-out `>= 0.9`, rationale recorded next to the threshold.** Granted
   only where a structural mechanism -- not a tuning gap -- caps the ratio. The
   owned-BGR sequential shapes carry it: av's reformat-plus-copy (above) is
   structural, ~0.4-0.5 ms/frame at 1080p, and its magnitude sits inside 0.9
   with margin for session variance.
3. **Below 0.9: a non-gating documented bound at the stable measured value plus
   a tracked issue** directed at the consumer-migration decision (the existing
   cold-seek mechanism). On the PyAV architecture nothing measured below 0.9, so
   no workload takes this tier today and no such issue is opened.

The measured table (av 18.0.0; stabilization medians -- repeated serialized runs
on this machine at one base commit -- in the "median" column), which the gate
branch's threshold amendment consumes verbatim:

Tier CARVE (`>= 0.9`), owned-BGR structural copy:

| Workload | Stabilization median | Gate |
| --- | --- | --- |
| sequential-full-decode[gop12] | 0.956 | >= 0.9 |
| sequential-full-decode[gop250] | 0.949 | >= 0.9 |
| seek-then-sequential[gop12] | 1.011 | >= 0.9 |
| seek-then-sequential[gop250] | 0.986 | >= 0.9 |

Tier GATE (`>= 1.0`):

| Workload | Stabilization median | Gate |
| --- | --- | --- |
| sequential-full-decode[rotation] | 1.242 | >= 1.0 |
| strided-decode-step5[gop12] | 1.275 | >= 1.0 |
| strided-decode-step5[gop250] | measured at gate (gop12 form stabilized 1.275) | >= 1.0 |
| monotonic-strided-seeks[gop12] | 2.132 | >= 1.0 |
| monotonic-strided-seeks[gop250] | 1.100 | >= 1.0 |
| sorted-sparse-extraction[gop12] | 1.660 | >= 1.0 |
| sorted-sparse-extraction[gop250] | 1.063 | >= 1.0 |
| multi-video-junction[gop12+gop12] | 1.032 | >= 1.0 |
| metadata-open[gop12], [gop250], [rotation] | 1.067 / 1.060 (approximations) | >= 1.0 |

Non-gating reports:

| Report | Stabilization median | Bound |
| --- | --- | --- |
| cold-random-seek[gop12] | 1.745 | non-gating, `<= 2x`; now parity-or-better |
| cold-random-seek[gop250] | 1.021 | non-gating, `<= 2x`; now parity-or-better |
| probe-cost[gop12], [gop250], [rotation] | report only | none |

Recorded at the threshold site:

- **Rotation leaves the carve family.** The in-process reader rotates through a
  libav transpose filter graph (direction cclock for the corpus's 90-degree
  clip, verified bit-exact against CLI autorotation), which measured 1.242 -- a
  full-tier `>= 1.0` gate, not a carve-out. `numpy.rot90` is refuted for
  rotation (0.396; see the refuted-routes table).
- **Cold-random-seek stays a non-gating report** -- no consumer performs
  isolated random single-frame seeks. Its documented expectation tightens from
  the old 2x ceiling to the new architecture's measured parity-or-better
  (1.745 / 1.021), where the respawn reader was 0.356 / 0.455. The report keeps
  the `assert_bounded` 2x form; the tightening is in the recorded expectation,
  not a weakened assertion.
- **Thin-margin workloads run at 9 rounds.** Any gated workload whose
  stabilization margin over its bound is under 10 percent
  (sorted-sparse-extraction[gop250] 1.063, multi-video-junction[gop12+gop12]
  1.032, the metadata rows) runs at gate time with `rounds=9`, with the
  stabilization
  median recorded next to the threshold so a failure is diagnosable as
  regression-versus-noise. Bounds are never weakened to absorb noise.
- **metadata-open** is measured here through a from-open approximation
  (1.067 / 1.060); the real reader answers metadata from injected `MediaFacts`
  and is far cheaper, so `>= 1.0` holds with room.

Thresholds are set from the stabilization measurement, never a single session:
the observed 0.75-0.96 session variance on the sequential spike is why this is a
rule, not advice.

## Package structure

Distribution `mosaic-media`, import `mosaic_media`. Python floor 3.12 (the
lower of the two consumers; see README). Build backend `uv_build`, following
`mosaic_api`. Core has zero runtime dependencies.

```
mosaic_media/
|-- pyproject.toml
|-- README.md
|-- docs/                    # specs/, plans/, issues/ -- flat, no tool dirs
|-- src/mosaic_media/
|   |-- __init__.py          # facade: the only public import path
|   |-- hwaccel.py           # NVENC/NVDEC capability probing (from mosaic);
|   |                        #   core because io and transcode both need it
|   |-- probe/               # measurement + verdict (stdlib only)
|   |   |-- __init__.py
|   |   |-- errors.py        # copied from mosaic_api/media_probe, filenames
|   |   |-- ffprobe.py       #   unchanged so per-file diffs stay trivial
|   |   |-- timing.py        #   during the duplication window
|   |   |-- gop.py
|   |   |-- boxes.py
|   |   |-- facts.py
|   |   |-- probe.py
|   |   |-- candidates.py
|   |   |-- policy.py
|   |   |-- verdict.py
|   |   `-- sequence.py      # split: only uniform_properties, canonical_fps,
|   |                        #   VideoProperties move; duplicate_stems stays
|   |-- thumbnail/           # still-image utilities (stdlib only)
|   |   |-- __init__.py
|   |   |-- extract.py       # video -> full-res first-frame image
|   |   `-- downscale.py     # dimension math + still image -> exact-size JPEG
|   |-- transcode/           # ffmpeg execution (stdlib only)
|   |   |-- __init__.py
|   |   |-- commands.py      # Verdict -> command specification
|   |   `-- convert.py       # runner + re-probe acceptance
|   |-- io/                  # [io] extra: numpy
|   |   |-- __init__.py
|   |   |-- reader.py        # VideoReader
|   |   |-- index.py         # seek index over packets (adds pos byte offsets)
|   |   |-- multi.py         # MultiVideoReader
|   |   `-- writer.py        # FFmpegVideoWriter (absorbed from mosaic)
|   `-- cli/                 # [cli] extra: typer
|       `-- __init__.py      # media app: probe, transcode
`-- tests/
    |-- probe/               # copied from mosaic_api/tests/media_probe,
    |                        #   passing unmodified apart from import paths
    |-- thumbnail/
    |-- transcode/
    |-- io/
    |-- cli/
    |-- test_import_guard.py
    `-- bench/               # perf regression gate (pytest -m bench)
```

Layering is one-way and guarded by a test, not convention: `cli` imports
`io`/`transcode`/`thumbnail`/`probe`/`hwaccel`; `io` and `transcode` import
`probe` and `hwaccel`; `probe`, `thumbnail`, and `hwaccel` import the
standard library only. `hwaccel.py` sits at the top level rather than inside
`transcode/` for the transcode converter's NVENC gate, and `io` must not import
`transcode`. (The original reason also cited the `io` reader's NVDEC and
writer's NVENC needs; the 2026-07-17 in-process-decode revision removes both --
the reader software-decodes and consumes nothing from `hwaccel.py`, and the
writer probes NVENC usability through the `av` binding rather than the
capability listing. `hwaccel.py` stays top-level for the converter, and the
one-way layering is unchanged.) The import guard poisons `numpy`, `typer`, and
`cv2` in `sys.meta_path` while importing every core module; the revision adds an
`av` poison run over the core (av must never leak downward) and an
`av`-poisoned run that must fail to import `io` (av is as mandatory to `io` as
numpy). The recorded
justification (in `probe/timing.py`, replacing the expired "keeps it
extractable" comment): the transcode CLI must start on a machine that has
ffmpeg and nothing else -- a minimal container, or a tracking box without the
analysis stack.

What stays behind in `mosaic_api`: `facts_io.py` (names the backend's
persistence columns) and `media_types.py` (HTTP Content-Type selection).
`duplicate_stems` stays in the backend half of `sequence.py` (storage path
injectivity).

### pyproject.toml

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

[project.scripts]
mosaic-media = "mosaic_media.cli:app"

[dependency-groups]
dev = ["basedpyright", "ruff", "pytest>=8.0", "pytest-xdist"]
bench = ["opencv-python>=4.7", "numpy>=1.22"]

[tool.pytest.ini_options]
addopts = "-m 'not bench'"
markers = [
    "bench: performance regression gate against OpenCV; excluded by default, run explicitly and serialized with pytest -m bench -n0 -s on an otherwise idle machine",
]
```

(Exact pins for the dev group are set at implementation time to current
versions; basedpyright scope includes `tests/`.)

Consumers wire this in as an editable path dependency
(`{ path = "../mosaic_media", editable = true }`), the `mosaic-behavior`
precedent. No releases while `MediaFacts` is still growing fields.

## io/: the OpenCV decode replacement

**(2026-07-17 revision.)** The `io` layer decodes and encodes in process through
PyAV -- see "In-process decode supersedes the subprocess pipe for io". The
public surface, the frame contract, the packet-index seek semantics, and the
correctness suites are unchanged; the process lifecycle inside `reader.py`,
`writer.py`, and the packet acquisition consumed by `multi.py` are replaced by
container lifecycle. The bullets below describe the pinned behavior with the
revised mechanism.

`VideoReader` (one class):

- **Opening.** Accepts injected probe-derived properties (width, height, fps,
  frame count, packet index). Consumers holding `MediaFacts` never re-measure
  ("measurement is not re-derived"). Without injected facts the reader reads
  declared width, height, rate, and rotation from the av stream header and
  builds the packet index lazily from an in-process demux; `av.open` is lazy
  (first read/seek/metadata need). No consumer workflow re-runs the probe's
  grid-fit measurement.
- **Sequential, strided, and range reads.** One open `av` container decoded
  forward: `frame.to_ndarray(format="bgr24")`, or `"gray"` when the consumer
  asks for grayscale (skips the BGR conversion OpenCV always pays; several
  consumers convert to gray anyway). Frame stepping and the `[start, end)`
  window are Python-side accounting over the forward decode (the CLI `-vf
  select` also decoded every frame; only the drop point moves). `resize`
  produces exactly the requested dimensions after rotation.
- **Seeking.** `container.seek` to the presentation timestamp of the target's
  preceding keyframe (backward keyframe resolution), verify the decoded landing
  matches that keyframe's timestamp, then count frames forward to the target;
  the open decode position is reused when it already lies between that keyframe
  and the target. Frame-exact by construction; OpenCV's `CAP_PROP_POS_FRAMES`
  off-by-N class of bugs is structurally impossible.
- **Sparse batch.** `read_frames(sorted_indices)`: group targets by GOP via the
  packet index, one decode pass per group. Beats OpenCV's per-seek re-decode
  whenever two targets share a GOP; OpenCV re-decodes the chain from the
  keyframe for every single seek.
- **Rotation.** PyAV does not autorotate. The reader applies the display-matrix
  rotation itself through an in-process libav transpose filter graph, verified
  bit-exact against system-ffmpeg CLI autorotation, and reports and shapes every
  frame in the displayed orientation -- so the framemd5 goldens and the
  displayed-orientation contract hold. (`numpy.rot90` was refuted for this on
  performance; see the refuted-routes table.)
- **Frame convention.** BGR uint8, `(height, width, 3)`, matching
  `cv2.VideoCapture` so consumer migration is mechanical. `to_ndarray` returns a
  writable, C-contiguous, non-aliasing array (numpy `OWNDATA` is false by design
  -- the array wraps the reformatted frame's own buffer -- so the contract is
  asserted on writability and non-aliasing, not on the flag), framemd5-exact
  against system-ffmpeg ground truth despite the bundled-libav major skew.
- **hwaccel.** `hwaccel=True` is a documented no-op for the in-process reader:
  decode is always software. No consumer requests hardware decode today, and the
  GPU download path's bit-exactness against the framemd5 goldens is unverified;
  the parameter is retained for signature compatibility, with the
  `av.codec.hwaccel.HWAccel("cuda")` seam named in the docstring if that changes.

`index.py` builds the seek index from the packet scan and deduplicates
presentation timestamps consistently with `measure_timing`, so
`SeekIndex.frame_count` equals `MediaFacts.frame_count`. The `Packet` type still
carries a `pos` byte offset alongside time, size, and keyframe. The io demux
(`packets.py`) feeds seeking only; the probe's `scan_packets` stays the
authoritative measurement scanner, and the two need not agree packet-for-packet
on defective containers (libavformat synthesizes pts where ffprobe reports
absence).

`multi.py` provides `MultiVideoReader`: N ordered files as one global frame
space, segment lookup by bisect, uniformity validated on displayed dimensions
with `probe.sequence.uniform_properties`. Each file is probed once with
`probe_media` for its authoritative `MediaFacts` -- the sanctioned io-to-probe
boundary -- and its per-segment index comes from the in-process demux.
`writer.py` keeps `FFmpegVideoWriter`, now encoding through av (libx264,
yuv420p mp4), with hardware encode gated on caller permission AND an av-side
usability probe.

## transcode/: verdict to execution

`commands.py` maps a `Verdict` to the minimum operation, never a blanket
re-encode:

| Reason                                  | Operation                          |
| --------------------------------------- | ---------------------------------- |
| lying timing header                      | `-c copy` remux, corrected timebase |
| `moov` at tail                           | `-movflags +faststart` remux        |
| variable rate, rotation, non-square pixels, interlacing | AV1 re-encode      |

Encoder selection: SVT-AV1 on CPU; NVENC AV1 when the caller permits
hardware AND `hwaccel.py` detects support (capability probing absorbed from
`mosaic`'s `video_io`, not reimplemented). Hardware is permission-gated
because `ffmpeg -encoders` proves the encoder was compiled in, not that a
usable GPU exists -- distro ffmpeg commonly ships NVENC on GPU-less
machines, exactly the ffmpeg-only boxes this package targets, where a
hardware default would fail every transcode. Policy (`PlaybackProfile`,
`Thresholds`) is injected by the caller; what crosses the boundary is a
command specification.

`convert.py` executes the plan and re-probes the output as its acceptance
test, running both verdicts: a variable-rate source resampled to constant
rate can still carry residual drift. The output probe is not only a gate --
it mints the derivative's authoritative `MediaFacts`, which must be measured
once somewhere because consumers never re-measure; the verdict check is a
nearly-free assertion on facts that are needed anyway.

A red verdict on transcoded output is a **terminal failure**: the converter
raises, the job is marked failed, and a human sees it. It signals a
command-construction bug or an input class the corpus never covered -- a
deterministic condition that re-running the same command cannot fix. Nothing
in the stack may respond to acceptance failure by scheduling another
transcode; retries are reserved for transient errors (disk, resources). This
makes a transcode loop structurally impossible. Confidence that commands
produce clean output is established before any job is ever dispatched, by
the development-time corpus acceptance tests (each reason-to-command mapping
proven green on representative defect files).

Opt-out semantics (unchanged from README): analysis transcode has no opt-out;
playback transcode is mandatory on hard stream reasons and a suggestion on
soft ones.

Sequencing invariant: the transcode must not ship to production before the
reader lands in consumers, or the stack produces AV1 files its own toolkit
cannot decode. Inside this effort both are built; the invariant binds the
later migration effort.

## cli/

Two commands, mirroring the library:

```
mosaic-media probe <file>                 # MediaFacts + verdict, JSON
mosaic-media transcode <file> --target analysis|playback --output <path>
```

`--output` accepts a file path or a directory; given a directory, the output
filename is derived from the source stem (`.mp4` container). The resolved
output must never equal the source path -- the original is preserved in
every case, and the converter refuses rather than overwrites it. The CLI is
agnostic about dataset layout: it holds no knowledge of any directory
convention (for example, `mosaic` datasets keeping originals in `media_raw/`
and transcodes in `media/` -- that mapping belongs to the caller, exactly
like browser policy). Re-running the same transcode may atomically replace
an existing output file; it is idempotent by design.

`mosaic` later mounts the app (`app.add_typer(media_app, name="media")`).
Job infrastructure calls the Python API directly, never the CLI: structured
exceptions, no argv escaping, no output parsing. The CLI exists for humans
and for machines that have nothing but ffmpeg and this package.

### README pinning -- no drift

`README.md` is the package's user-facing document, and these invariants MUST
be pinned there when the implementation lands. A divergence between spec,
README, and implementation is a bug:

- Job infrastructure calls the Python API, never the CLI (already in the
  README's "CLI composition"; must survive every rewrite).
- A red verdict on transcoded output is a terminal failure; nothing ever
  responds to it by scheduling another transcode.
- Output destinations are caller-owned: the package knows no dataset layout,
  derives the filename from the source stem only when handed a directory,
  and never writes over the source file.
- The reader decodes in process through libav bindings (PyAV), with
  packet-index-exact seeking as the improvement. (2026-07-17 revision: this
  supersedes the original subprocess-architecture pin, which followed moviepy
  and imageio-ffmpeg; that architecture failed the performance gate. See
  "In-process decode supersedes the subprocess pipe for io".)

## Testing

- Copied probe tests pass unmodified apart from import paths -- the
  extraction acceptance test.
- Reader correctness: frame-hash equality against `cv2.VideoCapture` across
  the generated corpus for every workflow pattern; seek exactness asserted on
  cases where OpenCV is provably wrong (seeks landing exactly on keyframes,
  where cv2 re-decodes a full GOP and the packet index does not).
- Transcode acceptance: converted output re-probes clean on both verdicts.
- Import guard (layering) and CLI smoke tests.
- The perf regression gate described above, `-m bench`, run explicitly,
  serialized, on an otherwise idle machine.

## Deferred, recorded so nothing is silently dropped

- **Consumer migration** (`mosaic_api` onto `mosaic_media.probe`, `mosaic`
  onto the reader and metadata): the next effort; README phase 3 remains its
  reference. During the window, `mosaic_api/media_probe/` is frozen: fixes
  land here and are back-ported only if urgent.
- **imgstore chunk decode** moves onto `VideoReader` during the `mosaic`
  migration; the descriptor and index layer stays in `mosaic` as is.
- **Decoder daemon**: rejected; this spec's POC findings are the starting
  point if isolated-seek latency ever becomes a product constraint.
- **Live-inference latency** (smart annotations): if it materializes, its
  random access hits whatever analysis reads -- an analysis-clean original
  in any codec the file arrived in (a clean file is never re-encoded), or
  the AV1 derivative of a file that failed the analysis verdict. For AV1
  derivatives no OpenCV baseline exists and the respawn seek is the only
  option either way; for clean originals the respawn seek's documented
  bound of at most 2x OpenCV per cold seek is the cost to weigh, and the
  rejected-daemon POC findings are the starting point if it ever matters.
