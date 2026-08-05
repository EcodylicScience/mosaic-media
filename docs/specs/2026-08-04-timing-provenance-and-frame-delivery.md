# Timing the file did not supply

## Problem

The probe asks whether the packets carried timestamps. Two further questions
decide whether the timing can be trusted: whether the *file* supplied them, and
whether they are presentation timestamps or decode timestamps.

`scan_packets` answers only the first question, returning `TimestampSource` of
`pts`, `dts`, or `none`, and `probe.py` treats anything other than `none` as a
measurement. For some formats the demultiplexer manufactures the answer,
inventing one timestamp per packet from a frame rate that is itself a demuxer
default. The probe reads the invention as a measurement, `timing_measured` comes
out true, and the verdict declares the file analysis-ready.

Four open defects are that one confusion.

**A synthesized-timing source verdicts as analysis-ready.** A bare AV1 stream
carrying 74 packets over 50 pictures measures `fps=25.0`, `frame_count=74`,
`constant_frame_rate=True`, and an empty analysis reason set. Both frame reader
paths then raise on it -- correctly, since the frame model declares frames no
decode produces -- so a file the verdict calls clean is a file the reader
refuses.

**A source whose timestamps are not presentation timestamps receives decode
order.** A copy remux synthesizes timestamps from the packet index, which is
decode order. For a bitstream coded with reordering that is not presentation
order, so the timestamps are shuffled against the pictures they label. The
acceptance re-probe cannot see it: they remain uniform and complete.

**A raw stream stating no rate anywhere falls back to the muxer.** Where the
sequence parameter set carries no timing there is nothing to read, so the built
command leaves the mp4 muxer to synthesize timestamps at an approximation nobody
chose, logging a deprecation notice the runner's `-v error` hides.

**A decoder emitting two pictures at one timestamp goes unnoticed.** Where the
reader holds facts, builds no index, and never seeks, neither delivery check
reaches a collapse: a missing frame widens the spacing between decoded frames and
a duplicated one closes it to zero, and only the first is a gap.

## What was measured

Whether a format supplies its own timing is decided by measurement, not by
whether it is a container. Non-uniform timing is put through the format and read
back; timing the file supplies survives, timing the demultiplexer invents is
replaced by a uniform grid.

```bash
FILTER="setpts='if(gt(N,9),PTS+3/TB/25,PTS)'"
ffmpeg -f lavfi -i "testsrc2=size=128x96:rate=25" -frames:v 20 -vf "$FILTER" \
    -fps_mode passthrough -c:v <encoder> -f <muxer> sample.<extension>
ffprobe -select_streams v:0 -show_entries packet=pts_time -of csv=p=0 sample.<extension>
```

`-fps_mode passthrough` is load-bearing. Without it the encoder retimes the
frames to a uniform grid before the muxer sees them, every format reads back
uniform, and the measurement says nothing.

The source carries a three-frame gap after the tenth picture, so a format that
supplies its own timing reads `... 0.360000 0.520000 ...` and one that does not
reads `... 0.360000 0.400000 ...`.

The muxer name and the format name the probe sees are not always the same: MPEG-2
video is written with `-f mpeg2video` and reads back as `mpegvideo`. The table
records what the probe sees, since that is what the classification keys on.

| Format | `container` | Source | Gap survives | Supplies timing |
| --- | --- | --- | --- | --- |
| mp4 | `mov,mp4,m4a,3gp,3g2,mj2` | `pts` | yes | yes |
| Matroska | `matroska,webm` | `pts` | yes | yes |
| transport stream | `mpegts` | `pts` | yes | yes |
| audio video interleave | `avi` | `dts` | yes | yes |
| MPEG-4 part 2 elementary | `m4v` | `pts` | yes | yes |
| simple bitstream container | `ivf` | `pts` | yes | yes |
| AV1 low overhead bitstream | `obu` | `pts` | **no** | **no** |
| MPEG-2 video elementary | `mpegvideo` | `dts` | **no** | **no** |
| uncompressed frame stream | `yuv4mpegpipe` | `pts` | **no** | **no** |
| H.263 elementary | `h263` | `pts` | **no** | **no** |
| motion JPEG stream | `jpeg_pipe` | `pts` | **no** | **no** |
| H.264 elementary | `h264` | `none` | no timestamps | no |
| HEVC elementary | `hevc` | `none` | no timestamps | no |

