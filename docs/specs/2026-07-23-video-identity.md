# Video identity

The probe gains two derived values per file: an identity that never merges
distinct videos, and an index key for finding near-duplicates. They answer
different questions and are not interchangeable.

Both are minted once, by the probe at ingestion, and travel forward as facts.
The duplicate pathway compares stored values and never re-probes.

No frame is decoded. Both values come out of the packet scan the probe already
runs -- but that scan now reads every packet's payload, which it previously did
not. See "Cost".


## The two values

| | `video_uuid` | `content_digest` |
| --- | --- | --- |
| Type | RFC 9562 UUIDv8 string | 32-character hex string |
| Pins | coded content and exact timing | coded content only |
| Same across | faststart, moov relocation, tag edit, same-container repack | all of those, plus an mp4/mkv repack |
| Differs across | re-encode, retime, truncation, **container change** | re-encode, truncation, bitstream reframing |
| Use for | naming, hash chains, derived paths, cache keys | duplicate candidate lookup |
| Never use for | duplicate detection | naming, identity, anything a chain consumes |

`video_uuid` hashes every packet timestamp. A same-container rewrite leaves
those timestamps byte-identical, so faststart, moov relocation, a tag edit, and
a plain `-c copy` repack all preserve it. A container change does not: Matroska
quantizes to milliseconds and MPEG-TS rebases onto the first program clock
reference.

**A container change therefore changes `video_uuid`, and a directory named from
it is not recoverable across one -- not even by repacking back**, because the
quantization is inherited rather than undone. Measured on a 60-second clip,
hashing every packet timestamp:

| Rewrite | `video_uuid` timestamps | `content_digest` |
| --- | --- | --- |
| faststart | unchanged | unchanged |
| plain `-c copy` mp4 repack | unchanged | unchanged |
| tag edit | unchanged | unchanged |
| mkv repack | changed | unchanged |
| mkv back to mp4 | changed, and not restored to the original | unchanged |
| TS repack | changed | changed |

That is the deliberate split: `video_uuid` never merges things that differ,
`content_digest` survives the rewrites that do not change content, and the
duplicate pathway is how a repacked file is recognized.

`video_uuid` is derived from `content_digest`, so equal uuids imply equal
digests. The digest is the weaker test and the wider net.

Only `video_uuid` is named `uuid`. Nothing named `uuid` in this package is
safe-to-collide, so the wrong field cannot quietly end up naming a directory.

### What `content_digest` does not survive

A repack that reframes the elementary stream changes the coded bytes
themselves, so no digest over those bytes can be invariant to it. Converting
mp4 to MPEG-TS applies Annex B conversion: measured on a 60-second clip, packet
sizes go 3906/486/69 to 3948/492/75 and the total grows from 266879 to 277961
bytes. Canonicalizing that away needs a per-codec bitstream parser, which the
standard-library-only core cannot host.

The guarantee is therefore: invariant across container rewrites that preserve
the elementary stream, which covers mp4 to mkv and back, faststart, moov
relocation, and tag edits. Not invariant across mp4 to TS.


## The two pathways

**Identity.** Use `video_uuid` by equality. Nothing else.

**Duplicates.** Never use `video_uuid`. Group by `content_digest` (exact,
indexable), then call `compare_for_duplicate` on the group's members.

Consumers do not implement this comparison. It is exported from the package
facade because a tolerance test reimplemented downstream is a tolerance test
implemented wrongly.

```python
def compare_for_duplicate(
    left: MediaFacts,
    right: MediaFacts,
    *,
    fps_tolerance: float | None = None,
    duration_tolerance: float | None = None,
) -> DuplicateComparison: ...
```

Takes `MediaFacts` rather than loose digests and floats: the digest and the
floats it qualifies must come from the same probe, and a signature that accepts
them separately allows pairing a digest with another file's timing.

