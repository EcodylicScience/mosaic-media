# Every declared frame is delivered

## Problem

`MediaFacts.frame_count` counts the container's packet timestamps, while the
frame reader, the transcode output, and any external decoder a consumer runs all
work in decoded frames. Nothing reconciles the two, and on real media they
diverge: the reader raises on frames the facts declare, silently returns the
wrong frame, or a transcode produces output its own acceptance gate rejects. The
divergence is invisible to the probe, so the verdict calls these files
analysis-clean and builds no derivative -- the original is read as-is, against a
frame model it does not satisfy.

Three sources, all probing `analysis_transcode = None`. They are not
redistributable, so each carries its probe fingerprint and the short name used
throughout.

| Name | Container / codec | Dimensions | Packets | Frames decoded | Why |
| --- | --- | --- | --- | --- | --- |
| the pre-roll mov | `mov,mp4,m4a,3gp,3g2,mj2` / `h264` | 1280 x 960 | 594 | 591 | 3 packets carry the demuxer's discard flag, set from an edit list |
| the null-frame avi | `avi` / `indeo5` | 360 x 288 | 2000 | 1801 | 199 two-byte Indeo 5 null frames, each meaning "unchanged since the previous frame" |
| the mid-GOP avi | `avi` / `h264` | 2048 x 2080 | 286256 | 286203 | 53 leading packets precede the first keyframe; the recording was cut mid-stream |

Index-to-decode misalignment measures a constant +0.200 s on the pre-roll mov,
and grows from +2.72 s at index 500 to +7.92 s at index 1800 on the null-frame
avi. Today `seek` raises for indices 0-2 on the pre-roll mov and the mid-GOP avi,
where libav's bare `-1` surfaces as `[Errno 1] Operation not permitted`; the
null-frame avi returns wrong frames mid-file and nothing in its tail; and a
playback copy remux of the mid-GOP avi yields `start_time = 1.766`, because a
stream copy drops leading non-keyframes and the output clock keeps their offset.

OpenCV shares both defects, differing only in failing silently: it reports the
same 594 and 2000, yields the same 591 and 1801, lands at 44.28 s for index 1000
on the null-frame avi where the index declares 40.0 s, and on the pre-roll mov
renumbers its origin past the pre-roll without saying so, putting its index 297
at our index 300.

### Proportion: this is hardening

These are edge cases. The three sources come from a corpus of twenty-four files
assembled deliberately to break this package -- legacy codecs, recordings cut
mid-stream, unusual timing metadata -- and three of the twenty-four diverge at
all. No footage in routine use has hit any of it: what this package ingests is
H.264 at HD to 4K, and no H.264, HEVC or AV1 source in even the hardening corpus
changes its analysis verdict under this design.

What justifies the work is the shape of the failure, not its frequency. Two of
the three classes fail silently -- the reader returns a neighboring frame and
reports success, and nothing downstream can tell that from a correct read. A
defect that announces itself can wait; one that quietly substitutes frames in
per-frame analysis cannot, because the results it corrupts look exactly like
results that are fine.

The design is correspondingly conservative: it changes no measurement, re-mints
no identity, and moves no existing verdict.

## The principle

**Show what is there, and never drop a frame.** What was never encoded cannot be
reconstructed; everything a decoder can produce must be delivered, at the index
the frame model declares. A frame is delivered by decoding it, or by reading the
codec's own "unchanged since the previous picture" semantics, or not at all --
never by inventing content that no packet encodes.

The 53 leading packets fix that line. Each is a non-IDR P-slice: a
motion-compensated difference against a reference picture the file does not
contain. Substituting the first keyframe as that reference yields
`frame_k + (frame_53 - frame_-1)`, whose error term is the scene change across
the missing 1.77 s -- measured at 0.405 mean absolute luma difference against
0.031 between genuinely consecutive frames, so the error exceeds the signal. But
what the decoder emits for them is real: a neutral field carrying accumulated
residual, standard deviation rising 0.51 to 1.70 across the 53 where a real
picture measures 15.54. A player stepping frame by frame from a seek to zero
renders exactly this, while the same player's ordinary playback path drops them
and holds the first decodable picture -- which is why one player appears to do two
different things, and is the only third-party confirmation that delivering the
decoder's output matches what a real viewer shows. Padding them with copies of
the first good picture instead writes six identical frames at standard deviation
56.8. Padding fabricates; the decoder's output does not.

## Design

Seven changes. None narrows the frame model.

### 1. The reader decodes with the frames-preserving decoder flag

`VideoReader` sets `Flags2.show_all` on the stream's codec context. On the
mid-GOP avi this takes 100 packets from 47 decoded frames to 100.

