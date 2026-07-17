# PyAV IO Adoption Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with a fresh
> implementer per task and a review between tasks. Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the `mosaic_media.io` layer to decode and encode in process
through PyAV (the `av` package), replacing the system-ffmpeg subprocess pipe
that the performance gate measured as architecturally too slow (8 of 19
collected bench tests failed; see the spec's "In-process decode supersedes the
subprocess pipe for io"). The public io surface, the frame contract, and every
correctness suite are preserved; the process lifecycle inside `reader.py` and
`writer.py`, and the packet acquisition consumed by `multi.py` and the reader's
index seam, are replaced by container lifecycle.

**Architecture:** The layering is unchanged and one dependency ceiling moves.
`io` still imports only `probe`, `hwaccel`, and itself; the core stays
stdlib-only with its system-ffmpeg subprocesses (probe measurement, thumbnails,
transcode, cli). `av` joins numpy as the io layer's second third-party root.
Seeking becomes `container.seek` to the target frame's presentation timestamp
with backward keyframe resolution, then decode-forward comparing frame
timestamps -- the packet index still supplies the frame-index-to-timestamp map
and the GOP grouping. Rotation is applied in process through a libav transpose
filter graph, verified bit-exact against system-ffmpeg autorotation.

**Tech Stack:** Python 3.12+, uv, `av>=18,<19` (`[io]` extra, alongside numpy),
system ffmpeg and ffprobe on PATH for the core and for test ground truth. No
OpenCV in the runtime path. All av API used below was confirmed against
av 18.0.0 (bundled libav 62.x) with system ffmpeg 6.1.1.

## Global Constraints

Inherited from the frame-reader-io plan and the repository rules: Python floor
3.12 (never 3.13); ASCII only in code (`--`, `->`, `-`, never box-drawing,
arrows, or bullets); American spelling everywhere (`behavior`, `color`, `gray`,
`grayscale`); no `typing.Any`, `typing.Optional`, `typing.cast`, `# noqa`,
`# pyright: ignore`, or any suppression -- fix the design; no multi-line
f-strings (assign the message to a variable, then raise); full identifier names,
no abbreviations except comprehension-local names; frames are BGR uint8
`(height, width, 3)`, or `(height, width)` when `grayscale=True`; one-way
layering guarded by tests; injected `MediaFacts` are never re-measured;
plain-English commits with no conventional-commit prefixes and no
`Co-Authored-By` trailers; no process language, tool names, or plan references
in commits, docstrings, or comments.

Additional constraints for this plan:

- Every public signature under "Interfaces produced (pinned)" stays pinned. The
  performance gate on its branch is written against these sight unseen; a change
  here breaks it silently.
- The framemd5 ground-truth suites and the cv2-equality suite are the
  acceptance instrument for the rewrite and must pass with **zero assertion
  changes** (fixture additions allowed, assertion weakening never). A test whose
  premise the rewrite invalidates (the ffmpeg-availability construction guard)
  is **rewritten to assert the new invariant, never removed**.
- No timed benchmark runs inside this plan's tasks. The gate re-run happens on
  the gate branch after this plan lands (the orchestrator-executed final
  section).
- Full-suite runs go through `heavy uv run pytest`; a single quick test runs as
  plain `uv run pytest tests/io/test_foo.py::test_bar`. The standing environment
  is `uv sync --all-extras --group dev`; the `bench` group stays opt-in.
- `av`'s public surface leaks `Unknown` the way numpy's does. The existing
  `[tool.basedpyright]` global `reportUnknown* = false` and
  `reportMissingTypeStubs = false` already cover it; no per-file suppression is
  ever needed. Where a value read off an av object feeds a typed field, coerce
  it explicitly (`int(...)`, `float(...)`, `bool(...)`) so the annotation is
  honest.

## Interfaces consumed (pinned; import, do not re-create)

- `mosaic_media.probe.ffprobe`: `@dataclass(frozen=True, slots=True) class
  Packet` with `time: float`, `size: int`, `keyframe: bool`, `pos: int`;
  `TimestampSource = Literal["pts", "dts"]`. (`read_header` and `scan_packets`
  stay in the probe layer and remain the measurement scanner; the io layer stops
  importing them once its index seam moves in process.)
- `mosaic_media.probe.errors`: `class MediaProbeError(RuntimeError)`.
- `mosaic_media.probe.facts`: `@dataclass(frozen=True, slots=True) class
  MediaFacts` with `width: int`, `height: int`, `rotation_degrees: int`,
  `fps: float`, `frame_count: int`, `duration: float` (among other fields).
- `mosaic_media.probe.probe`: `probe_media(path: Path) -> MediaFacts`.
- `mosaic_media.probe.sequence`: `uniform_properties`,
  `MeasuredVideoProperties`, `PropertyMismatch`.
- `tests/helpers/media_fixtures.py`: the `clips` session fixture (keys include
  `cfr_mp4` (h264/mp4), `vp8_webm` (vp8/webm), `mjpeg_avi` (mjpeg/avi),
  `no_pts_avi`, `rotated_mp4`), and `build(destination, *arguments, source=...)`
  -- system ffmpeg is the ground-truth producer.
- `tests/helpers/corpus.py`: `generate_video(...)`, `decode_md5s(...)`,
  `frame_md5(...)`; session fixtures `corpus_gop12`, `corpus_gop250`.

## Interfaces produced (pinned; restated in full so this plan is self-contained)

The public io surface is unchanged. It is restated here rather than by reference
so the rewrite has the contract in front of it.

- `mosaic_media.io.VideoReader(path: Path | str, *, start_frame: int = 0,
  end_frame: int | None = None, frame_step: int = 1,
  resize: tuple[int, int] | None = None, grayscale: bool = False,
  hwaccel: bool = False, facts: MediaFacts | None = None,
  index: SeekIndex | None = None)`.
  - Properties `width: int`, `height: int`, `fps: float`, `frame_count: int`.
  - `read() -> tuple[bool, numpy.ndarray | None]`.
  - `read_batch(batch_size: int) -> tuple[numpy.ndarray, numpy.ndarray]`.
  - `read_frames(indices: Sequence[int]) -> Iterator[tuple[int, numpy.ndarray]]`
    (sorted unique targets, GOP-grouped).
  - `seek(frame_index: int) -> None`, `close() -> None`,
    `__iter__() -> Iterator[tuple[int, numpy.ndarray]]`, `__len__() -> int`,
    context manager.
- `mosaic_media.io.MultiVideoReader(video_paths: list[Path] | Path | str)` with
  properties `total_frames`, `fps`, `width`, `height`, `video_count`,
  `segments`, `frame_position`; methods
  `segment_for_frame(global_frame: int) -> tuple[int, int]`,
  `seek(global_frame: int) -> None`, `read() -> tuple[bool, numpy.ndarray |
  None]`, `close() -> None`, `__len__`; context manager. `VideoSegment` stays
  exported.
- `mosaic_media.io.FFmpegVideoWriter(output_path: Path | str, width: int,
  height: int, fps: float = 30.0, crf: int = 23, preset: str = "medium",
  hwaccel: bool = False)` with properties `output_path`, `width`, `height`,
  `fps`, `frames_written`; methods `write(frame: numpy.ndarray) -> None`,
  `close() -> None`; context manager. (The class name is retained even though
  the internals no longer shell out to ffmpeg -- it is a pinned public name.)
- `mosaic_media.io.SeekIndex` and
  `mosaic_media.io.build_seek_index(packets: tuple[Packet, ...]) -> SeekIndex`.

## Staffing note

The reader-rewrite task (Task 4) and the writer-rewrite task (Task 5) carry the
highest defect risk in this plan: they replace an entire process lifecycle with
a container lifecycle behind a pinned surface and a full correctness suite.
Assign the strongest implementer and the closest review to those two tasks.

---

## Task 0 -- the gate-threshold record

No measurement or code. This task records the authoritative per-workload
thresholds that Task 1's pin rationale and the gate-branch fold-in (final
section) consume. The numbers are the stabilization medians already measured for
the spec revision -- repeated serialized runs on this machine at one base
commit, under the `heavy` lock, on an otherwise idle machine (the stabilization
protocol; see the spec's "Gate policy and thresholds"). They are reproduced here
so the gate-branch amendment cites this plan, not a remembered number.

Tier CARVE (`>= 0.9`), owned-BGR structural copy (av reformats yuv into a bgr24
frame plus an ndarray copy out of it, ~0.4-0.5 ms/frame at 1080p, where cv2
converts into the returned array in one operation):

| Workload | Stabilization median | Gate |
| --- | --- | --- |
| sequential-full-decode[gop12] | 0.956 | >= 0.9 |
| sequential-full-decode[gop250] | 0.949 | >= 0.9 |
| seek-then-sequential[gop12] | 1.011 | >= 0.9 |
| seek-then-sequential[gop250] | 0.986 | >= 0.9 |

Tier GATE (`>= 1.0`):

| Workload | Stabilization median | Gate |
| --- | --- | --- |
| sequential-full-decode[rotation] | 1.242 | >= 1.0 |
| strided-decode-step5[gop12] | 1.275 | >= 1.0 |
| strided-decode-step5[gop250] | measured at gate (gop12 form stabilized 1.275) | >= 1.0 |
| monotonic-strided-seeks[gop12] | 2.132 | >= 1.0 |
| monotonic-strided-seeks[gop250] | 1.100 | >= 1.0 |
| sorted-sparse-extraction[gop12] | 1.660 | >= 1.0 |
| sorted-sparse-extraction[gop250] | 1.063 | >= 1.0 |
| multi-video-junction[gop12+gop12] | 1.032 | >= 1.0 |
| metadata-open[gop12], [gop250], [rotation] | 1.067 / 1.060 (approximations) | >= 1.0 |

Non-gating reports:

| Report | Stabilization median | Bound |
| --- | --- | --- |
| cold-random-seek[gop12] | 1.745 | non-gating, `<= 2x`; now parity-or-better |
| cold-random-seek[gop250] | 1.021 | non-gating, `<= 2x`; now parity-or-better |
| probe-cost[gop12], [gop250], [rotation] | report only | none |

Rules the fold-in applies (recorded here, applied there):

- **Rotation gates at `>= 1.0`, not the carve tier.** The reader rotates through
  a libav transpose filter graph (Task 4), which measured 1.242. `numpy.rot90`
  is refuted for rotation (0.396).
- **Cold-random-seek stays a non-gating report.** Its documented expectation
  tightens to parity-or-better (1.745 / 1.021) but keeps the `assert_bounded`
  2x form; the tightening is in the recorded expectation, not a weakened bound.
- **Thin-margin workloads run at `rounds=9`.** Any gated workload whose margin
  over its bound is under 10 percent (sorted-sparse-extraction[gop250] 1.063,
  multi-video-junction[gop12+gop12] 1.032, the metadata rows) runs at 9 rounds at gate time,
  with the stabilization median recorded next to the threshold so a failure is
  diagnosable as regression-versus-noise. Bounds are never weakened for noise.
- Nothing measured below 0.9, so no workload converts to a sub-0.9 non-gating
  bound and no migration-question issue is opened on current evidence.

**Acceptance:** the numbers above match the spec's "Gate policy and thresholds"
table exactly; the gate-branch fold-in cites this task.

---

## Task 1 -- add PyAV to the io extra

**Files:** `pyproject.toml`.

**Interfaces:** consumes nothing; produces the `av` dependency in the `[io]`
extra.

### Steps

- [ ] Add the pin to the `io` extra (numpy stays; nothing else changes):

  ```toml
  [project.optional-dependencies]
  io = ["numpy>=1.22", "av>=18,<19"]
  cli = ["typer>=0.12"]
  ```

  Pin rationale (record in the commit body): the spike and the empirical codec
  verifications were taken on av 18.0.0 (bundled libav 62.x). One wheel major:
  the upper bound keeps an untested wheel generation -- possibly with a
  reshuffled codec table and a new bundled libav major -- from arriving
  silently, and the codec guard, not the pin, is the protector inside the range.
  Widening the major is a deliberate act: bump, run the codec guard and the
  gate, commit.

- [ ] Extend the basedpyright leak comment so the next reader knows av is
  covered too:

  ```toml
  # numpy (the [io] extra), av (the [io] extra), and opencv-python (the bench
  # group) leak Any/Unknown through their public surfaces; that is not our
  # code's problem.
  ```

  Change only the comment; the `reportUnknown*` and `reportMissingTypeStubs`
  settings already cover av and need no edit.

- [ ] No change anywhere else: `dev` and `bench` groups untouched
  (`opencv-python` stays bench-only), `cli` extra untouched, core `dependencies`
  stay `[]`.

- [ ] Sync so av is importable. `uv sync` prunes the environment to exactly the
  named extras and groups, so a bare `uv sync --extra io` evicts the `cli` extra
  and the `bench` group that other tests import, breaking collection. Sync the
  full environment instead:

  ```bash
  uv sync --all-extras --group dev
  ```

  Expected: uv resolves and installs `av` alongside the existing extras and
  groups.

- [ ] Confirm av imports and the default suite is unchanged (no behavior change
  yet):

  ```bash
  uv run python -c "import av; print(av.__version__)"
  uv run pytest tests/ -q
  ```

  Expected: prints `18.x.y`; the suite passes exactly as before.

- [ ] Commit: `Add PyAV to the io extra`.

---

## Task 2 -- the codec guard test

New default-suite test module proving the installed av binary's codec table
covers what this stack produces and accepts. It **decodes one frame** per opened
format (open-only would not prove the codec table). It guards with
`pytest.importorskip("av")` so a core-only environment still passes the default
suite.

**Files:** `tests/io/test_codec_guard.py` (create).

**Interfaces:** consumes `av`, the `clips` fixture, and
`tests/helpers/media_fixtures.build`; produces no source. This is test-first and
lands before any io source imports av -- it uses `importorskip`, so it does not
trip the purity guard (which scans `src/` only).

### Steps

- [ ] Create `tests/io/test_codec_guard.py`:

  ```python
  """Codec guard: the installed av binary decodes and encodes this stack's codecs.

  A bundled decoder's codec table is curated and shifts between releases (PyAV
  v17 dropped libaom from its wheels). This test turns that table from a trusted
  property into a tested one: system ffmpeg -- the producer of record for every
  transcode -- encodes the codecs this stack writes, and av must decode a frame
  of each; av must round-trip its own h264 encode; and av must open and decode a
  frame from every container format the fixture corpus exercises. A failure means
  the installed av cannot serve this package's codec set; the remedy is to pin a
  different av release or build `av --no-binary av` against system libav.
  """

  from pathlib import Path

  import pytest

  from tests.helpers.media_fixtures import build

  av = pytest.importorskip("av")

  _REMEDY = (
      "the installed av binary cannot serve this codec; pin a different av "
      "release or build 'av --no-binary av' against system libav"
  )


  def _decode_one(path: Path) -> int:
      with av.open(str(path)) as container:
          stream = container.streams.video[0]
          for frame in container.decode(stream):
              return frame.width
      return 0


  def test_av_decodes_system_ffmpeg_h264(tmp_path: Path) -> None:
      clip = build(
          tmp_path / "h264.mp4", "-c:v", "libx264", "-pix_fmt", "yuv420p"
      )
      assert _decode_one(clip) > 0, _REMEDY


  def test_av_decodes_system_ffmpeg_av1(tmp_path: Path) -> None:
      clip = build(
          tmp_path / "av1.mp4", "-c:v", "libsvtav1", "-pix_fmt", "yuv420p"
      )
      assert _decode_one(clip) > 0, _REMEDY


  def test_av_round_trips_its_own_h264_encode(tmp_path: Path) -> None:
      import numpy

      output = tmp_path / "roundtrip.mp4"
      with av.open(str(output), mode="w") as container:
          stream = container.add_stream("libx264", rate=30)
          stream.width = 160
          stream.height = 120
          stream.pix_fmt = "yuv420p"
          for _ in range(5):
              frame = av.VideoFrame.from_ndarray(
                  numpy.zeros((120, 160, 3), dtype=numpy.uint8), format="bgr24"
              )
              for packet in stream.encode(frame):
                  container.mux(packet)
          for packet in stream.encode():
              container.mux(packet)
      assert _decode_one(output) > 0, _REMEDY


  def test_av_decodes_each_corpus_container(clips: dict[str, Path]) -> None:
      # mp4/h264, webm/vp8, avi/mjpeg -- the formats the fixture corpus and probe
      # fixtures exercise. The enumeration mirrors the corpus and grows with it.
      for key in ("cfr_mp4", "vp8_webm", "mjpeg_avi"):
          assert _decode_one(clips[key]) > 0, f"{key}: {_REMEDY}"
  ```

- [ ] Run the guard (av present after Task 1's sync; system ffmpeg 6.1.1 has
  libx264 and libsvtav1):

  ```bash
  uv run pytest tests/io/test_codec_guard.py -q
  ```

  Expected: `4 passed` (av 18.0.0 decodes h264, av1 via libdav1d, vp8, mjpeg,
  and encodes h264 via libx264).

- [ ] Format, lint, type-check the new file:

  ```bash
  uv run ruff format tests/io/test_codec_guard.py
  uv run ruff check tests/io/test_codec_guard.py
  uv run basedpyright tests/io/test_codec_guard.py
  ```

  Expected: clean; `0 errors, 0 warnings`.

- [ ] Commit: `Add the codec guard proving the decode path covers the stack's codecs`.

---

## Task 3 -- in-process packet scan, seek-index dedup, and guard sequencing

The first `import av` in `src/`. Per the layering guard, this task must move the
purity allowance and add the io-requires-av poisoned-import test in the **same
commit** as that first import -- otherwise `test_the_io_layer_imports_only_...`
fails the moment `packets.py` imports av. This task also rewires the reader's and
multi's index seams onto the new in-process scanner, so `import mosaic_media.io`
transitively pulls av and the io-requires-av test becomes meetable here.

**Files:**
- `src/mosaic_media/io/packets.py` (create).
- `src/mosaic_media/io/index.py` (edit: dedup).
- `src/mosaic_media/io/reader.py` (edit: `_ensure_index` seam only).
- `src/mosaic_media/io/multi.py` (edit: `_segment_index` seam only).
- `tests/io/test_packets.py` (create).
- `tests/io/test_index.py` (edit: add dedup unit tests, keep the rest).
- `tests/probe/test_purity.py` (edit: io allowed roots gain `av`).
- `tests/test_import_guard.py` (edit: add io-requires-av).
- `docs/issues/seek-index-counts-duplicate-timestamp-packets.md` (edit: closed).

**Interfaces:**
- Consumes `av`, `Packet`, `TimestampSource`.
- Produces `scan_packets_in_process(path: Path) -> tuple[tuple[Packet, ...],
  TimestampSource]`; the dedup `build_seek_index`.

**Verified av facts (av 18.0.0):** a packet carries `pts`, `dts`, `size`, `pos`,
`is_keyframe`, and `container.streams.video[0].time_base` is a `Fraction`. The
demuxer's trailing flush packet has `pts is None` and `size == 0`. On the
remuxed no-pts AVI, libavformat synthesizes pts, so the dts fallback engages more
rarely in process than in `scan_packets`; the two scanners need not agree
packet-for-packet on defective containers, and only the probe's scanner is
authoritative for measurement.

### Steps

- [ ] Write failing packet-scan tests. Create `tests/io/test_packets.py`:

  ```python
  from pathlib import Path

  from mosaic_media.io.packets import scan_packets_in_process


  def test_scan_returns_pts_ordered_packets_with_offsets(clips: dict[str, Path]) -> None:
      packets, source = scan_packets_in_process(clips["cfr_mp4"])
      assert source == "pts"
      assert len(packets) > 0
      assert all(packet.pos >= 0 for packet in packets)
      # No flush packet leaked in: every scanned packet has positive size.
      assert all(packet.size > 0 for packet in packets)
      assert packets[0].keyframe


  def test_scan_falls_back_to_dts_only_when_no_packet_has_pts(
      clips: dict[str, Path],
  ) -> None:
      # The remuxed AVI is the file the dts fallback exists for at the ffprobe
      # level. libavformat may synthesize pts in process, so assert the source is
      # one of the two and the scan is non-empty -- not that it is dts here.
      packets, source = scan_packets_in_process(clips["no_pts_avi"])
      assert source in ("pts", "dts")
      assert len(packets) > 0
  ```

- [ ] Run and observe failure:

  ```bash
  uv run pytest tests/io/test_packets.py -q
  ```

  Expected: `ModuleNotFoundError: No module named 'mosaic_media.io.packets'`.

- [ ] Create `src/mosaic_media/io/packets.py`:

  ```python
  """In-process packet scan for the io seek path. Requires av.

  Demux packets without decoding, mirroring probe.ffprobe.scan_packets so the
  seek index built here matches the probe's frame model: pts preferred, dts a
  whole-file fallback used only when no packet in the stream carries pts, times
  in float seconds via the stream time base, the demuxer's trailing flush packet
  (pts None, size 0) excluded. The probe's scan_packets stays the authoritative
  measurement scanner; this function feeds seeking only, and the two need not
  agree packet-for-packet on containers where libavformat synthesizes pts that
  ffprobe reports as absent.
  """

  from pathlib import Path

  import av

  from ..probe.ffprobe import Packet, TimestampSource


  def scan_packets_in_process(
      path: Path,
  ) -> tuple[tuple[Packet, ...], TimestampSource]:
      pts_packets: list[Packet] = []
      dts_packets: list[Packet] = []
      with av.open(str(path)) as container:
          stream = container.streams.video[0]
          time_base = stream.time_base
          for packet in container.demux(stream):
              if packet.size == 0:
                  # The demuxer's trailing flush packet: pts None, size 0.
                  continue
              keyframe = bool(packet.is_keyframe)
              position = int(packet.pos) if packet.pos is not None else -1
              size = int(packet.size)
              if packet.pts is not None:
                  seconds = float(packet.pts * time_base)
                  pts_packets.append(
                      Packet(time=seconds, size=size, keyframe=keyframe, pos=position)
                  )
              if packet.dts is not None:
                  seconds = float(packet.dts * time_base)
                  dts_packets.append(
                      Packet(time=seconds, size=size, keyframe=keyframe, pos=position)
                  )
      # Whole-file fallback, mirroring scan_packets: dts only when NO packet in
      # the stream carried pts. Never a per-packet mix.
      if pts_packets:
          source: TimestampSource = "pts"
          return tuple(pts_packets), source
      source = "dts"
      return tuple(dts_packets), source
  ```

- [ ] Run and observe pass:

  ```bash
  uv run pytest tests/io/test_packets.py -q
  ```

  Expected: `2 passed`.

- [ ] Write failing dedup unit tests. Append to `tests/io/test_index.py` (keep
  every existing test; they still pass because dedup is a no-op on distinct
  timestamps):

  ```python
  def test_duplicate_presentation_timestamps_collapse_to_one_frame() -> None:
      # An invisible alternate-reference packet shares a presentation timestamp
      # with the visible frame at that time (the VP8/VP9 alt-ref case). The index
      # must count distinct timestamps, matching measure_timing and MediaFacts.
      packets = (
          Packet(time=0.0, size=1000, keyframe=True, pos=0),
          Packet(time=0.0, size=20, keyframe=False, pos=1000),  # alt-ref duplicate
          Packet(time=0.04, size=100, keyframe=False, pos=1020),
          Packet(time=0.08, size=100, keyframe=False, pos=1120),
      )
      index = build_seek_index(packets)
      assert index.frame_count == 3  # not 4
      assert index.frame_times == (0.0, 0.04, 0.08)
      assert index.keyframe_indices == (0,)


  def test_dedup_matches_measure_timing_distinct_timestamp_count() -> None:
      # measure_timing counts frames as len(sorted({packet.time ...})); the index
      # must agree, so SeekIndex.frame_count == MediaFacts.frame_count.
      packets = (
          Packet(time=0.0, size=100, keyframe=True, pos=0),
          Packet(time=0.0, size=10, keyframe=False, pos=100),
          Packet(time=0.04, size=100, keyframe=False, pos=110),
          Packet(time=0.04, size=10, keyframe=False, pos=210),
          Packet(time=0.08, size=100, keyframe=True, pos=220),
      )
      distinct = len({packet.time for packet in packets})
      assert build_seek_index(packets).frame_count == distinct


  def test_a_distinct_timestamp_is_a_keyframe_when_any_packet_at_it_is() -> None:
      packets = (
          Packet(time=0.0, size=100, keyframe=False, pos=0),
          Packet(time=0.0, size=500, keyframe=True, pos=100),  # keyframe shares t=0
          Packet(time=0.04, size=100, keyframe=False, pos=600),
      )
      index = build_seek_index(packets)
      assert index.keyframe_indices == (0,)
  ```

- [ ] Run and observe the new tests fail:

  ```bash
  uv run pytest tests/io/test_index.py -q
  ```

  Expected: the three new tests fail (current `build_seek_index` counts packets,
  not distinct timestamps), the existing index tests pass.

- [ ] Replace `build_seek_index` in `src/mosaic_media/io/index.py` with the
  deduplicating form, and record the dedup rule in the module docstring:

  ```python
  def build_seek_index(packets: tuple[Packet, ...]) -> SeekIndex:
      """Build a SeekIndex from a packet scan, deduplicating presentation
      timestamps consistently with measure_timing (sorted({packet.time ...})).

      A container can carry several packets at one presentation timestamp -- an
      invisible VP8/VP9 alternate-reference packet shares its visible frame's
      timestamp. The frame model counts distinct timestamps, so frame_times is
      the sorted set of packet times and frame_count equals MediaFacts.frame_count
      by construction. A distinct timestamp is a keyframe timestamp when any
      packet bearing it is keyframe-flagged, so a preceding duplicate can no
      longer shift a keyframe's rank.
      """
      keyframe_times = {packet.time for packet in packets if packet.keyframe}
      frame_times = tuple(sorted({packet.time for packet in packets}))
      keyframe_indices = tuple(
          rank for rank, time in enumerate(frame_times) if time in keyframe_times
      )
      return SeekIndex(frame_times=frame_times, keyframe_indices=keyframe_indices)
  ```

- [ ] Rewire the reader's index seam only. In
  `src/mosaic_media/io/reader.py`, replace the `read_header` + `scan_packets`
  pair inside `_ensure_index` with the in-process scan, and update the imports
  (drop `scan_packets`; `read_header` stays for now -- Task 4 removes it):

  ```python
  from .packets import scan_packets_in_process

  # ... inside VideoReader:
  def _ensure_index(self) -> SeekIndex:
      if self._index is None:
          packets, _source = scan_packets_in_process(self._path)
          self._index = build_seek_index(packets)
      return self._index
  ```

- [ ] Rewire multi's index seam only. In `src/mosaic_media/io/multi.py`,
  replace `read_header` + `scan_packets` inside `_segment_index` with
  `scan_packets_in_process`, and drop the now-unused `read_header`,
  `scan_packets` imports (keep `probe_media`):

  ```python
  from .packets import scan_packets_in_process

  # ... inside MultiVideoReader:
  def _segment_index(self, segment_index: int) -> SeekIndex:
      cached = self._indices[segment_index]
      if cached is not None:
          return cached
      packets, _source = scan_packets_in_process(self._segments[segment_index].path)
      built = build_seek_index(packets)
      self._indices[segment_index] = built
      return built
  ```

- [ ] Move the purity allowance in the same commit as the first av import.
  In `tests/probe/test_purity.py`, add `"av"` to the io layer's allowed roots
  and rename the test to match:

  ```python
  def test_the_io_layer_imports_only_the_standard_library_numpy_and_av() -> None:
      modules = numpy_layer_files()
      assert modules, "io layer modules not found"
      allowed = sys.stdlib_module_names | {"numpy", "av"}
      offenders: dict[str, set[str]] = {}
      for module in modules:
          outside = imported_roots(module.read_text()) - allowed
          if outside:
              offenders[module.name] = outside
      message = (
          "the io layer must import only the standard library, numpy, and av, "
          f"found: {offenders}"
      )
      assert offenders == {}, message
  ```

- [ ] Add the io-requires-av poisoned-import test in
  `tests/test_import_guard.py`, mirroring `test_io_subpackage_requires_numpy`:

  ```python
  def test_io_subpackage_requires_av() -> None:
      result = _run_guarded(
          "import mosaic_media.io\nraise SystemExit('io imported without av')\n",
          forbidden_root="av",
      )
      # Importing mosaic_media.io must fail because av is poisoned; the SystemExit
      # sentinel must never be reached. av is as mandatory to io as numpy -- one
      # decode stack, no half-alive import mode whose reader cannot open anything.
      assert result.returncode != 0
      assert "io imported without av" not in (result.stdout + result.stderr)
      assert "av" in (result.stdout + result.stderr).lower()
  ```

- [ ] Close the duplicate-timestamp issue. Append a `## Resolution` section to
  `docs/issues/seek-index-counts-duplicate-timestamp-packets.md` (the file is
  renamed to `.closed.md` and its `_INDEX.md` row flipped to `closed` at the
  clean-branch completion, not here):

  > ## Resolution (closed 2026-07-17)
  >
  > `build_seek_index` now deduplicates presentation timestamps -- `frame_times`
  > is `tuple(sorted({packet.time ...}))` and a distinct timestamp is a keyframe
  > timestamp when any packet bearing it is keyframe-flagged -- so
  > `SeekIndex.frame_count` equals `MediaFacts.frame_count` by construction, the
  > same distinct-timestamp count `measure_timing` uses. Under the in-process
  > PTS-exact seek the reader compares decoded frame timestamps against the
  > target, so packet identity is non-load-bearing: only the
  > frame-index-to-distinct-timestamp map matters, and the deduplication is
  > exactly that map. A preceding duplicate can no longer shift a keyframe's
  > rank.
  >
  > The evidence is constructed-packet unit tests, not a generated clip. Five
  > attempts to synthesize a duplicate-presentation-timestamp container with the
  > local toolchain (ffmpeg 6.1.1, libvpx `-auto-alt-ref 1`) produced zero
  > duplicate-pts packets: ffmpeg's libvpx path coalesces the invisible
  > alternate-reference frame into the visible packet, so a synthetic container
  > cannot exercise the defect. The real recording with 533 duplicate-timestamp
  > packets (recorded in `tests/probe/test_timing.py`) came from an external
  > screen recorder; the defect class arrives only from external producers, never
  > from this stack's own encoders. The unit tests therefore build the duplicate
  > packets directly -- asserting the dedup is consistent with `measure_timing`
  > and that `SeekIndex.frame_count` matches the distinct-timestamp count -- which
  > is the honest and sufficient closure. Production reads still go through
  > transcoded H.264/AV1, one packet per presentation timestamp, and never hit
  > the path.

- [ ] Run the affected suites and observe green:

  ```bash
  uv run pytest tests/io/test_index.py tests/io/test_packets.py tests/probe/test_purity.py tests/test_import_guard.py -q
  ```

  Expected: all pass, including the three new dedup tests, the two packet-scan
  tests, the renamed purity test, and `test_io_subpackage_requires_av`.

- [ ] Format, lint, type-check the changed source and tests:

  ```bash
  uv run ruff format src/ tests/
  uv run ruff check src/ tests/
  uv run basedpyright src/mosaic_media/io/ tests/io/ tests/probe/test_purity.py tests/test_import_guard.py
  ```

  Expected: clean; `0 errors, 0 warnings`.

- [ ] Commit: `Scan packets and build the seek index in process, deduplicating timestamps`.

---

## Task 4 -- reader rewrite (highest risk)

Replace `VideoReader`'s subprocess machinery (`_spawn`, `_select_expression`,
pipe sizing, `readinto` loops, `_reap_after_eof`, the `ffmpeg_available` /
`nvdec_available` / `read_header` imports) with container lifecycle, preserving
the full pinned surface and semantics. The existing io suites are the
integration oracle: they must pass with zero assertion changes, except the one
test whose premise the rewrite invalidates, which is rewritten (below). Each
snippet's av API was confirmed against av 18.0.0.

