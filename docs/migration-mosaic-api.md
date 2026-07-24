# Migrating mosaic_api onto mosaic-media

Audience: a mosaic_api developer working on the migration. Line numbers cite
the state of both repositories at the time of writing and may drift; the
import statements and symbol names are the stable anchors. The guide is a map
and a suggested order, not a prescription -- where it disagrees with the
code, the code wins; please correct the guide.

## 1. What this migrates and why

`mosaic_api/src/mosaic_api/media_probe/` is the original of the code that was
extracted into the `mosaic-media` package, which now sits upstream of both
`mosaic_api` and the `mosaic` toolkit. The extraction copied the modules; this
migration closes the duplication window by deleting the internal copy and
rewiring every consumer onto the package. The full design rationale, the
extraction boundary, and the phase plan are in `mosaic_media/README.md`
(sections "Extraction inventory", "Layering and optional dependencies",
"Metadata authority", "Adopting this package").

Three modules deliberately stay behind because they encode mosaic_api's own
vocabulary, not the media domain: `facts_io.py` (the mapping between
`MediaFacts` and the backend's persistence columns), `media_types.py`
(container to HTTP `Content-Type` for downloads and `<source type>`), and
`duplicate_stems` from `sequence.py` (thumbnail and pose sidecar path
injectivity, a backend storage-layout concern).

## 2. Dependency wiring

In `mosaic_api/pyproject.toml`:

- Add `"mosaic-media"` to `[project] dependencies`. **Core only -- no
  extras.** mosaic_api uses the probe, the verdict, and the still-image
  derivatives, all of which are in the standard-library-only core. It reads
  no frames server-side (see section 3.4), so declaring `mosaic-media[io]`
  (numpy, av) or `mosaic-media[cli]` (typer; mosaic_api has its own typer
  dependency for its own CLI) would pull numpy or typer through the media
  dependency and break the package's layering contract.
- Add the editable path source, following the `mosaic-behavior` precedent
  already in the same file:

  ```toml
  [tool.uv.sources]
  mosaic-behavior = { path = "../mosaic", editable = true }
  mosaic-media = { path = "../mosaic_media", editable = true }
  ```

- Run `uv sync` and commit the updated `uv.lock`.

Switching to released versions is best deferred while `MediaFacts` is still
growing fields. Every added measurement touches `facts.py` in mosaic-media
and `FACT_FIELDS` plus its mirrors in mosaic_api (section 3.3) as one logical
change across two repositories; a release-and-bump cycle would be paid on
every field, where the editable path source keeps the pair in lockstep.

Python floors are compatible in the required direction: mosaic-media requires
`>=3.12`, mosaic_api requires `>=3.13`, so installing mosaic-media into
mosaic_api's environment is unconstrained. The floor difference is
intentional: mosaic-media stays installable by the `mosaic` toolkit
(`>=3.12`), which "aligning" it up to 3.13 would break.

## 3. Inventory of migration sites

The public import path is the `mosaic_media` facade (`from mosaic_media
import probe_media, derive, ...`); the facade re-exports everything
mosaic_api needs from the core. Submodule paths (`mosaic_media.probe.ffprobe`
and so on) exist but no mosaic_api production code needs them -- today only
tests reach into submodules, and those tests transfer (section 3.6).

Every name below maps one-to-one: `probe_media`, `derive`, `MediaFacts`,
`Verdict`, `Thresholds`, `PlaybackProfile`, `CHROME_149`,
`DEFAULT_THRESHOLDS`, `HARD_STREAM_REASONS`, `StreamReason`,
`AnalysisReason`, `StreamTranscode`, `MediaProbeError`, `VIDEO_EXTENSIONS`,
`is_candidate_video`, `MeasuredVideoProperties`, `VideoProperties`,
`PropertyMismatch`, `measured_or_none`, `uniform_properties`,
`canonical_fps`, `extract_first_frame`, `downscale_to_jpeg`,
`thumbnail_dimensions`. Signatures are unchanged from the originals.

Two names are NOT in the mosaic_media facade because they stay behind:
`duplicate_stems` and `media_type_for_container`. Sites that import them keep
importing from the retained mosaic_api package (section 4).

### 3.1 Probe core and policy (source)

| Site | Today | After |
| --- | --- | --- |
| `src/mosaic_api/config.py:26` | `from mosaic_api.media_probe.policy import DEFAULT_THRESHOLDS, Thresholds`; `media_probe_thresholds()` (line 674) builds the injected `Thresholds` from environment variables | `from mosaic_media import DEFAULT_THRESHOLDS, Thresholds`. The threshold policy itself stays in config -- this is the injection point the package's "policy is injected, never encoded" contract requires. |
| `src/mosaic_api/upload/probe_worker.py:22-29` | Ingestion probe: `probe_media` on each staged file, `extract_first_frame` as the decodability check, `derive(facts, CHROME_149, thresholds)`, then `facts_to_columns` onto the `UploadSessionFile` row | Probe names from `mosaic_media`; `facts_to_columns` from the retained package. The choice to apply `CHROME_149` remains mosaic_api's. |
| `src/mosaic_api/sequence_import/metadata.py:6-17` | Import-time probe per video (`probe_media` line 127, `extract_first_frame` line 140, `derive` line 144), sequence aggregation (`uniform_properties` line 190, `canonical_fps` line 235), stem check (`duplicate_stems` line 177) | Probe and sequence names from `mosaic_media`; `duplicate_stems`, `facts_to_columns`, `aggregate_transcode` from the retained package. |
| `scripts/reprobe_videos.py:25-37` | One-shot backfill: re-runs `probe_media` and `derive` over every stored `Video` row, recomputes sequence aggregates via `canonical_fps` and `aggregate_transcode` | Same split: probe names from `mosaic_media`, facts_io names from the retained package. This script is a deliberate re-run of the authoritative probe to fill null columns, not a metadata-authority violation. |
| `src/mosaic_api/db/queries/upload_session/validation.py:32-37` | Arrangement verdicts from persisted facts: `measured_or_none` (lines 317, 424), `uniform_properties` (line 325), `duplicate_stems` (line 298) | `MeasuredVideoProperties`, `measured_or_none`, `uniform_properties` from `mosaic_media`; `duplicate_stems` from the retained package. |
| `src/mosaic_api/upload/finalize.py:23-30` | Finalize gate: `is_candidate_video` (line 171), `duplicate_stems` (line 185), `measured_or_none` (line 197), `uniform_properties` (line 207), `VIDEO_EXTENSIONS` (line 346) | Same split as validation.py. |
| `src/mosaic_api/upload/orchestration.py:36` | `VIDEO_EXTENSIONS` (line 126), `measured_or_none` (line 266) | Both from `mosaic_media`. |
| `src/mosaic_api/endpoints/upload_sessions/files.py:19` | `is_candidate_video` gating file registration (line 505) | From `mosaic_media`. |

### 3.2 Wire schemas and endpoints exposing probe results

| Site | Today | After |
| --- | --- | --- |
| `src/mosaic_api/schemas/__init__.py:7-16` | `Video.from_db` / `VideoSequence.from_db` rebuild reason sets by running `derive(columns_to_facts(row), CHROME_149, media_probe_thresholds())` over stored facts (lines 87-152) | `CHROME_149`, `AnalysisReason`, `StreamReason`, `StreamTranscode`, `derive` from `mosaic_media`; `columns_to_facts` from the retained package. This is derivation from stored facts, never a re-measurement -- a property worth preserving through the rewire. The wire keeps `stream_transcode` and `analysis_transcode` as separate fields; the two verdicts are independent by design, and collapsing them would lose the distinction the reason sets exist for. |
| `src/mosaic_api/endpoints/upload_sessions/_status.py:9` | `HARD_STREAM_REASONS` selects the hard subset for the warning sentence (line 78) | From `mosaic_media`. |
| `src/mosaic_api/endpoints/sequences.py:21` | `VIDEO_EXTENSIONS` (lines 165, 265, 290, 306) and `media_type_for_container` for the video-stream response media type (line 632) | `VIDEO_EXTENSIONS` from `mosaic_media`; `media_type_for_container` from the retained package. |
| `src/mosaic_api/endpoints/annotations/images.py:28-29` | `thumbnail_dimensions` (line 108) and `downscale_to_jpeg` (line 114) produce annotation-image thumbnails; `MediaProbeError` handled (line 116) | All three from `mosaic_media` (the thumbnail helpers moved into the `mosaic_media.thumbnail` subpackage but are re-exported by the facade under the same names). |
| `src/mosaic_api/sequence_import/records.py:3` | `FACT_COLUMNS_NOT_ON_VIDEO_ROW` filters the fact bag before writing a `Video` row (line 56) | From the retained package. |

### 3.3 The FACT_FIELDS coupling

`src/mosaic_api/media_probe/facts_io.py` stays in mosaic_api. It is the one
place that knows the correspondence between a `MediaFacts` plus a `Verdict`
and the backend's fact columns: `FACT_FIELDS` (line 26),
`FACT_COLUMNS_NOT_ON_VIDEO_ROW` (line 56), the `FactRow` and `TranscodeRow`
protocols, `facts_to_columns` (line 106), `columns_to_facts` (line 144), and
`aggregate_transcode` (line 246). Its only change in this migration is its
own imports: `from .facts import MediaFacts` and `from .verdict import
Verdict` become `from mosaic_media import MediaFacts, Verdict`.

The columns it names are mirrored in five more places. Adding a `MediaFacts`
field is one logical change across two repositories, touching:

1. `mosaic_media/src/mosaic_media/probe/facts.py` plus the measurement that
   fills it (mosaic-media repository),
2. `facts_io.py`: `FACT_FIELDS` (if null means unprobed), the `FactRow`
   protocol, `facts_to_columns`, `columns_to_facts`,
3. the ORM columns in `src/mosaic_api/db/models/video.py` and
   `src/mosaic_api/db/models/upload_session.py`,
4. a new alembic migration (the existing fact columns came from
   `migrations/versions/2026_07_10_e1f2a3b4c5d6_add_measured_media_facts.py`;
   migrations do not import the probe package, so they are untouched by this
   migration),
5. the staged-file-to-Video copy in
   `src/mosaic_api/endpoints/upload_sessions/adopt.py` (around line 98),
6. the checkpoint-restore column lists in
   `src/mosaic_api/db/queries/restore_descriptors.py` (around line 434),
   which enumerate every fact and verdict column so a restore does not drop a
   sequence back to `probe_pending`.

This fan-out is why the dependency stays an editable path source. The
`media_raw/index.csv` schema carries the same fact columns in a second
persistence form; section 3.7 covers that coupling.

### 3.4 Frame access (io): nothing to migrate, and no extra to add

mosaic_api reads no video frames server-side. Verified surfaces:

- `src/mosaic_api/pose_data/conversion.py` converts trex_v1 parquet to the
  `.pose` binary -- tabular data only, no video decode.
- `src/mosaic_api/frame_runs.py` discovers frame-extraction runs the toolkit
  already wrote to disk as image files; it never decodes video.
- `src/mosaic_api/endpoints/annotations/images.py` downscales existing PNG
  frames through the core's ffmpeg subprocess helper, not a decoder.
- The probe worker's decodability check is `extract_first_frame` (core,
  ffmpeg subprocess), not an in-process decode.
