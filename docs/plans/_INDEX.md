# Plans Index

Every plan that exists or ever existed in this directory. Statuses:
`active` (tracked, work in flight or pending), `implemented`, `superseded`,
`closed` (archived on disk with the matching filename suffix, untracked).
For archived entries, "Last tracked" is the commit still containing the
tracked file: `git show <hash>:docs/plans/<file>.md` retrieves it.

| Plan | Status | Last tracked | Description |
| --- | --- | --- | --- |
| `2026-07-16-scaffold-and-probe-extraction.md` | active | - | Package scaffolding (uv, pyproject, guards) and verbatim probe duplication from mosaic_api, with the sequence.py split, thumbnail subpackage, and hwaccel module. |
| `2026-07-16-frame-reader-io.md` | active | - | The io subpackage: packet byte offsets, seek index, VideoReader with frame-exact seeking, MultiVideoReader, video writer, and correctness suites. |
| `2026-07-16-perf-regression-gate.md` | active | - | Benchmark harness and the seven consumer-workload regression gates against OpenCV, plus the non-gating probe-cost and cold-seek reports. |
| `2026-07-16-transcode-and-cli.md` | active | - | Verdict-to-command construction, the converter with re-probe acceptance and caller-owned output destinations, and the typer CLI. |
