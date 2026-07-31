# Specs Index

Every spec that exists or ever existed in this directory. Statuses:
`active` (tracked, work in flight or pending), `implemented`, `superseded`,
`closed` (archived on disk with the matching filename suffix, untracked).
For archived entries, "Last tracked" is the commit still containing the
tracked file: `git show <hash>:docs/specs/<file>.md` retrieves it.

| Spec | Status | Last tracked | Description |
| --- | --- | --- | --- |
| `2026-07-31-raw-elementary-stream-frame-rate.md` | implemented | 139e471 | A raw H.264 elementary stream declares its frame rate in its own bitstream, and the probe reported the h264 demuxer's fabricated default instead, leaving every copy remux to the mp4 muxer's deprecated timestamp synthesis. The probe now reads the declared rate for a stream whose packets carry no timestamps, and both copy remuxes set their timestamps from it, gated on unmeasured timing so a lying header can never write its own claim in as fact. |
| `2026-07-23-video-identity.implemented.md` | implemented | 5aeed06 | Two derived probe values: `video_uuid`, an exact identity for naming and hash chains, and `content_digest`, invariant across container rewrites that preserve the elementary stream, as the duplicate pathway's index key. Both built from per-packet payload hashes read out of the scan ffprobe already runs, with no decode. Exports `compare_for_duplicate` so the tolerance comparison is not reimplemented by consumers. |
| `2026-07-16-extraction-and-reader-design.md` | implemented | e3179b8 | Extraction of the probe from mosaic_api, the frame reader replacing OpenCV decode, transcode command construction and converter, CLI, and the OpenCV performance regression gate. Revised 2026-07-17: the io layer decodes in process through libav bindings (PyAV) under a codec guard, with the gate's refuted subprocess-tuning routes, three-tier gate policy, and measured threshold table recorded inline. Consumer migration is guided by the README's "Adopting this package" and the guides under docs/. |
| `2026-07-24-reader-conversion-filter-graph.implemented.md` | implemented | e99931c | Move the frame reader's pixel format conversion, scaling, and rotation into one persistent libavfilter graph built per reader, replacing the per-frame reformat calls. Removes repeated scaling-context construction and fixes a chroma-plane scaling divergence that put the resized bgr24 output up to 76 levels away from ffmpeg's `-vf scale`. Records the reference configuration the gates are calibrated on, replacing the sub-parity carve tier that measured the calibrating laptop rather than the reader. |
