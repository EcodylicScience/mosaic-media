# mosaic-media

Media probing, transcode planning, and frame reading for the Ecodylic stack.
Distribution name `mosaic-media`, import name `mosaic_media`.

The package answers two questions about a video file and executes the ffmpeg
work that follows from the answers:

1. Does it play well in a browser, and does it scrub quickly?
2. Is it usable for per-frame analyses like tracking?

A file can need a transcode for one and not the other, so the two verdicts are
kept independent.

```
mosaic-media          probe, verdict, transcode, reader, CLI
    ^          ^
    |          |
mosaic_api    mosaic
```

`mosaic_api` (the FastAPI backend) and `mosaic` (the animal behavior analysis
toolkit) both consume this package; it imports neither. The one-way direction
is what makes the CLI mount and the job wiring legal (see "CLI composition").

Everything described here is implemented, covered by the test suite and a
performance regression gate. What remains is the consumer migration:
`mosaic_api` still carries the original copy of the probe code this package
was extracted from, kept identical until its migration deletes it, and
`mosaic` still decodes through OpenCV.


## Why this package exists

The probe was written in `mosaic_api`, where video is ingested. Three things
argued for moving it below both consumers:

- `mosaic` needs the same measurements. Its `get_video_metadata` reads OpenCV
  properties, falls back to a one-off ffprobe call for frame rate, and counts
  frames by decoding the whole file. The probe's packet scan answers all of it
  without decoding a frame.
- The transcode runner needs the verdict: the reason a file failed selects the
  command that fixes it, and a CLI runner for transcode jobs cannot sit above
  the API.
- The reader needs the packet index the probe already produces (see "The
  reader").


## Adopting this package

Two migration guides inventory the consumer call sites we found and suggest a
migration order. They come from a survey of the code at a point in time; line
numbers drift, symbol names are the stable anchors, and where a guide
disagrees with the code, the code wins.

- `docs/migration-mosaic-api.md` -- the backend. Mostly closing the
  duplication window: delete the internal `media_probe/` copy and rewire its
  consumers onto this package. `mosaic_api` reads no frames server-side, so
  the core alone is enough -- no extras.
- `docs/migration-mosaic.md` -- the toolkit. The larger surface: metadata
  probing, sequential and random-access decode, multi-video sequences, video
  writing, capability probes, and the CLI mount, with an explicit list of the
  codec-free OpenCV image operations that stay.

Wiring is an editable path dependency, following the `mosaic-behavior`
precedent already in `mosaic_api`:

```toml
[project]
dependencies = [
    "mosaic-media",           # mosaic_api: core only
    # "mosaic-media[io,cli]"  # mosaic: reader, writer, CLI mount
]

[tool.uv.sources]
mosaic-media = { path = "../mosaic_media", editable = true }
```

An editable path beats released versions while `MediaFacts` is still growing
fields: every added measurement touches `facts.py` here and `FACT_FIELDS` in
`mosaic_api` as one logical change across two repositories, and a
release-and-bump cycle would be paid on every field.

Migrating `mosaic_api` first is the easier order -- its rewire is mechanical
and behavior-identical -- but nothing breaks if the toolkit goes first. One
constraint holds regardless of order: the transcode must not run against
production datasets before the toolkit can decode the transcode codec
(currently AV1; the reader migration provides that), or the stack produces
files its own toolkit cannot read.

What changes for consumers is small, and loud rather than silent: errors raise
(`MediaProbeError`, `TranscodeError`) instead of returning sentinel values,
frame counts are measured from packet timestamps rather than declared by
headers, and the ingestion probe's `MediaFacts` travel forward as the metadata
authority -- consumers inject them (`VideoReader(path, facts=..., index=...)`,
`MultiVideoReader(paths, facts=..., indices=...)`) instead of re-measuring.
The guides list each difference with its call sites.


## Layering and optional dependencies

| Extra | Adds | Contents |
| --- | --- | --- |
| `mosaic-media` | standard library only | Probe, verdicts, transcode command construction and converter, thumbnails, capability probing. |
| `mosaic-media[io]` | `numpy`, `av` | In-process libav (PyAV) frame reader, seek index, multi-video reader, video writer. No OpenCV. |
| `mosaic-media[cli]` | `typer` | The `mosaic-media` command line app. |

The core is standard library only so the transcode runner can start on a
machine that has ffmpeg and nothing else -- a minimal container, or a tracking
box without the analysis stack. An import test guards this; `mosaic_api`
depends on the core and pulls neither numpy nor typer through it. numpy would
do `timing.py`'s grid fit far faster (measured once during development at
roughly eighty times), but that is under one percent of a probe, and it would
cost the ffmpeg-only deployment.

