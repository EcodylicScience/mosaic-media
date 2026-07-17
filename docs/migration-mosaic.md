# Migrating mosaic onto mosaic-media

This guide is for a mosaic developer working on the toolkit's adoption of
`mosaic-media`. It inventories the places found in the mosaic repository that
read, probe, or write video, suggests which `mosaic_media` API could replace
each one, lists the OpenCV uses that stay, and proposes a migration order with
verification steps.

File and line references were surveyed at mosaic commit `8dfaed6` against
mosaic-media commit `e4226b7`. Line numbers drift; the symbol names do not.
The guide is a map and a suggested order, not a prescription -- it comes from
a survey of code its readers know better than the surveyor, so where it
disagrees with the code, the code wins; please correct the guide.

For the design rationale -- why the probe, verdict, transcode, and reader live
in one package upstream of both `mosaic_api` and `mosaic`, and why OpenCV
stops being the decoder -- see the mosaic-media `README.md`, especially "The
OpenCV decode problem", "The reader", and "Adopting this package".


## 1. What this migrates and why

mosaic currently decodes and measures video through `cv2.VideoCapture`, whose
bundled ffmpeg build cannot decode AV1 -- the codec the stack's transcodes
currently produce -- and whose `CAP_PROP_POS_FRAMES` seeking lands off by
tens of frames on variable-rate files (measured; see the mosaic-media README,
"The reader"). This migration replaces `cv2.VideoCapture`
and the `CAP_PROP_*` properties with `mosaic_media`'s in-process libav reader
(the `av` package, PyAV), replaces mosaic's ad hoc metadata probing (OpenCV
properties, a one-off ffprobe fallback, and a full-decode frame counter) with
the packet-scan probe, and mounts the `mosaic-media` command line application
into the `mosaic` CLI. OpenCV remains a mosaic dependency for codec-free image
processing; it stops being a decoder.


## 2. Dependency wiring

`mosaic-media` has three dependency layers:

| Layer | Adds | Contents |
| --- | --- | --- |
| core (`mosaic-media`) | standard library only | probe, verdicts, ffmpeg command construction, thumbnails, hardware capability probing |
| `mosaic-media[io]` | `numpy>=1.22`, `av>=18,<19` | `VideoReader`, `SeekIndex`, `MultiVideoReader`, `FFmpegVideoWriter` |
| `mosaic-media[cli]` | `typer>=0.12` | the `mosaic-media` typer application |

mosaic needs all three: the probe for metadata, `[io]` for frame reading, and
`[cli]` to mount the media application. Both floors are compatible: mosaic and
mosaic-media each require Python `>=3.12`.

In `pyproject.toml`, add the dependency and the editable path source. The
editable source follows the existing precedent of `mosaic-behavior` wired into
`mosaic_api` as `{ path = "../mosaic", editable = true }`:

```toml
[project]
dependencies = [
    # ... existing entries ...
    "mosaic-media[io,cli]>=0.1.0",
]

[tool.uv.sources]
mosaic-media = { path = "../mosaic_media", editable = true }
```

Released versions are best deferred while the API is still moving; every
cross-repository change (for example a new `MediaFacts` field) would pay a
release-and-bump cycle that the editable path source avoids.

System requirements shift; section 4.9 lists the documentation that still
describes the old stack. The new requirements:

- Frame decode no longer needs an ffmpeg binary at all: `VideoReader` and
  `FFmpegVideoWriter` run libav in process through the `av` wheel.
- `probe_media` (and therefore `MultiVideoReader` construction and
  `Dataset.index_media`) still shells out to system `ffprobe`.
- The transcode runner needs system `ffmpeg` 5.1 or newer (`-fps_mode`);
  running the mosaic-media test suite needs 6.0 or newer
  (`-display_rotation`).
- `import mosaic_media` (the core facade) pulls neither numpy nor typer;
  only `mosaic_media.io` imports numpy and av, and only `mosaic_media.cli`
  imports typer.


## 3. API orientation: what replaces what