Being a bare elementary stream does not decide it. `m4v` carries a per-picture
time increment in its own bitstream and `ivf` carries a timestamp in each frame
header; both survive. The presentation and decode distinction does not decide it
either: `mpegvideo` reports no presentation timestamps and reaches the probe
through the decode-timestamp fallback, and so does `avi`, whose timing is
genuine.

**Among the formats measured**, the invented-timing set is `obu`, `mpegvideo`,
`yuv4mpegpipe`, `h263` and `jpeg_pipe`. Every format that failed the measurement
goes on the list; none is held back on the grounds that no path here produces it,
because membership costs nothing at run time and the default for a format left
off is the unsafe one.

`h263` is the sharpest case and the reason the list is not confined to formats
with an obvious ingest story: its demultiplexer does not merely flatten the gap
but reports a rate the file never had, measuring 29.660 frames per second for a
25 frames per second source, with neighboring timestamps 0.0334 apart. A frame
model built on that is wrong about every frame, not only the ones after the gap.

The set is still not proved complete. It is a denylist over formats put through
the measurement, and a format never measured is trusted -- so the list's own
documentation has to say that plainly, and the measurement recipe has to live
beside it, or the next format arrives silently trusted.

Reachability did not decide membership, and it could not: the extension gate
governs directory scanning only, while `probe_media` takes a path and probes
whatever it is handed, so a format absent from the scanned extensions is still
reachable by a direct call.

## Design

### Timing provenance

`MediaFacts.timing_measured`, a boolean, is replaced by `timing_source`, a
`Literal` alias over four values:

| Value | Meaning | Formats measured |
| --- | --- | --- |
| `presentation` | The file supplied presentation timestamps. | mp4, Matroska, transport stream, `m4v`, `ivf` |
| `decode` | The file supplied timing, but only decode timestamps. | `avi` |
| `synthesized` | The demultiplexer invented the timestamps. | `obu`, `mpegvideo`, `yuv4mpegpipe`, `h263`, `jpeg_pipe` |
| `absent` | The packets carry no timestamps at all. | `h264`, `hevc` |

It is computed from the timestamp source and the format together, in this order:
a source of `none` is `absent`; a format on the invented-timing list is
`synthesized` whatever its source; otherwise `pts` is `presentation` and `dts` is
`decode`. The order matters, because `mpegvideo` arrives as `dts` and must not be
read as a file-supplied decode timestamp.

The invented-timing list is the whole mechanism, and it is explicit. No property
of a format readable at probe time separates invented timing from supplied
timing; the only test is to put non-uniform timing through the format and read it
back, which is an offline measurement rather than something a probe can do to the
file in front of it. Each entry is recorded in the code beside the list with the
measurement that put it there, so the list is extended by running the measurement.

A boolean cannot express this. The reordering defect below reaches `decode` as
well as `synthesized` and `absent`, and a boolean collapses `decode` into the
trusted side.

**What does not change.** The branch leaving `fps`, `duration` and
`constant_frame_rate` as placeholders stays keyed on the timestamp source being
`none`, not on `timing_source`. A synthesized-timing source has timestamps, and
measuring them yields a self-consistent rate; zeroing it would discard the only
rate available.

### Every reader of the removed boolean

`timing_measured` has five readers. Removing a field without naming the
replacement at each one leaves an implementer to choose, and one of these choices
decides whether a feature specified below is reachable at all. Each is settled
here.