**Files:**
- `src/mosaic_media/io/reader.py` (rewrite internals).
- `tests/io/test_reader_errors.py` (rewrite the failed-init test only).
- `tests/io/test_reader_rotation.py` (create: the bit-exact rotation-graph unit
  test, RED first).

**Interfaces:** consumes `av`, `MediaFacts`, `MediaProbeError`, `SeekIndex`,
`build_seek_index`, `scan_packets_in_process`. Produces the pinned `VideoReader`.

**Decisions, each recorded in the module:**

- **Module docstring** replaces the moviepy/imageio-ffmpeg subprocess attribution
  with the in-process libav description:

  ```python
  """Frame reading through in-process libav bindings (PyAV). Requires numpy and av.

  The reader decodes in an open av container: sequential reads decode forward;
  a seek resolves the target's preceding keyframe from the packet index, calls
  container.seek to that keyframe's presentation timestamp with backward
  resolution, and decodes forward until the target frame's timestamp is reached,
  landing frame-exact. That removes OpenCV's off-by-N CAP_PROP_POS_FRAMES class
  of bugs by construction, and the codec table is a tested invariant (the codec
  guard), not a trusted bundled binary. Rotation is applied in process through a
  libav transpose filter graph, bit-exact against system-ffmpeg autorotation.
  """
  ```