Applied per decode segment rather than per container open, and only where the
segment begins at the start of the stream: a sequential read from frame 0, which
decodes from the container's start without seeking, and a positioning whose
resolved preceding keyframe is index 0, which is where a source's leading
non-keyframes live. Every other positioning clears it.

The scoping is not a precaution. "Before the first keyframe" is relative to
where decoding starts, not to the file, so after a backward seek the segment
begins at the seek target and an unconditional flag emits the leading pictures
of the *previous* group -- decoded against reference pictures the seek discarded,
so their content matches nothing a sequential read produces at those timestamps.
Packets preceding the first keyframe exist only at the stream start, which is
the only segment where the flag has legitimate work to do.

Two narrower rules fail. Gating the flag on `leading_non_keyframe_frames > 0`,
mirroring change 2, leaves the class open: the mid-GOP avi both needs the flag
and carries thousands of keyframes, so every seek to an internal keyframe could
inject the same artifact. And the scoping cannot be relaxed into a landing-check
gate, because the artifact reaches a caller through the reader's reusable-decoder
branch, which returns before any landing verification runs -- so no landing
tolerance can contain it.

**open-GOP** is the shape that tests the rule: leading pictures that follow their
keyframe in decode order and precede it in presentation order, which a decoder
legitimately suppresses after a seek. The reader seeks constantly, so an open-GOP
source and a source opened mid-stream are in the verification set rather than
assumed safe, alongside the twelve healthy mp4, mov and avi sources on which the
flag is byte-identical either way.

### 2. The reader ignores an edit list only when the edit list drops frames

No decoder flag recovers the pre-roll mov's 3 discard-flagged packets; the
`ignore_editlist` demuxer option does, yielding 594 frames from 594 packets.

Not applied unconditionally: on healthy mp4 sources it preserves the frame count
but shifts the presentation origin from 0.000 to 0.080, the composition offset a
benign edit list exists to compensate. It is applied only to a source measured to
carry discard-flagged packets.

### 3. One timestamp space per source, for the index and the decode alike

`ignore_editlist` changes what the packets carry, not merely which frames are
emitted, so the timestamp space is a property of how a source was opened.

On a gated source it moves *which picture sits at each timestamp*, not the
timestamps. Both readings of the pre-roll mov run 0.000, 0.067, 0.133, 0.200:

| gated frame | time | picture |
| --- | --- | --- |
| 0, 1, 2 | 0.000 - 0.133 | the recovered pre-roll; absent from the default decode |
| 3, 4, 5 | 0.200 - 0.333 | identical to default frames 0, 1, 2 (difference 0.000) |

Every picture moves three indices later and the pre-roll fills the vacated slots.
The packet scan agrees from the other side: default packet times begin -0.200
against a decode beginning 0.000, and with the option both begin 0.000.

So whenever the change-2 gate fires, the option is applied to the decoding
container *and* to the seek-index scan
(`src/mosaic_media/io/packets.py`, which opens with no options today).

**The reader's index is always built by `scan_packets_in_process`, under the same
open options as the reader's own decode.** A `SeekIndex` built by the probe's
`scan_packets`, or in the other timestamp space, is not valid for the reader's
seek path.

That has to be enforceable, not merely stated. `SeekIndex` today carries
`frame_times` and `keyframe_indices` and nothing else (`io/index.py:20-23`), so a
probe-built and an in-process-built index are indistinguishable at the injection
seam -- two tuples of floats. It therefore gains a provenance field recording the
scanner that produced it and whether the gate was applied; both scanners populate
it through `build_seek_index`; and `_ensure_index` (`io/reader.py:175-179`) and
`MultiVideoReader`'s per-segment equivalent (`io/multi.py:177-182`) raise on a
mismatch against the source's own measured counts.

`scan_packets_in_process` takes only a path today and opens bare
(`io/packets.py:22-28`), so it gains the gate as a parameter and both call sites
forward it. Without that there is no public way to build a valid index for a
gated source at all, which would make the documented `index=` and `indices=`
injection parameters unusable for exactly the sources this design repairs. A
caller can tell the gate fired because `discard_flagged_packets` is on the facts
it already holds; that is the protocol.

Requiring one scanner costs nothing today, because nothing builds such an index:
every `build_seek_index` call site is inside this package (`io/reader.py:178`,
`io/multi.py:181`), each fed by `scan_packets_in_process`. It buys the removal
of an entire class of failure. The two
scanners genuinely disagree where libavformat synthesizes presentation timestamps
that ffprobe reports as absent -- the behavior `io/packets.py`'s docstring already
records. On the committed `tests/assets/h264.avi`:

