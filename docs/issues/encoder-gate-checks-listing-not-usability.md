# Encoder hardware gates check the encoder listing, not device usability

## Problem

`encoder_available(name)` in `src/mosaic_media/hwaccel.py` answers "does
`ffmpeg -encoders` list this name" -- a build-time property. Distribution
ffmpeg builds list `h264_nvenc` and `av1_nvenc` on machines with no usable
NVIDIA device (this machine does). The decode side was fixed to probe real
device usability (`nvdec_available` now runs an `-init_hw_device cuda` null
decode), but both encode-side consumers still gate on the listing:
`FFmpegVideoWriter` (`hwaccel and encoder_available("av1_nvenc")`) and the
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

## Update (2026-07-17): writer half resolved; transcode half re-scoped

The writer now probes usability idiomatically: it constructs an `av1_nvenc`
`av.codec.CodecContext` and opens it once (cached); on a GPU-less machine the
open raises `av.error.PermissionError` even though the wheel lists the
encoder, so the writer gates hardware encode on caller permission AND that
probe, falling back to `libsvtav1`. The writer half of this issue is done.

The remaining half is the transcode command builder
(`transcode/commands.py`, `allow_hardware and
encoder_available("av1_nvenc")`), which stays open and re-scoped: that
consumer runs system ffmpeg as a subprocess, where the in-process av probe
does not answer for the CLI binary's device access. The one-policy-for-both
premise dissolved with the architectural split -- the writer encodes in
process, the converter shells out. The fix there is a system-ffmpeg
usability probe in `hwaccel.py` (a cached null encode via
`-init_hw_device cuda`, mirroring the `nvdec_available` fix), gating
`av1_nvenc` on permission AND that probe with a `libsvtav1` fallback. Scope
now: `transcode/commands.py` and `hwaccel.py` only.

## Resolution (2026-08-19): transcode half closed

`hwaccel.encoder_usable(name)` opens the named encoder and encodes one frame,
cached per name beside `encoder_available`'s listing cache. `_selected_encoder`
in `transcode/commands.py` takes `av1_nvenc` only on permission AND that probe,
and returns `libsvtav1` otherwise, so a permitted encode on a machine that cannot
run the hardware encoder produces a file rather than failing at encoder startup.
`TranscodeCommand.encoder_name` and `TranscodeResult.encoder_name` record which
encoder ran, empty for a copy remux and for a no-op, because nothing else on the
result distinguishes a hardware encode from the CPU fallback the same permission
produces: the operation is `REENCODE_AV1` either way and the output measures as
`av1`. The CLI names it beside the operation.

Two departures from the plan in the 2026-07-17 update.

The probe does not use `-init_hw_device cuda`. That form answers a different
question and answers it wrongly for the encode side: the reported host is a pair
of GTX 1080 Ti, whose CUDA runtime initializes perfectly and whose Pascal silicon
has no AV1 encoder at all, so a device-init probe returns True on exactly the
machine the check exists to reject. The failure is NVENC refusing the AV1 GUID
when the encoder is opened. `_reencode_argv` passes no `-init_hw_device` either,
so opening the encoder is also what the transcode actually does. The frame is
256x256 rather than the decode probe's 64x64: NVENC declares a minimum encode
resolution per codec and AV1's is the largest of the family, so the smaller frame
would report False on a card that encodes AV1 correctly.

`encoder_available`'s signature is unchanged and `encoder_usable` was added
beside it, the sibling option this document offered. Changing the first in place
would have run a device probe for `libsvtav1` during test collection, where
`requires_svtav1` calls it to decide whether the AV1 acceptance suite can run.