System requirements: the probe, the transcode, and the CLI shell out to
`ffmpeg` and `ffprobe` on `PATH` -- version 5.1 or newer at runtime
(`-fps_mode`), 6.0 or newer for the test suite (`-display_rotation`).
`VideoReader` decodes in process and needs no ffmpeg binary; its codec table
is pinned by a codec guard test, and `pip install av --no-binary av` (building
against the system libav) is the fallback for a locked-down environment.
`MultiVideoReader` probes each file through ffprobe only when the caller does
not inject `facts`.


## The reader

The probe's packet scan returns every packet's time, size, and keyframe flag
in decode order -- the data frame-exact seeking needs. Keeping the reader in
the same package keeps that index next to its only consumer.

`VideoReader` decodes in process through libav (the `av` package). A seek
resolves the target's preceding keyframe from the packet index, seeks the
container to that keyframe's timestamp, verifies the decoded landing, and
counts frames forward to the target. The index carries every frame's actual
timestamp, so no frame-index-to-time conversion exists to get wrong.

For comparison, the OpenCV seeking this replaces converts the frame index to a
timestamp through one average frame rate (`CAP_PROP_POS_FRAMES`). Measured
against pixel-content ground truth with opencv-python 5.0.0: on constant-rate
files that conversion is sound -- every control seek landed exactly, so
constant-rate datasets were not being misread. On variable-rate files it is
wrong wherever the local rate differs from the average -- on a fixture with a
10 fps stretch inside a 30 fps recording, 12 of 14 seeks landed off by -35 to
+25 frames (upstream reports of this class span 2015-2025: opencv/opencv
issues 4890, 9053, 20227, 26827). Variable rate matters for this corpus
because recordings drop frames when the machine gets busy.
`tests/io/test_reader_vfr.py` pins the reader's frame-exact landing on that
variable-rate shape.

An earlier version of the reader piped frames from an ffmpeg subprocess (the
architecture moviepy and imageio-ffmpeg use); the measurements behind the move
to in-process decode are recorded in
`docs/specs/2026-07-16-extraction-and-reader-design.md`.


## The OpenCV decode problem

`opencv-python` wheels bundle their own ffmpeg build: not under this project's
control, not independently upgradable, with a codec table that varies by wheel
version and platform. A distro OpenCV linked against a capable system ffmpeg
can decode AV1; the pip wheel this stack installs cannot -- probing the 4.13
and 5.0.0 wheels shows no AV1 software decoder (no dav1d, no libaom) and no
hardware decode path (no CUDA, no NVCUVID, no VA-API), so an AV1 file opens
and decodes zero frames, GPU or not. To check any specific wheel:

```bash
python -c "import cv2; print(cv2.getBuildInformation())" | grep -i -A5 "Video I/O"
```

The transcode codec is currently AV1 (see "Why AV1 and not H.264"; the choice
is still open to discussion), and the wheel cannot decode it, so a file
transcoded for analysis could not be read back by a toolkit that decodes
through OpenCV. Owning the decode stack is therefore a prerequisite of any
modern codec, AV1 or a successor; the sequencing constraint under "Adopting
this package" follows from it. Choosing AV1 did not create the problem -- the
wheel decodes H.264 and H.265 only because those decoders are built into
libavcodec, while AV1's (dav1d, libaom) are external libraries the wheel
omits; AV1 is simply the first codec this stack uses that exposes the
ownership defect.

