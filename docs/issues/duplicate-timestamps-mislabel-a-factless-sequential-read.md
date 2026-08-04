# Two frames at one timestamp mislabel a sequential read that holds no index

## Problem

A container carrying two frames at one presentation timestamp makes the decoder
emit more frames than the frame model counts. `build_seek_index` and
`measure_timing` both deduplicate presentation timestamps by design, so the
index and `frame_count` see one frame where the decode produces two, and every
index after the duplicate is one behind the picture it names.

Measured on `tests/assets/cfr_30fps.mp4` with a `VideoReader` subclass whose
decoder emits presentation rank 10 twice -- the null-frame shape's mirror --
with honest facts:

| reader | outcome |
| --- | --- |
| sequential, facts injected, no index | 60 delivered of 60 declared, **no error**; 49 of the labels carry the wrong picture (every index 11 through 59, each one frame late) |
| sequential, facts and index injected | raises at `frame 11` |
| `seek(15)`, index injected | raises at `frame 15` |

So the reader is covered wherever an index exists, and uncovered in exactly the
region `_check_decode_gap` covers: facts injected, never seeked.

**The gap check cannot reach this shape, under any threshold.** Two frames at
one timestamp are 0.0 frame periods apart, and the check fires only above its
threshold. That is structural, not a matter of tuning: a missing frame widens
the spacing and a duplicated one collapses it, and only the first is a gap. The
per-file threshold introduced for coarse-timescale sources changes nothing here.

The file also probes clean -- `constant_frame_rate` True, no analysis reason,
`analysis_transcode` None -- so nothing upstream of the reader flags it either.

**This is not a regression.** Before the delivery checks landed, every reader
returned the mislabeled frame silently, including the seeking ones. The raise on
the indexed paths is an improvement; what remains open is the contract's first
clause, that a probed analysis-ready video reads cleanly and every index within
`frame_count` returns the frame that belongs to it.

## Reproducing

Two frames at one timestamp at the **packet** layer now has a generated
instance. Two *decoded pictures* at one timestamp, which is the shape above,
still does not.

`av1_frame_split` separates an AV1 temporal unit into its constituent frames. A
unit carries a hidden alternate-reference frame beside the visible one, so a
50-frame encode becomes 74 packets, and what those packets mean is decided
entirely by the container they are written to. One encode written two ways
produces both outcomes -- `av1_split_clips` in
`tests/helpers/media_fixtures.py`, differing in nothing but the muxer:

| container | packets | distinct timestamps | pictures | `frame_count` | reader |
| --- | --- | --- | --- | --- | --- |
| Matroska | 74 | 50 | 50 | 50 | clean, pixel-exact, every seek lands |
| bare stream | 74 | 74 | 50 | 74 | raises at frame 1, both reader paths |

Both probe analysis-clean: constant rate, no analysis reason, no discard flags,
no leading non-keyframes, neighboring timestamps exactly one frame period apart.
Reproduced on `libsvtav1` and `libaom-av1`.

**The Matroska instance is the deduplication's own case, and it is benign.** Two
packets share a presentation timestamp, the deduplication drops one, and the
reader delivers all 50 pictures with every seek landing pixel-exact -- because
the hidden packet decodes to no picture, so the decoder emits exactly one per
distinct timestamp. That this holds had been reasoned from format documentation
rather than measured. `seek-index-counts-duplicate-timestamp-packets.md` demanded
the empirical step in its own *Why deferred* -- characterized against a generated
`-auto-alt-ref` VP8 clip "before choosing the rule" -- and its Resolution
abandoned it after five attempts produced no duplicate-timestamp packets, closing
on constructed-packet unit tests instead. The premise now has a measured instance
behind it, on AV1 rather than VP8.

**The bare-stream instance is the null-frame shape rather than this one**, and it
is where the same packets stop being benign: with no container timestamps, the
demultiplexer synthesizes one per packet, so the hidden frames become declared
frames that no decode produces. It is covered -- both delivery checks raise at
frame 1 -- and it is the first instance of that shape as a file rather than a
decoder stand-in.

