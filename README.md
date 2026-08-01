# mosaic-media

Media probing, transcode planning, and frame reading. Distribution name
`mosaic-media`, import name `mosaic_media`. Python 3.12 or newer, Apache-2.0.

Source: <https://github.com/EcodylicScience/mosaic-media>

The package answers two questions about a video file, then constructs and runs
the ffmpeg work that follows from the answers:

1. Does it play well in a browser, and does it scrub quickly?
2. Is it usable for per-frame analyses like tracking?

A file can need a transcode for one and not the other, so the two verdicts are
independent. What counts as playable is supplied by the caller, not decided
here.


## Installation

```bash
pip install mosaic-media          # probe, verdict, transcode, thumbnails
pip install "mosaic-media[io]"    # + in-process frame reader and writer
pip install "mosaic-media[cli]"   # + the mosaic-media command line app
```

`ffmpeg` and `ffprobe` must be on `PATH` for the probe, the transcode, and the
CLI: version 5.1 or newer at runtime (`-fps_mode`), 6.0 or newer to run the test
suite (`-display_rotation`). The `[io]` reader decodes in process and needs no
ffmpeg binary.

Platforms: developed and tested on Linux. Nothing is platform-specific except
hardware encoding, which requires the relevant ffmpeg encoder and a device.


## Quick start

Probe once. `MediaFacts` is the authoritative metadata from then on: pass it to
everything downstream rather than re-measuring, because a file that is already
clean is never rewritten and so cannot be assumed to have canonical bytes.

```python
from mosaic_media import CHROME_149, DEFAULT_THRESHOLDS, derive, probe_media

facts = probe_media("recording.mp4")
print(facts.frame_count, facts.fps, facts.video_uuid)

verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
print(verdict.analysis_transcode, verdict.stream_transcode)
print(sorted(verdict.analysis_reasons), sorted(verdict.stream_reasons))
```

Both policy objects are yours to supply. `CHROME_149` is the one profile
shipped, measured against Chrome 149 on Linux. Describe a different target by
constructing your own:

```python
from mosaic_media import PlaybackProfile, Thresholds

profile = PlaybackProfile(
    # ffprobe format_name strings, never file extensions
    containers=frozenset({"mov,mp4,m4a,3gp,3g2,mj2"}),
    codecs=frozenset({"h264", "av1"}),
    client_dependent_codecs=frozenset({"hevc"}),  # plays only on some clients
    baseline_pixel_formats=frozenset({"yuv420p"}),
)
thresholds = Thresholds(drift_frame_periods=0.5)  # the rest keep defaults
```

`Thresholds` carries every numeric limit the verdict applies, and
`DEFAULT_THRESHOLDS` is `Thresholds()`: `drift_frame_periods=0.5` (how far a
frame may sit from its uniform-grid position before the file counts as variable
rate), `max_gop_bytes=524288`, `max_keyframe_interval_frames=200`,
`truncation_duration_ratio=0.95`, `start_time_frame_periods=0.5`.

Run the work the verdict calls for:

```python
from mosaic_media.transcode import ANALYSIS_ENCODING, run_transcode

result = run_transcode(
    "recording.mp4", "derived.mp4", "analysis", facts, verdict,
    profile=CHROME_149, thresholds=DEFAULT_THRESHOLDS,
    encoding=ANALYSIS_ENCODING,
)
if result.performed:
    print(result.operation, result.output_path)
    print(result.output_facts.video_uuid, result.source_video_uuid)
```

`run_transcode` accepts `on_progress` and `cancel_check` callbacks and a
`timeout`. It returns without writing when the verdict asks for nothing.

Read frames, with the `[io]` extra:

```python
from mosaic_media.io import VideoReader

# A no-op transcode writes nothing, so read the source in that case.
path = result.output_path if result.performed else "recording.mp4"
known = result.output_facts if result.performed else facts

with VideoReader(path, facts=known) as reader:
    for index, frame in reader:   # frame index, then a BGR numpy array
        ...
```

`output_path`, `output_facts`, and `output_verdict` are `None` on a no-op;
`performed` is what distinguishes the two branches, and `source_video_uuid` is
populated either way because it describes the input. Passing `facts` skips a
probe the caller has already paid for.
`MultiVideoReader` does the same across a list of files, presenting them as one
sequence.

