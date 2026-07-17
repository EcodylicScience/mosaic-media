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
| `multi-video-open-pays-per-file-probe.md` | active | - | Let the consumer migration add a facts-injection seam to MultiVideoReader so construction stops paying a per-file ffprobe subprocess the callers' held MediaFacts already answer; junction report bounded at 1.5x meanwhile. |