Each `*_tolerance` is an absolute value in the compared unit -- frames per
second, seconds. `None` derives it from `timing_tolerance`, which is the correct
default for every caller with no specific reason to override.

Decision order:

| Step | Condition | Verdict |
| --- | --- | --- |
| 0 | either `content_digest` is empty | `"unminted"` |
| 1 | `content_digest` differs | `"distinct"` |
| 2 | either `timing_measured` is `False`, or `left.duration` is not positive | `"timing_unknown"` |
| 3 | `fps` and `duration` both within tolerance | `"duplicate"` |
| 4 | otherwise | `"different_timing"` |

**Step 0 is not defensive programming; without it this function reports false
duplicates.** An absent `content_digest` is the empty string, and two facts that
both carry `""` compare *equal* at step 1, pass step 2 whenever both were built
with `timing_measured=True`, and reach the timing comparison -- where two
unrelated recordings at the same rate and length report `"duplicate"`.

The field is required rather than defaulted precisely so absence cannot arrive
by accident, but it does not remove the state. A consumer that hand-builds
`MediaFacts` for a source the probe never saw -- an image-store recording, a
directory of frames with no file to hash -- has no digest to pass and states an
empty one. A pair of those clears steps 1 and 2 together, so the guard is what
stands between them and a false `"duplicate"`.

`"unminted"` rather than `"distinct"`: the two files may well be the same, and
saying `"distinct"` claims knowledge the function does not have. The verdict is
also actionable -- re-probe the side that lacks a digest -- which `"distinct"`
is not. It is the same shape as `"timing_unknown"`: the comparison could not be
performed, and the caller is told which input to fix.

`left` is the reference: both tolerances are computed from `left`, so the
comparison is not symmetric when the two durations differ. Callers iterating a
group hold `left` fixed.

Step 2 is a guard, not a comparison. When `timing_measured` is `False` the
floats are `0.0` placeholders, so a tolerance test on them is meaningless and
step 3's division by `duration` is undefined. `"timing_unknown"` means same
coded content, timing unknown on at least one side.

The duration clause guards that division directly rather than relying on the
flag to imply it. For facts this package produces the two are equivalent --
`measure_timing` requires two distinct timestamps, so a positive duration is
guaranteed wherever `timing_measured` is `True`. They come apart in a consumer:
`timing_measured` defaults to `True`, and a consumer reconstructing facts from
persisted columns without that one sets it `True` regardless of what was
probed. Once such a consumer persists `content_digest` -- which it will, the
field costing no schema change -- a pair of untimed rows would clear steps 0
and 1 and divide by zero. Guarding the division makes the function total over
every `MediaFacts` a caller can legitimately construct, which is what an
exported "only supported way to ask" has to be.

`"different_timing"` means same coded content at a different rate. That is a
distinct video, not a duplicate.

`start_time` is deliberately not compared. It is a container property, not a
content property: an mp4-to-TS remux moves it by 1.47 seconds on a clip whose
content is unchanged, because TS starts its clock at the first program clock
reference. Comparing it would contradict the invariance `content_digest`
already establishes.

```python
DuplicateVerdict = Literal[
    "duplicate", "different_timing", "timing_unknown", "unminted", "distinct"
]


@dataclass(frozen=True, slots=True)
class DuplicateComparison:
    verdict: DuplicateVerdict
    fps_delta: float | None
    duration_delta: float | None
    fps_tolerance: float | None
    duration_tolerance: float | None
```

Deltas are absolute differences; tolerances are the values actually applied,
derived or overridden. Both are `None` for `"unminted"`, `"distinct"`, and
`"timing_unknown"`, where no comparison ran. Carrying the applied tolerance alongside the delta
makes a verdict self-explaining, for the same reason the comparison is not left
to consumers.


## Tolerance

```python
def timing_tolerance(value: float, duration: float) -> float:
    """The absolute tolerance for `value` on a file of `duration` seconds."""
    return DRIFT_SAFETY * value * TIMESTAMP_QUANTUM_SECONDS / duration
```

