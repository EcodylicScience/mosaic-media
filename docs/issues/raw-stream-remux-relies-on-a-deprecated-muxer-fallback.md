# Raw elementary stream remux relies on a deprecated mp4 muxer fallback

## Problem

`REMUX_TIMEBASE` copies a raw H.264 elementary stream into mp4 to give it the
timestamps a container carries and a bare stream does not. On FFmpeg 6.x,
`-fflags +genpts` generated those timestamps at the demuxer. From FFmpeg 7.0 it
does not: the copy hands the mp4 muxer packets whose timestamps are unset, and
the muxer falls back to synthesizing them itself, logging

    Timestamps are unset in a packet for stream 0. This is deprecated and will
    stop working in the future. Fix your code to set the timestamps properly

The output is correct today. Verified on FFmpeg 8.1: the remuxed file carries
real 25 fps timestamps, `timing_measured` is `True`, `constant_frame_rate` is
`True`, and the frame count is preserved, which is what
`test_raw_h264_remuxes_into_measured_timing` asserts. The message is
warning-level, so the runner's `-v error` suppresses it and nothing surfaces.

The exposure is the wording: when that fallback is removed, this remux stops
producing timestamps rather than producing wrong ones, and the raw-stream path
breaks outright on an ffmpeg upgrade. Raw `.h264` files are a real input class
here -- tracking boxes record them, and the probe measures their frame count by
packet scan precisely because the container cannot answer.

## Scope

Affected: the `REMUX_TIMEBASE` command for sources with no container timestamps,
in `src/mosaic_media/transcode/commands.py`, and its acceptance test in
`tests/transcode/`.

Not affected: every source that arrives with container timestamps. The
deprecation fires only where the demuxer supplies none, which is the raw
elementary stream case.

Four alternatives were measured and none restores demuxer-side timestamps:
`-r 25`, `-framerate 25`, `-f h264 -r 25`, and `-bsf:v setts=ts=N/25/TB`. The
bitstream filter silences the warning but writes a wrong average rate
(`9000/359` rather than `25`), so it trades a future break for a present
inaccuracy and was rejected.

## Why deferred

The remux is correct on every FFmpeg the project currently targets, including
the 8.1 build the deployment image pins, so nothing is broken now. Closing it
means finding an invocation that sets timestamps at the demuxer or the
bitstream level without misdeclaring the rate, and none of the obvious ones
does; that is exploratory work rather than a known edit.

## What would close it

- The raw elementary stream remux produces timestamps without relying on the
  mp4 muxer's fallback, verified by the absence of the deprecation message at
  warning level rather than by the output alone.
- The declared average frame rate of the output matches the real packet rate
  exactly, not an approximation of it.
- `test_raw_h264_remuxes_into_measured_timing` still passes, and a test pins
  the declared rate so a filter that silences the warning by misdeclaring the
  rate cannot satisfy this.