| mosaic today (`mosaic.core.media`) | mosaic-media replacement |
| --- | --- |
| `get_video_metadata` -> `VideoMetadata(path, width, height, fps, frame_count)` | `mosaic_media.probe_media(path) -> MediaFacts` (adds codec, rotation, duration, constant-frame-rate flag, keyframe interval, and more) |
| `_ffprobe_fps` fallback | deleted; the probe measures fps from packet timestamps |
| `_count_frames_by_decoding` | deleted; the packet scan counts frames without decoding |
| `_has_container` seekability heuristic | deleted; `VideoReader` seeks via the packet index on every container |
| `cv2.VideoCapture` + `CAP_PROP_POS_FRAMES` / `CAP_PROP_*` | `mosaic_media.io.VideoReader`: `read()`, `read_batch(n)`, `seek(frame_index)`, `read_frames(indices)`, `width`/`height`/`fps`/`frame_count`, iteration, context manager |
| `FFmpegFrameReader` (ffmpeg subprocess pipe) | `mosaic_media.io.VideoReader` (same constructor shape: `start_frame`, `end_frame`, `frame_step`, `resize`, `hwaccel`; adds `grayscale`, `facts=`, `index=` injection, and random access) |
| `MultiVideoReader` (capture-based) | `mosaic_media.io.MultiVideoReader` (probe-backed, frame-exact; plain video files only -- see section 4.4 for imgstore dispatch) |
| `FFmpegVideoWriter` (ffmpeg subprocess pipe) | `mosaic_media.io.FFmpegVideoWriter` (in-process libav; identical constructor and `write`/`close`/`frames_written` surface) |
| `_ffmpeg_available` / `_nvdec_available` / `_nvenc_available` | `mosaic_media.hwaccel.ffmpeg_available` / `nvdec_available` / `encoder_available(name)` |
| (new) | `mosaic_media.io.SeekIndex`, `build_seek_index` -- the packet index behind frame-exact seeking, injectable into `VideoReader` |
| (new) | `mosaic_media.transcode.run_transcode`, `derive` (verdicts), `mosaic_media.cli.media_app` |

Errors: everything in `mosaic_media` raises `MediaProbeError` (probe, reader,
writer) or `TranscodeError` (converter) instead of `RuntimeError` or silent
`(False, None)` mid-stream returns. Every `except RuntimeError` around a media
call is worth revisiting.

Keeping `mosaic.core.media` as the public facade (the import path every
mosaic consumer already uses) and swapping its internals -- the facade
re-exports mosaic-media types plus the mosaic-specific glue that stays
(imgstore adapters, dispatch helpers, pure range/crop helpers) -- avoids
scattering new import paths across the feature libraries.


## 4. Inventory of migration sites

### 4.1 Metadata and probing

| Site | What it does today | Migration |
| --- | --- | --- |
| `src/mosaic/core/media/video_io.py:173` `get_video_metadata` | OpenCV `CAP_PROP` read; falls back to ffprobe for fps and a full decode for frame count on raw streams; dispatches imgstore directories to `imgstore_metadata` | Body becomes: imgstore dispatch unchanged; plain files call `probe_media` and map `MediaFacts` to the returned metadata. Keep the function as the facade (callers at `extraction.py:133`, `inference.py:227`, `inference.py:758`, `inference.py:1202` use `meta.path`, which `MediaFacts` does not carry -- the facade resolves the path). Use display dimensions, not coded ones: swap width and height when `facts.rotation_degrees % 180 == 90` (see section 6, rotation). |
| `video_io.py:78` `_ffprobe_fps` | One-off ffprobe subprocess for `r_frame_rate` | Delete. The probe fits fps from the packet timestamps of the whole file; `r_frame_rate` is neither the average nor an upper bound and is deliberately not even stored in `MediaFacts`. |
| `video_io.py:107` `_count_frames_by_decoding` | Decodes the entire file to count frames | Delete. `MediaFacts.frame_count` comes from the packet scan without decoding a frame. |
| `video_io.py:279` `_has_container` | Treats `CAP_PROP_FRAME_COUNT` garbage as "not seekable" | Delete along with every `seekable` branch. `VideoReader` seeks through the packet index; there is no seekable/non-seekable split anymore. See the raw `.h264` risk in section 8 before deleting. |
| `src/mosaic/core/dataset.py:31` `_probe_video_metadata` and `:70` `_parse_ffprobe_rate` | Header-only ffprobe (`width`, `height`, `avg_frame_rate`/`r_frame_rate`, `codec_name`) for `index_media`; returns `{}` and warns on failure | Replace with `probe_media`. `MediaFacts` supplies `width`, `height`, `fps`, `codec_name`, and now also `frame_count` and `duration` for the index. Preserve the warn-and-skip contract by catching `MediaProbeError` at the call site (`dataset.py:1152`). Note the cost change, the caller-side probe parallelization, and the measurement-authority opportunity in section 6. |
| `dataset.py:1193` `imgstore_probe` | imgstore branch of `index_media` | Stays as is (imgstore layer). |

### 4.2 Sequential frame reading

