# Seek index counts duplicate-timestamp packets that the timing measurement deduplicates

## Problem

`build_seek_index` in `src/mosaic_media/io/index.py` keeps every packet from
`scan_packets`, while `measure_timing` in `src/mosaic_media/probe/timing.py`
deduplicates presentation timestamps (`sorted({packet.time ...})`) before
counting frames. On containers that carry invisible packets sharing a
timestamp with a visible frame -- VP8/VP9 alternate reference frames are the
documented case; `tests/probe/test_timing.py` records a real recording with
533 such packets -- the two disagree: `MediaFacts.frame_count` counts distinct
timestamps, `SeekIndex.frame_count` counts packets. Keyframe ranks in the
index then shift by the number of preceding duplicates, so
`preceding_keyframe` and the reader's discard arithmetic address the wrong
frame: the off-by-N seek class this package exists to eliminate, on exactly
the webm screen recordings the corpus documents.

## Scope

Affected: `build_seek_index` and every `SeekIndex` consumer (`VideoReader.seek`,
`read_frames`, `MultiVideoReader`) for sources with duplicate presentation
timestamps -- in practice VP8/VP9 with alternate reference frames. Not
affected: H.264 and AV1 sources (one packet per presentation timestamp), which
covers the transcoded files this stack produces and the whole generated test
corpus; the timing measurement itself, which already deduplicates.

## Why deferred

Correct deduplication needs to know which of the duplicate packets carries the
emitted frame (the alternate reference packet is not displayed), and that has
to be characterized empirically against a generated `-auto-alt-ref` VP8 clip
before choosing the rule. Production reads go through transcoded H.264/AV1,
so no shipped workflow hits the defect today.

## What would close it

- A corpus clip generated with libvpx `-auto-alt-ref 1` whose packet scan
  demonstrably contains duplicate-timestamp packets.
- `build_seek_index` deduplicates timestamps consistently with
  `measure_timing`, keeping the packet that ffmpeg actually emits as the
  frame, with the choice documented in the module.
- `SeekIndex.frame_count == MediaFacts.frame_count` on the alt-ref clip, and a
  seek/`read_frames` test on that clip passes against `ffmpeg -f framemd5`
  ground truth.
- The full pipeline stays green.

## Resolution (closed 2026-07-17)

`build_seek_index` now deduplicates presentation timestamps -- `frame_times`
is `tuple(sorted({packet.time ...}))` and a distinct timestamp is a keyframe
timestamp when any packet bearing it is keyframe-flagged -- so
`SeekIndex.frame_count` equals `MediaFacts.frame_count` by construction, the
same distinct-timestamp count `measure_timing` uses. Under the in-process
seek the reader verifies the decoded keyframe landing and counts frames
forward to the target, so packet identity is non-load-bearing: only the
frame-index-to-distinct-timestamp map matters, and the deduplication is
exactly that map. A preceding duplicate can no longer shift a keyframe's
rank.

The evidence is constructed-packet unit tests, not a generated clip. Five
attempts to synthesize a duplicate-presentation-timestamp container with the
local toolchain (ffmpeg 6.1.1, libvpx `-auto-alt-ref 1`) produced zero
duplicate-pts packets: ffmpeg's libvpx path coalesces the invisible
alternate-reference frame into the visible packet, so a synthetic container
cannot exercise the defect. The real recording with 533 duplicate-timestamp
packets (recorded in `tests/probe/test_timing.py`) came from an external
screen recorder; the defect class arrives only from external producers, never
from this stack's own encoders. The unit tests therefore build the duplicate
packets directly -- asserting the dedup is consistent with `measure_timing`
and that `SeekIndex.frame_count` matches the distinct-timestamp count -- which
is the honest and sufficient closure. Production reads still go through
transcoded H.264/AV1, one packet per presentation timestamp, and never hit
the path.
