# Amendments to the deliverable frame model spec

Design changes found during implementation, recorded here rather than written
into the spec. Each stayed proposed until the implementation confirmed it.

Both are confirmed and folded into
`docs/specs/2026-08-01-deliverable-frame-model.md`, which carries the
conclusions. This file keeps the measurements behind them, the alternatives
weighed and rejected, and the record of how each amendment moved from prediction
to measurement, and is archived alongside the spec.

---

## Amendment 1: `show_all` is scoped to a decode that starts at the stream start

**Status:** confirmed by implementation, and folded into the spec.

**Amends:** change 1, "The reader decodes with the frames-preserving decoder
flag"; the verification bullet that pins `show_all` as a no-op on healthy
sources; and the container-open table in change 4, whose `_ensure_container`
row reads `yes` for `show_all`. That row becomes `no`: under this amendment the
flag is not a container-open property at all, so the column no longer belongs in
a table keyed on container-open sites and is dropped from it. The other two rows
are unaffected, and `ignore_editlist` stays exactly as the table has it.

### What the spec says

> Applied unconditionally, on structural grounds: the flag shows frames *before
> the first keyframe*, so on a source starting at a keyframe there is nothing
> for it to act on.

The spec named open-GOP as the shape that could refute this and put it in the
verification set rather than assuming it safe. The verification covered
sequential reads, where the claim holds. It does not hold on the seek path.

### What is measured

"Before the first keyframe" is relative to where decoding starts, not to the
file. After a backward seek the decoder's segment begins at the seek target, so
the flag emits the leading pictures of the *previous* group -- whose reference
pictures the seek discarded.

On the committed `tests/assets/open_gop.mp4`, seeking to the keyframe at 0.480:

| | frames decoded |
| --- | --- |
| without `show_all` | 0.480, 0.520, 0.560, 0.600, 0.640 |
| with `show_all` | 0.400, 0.440, 0.480, 0.520, 0.560 |

The two extra pictures are not merely early. Their content does not match what a
sequential read produces at those timestamps, so they are decoder output against
missing references rather than reordered frames. The same happens at the
keyframe at 0.960.

Sequential reads are unaffected: 50 frames either way, byte-identical content.

### Why a gate on the measured count is not enough

Gating `show_all` on `leading_non_keyframe_frames > 0`, mirroring the
`ignore_editlist` gate, would leave the flag off for `open_gop.mp4` and fix that
case. It does not close the class. The mid-GOP avi in the spec's problem table
has 53 leading non-keyframes and 286256 packets, so it both needs the flag and
carries thousands of keyframes; under a count gate every seek to an internal
keyframe could inject the same artifact.

### Why the landing check did not contain it

This section was written before change 7 landed, as the argument for settling
the amendment ahead of it. Change 7 has since landed, and the prediction is
recorded here as measured rather than forecast.

At the time, the reader's landing check rejected a decode arriving before the
requested keyframe, so the artifact raised rather than being returned. Change 7
deliberately makes an earlier landing legitimate and decodes forward from it, so
the protective raise would disappear exactly when the artifact became
reachable -- which is why this could not wait for it.

Both halves are now confirmed against the landed change. The raise is gone: with
the flag applied unconditionally, the decoder-flag pin fails through its own
assertion rather than through a `MediaProbeError`, and
`test_open_gop_seeks_land_frame_exact` passes under that same regression, so the
violation is detectable only through the pin. And the artifact is reachable:
under an unconditional flag a seek to an internal keyframe followed by a seek
just below it returns content decoded from nothing -- `seek(12)`, `seek(11)`,
`read()` yields it at index 11, where the amended rule returns the correct
frame. The route is the reusable-decoder branch, which returns before any
landing verification, so no landing tolerance was ever going to contain it.

### The amended rule

`show_all` is applied when the decode segment begins at the start of the stream,
and not otherwise:

- a sequential read from frame 0, which decodes from the container's start
  without seeking;
- a positioning whose resolved preceding keyframe is index 0, which is where a
  source's leading non-keyframes live.

Every other positioning clears it. Packets preceding the first keyframe exist
only at the stream start, so that is the only segment where the flag has
legitimate work to do.

### Measured support for the rule

- Clearing the flag on an open container and then seeking decodes correctly:
  the 0.480 seek yields 0.480, 0.520, 0.560 with content matching a sequential
  read.
- Setting the flag and seeking to index 0 is harmless on a source with no
  leading non-keyframes: `open_gop.mp4` yields 0.000, 0.040, 0.080 with matching
  content.
- On sources that do carry leading non-keyframes, the container index begins at
  the first keyframe, so a backward seek below it raises
  `[Errno 1] Operation not permitted` -- the behavior change 7 already records.
  The flag is therefore set when such a source is opened and read from the
  start, and the clear-then-restore transition does not arise on the seek path
  for them.

