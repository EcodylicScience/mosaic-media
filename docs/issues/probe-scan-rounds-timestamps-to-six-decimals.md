# Probe scan rounds timestamps to six decimals

## Problem

`scan_packets` requests `pts_time` from ffprobe and parses the printed
decimal:

```python
"packet=pts_time,dts_time,size,pos,flags,data_hash",
...
time=float(columns[0]),
```

ffprobe prints `pts_time` with six decimals. A packet whose exact time is a
ratio of integers loses precision: on `time_base` 1/15360, the packet at tick
512 has the exact time 1/30 and parses to `0.033333`. The same query already
returns `pts` in ticks, and `time_base` is on the stream. The exact ratio is
available and discarded.

The in-process scan computes the same quantity from the ratio,
`float(packet.pts * time_base)` in `mosaic_media.io.packets`. The two scanners
disagree. Measured on an mp4 of 1745 packets at uniform 512-tick spacing,
`time_base` 1/15360:

| Quantity | `probe.ffprobe.scan_packets` | `io.packets.scan_packets_in_process` |
| --- | --- | --- |
| first three timestamps | 0.0, 0.033333, 0.066667 | 0.0, 0.03333333333333333, 0.06666666666666667 |
| fitted `fps` | 30.00000017201835 | 30.0 |

The worst per-packet difference is 3.33e-07. The docstring of
`scan_packets_in_process` permits disagreement only "on containers where
libavformat synthesizes pts that ffprobe reports as absent". An ordinary mp4 is
not that case.

`measure_timing` computes every timing figure from `Packet.time`. Each one
includes the rounding error, and `max_instantaneous_fps` includes the most of
it. Rounding makes consecutive deltas alternate between 0.033333 and 0.033334,
and `1.0 / min(deltas)` selects the smaller, reporting 30.000300003000977 for a
file of period 1/30. That is a relative error of 1e-5, against 6e-9 in `fps`.

## Scope

Affected: `Packet.time` from `probe.ffprobe.scan_packets`, and every reader of
it. `measure_timing` derives `fps`, `duration`, `max_drift_frame_periods`,
`max_timestamp_gap_frame_periods` and `max_instantaneous_fps` from it.
`video_uuid_input` hashes it once per packet.

Unaffected: `content_digest`, which does not serialize timestamps.
`io.packets.scan_packets_in_process` and the seek index built from it already
compute the exact ratio.

The rounding is a deterministic function of the ticks. Values minted today
reproduce. The defect is precision. Identities minted under the current scheme
are stable.

Bounded fix: `scan_packets` requests `pts` and `dts` alongside the stream time
base and computes `time` by division. Plain float division is enough. 512/15360
in double precision is the nearest double to 1/30, and the fitted rate comes
back 30.0. The core needs no `Fraction`.

## Why deferred

`video_uuid_input` appends `_encode_float(packet.time)` for every packet, so
changing `Packet.time` re-mints every `video_uuid` in every corpus. The note
above `IDENTITY_SCHEME` requires the bump and records that a bump re-mints
`content_digest` along with it, and that a bump is always a minor version bump
of this package.

The frame-rate estimator work is separate and costs none of that, because no
identity input hashes `fps`. Splitting them keeps a re-mint out of a change
that does not need one.

## What would close it

- `probe.ffprobe.scan_packets` computes `Packet.time` from the integer
  timestamp and the stream time base.
- `measure_timing` reports `fps` 30.0 and `max_instantaneous_fps` 30.0 for a
  committed fixture with uniform 512-tick spacing on `time_base` 1/15360.
- A test asserts both scanners return equal timestamps for that fixture, and
  the docstring of `scan_packets_in_process` states the agreement it then
  satisfies.
- `IDENTITY_SCHEME` reads "3", the note above it records why, and the golden
  vectors in `tests/probe/test_identity.py` are regenerated.
- The package minor version is bumped, and README "Video identity" and
  "Versioning" describe the change.
- Consumers re-probe every ingested file and record the new
  `identity_scheme`.