```
probe scan       source=dts   frame_times[:3] = (0.00, 0.04, 0.08)
in-process scan  source=pts   frame_times[:3] = (0.04, 0.08, 0.12)
decoded frames                              = (0.04, 0.08, 0.12)
```

A full frame period apart, and the seek landing check compares against the index
absolutely (`io/reader.py:388`): with a probe-built index all four seeks of that
file raise today, where the in-process index resolves all four. Requiring one
scanner makes index and decode agree exactly -- both sides are
`float(pts * time_base)` from the same time base -- so no offset exists to absorb
and the existing half-frame check keeps working unchanged, as margin rather than
as noise coverage.

The probe needs no second scan. Its shift is uniform and reorders nothing, so
default index `i` and gated index `i` are the same packet, and `frame_count`,
`fps`, `duration` and the shift-invariant grid fit are identical in both spaces --
confirmed on the pre-roll mov at 594 packets and 39.4 s either way. `start_time`
comes from the header, not the packets. `MediaFacts` therefore stays valid as
measured, and only the reader's index has to match the reader's decode.

### 4. Delivery measurements on `MediaFacts`

`scan_packets` already requests the `flags` column
(`src/mosaic_media/probe/ffprobe.py:369`) and parses it, testing only for `"K"`
(`ffprobe.py:434`); the discard flag is in hand and thrown away.

`Packet` (`ffprobe.py:76-95`) gains `discard: bool`, from that column in
`scan_packets` and from `packet.is_discard` in `scan_packets_in_process`. It must
precede the defaulted `data_hash` field, and `scan_packets_in_process` constructs
`Packet` by keyword (`packets.py:53,58`), so both call sites move together. It
enters no digest: `content_digest_input` enumerates its hashed fields explicitly
(`identity.py:102-123`) rather than serializing the dataclass.

`MediaFacts` gains the counts listed below, computed in `probe_media` over the
packet tuple
`scan_packets` returns -- the pts list, the dts list, or the untimed list, in that
list's order, never the raw rows. The untimed branch is included: a raw
elementary stream bypasses `measure_timing` (`probe.py:26-39`) but carries
keyframe flags, so the leading count is meaningful there -- but only when it is
counted in packet order, which is what the bullet below specifies for that
branch and what the first implementation of it did not do. Counting distinct
timestamps returns 0 for every untimed file however many frames precede its
keyframe, because they all carry one placeholder; the count then never
escalated a copy remux, and a raw stream cut mid-stream lost its leading frames
to one. The claim in this paragraph was false for as long as the implementation
counted timestamps in that branch.

- `discard_flagged_packets`: packets the demuxer marked "do not present". Gates
  changes 2 and 3, and tells command construction a stream copy would lose them.
- `leading_non_keyframe_frames`: frames preceding the first keyframe-flagged
  packet **in presentation order**, the order the frame model uses; zero for a
  stream with no keyframe flags. **In packet order for a stream whose packets
  carry no timestamps**, which has no presentation order and one access unit per
  frame, so packet order is both the only order available and the right one. A
  frame here is a distinct presentation timestamp, the same unit `frame_count`
  counts, not a packet -- a container
  carrying several packets at one timestamp contributes one. Its sibling
  `discard_flagged_packets` does count packets, and the two names differ for
  that reason. Tells command construction a stream copy would
  drop them. It applies `build_seek_index`'s own rule -- a distinct timestamp is a
  keyframe timestamp when *any* packet bearing it is keyframe-flagged
  (`index.py:78-82`) -- so the count and the index's `keyframe_indices` cannot
  disagree on a container carrying duplicate timestamps.

One further field joins them, for the reader's benefit rather than command
construction's: `max_timestamp_gap_frame_periods`, the widest step between
neighboring presentation timestamps, already computed by the grid fit that
decides `constant_frame_rate` and otherwise discarded. The reader cannot derive
it from what `MediaFacts` already carries. `constant_frame_rate` admits a
neighbor step of up to 2.0 frame periods, and the fit's drift bounds that step
only at `1 + 2 * drift`, which is the same 2.0 -- exactly the spacing one missing
frame produces, so neither can separate a legitimate step from a missing frame.
Carrying the measured step needs no such derivation.

All required rather than defaulted, following `MediaFacts`' existing no-defaults
convention (`facts.py:26-28`): a default would let a caller assert "no
undeliverable packets" for a file nobody measured, and a defaulted spacing would
assert uniform timing for one. This breaks every construction site, the expected
shape of a measurement addition here.

**The reader needs both before it opens the container**, since they select the
open options. Injected facts supply them, so the injected-facts path runs no scan
and its contract (`io/reader.py:65-67`) is preserved exactly. Without facts the
reader runs the in-process packet scan first and opens afterwards, so a factless
sequential read pays one demux pass before its first frame; the docstring says
so.

