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
    ^
    |
 consumers            e.g. mosaic (animal behavior analysis toolkit), a backend
```

Higher-level tools depend on this package; it imports none of them. `mosaic`
(the animal behavior analysis toolkit) is one such consumer. The one-way
direction is what makes the CLI mount and the job wiring legal (see "CLI
composition").


## Installation

```bash
pip install mosaic-media          # core: probe, verdict, transcode, thumbnails
pip install "mosaic-media[io]"    # + in-process libav reader/writer (numpy, av)
pip install "mosaic-media[cli]"   # + the mosaic-media command line app
```

The core is standard library only (see "Layering and optional dependencies").
`ffmpeg` and `ffprobe` must be on `PATH` for the probe, the transcode, and the
CLI -- version 5.1 or newer at runtime; the in-process reader decodes through
`av` and needs no ffmpeg binary.

```python
from mosaic_media import probe_media

facts = probe_media("recording.mp4")
print(facts.frame_count, facts.fps, facts.video_uuid)
```

The probe runs once at ingestion, and its `MediaFacts` travel forward as the
authoritative metadata; consumers inject them rather than re-measuring (see
"Metadata authority").


## Why this package exists

The probe began life inside the backend where video is ingested. Three things
argued for moving it below its consumers into a package of its own:

- `mosaic` needs the same measurements, and its earlier metadata path read
  OpenCV properties, fell back to a one-off ffprobe call for frame rate, and
  counted frames by decoding the whole file. The probe's packet scan answers
  all of it without decoding a frame.
- The transcode runner needs the verdict: the reason a file failed selects the
  command that fixes it, and a CLI runner for transcode jobs cannot sit above
  the API.
- The reader needs the packet index the probe already produces (see "The
  reader").

Consumers wire it as an editable path dependency during development, following
the `mosaic-behavior` precedent, and pin a compatible range
(`mosaic-media>=0.2.0,<0.3.0`) otherwise -- see "Versioning".


## Layering and optional dependencies

| Extra | Adds | Contents |
| --- | --- | --- |
| `mosaic-media` | standard library only | Probe, verdicts, transcode command construction and converter, thumbnails, capability probing. |
| `mosaic-media[io]` | `numpy`, `av` | In-process libav (PyAV) frame reader, seek index, multi-video reader, video writer. No OpenCV. |
| `mosaic-media[cli]` | `typer` | The `mosaic-media` command line app. |

The core is standard library only so the transcode runner can start on a
machine that has ffmpeg and nothing else -- a minimal container, or a tracking
box without the analysis stack. An import test guards this: a consumer that
needs only the core pulls neither numpy nor typer through it. numpy would do
`timing.py`'s grid fit far faster (measured once during development at roughly
eighty times), but that is under one percent of a probe, and it would cost the
ffmpeg-only deployment.

System requirements: the probe, the transcode, and the CLI shell out to
`ffmpeg` and `ffprobe` on `PATH` -- version 5.1 or newer at runtime
(`-fps_mode`), 6.0 or newer for the test suite (`-display_rotation`).
`VideoReader` decodes in process and needs no ffmpeg binary; its codec table
is pinned by a codec guard test, and `pip install av --no-binary av` (building
against the system libav) is the fallback for a locked-down environment.
`MultiVideoReader` probes each file through ffprobe only when the caller does
not inject `facts`.


## Versioning

Releases follow semantic versioning, with the pre-1.0 convention that a minor
bump is a breaking change and a patch bump is compatible. A consumer pins a
range (`mosaic-media>=0.2.0,<0.3.0`); a bare floor would not exclude the next
breaking release.

The identity scheme is a second, independent number: `IDENTITY_SCHEME`, carried
in both format tags and recorded on every probe as `MediaFacts.identity_scheme`.
It moves only when the bytes hashed into `video_uuid` or `content_digest`
change, and a move re-mints every value in every corpus.

The two are coupled in one direction only: **a scheme bump always forces a
version bump, and a version bump never implies a scheme bump.** Bumping the
scheme is an edit here that invalidates every stored value, which is a breaking
release by definition; most releases change nothing that is hashed, so the
reverse does not follow.

They cannot be one number. The scheme's trigger is an ffmpeg upgrade that
changes libavformat's demuxer output -- the digest is defined against that
output rather than raw file bytes -- and no API here changes when that happens,
so a version number has nothing to signal it with. And a shared number would
fold build metadata into identity: every unrelated release would re-mint every
uuid in every corpus. Hashing only part of the version does not rescue it --
past 1.0, an unrelated API break would re-mint everything while a genuine
format break inside a major line would not.


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
through OpenCV -- which is why `mosaic` decodes through this package's reader,
not OpenCV. Owning the decode stack is a prerequisite of any modern codec, AV1
or a successor. Choosing AV1 did not create the problem -- the
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

A transcode changes the pixels and therefore every measured fact, so the
derivative's identity shares nothing with its source's and no hash can recover
the link. `TranscodeResult` carries `source_video_uuid` for that reason -- the
source's `video_uuid`, recorded on the result (on the no-op branch too, since it
describes the input either way). It is the move-resilient form of a
source-to-derivative edge a consumer may also track by path; a caller that wants
it persisted stores it alongside the derivative's own facts.

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
mosaic media compare left.mp4 right.mp4
```

