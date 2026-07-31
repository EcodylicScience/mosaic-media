# Raw elementary stream remux relies on a deprecated mp4 muxer fallback

## Problem

A raw H.264 elementary stream carries no packet timestamps, so a copy remux has
to obtain them somewhere. Where the stream's sequence parameter set states a
frame rate, the probe reads it and the remux writes the timestamps itself. Where
it states none, there is nothing to read: the remux falls back to `-fflags
+genpts`, the mp4 muxer synthesizes the timestamps, and ffmpeg logs

    Timestamps are unset in a packet for stream 0. This is deprecated and will
    stop working in the future. Fix your code to set the timestamps properly

Measured on a stream encoded without video usability information:
`declared_fps` reads `0.0`, the built command carries `+genpts` and no `setts`
filter, and the notice fires. The runner's `-v error` hides it.

The output is correct today on every FFmpeg the project targets. The exposure is
the wording: when that fallback is removed the remux stops producing timestamps
rather than producing wrong ones, and this class of file breaks outright on an
ffmpeg upgrade.

This document previously covered every raw elementary stream and was wrong twice
about why. It attributed the fallback to FFmpeg 7.0 dropping demuxer-side
`+genpts`; measured, the notice fires and the output is byte-identical on 6.1.1,
7.1 and 8.1 alike, so the fallback always did the work. It also claimed the
remuxed file carried real 25 fps timestamps; it never did, and the rate was not
25. Both are corrected here.

## Scope

Affected: a raw elementary stream whose sequence parameter set carries no
timing. Such a stream reports `r_frame_rate` as the demuxer time base echoed
back -- measured `1200000/1` -- which the probe rejects as implausible, leaving
`declared_fps` at `0.0`.

Not affected: a raw stream that states a rate, which is the common case and the
one every committed fixture exercises. The probe reads it, both copy remuxes set
their timestamps from it, and `tests/transcode/test_convert.py` pins the absence
of the deprecation notice, with a companion test proving that check can fail.

Not affected: any containerized source, which carries real timestamps.

## Why deferred

Nothing in the file states a rate, so nothing can be read from it. Closing this
means either synthesizing timestamps without a rate -- which is what the muxer
fallback already does, at an approximation nobody chose -- or deciding what a
stream with no declared timing should mean to this package. That is a design
question rather than a missing edit, and the current behavior is no worse than
what preceded it.

## What would close it

- A raw elementary stream with no declared rate either gets timestamps this
  package writes, or is refused with a reason that names why, rather than
  silently receiving the muxer's approximation.
- Whichever route is taken, the deprecation notice is absent at warning level,
  verified by a test that is shown to fail when the behavior regresses.
- A committed fixture with no video usability information pins it, so the case
  stops being reachable only through a generated stream.