- There is no `cv2`, `av`, or frame-array numpy code anywhere in
  `mosaic_api/src`.

So mosaic_api adopts nothing from `mosaic_media.io` in this migration and
does not declare the `[io]` extra. The io adoption target is the `mosaic`
toolkit (its own migration guide). If a future mosaic_api feature needs
server-side frame reading, `mosaic_media.io.VideoReader` accepts `facts=` and
`index=` injection so the stored `MediaFacts` can suppress re-probing -- add
the extra then, in the commit that introduces that feature.

### 3.5 Transcode: nothing to migrate yet

mosaic_api stores and serves the two verdict columns (`stream_transcode`,
`analysis_transcode`) but has no transcode execution call sites -- command
construction and job scheduling are not wired anywhere in the backend today,
and per the mosaic-media README, scheduling belongs to `mosaic`'s job
infrastructure (a late step of the toolkit migration). When that wiring
lands, what
crosses the boundary from mosaic_api is a command specification built by
`mosaic_media.transcode.build_command` from the verdict, the facts, and the
injected profile and thresholds -- never a policy. Two constraints bind that
future work, not this migration: the transcode must not ship to production
before the toolkit's frame reader is adopted (or the stack produces
derivatives -- currently AV1 -- its own toolkit cannot decode), and the
transcoded output is re-probed as its
acceptance test with a red verdict terminal, never retried.

