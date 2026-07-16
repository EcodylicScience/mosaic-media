# Specs Index

Every spec that exists or ever existed in this directory. Statuses:
`active` (tracked, work in flight or pending), `implemented`, `superseded`,
`closed` (archived on disk with the matching filename suffix, untracked).
For archived entries, "Last tracked" is the commit still containing the
tracked file: `git show <hash>:docs/specs/<file>.md` retrieves it.

| Spec | Status | Last tracked | Description |
| --- | --- | --- | --- |
| `2026-07-16-extraction-and-reader-design.md` | active | - | Extraction of the probe from mosaic_api, the pure-ffmpeg frame reader replacing OpenCV decode, transcode command construction and converter, CLI, and the OpenCV performance regression gate. |
