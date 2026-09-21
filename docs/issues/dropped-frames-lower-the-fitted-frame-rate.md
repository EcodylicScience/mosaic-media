# Dropped frames lower the fitted frame rate

## Problem

`measure_timing` fits the rate as `fps = (frame_count - 1) / span`, dividing
the timestamp span by the interval count. An interval spanning several grid
periods counts as one, and the missing periods reduce the rate.

Measured on two chunks from one recording system, both written on `time_base`
1/15360 with every packet 512 ticks apart, a period of 1/30. One is unbroken.
The other has a single 2048-tick interval at its last position, three missing
frames:

| Chunk | Packets | `fps` |
| --- | --- | --- |
| unbroken | 1745 | 30.00000017201835 |
| one 4-period gap | 1742 | 29.94839466713529 |

`uniform_properties` then refuses the pair:

```
property mismatch across sequence: fps 30.00000017201835 vs 29.94839466713529
```

The drift is 3.0 frames over the shorter chunk, against the 0.5 budget in
`_MAX_FRAME_DRIFT`. Two chunks of one rate cannot form a sequence.

`_reencode_argv` also writes the fitted rate into the analysis transcode, as
`-r {fps_value:.6f} -fps_mode cfr`. Measured through ffmpeg's null muxer:

| Output rate | Frames | Result |
| --- | --- | --- |
| `-r 29.948395` | 1742 | dup=1 drop=1 |
| `-r 30` | 1745 | dup=3 drop=0 |

At the fitted rate the dropped frame appears by the `frame=1352` progress line,
away from the gap. That repair discards a frame from an unbroken part of the
file and shifts every timestamp after it. At the grid period every frame is
preserved at its slot, and duplication fills the three missing frames at
the gap. The error grows with duration: 0.17% over 58 seconds costs one frame,
and the six-minute chunk from the same system would cost about eighteen.

### A misnegotiated chunk probes clean

A third chunk from that system declares 31 fps in its container and 31 in its
H.264 sequence parameter set (`time_scale` 62, `num_units_in_tick` 1), against
30 in both places for the other two. Its timestamps are a uniform 1/31 grid, so
it probes `constant_frame_rate: true` with an empty `analysis_reasons`, and
`_select_operation` returns None for it. The rate is wrong by 3.3%, and a
single-file probe cannot detect it, because the file is internally consistent.
Only a comparison across the sequence shows it.

Repairing it means writing a 30 fps grid over the existing packets, a copy
remux. `_copy_remux_argv` already writes timestamps from a rate, as
`setts=ts=N/{timestamp_fps:.6f}/TB`, but `build_command` supplies that value
only through `timestamp_fps = facts.declared_fps if facts.timing_source == "absent"
else 0.0`. A chunk with presentation timestamps does not qualify. That gate is
correct as written, because from inside one file a declared rate may be the
header error the remux exists to correct. The rate that authorizes the repair
is a property of the sequence, and `canonical_fps` computes it one level above
this package's per-file decision.

## Scope

Affected: `fps` and `duration` from `measure_timing`; `uniform_properties` and
`canonical_fps`, which read them; the `-r` in `_reencode_argv`; the timestamp
gate in `build_command`; `derive`, once the disagreement becomes a verdict
reason.

Unaffected: `video_uuid` and `content_digest`, neither of which hashes a rate.
`frame_count`, `max_timestamp_gap_frame_periods` and the drift measurement
already describe the gap.

Bounded fix, in two parts. Count grid slots: take a period from the deltas,
convert each interval to `round(delta / period)` slots, and fit `fps =
total_slots / span`. Measured against the three chunks, that returns
30.00000017201835 for both 30 fps chunks and 31.00000004325984 for the 31 fps
one, with the gapped chunk's drift at 3.0, which keeps it non-constant and
still requiring the analysis transcode. On a file without gaps the formula
reduces to today's. Then inject the expected rate: `derive` and `build_command`
take an optional rate from the caller, a disagreement emits a verdict reason on
both sets, and that reason selects `REMUX_TIMEBASE`, writing `setts` and an
`h264_metadata` tick rate so that the container and the bitstream agree.

Open design question for the spec. Slot fitting breaks the identity
`measure_timing` documents, that `frame_count / duration` reproduces `fps`. A
gapped chunk has 1742 frames and 1745 slots. `canonical_fps` computes
`total_frames / total_duration`, and `MultiVideoReader` lays out its global
frame space by frame count. Each gap compresses the concatenated timeline by
its width. Settle whether the slot count becomes a fact that the frame
space maps through, or whether the divergence is documented and both readers
keep counting frames.

## Why deferred

Ordered behind `probe-scan-rounds-timestamps-to-six-decimals.md`. Slot counting
survives the rounding unaided, because `round(delta / period)` tolerates the
1e-5 relative error. The period estimate is then usable for counting slots and
unusable as a rate: a median over rounded deltas reads 30.00030000299978 for a
file of period 1/30, since rounding makes consecutive deltas alternate between
0.033333 and 0.033334. Exact timestamps make the median the rate, and the
estimator becomes the median delta with no slot arithmetic at all. Ordering the
timestamp fix first avoids building slot arithmetic and then deleting it.

## What would close it

- `measure_timing` reports 30.0 for a fixture with one multi-period gap on an
  otherwise uniform 1/30 grid, equal to the rate it reports for the ungapped
  fixture, and reports that fixture non-constant.
- `uniform_properties` admits the pair.
- The analysis transcode of the gapped fixture runs at the grid period,
  verified by dup and drop counts: every source frame preserved, duplication
  only at the gap.
- `derive` and `build_command` accept an optional expected rate. A
  disagreement emits a reason on both reason sets and selects a copy remux
  that rewrites the timestamps and the H.264 tick rate.
- A fixture given an injected rate below its measured one produces that
  command, and the acceptance re-probe of the output reports the injected
  rate.
- The spec settles the frame-space question above, and `measure_timing`'s
  docstring states whichever relation then applies.