Exported alongside `compare_for_duplicate`, so a caller overriding a tolerance
can see what it is overriding.

Applied as:

| Compared | Call | Result |
| --- | --- | --- |
| `fps` | `timing_tolerance(left.fps, left.duration)` | scales with rate and inversely with length |
| `duration` | `timing_tolerance(left.duration, left.duration)` | a flat `DRIFT_SAFETY * TIMESTAMP_QUANTUM_SECONDS`, 4 ms |

| Constant | Value | Why |
| --- | --- | --- |
| `TIMESTAMP_QUANTUM_SECONDS` | `0.001` | Coarsest timestamp granularity in common containers (Matroska millisecond scale). |
| `DRIFT_SAFETY` | `4.0` | Roughly 10x margin over measured drift. |

The returned value is absolute, but derived per file rather than fixed. The
drift is quantization noise averaged over the file, so it scales as
`quantum / duration`: one fixed number for the whole corpus is too tight for
long files or too loose for short ones. At 30 fps the derived tolerance is
0.12 fps on a 1-second clip and 0.002 fps on a 60-second one.

Measured basis. Each row is a `testsrc` clip and its `-c copy` Matroska repack,
the worst case among the rewrites tested. The 60-second row was additionally
measured across faststart, TS, and the mkv-to-mp4 round trip: faststart and TS
reproduced the source's rate exactly, while the round trip reproduced the
*mkv's* rate, not the source's -- the millisecond quantization is inherited and
is not undone by returning to the original container.

Relative drift is `abs(fps - reference) / reference`; the tolerance is absolute,
so both are shown. The tolerance column uses the *measured* duration, which is
not the nominal one (a nominal 5 s clip measures 5.005 s), so the arithmetic is
reproducible from this table alone.

| Clip | Rate | Measured duration | Relative drift | Absolute drift | Derived tolerance | Margin |
| --- | --- | --- | --- | --- | --- | --- |
| 1 s | 30000/1001 | 1.001 s | 3.79e-4 | 0.011363 fps | 0.119760 fps | 10.5x |
| 2 s | 30000/1001 | 2.002 s | 1.86e-4 | 0.005586 fps | 0.059880 fps | 10.7x |
| 5 s | 30000/1001 | 5.005 s | 7.38e-5 | 0.002212 fps | 0.023952 fps | 10.8x |
| 60 s | 30000/1001 | 60.026634 s | 4.45e-6 | 0.000133 fps | 0.001997 fps | 15.0x |
| 2 s | 25 | 2.0 s | 0 | 0 | 0.05 fps | n/a (no drift) |

Maximum observed duration drift was 0.00038 s against the flat 0.004 s
tolerance, the same 10x margin.

Two limits, both open:

- Synthetic fixtures on a single ffmpeg build. `DRIFT_SAFETY` is provisional
  until re-measured across the real corpus.
- Below 4 seconds the derived `fps` tolerance exceeds the 0.02997 gap between
  29.97 and 30, so those rates are not separable at that length. The crossover
  is exact: `4.0 * 29.97003 * 0.001 / d = 30 - 29.97003` gives `d = 4.000 s`.
  This affects only the duplicate pathway's "remux or retime" question, never
  `video_uuid`.


## What is hashed

`hashlib.blake2b(digest_size=16)`, no key, salt, or person parameter.

### Payload comes from the demuxer, not from file offsets

The scan reads each packet's payload hash from ffprobe rather than reading
bytes at the recorded byte offset. Byte offsets are container-relative and do
not point at the payload: in Matroska the offset addresses the SimpleBlock
element, whose header (track-number varint, timecode, flags) precedes the
payload. Measured at 4 bytes on a single-byte track number, with identical
`size` on both sides and different bytes at the offset; a two-byte track number
and lacing each move it further, which follows from the element layout but was
not measured. Reading at the offset therefore mixes container header bytes into
the digest and breaks the mp4-to-mkv invariance this value exists for.