**A check comparing packet count against distinct timestamp count is
anti-correlated with the defect, not merely unsound.** The benign file carries 74
packets over 50 timestamps; the defective one carries 74 over 74. That comparison
fires on the healthy file and stays silent on the broken one, on the only
measured instances of either shape. Pinned by
`test_a_source_whose_hidden_frames_share_a_timestamp_reads_clean` and
`test_a_source_declaring_more_frames_than_it_decodes_raises`.

What still has no file is this issue's own shape, two decoded pictures at one
timestamp, and that bounds how urgent it is:

- The mp4 muxer refuses to write it: two packets sharing a decode timestamp fail
  with `av.error.ArgumentError: Invalid argument ... returned 22`, before any
  file is produced.
- It was reached with a `setpts` filter expression, not from natural media.
- VP8 and VP9 alternate-reference packets are the natural candidate and do not
  produce it. Measured on freshly encoded `libvpx` and `libvpx-vp9` clips: 50
  packets, 50 distinct presentation timestamps, 50 frames decoded, worst
  neighbor spacing 1.000 periods. The invisible packet does not reach the packet
  layer as a separate timestamp.
- The AV1 split above reaches the packet layer but not the decoder: its
  duplicate packet is hidden by construction, which is what makes that instance
  benign.

The subclass above is the reproduction that does not need a file: emit one
decoded frame twice and read sequentially with facts injected.

## Scope

`VideoReader`'s delivery checks, and only the configuration where neither
reaches: facts injected, no index built, no seek. Everything else already
raises.

Not in scope:

- The deduplication itself. `build_seek_index` and `measure_timing` deduplicate
  deliberately -- an alternate-reference packet sharing a visible frame's
  timestamp is the case that rule exists for, and
  `seek-index-counts-duplicate-timestamp-packets.md` records what happened when
  they disagreed. The frame model counting distinct timestamps is settled, and
  the Matroska instance above is a measured file on which it is right.
- `_check_decode_gap`'s threshold. No value of it reaches a 0.0 spacing.
- The probe. A file of this shape is measured correctly; the divergence is
  between the frame model and the decoder, not inside the measurement.

## Why deferred

Closing it means detecting a decode that runs ahead of the index without holding
an index, which is the constraint that shaped both existing checks: the
injected-facts sequential and strided reads are pinned at zero packet scans
because the performance gate measures them, so building one is not available.
A count of decoded frames against `frame_count` would see it only at end of
stream and only for an unbounded window -- the same two limits that made the
delivery count insufficient for the null-frame shape.

The shape is also not known to occur on media this stack ingests: no muxer
here writes it, and the natural candidate does not produce it. That makes it
worth a correct answer rather than an urgent one.

**Closing it is a frame-model question rather than a fix**, and the two AV1
instances are what show that. The model defines `frame_count` as the number of
distinct presentation timestamps. The benign file depends on that definition --
its 74 packets are 50 frames only because duplicates collapse -- while the
defective one violates it, declaring 74 frames over 50 pictures with one packet
per timestamp. Nothing at the packet layer separates them; the timestamps
themselves are what disagree with the pictures. Only a decode tells them apart,
and the probe decodes nothing, which is a stated invariant of this package
rather than an implementation choice. Any rule reaching this shape therefore
either changes what a frame is or makes the probe decode, and both are larger
decisions than a reader-side check.

## What would close it

- A sequential read with facts injected and no index either delivers the frame
  belonging to each index it hands out, or raises naming the index at which it
  can no longer promise that -- on a source whose decoder emits more frames than
  the frame model counts, and without building a seek index or running a packet
  scan on any path currently pinned at zero.
- The existing indexed behavior is unchanged: `frame 11` sequentially and
  `frame 15` on a seek still raise, and the healthy corpus still reads clean
  across the window shapes `test_the_delivery_check_stays_silent_across_a_healthy_source`
  sweeps.
- A pin built on the duplicating subclass, since no file of this shape can be
  committed.