| container-open site | `ignore_editlist` |
| --- | --- |
| `_ensure_container` (`reader.py:139`) | when gated |
| `scan_packets_in_process` (`io/packets.py:28`) | when gated, per change 3 |
| `_probe_rotation` (`reader.py:159`) | no -- reads display-matrix side data, which the option does not affect |

`show_all` is absent from this table because it is not a container-open property:
change 1 decides it per decode segment.

### 5. A trusted codec set, injected as policy

A packet carrying no coded picture is not detectable from the container. The
null-frame avi is well formed: 2000 packets present, consecutive timestamp gaps
uniformly 1, not truncated, its 199 two-byte packets valid Indeo 5 null frames
each beginning `0x9f`. Both recovery options leave 1801 frames.

Whether such a packet yields a frame is a property of the codec and its decoder,
so the property is asserted about the codec. A codec on the trusted set yields
exactly one emitted frame per packet bearing a distinct presentation timestamp,
once changes 1 and 2 are in force, and membership is established by test against
real media, never assumed. A codec outside the set gains an analysis reason, so
the file is transcoded into one that is.

The reason is `"unverified_frame_correspondence"`. It joins the `AnalysisReason`
literal (`probe/policy.py:20-26`) and `_REENCODE_ANALYSIS_REASONS`
(`transcode/commands.py:58-65`), which is annotated `frozenset[AnalysisReason]`
and does not type-check without the first. Selecting the timestamp remux instead
would copy the untrusted codec forward, and the output would fire the same reason
and fail the acceptance gate; selecting nothing would report a no-op while the
verdict said a transcode was required.

**The set is a defaulted frozenset field on `Thresholds`**, which `derive`
already takes (`verdict.py:52-54`) and which is the injected-policy carrier for
every non-browser decision. A defaulted field breaks no call site, where a new
positional parameter on `derive` would break every one of them plus
`run_transcode` and its callers -- `grep -rn "derive(" src/ tests/ scripts/`
names the set. The package ships a tested default constant beside `DEFAULT_THRESHOLDS`,
following the `CHROME_149` precedent.

Every member of that default is measured by a delivery test over a real clip;
none is admitted on decoder-family reasoning, because a wrong member fails in the
silent way this design exists to remove. Being unable to *encode* a codec here is
not a bar: H.264 and HEVC clips are committed under `tests/assets/` and read with
no added dependency, because both decoders are native and LGPL.

The one non-GPL software H.264 encoder, `libopenh264`, is not the missing piece.
It is built for real-time conferencing and its interface says so: rate control
offers `off`, `quality`, `bitrate`, `buffer` and `timestamp` with no
constant-quality target, input is 8-bit 4:2:0 only (`yuv420p` and `yuvj420p`),
and profiles stop at High. None of that suits the near-lossless permanent
derivative this package writes, so it is not what a machine carrying it would
encode with either.

The derivative materializes the missing frames as duplicates of the preceding
frame through the re-encode's constant-rate resample. That is not the fabrication
rejected above, because an Indeo 5 null frame *means* "unchanged since the
previous frame" -- but the mechanism does not know that, and the distinction must
not rest on it: `-fps_mode cfr` (`commands.py:301`) duplicates whatever frame is
current at each output tick regardless of what the packet meant. What contains
that is the trusted-set gate itself -- a codec reaches this path only by being
outside the set, and joining requires the per-packet test above.

Measured impact: seven corpus files newly require an analysis transcode -- two
`wmv2`, one `indeo5`, one `mpeg4`, two `msmpeg4v2`, one `rpza`. The null-frame
avi's analysis derivative probes 1999 frames with 1999 demuxed packets and 1999
decoded frames, and re-probes analysis-clean; a derivative's frame count need not
equal its source's, since a constant-rate resample is not a frame-for-frame copy
and consumers use the derivative's own facts.

### 6. A stream copy is chosen only when it can deliver every frame

A stream copy carries the source's packets, so a source whose packets do not all
decode yields a derivative whose packets do not all decode. Command construction
consults the change-4 counts, which `build_command` already has in `facts`
(`commands.py:314`) and which `_select_operation` (`commands.py:171`) already
takes.

That selector becomes a two-layer arrangement, both layers new here.
`_select_minimum_operation` picks the lightest operation clearing the target's
reasons -- the whole of what `_select_operation` did before -- and
`_select_operation` becomes a wrapper adding two escalations: a copy whose codec
the mp4 container cannot carry, against the new `MP4_STREAM_COPY_CODECS`, and a
copy that would not carry every frame. Neither the split nor the codec set
exists before this design; on `main` `_select_operation` takes only a verdict
and a target and returns the minimum directly. Muxability and deliverability are
independent reasons to escalate, and a copy must survive both.

