# Thumbnail-dimension tests assert literal tuples instead of the sizing properties

## Problem

The `thumbnail_dimensions` tests in `tests/thumbnail/test_downscale.py` pin
pre-calculated result tuples rather than the properties the function
guarantees:

```python
def test_thumbnail_dimensions_caps_long_edge() -> None:
    assert thumbnail_dimensions(1024, 570) == (320, 178)
    assert thumbnail_dimensions(570, 1024) == (178, 320)
```

A literal like `(320, 178)` bakes the rounding of one specific input into the
assertion. It does not state what actually matters -- that the longer edge is
pinned to `cap` (or its default), that the shorter edge follows from the
aspect-ratio formula `round(short_edge * cap / long_edge)` (clamped to at
least 1), and that orientation is preserved. The same pattern appears in
`test_thumbnail_dimensions_never_zero` (`== (320, 1)`) and in
`test_downscale_produces_jpeg_with_exact_dimensions`, which asserts the
re-probed output equals a literal `(320, 180)` instead of the
`(width, height)` it just requested. A future change to the rounding rule or
default cap would fail these tests without revealing which guarantee broke.

## Scope

Affected: the three `thumbnail_dimensions` unit tests and the literal
dimension assertion in `test_downscale_produces_jpeg_with_exact_dimensions`,
all in `tests/thumbnail/test_downscale.py`.
Not affected: `thumbnail_dimensions` itself and `downscale_to_jpeg` (behavior
is correct; this is assertion style only), `tests/thumbnail/test_extract.py`,
and the error-path tests in the same file. No production code changes.

## Why deferred

`tests/thumbnail/test_downscale.py` is a verbatim copy of
`mosaic_api/tests/media_probe/test_downscale.py` (import paths adjusted). The
extraction keeps copied files diff-identical to their `mosaic_api` sources
until the consumer migration removes the originals, so drift between the two
copies stays detectable with a plain `diff`. Rewriting the assertions now
would break that property for this file for a style-only gain.

**Unblocked.** The consumer migration has deleted `mosaic_api`'s `media_probe`
tests, so the diff-identical property no longer binds this file; the rewrite can
proceed.

## What would close it

After the consumer migration deletes `mosaic_api`'s `media_probe` tests:

- `test_thumbnail_dimensions_caps_long_edge` asserts, for both orientations
  of an over-cap input: `max(result) == cap`,
  `min(result) == round(min(width, height) * cap / max(width, height))`, and
  orientation preservation
  (`(result[0] >= result[1]) == (width >= height)`).
- A case exercises a non-default `cap=` value against the same properties.
- `test_thumbnail_dimensions_never_zero` asserts `max(result) == cap` and
  `min(result) == 1` for an extreme aspect ratio whose short edge would
  otherwise round to 0.
- `test_downscale_produces_jpeg_with_exact_dimensions` asserts the re-probed
  output equals the `(width, height)` pair it passed to `downscale_to_jpeg`,
  not a literal.
- `uv run pytest tests/thumbnail/test_downscale.py` passes and the full
  pipeline stays green.