The packet scan gains `-show_data_hash` and a `data_hash` entry, so the value
arrives in the call already being made. No second subprocess, no seeking, no
dependence on byte offsets being exposed at all.

The algorithm is `CRC32`, the flag's cheapest option, chosen on the collision
budget rather than on cryptographic strength (see "Collision budget"). Measured
on the 481 MB file, best of three, against a 0.97 s baseline scan with no
hashing:

| Algorithm | Total | Overhead |
| --- | --- | --- |
| CRC32 | 1.74 s | +0.77 s |
| MD5 | 2.32 s | +1.35 s |
| SHA512 | 3.25 s | +2.28 s |
| SHA256 | 4.20 s | +3.23 s |

A stronger algorithm buys resistance to deliberate forging, a different property
from the one this value promises. `-show_data_hash` accepts any of these through
the same flag if the threat model changes; that is a format version bump.

### The digest is defined against demuxer output

This is the cost of dropping file-offset reads. Raw file bytes are stable
forever; `data_hash` covers what libavformat hands ffprobe, which is a function
of the libavformat version. A future default bitstream filter, a change in
in-band parameter-set handling, or a demuxer bug fix would move every digest for
the affected container -- and these values are minted once and become directory
names.

Treat an ffmpeg upgrade that changes demuxer output as a format break: bump the
format tag and re-mint. The alternative, recording the producing ffprobe version
alongside the digest, is not specified here but is the obvious extension if
re-minting a corpus ever becomes unacceptable.

`-show_data_hash` imposes no new version floor. It was added by commit
`4f3e2f10` ("ffprobe: add -show_data_hash option"), authored 2014-04-21 and
committed 2014-08-17 -- a month after 2.3 released, which is why it first
shipped in **FFmpeg 2.4**. Verified by source: the string is absent from
`ffprobe.c` at tags `n2.2` and `n2.3` and present at `n2.4`.

The README's existing runtime floor is ffprobe 5.1, for `-fps_mode`, seven
years later. Every build this package already requires therefore has the flag,
and the README records it as covered rather than as a new requirement.

The parse guard stays regardless. It costs nothing and it names the one failure
that remains reachable: a build that accepts the flag but does not report the
`data_hash` entry, which ffprobe drops silently rather than rejecting.

### Zero-length payloads

A packet whose payload is zero bytes is hashed exactly as scanned, whatever
ffprobe prints for its `data_hash`. No special case.

This is worth stating because the two scanners disagree: `io/packets.py` skips
`packet.size == 0` (the demuxer flush packet) while `probe/ffprobe.py` does not,
since `"0".isdigit()` is true. Across ten containers -- mp4, mkv, webm/VP8,
mkv/VP9, AVI/MJPEG, AVI without PTS, raw h264, mp4/AV1, mkv/AV1, and TS -- no
such packet was produced, so the case is unobserved rather than impossible; AVI
permits a zero-length chunk for a dropped frame. The rule exists so the first
implementer does not have to invent one.

### Encoding primitives

Fixed so the input bytes are reproducible across processes, interpreters, and
operating systems -- not merely the output. Every element is framed by exactly
one of these rules:

| Kind | Encoding |
| --- | --- |
| integer | `struct.pack("<q", value)` |
| boolean | `b"\x01"` or `b"\x00"` |
| float | `struct.pack("<d", value)` |
| string | `struct.pack("<I", len(raw))` then `raw`, `raw = value.encode("utf-8")` |
| bytes | `struct.pack("<I", len(value))` then `value` |

Every variable-length element is length-prefixed, including the format tag and
each packet run. There is no raw, unframed element anywhere in either input.

### `content_digest` input, in this order