- **Lifecycle.** `av.open(str(self._path))` is lazy -- on the first read, seek,
  or metadata need. This is required by the truncated-file test, which constructs
  the reader outside its `pytest.raises` block and expects the raise during
  decode. `__init__` sets `self._closed` and `self._container` first so
  `close()`/`__del__` are safe if a later line raises. `close()` closes the
  container; context-manager, `__del__`, read-after-close, and seek-after-close
  semantics are unchanged (`seek`/`read_frames` on a closed reader raise
  `MediaProbeError` before opening anything). Open, seek, and decode are wrapped
  so `av.error.FFmpegError` (the base of av's exception hierarchy; a truncated
  mp4 raises `av.error.InvalidDataError`, a subclass) becomes `MediaProbeError`
  with the path in the message:

  ```python
  def _open(self) -> None:
      try:
          container = av.open(str(self._path))
      except av.error.FFmpegError as exc:
          message = f"failed to open {self._path}: {exc}"
          raise MediaProbeError(message) from exc
      stream = container.streams.video[0]
      stream.thread_type = "AUTO"  # frame threading; the decode loop drains on EOF
      self._container = container
      self._stream = stream
  ```

- **Metadata.** Injected `facts` stay authoritative and suppress all probing;
  rotation comes from `facts.rotation_degrees`. Without facts, declared width,
  height, and rate come from the av stream (`stream.width`, `stream.height`,
  `float(stream.average_rate)`), the frame count from `stream.frames` when
  positive else the in-process index length, and rotation from a single-frame
  peek -- the av stream exposes no rotation getter before decode, so the
  no-facts path opens a short-lived container, decodes one frame, reads
  `frame.rotation`, and closes it:

  ```python
  def _probe_rotation(self) -> int:
      with av.open(str(self._path)) as container:
          stream = container.streams.video[0]
          for frame in container.decode(stream):
              return int(frame.rotation)
      return 0
  ```

  The geometry rule is unchanged from the current reader: a `resize` sets the
  output dimensions directly; otherwise a quarter-turn (`rotation_degrees %
  180 == 90`) swaps coded width and height for the displayed orientation.