The last point is a property of the corpus rather than a guarantee, so the
implementation settled it directly: restoring the flag after clearing it does
take effect on an open container. Measured functionally on a source cut
mid-stream with 24 leading non-keyframes -- default decode 25 frames, flag set
throughout 49, cleared and then restored 49 -- and structurally by re-enabling
the flag mid-container and observing the artifact reappear at a third keyframe.
That measurement carries its control, because it needs one: the artifact appears
at every internal keyframe when the flag is set, so its reappearance is on its
own equally consistent with being a property of that keyframe rather than of the
re-enable. With the flag frozen clear across the same three seeks, all three
decode clean -- so the reappearance is attributable to the transition and
nothing else.

### What this changes downstream

- The unconditional application in the reader's container-open path becomes a
  decision made per decode segment.
- `test_show_all_is_a_no_op_on_an_open_gop_source` and
  `test_open_gop_seeks_land_frame_exact` both stay, in their original roles.
  Neither is this amendment's regression pin: measured, a violation of the rule
  was caught by the landing check rather than by either test's assertions, and
  change 7 has since removed that check. Decoding forward from the earlier
  landing yields correct pixels from the requested keyframe onward, so a
  pixel-content assertion cannot see the violation at any target.
- The pin is `test_show_all_is_set_only_for_a_segment_starting_at_the_stream_start`,
  which asserts decoder state directly -- the flag is set when the resolved
  preceding keyframe is index 0 and on a sequential read from frame 0, and clear
  otherwise -- so no change to landing tolerance can mask it. It observes the
  flag through a test-local subclass of `VideoReader`, which keeps the
  observation out of the reader's public surface.
- The verification bullet gains the seek path: `show_all` is pinned as a no-op
  on a healthy open-GOP source across both a sequential read and a seek to every
  internal keyframe.

---

## Amendment 2: the backstop is a per-frame check on two mechanisms

**Status:** confirmed by implementation, and folded into the spec.

**Amends:** "The backstop, on both paths" -- specifically its opening claim that
the backstop never fires for a source the analysis verdict accepted, its
seek-path paragraph, and the end-of-stream count it prescribes for the
sequential path.

### What the spec says

Two mechanisms, one per path: on the seek path, the index resolution failing;
on the sequential path, one integer comparison against `source_frame_count` at
end of stream. It states that neither ever fires for an analysis-ready source,
because acceptance means trusted-set membership, "which is exactly the assertion
that every packet bearing a distinct timestamp yields a frame".

### What is measured

Neither mechanism catches a mid-file mislabeling, which is the defect they
exist to make impossible.

The seek path resolves where the decoder *landed*. A landing is a real packet
timestamp and is therefore always in the index, so the resolution succeeds; what
follows is an arithmetic count forward to the target, and nothing verified it.
Measured on a constant-rate source whose decoder never emits presentation ranks
2 and 3, with honest facts and an index built in process: `seek(5)` returned the
picture at index 7, `seek(8)` the one at 10, and `seek(20)` the one at 22 --
each `ok=True`, no error.

The end-of-stream count catches a short delivery only when the window runs out
early. A bounded window does not: it ends on a frame the source still had.
Measured on the same source, `end_frame=40` delivered 40 frames, 38 of them the
wrong picture, and terminated clean; with `frame_step=3` it delivered 14, 13 of
them wrong, and terminated clean. `start_frame`/`end_frame` is the documented
windowing API, so this is a supported call, not an exotic one.

The claim that the backstop never fires for an analysis-ready source also
assumes the property the backstop verifies. Trusted-set membership is asserted
per codec from a test corpus rather than proven per file, and the spec already
names "the trusted-set claim turning out wrong" as a case the backstop exists
for. That case is a probed, analysis-ready source; under the spec's design it is
read sequentially with facts injected, where neither mechanism reaches it.

### The amended rule

The backstop is a per-frame check made where the frame is delivered, so it
cannot depend on how the window ends. Two mechanisms, selected by what the
reader holds rather than by which public method was called, and never both:

- **With a seek index, each delivered frame is checked against the index entry
  for the index it is being returned under.** `_target` is an absolute source
  frame index on every path that reaches delivery, and the index is in absolute
  source ranks, so the entry is `frame_times[_target]`; `start_frame`,
  `frame_step` and the delivery count's origin choose which targets are visited
  and shift this mapping not at all. Tolerance is half a frame period, as the
  landing check already uses. Measured across every committed asset: the
  delivered frame's own time deviates from that entry by 0.000 periods,
  sequential and seek alike.
- **Without one, consecutive decoded frames are checked for the gap a missing
  frame leaves.** An index
  exists only when the reader was constructed without facts or has since
  seeked, so the uncovered region is exactly a caller injecting facts and
  reading forward -- the sequential and strided read, which must keep building
  no index because the performance gate measures it at zero packet scans. The
  gap needs only the previous decoded timestamp.

The first is strictly stronger: it compares each frame against the entry for its
own index, so it locates a mislabel rather than inferring one from spacing. It
is authoritative wherever it is available, and the gap check is gated off there,
so the two never overlap.

