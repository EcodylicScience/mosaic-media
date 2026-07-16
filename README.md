# mosaic-media

Media probing, transcode planning, and frame reading for the Ecodylic stack.
Distribution name `mosaic-media`, import name `mosaic_media`.

This package sits upstream of both `mosaic_api` (the FastAPI backend) and
`mosaic` (the animal behavior analysis toolkit). Both consume it; it consumes
neither. It answers two questions about a video file and executes the ffmpeg
work that follows from the answers:

1. Does it play well in a browser, and does it scrub quickly?
2. Is it usable for per-frame analyses like tracking?

Those are not the same question, and a file can need a transcode for one and
not the other. The two verdicts are independent by design.

This document doubles as the extraction plan. The code does not live here yet;
it lives in `mosaic_api/src/mosaic_api/media_probe/` and moves here in the
phases below.


## Position in the stack

```
mosaic-media          probe, verdict, ffmpeg command construction, reader, CLI
    ^          ^
    |          |
mosaic_api    mosaic
```

The edges run one way. `mosaic_media` never imports `mosaic_api` or `mosaic`.
This is the constraint that makes the CLI composition and the job wiring below
legal; violating it in either direction reintroduces a cycle.


## Why this package exists

The probe currently lives in `mosaic_api` because that is where video is
ingested. Three things pull it lower:

- **`mosaic` needs the same measurements.** Its `get_video_metadata` reads
  width, height, frame rate, and frame count from OpenCV properties and falls
  back to a one-off ffprobe call for frame rate and a **full decode** to count
  frames. The packet scan in the probe answers all of it more accurately and
  without decoding a frame.
- **The transcode needs the verdict.** The command to run is selected by the
  reason the file failed, and the reasons live in the verdict. A CLI runner for
  transcode jobs cannot sit above the API.
- **The reader needs the packet index.** See "Why the reader comes here too".


## What lives here, and what stays behind

The probe subpackage is 17 modules, roughly 1400 lines, and every import in it
is either standard library (`json`, `struct`, `subprocess`, `dataclasses`,
`pathlib`, `typing`, `collections`) or package-local. **There are no external
dependencies and no database imports anywhere in it, including `facts_io.py`.**

This has a practical consequence for anyone executing the extraction: *the
boundary is semantic, not mechanical*. Grepping for database imports to decide
what to leave behind finds nothing, because `facts_io.py` was deliberately
written against a structural `Protocol` so the package never imports the ORM
models. It stays behind because `FACT_FIELDS` names `mosaic_api`'s persistence
columns -- it encodes the backend's schema vocabulary -- not because it touches
a database.

The upside of the same fact: the extraction requires no import untangling and
no dependency surgery. All the difficulty is in deciding which concepts are
media-domain and which are API-domain.

### Extraction inventory

| Module | Destination | Reason |
| --- | --- | --- |
| `errors.py` | `mosaic-media` | Media-domain error type. |
| `ffprobe.py` | `mosaic-media` | Header read and packet scan. The foundation. |
| `timing.py` | `mosaic-media` | Grid fit over packet timestamps. |
| `gop.py` | `mosaic-media` | Seek cost in bytes and frames. |
| `boxes.py` | `mosaic-media` | ISOBMFF `moov` placement. |
| `facts.py` | `mosaic-media` | `MediaFacts`, the measurement result. |
| `probe.py` | `mosaic-media` | Composes the above into one scan. |
| `candidates.py` | `mosaic-media` | Video extension set. |
| `policy.py` | `mosaic-media` | Already designed for this: "Injected policy. The package encodes no opinion about any one browser." The `PlaybackProfile` and `Thresholds` types are media-domain; the choice to apply a given profile stays with the caller. |
| `verdict.py` | `mosaic-media` | The transcode CLI needs it to select commands. |
| `downscale.py` | `mosaic-media` | Runs ffmpeg to produce a derivative -- the same family as the transcode converter. Also overlaps `mosaic`'s existing `save_frames_as_png`. |
| `thumbnail.py` | `mosaic-media` | Same. |
| `media_types.py` | stays in `mosaic_api` | Keyed on ffprobe `format_name`, but its output is an HTTP `Content-Type` and its purpose is downloads, caching intermediaries, and `<source type>` selection. A transcode CLI has no use for it. |
| `facts_io.py` | stays in `mosaic_api` | Names the backend's persistence columns. |
| `sequence.py` | **split** | `uniform_properties`, `canonical_fps`, and the `VideoProperties` protocol move here -- `MultiVideoReader` reads N videos as one stream and wants exactly that uniformity check. `duplicate_stems` stays in `mosaic_api`: it exists for thumbnail and pose sidecar path injectivity, which is backend storage layout. |

