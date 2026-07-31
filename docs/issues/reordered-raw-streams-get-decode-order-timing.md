# Reordered raw streams get decode-order timing

## Problem

A raw H.264 elementary stream carries no timestamps, so a copy remux has to
synthesize them from the packet index. Packets arrive in decode order. For a
stream coded with bidirectionally predicted frames, decode order is not
presentation order -- a frame that references a later picture is transmitted
after that picture -- so the timestamps written are shuffled relative to the
pictures they label.

Measured, with the recipe recorded so the result can be checked rather than
taken on trust. Generate a reordered raw stream, remux it with the command this
package builds, and read the decoded frames' presentation times:

```bash
ffmpeg -v error -y -f lavfi -i "testsrc2=size=128x96:rate=30" -frames:v 30 \
    -c:v libx264 -bf 2 -profile:v high -f h264 reordered.h264
ffmpeg -v error -y -i reordered.h264 -c copy \
    -bsf:v "setts=ts=N/30.000000/TB" -movflags +faststart reordered.mp4
ffprobe -v error -select_streams v:0 -show_entries frame=pts_time \
    -of default=nw=1:nk=1 reordered.mp4 | head -8
```

    written:  0.000000  0.066667  0.100000  0.033333  0.166667  0.200000  0.133333  0.266667
    correct:  0.000000  0.033333  0.066667  0.100000  0.133333  0.166667  0.200000  0.233333

The fourth frame carries a lower timestamp than the second. The sequence is
identical under FFmpeg 6.1.1, 7.1, and 8.1, for both the remux and the read, so
it is a property of the stream copy rather than of one build.

Read `pts_time` and nothing else. `best_effort_timestamp_time` is ffprobe's
heuristic repair of exactly this damage: on the same file it returns
`0.000000 0.066667 0.100000 0.166667 0.200000`, monotonic and clean, so a reader
who reaches for it -- and it is the field most tooling defaults to -- concludes
there is no defect.

This is not a regression. The muxer fallback that preceded the current command
produced the same ordering, plus a negative start timestamp; neither path is
correct for such a stream. What changed is that the package now writes these
timestamps itself rather than leaving them to ffmpeg, so the behavior is now
this package's to own.

The acceptance re-probe cannot see it. On that same output it reports
`fps=29.99999`, `constant_frame_rate=True`, the full frame count, and
`start_time=0.0` -- every check the transcode makes passes, because the
timestamps are uniformly spaced and complete. Only their assignment to pictures
is wrong.

## Scope

Affected: a source whose packets carry no timestamps and whose bitstream is
coded with frame reordering. Both copy remuxes reach it, and a re-encode of the
same source does not -- a decoder recovers presentation order from the
bitstream's picture order count, which is exactly what a stream copy cannot do.

Not affected: any containerized source, which carries real decode and
presentation timestamps; and any raw stream coded without reordering. The
Baseline and Constrained Baseline profiles forbid B-slices outright, because
buffering a future frame is incompatible with low-latency capture, and no raw
stream this package has seen is coded with reordering. That is a property of
those profiles rather than of capture hardware generally: a device emitting
Main or High profile can reorder, so this is not-yet-observed rather than
unreachable.

Detection would be cheap but is not in place. `ffprobe` reports the coded
reordering depth on the stream, reading 0 for every committed raw fixture and
non-zero for a reordered one, but nothing in this package reads that field
today -- neither the header nor the facts carry it.

## Why deferred

Closing it correctly means routing such a source to a re-encode rather than a
remux, and that is not a local change:

- **A schema change.** The verdict is derived from `MediaFacts`, so the
  reordering depth has to become a persisted fact. Every consumer that stores
  facts gains a column, and every stored row predates it.
- **New verdict reasons.** The reason set is what selects the command, so
  expressing "this source cannot be copy-remuxed correctly" needs a reason that
  does not exist yet.
- **A consumer change.** Reason sets are read and displayed downstream. A new
  reason is not additive for a consumer that enumerates them to decide what to
  show or what to offer.

Against that cost, the input class is unreachable from the devices that produce
raw elementary streams, and the current behavior is no worse than what preceded
it.

## What would close it

- The probe records the bitstream's coded reordering depth as a fact.
- The verdict expresses "presentation order is unrecoverable by a stream copy"
  as its own reason, in the analysis reason set at minimum.
- The command builder selects a re-encode for that reason instead of either
  copy remux, and the re-encoded output's frames land in presentation order.
- A committed fixture coded with reordering pins it, so the guarantee is
  measured rather than argued.
- Consumers that enumerate verdict reasons handle the new one rather than
  falling through.