`--output` takes a file path or an existing directory (the filename then
derives from the source stem, always `.mp4`). The converter refuses to write
over the source and refuses a file destination that does not end in `.mp4`;
re-running the same transcode replaces its output atomically. The package
knows no dataset layout -- a convention like `media_raw/` for originals and
`media/` for derivatives belongs to the caller, like every other policy.

`compare` probes both files and prints the `DuplicateComparison` as JSON, for
ad-hoc use; the ingestion pathway compares stored facts and never re-probes. Its
verdict is also the exit code, so it works as a shell test without parsing
stdout:

| Code | Meaning |
| --- | --- |
| 0 | duplicate |
| 1 | a probe failed on either file |
| 3 | distinct |
| 4 | different timing |
| 5 | timing unknown |

Exit 2 is left to the CLI framework's usage error, so a mistyped option is never
mistaken for a verdict. `--fps-tolerance` and `--duration-tolerance` override
the derived defaults; the left file is the reference the tolerances are computed
from.

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


## Video identity

The probe derives two values per file, both `MediaFacts` fields, so they travel
with the rest of the metadata and are minted once at ingestion. They answer
different questions and are not interchangeable.

| | `video_uuid` | `content_digest` |
| --- | --- | --- |
| Pins | coded content and exact timing | coded content only |
| Survives | faststart, moov relocation, a tag edit, a same-container repack | all of those, plus an mp4/mkv repack |
| Changes on | a re-encode, a retime, a truncation, a container change | a re-encode, a truncation, a bitstream reframing |
| Use for | naming, hash chains, derived paths, cache keys | duplicate candidate lookup |
| Never use for | duplicate detection | naming, or anything a chain consumes |

Both are built from a per-packet payload hash the packet scan now reads through
ffprobe's `-show_data_hash`, not from a decoded frame and not from a file
checksum. `content_digest` hashes the codec-level facts and every packet's size,
keyframe flag, and payload hash; `video_uuid` folds the packet timestamps on top
and is emitted as an RFC 9562 UUIDv8.

`video_uuid` is the only value safe to compare for identity or to name anything.
Because it hashes the timestamps, **a container change moves it, and a directory
named from it is not recoverable across one -- not even by repacking back**:
Matroska quantizes timestamps to milliseconds and MPEG-TS rebases onto the first
program clock reference, and returning to the original container inherits that
quantization rather than undoing it.

`content_digest` survives those container rewrites, but only where they preserve
the elementary stream. It is not invariant across a bitstream reframing: an mp4
to MPEG-TS repack applies Annex B conversion, which changes the coded bytes
themselves, so the digest changes with them. Canonicalizing that away would need
a per-codec bitstream parser, which the standard-library core cannot host.

To find duplicates, group by `content_digest` and call `compare_for_duplicate`
on the members of a group. It compares the timing floats with a duration-scaled
tolerance and returns one of five verdicts -- duplicate, different timing,
timing unknown, unminted, or distinct. Consumers do not reimplement that
comparison; a tolerance test written downstream is a tolerance test written
wrongly, which is why it is exported.

The payload read is the cost: the scan now reads every packet's payload rather
than only its header, measured at roughly 1.5x to 2x the previous scan on a
large file (provisional, pending measurement on a real corpus). It is paid once,
at the ingestion probe. `-show_data_hash` needs no newer ffprobe than the
package already requires -- it shipped in FFmpeg 2.4, well below the 5.1 runtime
floor.

Every probe also records `identity_scheme` and `prober_version` on `MediaFacts`:
the declared scheme version that minted `video_uuid` and `content_digest`, and
the ffprobe build whose demuxer output the digest is defined against. Neither
is hashed -- they are provenance, not content -- and they are what lets a
consumer tell a re-mint under a later scheme apart from a file whose content
actually changed. See "Versioning" for how `identity_scheme` relates to the
package's own release number.

The two format tags are internal constants and are deliberately not exported. A
consumer reading a format tag is reimplementing the digest; `IDENTITY_SCHEME` is
the opposite case, a recorded fact a consumer compares against a stored one.


## License

Apache License 2.0 -- see [LICENSE](LICENSE).

The package shells out to the system `ffmpeg`/`ffprobe` binaries and, for the
`[io]` extra, uses PyAV. Those components are not distributed with this package
and carry their own licenses (FFmpeg is LGPL-2.1-or-later, or GPL if built with
GPL-only components; PyAV is BSD-3-Clause); redistributors who bundle them must
observe those licenses independently.