Everything in the core is importable from `mosaic_media` itself; the two extras
keep their own namespaces, `mosaic_media.transcode` and `mosaic_media.io`.

Compare two files for duplication:

```python
from mosaic_media import compare_for_duplicate

print(compare_for_duplicate(facts, probe_media("other.mp4")).verdict)
```

Thumbnails need no extra:

```python
from mosaic_media import (
    downscale_to_jpeg, extract_first_frame, thumbnail_dimensions,
)

width, height = thumbnail_dimensions(facts.width, facts.height, cap=320)
extract_first_frame("recording.mp4", "frame.png")
downscale_to_jpeg("frame.png", "thumb.jpg", width=width, height=height)
```

`thumbnail_dimensions` returns the `(width, height)` that fits the long edge to
`cap` while preserving aspect.


## Layering and optional dependencies

| Extra | Adds | Contents |
| --- | --- | --- |
| `mosaic-media` | standard library only | Probe, verdicts, transcode command construction and converter, thumbnails, capability probing. |
| `mosaic-media[io]` | `numpy>=1.22`, `av>=18,<19` | In-process frame reader, seek index, multi-video reader, video writer. |
| `mosaic-media[cli]` | `typer>=0.12` | The `mosaic-media` command line app. |

The core is standard library only so the transcode runner can start on a machine
that has ffmpeg and nothing else: a minimal container, or a recording box
without an analysis stack. A test enforces it, so installing the core pulls
neither numpy nor typer.


## Decoding

The probe and the transcode run through the system `ffmpeg` binaries; the CLI is
the terminal interface to both. The probe decodes no frame at all -- it reads
demultiplexed packets, and returns each one's time, size, and keyframe flag in
decode order. Only the `[io]` extra decodes and encodes in process, through
`av`.

That packet index is what makes seeking exact. `VideoReader` resolves the
target's preceding keyframe from the index, seeks the container to that
keyframe's timestamp, verifies the decoded landing, and counts frames forward,
using each frame's own recorded timestamp throughout.

Neither path uses OpenCV, for two reasons that matter if you are replacing a
`cv2.VideoCapture` decode path:

- `CAP_PROP_POS_FRAMES` converts a frame index to a timestamp through one
  average rate, which is exact at constant rate and inexact otherwise.
- Current `opencv-python` wheels decode zero frames from an AV1 file while
  reporting a plausible frame count and raising nothing, and the derivatives
  this package produces are AV1 (`tests/io/test_reader_cv2_equality.py`).

By default `av` installs as a wheel carrying its own FFmpeg build, so the
reader's codec table is that build's rather than the system one. Where a single
decode stack matters, `pip install av --no-binary av` builds `av` against the
system libav, after which the reader and the subprocess paths share one FFmpeg.

A performance gate measures the reader against `cv2.VideoCapture` on metadata
open, sequential decode, strided decode, and cold random seek, among others. It
is excluded from the default suite and run explicitly (`pytest -m bench`), on an
idle machine, because contended measurements swing enough to make the comparison
meaningless.

A `VideoReader` is not thread-safe: it holds an open decoder with position
state. Use one per thread.


## Transcode semantics

Two targets, one codec, differing in rate control, in whether audio is kept, and
in whether they run at all. The analysis derivative carries no audio track and
no keyframe-interval cap; the playback derivative keeps audio and caps the
interval so scrubbing does not decode long runs. The original is preserved in
every case; derivatives are separate artifacts.

| Target | Trigger | `stream_transcode` |
| --- | --- | --- |
| Analysis | Any analysis reason: variable frame rate, unreliable timing metadata, rotation, non-square pixels, interlacing. | not applicable |
| Playback | A hard stream reason: the browser's rendering would disagree with the coordinate or time model -- unsupported container or codec, variable frame rate, rotation, non-square pixels, non-zero start time. | `required` |
| Playback | A soft stream reason: it plays correctly, but not well or not everywhere. | `recommended` |

Which containers and codecs count as supported is the caller's
`PlaybackProfile`, not a constant here. `Verdict.stream_transcode` is
`Literal["required", "recommended"] | None` and `HARD_STREAM_REASONS` is the set
that makes it `required`; `Verdict.analysis_transcode` is
`Literal["required"] | None`, because a defect that invalidates per-frame
results has no advisory tier.

