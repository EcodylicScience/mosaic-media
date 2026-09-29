# Writer color conversion bias on bgr24 frames

## Problem

`FFmpegVideoWriter` (`src/mosaic_media/io/writer.py`) hands each frame to the encoder as

```python
video_frame = VideoFrame.from_ndarray(frame, format="bgr24")
for packet in self._stream.encode(video_frame):
```

on a stream whose `pix_fmt` is `yuv420p`. PyAV converts bgr24 to yuv420p through swscale
with its default flags. Those flags round the bgr24 conversion with a bias, and every
frame written comes back darker than it went in, most in blue.

Measured 2026-09-29 on 20 real frames (Princeton-Fish composite, 512x192 crops), written
with mosaic-media 0.3.4 (libsvtav1, CRF 14) and read back with the same package's reader,
mean signed error per channel in gray levels:

| Encode | (R, G, B) | Mean abs. error |
| --- | --- | --- |
| `FFmpegVideoWriter` (bgr24, default flags) | (-1.75, -1.87, -3.24) | 2.98 |
| ffmpeg CLI, libsvtav1, bgr24, default flags | (-1.73, -1.88, -3.13) | 2.50 |
| ffmpeg CLI, libsvtav1, bgr24, `-sws_flags accurate_rnd+full_chroma_int` | (-1.06, -1.18, -0.98) | 1.73 |
| ffmpeg CLI, libx264 CRF 1, rgb24, default flags | (-1.09, -1.20, -1.04) | 1.19 |

The reader contributes nothing: decoding the same source with the package's reader and
with PyAV's `to_ndarray(format="rgb24")` gives identical pixels. About -1.1 per channel
is the floor of any 8-bit RGB to yuv420p round trip; the rest (up to about -2.2 in blue)
comes from the bgr24 conversion's default rounding. On a flat (B, G, R) = (60, 120, 200)
frame the default path decodes (-3, -1, -2) off, the accurate path (0, 0, -1).

Each encode applies the bias again, so a derivative of a derivative is darker still.
mosaic's analysis transcodes, joined exports and AV1 media variants all write through
this writer. mosaic fixed the same bias in its own libx264 pipe (its H.264 variant
writer) by passing `-sws_flags accurate_rnd`.

## Scope

- In scope: the conversion inside `FFmpegVideoWriter.write` for both encoders it drives
  (libsvtav1 and av1_nvenc), and a round-trip test.
- Not in scope: the reader, the probe, the encoder settings (CRF, preset), and the -1.1
  floor, which no 8-bit yuv420p encode avoids.
- In the one downstream accuracy check so far (Princeton-Fish pose models, 12 tanks), the
  bias moved no metric measurably: PCK@0.05 stayed within 0.003 of a reference encoded
  from PNG. A model trained on frames written one way and run on frames written the
  other sees a systematic shift, so the risk is largest for training data.

## Why deferred

It was found by a mosaic workflow test while mosaic's media-variants branch was under
review, and that branch does not change mosaic-media. PyAV's `VideoFrame.reformat`
exposes an interpolation choice but no swscale rounding flag, so the fix needs a choice
between feeding rgb24 and converting through an `av.filter` graph, then a release and a
floor raise in mosaic.

## What would close it

- A frame written by `FFmpegVideoWriter` and read back with the package's reader has a
  per-channel mean signed error within 1.5 gray levels of its input, pinned by a test on
  a lossless-known, non-gray source (a flat (B, G, R) = (60, 120, 200) frame fails today
  on blue).
- The fix holds for libsvtav1 and, where a device exists, av1_nvenc.
- The writer's public contract (bgr24 frames in, mp4/av1/yuv420p out) is unchanged, or
  the change is stated in the README and the changelog.