The threshold comes from the file, and `MediaFacts` gains a third required
field to carry it: `max_timestamp_gap_frame_periods`, the widest step between
neighboring presentation timestamps, measured by the same grid fit that decides
`constant_frame_rate`. The reader raises above that value plus half a period,
the margin the landing and index checks also carry.

A fixed threshold was tried first and is unsound. A container too coarse to
express its own frame rate quantizes the timestamps, so a constant-rate file's
neighbors land unevenly: 30 fps written into a 1/36 timescale measures 1.662
periods, 23.976 into 1/30 measures 1.595, and 25 into 1/30, 50 into 1/60 and 10
into 1/12 all measure 1.662. Every one of them is analysis-ready by this
package's own verdict, and a threshold of 1.5 raises mid-read on all five. The
corpus could not see it, being 1/15360 mp4 throughout, and timestamp
quantization is the thing the whole-file grid fit exists to see past.

Deriving the threshold from `constant_frame_rate`'s own drift bound was the
other candidate and was rejected on measurement. Drift bounds each timestamp's
deviation from its slot on the global grid, which bounds the neighbor step only
at `1 + 2 * drift` -- 2.0 periods under the shipped threshold, the exact spacing
one missing frame produces. Carrying the measured step instead needs no such
derivation, and the two behave identically on every class measured: both pass
the corpus, both decline on the quantized clips and the variable-rate fixture,
both raise on the null-frame shape. The measured step wins on a case they do not
share -- a clip whose rate ramps from 30 fps to 24 measures 1.611 periods of
drift, so the grid fit calls it variable, while no two neighbors sit more than
1.116 apart. A drift-derived threshold gated on `constant_frame_rate` exempts
that file from the check entirely; its own measured step keeps it covered.

**Above 2.0 periods the check declines rather than guessing**, and that is a
documented outcome, not a gap. One missing frame puts two neighbors at the sum
of the steps it spanned, which on a uniform file is 2.0; once a file's own step
plus margin reaches 2.0 the signal and the tolerance overlap and no comparison
of spacings can separate them. On the quantized clips the legitimate steps
alternate 0.833 and 1.662, so a frame lost between two short ones produces
exactly a step the file takes anyway. Those files keep the delivery count and
the index check; what they do not get is a verdict this mechanism cannot
support.

The same rule retires the variable-rate exemption the check first carried. A
genuinely variable source declines on its own measured step -- 2.248 periods on
a 30 fps recording with a 10 fps stretch -- rather than through a separate
`constant_frame_rate` gate. One rule covers both, and the mildly variable file
above is checked instead of exempted.

Both mechanisms read only the decoded frame's own timestamp and what the reader
already holds. Neither builds an index, runs a packet scan, or consults a
verdict -- the reader receives no profile and no thresholds, and acquiring a
verdict here would put policy in the one place this package keeps free of it.

### What this changes downstream

- The end-of-stream count stays, unchanged and in its original role: it reports
  a short delivery, which neither per-frame mechanism reports.
- The seek path's resolution failure stays, likewise unchanged. It reports a
  landing outside the index span; the per-frame check reports a delivery that
  is not the frame its index names. They are distinct events with distinct
  messages.
- The spec's claim that the backstop cannot fire for an analysis-ready source
  is withdrawn rather than repaired. What replaces it is a measured property:
  neither mechanism fires on any healthy source in the corpus, verified by
  sweeping 2400 window combinations -- every committed asset plus the pre-roll,
  the mid-stream cut and the variable-rate fixture, across `start_frame`,
  `end_frame` and `frame_step`, injected-facts and factless. The only errors
  raised were the two already-recorded ones: a raw elementary stream having no
  seek index, and the avi seek defect filed as
  `docs/issues/reader-cannot-deliver-frames-the-facts-declare.md`. A control run
  with the gap check disabled raised exactly the same set.
- `MediaFacts` gains `max_timestamp_gap_frame_periods` alongside change 4's two
  counts, required and undefaulted like them, so the same consumer migration
  carries all three. It is a measurement rather than a policy value: a caller
  reads it and does not re-derive it, which is the same rule every other fact
  follows.
- The pins are `test_a_seek_onto_a_skipped_rank_raises_rather_than_returning_a_neighbor`,
  `test_every_entry_point_rejects_a_frame_the_index_does_not_place_there`,
  `test_a_bounded_window_over_a_skipped_rank_raises`,
  `test_the_gap_check_covers_the_reader_that_builds_no_index` (which also
  asserts the scan count stays zero),
  `test_a_coarse_timescale_source_reads_clean` over each rate and timescale
  pair, `test_the_check_declines_where_the_files_own_spacing_reaches_the_signal`
  for the declining region, `test_a_ramped_rate_source_is_checked_rather_than_exempted`
  for the case a constant-rate gate would have dropped,
  `test_a_variable_rate_source_declines_on_its_own_spacing`, and
  `test_the_delivery_check_stays_silent_across_a_healthy_source` for the
  direction that must never fire.