1. `b"mosaic-media/content/1"` as bytes -- format tag and version.
2. Invariant facts as strings and integers, in this order: `codec_name`,
   `pixel_format`, `color_range`, `color_primaries`, `color_transfer`, `width`,
   `height`, `rotation_degrees`, `square_pixels`, `progressive`.
3. The packet count as an integer.
4. For every packet, in decode order: `size` as an integer, `keyframe` as a
   boolean, `data_hash` as a string.

The `data_hash` string is hashed whole, algorithm prefix included
(`"CRC32:a73878e0"`, not `"a73878e0"`). Changing the algorithm therefore changes
every digest rather than silently producing an incomparable one that looks
comparable.

Excluded, each for a stated reason:

| Excluded | Reason |
| --- | --- |
| `fps`, `duration`, `start_time`, `max_instantaneous_fps` (`float \| None`) | Measured from timestamps. A remux into a different timebase requantizes them. They belong to the tolerance comparison instead. |
| `frame_count` | Timestamp-derived: `len(sorted({packet.time ...}))`, the count of distinct timestamps, and `len(packets)` instead when `timing_measured` is `False`. Two meanings in two modes, and redundant with the packet count in step 3. |
| `constant_frame_rate` | Derived from timestamps; can flip near the threshold on remux. |
| `max_keyframe_interval_frames`, `max_gop_bytes` | Redundant with the per-packet run, which is hashed in full. |
| `container`, `declared_duration`, `declared_fps`, `declared_frame_count`, `moov_at_start` | Header claims a rewrite legitimately changes. |
| `has_audio`, `video_stream_count` | Not the video stream's content. Stripping or re-encoding audio keeps the digest. |
| `timing_measured` | Timing metadata, not content. Enters `video_uuid` instead. |
| Packet timestamps | Requantized on remux. Enter `video_uuid` instead. |

Every field of `MediaFacts` appears in step 2 or in this table, besides the two
this spec adds.

### `video_uuid` input, in this order

1. `b"mosaic-media/video/1"` as bytes -- format tag and version.
2. The 16 raw `content_digest` bytes.
3. `timing_measured` as a boolean.
4. The packet count as an integer.
5. `time` for every packet, in decode order, as a float.

The 16 output bytes become a UUIDv8 by setting the version and variant bits by
hand -- byte 6 to `(b & 0x0F) | 0x80`, byte 8 to `(b & 0x3F) | 0x80` -- and then
`uuid.UUID(bytes=...)`. The `version=` keyword argument is not usable: CPython
accepts only versions 1 through 5 until 3.14, and this package's floor is 3.12.
Verified: `uuid.UUID(bytes=..., version=8)` raises `ValueError: illegal version
number` on 3.12.3.

122 hash bits are retained.


## Collision budget

| Source | Scope | Bound |
| --- | --- | --- |
| Digest collision | corpus-wide | 122 retained bits. Birthday bound about 9.4e-26 across a million files. |
| Payload aliasing | **pairwise** | Every packet's payload hash is folded in; there is no sampling, so there is no sampling-miss term. Two files collide only if every packet's payload hash collides: 2^-32 per packet. |

The two bounds have different scopes and must not be read together. The payload
term is pairwise, so across a corpus of N files that already agree on facts and
packet count it grows as `N^2 / 2 * 2^-32`.

The floor is set by the shortest file. A timed file cannot have fewer than two
packets: `measure_timing` raises when a stream carries fewer than two distinct
timestamps, and `probe_media` routes every timed stream through it, so a
single-packet mp4 is not probeable at all (verified: `MediaProbeError: too few
distinct packet timestamps to measure timing: 1`). Two packets give 2^-64, about
5.4e-20 pairwise.

The single-packet case is reachable only as an untimed raw elementary stream,
where the floor is 2^-32, about 2.3e-10 pairwise -- 43x inside the 1e-8 target
for a pair, but only about 1e-4 across a thousand such files. That exposure is
confined to single-packet raw elementary streams, which is a narrow category but
a real one here: tracking boxes record raw streams, which is why `raw_h264` is in
the fixture corpus. A corpus of single-frame raw streams is not a use this
package supports; anything longer is astronomically stronger.