| Site | Today | Replacement | Why |
| --- | --- | --- | --- |
| `verdict.py:67`, the variable-rate gate | `timing_measured and not constant_frame_rate` | `timing_source in ("presentation", "decode") and not constant_frame_rate` | An unsupplied-timing source carries `constant_frame_rate=False` as a placeholder, never a measurement. Firing `variable_frame_rate` on it would re-encode a file a timestamp-generating remux fixes. |
| `verdict.py:110`, `unreliable_timing_metadata` | `not timing_measured` | `timing_source in ("absent", "synthesized")` | Both are timing the file did not supply. |
| `identity.py:272`, `compare_for_duplicate` | `left.timing_measured and right.timing_measured`, beside a zero-duration guard | both sides `in ("presentation", "decode")`; the duration guard is untouched | The comparison needs timing that measures the file, which is the same predicate the identity hash uses. The duration guard is a separate condition keeping the function total over any facts a caller can construct, which a `Literal` does not change. |
| `commands.py:372`, `timestamp_fps` | `0.0 if timing_measured else declared_fps` | `declared_fps if timing_source == "absent" else 0.0` | Only a source with no timestamps at all may have them written from a declared rate. A synthesized source's `declared_fps` is the demuxer's own invention. |
| `identity.py:127` and `:167`, the hash input | `timing_measured: bool` | unchanged in type, renamed to `timing_supplied_by_source` | The hashed value stays a boolean so unchanged files keep their identity. Only the parameter name moves, because the fact it was named for no longer exists. |

The `unreliable_timing_metadata` rekey is the load-bearing one, and `absent` is
the value it must not lose. A rate-less raw stream is `absent`, and today that
reason is its only analysis reason -- H.264 is a frame-exact codec so
`unverified_frame_correspondence` does not fire, and its coded reordering depth is
zero so the new reason does not either. Rekeying to `synthesized` alone would
leave such a file with an empty analysis reason set, so `analysis_transcode` would
read `None`, the command line would report it already analysis-clean and return,
and the refusal specified below would never be reached on the analysis target.

Adding `synthesized` alongside costs nothing: the re-encode reason set is tested
first, so the operation is still a re-encode, and the reason set carries something
true of the file.

### A stream copy cannot recover this timing

One new reason, `presentation_timing_requires_decode`, says that correct
presentation timing for this source cannot be produced by copying its packets --
only by decoding them. It fires in two cases:

- `timing_source` is `synthesized`. The packet-to-picture mapping is unknown
  without a decode: the bare AV1 stream's 74 packets are 50 pictures, and no
  measurement over packets can say so.
- The coded reordering depth is positive and `timing_source` is `decode`,
  `synthesized`, or `absent`. Presentation order is unknown without a decode,
  because a decoder recovers it from the bitstream's picture order and a copy
  does not decode.

It must **not** fire where `timing_source` is `presentation` and the reordering
depth is positive, which is the ordinary containerized case: real presentation
timestamps already carry the order, and firing there would re-encode most of the
corpus.

The reason is added to both `AnalysisReason` and `StreamReason`, and to the sets
selecting a re-encode for each target. Both copy remuxes reach the defect -- the
analysis target through the timebase remux and the playback target through the
container remux, which a raw source enters by way of `unsupported_container` --
so a reason present in only one set leaves the playback derivative carrying
shuffled timing.

It also joins `HARD_STREAM_REASONS`, so `playable` reads false and the playback
transcode is required rather than recommended. A hard stream reason means the
browser's rendering disagrees with this package's coordinate or time model, and
timing assigned to the wrong pictures is exactly a broken frame-index-to-time
mapping. Every source that fires the reason in today's corpus also fires
`unsupported_container`, so the classification changes nothing measurable now and
no test would catch it being wrong -- which is why it is settled here rather than
left to the implementation.