### 3.6 Tests

mosaic-media already carries adapted copies of every test that covers moved
code, so no coverage is lost by deleting the mosaic_api copies -- the
coverage's home simply changes. Verify each pairing exists before deleting.

Transferred (delete the mosaic_api copy; the covering suite is in
mosaic-media):

| mosaic_api test | Covering mosaic-media test |
| --- | --- |
| `tests/media_probe/test_boxes.py` | `tests/probe/test_boxes.py` |
| `tests/media_probe/test_candidates.py` | `tests/probe/test_candidates.py` |
| `tests/media_probe/test_ffprobe.py` | `tests/probe/test_ffprobe.py` |
| `tests/media_probe/test_gop.py` | `tests/probe/test_gop.py` |
| `tests/media_probe/test_probe.py` | `tests/probe/test_probe.py` |
| `tests/media_probe/test_stream_selection.py` | `tests/probe/test_stream_selection.py` |
| `tests/media_probe/test_timing.py` | `tests/probe/test_timing.py` |
| `tests/media_probe/test_verdict.py` | `tests/probe/test_verdict.py` |
| `tests/media_probe/test_downscale.py` | `tests/thumbnail/test_downscale.py` |
| `tests/media_probe/test_thumbnail.py` | `tests/thumbnail/test_extract.py` |
| `tests/media_probe/test_purity.py` | `tests/probe/test_purity.py` plus `tests/test_import_guard.py` -- and see the rewrite below |
| `tests/media_probe/test_sequence.py` | Split: the uniformity and `canonical_fps` tests are in `tests/probe/test_sequence.py`; the three `duplicate_stems` tests (mosaic_api lines 62-74) stay in mosaic_api with the retained function |

