# Identity provenance: implementation plan

> Execute task by task with a fresh implementer per task and a combined
> specification-and-quality review between tasks. Steps use checkbox syntax
> for tracking.

**Goal:** Record which regime minted `video_uuid` and `content_digest`, so a
consumer can tell a re-mint under a later scheme apart from a file whose
content actually changed, without folding that provenance into either hash.

**Architecture:** Two new required `MediaFacts` fields carry the provenance.
`identity_scheme` is the existing format-tag version number
(`IDENTITY_SCHEME`), pulled out from the two literal tag strings into one
named constant both tags are built from, so the number cannot drift between
them. `prober_version` is a new `ffprobe.py` function that reads the running
ffprobe's own `-show_program_version` / `-show_library_versions` output and
reduces it to `"<program> <libavformat ident>"` -- libavformat because the
digest is defined against its demuxer output, not raw file bytes, so an
ffmpeg upgrade that changes that output is exactly the event `identity_scheme`
exists to distinguish from a real content change. The result is cached for the
process: it is a property of the binary, not of the file being probed.

Neither field is hashed. Both functions that build the hashed byte strings
(`content_digest_input`, `video_uuid_input`) take a `Header` and packets, not a
`MediaFacts`, which makes adding a `MediaFacts`-only field to their inputs a
type error rather than a discipline problem.

**Tech stack:** Python 3.12, standard library only in the probe core
(`functools.lru_cache`, `json`). System ffprobe. pytest.

## Global constraints

- Python floor is 3.12. Nothing may require 3.13 or later.
- The probe core imports standard library only. Never numpy, typer, av, or cv2
  from `src/mosaic_media/probe/`.
- Never import `mosaic` or `mosaic_api`.
- No `typing.Any`, no `typing.Optional` (use `X | None`), no `typing.cast`.
- No `# noqa`, no `# pyright: ignore`, no `# type: ignore`. Fix the design.
- ASCII only in source and comments. American spelling.
- No abbreviations in identifiers.
- No conventional-commit prefixes in commit messages. Plain English. No
  co-authorship trailers.
- No process language in code, comments, docstrings, or commits.
- Verification commands:
  ```bash
  uv run ruff format src/ tests/
  uv run ruff check src/ tests/
  uv run basedpyright src/ tests/
  uv run pytest tests/
  ```
  basedpyright scope includes `tests/`. Run a full suite through `heavy`.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/mosaic_media/probe/identity.py` | `IDENTITY_SCHEME`, the constant both format tags are built from. |
| `src/mosaic_media/probe/ffprobe.py` | `prober_version`, cached per process, raising `MediaProbeError` on any parse failure. |
| `src/mosaic_media/probe/facts.py` | Two new required fields: `identity_scheme`, `prober_version`. |
| `src/mosaic_media/probe/probe.py` | Fills both fields on every probe. |
| `src/mosaic_media/__init__.py` | Exports `IDENTITY_SCHEME`; the two format tags stay unexported. |
| `tests/probe/test_identity.py` | Pins the format tags to the shared constant and to their literal bytes. |
| `tests/probe/test_ffprobe.py` | `prober_version`: names program and libavformat, caches, raises on a missing libavformat entry. |
| `tests/probe/test_probe.py` | A probed file records both fields against the live constant and the live prober. |
| `tests/probe/test_verdict.py` | The hand-built `MediaFacts` constant states both fields. |
| `README.md` | The versioning contract between package releases and the identity scheme; the two new fields in "Video identity". |
| `docs/migration-mosaic-api.md` | One bullet in section 5 naming the two new required fields. |

---

### Task 1: Scheme constant, prober version, and the two fields

- [x] **Step 1: Failing test for the shared scheme constant.** Both format
  tags must end with one `IDENTITY_SCHEME` string, and the literal tag bytes
  must not move -- every golden vector in `test_identity.py` depends on them.
- [x] **Step 2: `IDENTITY_SCHEME = "1"`** replaces the two literal tag
  assignments in `identity.py`; both tags are now built from it.
- [x] **Step 3: Failing tests for `prober_version`.** Names the program and
  the libavformat ident, is cached (`is`, not `==`, across two calls), and
  raises `MediaProbeError` naming `libavformat` when the library list omits it.
- [x] **Step 4: Implement `prober_version`** in `ffprobe.py`: `lru_cache`,
  one `ffprobe -show_program_version -show_library_versions` call reduced
  through `run_to_completion`, every level of the JSON parse guarded the same
  way `read_header` already guards its own parse, no payload ever
  interpolated into a message.
- [x] **Step 5: Failing test for the two `MediaFacts` fields.** A probed file
  states `identity_scheme == IDENTITY_SCHEME` and
  `prober_version == ffprobe.prober_version()`.
- [x] **Step 6: Add `identity_scheme: str` and `prober_version: str`** to
  `MediaFacts`, required with no default, and fill both in `probe_media`.
- [x] **Step 7: Fix every `MediaFacts(` construction site.** Two exist in this
  repository: `probe.py` (fills both from the live scheme and the live
  prober) and the `CLEAN` fixture in `test_verdict.py` (states the real scheme
  and a build string that parses like a real one, `"0.0.0-test Lavf0.0.0"`,
  naming no real build).
- [x] **Step 8: Export `IDENTITY_SCHEME`** from the facade, with a comment
  recording why it is exported while the two format tags are not: a format tag
  is an input to the digest, so reading one is reimplementing the hash; the
  scheme is a fact a consumer compares against a stored one, which is the
  whole reason it exists.
- [x] **Step 9: Bump the package to 0.2.0** and write the versioning
  contract: a scheme bump always forces a package version bump; a version
  bump never implies a scheme bump. The two cannot be one number, because the
  scheme's trigger (an ffmpeg upgrade changing libavformat's demuxer output)
  is independent of anything this package's own API surface does.
- [x] **Step 10: Verify and commit.** `ruff format`, `ruff check`,
  `basedpyright`, and the full suite under `heavy`, all clean.

---

## Design notes

**Why one scheme number, not two.** `video_uuid` hashes the content digest, so
any change to what `content_digest_input` serializes moves both values; a
scheme that could say otherwise would misreport which value changed. A change
confined to `video_uuid_input` moves the shared number too, which re-mints
`content_digest` alongside it even though its own bytes did not change --
accepted, because one probe mints both values in one scan, so the re-mint
costs the same pass over the corpus either way, and `identity_scheme` on the
row is what tells a consumer why a value moved.

**Why the version and the scheme cannot be one number.** The scheme's trigger
is an ffmpeg upgrade that changes libavformat's demuxer output; no API this
package exposes changes when that happens, so a package version number has
nothing to signal it with. A shared number would also fold build metadata
into identity: every unrelated release would re-mint every uuid, or, if only
a major segment were hashed, an unrelated API break would re-mint every value
while a genuine format break would not.

**Why `prober_version` is cached, not re-read per probe.** It answers a
question about the ffprobe binary, not about the file being probed. A
subprocess per probed file to answer a per-process constant would be a real
cost paid for nothing; the cost of the cache is that an ffprobe binary
upgraded underneath a long-lived process is not observed until it restarts.