Which codecs mp4 carries through a copy is itself a measurement, and the set
recording it becomes one the repository can check: each member is muxed from a
real sample through the real muxer, and each codec measured as refused is pinned
outside the set. The constant loses its leading underscore in the process, since
the test that measures it imports it. `hevc` joins it here. It was omitted only
because no sample existed to measure it with, and `tests/assets/hevc.mp4` now
supplies one. The reach is narrow: under the shipped playback profile an HEVC
source is client-dependent and re-encodes for playback on that reason alone, so
the change is confined to an HEVC source whose only analysis reason is
`unreliable_timing_metadata`, which then takes the timestamp remux that reason
asks for instead of a full re-encode.

`_reencode_argv` (`commands.py:281`) gains the input-flag insertion point
`_copy_remux_argv` already has (`commands.py:203-207`), so the re-encode can
carry `-flags2 +showall` and, when gated, `-ignore_editlist 1`.

**One rule, no exception: a copy remux that cannot deliver every frame escalates
to a re-encode.** That includes `REMUX_FASTSTART`, whose `moov_not_at_start`
reason is soft (absent from `HARD_STREAM_REASONS`, `policy.py:33-42`).

Skipping the transcode for a soft reason was considered and is wrong. Nothing in
this package distinguishes a required transcode from a recommended one at
selection time -- `_select_operation` reads the reason set, never
`stream_transcode` -- so a caller electing a recommended playback transcode
simply calls `run_transcode` for that target and expects an output. The
distinction exists only afterwards: the acceptance gate fails on a `required`
reason in the output (`convert.py:406`), and a residual soft reason is reported
through `residual_recommended` (`convert.py:414`). Skipping would turn a
deliberate request into a silent no-op that leaves the verdict standing, so a
caller electing every recommended transcode would find the verdicts unchanged and
nothing to show for it.

Verified end to end: the mid-GOP shape goes from 49 packets yielding 25 frames to
a derivative of 49 yielding 49, and the pre-roll mov from 594 yielding 591 to 594
yielding 594. Both are one frame per source packet and fully decodable.

This also fixes the `start_time` defect, since the mid-GOP avi's playback
derivative becomes a re-encode and a re-encode starts at zero by construction. No
output-clock shifting is involved: shifting hides the dropped prefix rather than
delivering it.

Two costs, named rather than left to be discovered: a source cut mid-GOP is
re-encoded rather than rewrapped; and the run takes far longer than a copy, which
makes `run_transcode`'s `timeout` load-bearing. That is already a caller-supplied
parameter (`convert.py:321`, exported as `DEFAULT_TRANSCODE_TIMEOUT_SECONDS`), so
this package needs no change. A caller that took the default while every
operation was a copy may find it short once a re-encode is selected; the default
is documented so that a caller can size it, and no figure measured on a
development machine would be a sound basis for one anyway.

Encoding quality is not among the costs: a file needing a playback derivative
gets the injected playback encoding parameters by construction, so the result is
what the policy specifies rather than a regression against it.

### 7. The reader resolves a seek landing by timestamp; sequential reads keep counting

`VideoReader` counts decoded frames forward from a keyframe while every public
index comes from the packet-timestamp model. Under the delivery guarantee those
two agree: with changes 1 and 2 in force every packet yields a frame, so the Nth
decoded frame is the Nth packet timestamp. Confirmed on both defective sources --
counted index N equals packet-timestamp N across the first 300 frames of the
pre-roll mov and the mid-GOP avi.

**So sequential reads keep counting, and build no index.** Counting only starts
from the wrong place when a seek lands somewhere other than where the index said,
which is one position per seek, resolved by bisecting an index the seek path
already builds.

#### The injected-facts path pays nothing

This scoping is a requirement, not an optimization. Injecting facts exists to
make reads fast by probing once, and the performance gate measures exactly that
against OpenCV across seventeen payloads. Resolving every read against
`frame_times` would have forced a demux pass onto paths that run none today:

| gate payloads | packet scans today | must stay |
| --- | --- | --- |
| `metadata_open` x3 | none -- with facts injected the reader never opens a container; reader median about 0.1 ms | none |
| `sequential_full_decode` x3, `strided_decode` x2 | none | none |
| `cold_random_seek` x2, `monotonic_strided_seeks` x2, `seek_then_sequential` x2, `sorted_sparse_extraction` x2 | one, built on the seek path | one |
| `multi_video_junction` x1 | index built and injected by the caller | unchanged |