Rewritten, not removed: `tests/media_probe/test_purity.py` guards a contract
that changes rather than disappears. Today it asserts the internal package is
standard library only; after the migration the equivalent mosaic_api
invariant is "the media dependency is core-only". A rewrite that keeps the
coverage: assert, in a fresh subprocess, that `import mosaic_media` leaves
`numpy` and `typer` out of `sys.modules` (a subprocess because mosaic_api's
own CLI imports typer in-process), and that `pyproject.toml` declares
`mosaic-media` without extras.

Remaining in mosaic_api, rewired only:

| Test | Change |
| --- | --- |
| `tests/media_probe/test_facts_io.py` | Stays (facts_io stays). Rewire `CHROME_149`, `DEFAULT_THRESHOLDS`, `MediaFacts`, `derive` to `mosaic_media`. |
| `tests/media_probe/test_media_types.py` | Stays. Rewire `CHROME_149` to `mosaic_media`. |
| `tests/media_probe/test_thresholds.py` | Stays (it tests `config.media_probe_thresholds`). Rewire `DEFAULT_THRESHOLDS`. |
| `tests/test_metadata.py` | Integration test of `parse_sequence_metadata` against real clips; stays. Rewire `VIDEO_EXTENSIONS`. |
| `tests/sequence_import/test_extension_set.py` | Rewire `VIDEO_EXTENSIONS`. |
| `tests/upload/test_probe_worker.py` | Rewire `MediaProbeError`. Boundary integration test; stays. |
| `tests/upload/test_arrangement_probe_issues.py` | Rewire `MeasuredVideoProperties`. |
| `tests/upload/test_sequence_atomic_finalize.py` | Rewire `MeasuredVideoProperties`. |
| `tests/upload/test_submit_cancel.py` | Rewire `StreamTranscode`. |
| `tests/helpers/media_fixtures.py` | Stays: the integration suites (`tests/sequence_import`, `tests/upload`, `tests/test_metadata.py`) still build real clips. mosaic-media carries its own copy for its own suite; after the transferred probe tests are deleted here, prune any clip variants no remaining mosaic_api test consumes. |
| `tests/test_import_layering.py` | The `LAYERS` map names `mosaic_api.media_probe` at layer 0 (line 60) and `test_layers_map_has_no_stale_entries` fails on entries for modules that no longer exist -- rename the entry to the retained package name in the same commit as the deletion. |
| `tests/sequence_import/test_no_hardcoded_extension.py` | The `ALLOWED` set names `media_probe/candidates.py` and `media_probe/media_types.py`. `candidates.py` leaves the scanned tree entirely (drop its entry); the `media_types.py` entry follows the package rename. |
| `tests/conftest.py` (line 66 comment) | The `pytest_plugins` comment names `tests/media_probe`; update it when that directory is renamed. |