| Site | What it does today | Migration |
| --- | --- | --- |
| `video_io.py:670` `FFmpegFrameReader` | ffmpeg subprocess pipe: decode-time resize, `select` filter stepping, optional NVDEC | Replace with `mosaic_media.io.VideoReader`. The constructor is compatible (`start_frame`, `end_frame`, `frame_step`, `resize`, `hwaccel`); `read`, `read_batch`, iteration, `frame_count`, and `__len__` have the same shapes and semantics (frame_count is the number of frames the window will output). Differences: `hwaccel` is a documented no-op (software decode; see section 6), and the reader additionally supports `grayscale=True`, `seek`, `read_frames`, and `facts=`/`index=` injection. |
| `video_io.py:903` `open_frame_reader` | Dispatch: imgstore directory -> `ImgStoreFrameReader`, else `FFmpegFrameReader` | Keep the dispatcher in mosaic; the plain-video branch returns `VideoReader`. The `FrameReader` protocol (`video_io.py:47`) stays as the dispatch vocabulary -- `VideoReader` satisfies it structurally. |
| `video_io.py:938` `_prefetch_batches` | Background-thread batch prefetch over any `read_batch` reader | Keep in mosaic unchanged; it works over `VideoReader` and `ImgStoreFrameReader` alike. |
| `src/mosaic/tracking/pose_training/inference.py:139` `run_inference` (reader path at `:250`, availability gate at `:200-224`) and `:662` `run_point_inference` (reader path at `:781`, gate at `:729-755`) | Batched inference over `open_frame_reader`; `use_ffmpeg=None/True/False` chooses between the ffmpeg pipe and an OpenCV fallback | The reader path is unchanged through the dispatcher. The OpenCV fallback branches (`inference.py:334`, `inference.py:868`) and the `_ffmpeg_available` gating lose their reason to exist: in-process decode needs no ffmpeg binary. A reasonable cleanup: delete the fallback branches and the `use_ffmpeg` parameter (or accept-and-ignore it with a deprecation warning for one release); porting the fallback onto `VideoReader` would duplicate one code path. |

### 4.3 Capture-style random access (`open_capture` and `CAP_PROP_*`)

`SupportsCapture` (`video_io.py:31`) is the `cv2.VideoCapture` subset:
`isOpened`/`read`/`set(propId, value)`/`get(propId)`/`release`. `VideoReader`
deliberately does not implement it -- seeking is `reader.seek(frame_index)`
and metadata are properties. Each call site is rewritten onto the reader's own
surface; the `CAP_PROP` idiom does not survive the migration on plain videos.

| Site | What it does today | Migration |
| --- | --- | --- |
| `video_io.py:69` `open_capture` | imgstore -> `ImgStoreCapture`, else `cv2.VideoCapture` | The plain-video branch becomes `VideoReader`. Since the two halves no longer share the `CAP_PROP` protocol, either give `ImgStoreCapture` a small `seek()`-style wrapper so both sides satisfy one lean protocol (`read`, `seek`, `width`, `height`, `fps`, `frame_count`, `close`), or dispatch at each call site. The wrapper is the smaller change and keeps `open_capture` as a facade. |
| `video_io.py:290` `extract_candidate_features` | `open_capture` + `_has_container` + `CAP_PROP_POS_FRAMES` seek, then per-frame `cv2.resize`/`cvtColor` and flatten | Collapses onto `VideoReader(path, start_frame=start, end_frame=end + 1, frame_step=candidate_step, resize=resize, grayscale=grayscale)` -- the window, stepping, resize, and grayscale all happen in the decoder. Keep `apply_crop` (crop before resize means the reader-level `resize` only applies when there is no crop; with a crop, read full frames and keep the existing `cv2.resize` image op). Note the interpolation difference in section 6. |
| `video_io.py:353` `save_frames_as_png` | Seek per target frame via `CAP_PROP_POS_FRAMES` (or sequential scan on raw streams), `cv2.imwrite` per frame | `VideoReader.read_frames(indices)` decodes the targets in GOP-grouped forward passes, frame-exact. `cv2.imwrite` stays. The seekable/non-seekable split disappears. |
| `inference.py:33` `run_inference_opencv` (capture at `:82`, count at `:86`, seek at `:90`) and `:509` `run_point_inference_opencv` (capture at `:560`, count at `:564`, seek at `:568`) | Single-frame OpenCV decode variants | Rewrite onto `VideoReader` (`frame_count` property replaces the `raw_count` garbage check; constructor `start_frame`/`end_frame`/`frame_step` replace the manual loop bookkeeping) -- or fold them into `run_inference`/`run_point_inference`, whose batched path now covers the no-ffmpeg case too. Folding is cleaner; keep the public names as thin wrappers if callers exist. |
| `inference.py:1094` `visualize_inference` (capture at `:1214`, per-result seek at `:1251`) | Seeks to `start_frame + i * frame_step` per result | `VideoReader(start_frame=..., frame_step=...)` read sequentially, or `read_frames` over the exact target indices. The per-frame `cap.set` seek loop disappears. |
| `src/mosaic/tracking/pose_training/localizer_inference.py:154` `run_localizer_inference` (capture at `:228`, count at `:232`, seek at `:236`) | Same capture pattern | Same rewrite as the inference functions. |
| `src/mosaic/behavior/visualization_library/video_stream.py:18` `_FrameStream` | Calls `reader.seek(start)` and `reader.read()` | Already reader-shaped; works unchanged over the mosaic-media `MultiVideoReader`. Only its `cv2.resize` stays (image op). |

