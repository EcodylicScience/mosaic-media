# Plans Index

Every plan that exists or ever existed in this directory. Statuses:
`active` (tracked, work in flight or pending), `implemented`, `superseded`,
`closed` (archived on disk with the matching filename suffix, untracked).
For archived entries, "Last tracked" is the commit still containing the
tracked file: `git show <hash>:docs/plans/<file>.md` retrieves it.

| Plan | Status | Last tracked | Description |
| --- | --- | --- | --- |
| `2026-07-16-scaffold-and-probe-extraction.md` | implemented | 3f4eee2 | Package scaffolding (uv, pyproject, guards) and verbatim probe duplication from mosaic_api, with the sequence.py split, thumbnail subpackage, and hwaccel module. |
| `2026-07-16-frame-reader-io.md` | implemented | 6efb5ea | The io subpackage: packet byte offsets, seek index, VideoReader with frame-exact seeking, MultiVideoReader, video writer, and correctness suites. |
| `2026-07-16-perf-regression-gate.md` | implemented | 7704151 | Benchmark harness and the seven consumer-workload regression gates against OpenCV, plus the non-gating probe-cost and cold-seek reports. |
| `2026-07-16-transcode-and-cli.md` | implemented | fa3b4d2 | Verdict-to-command construction, the converter with re-probe acceptance and caller-owned output destinations, and the typer CLI. |
| `2026-07-23-video-identity.implemented.md` | implemented | 5aeed06 | Two derived probe values: `video_uuid`, an exact identity for naming and hash chains, and `content_digest`, invariant across container rewrites that preserve the elementary stream, as the duplicate pathway's index key. Per-packet payload hashes folded into the existing scan, a standard-library-only identity module, an exported duplicate comparison with a duration-scaled tolerance, a `compare` command, and transcode provenance. |
| `2026-07-17-pyav-io-adoption.md` | implemented | 5e65eb0 | Rewrite the io layer to decode and encode in process through PyAV, replacing the subprocess pipe the performance gate measured as too slow: codec guard, in-process packet scan with presentation-timestamp deduplication, reader and writer rewrites, the layering guards for the av dependency, and the gate-branch threshold fold-in. |
| `2026-07-24-reader-conversion-filter-graph.implemented.md` | implemented | e99931c | Task-by-task implementation of the reader conversion graph: extend the frame contract to every conversion path and then to every array-returning entry point, move conversion into the graph, tighten the resize tolerances from 64 to 2, and recalibrate every gate to parity on a twenty-core reference configuration. |
| `2026-07-24-identity-provenance.implemented.md` | implemented | f70116a | Two new required `MediaFacts` fields, `identity_scheme` and `prober_version`, recording which regime minted `video_uuid` and `content_digest` without hashing either; the format tags now built from one shared `IDENTITY_SCHEME` constant; the versioning contract between package releases and scheme bumps. |