- **Rotation.** PyAV does not autorotate (a display-matrix clip decodes at coded
  orientation). The reader applies the rotation itself through a libav transpose
  filter graph, built once per reader and reused. Verified bit-exact against
  system-ffmpeg CLI autorotation for the corpus's 90-degree clip
  (`transpose=cclock`); `numpy.rot90` is refuted for rotation on performance
  (0.396). The graph is built from the stream template:

  ```python
  # transpose direction per display rotation. 90 (cclock) is corpus-verified
  # bit-exact; the other quarter-turns use the analogous transpose, covered by
  # the framemd5 suite when a fixture of that rotation exists.
  _TRANSPOSE_BY_ROTATION: dict[int, str] = {90: "cclock", 270: "clock"}

  def _build_rotation_graph(self) -> None:
      direction = _TRANSPOSE_BY_ROTATION[self._rotation_degrees % 360]
      graph = av.filter.Graph()
      buffer = graph.add_buffer(template=self._stream)
      transpose = graph.add("transpose", direction)
      sink = graph.add("buffersink")
      buffer.link_to(transpose)
      transpose.link_to(sink)
      graph.configure()
      self._rotation_graph = graph

  def _rotate(self, frame: object) -> object:
      # Push the decoded frame through the transpose graph; pull the rotated one.
      self._rotation_graph.push(frame)
      return self._rotation_graph.pull()
  ```

  A 180-degree rotation composes two transposes or `vflip`+`hflip`; add it to the
  mapping when a 180 fixture lands. A rotation the mapping does not cover raises a
  clear `MediaProbeError` rather than emitting a wrong orientation.

