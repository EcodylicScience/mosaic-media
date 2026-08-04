# Sparse reads undercount the sequential delivery count

## Problem

`VideoReader.read` raises when a sequential read ends before its window,
reporting how many frames it delivered against how many the facts declare. A
preceding sparse read makes that number too low.

`read_frames` calls `seek` for each target, and `seek` zeroes `_delivered` and
moves `_count_origin` to the target (`src/mosaic_media/io/reader.py:623-624`).
But `read_frames` then delivers through `_read_current` directly rather than
through `read` (`:640-651`), and only `read` increments `_delivered`. So the
frames a sparse read yields are never counted.

Measured two ways. With facts overstating the frame count on a healthy file,
which is the easiest reproduction and needs no fixture the tree lacks:

```
read_frames([10]) then a sequential read to exhaustion
    -> reports 49 delivered where 50 were
```

and on a source that genuinely ends short, read with honest facts, which is the
shape this actually reaches:

```
read_frames([10]) then a sequential read to exhaustion
    -> reports 39 delivered where 40 were
```

The undercount is the number of frames the sparse read yielded, in both. The
error still fires and no frame is misdelivered; only the count in the message is
wrong.

This is reachable in ordinary use. It needs no overstated facts: on a source
that genuinely delivers fewer frames than its packet index declares -- the class
the backstop exists for -- the decode dries inside the window, `_read_current`
returns `None`, and the shortfall check runs with `_delivered` undercounted.

## Scope

The arithmetic of one error message. The error still raises, its substantive
half ("its analysis verdict requires a transcode before it can be read") is
unaffected, and no frame is returned wrongly.

Not in scope: `read_frames`' own raise, which is correct and separately
reported; the sparse path's cursor contract, which `read_frames` documents at
`:645-649` as leaving "the positioned cursor at the decoder's true next frame";
and the window guard, which is what makes this unreachable on a healthy source.

## Why it was deferred, and what the deferral got right

It was filed as out of scope on the reasoning that any fix reaches into the
sparse path's cursor contract, and on a worry that a strided window would break
the arithmetic. The first was refuted by measurement; the second was correct and
was dismissed on a measurement that could not detect it.

The cursor contract is genuinely untouched: the fix sets `_delivered` and
`_count_origin`, and touches neither `_target` nor `_decoder_pos`.

The strided worry was real. `read()` counts one delivery per grid slot and
advances by `_frame_step`; `read_frames` delivers at a target that need not lie
on the grid and leaves the cursor at `target + 1`. A first fix that simply
counted the sparse frame therefore made `_delivered` count frames while
`expected` counted slots, and at `_frame_step > 1` the surplus of one was
exactly the margin the shortfall check needs -- so a strided read of a source
short by one slot went silent where it had previously raised.

That fix was cleared by a measurement taken with facts overstated by five, where
`expected` exceeds `delivered` by more than one and the surplus can never close
the gap. The mechanism could not fire in the configuration chosen to test it.

The lesson is not that the deferral was wrong. It is that a measurement settles
a question only if the mechanism can fire in the case measured, and that a
question is not settled by the first configuration that comes to hand.

## What closed it

`read_frames` rebases the count rather than adding to it: after restoring the
cursor it sets `_delivered` to 0 and `_count_origin` to that cursor, so the
sequential phase counts grid slots from where it actually resumes and both sides
of the comparison are in the same unit. The sparse frame is excluded from both,
which is honest, because the sequential read did not deliver it. It is the rule
`seek` already follows, which counts from the seek target rather than from the
window start.

Verified by a sweep over strides 1, 2, 3, 5 and 7 against every dry-out point in
the shortfall range and four sparse shapes, deriving the correct outcome per
configuration from the grid rather than assuming it: no violations, where both
alternatives violate it in scores of configurations. Two tests pin it -- the
rebased count on an unstrided window, and a strided window missing one slot
after a sparse read, which nothing in the suite previously covered.
