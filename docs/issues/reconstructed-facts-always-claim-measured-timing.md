# Reconstructed facts always claim measured timing

A consumer that rebuilds `MediaFacts` from persisted columns without setting
`timing_measured` gets `True` on every row, because that is the field's default.
The reconstructed facts then assert timing was measured for files where it was
not.

## Why the default is `True`

`timing_measured` was added after `MediaFacts` already existed and defaults to
`True` so facts persisted before the field existed round-trip unchanged. That is
correct for its own migration and wrong as a value for a row that never carried
it: the safe default and the backward-compatible default point opposite ways.

The same shape recurs with `video_uuid` and `content_digest`, which default to
`""` for the identical reason. The difference is that an empty digest is
*detectably* absent, so `compare_for_duplicate` can guard on it. A `True`
timing flag is indistinguishable from a measured one.

## What it affects

A raw elementary stream probes with `timing_measured=False`, `fps` and
`duration` at `0.0` as placeholders, and a real `frame_count`. `.h264` is a
first-class candidate extension, so these rows are a supported population, not
an edge case.

Reconstructed with `timing_measured=True`, such a row claims a measured
`0.0` frame rate over a measured `0.0` seconds. Any consumer that reads the flag
to decide whether the timing floats are trustworthy is told the wrong thing.

`compare_for_duplicate` is defended: its step 2 guards on `duration` being
positive as well as on the flag, so the pair reports `"timing_unknown"` rather
than dividing by zero. That closes the crash, not the underlying
misrepresentation.

## What closing it needs

The consumer persists and restores `timing_measured` alongside the other
measured fields. No change in this repository.

Until then, treat the flag as unreliable on reconstructed facts and derive
timing trustworthiness from `duration > 0.0` instead, which is what this
package's own comparison does.

## Why it is not fixed here

Consumer persistence is coordinated separately and is out of scope for the work
that introduced the identity values. Recording it so the guard in
`compare_for_duplicate` is understood as covering for a known gap rather than as
unexplained defensiveness -- a later reader would otherwise be tempted to
simplify the duration clause away as redundant with the flag.