Five payloads would have regressed outright and three more sit on a 0.1 ms
baseline a full demux would dwarf. The rule that prevents it: **no path that
injects facts may run a packet scan it did not run before this change**, because
that is what every gated payload does and what the gate measures. Change 4's
measurements come from injected facts, and sequential reads resolve nothing, so
those paths still scan exactly as often as they did.

Stated that way rather than as a blanket "no path runs a new scan", which this
design does not hold to and which two of its own passages contradict. A factless
reader gains one scan, because the options have to come from somewhere when no
facts supply them; and a factless reader given an index gains one too, for the
same reason -- the index records which space it was built in, but taking the
answer from there would leave nothing to check the index against. Neither path
is gated, and neither existed on the injected-facts contract the gate protects.
What the gate would catch is a scan appearing where facts were injected, and no
change here puts one there.

The seek landing check additionally accepts a landing at or before the requested
keyframe and decodes forward from where it actually landed. A backward seek is
not guaranteed to reach the keyframe the index named: on one `asf` source a seek
to 6.54 s lands on the keyframe at 5.82 s, on another a seek to 169.481 s lands
at 168.377 s. Both are correct seeks the current check rejects. That check is
already a half-frame-period tolerance rather than an equality
(`io/reader.py:388`) and is skipped when `fps <= 0`; the tolerance is retained
and the "at or before" allowance is added. A landing *after* the requested
keyframe stays an error.

Resolving the landing's index rank keeps the same tolerance, which is zero when
`fps <= 0` -- so that case demands exact equality rather than being skipped. That
is correct rather than an oversight: index and decode are built by one scanner in
one timestamp space, so their times agree bit for bit, and a stream with no
measurable rate has no frame period to derive a tolerance from. A mismatch there
means the index does not describe the decode, which is the condition the
resolution failure reports.

Where the container index does not cover the target the reader decodes from the
start of the stream instead of seeking -- the mid-GOP avi's index begins at the
keyframe 1.8 s in, and a backward seek below it fails.

**The backstop, on both paths.** It exists for the two cases the package cannot
prevent: a caller handing the reader an unprobed source, or one whose verdict
said transcode and was read directly anyway, since `VideoReader` consults no
verdict and opens what it is given; and the trusted-set claim turning out wrong,
since membership is asserted per codec from a test corpus rather than proven per
file.

That a healthy source never trips the backstop is measured rather than argued:
sweeping 2400 window combinations -- every committed asset plus the pre-roll,
the mid-stream cut and the variable-rate fixture, across `start_frame`,
`end_frame` and `frame_step`, injected-facts and factless -- raises nothing, and
a control run with the check disabled raises exactly the same set. Arguing it
instead from trusted-set membership would assume the property the backstop
verifies, and would contradict the third case above.

**The backstop is a per-frame check, made where the frame is delivered**, so it
cannot depend on how the window ends. Two mechanisms, selected by what the reader
holds rather than by which public method was called, and never both.

*With a seek index*, each delivered frame is checked against the index entry for
the index it is being returned under. `_target` is an absolute source frame index
on every path that reaches delivery and the index is in absolute source ranks, so
the entry is `frame_times[_target]`; `start_frame`, `frame_step` and the delivery
count's origin choose which targets are visited and shift that mapping not at
all. Tolerance is half a frame period, as the landing check already uses.

*Without one*, consecutive decoded frames are checked for the gap a missing frame
leaves. An index exists only when the reader was constructed without facts or has
since seeked, so the region this covers is exactly a caller injecting facts and
reading forward -- the sequential and strided read, which must keep building no
index because the performance gate measures it at zero packet scans. The gap
needs only the previous decoded timestamp.

The first is strictly stronger: it compares each frame against the entry for its
own index, so it locates a mislabel rather than inferring one from spacing. It is
authoritative wherever it is available and the gap check is gated off there, so
the two never overlap.

The gap threshold is the file's own `max_timestamp_gap_frame_periods` plus half a
period. A fixed threshold is unsound, because a container too coarse to express
its own frame rate quantizes the timestamps and a constant-rate file's neighbors
then land unevenly -- 30 fps written into a 1/36 timescale steps 1.662 periods.
Every such file is analysis-ready by this package's own verdict, so a constant at
1.5 raises mid-read on all of them.

Above 2.0 periods the check declines rather than guessing, which is a documented
outcome and not a gap. One missing frame puts two neighbors at the sum of the
steps it spanned, which on a uniform file is 2.0, so once a file's own step plus
margin reaches 2.0 the signal and the tolerance overlap and no comparison of
spacings can separate them. Those files keep the delivery count and the index
check. The same rule covers a genuinely variable source, which declines on its
own measured step rather than through a `constant_frame_rate` gate -- and that
matters in the other direction too, since a gate would exempt a mildly variable
file from checking entirely where its own step keeps it covered.