**Why a re-encode and not a timestamp-writing remux.** The timebase remux copies
packets and writes timestamps from `declared_fps`. For a synthesized-timing
source that field is the demultiplexer's own default, so the remux would bake the
invention in as the file's truth, and the copy would carry 74 packets forward as
74 declared frames over 50 pictures. Measured on such a derivative: it re-probes
`constant_frame_rate=True` with the full declared count, passes the acceptance
gate, and verdicts `analysis_transcode=None` -- a file the reader refuses,
laundered into one the verdict calls clean. Only a decode collapses packets to
pictures.

`timestamp_fps` is therefore keyed on `timing_source == "absent"` rather than on
the old boolean, so a synthesized source can never reach the timestamp-writing
path even if a future reason routes it there.

### `-fflags +genpts` stays

It is not confined to raw streams and must not be removed. A measured file
selecting the timebase remux -- the lying-header case -- gets `timestamp_fps` of
zero and therefore `+genpts`, and it is load-bearing there: an audio video
interleave source supplies only decode timestamps, and without the flag the mp4
muxer emits "pts has no value" once per packet. Two existing tests pin this
behavior and both stay as written.

### A source that states no rate anywhere

Where `timing_source` is `absent` and the rate the file itself states is zero --
`declared_fps`, which for such a source carries the bitstream's own rate and not
the demuxer's default -- the source has no timing at all, and this package will
not invent one. No command is built for either target: `build_command` raises
`TranscodeError`, naming that the source states no frame rate in its container or
its bitstream. `run_transcode`'s documented raise list gains the case, and the
command line already catches that type, so the refusal surfaces as a transcode
failure rather than a traceback. The raise leaves no partial state: output
resolution creates nothing, and the destination directory and temporary file are
made after.

**`TranscodeError` moves to `transcode/errors.py` first.** It is defined in
`convert.py` today, which already imports `commands.py`, so raising it from
`commands.py` would be a circular import and a breach of the one-way import rule.
A dedicated error module both import is the package's own precedent, matching
`probe/errors.py`. It stays re-exported from the transcode package, so the import
path the type is reached by does not change, and its docstring widens: it currently covers a transcode that ran
and failed or produced unclean output, and now also covers a refusal to build a
command at all.

The refusal takes precedence over every reason, including
`presentation_timing_requires_decode`. A re-encode is buildable without a source
rate, but its output rate would be an invention exactly as the muxer's fallback
is, so a rate-less source is refused rather than re-encoded.

**`elementary_stream_fps` extends to HEVC first, or the refusal swallows a whole
codec.** That function is gated on the format and codec both being `h264`, so
`declared_fps` is zero for every raw HEVC stream by construction, whatever its
bitstream states -- measured on a raw HEVC stream declaring 30 frames per second,
`declared_fps` reads `0.0` while `r_frame_rate` correctly reports `30/1`. Applying
the refusal predicate as written would refuse every raw HEVC source rather than the
"states no rate" class the issue scopes. The gate therefore widens to `hevc`,
which reports one tick per frame where H.264 reports two, so the H.264 divisor
does not carry over and the rate is the tick rate unchanged. Measured on a raw
HEVC stream copied from the committed 25 frames per second asset: the tick rate
reads `25/1`, so a divisor of one gives 25.0 where two would give 12.5.

The implausible-rate ceiling is re-measured for HEVC rather than assumed. It
rejects the demuxer time base echoed back, which is what leaves `declared_fps` at
zero for a stream stating nothing, and the effective ceiling halves when the
divisor does. The same measurement settles it: read what the raw HEVC demuxer
reports for a stream carrying no timing, and confirm the ceiling still rejects it.

This widening inverts `tests/probe/test_ffprobe.py`'s assertion that the rate is
absent for a raw HEVC stream, along with the comment explaining the exclusion.
That is an expectation change belonging to this widening rather than to the field
removal, and it is the only test pinning the exclusion.