The verdict selects the minimum operation rather than a blanket re-encode. A
header that lies about timing gets a `-c copy` remux with corrected timestamps,
a tail `moov` gets `-movflags +faststart`, a supported stream in an unopenable
container gets a rewrap, and only a defect in the pixel grid or the frame clock
gets a re-encode. Encoding runs on SVT-AV1, or on NVENC when the caller permits
hardware and ffmpeg lists `av1_nvenc`. A listing proves the encoder was compiled
in, not that a usable device is present, so permitting hardware on a machine
without one fails at encoder startup rather than falling back.

Variable frame rate is the hard measurement. ffprobe and OpenCV both report it
for containers that merely quantize timestamps to milliseconds, so the probe
instead fits a uniform grid across all frame timestamps and measures the worst
deviation in frame periods, against the drift limit in `Thresholds`. The fit
covers the whole file: a bounded window misclassifies recordings that drop
frames partway through rather than at the start.

The transcoded output is re-probed as its acceptance test, since a variable-rate
source resampled to a constant rate can still carry residual drift, and that
probe mints the derivative's authoritative `MediaFacts`. If the output's own
verdict is not clean the converter raises rather than retrying: the input is
unchanged and the command is unchanged, so a second attempt has nothing new to
work with.

A transcode changes the pixels and therefore every measured fact, so the
derivative's identity shares nothing with its source's and no hash recovers the
link. `TranscodeResult` carries `source_video_uuid` for that reason -- the
source's `video_uuid`, recorded on the result, including on the no-op branch
where it describes the input either way.


## Video identity

The probe derives two values per file, both `MediaFacts` fields, so they travel
with the rest of the metadata and are minted once. They answer different
questions and are not interchangeable.

| | `video_uuid` | `content_digest` |
| --- | --- | --- |
| Pins | coded content and exact timing | coded content only |
| Survives | faststart, moov relocation, a tag edit, a same-container repack | all of those, plus an mp4/mkv repack |
| Changes on | a re-encode, a retime, a truncation, a container change | a re-encode, a truncation, a bitstream reframing |
| Use for | naming, hash chains, derived paths, cache keys | duplicate candidate lookup |
| Never use for | duplicate detection | naming, or anything a chain consumes |

Both are built from a per-packet payload hash the packet scan reads through
ffprobe's `-show_data_hash`, not from a decoded frame and not from a file
checksum. `content_digest` hashes the codec-level facts and every packet's size,
keyframe flag, and payload hash; `video_uuid` folds the packet timestamps on top
and is emitted as an RFC 9562 UUIDv8. The payload hash is CRC32, which guards
against accidental collision, not against a crafted one.

`video_uuid` is the only value safe to compare for identity or to name anything.
Because it hashes the timestamps, **a container change moves it, and a directory
named from it is not recoverable across one -- not even by repacking back**:
Matroska quantizes timestamps to milliseconds and MPEG-TS rebases onto the first
program clock reference, and returning to the original container inherits that
quantization rather than undoing it.

`content_digest` survives those container rewrites, but only where they preserve
the elementary stream. It is not invariant across a bitstream reframing: an mp4
to MPEG-TS repack applies Annex B conversion, which changes the coded bytes
themselves, so the digest changes with them.

To find duplicates, group by `content_digest` and call `compare_for_duplicate`
on the members of a group. It compares the timing with a duration-scaled
tolerance -- a longer file is allowed proportionally more absolute drift -- and
returns one of five verdicts: duplicate, different timing, timing unknown,
unminted, or distinct. It is exported so the tolerance rule has one
implementation rather than one per caller.

Reading a packet's payload is what this costs: the scan reads every packet's
bytes rather than only its header, once, at the probe.

`identity_scheme` and `prober_version` are recorded on every probe -- the scheme
version that minted the two values, and the ffprobe build whose demuxer output
the digest is defined against. Neither is hashed.