**This is not a security primitive.** The payload hash is a checksum, not a
collision-resistant hash, and a party who can craft input files can produce a
colliding pair deliberately. The guarantee stated here is against accidental
collision between independently produced recordings.


## Surface

`MediaFacts` gains two required fields:

```python
video_uuid: str
content_digest: str
```

**Required, not defaulted.** A default would exist to let facts persisted before
the fields round-trip unchanged, and consumers are migrating rather than staying
backward compatible, so nothing needs that. What a default would cost is
concrete: an absent digest would become indistinguishable from a never-set one,
because the field would silently fill itself in at every construction site that
forgot it. Requiring it makes absence something a caller states.

Absence remains a real state -- an image-store recording is a directory of
frames with no file to hash, so the consumer that hand-builds facts for one
passes an explicit empty digest. That is why the duplicate pathway still needs
its `"unminted"` verdict. The difference is that the empty value is now written
down at the one site where it is true, instead of arriving by default everywhere.

Every construction site must pass both. In this package that is `probe_media`
and the verdict tests. In the consumers it is the image-store fact builder and
each path that rebuilds facts from persisted columns -- which is the point:
`MediaFacts(**payload)` over a blob written before these fields now raises
rather than silently producing a fact that claims an identity it never had.

`Packet` gains `data_hash: str = ""`. It is defaulted because
`io/packets.py`'s `scan_packets_in_process` mirrors the probe's `scan_packets`
and does not populate it: identity is minted once by the probe at ingestion and
never by the reader, so the io layer has no reason to pay for it. That
asymmetry is deliberate and belongs in the field's docstring, not left for a
reader to discover.

`scan_packets` adds `-show_data_hash` and `data_hash` to its `-show_entries`
list. The CSV gains a sixth column at index 5, formatted `ALGO:hexdigest`; the
row-length guard moves from 5 to 6. The inline comment above the parse loop
names the five requested entries in ffprobe's natural order and must be updated
with them.

MPEG-TS is the one seven-column case: an empty `[SIDE_DATA]` subsection prints
after `data_hash`, so index 5 is unaffected and a "fewer than six" guard admits
the row correctly.

New module `src/mosaic_media/probe/identity.py`: `hashlib`, `struct`, `uuid`,
`dataclasses`, and `typing.Literal` only -- all standard library, so the core
layer is unchanged. It hosts the minting functions, `compare_for_duplicate`,
`DuplicateComparison`, `DuplicateVerdict`, and `timing_tolerance`.
`probe_media` calls it after `scan_packets` and fills the two fields.

`tests/test_import_guard.py` pins the core with an explicit literal module list
(`_CORE_IMPORTS`). Add `mosaic_media.probe.identity` to it: a new core module
absent from that list is never guarded, and the test passes while proving
nothing about it.

Exported from the `mosaic_media` facade, so no consumer reimplements the
comparison:

| Export | Kind |
| --- | --- |
| `compare_for_duplicate` | The duplicate pathway. The only supported way to ask whether two probed files are the same video. |
| `DuplicateComparison` | Its return type. |
| `DuplicateVerdict` | The `Literal` alias over its five verdicts. |
| `timing_tolerance` | The per-file absolute tolerance a caller would otherwise guess at. |
| `DRIFT_SAFETY`, `TIMESTAMP_QUANTUM_SECONDS` | The tolerance constants, named rather than inline. |

The format tags are module-level named constants in `identity.py`, not inline
literals, and are not exported: a consumer reading them is reimplementing the
digest.

