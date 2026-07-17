# run_transcode offers no progress reporting and no cancellation seam

## Problem

`_run_ffmpeg` in `src/mosaic_media/transcode/convert.py` is one blocking
`subprocess.run` at `-v error`: a caller learns nothing until the encode
finishes, and the only way to stop one is the timeout. The `mosaic` toolkit's
transcode job (its migration design, step 9) needs both: live progress mapped
into its job heartbeat, and cooperative cancellation from its cancel token.
That design names this capability as an upstream dependency that lands before
its step 9; the mosaic side is wiring only.

## What would close it

- `run_transcode` gains two optional keyword parameters, threaded to the
  ffmpeg invocation: `on_progress` (a callback receiving a structured update)
  and `cancel_check` (a zero-argument callable polled during the run). Both
  default to None; the CLI and existing callers are unchanged.
- The invocation switches from `subprocess.run` to `Popen` with
  `-progress pipe:1` inserted into the argv (stdout carries the key=value
  progress stream; `-v error` semantics on stderr are preserved for failure
  detail). The reader parses `out_time_us` (with `out_time` as fallback),
  `speed`, `fps`, and `progress=continue|end`, and computes a completion
  fraction against the source `facts.duration`.
- `facts.duration` is 0.0 for timestampless sources (a raw elementary
  stream -- exactly a file the analysis verdict transcodes), so the fraction
  is None when duration is not positive; the update still carries the raw
  `out_time`, `speed`, and `fps` so a caller can show indeterminate progress.
- A true `cancel_check` terminates the child process, removes the temporary
  output (the existing cleanup already runs on any raise and must keep
  holding), and raises a `TranscodeError` that identifies the run as
  cancelled rather than failed.
- The timeout behavior is preserved under `Popen`.
- Covered by tests: a progress callback observing a monotonic fraction on a
  real encode, the duration-zero indeterminate case, and a cancellation
  mid-encode leaving no temporary file behind.
