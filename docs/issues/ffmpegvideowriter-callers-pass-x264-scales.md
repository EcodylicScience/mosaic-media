# FFmpegVideoWriter callers still pass x264-scale quality arguments

## Problem

`FFmpegVideoWriter` encodes AV1. Its `crf` and `preset` parameters are the
x264-scale ones it accepted while it encoded H.264, kept so existing callers
keep the picture they asked for, and both emit a `DeprecationWarning`:

```python
av1_crf, av1_preset = self._resolve_quality(crf, preset, av1_crf, av1_preset)
```

Two call sites in `mosaic` still use them:

- `src/mosaic/behavior/visualization_library/interaction_crop.py` passes
  `preset="fast"`, an x264 preset name.
- `src/mosaic/tracking/pose_training/inference.py` passes `crf=23`, an x264
  rate, threaded from its own `crf: int = 23` parameter and so reachable from
  that function's public surface.

Neither is broken. The translation maps `"fast"` to SVT-AV1 preset 9 and 23 to
SVT-AV1 crf 30, which is the picture each asked for. But every call pays a
deprecation warning, and the compatibility layer is the only thing keeping two
incompatible numeric scales from being confused: on SVT-AV1, 23 is a
near-lossless rate rather than a middling one, so a caller that drops the
translation without changing the number silently inflates every output.

## Scope

Affected: the two call sites above, and `inference.py`'s own `crf` parameter,
which propagates the x264 scale to its callers and would need the same decision.

Not affected: this package. The translation in
`src/mosaic_media/io/writer.py` (`_resolve_quality`, `_AV1_PRESET_FROM_X264`,
`_av1_crf_from_x264`) is complete and tested, and the native `av1_crf` /
`av1_preset` parameters are available now.

Removing the deprecated parameters is a separate, later step and is not in
scope here: it cannot happen until both call sites move, and doing both at once
would break `mosaic` at the moment this package changed.

## Why deferred

`mosaic` has unrelated work in flight, and this migration is not urgent enough
to interleave with it: the compatibility layer makes the current behavior
correct, not merely tolerable. Landing the writer's codec change without
touching `mosaic` was the deliberate trade.

The x264-to-AV1 rate offset the translation uses is also approximate, and
`encoding-presets-unmeasured-against-quality-goals.md` owns measuring the real
mapping. A caller that migrates before that measurement lands would pick its
new numbers from the same unmeasured offset, then likely revisit them.

## What would close it

- `interaction_crop.py` passes `av1_preset` (an integer) instead of `preset`.
- `inference.py` passes `av1_crf` instead of `crf`, and its own `crf` parameter
  is renamed or documented as an AV1 rate, whichever its callers allow.
- No call site in `mosaic` triggers the `DeprecationWarning`, verifiable by
  running that suite with `-W error::DeprecationWarning`.
- The values chosen come from the measurement in
  `encoding-presets-unmeasured-against-quality-goals.md`, not from the
  approximate offset the compatibility layer applies.

Retiring `crf` and `preset` from `FFmpegVideoWriter` becomes possible once this
closes, and should be tracked separately when it does.