Both mechanisms read only the decoded frame's own timestamp and what the reader
already holds. Neither builds an index, runs a packet scan, or consults a verdict:
the reader receives no profile and no thresholds, and acquiring a verdict here
would put policy in the one place this package keeps free of it.

Two mechanisms this design also prescribes stay, in roles the per-frame check
does not fill. On the seek path, the resolution failing: an index entry whose
timestamp matches no decoded frame raises an error naming the cause, covering a
landing outside the index span. A landing is a real packet timestamp and so is
always in the index, which is why it reports a distinct event from a delivery
that is not the frame its index names, with a distinct message.

The sequential path resolves nothing, so it needs its own. Today an
untrusted-codec source read sequentially ends short and says nothing: the
null-frame avi declares 2000, delivers 1801, and `read()` returns `(False, None)`
199 frames inside its window, which `__iter__` and `read_batch` take as normal
termination. **So a sequential read that ends before the window does counts what
it emitted and raises the same named error.** One integer comparison against
`source_frame_count` at end of stream -- no index, no scan, and therefore no cost
on the injected-facts path.

That count reports a short delivery, which no per-frame check sees, and it
reports nothing else. It does not catch a mid-file mislabeling, and it does not
fire at all for a bounded window, which ends on a frame the source still had:
those are the two gaps the per-frame check exists to close.

There is deliberately **no fallback and no retry**. The only available fallback
is returning a neighboring frame, the defect this design exists to remove.

`read()` keeps returning `(False, None)` for end of stream and end of window,
which `__iter__` and `read_batch` treat as normal termination
(`reader.py:421-426`, `446-449`, `506-511`). An unresolvable index *inside* the
window is a different event and raises, matching `read_frames`, which already
raises in exactly this situation (`reader.py:494-496`) where `read` does not.

## What deliberately does not change

An earlier version of this design changed all of it, and the change was wrong.

- **`MediaFacts.frame_count` keeps counting packet timestamps.** No packet class
  is excluded; every packet is delivered as a frame instead.
- **The seek index keeps the same entry count as the frame model.**
  `build_seek_index`'s invariant -- its `frame_count` equals
  `MediaFacts.frame_count` -- holds unchanged and needs no matching filter. An
  excluding frame model would have desynchronized the two by 53 entries at the
  head of the mid-GOP avi and returned wrong frames silently. The timestamp
  *space* is a separate question, settled by change 3 rather than by construction.
- **Identity is untouched**, so no `video_uuid` or `content_digest` is re-minted.
- **`measure_gop`, `measure_timing`, `duration`, `truncated`,
  `_timing_metadata_lies` and `constant_frame_rate` see the same packet sequence
  as before** (`probe.py:22-50` passes one tuple to all of them).
- **The whole-file grid fit is unchanged.**

## Rejected alternatives

- **Narrow the frame model to what decodes.** Desynchronized the seek index,
  moved four derived facts, and still left the null-frame avi declaring 2000
  frames it could not deliver.
- **Repeat the previous frame for any index with no decoded frame.** Correct for
  a codec-level null frame, wrong as a general rule: for the leading-packet class
  it invents content, and it would put this package's index out of step with every
  other decoder -- a blanket repeat rule places our frame 1000 on the null-frame
  avi at 40.0 s where OpenCV's and ffmpeg's is at 44.28 s.
- **Let the two scanners both feed the reader's index and reconcile the
  difference.** Two ways were considered and both rejected in favor of requiring
  one scanner. A wider tolerance cannot work: the disagreement is a systematic
  one-frame-period offset, not noise, so a tolerance wide enough to span it
  resolves into the neighboring frame. Anchoring each side on its own first entry
  does absorb a constant offset, but it fails silently exactly where it is needed
  -- if the first packet yields no frame, the anchor registers frame 0 against
  index 1 and every later resolution is shifted, with no index left unresolved for
  the backstop to catch. It also has no defined value on the seek path, where the
  first frame emitted is the landed keyframe rather than stream frame 0.
- **Resolve every read by timestamp rather than only a seek landing.** Correct,
  and unaffordable: it forces an index build onto the sequential and metadata
  paths, which run no packet scan today and which the performance gate measures
  against OpenCV. The delivery guarantee already makes counting exact on those
  paths, so the cost buys nothing.
- **Decode-verify the frame count at probe time.** On the 286256-frame source the
  demux-only probe takes 3.3 s against roughly 320 s for a full decode. About
  100x, and a floor rather than an estimate: that file runs at about 890 kbit/s
  for 4.3 megapixel frames, so it decodes unusually fast for its size.