### 4.4 Multi-video sequences

`mosaic_media.io.MultiVideoReader` reads N ordered plain video files as one
global frame space: `probe_media` per file at construction, uniformity
validated with `uniform_properties` (the same check the backend uses), facts
and cached packet indices injected into per-segment `VideoReader`s, `seek` and
`read` with automatic segment transitions. It does not know imgstores.
mosaic's current `MultiVideoReader` (`video_io.py:447`) dispatches per path
through `open_capture`, so store directories work today (exercised by
`tests/test_imgstore_io.py:149` and `:172`).

Migration: introduce a dispatch at the facade -- sequences of plain video
files construct `mosaic_media.io.MultiVideoReader`; sequences containing an
imgstore keep a capture-based implementation over `ImgStoreCapture` (which can
shrink to exactly the store case once plain videos leave it). Decide
explicitly whether mixed video-plus-store sequences are supported; today they
are possible in principle and almost certainly unused. Recommended: reject
mixed sequences with a clear error until a need appears.

| Call site | Notes |
| --- | --- |
| `video_stream.py:144` `render_stream` | Uses `seek`/`read`/`fps`/`width`/`height` -- compatible as is. |
| `src/mosaic/behavior/visualization_library/interaction_crop.py:197-223` | Uses `seek`/`read`/`fps` -- compatible. |
| `src/mosaic/behavior/visualization_library/egocentric_crop.py:634-642` | Uses `read`/`total_frames`/`fps` -- compatible. |
| `src/mosaic/tracking/frame_extraction/extraction.py:278` `extract_frames_multi` | Uses `total_frames`/`width`/`height`/`fps`/`seek`/`read` -- compatible. |
| `video_io.py:1113` `extract_candidate_features_multi` | Reader-shaped loop; drop only the image-op notes from 4.3. |
| `video_io.py:1162` `save_frames_as_png_multi` | Checks `s.seekable for s in reader.segments`; the mosaic-media `VideoSegment` has no `seekable` field (every segment seeks). Delete the non-seekable branch. |

Two behavioral deltas and one cost delta are called out in section 6:
frame-rate mismatch now raises instead of warning, an uninjected construction
runs one ffprobe subprocess per file (inject `facts=` and `indices=` to skip
it), and `VideoSegment` reports displayed (rotation-corrected) dimensions.

### 4.5 Video writing

| Site | What it does today | Migration |
| --- | --- | --- |
| `video_io.py:957` `FFmpegVideoWriter` | Pipes raw bgr24 to an ffmpeg subprocess; libx264 or h264_nvenc | Delete; `mosaic_media.io.FFmpegVideoWriter` is its drop-in replacement (same constructor: `output_path, width, height, fps, crf, preset, hwaccel`; same `write`, `close`, `frames_written`, context manager). Differences: encodes in process through libav; raises `MediaProbeError` instead of `RuntimeError`; validates frame shape/dtype per write; gates NVENC on a cached real-usability probe (opening the encoder once), not on the encoder listing; exposes `encoder_name`. It requires `(height, width, 3)` uint8 BGR -- it does not accept grayscale frames. |
| `interaction_crop.py:313-320` | Prefers `FFmpegVideoWriter(hwaccel=True)`, falls back to `create_video_writer` on `(ImportError, RuntimeError)` | Import from `mosaic_media.io`; the fallback exception must include `MediaProbeError`. Since the writer now probes NVENC usability itself and falls back to libx264 internally, the only remaining failure worth catching is an unwritable output -- consider letting it raise. The grayscale branch (`:322-325`, `cv2.VideoWriter` with `isColor=False`) stays on OpenCV or stacks the single channel to three; the mosaic-media writer will not take 2D frames. |
| `inference.py:1225-1234` `visualize_inference` writer | Same prefer-then-fallback pattern, catching `(RuntimeError, ImportError)` | Same treatment. |
| Sites that stay on `cv2.VideoWriter` | `helpers.py:133` `create_video_writer`, `egocentric_crop.py:664-669` (grayscale), `playback.py:180-182`, and the test fixtures below | The migration target is the decoder (`cv2.VideoCapture` and `CAP_PROP_*`), not every encoder. These write visualization artifacts, not analysis inputs. Migrating the BGR ones to the shared writer is a reasonable follow-up, not a requirement of this migration. |

### 4.6 Hardware and binary capability probing