Do not move `sequence.py` whole in either direction. It is the one module that
is internally mixed.


## Layering and optional dependencies

Three layers, each a heavier dependency set than the last:

| Extra | Adds | Contents |
| --- | --- | --- |
| `mosaic-media` | standard library only | Probe, verdict, ffmpeg command construction. |
| `mosaic-media[io]` | `numpy` | ffmpeg-pipe frame reader, seek index, multi-video reader. **No OpenCV.** |
| `mosaic-media[cli]` | `typer` | The `mosaic-media` command line app. |

`mosaic_api` imports the core and must not pull in `typer` or `numpy` through
it. Only `mosaic_media.cli` may import `typer`.

### The standard-library-only invariant needs a new reason

The probe is standard library only today, and the reason recorded in
`timing.py` is that this "is what keeps it extractable" -- numpy would do the
grid fit roughly eighty times faster but buys under one percent of the whole
probe, so it was never worth the dependency.

**That justification expires the moment this extraction lands.** Once the
package is extracted, "keeps it extractable" is spent, and the next person to
read that comment will add numpy to `timing.py`.

The durable reason is the CLI: the transcode runner has to start on a machine
that has ffmpeg and nothing else -- a minimal container, or a tracking box
without the analysis stack. Record that reason in the code, and guard the
invariant with an import test rather than leaving it to convention.


## Why the reader comes here too

The scope of this package is probe **and** reader, not probe alone.

`scan_packets` already returns every packet's time, size, and keyframe flag in
decode order. **That is a seek index.** Frame-exact random seeking is the one
genuinely difficult part of reading video through ffmpeg, and the probe already
collects exactly the data that solves it. Splitting probe from reader puts the
index in one package and its only consumer in another.

It is also an upgrade rather than a risk: the reader being replaced seeks with
OpenCV's `CAP_PROP_POS_FRAMES`, which is a well-known source of off-by-N frame
errors.


## The OpenCV decode problem

`opencv-python` wheels bundle their own ffmpeg build. That build is not under
this project's control, cannot be upgraded independently, and its codec table
varies by wheel version, platform, and install method. This is an ownership
problem, not a universal impossibility: a distro OpenCV linked against a
capable system ffmpeg, or a custom CUDA build, can decode AV1. The pip wheel
this stack installs cannot, in any configuration -- probing opencv-python
4.13 shows no AV1 software decoder (no dav1d, no libaom) and no hardware
decode path at all (no CUDA, no NVCUVID, no VA-API), so a dedicated GPU
changes nothing for it: an AV1 file opens but decodes zero frames. Which
capability you get is a property of whichever binary happened to be
installed, invisible to the code that depends on it.