Renaming `tests/media_probe/` to the retained package name from section 4
keeps the directory naming what it still tests.

### 3.7 The index.csv column coupling

`src/mosaic_api/upload/finalize.py` wraps mosaic's `Dataset.index_media` to
rewrite `media_raw/index.csv` on every upload finalize, so the CSV index is a
second persistence schema that must track the columns the probe emits -- the
`media_raw/index.csv` analogue of the `FACT_FIELDS` coupling in section 3.3,
and the reason that section's fan-out is not the whole story.

On the `mosaic-media-migration` branch `index_media` writes the full media
index schema. `mosaic.core.dataset.MEDIA_INDEX_COLUMNS` (21 columns) now ends
its display columns with `mosaic.core.media._facts_columns.FACTS_COLUMNS`
(seven columns) before `video_order`: `frame_count`, `analysis_transcode`,
`stream_transcode`, `analysis_derivative_path`, `playback_derivative_path`,
`source_path`, and `media_facts` -- the whole `MediaFacts` serialized as JSON
for `facts=` injection. The two derivative-path columns are per-target:
analysis and playback derivatives are independent and never share a cell, and
`index_media` carries an existing index's derivative links forward across a
re-probe (`_carry_forward_derivative_links`).

`finalize.py` undoes all of it. `write_index` calls `index_media` (full
schema), reads the rows back, merges them with the preserved rows for the
other sequences, then rewrites through `write_index_rows`, whose
`csv.DictWriter` is pinned to a local `INDEX_COLUMNS` list -- the fourteen
pre-migration columns only -- and whose
`{column: row.get(column, "") for column in INDEX_COLUMNS}` projection drops
every key not in that list. The seven columns `index_media` wrote a few lines
earlier are erased on every API-managed finalize.

Both migration features that depend on these columns are defeated until this
is fixed:

- `media_facts` injection (the no-re-probe metadata authority, and raw `.h264`
  reads) has no persisted facts to inject.
- The three-way transcode routing (`analysis_derivative_path` /
  `playback_derivative_path` / `source_path`) is erased, so
  `Dataset.resolve_media` cannot find a transcoded derivative and a per-frame
  read falls back to the defective original -- which now raises loudly rather
  than degrading silently.