### Narrower than "remove OpenCV"

Splitting `mosaic`'s OpenCV surface by whether a codec is involved:

- Codec-bound: `cv2.VideoCapture` and the `CAP_PROP_*` properties -- decode,
  seek, metadata. This is what the reader and the probe replace.
- Codec-free: `cv2.resize`, `cv2.cvtColor`, `cv2.imwrite`, and the rest of the
  image operations. These stay. OpenCV remains a `mosaic` dependency for
  overlays, identity models, and pose training converters; it stops being the
  decoder.

The one structure ffmpeg cannot read is the imgstore index layer (the
`__store` descriptor, per-chunk `.npz` indexes, the frame-to-chunk mapping).
That layer is plain Python and numpy and stays in `mosaic`. The chunks
themselves are mp4, which the reader decodes fine.


## Transcode semantics

Two targets, one codec -- currently AV1, see below -- differing in rate
control and in whether they run at all. The original upload is preserved in
every case; derivatives are separate artifacts.

| Target | Trigger | Opt-out |
| --- | --- | --- |
| Analysis | Any analysis reason: variable frame rate, unreliable timing metadata, rotation, non-square pixels, interlacing. | None -- required for valid per-frame results. |
| Playback | A hard stream reason: the file cannot play in the browser at all. | None. |
| Playback | A soft stream reason: the file plays, but not well or not everywhere. | Opt-out; presented as a suggestion. |

The verdict selects the minimum operation, not a blanket re-encode: a header
that lies about timing gets a `-c copy` remux with a corrected timebase, a
tail `moov` gets `-movflags +faststart`, a supported stream in an unopenable
container gets a rewrap, and only a defect in the pixel grid or the frame
clock gets a real AV1 re-encode. Encoding runs on SVT-AV1 (CPU), or NVENC AV1
when the caller permits hardware and `mosaic_media.hwaccel` verifies a usable
device -- an encoder listing proves it was compiled in, not that it works.

Variable frame rate is the hard measurement. ffprobe and OpenCV both produce
false positives on containers that merely quantize timestamps to milliseconds,
so the probe fits a uniform grid across all frame timestamps and measures the
worst deviation in frame periods -- over the whole file, because a bounded
window misclassified a large share of the ingestion corpus this was developed
against (recordings drop frames when the machine gets busy, not at the start).

The transcoded output is re-probed as its acceptance test (a variable-rate
source resampled to constant rate can still carry residual drift), and that
probe also mints the derivative's authoritative `MediaFacts`. A red verdict on
the output is terminal: the converter raises, the job is marked failed for a
human to see, and nothing retries -- the same deterministic command on the
same input would reproduce the same red output. Retries are reserved for
transient faults such as a killed subprocess or a full disk.

### Why AV1 and not H.264

AV1 is the current choice, not a settled constant -- the discussion stays
open. Encoder selection is confined to the transcode layer and playback
support is injected profile policy, so revisiting the choice would not ripple
through consumers. The case for AV1 today: the derivatives are a permanent
second copy of every defective upload, which makes the codec choice a storage
decision first.

- Lower bitrate than H.264 at equal perceptual quality: published encoder
  comparisons typically report 30-50% BD-rate savings, varying by encoder,
  preset, and content (not measured on this corpus). Equivalently, at a fixed
  storage budget the derivative carries fewer quantization artifacts into the
  tracker.
- Royalty-free (AOMedia). H.265's patent pools rule it out on their own;
  H.264's pool is manageable but nonzero for a platform distributing encoded
  content.
- Archive runway: re-encoding a corpus later is expensive, H.264 encoders are
  at the end of their improvement curve, and AV1 encoders keep improving
  against a fixed bitstream specification.
