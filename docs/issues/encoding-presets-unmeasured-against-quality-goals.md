# Encoding preset quality is unmeasured against the stated goals

## Problem

`ANALYSIS_ENCODING` (SVT-AV1 crf 20, preset 6) and `PLAYBACK_ENCODING`
(crf 32, preset 8) in `src/mosaic_media/transcode/commands.py` were set by
encoder convention, not measurement. The goals they serve: near lossless for
the analysis derivative -- it replaces the original as tracker input under
the metadata authority, so quantization loss propagates into every
downstream measurement -- and visually lossless for the playback derivative.

By common encoder practice, crf 20 at preset 6 sits at the top of "visually
transparent for most content", one notch below near lossless, and crf 32 at
preset 8 is a web-streaming operating point, below visually lossless. Both
values therefore likely need a bump -- but published intuitions may not
transfer to this corpus, which spans dissimilar content classes:
static-camera arenas with fine texture and small moving subjects, but also
moving cameras over variable backgrounds and focal observations of large
subjects. The values should come from measurement on a representative set
rather than from convention in either direction.

A second defect: the single `quality` field feeds both SVT-AV1 `-crf` and
NVENC `-cq`, whose scales are not perceptually equivalent. A
hardware-permitted transcode silently lands at a different quality point
than the CPU path for the same parameters.

## Scope

Affected: the two shipped defaults, every analysis derivative produced from
a re-encode reason (variable frame rate, rotation, non-square pixels,
interlacing), and every playback derivative from a re-encode.
Not affected: remuxes (lossless by construction), clean files (never
re-encoded), and originals (always preserved, so derivatives can be
re-derived after a preset change).

## What would close it

- A measurement corpus of real recordings representative of the footage's
  full diversity -- at least: a static camera over a fixed arena with small
  moving subjects, fast subject movement, a moving camera over a variable
  background, and a focal observation of a large subject. One crf value has
  to hold across all of these (or be set by the hardest class), so an
  under-sampled set biases the chosen values toward whichever class it
  overrepresents.
- Both presets measured against their sources: PSNR and SSIM through
  ffmpeg's filters (VMAF too if a libvmaf-enabled build is available), plus
  the task-relevant check for the analysis half -- tracker output stability
  between original and derivative.
- The crf values chosen per goal from those measurements: analysis near
  lossless, playback visually lossless -- or the playback goal consciously
  restated as a scrub copy if the storage cost of transparency is not worth
  paying.
- Hardware and software quality decoupled: a separate NVENC `-cq` value
  tuned to match each chosen SVT-AV1 operating point, instead of one shared
  number.
- The chosen values recorded with their measurements in the preset
  docstrings, replacing the convention-derived comments.