AV1 is the transcode codec for this stack, chosen for compression efficiency
on this content class, royalty freedom, and archive runway (see "Why AV1 and
not H.264" under Transcode semantics). So:

**A file this package transcodes for analysis cannot be read back by the
toolkit that consumes it, for as long as that toolkit decodes through OpenCV.**

That makes replacing OpenCV decoding a prerequisite of the codec decision, not
a cleanup that can be deferred. It also sets a hard sequencing constraint: the
transcode must not ship to production before the reader lands, or the stack
produces files it cannot read.

To confirm the codec table of a specific wheel:

```bash
python -c "import cv2; print(cv2.getBuildInformation())" | grep -i -A5 "Video I/O"
```

### The problem is narrower than "remove OpenCV"

The full OpenCV surface in `mosaic`'s `video_io.py` is small. Split by whether a
codec is involved:

- **Codec-bound, and the only exposure:** `cv2.VideoCapture` and the
  `cv2.CAP_PROP_*` properties -- decode, seek, and metadata. Metadata is already
  answered better by the probe.
- **Codec-free, no exposure at all:** `cv2.resize` / `INTER_AREA`,
  `cv2.cvtColor` / `COLOR_BGR*`, `cv2.imwrite`. These are pure image operations.
  ffmpeg could do them via `-vf scale` and `-pix_fmt`, but there is no reason to
  force it.

So the migration is surgical: **replace `VideoCapture`, keep the image
operations.** This is not a project to drop the OpenCV dependency. OpenCV
remains a `mosaic` dependency regardless -- more than a dozen modules use it for
overlays, identity models, and pose training converters. It simply stops being
the decoder.

### What genuinely cannot be native ffmpeg

Only one thing, and less of it than expected.

**The imgstore index layer.** An imgstore is a descriptor with a top-level
`__store` key plus zero-padded chunk files (`000000.mp4` with an `000000.npz`
index). ffmpeg cannot read the descriptor, the per-chunk `.npz` index, or the
mapping from a contiguous frame index to a (chunk, `frame_index`) pair. But
that layer is plain Python and numpy -- it needs no OpenCV -- and **the chunks
themselves are mp4, which ffmpeg decodes fine**. So `imgstore_io` splits along
the same line as everything else: the index and descriptor logic stays Python,
the chunk decode becomes ffmpeg. Its OpenCV usage (`VideoCapture`, `cvtColor`
for grayscale to BGR, `CAP_PROP_*`, `resize`) is all replaceable. It is already
an optional dependency, gated behind `_require_imgstore`.

Nothing else in either media module fundamentally requires OpenCV for I/O.


## CLI composition

`mosaic` already depends on `typer` and already composes sub-applications:

```python
app.add_typer(features_app, name="features")
app.add_typer(tracking_app, name="tracking")
```

Mounting this package's app is the identical pattern:

```python
app.add_typer(media_app, name="media")
```

which gives a toolkit user the native form:

```bash
mosaic media transcode video.mp4 --target streaming
```

The dependency runs `mosaic -> mosaic-media`, one way, no cycle.

**The job infrastructure calls the Python API directly, not the CLI.** Since
`mosaic` imports this package to mount the app, its transcode jobs should call
the library: structured exceptions, no argv escaping, no output parsing. The
CLI exists for humans and for standalone use. Both entry points are the same
one-way edge.

The division of responsibility across the stack:

- **Policy** -- which criteria a file must satisfy, and which command follows
  from a failure -- is constructed in `mosaic_api` from its own playback profile
  and thresholds, and executed here. This package encodes no opinion about any
  one browser.
- **Execution** -- ffmpeg invocation, GPU acceleration, codec settings -- lives
  here.
- **Scheduling** -- queueing, subprocess lifecycle, cancellation -- stays in
  `mosaic`'s job infrastructure, which calls in.

What crosses the boundary from the backend is a command specification, never a
policy.


## Transcode semantics

Two targets, one codec (AV1), differing in rate control and in whether they run
at all. The raw upload is preserved in every case; the derivatives are separate
artifacts.

### Why AV1 and not H.264

The derivatives are a permanent second copy of every defective upload, so the
codec choice is a storage decision first:

- **Roughly half the bitrate of H.264 at equal perceptual quality** (40-50%
  BD-rate savings is the consistently reproduced range), and static-camera
  behavioral footage -- a fixed arena, a long static background, small moving
  subjects -- is the content class where AV1's prediction tools open that gap
  widest. Equivalently: at a fixed storage budget the derivative carries
  fewer quantization artifacts into the tracker.
- **Royalty-free by construction** (AOMedia). H.265's fragmented patent pools
  rule it out on their own; H.264's pool is manageable but nonzero for a
  platform distributing encoded content.
- **Archive runway.** Re-encoding a corpus later is expensive. H.264 is at
  the end of its improvement curve; AV1 encoders keep improving against a
  fixed bitstream specification.
- **Playback coverage matches the injected profile.** Chrome, Firefox, and
  Edge software-decode AV1 everywhere; Safari requires AV1 hardware (M3 and
  A17 or later). The shipped default profile is Chrome; if Safari ever
  becomes a target, the playback codec is profile policy, not a hardcoded
  constant.

What H.264 would buy instead: faster encodes, cheaper decode, and
decode-everywhere universality -- at roughly twice the storage, forever.
Universality is already provided structurally (originals are preserved, and
the toolkit decodes through system ffmpeg); encode speed is a one-time cost
per defective file.

One causality worth stating plainly: the OpenCV decode problem above is not
caused by choosing AV1. It is an ownership defect that the first modern codec
exposes -- the wheel decodes H.264 and H.265 only because their software
decoders are built into libavcodec, while AV1's (dav1d, libaom) are external
libraries the wheel omits.

| Target | Trigger | Opt-out |
| --- | --- | --- |
| Analysis | Any analysis reason: variable frame rate, unreliable timing metadata, rotation, non-square pixels, interlacing. | **None.** Required to ensure valid per-frame results. |
| Playback | A hard stream reason -- the file cannot play in the browser at all. | None. Mandatory. |
| Playback | A soft stream reason -- the file plays, but not well or not everywhere. | Opt-out. Presented as a suggestion. |

Two properties of the existing verdict worth preserving through the move:

- **It selects the minimum operation, not a re-encode by default.** A header
  that lies about timing needs a `-c copy` remux with a corrected timebase; a
  `moov` at the tail needs `-movflags +faststart`; only genuine variable frame
  rate needs a real re-encode. This is the answer to "should we re-encode
  everything" -- no, and the reason set is what picks the cheap fix.
- **Variable frame rate is the hard case.** ffprobe and OpenCV both produce
  false positives on containers that merely quantize timestamps to
  milliseconds. The probe fits a uniform grid across the frame timestamps and
  measures the worst deviation in frame periods. The fit must cover the whole
  file: a bounded window misclassifies a large share of the corpus, because a
  recording drops frames when the machine gets busy, not at the start.

The transcoded output is re-probed as its acceptance test. A variable-rate
source resampled to a constant rate can still carry residual drift, so the
verdict runs on both sides of the transcode.


## Metadata authority

The probe runs once, at ingestion, and its `MediaFacts` travel forward as the
authoritative metadata. **Consumers do not re-derive them.**

This matters because a file that is already analysis-clean is not re-encoded,
so downstream code cannot assume canonically-written bytes. If the toolkit
re-probes with OpenCV, it reintroduces exactly the false-positive variable-rate
detection and unreliable frame counts that this package exists to avoid. One
measurement at the boundary, carried forward, is the whole point.


## Extraction plan

Phases are ordered by dependency. The sequencing constraint from the OpenCV
decode problem is binding: the transcode cannot ship to production before the
reader lands.

### 1. Extract the probe core

Move the modules marked `mosaic-media` in the inventory, split `sequence.py`,
leave `facts_io.py` and `media_types.py` behind. No import untangling is needed;
the package is already standard library only. Rewire `mosaic_api` onto the new
import path. No behavior change -- the acceptance test is that the backend's
existing probe tests pass unmodified against the extracted package.

Add the import guard for the standard-library-only invariant and record its new
justification.

Use an editable path dependency rather than releases. `mosaic-behavior` is
already wired into `mosaic_api` this way, and the precedent matters here: every
added measurement touches `facts.py` (in this package) and `FACT_FIELDS` (in the
backend) as one logical change across two repositories. Release-and-bump
friction on that surface would be paid on every field.

### 2. Frame reader and seek index

Build the ffmpeg-pipe reader behind the `[io]` extra: decode to raw frames,
seek via the packet index that `scan_packets` already produces, multi-video
reading. numpy only, no OpenCV.

### 3. Toolkit adoption

Rewire `mosaic`'s `get_video_metadata` onto the probe, deleting the OpenCV
property read, the one-off ffprobe frame-rate fallback, and the full-decode
frame counter. Rewire `video_io`'s readers and `imgstore_io`'s chunk decode onto
the reader from phase 2, keeping the imgstore index layer as is and keeping
OpenCV for image operations.

**This phase is what justifies the package.** The case for sitting upstream of
both consumers rather than staying in the backend rests on the toolkit actually
adopting it. Deferred, this is a rename with packaging overhead and one
consumer.

### 4. Transcode command construction and CLI

The command builder -- verdict to argv -- plus the converter, GPU acceleration,
and the typer app. Mount it into `mosaic`'s CLI. Wire `mosaic`'s job
infrastructure to the Python API.


## Open questions

- **Repository visibility.** The transcode converter was scoped as a private
  backend repository. If this package is not private, the split needs a decision
  and the command builder needs an explicit home -- it depends on `verdict.py`,
  which lives here, but it is transcode policy.
- **Scope ceiling.** Whether `mosaic`'s readers move here permanently under
  `[io]`, making this the home for all media I/O and leaving `mosaic` with image
  processing only. The extras split above is arranged so that this can happen
  without breaking the standard-library-only core, but it is not yet decided.
- **GPU coverage.** AV1 hardware encode is limited to recent GPUs. The CPU
  fallback via SVT-AV1 is acceptable on a decent multi-core machine at a sane
  preset. Capability probing for NVENC and NVDEC already exists in `mosaic`'s
  `video_io` and should move here with the reader rather than be reimplemented.
</content>