The fix: drive `write_index_rows` off mosaic's authority instead of a parallel
copy. Import `MEDIA_INDEX_COLUMNS` from `mosaic.core.dataset` (the module
finalize.py already imports `Dataset` and `new_dataset_manifest` from), delete
the local `INDEX_COLUMNS`, and write the union of `MEDIA_INDEX_COLUMNS` and any
extra keys present in the rows in a stable order (the authority's order, then
any appended extras), so the writer never narrows a row. A future schema
addition in mosaic then survives finalize with no mosaic_api change -- the
parallel list that silently drifted behind mosaic's schema is exactly what
caused this. Do not re-hardcode the seven names into a longer literal; that
reintroduces the drift. The tests that build fixture rows from
`finalize.INDEX_COLUMNS` (`test_video_order_registration.py`,
`test_finalize.py`, `test_sequence_atomic_finalize.py`,
`tests/helpers/finalize_fakes.py`) reference the imported authority the same
way after the swap.

This is not a moved-symbol rewire, so it is independent of the media_probe
deletion (sections 3.1-3.6): it depends only on `mosaic.core.dataset`, already
a dependency, and can land as the first mosaic_api change of this migration.
It is the merge gate. mosaic's `mosaic-media-migration` branch and this
finalize change must ship in sync; merging mosaic to main first ships a
metadata-authority and transcode-routing feature the primary consumer strips
on every upload.

## 4. The deletion

Goes away from `src/mosaic_api/media_probe/`: `boxes.py`, `candidates.py`,
`downscale.py`, `errors.py`, `facts.py`, `ffprobe.py`, `gop.py`, `policy.py`,
`probe.py`, `thumbnail.py`, `timing.py`, `verdict.py`, and everything in
`sequence.py` except `duplicate_stems` (`VideoProperties`,
`MeasuredVideoProperties`, `PropertyMismatch`, `uniform_properties`,
`canonical_fps`, `measured_or_none` all moved).

Stays behind, with a rename suggestion: after the migration the package no
longer probes anything, so `media_probe` would name something the code no
longer does. `src/mosaic_api/media_facts/` with three modules would fit what
remains:

- `facts_io.py` -- unchanged except its imports of `MediaFacts` and `Verdict`
  now come from `mosaic_media`.
- `media_types.py` -- unchanged.
- `stems.py` -- new home for `duplicate_stems` (moved out of the deleted
  `sequence.py`).

Its `__init__.py` facade re-exports exactly the retained surface:
`FACT_FIELDS`, `FACT_COLUMNS_NOT_ON_VIDEO_ROW`, `FactRow`, `TranscodeRow`,
`facts_to_columns`, `columns_to_facts`, `aggregate_transcode`,
`media_type_for_container`, `duplicate_stems`. Re-exporting mosaic_media
names here would create a second import path per symbol and a compatibility
shim to unwind later; consumers importing `mosaic_media` directly keeps one
path per symbol.

## 5. Behavioral differences to expect

The extraction's acceptance criterion was that the backend's probe tests pass
unmodified against the extracted package, and the probe, verdict, policy,
facts, and thumbnail behavior is unchanged. Differences that do exist:

- **`Packet` gained a required `pos` field** (byte offset), and
  `scan_packets` now asks ffprobe for `pts_time,dts_time,size,pos,flags`
  instead of four fields. `pos` feeds the io layer's seek index and is unused
  by measurement; `MediaFacts` and every verdict are unaffected. Any code
  constructing `Packet(...)` directly must pass `pos` -- in mosaic_api only
  the transferred tests did.