- **Detect the third class from container structure.** Missing frame numbers,
  timestamp discontinuity, and chunk sizes past the file length all diagnose a
  damaged file. The null-frame avi is intact -- consecutive timestamp gaps
  uniformly 1 across all 2000 packets -- so none of these fire.
- **Infer the third class from packet size.** No threshold separates the cases
  across codecs: an Indeo 5 null frame is two bytes and yields no frame, while an
  H.264 skip picture is comparably small and yields one.
- **Shift the output clock to hide a dropped prefix.**
  `-avoid_negative_ts make_zero` rebases every stream against the earliest
  timestamp in any of them, which for a source carrying AAC is the encoder delay
  ahead of its first audio sample: measured, it moves a clean file's video from
  0.000 to 0.080. Scoped bitstream-filter rebases fail differently -- anchoring on
  `STARTPTS` corrupts AVI, which carries no presentation timestamps, and anchoring
  on `STARTDTS` moves B-frame mp4 sources off zero.
- **Copy the initial non-keyframes rather than dropping them.** Keeps the
  timeline and the start time, but leaves a derivative whose first 53 frames do
  not decode -- the defect the transcode exists to remove.

## What this closes

`docs/issues/reader-cannot-deliver-frames-the-facts-declare.md`, in the terms the
issue sets: every frame index inside `frame_count` either returns a frame or
raises an error naming the reason. The reader delivers the leading-packet and
edit-list classes directly; the null-frame class is routed to a derivative that
delivers them, and a caller reading such a source directly gets change 7's named
error at the frame it asked for, which is the issue's own second disjunct rather
than a gap. The mechanism that reaches that frame is change 7's per-frame
delivery check; neither the seek path's resolution failure nor the end-of-stream
count does.

`docs/issues/playback-transcode-introduces-non-zero-start-time.md`, first case:
the source cut mid-GOP is re-encoded rather than rewrapped, so its derivative
starts at zero and carries every frame. The issue's second case -- a copy into a
container that cannot hold the codec -- is closed by the codec/container
escalation in `_select_operation`, which change 6 introduces along with the
deliverability escalation beside it. Both cases close here; neither was closed
before.

Both issues proposed mechanisms that turned out wrong, and should be read with
that correction. The `start_time` shift is the dropped leading prefix, not an
edit-list composition offset acquired at the muxer. The `Operation not permitted`
text carries no meaning: that source's container index begins at the keyframe
1.8 s in, a backward seek below it fails, and libav's bare `-1` is rendered as a
permission error.

## Verification

- The three sources read every index in their declared range, and the two `asf`
  sources seek to their midpoints.
- Frame content is checked against timestamps, not merely against a successful
  return: an index resolves to the frame whose presentation timestamp it names.
- An index built in a different timestamp space, or by the probe's scanner rather
  than the reader's, is rejected rather than resolved into. The two cases are the
  pre-roll mov's three-index picture shift and `tests/assets/h264.avi`, where a
  probe-built index makes all four seeks raise today while the in-process index
  resolves all four.
- No path that injects facts runs a packet scan it did not run before: the
  injected-facts sequential, strided and metadata-open payloads are pinned at
  zero scans, and the seek payloads at the one they already run. The factless
  paths, which no gated payload uses, are pinned separately -- one scan, or two
  where a source needing the edit list ignored must also be indexed.
- A derivative of each recovered class carries one frame per source packet and
  decodes every one.
- `show_all` is pinned as a no-op on healthy sources, including one open-GOP and
  one opened mid-stream, across both a sequential read and a seek to every
  internal keyframe. Its scoping is pinned on decoder state directly -- set when
  the resolved preceding keyframe is index 0 and on a sequential read from frame
  0, clear otherwise -- so no change to landing tolerance can mask a violation.
  `ignore_editlist` is pinned as *not* applied to a source without
  discard-flagged packets, with the 0.080 s origin shift as the reason.
- Reading an untrusted-codec source directly raises the named backstop error at
  the frame the caller asked for, rather than returning a neighboring frame or
  ending a short read silently -- from every entry point, and whether or not the
  window is bounded. The null-frame avi's 2000-declared against 1801-delivered
  is the case. Both per-frame mechanisms are pinned: the index-entry check where
  the reader holds an index, and the decode-gap check where it holds facts and
  builds none, the latter alongside its zero-scan count.
- An index injected from the wrong scanner or the wrong timestamp space is
  rejected on provenance rather than resolved into.
- Every codec in the mp4 stream-copy set is muxed into mp4 from a real sample
  through the real muxer, and every codec measured as refused is pinned outside
  the set.
- `video_uuid` and `content_digest` are unchanged for every corpus file.
- No H.264, HEVC or AV1 source in the corpus changes its analysis verdict.
