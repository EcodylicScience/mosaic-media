# mosaic-media: extraction, ffmpeg reader, and transcode design

Date: 2026-07-16
Status: approved design, awaiting implementation plan

This spec covers the initial population of the `mosaic-media` package: the
duplication of the probe subpackage out of `mosaic_api`, the pure-ffmpeg frame
reader that replaces OpenCV decoding, the transcode command builder and
converter, and the CLI. `README.md` remains the standing architecture
reference; this spec records the decisions specific to this effort and the
evidence behind them.

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
`transcode/` because the reader (NVDEC) and writer (NVENC) need it as much as
the converter does, and `io` must not import `transcode`. The import guard
poisons `numpy`, `typer`, and `cv2` in `sys.meta_path` while importing every
core module. The recorded
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

`VideoReader` (one class):

- **Opening.** Accepts injected probe-derived properties (width, height, fps,
  frame count, packet index). Consumers holding `MediaFacts` never
  re-measure ("measurement is not re-derived"). Without injected facts the
  reader runs this package's probe once at open.
- **Sequential, strided, and range reads.** One persistent ffmpeg process:
  `-vf select` for frame stepping, decode-time `scale` for resize, `-pix_fmt
  bgr24` output, or `gray` when the consumer asks for grayscale (skips the
  BGR conversion OpenCV always pays; several consumers convert to gray
  anyway). Consumer loop: pipe enlarged to 1 MB via `F_SETPIPE_SZ`,
  `readinto` preallocated buffers. Both tunings measured necessary: the
  naive Python read loop cost 0.5 s over 300 frames.
- **Seeking.** The packet index makes the discard-versus-respawn choice
  exact: a seek respawns with `-ss <timestamp of the target's preceding
  keyframe>` and decodes `target - keyframe` frames, unless the current
  position already lies between that keyframe and the target, in which case
  reading and discarding `target - current` frames on the live process is
  strictly cheaper. Frame-exact by construction; OpenCV's
  `CAP_PROP_POS_FRAMES` off-by-N class of bugs is structurally impossible.
- **Sparse batch.** `read_frames(sorted_indices)`: group targets by GOP via
  the packet index, one decode pass per group. Beats OpenCV's per-seek
  re-decode whenever two targets share a GOP; OpenCV re-decodes the chain
  from the keyframe for every single seek.
- **Frame convention.** BGR uint8, `(height, width, 3)`, matching
  `cv2.VideoCapture` so consumer migration is mechanical.

`index.py` builds the seek index from `scan_packets` output. `scan_packets`
gains a `pos` field (packet byte offset) alongside time, size, and keyframe.

`multi.py` provides `MultiVideoReader`: N ordered files as one global frame
space, segment lookup by bisect, uniformity validated with
`probe.sequence.uniform_properties`. `writer.py` absorbs `FFmpegVideoWriter`
from `mosaic` (it is already pure ffmpeg).

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
- The reader's subprocess architecture follows moviepy and imageio-ffmpeg,
  with packet-index-exact seeking as the improvement.

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