| Site | Migration |
| --- | --- |
| `video_io.py:129` `_ffmpeg_available` | `mosaic_media.hwaccel.ffmpeg_available()`. Most callers disappear with the OpenCV fallbacks (4.2); the remaining need is documentation/setup checks. |
| `video_io.py:137` `_nvdec_available` (`ffmpeg -hwaccels` string match) | `mosaic_media.hwaccel.nvdec_available()` -- an actual `-init_hw_device cuda` null decode, because distribution builds list `cuda` on machines with no usable device. Note that `VideoReader` decode is software-only today, so this matters mainly for the transcode path. |
| `video_io.py:155` `_nvenc_available` (`h264_nvenc` in `-encoders` output) | `mosaic_media.hwaccel.encoder_available("h264_nvenc")` for the system-ffmpeg question; the in-process writer runs its own usability probe internally and does not need the caller to check. See the open issue in section 8 about listing versus usability for the transcode encoder gate. |

### 4.7 CLI

| Site | Migration |
| --- | --- |
| `src/mosaic/cli/__init__.py` | Mount the media application exactly like the existing sub-applications: `from mosaic_media.cli import media_app` then `app.add_typer(media_app, name="media")`. This yields `mosaic media probe FILE` (MediaFacts plus both verdicts as JSON) and `mosaic media transcode FILE --target analysis|playback --output PATH`. The import is light (typer is already a mosaic dependency; the media commands lazy-import nothing heavy), consistent with the CLI's import-light convention. |
| `src/mosaic/cli/index_media.py` | Surface unchanged; the behavior change is inside `Dataset.index_media` (4.1). Optional: default the extension list from `mosaic_media.VIDEO_EXTENSIONS` instead of the hardcoded `".mp4,.avi"`. |
| Job infrastructure (transcode as a job) | Per the mosaic-media README's division of responsibility: scheduling stays in mosaic's job infrastructure, which calls the Python API (`mosaic_media.transcode.run_transcode`) directly -- structured exceptions, no argv escaping, no output parsing. The CLI is for humans. This wiring can land after the reader adoption; it is listed here so it is not lost. |

### 4.8 Tests

Never delete a test when its contract changes; rewrite it to assert the new
invariant.

| Site | Migration |
| --- | --- |
| `tests/test_imgstore_io.py:215-232` `test_open_frame_reader_dispatch` | Asserts the plain-mp4 branch returns `FFmpegFrameReader`; rewrite to assert `mosaic_media.io.VideoReader`. The `_ffmpeg_available` skip guard is no longer about decode (in-process) -- keep it only if the fixture encoding still needs it. |
| `tests/test_imgstore_io.py:117-140` `ImgStoreCapture` `CAP_PROP` assertions | Stay while `ImgStoreCapture` keeps the capture protocol; if you add the lean `seek()` wrapper from 4.3, add assertions for it rather than replacing these. |
| `tests/test_imgstore_io.py:149,172` `MultiVideoReader` over stores | Pin the imgstore dispatch branch of the new facade. |
| `tests/test_imgstore_io.py:227` and `tests/test_index_media_imgstore.py:31` | `cv2.VideoWriter` mp4v fixtures. Fine to keep (test-only encoding), or switch to `mosaic_media.io.FFmpegVideoWriter` for consistency. |
| `tests/conftest.py` `make_imgstore` | `npy` stores, codec-free -- unchanged. |
| New coverage to add | (a) metadata equivalence: `get_video_metadata` after the rewire returns the same width/height and a sane fps/frame_count on the existing fixtures; (b) a raw `.h264` fixture if raw-stream support is kept (section 8); (c) the multi-video dispatch (plain, store, mixed-rejected). |

### 4.9 Documentation

All of these currently describe the OpenCV/ffprobe stack and need updating to
the new dependency story from section 2:

- `README.md:37-41` ("`ffmpeg` provides `ffprobe`, used by media indexing and
  raw H.264 support") and the pipeline diagram at `:109` ("ffprobe metadata").
- `docs/getting-started.md:10-35` (conda ffmpeg install rationale, index-media
  description).
- `docs/index.md:44` and `:112` (same statements).
- `docs/api/media/video-io.md` (documents `FFmpegFrameReader`, frame counting,
  fps detection; the mosaic-media-backed surface replaces all of it).

### 4.10 Out of scope

- `src/mosaic/behavior/feature_library/external/` -- an isolated, separately
  locked environment for external model protocols; its lockfile pulls
  `imageio-ffmpeg` transitively through those third-party packages. Not
  mosaic's media I/O; untouched.
- `src/*.egg-info/PKG-INFO` -- build artifacts echoing the README text.
- The imgstore descriptor and index layer (`metadata.yaml` parsing, `.npz`
  chunk indexes, frame-to-chunk mapping) stays in mosaic permanently. Only its
  chunk decode moves onto `VideoReader`, and that is a later step (section 7,
  step 8): today the decode happens inside the third-party `imgstore` package
  (`imgstore_io.py:97` `_open_store` -> `store.get_next_image`), so moving it
  means reimplementing store reading natively (descriptor + `.npz` index +
  `VideoReader` over the mp4 chunks) and dropping the `imgstore` package from
  the read path. That is a self-contained project; nothing else in this
  migration depends on it.


## 5. What stays: the OpenCV image-processing surface

OpenCV remains a mosaic dependency. Every use below is codec-free and stays
exactly as is. Listing them makes the boundary unambiguous -- if a `cv2` call
is not in this list and not in the inventory above, it was missed.

- Drawing and text: `overlay.py` (rectangle, circle, putText, line,
  arrowedLine, fillPoly, polylines, addWeighted), `inference.py`
  keypoint/detection drawing (`:431-438`, `:1023-1030`),
  `localizer_inference.py:275`.
- Geometry and filtering: `interaction_crop.py` and `egocentric_crop.py`
  (getRotationMatrix2D, warpAffine, ellipse, bitwise_and, createCLAHE,
  cvtColor BGR2GRAY/BGR2LAB/LAB2BGR), `interaction_crop.py:199`
  `cv2.setNumThreads(2)`.
- Resizing: `video_stream.py:77` (INTER_AREA), `extract_candidate_features`
  crop-then-resize path, `identity_model.py:240`,
  `megadescriptor_identity_model.py:249`,
  `dinov2_temporal_identity_model.py:346` (INTER_LINEAR),
  `inference.py:1262`, `imgstore_io.py:354`.
- Image file I/O (image codecs, not video codecs): `cv2.imread` in
  `identity_common.py:342-350`, `identity_model.py:479-483`,
  `coco_localizer.py:260`, `cvat_localizer.py:165`; `cv2.imwrite` everywhere
  frames or crops are saved (`video_io` PNG saving, `egocentric_crop.py:722`,
  `inference.py`, `localizer_inference.py:277`, `playback.py:217`).
- Interactive display: `cv2.imshow`/`waitKey`/`destroyWindow` in
  `playback.py:204-233` and `inference.py:1313-1348`.
- Channel normalization for imgstore frames: `imgstore_io.py:183` `_to_bgr`
  (GRAY2BGR).
- Encoding that stays per section 4.5: `helpers.py:133` `create_video_writer`
  and the grayscale `cv2.VideoWriter` branches.

`helpers.py:114` `_open_video_capture` has no callers in the repository;
delete it during this migration rather than porting it.


## 6. Behavioral differences to expect

**Color and array shapes.** `VideoReader` emits BGR uint8 `(height, width, 3)`
-- the OpenCV convention -- so downstream image code is unchanged. With
`grayscale=True` it emits 2D `(height, width)` arrays (no third axis), unlike
the `cvtColor`-after-read pattern which also yields 2D; check any code that
assumed three channels before an explicit conversion.

**Seeks are frame-exact through the packet index.** This matters only on
variable-rate files: measured against pixel-content ground truth, OpenCV
landed every constant-rate control seek exactly, so artifacts built from
constant-rate sources should not shift. On variable-rate files OpenCV's
average-rate conversion landed tens of frames off -- and those are the files
the analysis pipeline transcodes to constant rate before per-frame work
anyway. A shift after migration should therefore appear only where a
variable-rate original was seeked directly; if a diff-based expectation (a
golden image, a cached crop) moves, that is the first place to look.

**Frame counts are measured, not declared.** `MediaFacts.frame_count` counts
distinct packet presentation timestamps across the whole file. On clean files
it equals what OpenCV reported; on variable-rate or lying-header files it can
differ -- and the probe is the authority. Any dataset whose track tables were
aligned against OpenCV's count on such files will surface the discrepancy now
rather than silently misaligning (see section 8).

**Rotation.** OpenCV auto-orients on read, and its reported properties follow
suit. `MediaFacts.width`/`height` are the coded dimensions; the displayed
dimensions swap when `rotation_degrees % 180 == 90`. `VideoReader` applies the
rotation in process (bit-exact against system-ffmpeg autorotation for the
corpus-verified quarter-turn) and reports displayed dimensions, and the
mosaic-media `MultiVideoReader`'s segments do the same -- but code that reads
raw `MediaFacts` for geometry (crop validation, writer dimensions) must do the
swap itself. All three display rotations (90, 180, 270) have an in-process
filter mapping, each golden-verified bit-exact against system-ffmpeg
autorotation; a rotation outside the mapping raises `MediaProbeError` instead
of being silently mis-oriented.

**Resize interpolation.** The reader's `resize` uses bicubic (matching system
ffmpeg's `-vf scale` default). mosaic's paths used `INTER_AREA`
(`extract_candidate_features`, `_FrameStream`) or the ffmpeg pipe's `scale`.
Pixel values of resized frames will differ slightly from the OpenCV-resized
history; k-means frame selection over resized features can select different
representatives across a version boundary. Not a correctness issue -- but do
not compare resized outputs across the migration bit-for-bit.

**Errors are loud.** A truncated or undecodable file surfaces as
`MediaProbeError` mid-read instead of a silent early `(False, None)`. A
multi-video sequence with mismatched frame rates raises `ValueError` at
construction (mosaic's reader only warned). Failed probes raise instead of
returning zeros. Broad `except` blocks around media calls are worth auditing.

**Metadata authority.** The probe runs once and its `MediaFacts` travel
forward; consumers do not re-measure (mosaic-media README, "Metadata
authority"). Concretely: wherever mosaic already holds facts for a file (an
index row, a prior probe), inject them -- `VideoReader(path, facts=...,
index=...)` skips all per-open probing. `Dataset.index_media` is the natural
place to measure once: consider persisting more of `MediaFacts` in
`media/index.csv` so downstream opens can inject instead of re-probing. The
`MultiVideoReader` constructor accepts `facts=` and `indices=` sequences
parallel to the paths; wire the stored facts through wherever sequences are
opened.

**Cost envelope.** The mosaic-media performance gate (spec
`2026-07-16-extraction-and-reader-design.md`, gate table) holds sequential and
batched decode at parity or better against OpenCV, sparse reads at parity, and
documents one regression accepted with rationale: a cold random seek costs up
to about 2x an OpenCV seek (correctness of the landing is the trade). A
from-scratch multi-video open measures 0.739 of OpenCV's throughput because
construction pays a per-file probe (bounded at 1.5x); the consumer-shaped open
injects `facts=` and `indices=` at construction and is gated at the sequential
tier. Separately, `index_media` moves from a header-only ffprobe per file to
a full packet scan per file -- on large corpora indexing takes visibly longer
and returns strictly more (frame counts, measured fps) -- worth stating in
the docs. The per-file probes are independent ffprobe subprocesses and
parallelize near-linearly from a caller-side worker pool (measured 1.99x on
two workers with a warm page cache; the interpreter releases the GIL while
waiting on the child process). A bounded pool inside `index_media`'s loop is
the natural home for that -- the package itself takes no concurrency policy.
Size the bound to the storage: a warm probe is dominated by process startup
(roughly 90 ms for the two ffprobe calls), while a cold probe reads the
entire file, so a wide pool against a spinning disk or shared NAS can degrade
aggregate throughput where an NVMe drive benefits.

**Encoding.** The in-process writer produces the same mp4/h264/yuv420p
output. NVENC selection is now usability-probed rather than listing-probed, so
a GPU-less machine with a listing-happy ffmpeg build silently gets libx264
instead of a startup failure.


## 7. Migration order

A suggested order, sized so each step lands green on its own. Running the
full mosaic suite after each step (`uv run pytest tests/`), plus the named
checks, catches slips early.

1. **Wire the dependency** (section 2). Verify `import mosaic_media`,
   `import mosaic_media.io`, and `from mosaic_media.cli import media_app` all
   resolve inside mosaic's environment.
2. **Metadata**: rewire `get_video_metadata` internals onto `probe_media`;
   delete `_ffprobe_fps` and `_count_frames_by_decoding`; rewire
   `Dataset.index_media` off `_probe_video_metadata`. Verify: existing
   imgstore metadata tests pass unchanged; a manual `mosaic index-media` run
   over a real dataset produces the same width/height and sane fps values.
3. **Sequential readers**: `open_frame_reader`'s plain-video branch returns
   `VideoReader`; delete `FFmpegFrameReader`; delete the OpenCV fallback
   branches and `use_ffmpeg` gating in `run_inference`/`run_point_inference`.
   Verify: the dispatch test (rewritten per 4.8) and an end-to-end inference
   smoke run on a real video.
4. **Capture call sites**: rewrite `extract_candidate_features`,
   `save_frames_as_png`, `run_inference_opencv`, `run_point_inference_opencv`,
   `run_localizer_inference`, and `visualize_inference` onto reader-native
   `seek`/`read_frames`; decide and implement the `open_capture` facade shape
   (lean protocol wrapper for `ImgStoreCapture`). Verify: frame-extraction
   round trip (`extract_frames` uniform and kmeans) on a fixture video;
   saved-PNG indices match requested indices.
5. **Multi-video**: dispatching facade over `mosaic_media.io.MultiVideoReader`
   (plain) and the store-backed implementation; adapt
   `save_frames_as_png_multi` (drop `seekable`); pass stored facts through the
   constructor's `facts=` seam so repeated opens stop re-probing. Verify:
   `test_imgstore_io.py` multi tests;
   `extract_frames_multi` on a two-file sequence; overlay rendering
   (`render_stream`) over a sequence.
6. **Writers and capability probes**: swap `FFmpegVideoWriter` imports, fix
   the fallback exception types, replace the three availability helpers with
   `mosaic_media.hwaccel`, then delete the dead halves of `video_io.py`. What
   remains in `mosaic.core.media` is: the facade re-exports, the imgstore
   modules, the dispatchers, `_prefetch_batches`, and the pure helpers
   (`normalize_frame_range`, `normalize_crop_rect`, `apply_crop`,
   `extract_candidate_features*`, `save_frames_as_png*`). Verify: crop
   pipelines (`interaction_crop`, `egocentric_crop`) produce playable output
   on a fixture.
7. **CLI mount and docs**: mount `media_app`; update the four documentation
   surfaces (4.9). Verify: `mosaic media probe` on a fixture; `mosaic --help`
   lists `media`; `mosaic media transcode --target analysis` on a
   known-defective fixture produces output that re-probes clean.
8. **imgstore chunk decode** (separable, later): reimplement store reading on
   the descriptor + `.npz` index with `VideoReader` chunk decode, dropping the
   `imgstore` package from the read path. Keep `ImgStoreCapture`'s public
   behavior pinned by the existing tests.
9. **Transcode job wiring** (last, after the reader adoption): mosaic jobs call
   `run_transcode`; scheduling, cancellation, and subprocess lifecycle stay in
   mosaic's job infrastructure.

The sequencing constraint from the mosaic-media README binds here: the
transcode must not be exposed to production datasets before step 3 lands,
or the stack produces files (currently AV1) the toolkit cannot read back.


## 8. Risks and open questions

**Raw `.h264` elementary streams: supported, with defined behavior.** mosaic
reads them today, so mosaic-media keeps them: `.h264` is in
`VIDEO_EXTENSIONS`, `probe_media` returns facts with `timing_measured=False`
(the frame count is real -- the packet count -- while fps and duration are
unmeasurable placeholders), the analysis verdict fires
`unreliable_timing_metadata` and selects the timestamp-generating remux, and
`VideoReader` decodes such a file sequentially with `facts=` injected.
Seeking raises `MediaProbeError` -- there are no timestamps to index, the
same non-seekable treatment mosaic gives these files today -- and the
analysis transcode is the route to a seekable derivative. The fallbacks in
steps 2-4 can therefore be deleted: sequential paths keep working through the
reader, and metadata comes from the probe. Keeping the old fallback code as a
parallel path would leave two implementations of one behavior, so the guide
suggests deleting it outright.

**Frame-count discrepancies against existing artifacts.** Track tables,
extraction manifests, and cached crops produced under OpenCV's counts can
disagree with the probe's counts on variable-rate or millisecond-quantized
files. The probe is the authority, but the first pipeline run after migration
on such a dataset will surface off-by-some alignment questions. Plan for a
one-time audit rather than treating each report as a new bug.

**180-degree rotation.** Resolved upstream: the rotation mapping covers 90,
180, and 270, each with a golden fixture; no corpus check is needed.

**Active mosaic-media issues that this migration owns or unblocks** (see
`docs/issues/_INDEX.md` in mosaic-media):

- `multi-video-open-pays-per-file-probe.md` -- resolved: the constructor
  accepts `facts=` and `indices=` sequences parallel to the paths, and the
  junction workload's consumer-shaped (injected) form is gated. What remains
  for this migration is wiring stored facts through the four
  `MultiVideoReader` call sites.
- `encoder-gate-checks-listing-not-usability.md` -- the in-process writer half
  is resolved (usability-probed); the transcode command builder still gates
  `av1_nvenc` on the encoder listing. Relevant once mosaic wires transcode
  jobs (step 9): on a GPU-less machine with permission granted, the transcode
  fails loudly instead of falling back to `libsvtav1`.
- `ffmpeg-runner-duplication.md` -- four run-to-completion ffmpeg helpers in
  the mosaic-media core await consolidation "after the consumer migration
  closes the duplication window". mosaic's adoption is half of that window
  (the `mosaic_api` rewire is the other half); finishing both unblocks it.
- `thumbnail-dimension-tests-assert-literal-tuples.md` -- same
  duplication-window condition; no mosaic action, listed for completeness.

**Scope ceiling (mosaic-media README, open questions).** Whether mosaic's
readers move into mosaic-media permanently under `[io]`, making it the home
for all media I/O and leaving mosaic with image processing only, is not yet
decided. This guide takes the conservative reading: mosaic keeps its facade,
dispatchers, imgstore layer, and pure helpers, and consumes mosaic-media
underneath. If the ceiling decision later moves the dispatchers down, that is
a follow-up extraction, not part of this migration.

**Repository visibility.** The transcode converter was once scoped as a
private backend concern; mosaic-media now carries it. If the repositories'
visibility ever diverges, the `mosaic media transcode` mount inherits the
question. No action for this migration; recorded so the CLI mount is not done
blind to it.
