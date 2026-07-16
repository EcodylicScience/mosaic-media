# Encoder hardware gates check the encoder listing, not device usability

## Problem

`encoder_available(name)` in `src/mosaic_media/hwaccel.py` answers "does
`ffmpeg -encoders` list this name" -- a build-time property. Distribution
ffmpeg builds list `h264_nvenc` and `av1_nvenc` on machines with no usable
NVIDIA device (this machine does). The decode side was fixed to probe real
device usability (`nvdec_available` now runs an `-init_hw_device cuda` null
decode), but both encode-side consumers still gate on the listing:
`FFmpegVideoWriter` (`hwaccel and encoder_available("h264_nvenc")`) and the
transcode command builder (`allow_hardware and
encoder_available("av1_nvenc")`). With permission granted on a GPU-less
machine, both select an NVENC encoder that fails at startup. The failure is
loud -- the writer raises `MediaProbeError`, the converter raises
`TranscodeError` -- but the correct outcome is a silent fallback to the CPU
encoder, which the caller's permission already sanctions.

## Scope

Affected: the encoder-selection paths in `src/mosaic_media/io/writer.py` and
`src/mosaic_media/transcode/commands.py`, and `encoder_available` itself.
Not affected: `nvdec_available` (already usability-checked), CPU-only callers
(`hwaccel=False` / `allow_hardware=False`, the defaults), and machines whose
listing matches reality.

## Why deferred

The two consumers landed on separate branches with a shared contract pinned
before the decode-side fix changed the availability semantics; aligning the
encode gate is one policy decision that should be made once, for both
consumers, after integration -- not twice under separate reviews. The failure
mode is loud, opt-in, and absent on default settings.

## What would close it

- A usability-checked encoder probe in `hwaccel.py` (a cached null encode via
  `-init_hw_device cuda` mirroring the decode probe, or an equivalent), keeping
  the pinned `encoder_available(name: str) -> bool` signature or adding a
  clearly named sibling.
- The writer and the transcode command builder both gate hardware encoding on
  caller permission AND the usability probe, falling back to their CPU
  encoders when the device is unusable.
- Unit tests pinning the fallback on a stubbed unusable device, and the full
  pipeline green.
