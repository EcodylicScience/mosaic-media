# A playback transcode can introduce the very defect it rejects its output for

## Problem

`run_transcode` re-probes its output and refuses to report success when the
result still cannot play in a browser. That gate is correct and worked. What it
caught is a defect the transcode itself introduced:

```
TranscodeError: transcode of <source> produced output that still cannot play
in the browser (non_zero_start_time)
```

raised at `src/mosaic_media/transcode/convert.py:412`.

**The source's `start_time` is 0.** The playback command produced an output
whose first timestamp is not, so the blocking reason in the output was not
present in the input. The acceptance probe then declined the result, leaving the
caller with a failure it cannot act on: the reason names a property of a file the
caller never asked for and cannot inspect, and re-running is deterministic.

Reproduced twice on the same source, hours apart, on different runs. It is not a
flake, a timeout, or resource pressure.

## Reproducing without the file

The source is not redistributable, so here is its full probe fingerprint. Any
file matching it should exercise the same path:

| Property | Value |
| --- | --- |
| Container | `avi` |
| Video codec | `h264`, `yuv420p` |
| Dimensions | 2048 x 2080 (nearly square, no rotation, square pixels) |
| Duration | 9541.771248 s (about 2 h 39 m) |
| Frame rate | 30.000300003, constant |
| Frame count | 286256, matching the declared count |
| Audio streams | none |
| Video streams | 1 |
| `start_time` | **0** |
| Progressive | yes |
| Max keyframe interval | 66 frames |
| Max GOP | 272633 bytes |
| Prober | `n8.1.2-32-gcfa62de001-20260730 Lavf62.12.102` |

The playback verdict on this source is `required`, which is expected: `avi` is
not a browser container, so the file has to be rewritten regardless of its
timing being clean.

## A second case: a codec the target container cannot carry

The same command construction fails a different way on an analysis target, and
this one never reaches the acceptance probe at all:

```
[mp4 @ ...] Could not find tag for codec vp8 in stream #0, codec not currently
supported in container
[out#0/mp4 @ ...] Could not write header (incorrect codec parameters ?):
Invalid argument
```

The command elected to copy the video stream into an MP4 container. MP4 cannot
carry VP8, so the muxer refuses before a header is written. Nothing checks that
the chosen container can hold the codec the copy would preserve.

Two sources, both failing identically and deterministically:

| Property | Source A | Source B |
| --- | --- | --- |
| Container | `matroska,webm` | `matroska,webm` |
| Video codec | `vp8`, `yuv420p` | `vp8`, `yuv420p` |
| Dimensions | 480 x 360 | 480 x 360 |
| Duration | 302.333 s | 40.373 s |
| Frame rate | 30.0, constant | 29.9702528508, constant |
| Frame count | 9070 | 1210 |
| Audio | present | present |

Their playback verdict is already clear -- WebM plays in a browser -- so the
analysis target is the only transcode that ever runs against them. With it
failing, these sources can be viewed but can never be prepared for per-frame
analysis.

The two cases share a shape worth naming: the command is built without checking
what it will produce against what the target has to accept. One produced output
the probe rejected; the other produced a command the muxer rejected.

## Scope

Command construction for both targets, and the acceptance probe that judges a
playback result -- `run_transcode` in `src/mosaic_media/transcode/convert.py`
and the command builder it drives. Not the verdict logic: classifying these
sources as needing a rewrite is right in every case here.

The most likely mechanism, to be confirmed rather than assumed: an H.264 stream
carried out of AVI into a fragment- or edit-list-bearing container acquires a
composition-time offset on its first sample, which surfaces as a non-zero
`start_time` even though the source had none. If that is the cause, the fix
belongs in the command rather than in the gate -- the muxer flags that normalise
initial timestamps -- and the gate stays exactly as it is.

Whether other sources hit this is unknown. It was one file in twenty-two; the
other twenty-one produced accepted output. Any shared property of the failing
case beyond "AVI-wrapped H.264" has not been isolated, and the table above is
deliberately complete so a future reader can narrow it.

## Why deferred

Found while proving a deployment end to end, where the transcode was being
exercised in bulk for the first time. Diagnosing a muxer timestamp behavior and
changing the command that writes every playback derivative is its own piece of
work, and the gate that caught it means nothing incorrect shipped.

## What would close it

A source matching the first fingerprint transcodes for playback and its output
passes the acceptance probe, with a test that pins the output's `start_time` at
0 rather than only asserting the transcode returned. A VP8 source transcodes for
analysis and produces a file, with a test that pins the chosen container against
the codec that reaches it, so a copy into a container that cannot carry the
stream fails at construction rather than in the muxer. If the mechanism turns out
to be inherent to the container pairing rather than fixable in the command, then
the failure must at least carry what a caller can do about it, since today it
reports a property of an output the caller never sees.
