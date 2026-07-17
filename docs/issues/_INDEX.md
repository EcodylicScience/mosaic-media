# Issues Index

Every issue that exists or ever existed in this directory. Statuses:
`active` (tracked, open), `closed` (archived on disk with the matching
filename suffix, untracked).
For archived entries, "Last tracked" is the commit still containing the
tracked file: `git show <hash>:docs/issues/<file>.md` retrieves it.

| Issue | Status | Last tracked | Description |
| --- | --- | --- | --- |
| `ffmpeg-runner-duplication.md` | active | - | Consolidate the per-module run-ffmpeg-to-completion helpers into one core leaf after the consumer migration closes the duplication window. |
| `thumbnail-dimension-tests-assert-literal-tuples.md` | active | - | Rewrite the thumbnail_dimensions tuple-equality assertions as property assertions (long edge pinned to cap, short edge from the aspect-ratio formula) once the consumer migration closes the duplication window. |
| `seek-index-counts-duplicate-timestamp-packets.md` | closed | 5e65eb0 | Deduplicate presentation timestamps in build_seek_index consistently with measure_timing so VP8/VP9 alternate-reference packets cannot shift keyframe ranks and reintroduce off-by-N seeks. |
| `encoder-gate-checks-listing-not-usability.md` | active | - | Gate hardware encoding on a usability-checked probe (mirroring the nvdec device-init fix) in both the writer and the transcode command builder, falling back to CPU encoders when the device is unusable. |
| `multi-video-open-pays-per-file-probe.md` | closed | e3179b8 | Let the consumer migration add a facts-injection seam to MultiVideoReader so construction stops paying a per-file ffprobe subprocess the callers' held MediaFacts already answer; junction report bounded at 1.5x meanwhile. Resolved: the constructor accepts facts= and indices=, and the injected junction workload is gated at the carve tier (measured 1.065). |
| `run-transcode-lacks-progress-and-cancellation.md` | active | - | Give run_transcode optional on_progress and cancel_check parameters over a Popen `-progress pipe:1` invocation (fraction against facts.duration, None when duration is 0), preserving temp cleanup and timeout -- the upstream dependency of the mosaic transcode job. |
| `encoding-presets-unmeasured-against-quality-goals.md` | active | - | Measure both encoding presets on a representative set of real footage (static and moving cameras, small and large subjects, fast movement, variable backgrounds) against their goals -- analysis near lossless, playback visually lossless -- pick the crf values from the measurements, and decouple the NVENC cq value from the SVT-AV1 crf, whose scales are not equivalent. |
