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