This is what closes the deprecated-fallback exposure. The fallback is reached
today down two paths -- the timebase remux for the analysis target and the
container remux for the playback target, the latter never having used `+genpts`
at all -- and a refusal covers both, where removing a flag covered neither.

### Coded reordering depth

The probe records the bitstream's coded reordering depth, read from the stream's
`has_b_frames`. Measured: 0 for both committed raw fixtures, 2 for a raw stream
coded with bidirectional prediction, 2 for ordinary containerized sources, and 0
for the split AV1 stream.

### The reader's collapse check

Where two consecutive decoded pictures carry the same presentation time, the
reader raises, naming `self._target` -- the index the caller asked for -- as the
index at which it can no longer promise the frame belongs to the index it is
handed out under.

This is the other end of the range `_check_decode_gap` already measures, so it
costs no packet scan, no index, and no new fact. The zero-scan guarantee on the
injected-facts sequential and strided reads is unaffected.

**Why it cannot fire on a sound file.** The frame model counts distinct
presentation timestamps, and the reader reaches a target by counting decoded
pictures. Two pictures at one timestamp means the decode has produced more
pictures than the model has indices, so from that point every index names the
picture one position ahead of the one the model assigns it. The check fires
exactly when that has already happened. A duplicate on the last timestamp, or
past a bounded window end, is never decoded and never fires.

**The condition is exact equality, never a non-positive spacing**, because a
backwards step is a different defect with a different answer. Two pictures at one
timestamp mean the model counted one where the decoder produced two, which no
verdict can see and only this check catches. A backwards step means the decode
order and the frame model's sorted-timestamp order disagree -- which is the
reordering defect, the one `presentation_timing_requires_decode` now routes to a
re-encode at the verdict level, and which the index check already catches wherever
an index exists. Widening this rule to cover it would put a second, weaker
detector on a defect that already has a verdict-level answer.

The audio video interleave fixture is where both appear. It decodes 50 pictures
at 50 distinct times with 13 consecutive pairs running backwards, beginning
`0.04, 0.16, 0.20, 0.12, 0.24`, and it is `timing_source` of `decode` with a coded
reordering depth of 2 -- exactly the configuration the new reason fires for. It is
**not** a healthy source: its header declares 50 frames per second and 100 frames
against a measured 25 and 50, so it already verdicts `analysis_transcode=required`,
the existing gap check already raises on it at 4.0 frame periods, and the index
check raises at frame 1 with the seek index placing that frame at `0.04` where the
decoder delivered `0.16`. For the same reason the comparison is between
consecutive decodes only, never against a set of times already seen.

**Placement.** Ahead of the guards reading `max_timestamp_gap_frame_periods` and
declining above two frame periods, because a collapse is detectable whatever the
file's own spacing, and a format quantizing timestamps to a coarse tick is the
most able to put two pictures on one tick. Behind the frame rate guard
(`geometry.fps > 0`), which is what makes a frame's own time safe to read -- a
source with no measured rate has frame times of `None`, and two absent times
compare equal. The guard is keyed on the measured rate, never on `timing_source`.

The read of `_previous_decoded_time` and its update move together, ahead of those
guards. Moving the comparison without the assignment leaves the previous time
permanently unset on every path where the guards return, and the check is
silently dead.

## Identity

`timing_measured` is hashed into `video_uuid`. Replacing the field does not
change the hash input: `video_uuid_input` keeps taking a boolean, renamed to
`timing_supplied_by_source`, and every file whose value is unchanged keeps its
identity byte for byte. The re-mint is exactly the formats moving to
`synthesized`: `obu` and `mpegvideo`.

The boolean is produced by one named function taking a `timing_source` and
returning whether the file supplied the timing. Both the probe and the identity
test helper call it. The membership test is written once, or the helper --
which today hand-writes `timing_measured=source != "none"` -- drifts from the
probe the moment an invented-timing format reaches an identity fixture.