- **Raw H.264 elementary streams probe instead of raising.** `MediaFacts`
  gained `timing_measured` (default True), `TimestampSource` gained
  `"none"`, and `.h264` joined `VIDEO_EXTENSIONS`. A raw stream probes with
  placeholder timing (fps and duration 0.0, frame count real) and an
  analysis verdict that selects the timestamp-generating remux. Binding for
  this migration: `FACT_FIELDS` and its mirrors do not persist
  `timing_measured`, so a raw stream's facts would round-trip through the
  database as `timing_measured=True` and mis-fire `variable_frame_rate` on
  re-derivation. Before mosaic_api accepts `.h264` uploads, add the
  `timing_measured` column across `FACT_FIELDS`, the ORM models, an alembic
  migration, and the restore lists (the standard field-addition flow of
  section 3.3) -- or gate `.h264` out of uploads at registration until that
  column lands.
- **`MediaFacts` gained two more required fields, `identity_scheme` and
  `prober_version`.** They record which regime minted `video_uuid` and
  `content_digest` -- the declared scheme version and the ffprobe build whose
  demuxer output the digest is defined against -- so a later consumer can
  tell a re-mint under a new scheme apart from a file whose content actually
  changed. Neither is hashed, and neither is optional: every construction of
  `MediaFacts` in mosaic_api's own tests (the analogue of `test_verdict.py`'s
  `CLEAN` in this package) must state both. Add `identity_scheme` and
  `prober_version` across `FACT_FIELDS`, the ORM models, an alembic migration,
  and the restore lists the same way as `timing_measured` above, before
  wiring the rewired `probe_media` into production -- a null default here
  would silently claim a scheme and a build the row never had.
- **The thumbnail helpers moved modules** (`media_probe.downscale` and
  `media_probe.thumbnail` became `mosaic_media.thumbnail.downscale` and
  `mosaic_media.thumbnail.extract`) but keep their names on the facade, so
  facade importers see no difference.
- **The stdlib-only justification changed.** `timing.py` no longer says
  "keeps it extractable" (spent once extracted); the recorded reason is now
  the CLI: the transcode runner must start on a machine with ffmpeg and
  nothing else. Do not add numpy to the core.
- **New capabilities mosaic_api did not have** (available, not adopted here):
  `mosaic_media.io` (in-process libav `VideoReader` with frame-exact seeking
  via the packet index and `facts=`/`index=` injection, `MultiVideoReader`,
  `SeekIndex`, `FFmpegVideoWriter`), `mosaic_media.transcode`
  (`build_command`, `run_transcode` with re-probe acceptance),
  `mosaic_media.hwaccel`, and the `mosaic-media` command line application.
- **System requirements are explicit now**: ffmpeg and ffprobe on `PATH`,
  ffmpeg 5.1 or newer at runtime (6.0 or newer to run mosaic-media's own test
  suite). mosaic_api's Docker image already installs ffmpeg; confirm the base
  image's version meets the floor.

## 6. Migration order