- Playback coverage matches the shipped profile: current Chrome, Firefox, and
  Edge ship software AV1 decode; Safari plays AV1 only with hardware decode
  (M3 and A17 or later). The default profile is Chrome; if Safari becomes a
  target, the playback codec is profile policy, not a constant.

H.264 would buy faster encodes, cheaper decode, and universal playback -- at
roughly twice the storage, forever. Originals are preserved and the toolkit
decodes through libav, so universality is already covered structurally.


## CLI composition

`mosaic` composes typer sub-applications; mounting this package's app is the
same pattern:

```python
app.add_typer(media_app, name="media")
```

```bash
mosaic media probe video.mp4
mosaic media transcode video.mp4 --target playback --output media/
```

`--output` takes a file path or an existing directory (the filename then
derives from the source stem, always `.mp4`). The converter refuses to write
over the source and refuses a file destination that does not end in `.mp4`;
re-running the same transcode replaces its output atomically. The package
knows no dataset layout -- a convention like `media_raw/` for originals and
`media/` for derivatives belongs to the caller, like every other policy.

Job infrastructure calls the Python API (`run_transcode`) rather than the CLI:
structured exceptions, no argv escaping, no output parsing. The CLI exists for
humans and standalone use; both entry points are the same one-way edge.

Division of responsibility: policy (which criteria a file must satisfy, which
profile applies) is constructed by the caller and injected; execution (ffmpeg
invocation, encoder selection) lives here; scheduling (queueing, cancellation,
subprocess lifecycle) stays in `mosaic`'s job infrastructure, which calls in.
What crosses the boundary is a command specification, never a policy.


## Metadata authority

The probe runs once, at ingestion, and its `MediaFacts` travel forward as the
authoritative metadata; consumers inject them instead of re-measuring. A file
that is already analysis-clean is never re-encoded, so downstream code cannot
assume canonically written bytes -- re-probing with OpenCV would reintroduce
the false-positive variable-rate detection and unreliable frame counts this
package exists to avoid.


## Extraction boundary

The code was extracted from `mosaic_api/src/mosaic_api/media_probe/`. The
boundary is semantic, not mechanical: the original package had no external or
database imports anywhere, so what moved was decided by domain -- media
measurement moved, backend vocabulary stayed.

### Extraction inventory

| Module | Destination | Reason |
| --- | --- | --- |
| `errors.py` | mosaic-media | Media-domain error type. |
| `ffprobe.py` | mosaic-media | Header read and packet scan. |
| `timing.py` | mosaic-media | Grid fit over packet timestamps. |
| `gop.py` | mosaic-media | Seek cost in bytes and frames. |
| `boxes.py` | mosaic-media | ISOBMFF `moov` placement. |
| `facts.py` | mosaic-media | `MediaFacts`, the measurement result. |
| `probe.py` | mosaic-media | Composes the above into one scan. |
| `candidates.py` | mosaic-media | Video extension set. |
| `policy.py` | mosaic-media | The `PlaybackProfile` and `Thresholds` types; which profile to apply stays with the caller. |
| `verdict.py` | mosaic-media | Reason sets; the transcode selects commands from them. |
| `downscale.py`, `thumbnail.py` | mosaic-media | ffmpeg-produced derivatives, the converter's family. |
| `media_types.py` | stays in `mosaic_api` | Container to HTTP `Content-Type`; a download and `<source type>` concern. |
| `facts_io.py` | stays in `mosaic_api` | Names the backend's persistence columns. It never imported the ORM -- it is written against a structural `Protocol` -- and stays for what it encodes, not what it imports. |
| `sequence.py` | split | The uniformity check and `canonical_fps` moved (the multi-video reader validates sequences with them); `duplicate_stems` stayed (backend storage layout). |


## Open questions

- **Scope ceiling.** Whether `mosaic`'s remaining media I/O (the imgstore
  index layer, the reader dispatchers) eventually moves here under `[io]`,
  leaving `mosaic` with image processing only. The extras split is arranged so
  this could happen without touching the standard-library core; nothing forces
  the decision yet.