`PAYLOAD_HASH_ALGORITHM` lives in `ffprobe.py` instead, beside the command that
uses it. It cannot live in `identity.py`: `scan_packets` needs it to build the
ffprobe invocation, and `identity.py` imports `Header` and `Packet` from
`ffprobe.py`, so importing the constant back would close an import cycle.
`identity.py` never needs it -- the payload hash arrives as a string with its
algorithm prefix already attached, and hashing that whole string is what makes
an algorithm change alter every digest.

### CLI

```
mosaic-media compare LEFT RIGHT
                     [--fps-tolerance FLOAT]
                     [--duration-tolerance FLOAT]
```

Probes both files and prints the `DuplicateComparison` as JSON on stdout,
matching the `probe` command's `indent=2, sort_keys=True` style. Each option
maps to the matching keyword argument; omitting one derives it.

This command probes, unlike the ingestion pathway which compares stored facts.
It takes paths for ad-hoc use; there is no cached-facts entry point on the CLI.

The verdict is also the exit code, so the command is usable as a shell test
without parsing stdout:

| Code | Meaning |
| --- | --- |
| 0 | `"duplicate"` |
| 1 | Probe failed on either file. Reserved for failure, matching the existing commands. |
| 2 | Not used. Reserved: `click.UsageError.exit_code` is 2, so a mistyped option already exits 2 and a verdict here would be indistinguishable from it. |
| 3 | `"distinct"` |
| 4 | `"different_timing"` |
| 5 | `"timing_unknown"` |
| 6 | `"unminted"`. Unreachable from this command, which probes both files and so always has digests, but the mapping is total so a future caller cannot fall off it. |

The `Typer` app help text and the `cli/application.py` module docstring both
name probing and transcoding only; both gain comparison.

### Transcode

`TranscodeResult` gains `source_video_uuid: str`, recorded from the input facts.
It is populated on the no-op branch too (`performed=False`, every optional field
`None`), because the input facts are in hand there and the field describes the
input, not the output.

No content hash can link a derivative to its source -- different pixels,
different facts -- so the transcode carries the edge and the identity module
only mints.

**It carries it in process only.** `video_uuid` and `content_digest` are
`MediaFacts` fields, so a consumer that persists facts as a serialized blob
stores them with no schema change. `source_video_uuid` is not: it lives on
`TranscodeResult`, and a consumer flattening a derivative into its media index
has no column for it, so the edge is minted and then dropped at the moment it
would become durable. Closing that is one consumer-side column, tracked in
`docs/issues/transcode-provenance-has-no-consumer-column.md`. Until it lands,
this field is available to a caller that reads the result object and nowhere
else -- do not describe the provenance edge as persisted.

### README

`README.md` is this repository's user-facing authority, so it gains the two
values, the two pathways, and the invariance limits. "Transcode semantics" gains
the identity split; "CLI composition" gains the `compare` command.


## Cost

The payload hash is computed inside the packet scan already being run, but it
makes that scan read every payload byte. Measured on a 481 MB, 216000-packet
file under a serialized lock with a warm page cache:

| Measurement | Baseline | With payload hashes | Ratio |
| --- | --- | --- | --- |
| best of 3, sequential arms | 0.97 s | 1.85 s | 1.91x |
| best of 5, interleaved arms | 1.48 s | 2.22 s | 1.50x |

**Provisional, like the drift constant.** Two serialized runs on one machine
disagree by 25%, and the interleaved run's slower baseline with a lower ratio is
the signature of an I/O-bound rather than CPU-bound measurement -- both runs
queued behind other sessions' builds. Take "between 1.5x and 2x the scan" as the
claim, re-measure on the real corpus, and expect a cold cache to be bounded by
read throughput rather than by either figure.

Three callers pay it, not one:

| Caller | Cost |
| --- | --- |
| `probe_media` at ingestion | Once per file. The intended cost. |
| `run_transcode` | Re-probes its output as the acceptance test, so every transcode pays it again on the derivative. |
| `mosaic-media compare` | Twice per invocation, once per file. |

`SCAN_TIMEOUT_SECONDS` is currently 900 and was sized for a scan that did not
read payload. Re-evaluate it before implementation.