A suggested order, sized so each step lands green on its own. The full
pipeline (ruff format, ruff check, basedpyright, pytest -- full runs
serialized per the repository's conventions) is worth running after steps 2,
3, and 4.

One change sits outside this ordering and gates the mosaic merge: making
`finalize.py` track the media index schema so it stops stripping the fact
columns (section 3.7). It depends only on `mosaic.core.dataset`, already a
dependency, not on any moved symbol, so it can land first and on its own,
before the dependency rewire -- and it must land before (or with) the mosaic
`mosaic-media-migration` merge, or every upload finalize strips the columns
that branch just started writing.

1. **Wire the dependency** (section 2): pyproject, `uv sync`, lock file.
   Verify `uv run python -c "import mosaic_media"` and that the fresh
   subprocess pulls neither numpy nor typer.
2. **Rewire the moved names.** Point every import of a moved symbol at
   `mosaic_media` (sections 3.1 and 3.2), leaving `media_probe` temporarily
   in place -- the copies are behavior-identical, so this step is safely
   incremental and the whole suite plus the type checker gate it.
3. **Delete and rename.** Remove the moved modules, rename the remainder to
   `media_facts/` (section 4), rewire the retained-name importers
   (`facts_io`, `media_type_for_container`, `duplicate_stems` sites), update
   `LAYERS` and the extension-literal allowlist, rename `tests/media_probe/`.
4. **Settle the tests** (section 3.6): delete the transferred copies after
   confirming each mosaic-media counterpart exists, trim `test_sequence.py`
   to the `duplicate_stems` cases, rewrite the purity test into the
   core-only-dependency guard, rewire the remaining imports, prune unused
   clip fixtures.
5. **After merge, close the duplication window in mosaic-media.** Two of its
   issues are deferred solely on "the copied files must stay diff-identical
   to the mosaic_api originals": `docs/issues/ffmpeg-runner-duplication.md`
   (consolidate the four run-ffmpeg-to-completion helpers into one core leaf)
   and `docs/issues/thumbnail-dimension-tests-assert-literal-tuples.md`
   (rewrite tuple-literal assertions as property assertions). This migration
   unblocks both; record that in the mosaic-media repository so they are
   picked up.

## 7. Risks and open questions

- **Deployment cannot resolve the editable path source yet.**
  `mosaic_api/Dockerfile` copies only mosaic_api's own tree and runs
  `uv sync --frozen`, and `mosaic_deploy/setup.sh` clones only mosaic_api and
  mosaic_app -- neither `../mosaic` nor `../mosaic_media` exists in the image
  build context. This gap already exists for the `mosaic-behavior` path
  dependency; adding mosaic-media makes it two instances. The deploy
  repository needs to clone both siblings and extend the build to include
  them before the next image build.
- **Multi-video open cost** (resolved upstream): `MultiVideoReader` accepts
  `facts=` and `indices=` sequences parallel to the paths, so persisted
  facts are injectable without a re-probe (which would also apply default
  thresholds rather than the configured ones). mosaic_api constructs no
  multi-video readers today; if it ever does, inject the stored facts.
- **Hardware-encode gate on the transcode half**
  (`encoder-gate-checks-listing-not-usability.md`, active): the transcode
  command builder still gates `av1_nvenc` on the ffmpeg encoder listing, a
  build-time property, so on a machine whose build lists NVENC without a
  usable device an opted-in hardware transcode fails loudly instead of
  falling back to the CPU encoder. Irrelevant to this migration (no transcode
  call sites), binding for the future job wiring: until the issue closes,
  wire transcode jobs with `allow_hardware=False` or accept the loud failure.
- **`index.csv` now carries authoritative facts** (was: re-measured via
  OpenCV). On the `mosaic-media-migration` branch `Dataset.index_media` probes
  through `mosaic_media.probe_media`, not the toolkit's former OpenCV
  `get_video_metadata`, so `index.csv`'s width/height/fps/codec are the same
  measurement the backend holds rather than a divergent re-measurement -- the
  metadata-authority gap this bullet previously flagged is closed upstream.
  What remains is the reverse: `finalize.py` strips the fact columns
  `index_media` writes (section 3.7); fix that in sync with the mosaic merge.
  The flat `analysis_transcode` / `stream_transcode` cells `index_media`
  writes are derived with `DEFAULT_THRESHOLDS`, so the database verdict
  columns (re-derived with the configured thresholds, section 3.2) stay
  authoritative for the verdicts; the index's added value is the `media_facts`
  JSON and the derivative-path routing links, not the flat verdict cells.
- **The verdicts stay independent.** The rewire touches every place the two
  transcode verdicts flow (schemas, status endpoint, aggregation). Browser
  playback and per-frame analysis are independent questions with independent
  reason sets; merging fields, reason vocabularies, or severity logic while
  moving imports would collapse a distinction the command selection depends
  on.
- **Scope not decided upstream**: mosaic-media's README leaves open whether
  the toolkit's readers move into `[io]` permanently and where the repository
  visibility line sits for the transcode command builder. Neither affects
  this migration's mechanics; both affect what mosaic_api may later import.