- **Frame emission.** `frame.to_ndarray(format="bgr24")`, or `"gray"` when
  grayscale. Verified: the array is writable, C-contiguous, uint8, and does not
  alias the next frame; numpy `OWNDATA` is false by design (the array wraps the
  reformatted frame's own buffer), so no `.copy()` is added -- the pinned tests
  assert writability and non-aliasing, not the flag. When the reader is rotated,
  rotate the frame through the graph before `to_ndarray`. Resize continues to
  produce exactly the requested dimensions after rotation. Verified bit-exact
  against system-ffmpeg framemd5 for both `bgr24` and `gray`.

- **Sequential and windowed reads.** Decode forward over
  `container.decode(stream)` (frames arrive in presentation order), keeping a
  running presentation index; apply `start_frame`, `end_frame`, and `frame_step`
  as Python-side accounting (the CLI `-vf select` also decoded every frame; only
  the drop point moves). A reader windowed with `start_frame > 0` positions its
  first read through the seek path rather than decoding the prefix.

- **Seeking.** Resolve the target's preceding keyframe from the index, seek to
  its presentation timestamp with backward resolution, then decode forward
  comparing frame timestamps until the target is reached; reuse the open decode
  position when it already lies between that keyframe and the target
  (`group_by_gop` keeps driving `read_frames` batching). The seek offset is in
  the stream time base:

  ```python
  def seek(self, frame_index: int) -> None:
      if self._closed:
          message = "reader is closed"
          raise MediaProbeError(message)
      geometry = self._ensure_ready()
      target = int(frame_index)
      window_end = self._window_end(geometry)
      if target < self._start_frame or target >= window_end:
          message = (
              f"frame index {target} out of range "
              f"[{self._start_frame}, {window_end})"
          )
          raise IndexError(message)
      index = self._ensure_index()
      keyframe_index, keyframe_time = index.preceding_keyframe(target)
      reusable = (
          self._container is not None
          and self._mode == "positioned"
          and keyframe_index <= self._decoder_pos <= target
      )
      if not reusable:
          self._ensure_container()
          offset = int(round(keyframe_time / float(self._stream.time_base)))
          try:
              self._container.seek(offset, stream=self._stream, backward=True)
          except av.error.FFmpegError as exc:
              message = f"failed to seek {self._path} to frame {target}: {exc}"
              raise MediaProbeError(message) from exc
          self._decode_iterator = self._container.decode(self._stream)
          self._decoder_pos = keyframe_index
      self._mode = "positioned"
      self._target = target
  ```

  Frame times come from `frame.time` (or `frame.pts * time_base`), not
  arithmetic, wherever pts is available; the decode-forward loop advances
  `self._decoder_pos` until it reaches `self._target`.

- **Threading and EOF.** `stream.thread_type = "AUTO"` (the spike
  configuration). Frame threading buffers frames inside the decoder; iterating
  `container.decode(stream)` to exhaustion drains them, so tail frames are not
  lost. A decode that raises `av.error.FFmpegError` mid-stream (truncated file)
  maps to `MediaProbeError` -- preserving the current reader's truncated-file
  semantics exactly.

- **hwaccel.** `hwaccel=True` is a documented no-op: software decode always.
  Docstring for the parameter:

  ```python
  # hwaccel is retained for signature compatibility and is a no-op: decode is
  # always software. No consumer requests hardware decode today, and the GPU
  # download path's bit-exactness against the framemd5 goldens is unverified.
  # The implementation seam if that changes is av.codec.hwaccel.HWAccel("cuda").
  ```

  `nvdec_available` stays in core untouched for the transcode side; the reader no
  longer imports it.

### Steps

- [ ] Write the RED rotation-graph unit test. Create
  `tests/io/test_reader_rotation.py`:

  ```python
  from pathlib import Path

  from mosaic_media.io.reader import VideoReader
  from tests.helpers.corpus import decode_md5s, frame_md5, generate_video


  def test_rotation_is_bit_exact_against_ffmpeg_autorotation(tmp_path: Path) -> None:
      # The reader's in-process transpose graph must match system-ffmpeg's
      # autorotated output frame-for-frame, so the displayed-orientation contract
      # and the framemd5 goldens hold with no cv2 in the loop.
      path = generate_video(
          tmp_path / "rot.mp4", frames=24, fps=30.0, gop=12, rotation_degrees=90
      )
      goldens = decode_md5s(path)
      produced: list[str] = []
      with VideoReader(path) as reader:
          assert reader.width == 240
          assert reader.height == 320
          for _index, frame in reader:
              assert frame.shape == (320, 240, 3)
              produced.append(frame_md5(frame))
      assert produced == goldens
  ```

- [ ] Rewrite the failed-init test to the new invariant (the `ffmpeg_available`
  seam the current test monkeypatches is removed with the subprocess). In
  `tests/io/test_reader_errors.py`, replace `test_del_after_failed_init_does_not_raise`
  with a version that forces an `__init__` failure through a still-present seam
  and asserts the same no-AttributeError-on-finalization invariant:

  ```python
  def test_del_after_failed_init_does_not_raise(
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      # When __init__ raises before finishing, finalization must not add
      # AttributeError noise from close() touching an unset attribute. Force a
      # construction failure through a seam __init__ still calls (path
      # resolution) and capture anything the garbage collector reports.
      def _boom(_self: object) -> Path:
          raise RuntimeError("forced init failure")

      monkeypatch.setattr(Path, "expanduser", _boom)
      unraisable: list[object] = []
      monkeypatch.setattr(
          sys, "unraisablehook", lambda hook_args: unraisable.append(hook_args)
      )
      with pytest.raises(RuntimeError):
          _ = VideoReader("nonexistent.mp4")
      _ = gc.collect()
      assert not any(
          isinstance(getattr(entry, "exc_value", None), AttributeError)
          for entry in unraisable
      )
  ```

  Keep every other test in the file unchanged; the truncated-file and
  after-close tests already assert the preserved semantics.

- [ ] Run the two new/rewritten tests and observe failure (the reader still
  spawns ffmpeg and does not rotate in process):

  ```bash
  uv run pytest tests/io/test_reader_rotation.py tests/io/test_reader_errors.py::test_del_after_failed_init_does_not_raise -q
  ```

  Expected: both fail (rotation not yet in-process; `ffmpeg_available` still
  present so the old test's premise differs).

- [ ] Rewrite `src/mosaic_media/io/reader.py` internals per the decisions above.
  Keep the constructor signature, the `_Geometry` dataclass, the `_ReaderMode`
  Literal, `_window_end`, the properties, `read`, `read_batch`, `seek`,
  `read_frames`, `__iter__`, `close`, `__enter__`/`__exit__`/`__del__`,
  `__len__`, and the exact `IndexError`/`MediaProbeError` messages. Replace the
  process methods with container methods; drop the `fcntl`, `io`, `subprocess`,
  `ffmpeg_available`, `nvdec_available`, and `read_header` imports; add
  `import av` and the `scan_packets_in_process` import.

- [ ] Run the full reader suite and observe green with zero assertion changes:

  ```bash
  uv run pytest tests/io/test_reader_sequential.py tests/io/test_reader_strided.py tests/io/test_reader_seek.py tests/io/test_reader_sparse.py tests/io/test_reader_cv2_equality.py tests/io/test_reader_errors.py tests/io/test_reader_rotation.py -q
  ```

  Expected: all pass -- framemd5 sequential/strided/seek/sparse, cv2-equality,
  error and close semantics, and the bit-exact rotation test.

- [ ] Type-check and lint the reader:

  ```bash
  uv run ruff format src/mosaic_media/io/reader.py tests/io/test_reader_rotation.py tests/io/test_reader_errors.py
  uv run ruff check src/mosaic_media/io/reader.py
  uv run basedpyright src/mosaic_media/io/reader.py
  ```

  Expected: clean; `0 errors, 0 warnings`, no suppression.

- [ ] Commit: `Decode frames in process through libav`.

---

## Task 5 -- writer rewrite

Replace `FFmpegVideoWriter`'s subprocess with `av.open(path, "w")`, an h264
stream, `av.VideoFrame.from_ndarray`, encode-and-mux per write, flush and trailer
on close. Preserve the pinned surface, the shape/dtype validation and its exact
message, and loud failure on encode/open error. Add the av-side NVENC usability
probe and half-close the encoder-gate issue.

**Files:**
- `src/mosaic_media/io/writer.py` (rewrite internals).
- `tests/io/test_writer.py` (add the usability-probe fallback unit test; keep
  the rest unchanged).
- `docs/issues/encoder-gate-checks-listing-not-usability.md` (edit: writer half
  resolved, transcode half re-scoped).

**Interfaces:** consumes `av`, `MediaProbeError`, numpy. Produces the pinned
`FFmpegVideoWriter`. Drops the `hwaccel` (`ffmpeg_available`,
`encoder_available`) imports.

**Verified av facts:** `av.open(path, "w")` on an extension it cannot map to a
muxer raises a **builtin `ValueError`** ("Could not determine output format")
before libav is engaged, while runtime encode/mux failures surface as
`av.error.FFmpegError`; the open mapping catches both. On this GPU-less machine,
`av.codec.CodecContext.create("h264_nvenc", "w").open()` raises
`av.error.PermissionError` (an `FFmpegError` subclass) even though the wheel
lists the encoder -- exactly the listing-versus-usability gap.

### Steps

- [ ] Add the usability-probe fallback unit test to `tests/io/test_writer.py`
  (RED first). It monkeypatches the probe to report the device unusable and
  asserts the writer selects libx264:

  ```python
  def test_hardware_encode_falls_back_when_device_unusable(
      tmp_path: Path, monkeypatch: pytest.MonkeyPatch
  ) -> None:
      # With permission granted but the device unusable, the writer must fall back
      # to libx264 rather than select an NVENC encoder that fails at startup.
      monkeypatch.setattr(
          "mosaic_media.io.writer._nvenc_encoder_usable", lambda: False
      )
      output = tmp_path / "fallback.mp4"
      with FFmpegVideoWriter(output, 320, 240, fps=30.0, hwaccel=True) as writer:
          assert writer.encoder_name == "libx264"
  ```

  Expose a read-only `encoder_name` property on the writer for this assertion
  (the selected codec name; it is derived state, not a new behavior).

- [ ] Run and observe failure:

  ```bash
  uv run pytest tests/io/test_writer.py::test_hardware_encode_falls_back_when_device_unusable -q
  ```

  Expected: fails (`_nvenc_encoder_usable` and `encoder_name` do not exist yet).

- [ ] Rewrite `src/mosaic_media/io/writer.py`. Module docstring, the cached
  usability probe, and the encode path:

  ```python
  """BGR-frame video writer through in-process libav bindings (PyAV). Requires numpy and av.

  Raw bgr24 frames are fed to av's libx264 encoder, or to h264_nvenc when the
  caller permits hardware AND a cached usability probe confirms the device
  actually encodes -- listing an encoder is not proof it runs. Output stays
  mp4/h264/yuv420p. Shape and dtype are validated per write; an open failure, an
  unwritable format, or an encode error surfaces as MediaProbeError rather than a
  silently incremented frame count.
  """

  from fractions import Fraction

  import av
  import numpy

  from ..probe.errors import MediaProbeError

  _nvenc_usable_cache: bool | None = None


  def _nvenc_encoder_usable() -> bool:
      """Whether h264_nvenc actually opens on this machine. Cached: the probe
      constructs and opens a tiny encoder context once; a GPU-less machine raises
      even though the wheel lists the encoder."""
      global _nvenc_usable_cache
      if _nvenc_usable_cache is None:
          try:
              context = av.codec.CodecContext.create("h264_nvenc", "w")
              context.width = 16
              context.height = 16
              context.pix_fmt = "yuv420p"
              context.time_base = Fraction(1, 30)
              context.open()
              context.close()
              _nvenc_usable_cache = True
          except av.error.FFmpegError:
              _nvenc_usable_cache = False
      return _nvenc_usable_cache
  ```

  `__init__` opens the container (mapping the builtin `ValueError` and any
  `FFmpegError`), selects the encoder, and sets `encoder_name`:

  ```python
  self._closed: bool = False
  self._container: "av.container.OutputContainer | None" = None
  self._output_path: Path = Path(output_path).expanduser().resolve()
  self._output_path.parent.mkdir(parents=True, exist_ok=True)
  self._width: int = width
  self._height: int = height
  self._fps: float = fps
  self._frames_written: int = 0
  self._encoder_name: str = (
      "h264_nvenc" if (hwaccel and _nvenc_encoder_usable()) else "libx264"
  )
  try:
      container = av.open(str(self._output_path), mode="w")
  except (ValueError, av.error.FFmpegError) as exc:
      # av reports an unmappable output format as a builtin ValueError before
      # libav is engaged; runtime failures come through FFmpegError.
      message = f"failed to open {self._output_path} for writing: {exc}"
      raise MediaProbeError(message) from exc
  stream = container.add_stream(self._encoder_name, rate=fps)
  stream.width = width
  stream.height = height
  stream.pix_fmt = "yuv420p"
  if self._encoder_name == "libx264":
      stream.options = {"preset": preset, "crf": str(crf)}
  else:
      stream.options = {"preset": preset, "cq": str(crf)}
  self._container = container
  self._stream = stream
  ```

  `write` validates then encodes and muxes; `close` flushes and closes; both map
  `FFmpegError` to `MediaProbeError`:

  ```python
  def write(self, frame: numpy.ndarray) -> None:
      if self._closed:
          message = "writer is closed"
          raise MediaProbeError(message)
      expected_shape = (self._height, self._width, 3)
      if frame.shape != expected_shape or frame.dtype != numpy.uint8:
          message = (
              f"frame shape {tuple(frame.shape)} dtype {frame.dtype} does not "
              f"match writer geometry {expected_shape} dtype uint8"
          )
          raise MediaProbeError(message)
      video_frame = av.VideoFrame.from_ndarray(frame, format="bgr24")
      try:
          for packet in self._stream.encode(video_frame):
              self._container.mux(packet)
      except av.error.FFmpegError as exc:
          message = f"failed to encode a frame to {self._output_path}: {exc}"
          raise MediaProbeError(message) from exc
      self._frames_written += 1
  ```

  Keep the pinned properties, and add `encoder_name`. The `preset`/`crf`
  arguments keep their names and defaults.

- [ ] Update the startup-failure test's mechanism if needed: the existing
  `test_writer_surfaces_ffmpeg_startup_failure` opens `out.unknownext` and wraps
  construction, writes, and close in `pytest.raises(MediaProbeError)`. Under av
  the open raises at construction (builtin `ValueError` mapped to
  `MediaProbeError`), which is inside the `pytest.raises` block -- the test
  passes unchanged. Do not weaken it.

- [ ] Half-close the encoder-gate issue. In
  `docs/issues/encoder-gate-checks-listing-not-usability.md`, append an update
  marking the writer half done and re-scoping the remaining half (the file stays
  `active` and tracked -- the transcode half is open):

  > ## Update (2026-07-17): writer half resolved; transcode half re-scoped
  >
  > The writer now probes usability idiomatically: it constructs an `h264_nvenc`
  > `av.codec.CodecContext` and opens it once (cached); on a GPU-less machine the
  > open raises `av.error.PermissionError` even though the wheel lists the
  > encoder, so the writer gates hardware encode on caller permission AND that
  > probe, falling back to libx264. The writer half of this issue is done.
  >
  > The remaining half is the transcode command builder
  > (`transcode/commands.py`, `allow_hardware and
  > encoder_available("av1_nvenc")`), which stays open and re-scoped: that
  > consumer runs system ffmpeg as a subprocess, where the in-process av probe
  > does not answer for the CLI binary's device access. The one-policy-for-both
  > premise dissolved with the architectural split -- the writer encodes in
  > process, the converter shells out. The fix there is a system-ffmpeg
  > usability probe in `hwaccel.py` (a cached null encode via
  > `-init_hw_device cuda`, mirroring the `nvdec_available` fix), gating
  > `av1_nvenc` on permission AND that probe with a `libsvtav1` fallback. Scope
  > now: `transcode/commands.py` and `hwaccel.py` only.

- [ ] Run the writer suite and observe green (unmodified assertions plus the new
  fallback test):

  ```bash
  uv run pytest tests/io/test_writer.py -q
  ```

  Expected: all pass, including the roundtrip, the shape/dtype rejections, the
  startup-failure mapping, and the usability-probe fallback.

- [ ] Type-check and lint:

  ```bash
  uv run ruff format src/mosaic_media/io/writer.py tests/io/test_writer.py
  uv run ruff check src/mosaic_media/io/writer.py
  uv run basedpyright src/mosaic_media/io/writer.py
  ```

  Expected: clean; `0 errors, 0 warnings`.

- [ ] Commit: `Encode through libav with a hardware-usability probe`.

---

## Task 6 -- multi reader

`MultiVideoReader`'s logic is nearly untouched: per-segment `VideoReader`s are
now per-segment av containers by construction (Task 4), and the index seam
already moved to `scan_packets_in_process` in Task 3. This task confirms the
whole multi suite is green on the rewritten stack and verifies nothing else in
`multi.py` still reaches for the probe's packet scanner or a subprocess.

**Files:** `src/mosaic_media/io/multi.py` (verify; edit only if a stray
`read_header`/`scan_packets`/`subprocess` reference remains).

**Interfaces:** consumes `probe_media` (the sanctioned io-to-probe boundary --
each file is probed once for authoritative `MediaFacts`, measurement is not
re-derived), `scan_packets_in_process`, `VideoReader`, `uniform_properties`.

### Steps

- [ ] Confirm `multi.py` imports: `probe_media` stays; `read_header` and
  `scan_packets` are gone (removed in Task 3); no `subprocess` import. The
  facts/index injection into per-segment readers is verbatim, and
  `_displayed_dimensions` and the displayed-orientation uniformity check are
  unchanged -- they now describe what the reader itself produces via its
  rotation handling.

- [ ] Run the multi suite:

  ```bash
  uv run pytest tests/io/test_multi.py -q
  ```

  Expected: all pass, including the junction-crossing read and the
  rotation-mismatch rejection at construction.

- [ ] Type-check and lint if edited:

  ```bash
  uv run ruff check src/mosaic_media/io/multi.py
  uv run basedpyright src/mosaic_media/io/multi.py
  ```

  Expected: clean.

- [ ] Commit (skip if the suite passed with no source edit):
  `Read multi-video segments through in-process containers`.

---

## Task 7 -- final layering guards

The io-requires-av positive test and the io purity allowance already landed in
Task 3. This task adds the remaining one-way-direction checks -- the core and cli
must import with `av` poisoned (av must never leak downward) -- and verifies a
deliberate break trips the matching guard.

**Files:** `tests/test_import_guard.py` (add core/cli-without-av).

### Steps

- [ ] Add the downward-direction guards to `tests/test_import_guard.py`:

  ```python
  def test_the_core_imports_without_av() -> None:
      result = _run_guarded(_CORE_IMPORTS, forbidden_root="av")
      assert result.returncode == 0, result.stderr


  def test_the_cli_imports_without_av() -> None:
      result = _run_guarded("import mosaic_media.cli", forbidden_root="av")
      assert result.returncode == 0, result.stderr
  ```

- [ ] Run the guard suites:

  ```bash
  uv run pytest tests/test_import_guard.py tests/probe/test_purity.py -q
  ```

  Expected: all pass -- core and cli import with av poisoned; io fails with av
  poisoned; the io purity allowance includes av.

- [ ] Verify a deliberate break trips a guard (scratch check, not committed):
  temporarily add `import av` to a core module (for example
  `src/mosaic_media/probe/gop.py`), run
  `uv run pytest tests/probe/test_purity.py::test_the_core_imports_only_the_standard_library tests/test_import_guard.py::test_the_core_imports_without_av -q`,
  confirm both fail, then revert the edit and confirm they pass again. Leave the
  tree clean.

- [ ] Commit: `Guard the io layer's libav dependency and one-way layering`.

---

## Task 8 -- no io module spawns an ffmpeg subprocess

After Tasks 4-6 no io module imports `subprocess` or constructs an ffmpeg/ffprobe
command. Commit this as a small purity-style test so the property survives future
edits, not a one-time review step. The sanctioned boundary, stated in the io
package docstring: io modules may call probe-layer public functions
(`multi.py` calls `probe_media`), and the probe layer owns its ffprobe subprocess
as the measurement authority. Corpus and test helpers (`tests/helpers/`, the
framemd5 ground truth) keep their system-ffmpeg generation -- they are
ground-truth producers, deliberately a different binary from the decoder under
test.

**Files:** `tests/probe/test_purity.py` (add the io-no-subprocess guard).

### Steps

- [ ] Add the committed guard to `tests/probe/test_purity.py`, keyed on the
  `subprocess` import (the spawn mechanism), which does not false-positive on the
  word "ffmpeg" appearing in a docstring or the `FFmpegVideoWriter` name:

  ```python
  def test_the_io_layer_spawns_no_ffmpeg_subprocess() -> None:
      """No io module spawns an ffmpeg or ffprobe subprocess: the io layer
      decodes and encodes in process through av. The sanctioned boundary is a
      call into the probe layer's public API (multi.py calls probe_media), which
      owns its ffprobe subprocess as the measurement authority; that is an
      imported name, not a subprocess spawned here."""
      modules = numpy_layer_files()
      assert modules, "io layer modules not found"
      offenders = [
          str(module.relative_to(CORE_ROOT))
          for module in modules
          if "subprocess" in imported_roots(module.read_text())
      ]
      message = f"io modules must not spawn a subprocess, found: {offenders}"
      assert offenders == [], message
  ```

- [ ] Run and confirm green, and run the grep acceptance as a manual
  cross-check (io modules carry no `subprocess`/`ffmpeg`/`ffprobe` invocation;
  the class name `FFmpegVideoWriter` and prose mentions are expected and do not
  spawn anything):

  ```bash
  uv run pytest tests/probe/test_purity.py -q
  grep -rn "subprocess\|Popen" src/mosaic_media/io/ || echo "no subprocess in io"
  ```

  Expected: the suite passes; the grep prints `no subprocess in io`.

- [ ] Commit: `Assert no io module spawns an ffmpeg subprocess`.

---

## Task 9 -- README: in-process libav decode

Update `README.md` so the io section reflects in-process decode, the `av`
dependency, and the ffmpeg-floor sentence rescoped to the core and the test
ground truth (the reader no longer needs an ffmpeg binary at runtime).

**Files:** `README.md`.

### Steps

- [ ] Layering table row for the io extra: add `av`, and describe the reader as
  in-process rather than ffmpeg-pipe. Change

  ```
  | `mosaic-media[io]` | `numpy` | ffmpeg-pipe frame reader, seek index, multi-video reader. **No OpenCV.** |
  ```

  to

  ```
  | `mosaic-media[io]` | `numpy`, `av` | In-process libav (PyAV) frame reader, seek index, multi-video reader. **No OpenCV.** |
  ```

- [ ] Rescope the ffmpeg-floor sentence. Change

  ```
  The reader and the CLI require a system `ffmpeg` on `PATH`: version 5.1 or newer
  for the runtime path (`-fps_mode`), and 6.0 or newer to run the test suite
  (`-display_rotation`).
  ```

  to a version that scopes the ffmpeg floor to the probe/transcode/CLI core and
  the test ground truth, and states the io reader decodes in process through av
  and needs no ffmpeg binary at runtime -- the codec table is verified by the
  codec guard, with `av --no-binary av` as the system-libav fallback. Keep the
  6.0+ test-suite floor (`-display_rotation`, used by corpus generation).

- [ ] Update "Why the reader comes here too": the reader still improves on
  OpenCV's `CAP_PROP_POS_FRAMES` by seeking against the exact packet index, but
  it now decodes in process through libav rather than through a
  moviepy/imageio-ffmpeg subprocess. Replace the subprocess-architecture
  paragraph accordingly (the packet-index-exact seeking claim stands), and point
  to the spec's "In-process decode supersedes the subprocess pipe for io" for the
  gate evidence.

- [ ] Leave "The OpenCV decode problem" as the standing treatment; it is
  unchanged (OpenCV stays excluded for both its wheel-table and its API defects).

- [ ] Verify the README has no remaining claim that the reader shells out to
  ffmpeg, and that "No OpenCV" and the transcode/probe ffmpeg dependency are
  intact:

  ```bash
  grep -n "subprocess\|persistent ffmpeg\|ffmpeg-pipe" README.md || echo "no stale reader-subprocess claim"
  ```

  Expected: no stale reader-subprocess claim (transcode/probe references to
  system ffmpeg are correct and remain).

- [ ] Commit: `Document in-process libav decode in the README`.

---

## Task 10 -- full verification

Run the whole check suite on the rewritten stack.

**Files:** none created; verification only.

### Steps

- [ ] Format and lint the whole tree:

  ```bash
  uv run ruff format src/ tests/
  uv run ruff check src/ tests/
  ```

  Expected: clean (or a re-format, then clean).

- [ ] Type-check the whole tree with the bench group synced so `tests/bench` and
  its cv2 stubs resolve (basedpyright scope includes `tests/`):

  ```bash
  uv run --group bench basedpyright src/ tests/
  ```

  Expected: `0 errors, 0 warnings`, no suppression anywhere.

- [ ] Run the full default suite through `heavy` (bench excluded by `addopts`;
  the codec guard, packet-scan, index, reader, writer, multi, guard, and purity
  suites included):

  ```bash
  heavy uv run pytest tests/
  ```

  Expected: green. If `heavy` auto-backgrounds the run (it queues behind another
  job on the global lock), re-block on it in the foreground with
  `heavy --wait <logpath>`; never end the task with a heavy run outstanding.

- [ ] Commit any residual formatting only (the guard skips the commit when the
  tree is clean):

  ```bash
  git diff --quiet || git commit -am "Format the io rewrite"
  ```

---

## Gate-branch fold-in (orchestrator-executed, after this plan lands on main)

This plan lands on main first. The performance gate lives on the
`perf-regression-gate` branch, which carries the bench suite (`tests/bench/`);
that suite exists only on the gate branch. The orchestrator then executes the
following against that branch -- it is not a task for this plan's implementers.

- [ ] Rebase `perf-regression-gate` onto post-adoption main.
- [ ] Amend the gate thresholds per Task 0's table and the amended three-tier
  policy: default `>= 1.0`; the sequential-shaped carve-out `>= 0.9` with the
  owned-BGR rationale recorded at the threshold site; rotation gated at `>= 1.0`
  (not the carve tier); cold-random-seek kept as a non-gating `assert_bounded`
  report with the tightened parity-or-better expectation recorded. Record each
  workload's stabilization median next to its threshold.
- [ ] Apply the thin-margin rule: any gated workload whose margin over its bound
  is under 10 percent (sorted-sparse-extraction[gop250], multi-video-junction,
  the metadata rows) runs at `rounds=9`.
- [ ] Re-run the full gate serialized on an otherwise idle machine -- on this
  machine, `heavy uv run pytest -m bench -n0 -s`, never bare, never parallel --
  and confirm every gated workload prints `-> PASS`. Retain the serialized run
  log on the branch.
- [ ] Complete the branch through the clean-branch cycle: archive the implemented
  plan (`.implemented.md`) and the closed duplicate-timestamp issue
  (`.closed.md`) with their `_INDEX.md` rows flipped, keep the still-open
  re-scoped encoder-gate issue tracked, fast-forward-merge, and leave the push to
  the user.

**Acceptance:** the gate branch is green on the amended thresholds, the
serialized run log is retained, and the branch is merged.

---

## Self-review

1. **Coverage.** Every deliverable in the brief has a task. Gate-threshold record
   (Task 0); av dependency wiring (Task 1); codec guard, TDD, decode-one-frame
   per format (Task 2); in-process packet scan via av demux with whole-file dts
   fallback, pts-dedup consistent with `measure_timing`, constructed-duplicate
   unit tests, `SeekIndex.frame_count == MediaFacts.frame_count`, the
   duplicate-timestamp issue closure, and the guard sequencing that lands the
   purity allowance and io-requires-av test with the first av import (Task 3);
   reader rewrite -- container lifecycle, PTS-exact `container.seek` plus
   decode-forward, window semantics, filter-graph rotation, `FFmpegError ->
   MediaProbeError` with truncation preserved, hwaccel documented no-op, the
   rewritten failed-init test (Task 4); writer rewrite -- av libx264 encode,
   validation and loud failure preserved, av-side NVENC usability probe, the
   encoder-gate issue half-closure with re-scoped transcode text (Task 5); multi
   reader (Task 6); final guards -- core/cli-without-av, deliberate-break (Task
   7); the committed no-subprocess guard with the grep acceptance and the
   sanctioned probe-layer/test-helper carve-out (Task 8); README rescope (Task
   9); full verification through `heavy` with bench-aware basedpyright (Task 10);
   the orchestrator-executed gate-branch fold-in as an explicit final section.
   Both issue dispositions are written out in full (Task 3 closure, Task 5
   re-scope). The staffing note directs the strongest implementer and closest
   review to Tasks 4 and 5.

2. **Placeholder scan.** Every code block is complete: no `...`, `TODO`, or
   pseudo-code. The one deliberately-open number is
   `strided-decode-step5[gop250]`, marked "measured at gate" in Task 0 and the
   spec, per the reviewer's finding that its stabilization was taken only in the
   gop12 form.

3. **Signature consistency.** `VideoReader`, `MultiVideoReader`,
   `FFmpegVideoWriter`, `SeekIndex`, and `build_seek_index` keep the exact
   signatures restated under "Interfaces produced (pinned)"; the rewrite changes
   internals only. `build_seek_index(packets: tuple[Packet, ...]) -> SeekIndex`
   is unchanged; the new `scan_packets_in_process(path) -> tuple[tuple[Packet,
   ...], TimestampSource]` mirrors the probe's `scan_packets` return shape. The
   writer gains a read-only `encoder_name` property (derived state for the
   fallback assertion), which does not alter the pinned constructor or methods.
   Every av API named in a code block (`av.open`, `container.demux`,
   `container.decode`, `container.seek(offset, stream=, backward=True)`,
   packet `pts`/`dts`/`size`/`pos`/`is_keyframe`, `stream.time_base`,
   `stream.thread_type`, `frame.to_ndarray`, `frame.rotation`,
   `av.filter.Graph` transpose, `av.VideoFrame.from_ndarray`,
   `stream.encode`/`container.mux`, `av.codec.CodecContext.create(...).open()`,
   `av.error.FFmpegError`/`InvalidDataError`/`PermissionError`, and the builtin
   `ValueError` at unwritable-format open) was confirmed against av 18.0.0.