Both hash inputs are explicit field lists, so `timing_source` and the coded
reordering depth enter neither and add no re-mint of their own.

No identity scheme version bump. A bump re-mints every value in every corpus, and
the change reaches two formats. A stored `video_uuid` for a file of either format
no longer matches what the probe now mints, and re-probing is what reconciles it;
that is the only detectable effect.

## Tests

Existing tests whose expectations change:

- `tests/io/test_reader_recovery.py::test_a_source_declaring_more_frames_than_it_decodes_raises`
  asserts `analysis_transcode is None` for the bare AV1 stream. It inverts to
  `required`, and the selected operation is a re-encode. The companion asserting
  the Matroska sibling verdicts clean is correct as written and stays.
- `tests/transcode/test_commands.py::test_timestamp_less_source_without_a_rate_keeps_the_generated_timestamps`
  pins the behavior the refusal replaces. It becomes a raise assertion. This is
  the one test whose contract changes rather than its expected value.
- `tests/transcode/test_convert.py` asserts `output_facts.timing_measured is
  True` on transcode outputs. Those become `timing_source == "presentation"`,
  which is an expectation change rather than a rename: it asserts what the
  derivative's timing now *is*, not merely that some was measured.
- `tests/probe/test_duplicate.py` is built around the boolean's default-True
  hazard -- a row reconstructed from persisted columns reporting True whatever was
  probed. A required `Literal` has no default, so the test's premise changes, not
  only its symbol.
- `tests/probe/test_raw_stream.py` asserts on the field and its module docstring
  states such a file routes "never to a re-encode", which stops being true for the
  invented-timing formats.
- `tests/probe/test_verdict.py`'s hand-built `CLEAN` facts,
  `tests/transcode/test_commands.py`'s clean baseline and its
  `TimestampLessOverrides` typed dictionary, `tests/probe/test_probe.py`, and
  `tests/probe/test_identity_probe.py` carry the field mechanically. The
  baseline's field-count comment is updated with them.
- `tests/probe/test_identity.py` builds its identity input as
  `timing_measured=source != "none"`, which agrees with the new rule only because
  no identity fixture is an invented-timing format. It calls the shared function
  named under Identity instead, so the two cannot drift.

Three source comments name the field and go stale with it:
`src/mosaic_media/probe/candidates.py`, `src/mosaic_media/probe/ffprobe.py`, and
three paragraphs of `src/mosaic_media/probe/facts.py`.

The two tests pinning `+genpts` for a lying header stay as written; that behavior
does not change.

New fixtures:

- A bare AV1 stream written from an ordinary encode rather than from the
  frame-split one, so the provenance fix is pinned without the frame-split
  stream's separate defect confounding it.
- A raw MPEG-2 video elementary stream, generated. It already verdicts
  `analysis_transcode=required` today through `unverified_frame_correspondence`,
  so it asserts on `timing_source` and on
  `presentation_timing_requires_decode` directly rather than on the transcode
  target. A default encode reads a coded reordering depth of 1, so it fires the
  reordering half as well; that is intended and is asserted.
- A raw H.264 stream coded with reordering, **generated** by `-c copy -f h264`
  from the committed `open_gop.mp4`, which reads a coded reordering depth of 2 and
  carries no packet timestamps. No encoder is involved, so the guard on
  copyleft encoders is not engaged, and this follows the existing precedent for
  deriving a raw stream from a committed container.
- A raw H.264 stream whose sequence parameter set carries no timing, **committed
  under `tests/assets/`**. This one cannot be derived by copying, and producing it
  needs an encoder the guard forbids naming, so committed media is the sanctioned
  route.
- A raw HEVC stream, generated by `-c copy -f hevc` from the committed HEVC
  asset, so no encoder is engaged. It pins the widened rate derivation end to end
  through the header read and the absence of the refusal on a rate-stating HEVC
  source -- the H.264 equivalent already has such a test, and without this one the
  widening is pinned only by a unit test over a synthetic payload.