The reader path is unaffected: `io/packets.py` demultiplexes in process and does
not call `scan_packets`.


## Tests

| Property | Test |
| --- | --- |
| Determinism | Same file probed twice in separate processes yields identical values. |
| Byte reproducibility | The digest input bytes match a golden vector, not only the output. Catches an encoding or ordering change that a same-machine round trip would hide. |
| Remux invariance | `content_digest` equal across mp4, mkv, mkv-to-mp4 round trip, and faststart. |
| Reframing limit | `content_digest` differs for the mp4-to-TS repack, asserted as specified behavior rather than left undefined. |
| Retime sensitivity | `-itsscale` output shares `content_digest` with its source and has a different `video_uuid`. |
| Container change moves the uuid | An mkv repack changes `video_uuid` while keeping `content_digest`; repacking back to mp4 does not restore the original `video_uuid`. |
| Same-container rewrite does not | faststart, a plain `-c copy` mp4 repack, and a tag edit each leave `video_uuid` unchanged. |
| Content sensitivity | Truncation changes `content_digest`. So does flipping one payload byte -- the fixture must locate `mdat` and flip inside it, because a byte in the header region does not change the digest and the identity code no longer computes byte offsets to borrow. |
| Identical fixtures agree | `cfr_mp4` and `faststart_mp4` are built from one source and differ only in `-movflags +faststart`; they must mint the same `video_uuid` and the same `content_digest`. |
| Distinctness | Fixtures that are genuinely distinct videos have distinct `video_uuid` values. Scoped to exclude the pair above. |
| Shortest probeable file | A two-packet timed file mints both values. A single-packet timed file is not probeable and must raise `MediaProbeError` from `measure_timing`, unchanged by this work. |
| Untimed stream | A raw elementary stream mints both values with `timing_measured=False`. |
| Duplicate pathway | All five verdicts: remuxed pair reports `"duplicate"`; retimed pair reports `"different_timing"`; a pair with either side untimed reports `"timing_unknown"`; unrelated pair reports `"distinct"`. |
| Unminted guard | Two facts that both carry an empty `content_digest`, both `timing_measured=True`, and matching `fps` and `duration` report `"unminted"`, not `"duplicate"`. This is the persisted-row and hand-built-facts shape; without step 0 it is a false duplicate. |
| Tolerance override | An explicit `fps_tolerance` flips a pair between `"duplicate"` and `"different_timing"`, and the derived default is used when it is `None`. |
| Comparison deltas | Deltas are populated for `"duplicate"` and `"different_timing"`, and `None` for `"distinct"` and `"timing_unknown"`. |
| Guard precedes division | A pair with `duration` `0.0` and `timing_measured` `False` returns `"timing_unknown"` rather than raising. |
| Tolerance property | Every remuxed pair compares `"duplicate"` under the derived tolerance. Asserted as a property, not as the measured floats: those come from one ffmpeg build's muxer rounding and a distro bump would fail a test that is not about this package's behavior. The measured table stays in this spec as recorded evidence. |
| CLI verdicts | `mosaic-media compare` exits 0, 3, 4, 5 on the four verdicts and 1 when either probe fails. |
| CLI tolerance options | `--fps-tolerance` reaches the keyword argument and changes the verdict and the exit code. |
| Import guard | `mosaic_media.probe.identity` is in `_CORE_IMPORTS` and imports with numpy, typer, cv2, and av each poisoned. |


## Out of scope

- Consumer persistence. Adding these fields to the backend's fact columns and
  backfilling existing rows is coordinated separately.
- Enforced uniqueness. A library may additionally require `content_digest` to be
  unique. That composes on top; `video_uuid` does not depend on it. The
  distinction is kept in the hash so the guarantee is a property of the bytes
  rather than of one library's enforcement -- directory names outlive and cross
  the boundary of any single library.
