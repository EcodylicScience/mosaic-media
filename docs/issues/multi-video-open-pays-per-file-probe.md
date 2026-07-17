# Multi-video reader construction pays a per-file probe the consumer path never needs

## Resolution

`MultiVideoReader` now accepts keyword-only `facts` and `indices` sequences
parallel to the paths (length-checked; paths-only construction unchanged), so
a consumer holding ingestion `MediaFacts` pays neither the ffprobe subprocess
nor the packet rescan on open. `seek` additionally reuses the open segment
reader instead of reconstructing it per call. The junction workload gained a
gated injected-facts form (the consumer-shaped open) in
`tests/bench/test_sparse_and_multi.py`; the from-scratch form remains a
bounded report with its recorded rationale. The measured ratio for the
injected form is recorded in the spec's gate table.

## Problem

Constructing a `MultiVideoReader` runs `probe_media` for every file in the
sequence -- an ffprobe subprocess each -- and scans each segment's packets
in process before the first junction read. The performance suite's
from-scratch junction workload measures this honestly: ratio 0.739 against
OpenCV's header-only capture opens, while a raw two-container decode of the
same 200 frames measures 1.032. The deficit is open cost, not decode.
Consumers hold `MediaFacts` from ingestion (measurement is never re-derived)
and would inject them if the constructor accepted facts, but the pinned
constructor is `MultiVideoReader(video_paths)` with no injection seam, so
every open re-probes files whose facts the caller already has.

## Scope

Affected: `MultiVideoReader.__init__` in `src/mosaic_media/io/multi.py`
(per-file `probe_media`), the from-scratch open cost of every consumer that
constructs multi-video readers repeatedly, and the bounded junction report
in `tests/bench/test_sparse_and_multi.py` (bound `<= 1.5x`).
Not affected: `VideoReader` (accepts `facts=` and `index=`), decode
throughput across junctions (measured at parity or better), and the
single-open long-session consumer pattern where one probe per file at
construction is amortized.

## Why deferred

Adding a facts-injection parameter to `MultiVideoReader` changes a pinned
public constructor -- a surface change that belongs to the consumer
migration, where the callers that hold `MediaFacts` are being wired up and
the right injection shape (per-path mapping, parallel list, or a
facts-bearing path object) can be chosen against real call sites rather
than guessed here.

## What would close it

- The consumer migration decides the injection shape and extends
  `MultiVideoReader` to accept pre-probed facts (and optionally per-segment
  indices) without breaking the paths-only construction.
- With facts injected, the junction workload's from-scratch form is
  re-specified or joined by an injected-facts variant, and the measured
  ratio for the consumer-shaped open is recorded in the spec's gate table
  (raw-container decode parity, 1.032, is the expectation).
- The `<= 1.5x` bounded report either returns to a `>= 1.0` gate under the
  injected-facts form or keeps the bound with a recorded rationale.
- The full serialized performance suite stays green.