- One fixture per remaining invented-timing format -- `yuv4mpegpipe`, `h263` and
  `jpeg_pipe` -- each pinning that it classifies as `synthesized`. All three are
  generated: raw video, and FFmpeg's native H.263 and motion JPEG encoders, none
  of which carries a copyleft obligation. The H.263 encode needs a scale filter,
  since the format accepts only a fixed set of dimensions; 128 by 96 is the
  smallest that works.
- The `h263` fixture additionally pins the fabricated rate, asserting that the
  probe measures a rate the source did not have. That is the case which shows why
  a synthesized-timing source cannot be trusted even when its timestamps look
  uniform, and it is the only measured instance of it.

The refusal is verified by asserting that no command is built and that the error
names the reason, for both targets. There is no argv to inspect at warning level
for that case, so the deprecation notice's absence stays pinned where it already
is -- the rate-stating source, whose existing test has a companion proving the
check can fail.

The collapse check is pinned on a reader subclass whose decoder emits one
presentation rank twice, since no file of that shape can be written -- the mp4
muxer refuses two packets sharing a decode timestamp outright. The existing
indexed behavior is unchanged and stays asserted: the sequential raise at frame
11, the seek raise at frame 15, and the healthy corpus reading clean across every
window shape `test_the_delivery_check_stays_silent_across_a_healthy_source`
sweeps.

## What travels outward

`MediaFacts` loses `timing_measured` and gains `timing_source` and
`coded_reordering_depth`, so any store holding these fields column by column
gains two and loses one.

**A stored row is re-probed, never backfilled.** The boolean does not map to the
literal: `True` covers `presentation`, `decode` and `synthesized`, and the
reordering reason turns on telling the first two apart, so any fill produces a
wrong verdict for some rows rather than an incomplete one. `coded_reordering_depth`
admits no backfill either, since zero is the value meaning no reordering.

`compare_for_duplicate` changes an observable outcome: two files of an
invented-timing format that compare `duplicate` today compare `timing_unknown`
after.

`presentation_timing_requires_decode` is one new reason literal, added to both
the analysis and the stream vocabularies and to the hard stream set. Both
vocabularies are exported `Literal` aliases, so a reader of a reason set widens
by re-typing against them rather than by enumerating.

## Out of scope

- The frame model's definition of a frame. It counts distinct presentation
  timestamps, and the Matroska sibling is a measured file on which that is right.
- Making the probe decode. It does not, and nothing here changes that.
- The deduplication in the seek index and the timing measurement.
- Measuring formats beyond the thirteen in the table. Every one that has been
  measured and fails is on the list; extending the measurement to further formats
  is separate work, and the list's documentation states that an unmeasured format
  is trusted so the gap is visible rather than assumed away.

## What this closes

- **The verdict defect.** A synthesized-timing source verdicts as requiring an
  analysis transcode and selects a re-encode, so no file the reader refuses is
  called analysis-ready, and no derivative of one is either.
- **`reordered-raw-streams-get-decode-order-timing`.** The reordering depth is a
  fact, the verdict expresses the reason, both copy remuxes give way to a
  re-encode, and a generated reordered fixture pins that its frames land in
  presentation order. Its last bullet is met by measurement rather than by work:
  the reason vocabularies are exported `Literal` aliases and nothing branches on
  an individual reason, so a new literal is additive.
- **`raw-stream-remux-relies-on-a-deprecated-muxer-fallback`.** A stream stating
  no rate is refused with a reason naming why, on both targets, and a committed
  fixture with no video usability information pins it.
- **`duplicate-timestamps-mislabel-a-factless-sequential-read`.** A sequential
  read holding facts and no index raises on a decoder emitting more pictures than
  the frame model counts, without building an index or running a packet scan on
  any path pinned at zero, and the indexed paths are unchanged.