The digest folds in each packet's payload hash as libavformat hands it over, so
an ffmpeg upgrade is the one event outside your control that could re-mint
stored values. Measured, it does not: both values are byte-identical across
FFmpeg 6.1, 7.1, and 8.1 for every committed fixture, including raw elementary
streams. `tests/probe/test_identity_across_ffmpeg_builds.py` re-checks it
whenever alternate builds are configured, and `prober_version` is what makes a
future re-mint detectable rather than silent.


## Versioning

Semantic versioning, with the pre-1.0 convention that a minor bump is a breaking
change and a patch bump is compatible. Pin a range
(`mosaic-media>=0.2.0,<0.3.0`); a bare floor would not exclude the next breaking
release. Release notes are on the repository's releases page.

`IDENTITY_SCHEME` is a second, independent number. It moves only when the bytes
hashed into `video_uuid` or `content_digest` change, and a move re-mints every
stored value. The two are coupled in one direction only: **a scheme bump always
forces a breaking version bump, and a version bump never implies a scheme
bump.** A pinned range therefore protects stored identity values.


## Command line

The `[cli]` extra installs a typer application, usable standalone or mounted as
a sub-application:

```python
import typer
from mosaic_media.cli import app as media_app

app = typer.Typer()
app.add_typer(media_app, name="media")
```

```bash
mosaic-media probe video.mp4
mosaic-media transcode video.mp4 --target playback --output media/
mosaic-media compare left.mp4 right.mp4
```

`--output` takes a file path or an existing directory, in which case the
filename derives from the source stem and is always `.mp4`. The converter
refuses to overwrite the source and refuses a file destination not ending in
`.mp4`; re-running the same transcode replaces its output atomically. The
package knows no dataset layout; where originals and derivatives live is the
caller's decision.

`compare` prints the `DuplicateComparison` as JSON, and its verdict is also the
exit code, so it works as a shell test without parsing stdout:

| Code | Meaning |
| --- | --- |
| 0 | duplicate |
| 1 | a probe failed on either file |
| 3 | distinct |
| 4 | different timing |
| 5 | timing unknown |
| 6 | unminted (neither file carries identity values) |

Exit 2 is left to the CLI framework's usage error, so a mistyped option is never
mistaken for a verdict. `--fps-tolerance` and `--duration-tolerance` override
the derived defaults; the left file is the reference the tolerances are computed
from.

For programmatic use prefer the Python API: structured exceptions, no argv
escaping, no output parsing.


## Errors

Two exception types, both `RuntimeError` subclasses.

`MediaProbeError` comes from the probe: no video stream, a missing file, or
ffprobe failing or returning output that cannot be parsed.

`TranscodeError` comes from the converter: ffmpeg failing, the run exceeding its
timeout, a cancel callback asking it to stop, a destination that is refused, or
the output failing its acceptance probe.


## Container image

`Dockerfile` assembles a prebuilt LGPL FFmpeg -- a third-party release pinned by
release tag and asset digest -- and compiles PyAV against it rather than against
the FFmpeg its own wheel bundles. The FFmpeg stage rejects a download whose
configuration carries `--enable-gpl`, `--enable-libx264`, or `--enable-libx265`;
the test and runtime stages then run `scripts/verify-ffmpeg-lgpl.py`, which
inspects the library the process actually links and fails if a GPL encoder is
reachable or if the CLI on `PATH` is a different build from that library. Each
is a build step, so a stage that fails its gate produces no image. The test stage
also runs the suite and pins video identity against that exact FFmpeg.
`--target ffmpeg` yields the gated prefix with the verifier installed but not yet
run -- that stage has no PyAV to run it against -- and `--target runtime` adds
PyAV on top.


## License

Apache License 2.0 -- see [LICENSE](LICENSE).

The package shells out to the system `ffmpeg` and `ffprobe` binaries and, for
the `[io]` extra, uses `av`. Those components are not distributed with this
package and carry their own licenses: FFmpeg is LGPL-2.1-or-later, or GPL if
built with GPL-only components, and `av` is BSD-3-Clause.

Nothing in the distributed package names a GPL-only encoder. `av` links FFmpeg
into the calling process, so an encoder named here would become a dependency of
this package; the default `av` wheel also carries its own FFmpeg build, whose
codec set is chosen by whoever built the wheel rather than by this package.

Redistributors bundling any of these are combining separately licensed works and
should establish their own obligations rather than relying on this summary.
