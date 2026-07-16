# Frame Reader IO Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with a fresh implementer per task and a review between tasks. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `mosaic_media.io` subpackage -- the pure-ffmpeg frame reader that replaces OpenCV decoding. A single `VideoReader` (sequential, strided, resize, grayscale, frame-exact seek, GOP-grouped sparse batch), a `MultiVideoReader` over an ordered file set, an `FFmpegVideoWriter` absorbed from `mosaic`, and a `SeekIndex` built from the packet scan. All decode goes through system ffmpeg over a subprocess pipe; numpy is the only third-party dependency and it is confined to this subpackage.

**Architecture:** `io/` sits one layer above `probe/` and `hwaccel`. It imports `mosaic_media.probe` (for `read_header`, `scan_packets`, `MediaFacts`, `uniform_properties`, `probe_media`, `MediaProbeError`) and `mosaic_media.hwaccel` (for `ffmpeg_available`, `nvdec_available`, `encoder_available`), and nothing higher. The core facade `mosaic_media/__init__` does not re-export `io`; users import `mosaic_media.io` explicitly, so `import mosaic_media` never pulls numpy. The reader keeps one persistent ffmpeg process for sequential and strided reads and respawns with an input `-ss` at the target's preceding keyframe for discontinuous seeks; the packet index makes the discard-versus-respawn choice exact and seeks frame-exact by construction.

**Tech Stack:** Python 3.12+, uv, system ffmpeg and ffprobe on PATH, numpy (>=1.22, `[io]` extra). No OpenCV in the runtime path; `opencv-python` appears only as a `bench` group dependency consumed by the perf gate (a separate plan) and by the opt-in cv2-equality suite here behind `pytest.importorskip`.

## Global Constraints

- Python floor is 3.12, never 3.13 (this package is upstream of both consumers and takes the lower floor).
- numpy is imported only inside `io/` modules (the `[io]` extra); `probe/`, `thumbnail/`, and `hwaccel` stay standard-library only.
- ASCII only in code: no box-drawing, arrows, or bullets; use `---`, `->`, `-`.
- American spelling everywhere (identifiers, comments, docstrings): `behavior`, `color`, `gray`, `grayscale`.
- No `typing.Any`, no `typing.Optional` (use `X | None`), no `typing.cast`, no `# noqa`, no `# pyright: ignore`. Fix the underlying design instead of suppressing.
- No multi-line f-strings: assign the message to a variable first, then raise or log.
- Full identifier names, no abbreviations (`frame_index` not `f_idx`, `keyframe_index` not `kf_idx`), except comprehension-local names where compactness is conventional.
- Frames are BGR uint8 with shape `(height, width, 3)` unless `grayscale=True`, in which case `(height, width)`.
- Layering is one-way: `io` imports `probe` and `hwaccel` only, never `transcode` or `cli`.
- Measurement is never re-derived when facts are injected: an injected `MediaFacts` supplies width, height, fps, and frame count directly; the reader does not re-run the probe's grid-fit measurement.
- Commit messages are plain English with no conventional-commit prefixes (`feat:`, `fix:`, ...) and no `Co-Authored-By` trailers. No process language, tool names, or plan references in commits, docstrings, or comments.
- Full-suite runs go through `heavy uv run pytest`; a single quick test runs as plain `uv run pytest tests/io/test_foo.py::test_bar`.
- The standing development environment is `uv sync --all-extras --group dev`: `uv sync` and `uv run` install only the dependencies plus default groups and never the optional extras, so the `io` extra (numpy) must be synced explicitly or every numpy-importing test fails with `No module named 'numpy'`. The `bench` group stays opt-in (`uv sync --all-extras --group dev --group bench`) for the perf gate and the cv2-equality suite.

## Interfaces consumed from the scaffold-and-probe-extraction plan (plan 1)

Treat these as already existing. Do not re-create them; import them.

- `mosaic_media.probe.ffprobe`:
  - `@dataclass(frozen=True, slots=True) class Packet` with fields `time: float`, `size: int`, `keyframe: bool` (this plan's Task 1 adds `pos: int`).
  - `@dataclass(frozen=True, slots=True) class Header` including `width: int`, `height: int`, `rotation_degrees: int`, `video_position: int`, `declared_fps: float`, `declared_frame_count: int`.
  - `read_header(path: Path) -> Header`.
  - `scan_packets(path: Path, video_position: int) -> tuple[tuple[Packet, ...], TimestampSource]` (this plan's Task 1 adds the `pos` field to the scan).
  - `TimestampSource = Literal["pts", "dts"]`.
- `mosaic_media.probe.errors`: `class MediaProbeError(RuntimeError)`.
- `mosaic_media.probe.facts`: `@dataclass(frozen=True, slots=True) class MediaFacts` with `width: int`, `height: int`, `rotation_degrees: int`, `fps: float`, `frame_count: int`, `duration: float` (among other measured fields).
- `mosaic_media.probe.probe`: `probe_media(path: Path, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> MediaFacts`.
- `mosaic_media.probe.sequence`: `uniform_properties(videos: Sequence[VideoProperties]) -> PropertyMismatch | None`; `@dataclass(frozen=True, slots=True) class PropertyMismatch` with `field: str`, `first: float`, `other: float`. `MediaFacts` satisfies the `VideoProperties` protocol structurally (it exposes `fps`, `width`, `height`, `frame_count`, `duration`).
- `mosaic_media.hwaccel` with exactly `ffmpeg_available() -> bool`, `nvdec_available() -> bool`, `encoder_available(name: str) -> bool`.
- `tests/probe/` (copied probe tests, currently constructing `Packet(time=..., size=..., keyframe=...)`), `tests/thumbnail/`, `tests/test_import_guard.py` (subprocess guard that poisons `numpy`, `typer`, and `cv2` in `sys.meta_path` and imports `mosaic_media`, `mosaic_media.probe.*`, `mosaic_media.thumbnail.*`, `mosaic_media.hwaccel`). Its shared runner `_run_guarded(body: str, *, forbidden_root: str) -> subprocess.CompletedProcess[str]` (fresh subprocess; finder raises `AssertionError` on any import whose top-level name equals `forbidden_root`) is reused by Task 10.
- `pyproject.toml` already declares the `io` extra (`numpy>=1.22`), the `bench` group (`opencv-python>=4.7`, `numpy>=1.22`), and the `bench` pytest marker excluded by default via `addopts = "-m 'not bench'"`.

## Interfaces produced for the performance-regression-gate plan (plan 3)

The gate is written against these signatures sight unseen. Do not change them after this plan lands without updating plan 3.

- `mosaic_media.io.VideoReader(path: Path | str, *, start_frame: int = 0, end_frame: int | None = None, frame_step: int = 1, resize: tuple[int, int] | None = None, grayscale: bool = False, hwaccel: bool = False, facts: MediaFacts | None = None, index: SeekIndex | None = None)`.
  - Properties `width: int`, `height: int`, `fps: float`, `frame_count: int`.
  - `read() -> tuple[bool, numpy.ndarray | None]`.
  - `read_batch(batch_size: int) -> tuple[numpy.ndarray, numpy.ndarray]`.
  - `read_frames(indices: Sequence[int]) -> Iterator[tuple[int, numpy.ndarray]]`.
  - `seek(frame_index: int) -> None`, `close() -> None`, `__iter__() -> Iterator[tuple[int, numpy.ndarray]]`, `__len__() -> int`, context manager.
- `mosaic_media.io.MultiVideoReader(video_paths: list[Path] | Path | str)` with properties `total_frames`, `fps`, `width`, `height`, `video_count`, `segments`, `frame_position`; methods `segment_for_frame(global_frame: int) -> tuple[int, int]`, `seek(global_frame: int) -> None`, `read() -> tuple[bool, numpy.ndarray | None]`, `close() -> None`, `__len__`; context manager.
- `mosaic_media.io.FFmpegVideoWriter(output_path: Path | str, width: int, height: int, fps: float = 30.0, crf: int = 23, preset: str = "medium", hwaccel: bool = False)` with properties `output_path`, `width`, `height`, `fps`, `frames_written`; methods `write(frame: numpy.ndarray) -> None`, `close() -> None`; context manager.
- `mosaic_media.io.SeekIndex` and `mosaic_media.io.build_seek_index(packets: tuple[Packet, ...]) -> SeekIndex`.
- Test corpus helper `tests/helpers/corpus.py`: `generate_video(path: Path, *, frames: int, fps: float = 30.0, gop: int = 30, size: tuple[int, int] = (320, 240), codec: str = "libx264", rotation_degrees: int = 0) -> Path`; session fixtures `corpus_gop12` and `corpus_gop250`; ground-truth helper `decode_md5s(path: Path, *, grayscale: bool = False) -> list[str]`.

---

## Task 1 -- probe/ffprobe.py divergence: add the `pos` byte offset

**Files:**
- `src/mosaic_media/probe/ffprobe.py` (edit the copied module)
- `tests/probe/test_ffprobe.py` (edit the copied test)
- `tests/probe/test_gop.py` (edit the copied test)
- `tests/probe/test_timing.py` (edit the copied test)

**Interfaces:**
- Consumes: the copied `Packet(time: float, size: int, keyframe: bool)` and `scan_packets` from plan 1.
- Produces: `Packet(time: float, size: int, keyframe: bool, pos: int)`; `scan_packets` requesting and parsing `packet=pts_time,dts_time,size,pos,flags`. This is a deliberate, documented divergence from the `mosaic_api` copy.

**Rationale, recorded in the code and the commit:** the `pos` byte offset costs one ffprobe token and is independently useful to any `io` consumer that wants a packet's location in the container. It rides on `Packet` itself; the seek index does not carry it (`SeekIndex` needs only frame timestamps and keyframe positions), and the reader's seek path is timestamp-based and never consults it.

Verified column order (system ffprobe 6.1.1): `-show_entries packet=pts_time,dts_time,size,pos,flags -of csv=p=0` emits five columns in the order `pts_time,dts_time,size,pos,flags`. When `pos` is unavailable (some containers), ffprobe emits `N/A`; parse it to `-1`.

### Steps

- [ ] Add a failing test asserting `scan_packets` populates `pos`. In `tests/probe/test_ffprobe.py`, append:

  ```python
  def test_scan_packets_populates_byte_offset(clips: dict[str, Path]) -> None:
      packets, _source = scan_packets(clips["cfr_mp4"], video_position=0)
      # The first packet of an mp4 sits at a small positive byte offset; every
      # packet in a well-formed mp4 has a known position.
      assert packets[0].pos >= 0
      assert all(packet.pos >= 0 for packet in packets)
      # Positions are distinct: no two packets share a byte offset.
      assert len({packet.pos for packet in packets}) == len(packets)
  ```

- [ ] Run and observe failure:

  ```bash
  uv run pytest tests/probe/test_ffprobe.py::test_scan_packets_populates_byte_offset -q
  ```

  Expected: `AttributeError: 'Packet' object has no attribute 'pos'` (the field does not exist yet).

- [ ] Add `pos` to the `Packet` dataclass in `src/mosaic_media/probe/ffprobe.py`. Append the field so the diff from the `mosaic_api` copy is one added line:

  ```python
  @dataclass(frozen=True, slots=True)
  class Packet:
      time: float
      size: int
      keyframe: bool
      pos: int
  ```

- [ ] Update `scan_packets` to request and parse `pos`. Replace the `-show_entries` value and the parse loop:

  ```python
      command = [
          "ffprobe",
          "-v",
          "error",
          "-select_streams",
          f"v:{video_position}",
          "-show_entries",
          "packet=pts_time,dts_time,size,pos,flags",
          "-of",
          "csv=p=0",
          str(path.absolute()),
      ]
      raw = _run(command, SCAN_TIMEOUT_SECONDS, f"scanning the packets of {path}")

      pts_packets: list[Packet] = []
      dts_packets: list[Packet] = []
      for line in raw.splitlines():
          # ffprobe emits the requested entries in its own natural order:
          # pts_time, dts_time, size, pos, flags. Byte offset (pos) is N/A on
          # containers that do not expose it; it is carried for io consumers and
          # is not used by the timestamp-based seek path, so -1 is a safe unknown.
          columns = line.split(",")
          if len(columns) < 5:
              continue
          size_text, pos_text, flags = columns[2], columns[3], columns[4]
          if not size_text.isdigit():
              continue
          size = int(size_text)
          pos = int(pos_text) if pos_text.lstrip("-").isdigit() else -1
          keyframe = "K" in flags
          if columns[0] not in _ABSENT:
              pts_packets.append(
                  Packet(time=float(columns[0]), size=size, keyframe=keyframe, pos=pos)
              )
          if columns[1] not in _ABSENT:
              dts_packets.append(
                  Packet(time=float(columns[1]), size=size, keyframe=keyframe, pos=pos)
              )
  ```

- [ ] Update the copied probe tests that construct `Packet` instances to pass `pos`. In `tests/probe/test_gop.py`, every literal `Packet(...)` construction gains `pos=<n>` (any distinct or placeholder value; `gop` never reads `pos`). Rewrite the two literal-tuple tests, e.g.:

  ```python
  def test_gop_bytes_and_frames_are_the_worst_interval() -> None:
      packets = (
          Packet(time=0.0, size=1000, keyframe=True, pos=0),
          Packet(time=0.04, size=10, keyframe=False, pos=1000),
          Packet(time=0.08, size=10, keyframe=False, pos=1010),
          Packet(time=0.12, size=5000, keyframe=True, pos=1020),
          Packet(time=0.16, size=10, keyframe=False, pos=6020),
      )
      stats = measure_gop(packets)
      assert stats.max_gop_bytes == 5010
      assert stats.max_keyframe_interval_frames == 3
  ```

  Apply the same `pos=` addition to every remaining `Packet(...)` in `test_gop.py`. For the comprehension-built tuples, pass `pos=index`:

  ```python
      packets = tuple(
          Packet(time=index / 25.0, size=100, keyframe=False, pos=index)
          for index in range(10)
      )
  ```

- [ ] Update `tests/probe/test_timing.py` the same way: every `Packet(time=..., size=..., keyframe=...)` gains `pos=...`. For the comprehension helpers use `pos=index`; for the single-literal cases use any non-negative integer. Example:

  ```python
      packets = tuple(
          Packet(time=index / fps, size=100, keyframe=index == 0, pos=index)
          for index in range(count)
      )
  ```

  and the ad-hoc appended packet:

  ```python
      packets = uniform(100, 25.0) + (Packet(time=0.0, size=10, keyframe=False, pos=0),)
  ```

- [ ] Update the CSV-canary test in `tests/probe/test_ffprobe.py` (`test_packet_csv_column_order_is_...`) to request `pos` and assert the five-column order, so the parser's assumption stays pinned:

  ```python
  def test_packet_csv_column_order_is_pts_dts_size_pos_flags(
      clips: dict[str, Path],
  ) -> None:
      # ffprobe emits -show_entries fields in its own natural order, not the order
      # requested. If a future ffmpeg reorders them the parser silently mis-reads
      # a column. This test is the canary for the five-field scan.
      import subprocess

      command = [
          "ffprobe",
          "-v",
          "error",
          "-select_streams",
          "v:0",
          "-show_entries",
          "packet=pts_time,dts_time,size,pos,flags",
          "-of",
          "csv=p=0",
          str(clips["cfr_mp4"]),
      ]
      first = subprocess.run(
          command, capture_output=True, text=True, timeout=60
      ).stdout.splitlines()[0]
      columns = first.split(",")
      assert len(columns) == 5
      assert float(columns[0]) >= 0.0  # pts_time
      assert columns[2].isdigit()  # size
      assert columns[3].lstrip("-").isdigit()  # pos
      assert "K" in columns[4] or "_" in columns[4]  # flags
  ```

  If plan 1's copy kept the old four-column canary under its original name, rename it to the five-field form above (do not leave a stale four-column assertion).

- [ ] Run the probe suite and observe pass:

  ```bash
  uv run pytest tests/probe/ -q
  ```

  Expected: all probe tests pass, including `test_scan_packets_populates_byte_offset` and the renamed canary. Terminal line resembles `NN passed`.

- [ ] Commit: `Add packet byte offset to the media probe scan`.

---

## Task 2 -- io/index.py: the seek index

**Files:**
- `src/mosaic_media/io/__init__.py` (create, minimal for now: export `SeekIndex`, `build_seek_index`)
- `src/mosaic_media/io/index.py` (create)
- `tests/io/__init__.py` (create, empty)
- `tests/io/test_index.py` (create)

**Interfaces:**
- Consumes: `Packet` (with `pos`) from `mosaic_media.probe.ffprobe`.
- Produces:
  - `@dataclass(frozen=True, slots=True) class SeekIndex` with `frame_times: tuple[float, ...]` (presentation order, ascending) and `keyframe_indices: tuple[int, ...]` (presentation frame indices of keyframes, ascending).
    - property `frame_count -> int`.
    - `preceding_keyframe(frame_index: int) -> tuple[int, float]` -- the frame index and presentation timestamp of the keyframe at or before `frame_index`.
    - `group_by_gop(indices: Sequence[int]) -> list[list[int]]` -- sorted unique targets partitioned so that all targets in a group share one preceding keyframe.
  - `build_seek_index(packets: tuple[Packet, ...]) -> SeekIndex`.

**Design rationale (record in the module docstring):** `scan_packets` returns packets in decode order; the reader emits frames in presentation order, and every public frame index is a presentation index. So the index sorts packet timestamps ascending to recover presentation order, and a keyframe's presentation frame index is its rank in that sorted order. `preceding_keyframe(N)` returns the largest keyframe frame index `<= N`; seeking there with an input `-ss` at that keyframe's timestamp and discarding `N - keyframe_index` frames is frame-exact. `index.py` imports only the standard library (`bisect`); it does not import numpy.

### Steps

- [ ] Write failing tests. Create `tests/io/test_index.py`:

  ```python
  from mosaic_media.io.index import SeekIndex, build_seek_index
  from mosaic_media.probe.ffprobe import Packet


  def _cfr_packets(count: int, gop: int) -> tuple[Packet, ...]:
      # Decode order equals presentation order here (no B-frame reordering).
      return tuple(
          Packet(time=index / 30.0, size=100, keyframe=index % gop == 0, pos=index)
          for index in range(count)
      )


  def test_frame_times_are_presentation_order_ascending() -> None:
      index = build_seek_index(_cfr_packets(40, 12))
      assert index.frame_count == 40
      assert list(index.frame_times) == sorted(index.frame_times)


  def test_frame_times_recovered_from_decode_order_with_reordering() -> None:
      # Two B-frame-reordered packets: decode order (0.0, 0.20, 0.10, 0.30),
      # presentation order sorts to (0.0, 0.10, 0.20, 0.30).
      packets = (
          Packet(time=0.0, size=100, keyframe=True, pos=0),
          Packet(time=0.20, size=100, keyframe=False, pos=100),
          Packet(time=0.10, size=100, keyframe=True, pos=200),
          Packet(time=0.30, size=100, keyframe=False, pos=300),
      )
      index = build_seek_index(packets)
      assert index.frame_times == (0.0, 0.10, 0.20, 0.30)
      # The keyframe at presentation time 0.10 is presentation frame 1.
      assert index.keyframe_indices == (0, 1)


  def test_preceding_keyframe_returns_index_and_timestamp() -> None:
      index = build_seek_index(_cfr_packets(40, 12))
      # Frame 15's preceding keyframe is frame 12 at time 12/30 = 0.4.
      keyframe_index, keyframe_time = index.preceding_keyframe(15)
      assert keyframe_index == 12
      assert keyframe_time == 12 / 30.0
      # A frame that is itself a keyframe returns itself.
      assert index.preceding_keyframe(24) == (24, 24 / 30.0)
      # A frame before any later keyframe returns frame 0.
      assert index.preceding_keyframe(5) == (0, 0.0)


  def test_preceding_keyframe_rejects_out_of_range() -> None:
      index = build_seek_index(_cfr_packets(40, 12))
      import pytest

      with pytest.raises(IndexError):
          index.preceding_keyframe(40)
      with pytest.raises(IndexError):
          index.preceding_keyframe(-1)


  def test_group_by_gop_partitions_by_shared_keyframe() -> None:
      index = build_seek_index(_cfr_packets(40, 12))
      # Targets in GOPs [0,12), [12,24), [24,36), [36,40).
      groups = index.group_by_gop([5, 3, 13, 20, 25, 5])
      assert groups == [[3, 5], [13, 20], [25]]


  def test_group_by_gop_empty_input() -> None:
      index = build_seek_index(_cfr_packets(40, 12))
      assert index.group_by_gop([]) == []


  def test_stream_without_keyframe_flags_seeks_from_start() -> None:
      packets = tuple(
          Packet(time=index / 25.0, size=100, keyframe=False, pos=index)
          for index in range(10)
      )
      index = build_seek_index(packets)
      assert index.preceding_keyframe(7) == (0, 0.0)
      assert index.group_by_gop([2, 5, 7]) == [[2, 5, 7]]
  ```

- [ ] Run and observe failure:

  ```bash
  uv run pytest tests/io/test_index.py -q
  ```

  Expected: `ModuleNotFoundError: No module named 'mosaic_media.io'`.

- [ ] Create `src/mosaic_media/io/index.py`:

  ```python
  """Seek index over a packet scan. Standard library only (bisect); no numpy.

  scan_packets returns packets in decode order, but the reader emits frames in
  presentation order and every public frame index is a presentation index. This
  index sorts packet timestamps ascending to recover presentation order, records
  which presentation frames are keyframes, and answers the one question a
  frame-exact ffmpeg seek needs: the preceding keyframe of a target frame, as a
  (frame index, timestamp) pair. Seeking with an input -ss at that timestamp and
  discarding target-minus-keyframe frames lands on the target exactly, which is
  what makes OpenCV's off-by-N CAP_PROP_POS_FRAMES class of bugs impossible here.
  """

  import bisect
  from collections.abc import Sequence
  from dataclasses import dataclass

  from mosaic_media.probe.ffprobe import Packet


  @dataclass(frozen=True, slots=True)
  class SeekIndex:
      frame_times: tuple[float, ...]
      keyframe_indices: tuple[int, ...]

      @property
      def frame_count(self) -> int:
          return len(self.frame_times)

      def preceding_keyframe(self, frame_index: int) -> tuple[int, float]:
          """The (frame index, presentation timestamp) of the keyframe at or
          before `frame_index`. A stream with no keyframe flags decodes from
          frame 0, so the fallback is (0, first timestamp)."""
          if frame_index < 0 or frame_index >= len(self.frame_times):
              message = (
                  f"frame index {frame_index} out of range "
                  f"[0, {len(self.frame_times)})"
              )
              raise IndexError(message)
          if not self.keyframe_indices:
              return 0, self.frame_times[0]
          position = bisect.bisect_right(self.keyframe_indices, frame_index) - 1
          if position < 0:
              return 0, self.frame_times[0]
          keyframe_index = self.keyframe_indices[position]
          return keyframe_index, self.frame_times[keyframe_index]

      def group_by_gop(self, indices: Sequence[int]) -> list[list[int]]:
          """Partition sorted unique targets so each group shares one preceding
          keyframe. The reader decodes each group in a single forward pass."""
          groups: list[list[int]] = []
          current: list[int] = []
          current_keyframe: int | None = None
          for target in sorted({int(index) for index in indices}):
              keyframe_index, _ = self.preceding_keyframe(target)
              if current and keyframe_index == current_keyframe:
                  current.append(target)
              else:
                  if current:
                      groups.append(current)
                  current = [target]
                  current_keyframe = keyframe_index
          if current:
              groups.append(current)
          return groups


  def build_seek_index(packets: tuple[Packet, ...]) -> SeekIndex:
      """Build a SeekIndex from a decode-order packet scan."""
      order = sorted(range(len(packets)), key=lambda position: packets[position].time)
      frame_times = tuple(packets[position].time for position in order)
      keyframe_indices = tuple(
          rank for rank, position in enumerate(order) if packets[position].keyframe
      )
      return SeekIndex(frame_times=frame_times, keyframe_indices=keyframe_indices)
  ```

- [ ] Create `src/mosaic_media/io/__init__.py` (minimal for now; Task 8 completes it):

  ```python
  """Frame reading through system ffmpeg. Requires the [io] extra (numpy).

  This subpackage is not re-exported by the mosaic_media core facade: importing
  mosaic_media must not pull numpy. Import mosaic_media.io explicitly.
  """

  from .index import SeekIndex, build_seek_index

  __all__ = ["SeekIndex", "build_seek_index"]
  ```

- [ ] Create empty `tests/io/__init__.py`.

- [ ] Run and observe pass:

  ```bash
  uv run pytest tests/io/test_index.py -q
  ```

  Expected: all index tests pass (`8 passed`).

- [ ] Commit: `Add the frame seek index over the packet scan`.

---

## Task 3 -- tests/helpers/corpus.py: generated corpus and ground truth

**Files:**
- `tests/helpers/__init__.py` (create if absent)
- `tests/helpers/corpus.py` (create)
- `tests/conftest.py` (create or edit: register the corpus fixtures)
- `tests/io/test_corpus.py` (create)

**Interfaces:**
- Consumes: system ffmpeg/ffprobe; `mosaic_media.probe.probe.probe_media` (to self-check generated clips); `tests.helpers.media_fixtures.build(destination: Path, *arguments: str, source: list[str] | None = None) -> Path` (the verbatim-copied fixture builder from plan 1, reused by `generate_video` instead of a second ffmpeg-runner copy).
- Produces (pinned for plan 3):
  - `generate_video(path: Path, *, frames: int, fps: float = 30.0, gop: int = 30, size: tuple[int, int] = (320, 240), codec: str = "libx264", rotation_degrees: int = 0) -> Path`.
  - `decode_md5s(path: Path, *, grayscale: bool = False) -> list[str]` -- per-frame md5 of the decoded frame in bgr24 (or gray) via ffmpeg `framemd5`, in presentation order.
  - Session fixtures `corpus_gop12` and `corpus_gop250` (both 320x240, small).

**Verified facts:** `ffmpeg ... testsrc2=size=WxH:rate=FPS:duration=D -frames:v N -c:v CODEC -pix_fmt yuv420p -g GOP` produces exactly `N` frames with keyframes every `GOP`. A rotation is applied by re-muxing with `-display_rotation` and `-c copy` (which writes a display-matrix side data rotation and leaves coded dimensions unchanged). `framemd5` with `-pix_fmt bgr24` hashes each decoded frame's packed bytes; that md5 equals `hashlib.md5(frame.tobytes()).hexdigest()` for the numpy frame the reader yields (verified against the seek path).

### Steps

- [ ] Sync the `io` extra so numpy is importable. This is the first task whose tests import numpy (`corpus.py`), and `uv sync` / `uv run` never install optional extras on their own, so without this every test below fails with `No module named 'numpy'` rather than the intended red:

  ```bash
  uv sync --extra io
  ```

  Expected: uv resolves and installs numpy (and the default `dev` group). Per Global Constraints the standing environment is `uv sync --all-extras --group dev`; this step names the one extra these tests need.

- [ ] Write a failing self-check test. Create `tests/io/test_corpus.py`:

  ```python
  from pathlib import Path

  from mosaic_media.probe.probe import probe_media
  from tests.helpers.corpus import decode_md5s, generate_video


  def test_generate_video_produces_exact_frame_count(tmp_path: Path) -> None:
      path = generate_video(tmp_path / "clip.mp4", frames=40, fps=30.0, gop=12)
      facts = probe_media(path)
      assert facts.frame_count == 40
      assert (facts.width, facts.height) == (320, 240)
      assert facts.max_keyframe_interval_frames <= 12


  def test_generate_video_embeds_rotation_as_side_data(tmp_path: Path) -> None:
      path = generate_video(
          tmp_path / "rot.mp4", frames=24, fps=30.0, gop=12, rotation_degrees=90
      )
      facts = probe_media(path)
      assert facts.rotation_degrees == 90
      # Rotation is metadata only: coded dimensions are unchanged.
      assert (facts.width, facts.height) == (320, 240)


  def test_decode_md5s_returns_one_hash_per_frame(tmp_path: Path) -> None:
      path = generate_video(tmp_path / "clip.mp4", frames=40, fps=30.0, gop=12)
      goldens = decode_md5s(path)
      assert len(goldens) == 40
      assert all(len(digest) == 32 for digest in goldens)
      gray = decode_md5s(path, grayscale=True)
      assert len(gray) == 40
      assert gray != goldens


  def test_corpus_fixtures_available(
      corpus_gop12: Path, corpus_gop250: Path
  ) -> None:
      assert probe_media(corpus_gop12).max_keyframe_interval_frames <= 12
      assert probe_media(corpus_gop250).frame_count >= 250
  ```

- [ ] Run and observe failure:

  ```bash
  uv run pytest tests/io/test_corpus.py -q
  ```

  Expected: `ModuleNotFoundError: No module named 'tests.helpers.corpus'`.

- [ ] Create `tests/helpers/__init__.py` (empty) if it does not already exist.

- [ ] Create `tests/helpers/corpus.py`:

  ```python
  """Generated video corpus and ffmpeg-derived ground truth for the reader tests.

  Every clip is built by the ffmpeg on PATH, a documented system dependency, so a
  missing encoder is a failure, not a skip. Ground truth comes from ffmpeg's own
  framemd5: hashing the decoded frame in the reader's output pixel format lets the
  reader be checked frame-exact with no cv2 in the loop.
  """

  import hashlib
  import subprocess
  import tempfile
  from pathlib import Path

  import numpy

  from tests.helpers.media_fixtures import build


  def generate_video(
      path: Path,
      *,
      frames: int,
      fps: float = 30.0,
      gop: int = 30,
      size: tuple[int, int] = (320, 240),
      codec: str = "libx264",
      rotation_degrees: int = 0,
  ) -> Path:
      """Generate a testsrc2 clip of exactly `frames` frames at `fps` with a
      keyframe every `gop` frames. A nonzero `rotation_degrees` is written as a
      display-matrix side data rotation via a copy re-mux, leaving coded
      dimensions unchanged."""
      path.parent.mkdir(parents=True, exist_ok=True)
      width, height = size
      # Ask for more than enough duration, then cap with -frames:v for an exact count.
      duration = frames / fps + 1.0
      lavfi_source = f"testsrc2=size={width}x{height}:rate={fps}:duration={duration}"
      encode_source = ["-f", "lavfi", "-i", lavfi_source]
      encode_arguments = (
          "-frames:v", str(frames),
          "-c:v", codec, "-pix_fmt", "yuv420p", "-g", str(gop),
      )
      if rotation_degrees == 0:
          return build(path, *encode_arguments, source=encode_source)
      with tempfile.TemporaryDirectory() as work:
          upright = Path(work) / "upright.mp4"
          build(upright, *encode_arguments, source=encode_source)
          return build(
              path,
              "-c", "copy",
              source=["-display_rotation", str(rotation_degrees), "-i", str(upright)],
          )


  def decode_md5s(path: Path, *, grayscale: bool = False) -> list[str]:
      """Per-frame md5 of the decoded frame, in presentation order, in the same
      pixel format the reader yields (bgr24, or gray when grayscale=True). Each
      digest equals hashlib.md5(frame.tobytes()).hexdigest() for the reader frame."""
      pixel_format = "gray" if grayscale else "bgr24"
      # Not build(): build discards stdout, and this call must read the framemd5
      # report off stdout. The clip-construction path reuses build; this ground-
      # truth path needs the output, so it runs its own capture.
      result = subprocess.run(
          [
              "ffmpeg", "-hide_banner", "-v", "error",
              "-i", str(path),
              "-pix_fmt", pixel_format,
              "-f", "framemd5", "-",
          ],
          capture_output=True,
          text=True,
          timeout=120,
      )
      if result.returncode != 0:
          message = f"framemd5 failed for {path}: {result.stderr.strip()}"
          raise RuntimeError(message)
      digests: list[str] = []
      for line in result.stdout.splitlines():
          if line.startswith("#") or not line.strip():
              continue
          digests.append(line.rsplit(",", 1)[-1].strip())
      return digests


  def frame_md5(frame: numpy.ndarray) -> str:
      """md5 of a numpy frame's C-contiguous bytes, matching decode_md5s digests."""
      return hashlib.md5(frame.tobytes()).hexdigest()
  ```

  `generate_video` reuses `build` from the verbatim-copied `tests/helpers/media_fixtures.py` (same `ffmpeg -hide_banner -v error -y` prefix, same run-capture-raise-with-stderr contract, same 120 s timeout), so the corpus keeps no second copy of that logic. Importing `build` does not modify `media_fixtures.py`, so the duplication-window freeze on the copied module is untouched. `decode_md5s` cannot use `build` because `build` discards stdout while `decode_md5s` must consume the framemd5 report off stdout; that is stated in the code comment. `frame_md5` is annotated with the concrete numpy type: this test-support module is covered by the `[io]` extra, so numpy is available and no suppression is needed. The pinned `generate_video` signature is unchanged.

- [ ] Register the corpus fixtures in `tests/conftest.py`. Plan 1 already created this file to register the copied probe `clips` fixtures via `pytest_plugins` (pytest honors `pytest_plugins` only in the rootdir conftest). Extend that file with the corpus imports and fixtures; the merged result is:

  ```python
  """Shared test fixtures for the mosaic_media suite."""

  from collections.abc import Iterator
  from pathlib import Path

  import pytest

  from tests.helpers.corpus import generate_video

  # The copied probe tests consume the generated media clips; pytest_plugins is
  # honored only in the rootdir conftest. Preserved from plan 1.
  pytest_plugins = ["tests.helpers.media_fixtures"]


  @pytest.fixture(scope="session")
  def corpus_gop12(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
      root = tmp_path_factory.mktemp("corpus_gop12")
      yield generate_video(root / "gop12.mp4", frames=48, fps=30.0, gop=12)


  @pytest.fixture(scope="session")
  def corpus_gop250(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
      root = tmp_path_factory.mktemp("corpus_gop250")
      yield generate_video(root / "gop250.mp4", frames=300, fps=30.0, gop=250)
  ```

  Keep plan 1's `pytest_plugins = ["tests.helpers.media_fixtures"]` line exactly as shown (add the corpus imports and fixtures around it); do not drop it, or the copied probe tests lose their `clips` fixture.

- [ ] Run and observe pass:

  ```bash
  uv run pytest tests/io/test_corpus.py -q
  ```

  Expected: `4 passed`.

- [ ] Commit: `Add the generated video corpus and framemd5 ground truth`.

---

## Task 4 -- io/reader.py part A: process lifecycle and sequential read

**Files:**
- `src/mosaic_media/io/reader.py` (create)
- `tests/io/test_reader_sequential.py` (create)

**Interfaces:**
- Consumes: `read_header`, `scan_packets` from `mosaic_media.probe.ffprobe`; `MediaFacts` from `mosaic_media.probe.facts`; `MediaProbeError` from `mosaic_media.probe.errors`; `ffmpeg_available`, `nvdec_available` from `mosaic_media.hwaccel`; `SeekIndex`, `build_seek_index` from `.index`.
- Produces: `VideoReader` with the full pinned constructor signature; this task implements lazy metadata resolution, the persistent-process lifecycle, `read()` for the default sequential case (start=0, step=1, end=None, BGR full resolution), `read_batch`, `__iter__`, `__len__`, `close`, and the context manager. The `resize`, `grayscale`, `start_frame`, `end_frame`, `frame_step`, `seek`, and `read_frames` parameters are accepted and stored now; their effects land in Tasks 5, 6, and 7.

**Module docstring (use verbatim; it carries the required attribution):**

```python
"""Frame reading through a system-ffmpeg subprocess pipe. Requires numpy.

The subprocess architecture -- one persistent ffmpeg process for sequential
reads, respawned with an input -ss for a discontinuous seek -- follows the
established practice of moviepy's FFMPEG_VideoReader (MIT) and imageio-ffmpeg
(BSD-2). This reader improves on both by seeking against an exact packet index:
the preceding keyframe of a target frame is known, so a seek respawns at that
keyframe and discards a known number of frames, landing frame-exact. That
removes OpenCV's off-by-N CAP_PROP_POS_FRAMES class of bugs by construction and
lets a file be decoded by the same system ffmpeg that probed it, with no bundled
codec table in the loop.
"""
```

**Metadata resolution rules:**
- If `facts` is injected, width/height/fps/frame count/rotation come from it and no ffprobe runs before decode.
- Otherwise the reader lazily runs `read_header` once for width, height, `declared_fps`, and `rotation_degrees`; frame count comes from `declared_frame_count` when the header reports one, else from the lazily built index length. The reader never re-runs the probe's variable-rate grid-fit measurement; for measurement-authoritative fps and count, inject facts.
- The packet scan is deferred until the first operation that needs the index (a seek, `read_frames`, or a missing header frame count).
- ffmpeg autorotates a display-matrix rotation by default and cv2 (>= 4.5) auto-orients too, so the reader keeps autorotation on and reports the displayed orientation. When `rotation_degrees % 180 == 90` and no `resize` is requested, the reported width and height are the coded height and width swapped; every emitted frame is shaped to that displayed orientation. This is consulted in both the facts-injected and self-probed paths.

### Steps

- [ ] Write failing tests. Create `tests/io/test_reader_sequential.py`:

  ```python
  from pathlib import Path

  import numpy
  import pytest

  from mosaic_media.io.reader import VideoReader
  from mosaic_media.probe.probe import probe_media
  from tests.helpers.corpus import decode_md5s, frame_md5, generate_video


  def test_sequential_read_equals_framemd5_golden(corpus_gop12: Path) -> None:
      goldens = decode_md5s(corpus_gop12)
      produced: list[str] = []
      with VideoReader(corpus_gop12) as reader:
          while True:
              ok, frame = reader.read()
              if not ok:
                  break
              assert frame is not None
              produced.append(frame_md5(frame))
      assert produced == goldens


  def test_iter_yields_index_and_frame(corpus_gop12: Path) -> None:
      goldens = decode_md5s(corpus_gop12)
      with VideoReader(corpus_gop12) as reader:
          pairs = [(index, frame_md5(frame)) for index, frame in reader]
      assert [index for index, _ in pairs] == list(range(len(goldens)))
      assert [digest for _, digest in pairs] == goldens


  def test_frame_shape_and_dtype_are_bgr_uint8(corpus_gop12: Path) -> None:
      with VideoReader(corpus_gop12) as reader:
          ok, frame = reader.read()
      assert ok
      assert frame is not None
      assert frame.shape == (reader.height, reader.width, 3)
      assert frame.dtype == numpy.uint8


  def test_properties_from_injected_facts_avoid_probing(corpus_gop12: Path) -> None:
      facts = probe_media(corpus_gop12)
      reader = VideoReader(corpus_gop12, facts=facts)
      assert reader.width == facts.width
      assert reader.height == facts.height
      assert reader.fps == pytest.approx(facts.fps)
      assert reader.frame_count == facts.frame_count
      reader.close()


  def test_read_batch_returns_indices_and_stacked_frames(corpus_gop12: Path) -> None:
      with VideoReader(corpus_gop12) as reader:
          indices, frames = reader.read_batch(8)
      assert indices.shape == (8,)
      assert frames.shape == (8, reader.height, reader.width, 3)
      assert list(indices) == list(range(8))


  def test_read_batch_empty_at_end(corpus_gop12: Path) -> None:
      with VideoReader(corpus_gop12) as reader:
          # Drain the reader.
          while reader.read()[0]:
              pass
          indices, frames = reader.read_batch(4)
      assert indices.shape == (0,)
      assert frames.shape[0] == 0


  def test_len_is_output_frame_count(corpus_gop12: Path) -> None:
      facts = probe_media(corpus_gop12)
      with VideoReader(corpus_gop12, facts=facts) as reader:
          assert len(reader) == facts.frame_count


  def test_rotated_video_reports_displayed_orientation_self_probe(
      tmp_path: Path,
  ) -> None:
      # A 90-degree 320x240 source displays as 240 wide by 320 tall. ffmpeg
      # autorotates by default, so decode_md5s and the reader both emit the
      # rotated frame; only the reported shape distinguishes a correct reader
      # from one that reshapes the bytes with the coded dimensions.
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


  def test_rotated_video_reports_displayed_orientation_injected_facts(
      tmp_path: Path,
  ) -> None:
      path = generate_video(
          tmp_path / "rot.mp4", frames=24, fps=30.0, gop=12, rotation_degrees=90
      )
      facts = probe_media(path)
      assert facts.rotation_degrees == 90
      goldens = decode_md5s(path)
      produced: list[str] = []
      with VideoReader(path, facts=facts) as reader:
          # Coded dimensions are 320x240; the reader swaps them for the display.
          assert reader.width == 240
          assert reader.height == 320
          for _index, frame in reader:
              assert frame.shape == (320, 240, 3)
              produced.append(frame_md5(frame))
      assert produced == goldens
  ```

- [ ] Run and observe failure:

  ```bash
  uv run pytest tests/io/test_reader_sequential.py -q
  ```

  Expected: `ModuleNotFoundError: No module named 'mosaic_media.io.reader'`.

- [ ] Create `src/mosaic_media/io/reader.py` with the module docstring above followed by:

  ```python
  import fcntl
  import io
  import subprocess
  from collections.abc import Iterator, Sequence
  from dataclasses import dataclass
  from pathlib import Path

  import numpy

  from mosaic_media.hwaccel import ffmpeg_available, nvdec_available
  from mosaic_media.probe.errors import MediaProbeError
  from mosaic_media.probe.facts import MediaFacts
  from mosaic_media.probe.ffprobe import read_header, scan_packets

  from .index import SeekIndex, build_seek_index

  # Linux fcntl.F_SETPIPE_SZ. Hard-coded so the module imports on platforms whose
  # fcntl lacks the constant; the fcntl call is guarded and best-effort anyway.
  _F_SETPIPE_SZ = 1031
  _PIPE_BYTES = 1024 * 1024


  @dataclass(frozen=True, slots=True)
  class _Geometry:
      source_width: int
      source_height: int
      fps: float
      source_frame_count: int
      out_width: int
      out_height: int
      channels: int
      frame_nbytes: int


  class VideoReader:
      def __init__(
          self,
          path: Path | str,
          *,
          start_frame: int = 0,
          end_frame: int | None = None,
          frame_step: int = 1,
          resize: tuple[int, int] | None = None,
          grayscale: bool = False,
          hwaccel: bool = False,
          facts: MediaFacts | None = None,
          index: SeekIndex | None = None,
      ) -> None:
          if not ffmpeg_available():
              message = "ffmpeg not found on PATH; install ffmpeg to read frames"
              raise MediaProbeError(message)
          self._path = Path(path).expanduser().resolve()
          self._start_frame = max(0, int(start_frame))
          self._end_frame = None if end_frame is None else int(end_frame)
          self._frame_step = max(1, int(frame_step))
          self._resize = (
              None if resize is None else (int(resize[0]), int(resize[1]))
          )
          self._grayscale = bool(grayscale)
          self._want_hwaccel = bool(hwaccel)
          self._facts = facts
          self._index = index
          self._geometry: _Geometry | None = None
          self._scratch: numpy.ndarray | None = None
          self._process: subprocess.Popen[bytes] | None = None
          self._mode = "idle"  # "idle" | "sequential" | "positioned"
          self._emitted = 0  # sequential: count of frames returned so far
          self._decoder_pos = 0  # positioned: next absolute source frame emitted
          self._target = 0  # positioned: next absolute frame read() returns
          self._last_index = 0  # index of the most recently returned frame
          self._closed = False

      # --- Metadata resolution ---

      def _ensure_index(self) -> SeekIndex:
          if self._index is None:
              header = read_header(self._path)
              packets, _source = scan_packets(self._path, header.video_position)
              self._index = build_seek_index(packets)
          return self._index

      def _ensure_ready(self) -> _Geometry:
          if self._geometry is not None:
              return self._geometry
          if self._facts is not None:
              source_width = self._facts.width
              source_height = self._facts.height
              fps = self._facts.fps
              source_frame_count = self._facts.frame_count
              rotation_degrees = self._facts.rotation_degrees
          else:
              header = read_header(self._path)
              source_width = header.width
              source_height = header.height
              fps = header.declared_fps
              rotation_degrees = header.rotation_degrees
              if header.declared_frame_count > 0:
                  source_frame_count = header.declared_frame_count
              else:
                  source_frame_count = self._ensure_index().frame_count
          if self._resize is not None:
              # The scale filter runs after ffmpeg's automatic display-matrix
              # rotation, so a resize produces exactly the requested dimensions.
              out_width, out_height = self._resize
          elif rotation_degrees % 180 == 90:
              # ffmpeg autorotates by default and cv2 (>= 4.5) auto-orients too,
              # so a quarter-turn source is emitted with display width and height
              # swapped relative to the coded (source_width, source_height). The
              # reader reports and shapes frames in that displayed orientation to
              # match both decoders; the byte count is unchanged (w*h*3 is
              # symmetric), so only the reported shape distinguishes the two.
              out_width, out_height = source_height, source_width
          else:
              out_width, out_height = source_width, source_height
          channels = 1 if self._grayscale else 3
          self._geometry = _Geometry(
              source_width=source_width,
              source_height=source_height,
              fps=fps,
              source_frame_count=source_frame_count,
              out_width=out_width,
              out_height=out_height,
              channels=channels,
              frame_nbytes=out_width * out_height * channels,
          )
          return self._geometry

      # --- Properties ---

      @property
      def width(self) -> int:
          return self._ensure_ready().out_width

      @property
      def height(self) -> int:
          return self._ensure_ready().out_height

      @property
      def fps(self) -> float:
          return self._ensure_ready().fps

      @property
      def frame_count(self) -> int:
          geometry = self._ensure_ready()
          end = (
              geometry.source_frame_count
              if self._end_frame is None
              else min(self._end_frame, geometry.source_frame_count)
          )
          start = min(self._start_frame, end)
          return len(range(start, end, self._frame_step))

      # --- Process lifecycle ---

      def _spawn(self, seek_timestamp: float | None, use_select: bool) -> None:
          geometry = self._ensure_ready()
          command = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
          if self._want_hwaccel and nvdec_available():
              command += ["-hwaccel", "cuda"]
          if seek_timestamp is not None:
              command += ["-ss", f"{seek_timestamp:.6f}"]
          command += ["-i", str(self._path)]
          filters: list[str] = []
          if use_select:
              expression = self._select_expression(geometry.source_frame_count)
              if expression is not None:
                  filters.append(f"select={expression}")
          if self._resize is not None:
              filters.append(f"scale={geometry.out_width}:{geometry.out_height}")
          if filters:
              command += ["-vf", ",".join(filters)]
          pixel_format = "gray" if self._grayscale else "bgr24"
          command += [
              "-fps_mode", "passthrough",
              "-f", "rawvideo",
              "-pix_fmt", pixel_format,
              "pipe:1",
          ]
          process = subprocess.Popen(
              command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
          )
          if process.stdout is not None:
              try:
                  fcntl.fcntl(process.stdout.fileno(), _F_SETPIPE_SZ, _PIPE_BYTES)
              except OSError:
                  pass
          self._process = process

      def _select_expression(self, source_frame_count: int) -> str | None:
          parts: list[str] = []
          if self._start_frame > 0:
              parts.append(f"gte(n\\,{self._start_frame})")
          if self._end_frame is not None and self._end_frame < source_frame_count:
              parts.append(f"lt(n\\,{self._end_frame})")
          if self._frame_step > 1:
              parts.append(f"not(mod(n-{self._start_frame}\\,{self._frame_step}))")
          return "*".join(parts) if parts else None

      def _close_process(self) -> None:
          process = self._process
          self._process = None
          self._mode = "idle"
          self._emitted = 0
          if process is None:
              return
          if process.stdout is not None:
              process.stdout.close()
          try:
              process.kill()
          except ProcessLookupError:
              return
          try:
              process.wait(timeout=5)
          except subprocess.TimeoutExpired:
              pass

      # --- Low-level frame reads ---

      def _stdout(self) -> io.BufferedReader | None:
          # subprocess.Popen(stdout=PIPE) yields a BufferedReader at runtime;
          # typeshed widens it to IO[bytes], which does not declare readinto.
          # Narrow it here so the read loop calls readinto without a suppression.
          process = self._process
          if process is None or not isinstance(process.stdout, io.BufferedReader):
              return None
          return process.stdout

      @staticmethod
      def _read_exact(stream: io.BufferedReader, view: memoryview) -> bool:
          total = 0
          size = len(view)
          while total < size:
              read = stream.readinto(view[total:])
              if not read:
                  return False
              total += read
          return True

      def _grab(self, geometry: _Geometry) -> numpy.ndarray | None:
          stream = self._stdout()
          if stream is None:
              return None
          if self._grayscale:
              frame = numpy.empty(
                  (geometry.out_height, geometry.out_width), dtype=numpy.uint8
              )
          else:
              frame = numpy.empty(
                  (geometry.out_height, geometry.out_width, 3), dtype=numpy.uint8
              )
          if not self._read_exact(stream, memoryview(frame).cast("B")):
              return None
          return frame

      def _skip_one(self, geometry: _Geometry) -> bool:
          stream = self._stdout()
          if stream is None:
              return False
          if self._scratch is None:
              self._scratch = numpy.empty(geometry.frame_nbytes, dtype=numpy.uint8)
          return self._read_exact(stream, memoryview(self._scratch))

      # --- Public reads ---

      def read(self) -> tuple[bool, numpy.ndarray | None]:
          if self._closed:
              return False, None
          geometry = self._ensure_ready()
          if self._mode == "positioned":
              return self._read_positioned(geometry)
          return self._read_sequential(geometry)

      def _read_sequential(
          self, geometry: _Geometry
      ) -> tuple[bool, numpy.ndarray | None]:
          if self._mode == "idle":
              self._spawn(seek_timestamp=None, use_select=True)
              self._mode = "sequential"
          end = (
              geometry.source_frame_count
              if self._end_frame is None
              else min(self._end_frame, geometry.source_frame_count)
          )
          index = self._start_frame + self._emitted * self._frame_step
          if index >= end:
              return False, None
          frame = self._grab(geometry)
          if frame is None:
              return False, None
          self._last_index = index
          self._emitted += 1
          return True, frame

      def read_batch(self, batch_size: int) -> tuple[numpy.ndarray, numpy.ndarray]:
          geometry = self._ensure_ready()
          indices: list[int] = []
          frames: list[numpy.ndarray] = []
          for _ in range(batch_size):
              ok, frame = self.read()
              if not ok or frame is None:
                  break
              indices.append(self._last_index)
              frames.append(frame)
          if not frames:
              if self._grayscale:
                  empty = numpy.empty(
                      (0, geometry.out_height, geometry.out_width), dtype=numpy.uint8
                  )
              else:
                  empty = numpy.empty(
                      (0, geometry.out_height, geometry.out_width, 3),
                      dtype=numpy.uint8,
                  )
              return numpy.empty(0, dtype=numpy.int64), empty
          return numpy.asarray(indices, dtype=numpy.int64), numpy.stack(frames)

      def __iter__(self) -> Iterator[tuple[int, numpy.ndarray]]:
          while True:
              ok, frame = self.read()
              if not ok or frame is None:
                  break
              yield self._last_index, frame

      # --- Cleanup and context management ---

      def close(self) -> None:
          if not self._closed:
              self._closed = True
              self._close_process()

      def __enter__(self) -> "VideoReader":
          return self

      def __exit__(self, *_args: object) -> None:
          self.close()

      def __del__(self) -> None:
          self.close()

      def __len__(self) -> int:
          return self.frame_count
  ```

  The `_Geometry` dataclass is defined at module scope before `VideoReader` (shown in the import block above), so its annotations need no string forward references. The `readinto` loop reads through `_stdout()`, which narrows `subprocess.Popen[bytes].stdout` (typed `io.IOBase | None` by typeshed) to a concrete `io.BufferedReader` with an `isinstance` check; that is why `_read_exact` calls `readinto` with no suppression.

- [ ] Run and observe pass:

  ```bash
  uv run pytest tests/io/test_reader_sequential.py -q
  ```

  Expected: `9 passed`.

- [ ] Verify types on the new module:

  ```bash
  uv run basedpyright src/mosaic_media/io/reader.py
  ```

  Expected: `0 errors, 0 warnings` with no `# pyright: ignore`. If `readinto` typing is awkward, resolve it by narrowing `process.stdout` to `io.BufferedReader` with an `isinstance` check, not by suppression.

- [ ] Commit: `Add the ffmpeg video reader with sequential decode`.

---

## Task 5 -- io/reader.py part B: strided, resize, and grayscale

**Files:**
- `src/mosaic_media/io/reader.py` (no new code if Task 4 wired the filter chain; this task activates and tests the non-default parameters)
- `tests/io/test_reader_strided.py` (create)

**Interfaces:**
- Consumes: the Task 4 `VideoReader`.
- Produces: verified behavior for `frame_step > 1`, `start_frame > 0`, `end_frame`, `resize`, and `grayscale`. The `_select_expression` and `scale`/`gray` handling were written in Task 4; this task proves them against goldens and shapes. If Task 4's implementer scoped the filter chain to defaults only, add the `_select_expression`, `scale` filter, and `gray` pixel format here exactly as shown in Task 4's `_spawn`.

**Verified facts:** `select=gte(n\,S)*lt(n\,E)*not(mod(n-S\,K))` in an argv `-vf` value (no shell) emits exactly the frames `n` in `{S, S+K, ...} intersect [S, E)`. `scale=W:H` yields `W*H*3` bytes per bgr24 frame. `-pix_fmt gray` yields `W*H` bytes per frame with shape `(H, W)`. Grayscale via ffmpeg extracts luma and does not equal cv2's BGR2GRAY recomputation, so grayscale correctness is checked against ffmpeg's own gray framemd5, never against cv2. Resize interpolation differs from cv2's INTER_AREA, so resize is checked by output shape only.

### Steps

- [ ] Write failing tests. Create `tests/io/test_reader_strided.py`:

  ```python
  from pathlib import Path

  import numpy

  from mosaic_media.io.reader import VideoReader
  from tests.helpers.corpus import decode_md5s, frame_md5


  def test_strided_read_equals_golden_subset(corpus_gop12: Path) -> None:
      goldens = decode_md5s(corpus_gop12)
      expected = [(i, goldens[i]) for i in range(0, len(goldens), 5)]
      with VideoReader(corpus_gop12, frame_step=5) as reader:
          produced = [(index, frame_md5(frame)) for index, frame in reader]
      assert produced == expected


  def test_start_and_end_window(corpus_gop12: Path) -> None:
      goldens = decode_md5s(corpus_gop12)
      expected = [(i, goldens[i]) for i in range(10, 30)]
      with VideoReader(corpus_gop12, start_frame=10, end_frame=30) as reader:
          produced = [(index, frame_md5(frame)) for index, frame in reader]
      assert produced == expected


  def test_start_end_and_step_combined(corpus_gop12: Path) -> None:
      goldens = decode_md5s(corpus_gop12)
      expected = [(i, goldens[i]) for i in range(6, 40, 3)]
      with VideoReader(
          corpus_gop12, start_frame=6, end_frame=40, frame_step=3
      ) as reader:
          produced = [(index, frame_md5(frame)) for index, frame in reader]
      assert produced == expected


  def test_grayscale_equals_gray_golden_and_shape(corpus_gop12: Path) -> None:
      goldens = decode_md5s(corpus_gop12, grayscale=True)
      produced: list[str] = []
      with VideoReader(corpus_gop12, grayscale=True) as reader:
          for _index, frame in reader:
              assert frame.ndim == 2
              assert frame.shape == (reader.height, reader.width)
              assert frame.dtype == numpy.uint8
              produced.append(frame_md5(frame))
      assert produced == goldens


  def test_resize_changes_output_shape_only(corpus_gop12: Path) -> None:
      with VideoReader(corpus_gop12, resize=(160, 120)) as reader:
          assert reader.width == 160
          assert reader.height == 120
          ok, frame = reader.read()
      assert ok
      assert frame is not None
      assert frame.shape == (120, 160, 3)


  def test_resize_and_grayscale_together(corpus_gop12: Path) -> None:
      with VideoReader(corpus_gop12, resize=(160, 120), grayscale=True) as reader:
          ok, frame = reader.read()
      assert ok
      assert frame is not None
      assert frame.shape == (120, 160)
  ```

- [ ] Run and observe result:

  ```bash
  uv run pytest tests/io/test_reader_strided.py -q
  ```

  If Task 4 wired the full filter chain, these pass immediately (`6 passed`). If any fail because the filter chain was scoped to defaults, add the `_select_expression`, `scale`, and `gray` handling to `_spawn` exactly as written in Task 4, then re-run to green.

- [ ] Commit: `Support strided, resized, and grayscale reads in the video reader`.

---

## Task 6 -- io/reader.py part C: frame-exact seek

**Files:**
- `src/mosaic_media/io/reader.py` (add `seek`, `_read_positioned`, `_read_current`)
- `tests/io/test_reader_seek.py` (create)

**Interfaces:**
- Consumes: the Task 4 `VideoReader`, `_ensure_index`, `_grab`, `_skip_one`, `_spawn`, `_close_process`.
- Produces: `seek(frame_index: int) -> None` and the positioned read path. Seek repositions to an absolute source frame. It respawns with an input `-ss` at the target's preceding keyframe unless the live positioned process's next-emitted frame already lies between that keyframe and the target, in which case it reads and discards forward on the live process. After a seek, `read()` returns the sought frame, then advances by `frame_step` (bounded by the source frame count).

**Verified facts:** input `-ss <keyframe timestamp>` before `-i` lands the decoder exactly on that keyframe (first emitted frame is the keyframe), and reading-and-discarding `target - keyframe_index` frames yields a frame whose bytes md5 equals the framemd5 golden for the target -- confirmed for a target inside a GOP and for a target exactly on a keyframe. `-ss` at a keyframe resets the decoder's frame counter, which is why the positioned path never uses the `select` filter and discards in Python instead.

### Steps

- [ ] Write failing tests. Create `tests/io/test_reader_seek.py`:

  ```python
  from pathlib import Path

  import pytest

  from mosaic_media.io.reader import VideoReader
  from mosaic_media.probe.probe import probe_media
  from tests.helpers.corpus import decode_md5s, frame_md5


  def test_seek_into_gop_is_frame_exact(corpus_gop12: Path) -> None:
      goldens = decode_md5s(corpus_gop12)
      with VideoReader(corpus_gop12) as reader:
          reader.seek(15)
          ok, frame = reader.read()
      assert ok
      assert frame is not None
      assert frame_md5(frame) == goldens[15]


  def test_seek_exactly_on_keyframe(corpus_gop12: Path) -> None:
      goldens = decode_md5s(corpus_gop12)
      with VideoReader(corpus_gop12) as reader:
          for keyframe in (0, 12, 24, 36):
              reader.seek(keyframe)
              ok, frame = reader.read()
              assert ok
              assert frame is not None
              assert frame_md5(frame) == goldens[keyframe]


  def test_seek_then_sequential_continues(corpus_gop12: Path) -> None:
      goldens = decode_md5s(corpus_gop12)
      with VideoReader(corpus_gop12) as reader:
          reader.seek(20)
          produced = [frame_md5(reader.read()[1]) for _ in range(5)]
      assert produced == goldens[20:25]


  def test_monotonic_forward_seeks_reuse_live_process(corpus_gop12: Path) -> None:
      goldens = decode_md5s(corpus_gop12)
      with VideoReader(corpus_gop12) as reader:
          for target in (13, 14, 18, 30, 31):
              reader.seek(target)
              ok, frame = reader.read()
              assert ok
              assert frame is not None
              assert frame_md5(frame) == goldens[target]


  def test_backward_seek_respawns_and_is_exact(corpus_gop12: Path) -> None:
      goldens = decode_md5s(corpus_gop12)
      with VideoReader(corpus_gop12) as reader:
          reader.seek(30)
          assert frame_md5(reader.read()[1]) == goldens[30]
          reader.seek(5)
          assert frame_md5(reader.read()[1]) == goldens[5]


  def test_seek_out_of_range_raises(corpus_gop12: Path) -> None:
      facts = probe_media(corpus_gop12)
      with VideoReader(corpus_gop12, facts=facts) as reader:
          with pytest.raises(IndexError):
              reader.seek(facts.frame_count)
          with pytest.raises(IndexError):
              reader.seek(-1)
  ```

- [ ] Run and observe failure:

  ```bash
  uv run pytest tests/io/test_reader_seek.py -q
  ```

  Expected: `AttributeError: 'VideoReader' object has no attribute 'seek'`.

- [ ] Add the seek path to `VideoReader`. Insert these methods (after `read_batch`, before `__iter__`):

  ```python
      def seek(self, frame_index: int) -> None:
          geometry = self._ensure_ready()
          target = int(frame_index)
          if target < 0 or target >= geometry.source_frame_count:
              message = (
                  f"frame index {target} out of range "
                  f"[0, {geometry.source_frame_count})"
              )
              raise IndexError(message)
          index = self._ensure_index()
          keyframe_index, keyframe_time = index.preceding_keyframe(target)
          live = self._process is not None and self._mode == "positioned"
          reusable = live and keyframe_index <= self._decoder_pos <= target
          if not reusable:
              self._close_process()
              self._spawn(seek_timestamp=keyframe_time, use_select=False)
              self._decoder_pos = keyframe_index
          self._mode = "positioned"
          self._target = target

      def _read_current(self, geometry: _Geometry) -> numpy.ndarray | None:
          while self._decoder_pos < self._target:
              if not self._skip_one(geometry):
                  return None
              self._decoder_pos += 1
          frame = self._grab(geometry)
          if frame is None:
              return None
          self._decoder_pos += 1
          return frame

      def _read_positioned(
          self, geometry: _Geometry
      ) -> tuple[bool, numpy.ndarray | None]:
          if self._target >= geometry.source_frame_count:
              return False, None
          self._last_index = self._target
          frame = self._read_current(geometry)
          if frame is None:
              return False, None
          self._target += self._frame_step
          return True, frame
  ```

- [ ] Run and observe pass:

  ```bash
  uv run pytest tests/io/test_reader_seek.py -q
  ```

  Expected: `6 passed`.

- [ ] Commit: `Add frame-exact seeking to the video reader`.

---

## Task 7 -- io/reader.py part D: sorted sparse batch

**Files:**
- `src/mosaic_media/io/reader.py` (add `read_frames`)
- `tests/io/test_reader_sparse.py` (create)

**Interfaces:**
- Consumes: `_ensure_index`, `SeekIndex.group_by_gop`, `seek`, `_read_current`.
- Produces: `read_frames(indices: Sequence[int]) -> Iterator[tuple[int, numpy.ndarray]]`. Targets are sorted and de-duplicated, grouped by GOP via the index, and decoded one forward pass per group: the first target in a group respawns at the shared keyframe, each subsequent target in the same group reuses the live process via forward discard (the `seek` reuse branch).

### Steps

- [ ] Write failing tests. Create `tests/io/test_reader_sparse.py`:

  ```python
  from pathlib import Path

  from mosaic_media.io.reader import VideoReader
  from tests.helpers.corpus import decode_md5s, frame_md5


  def test_read_frames_sparse_is_exact(corpus_gop12: Path) -> None:
      goldens = decode_md5s(corpus_gop12)
      wanted = [5, 7, 13, 20, 25, 45]
      with VideoReader(corpus_gop12) as reader:
          produced = {index: frame_md5(frame) for index, frame in reader.read_frames(wanted)}
      assert set(produced) == set(wanted)
      for index in wanted:
          assert produced[index] == goldens[index]


  def test_read_frames_sorts_and_deduplicates(corpus_gop12: Path) -> None:
      goldens = decode_md5s(corpus_gop12)
      with VideoReader(corpus_gop12) as reader:
          produced = list(reader.read_frames([20, 5, 20, 13, 5]))
      indices = [index for index, _ in produced]
      assert indices == [5, 13, 20]
      for index, frame in produced:
          assert frame_md5(frame) == goldens[index]


  def test_read_frames_within_one_gop(corpus_gop12: Path) -> None:
      goldens = decode_md5s(corpus_gop12)
      wanted = [12, 14, 16, 18]  # all in the GOP starting at keyframe 12
      with VideoReader(corpus_gop12) as reader:
          produced = list(reader.read_frames(wanted))
      assert [index for index, _ in produced] == wanted
      for index, frame in produced:
          assert frame_md5(frame) == goldens[index]


  def test_read_frames_empty(corpus_gop12: Path) -> None:
      with VideoReader(corpus_gop12) as reader:
          assert list(reader.read_frames([])) == []
  ```

- [ ] Run and observe failure:

  ```bash
  uv run pytest tests/io/test_reader_sparse.py -q
  ```

  Expected: `AttributeError: 'VideoReader' object has no attribute 'read_frames'`.

- [ ] Add `read_frames` to `VideoReader` (after `_read_positioned`):

  ```python
      def read_frames(
          self, indices: Sequence[int]
      ) -> Iterator[tuple[int, numpy.ndarray]]:
          geometry = self._ensure_ready()
          targets = sorted({int(index) for index in indices})
          if not targets:
              return
          index = self._ensure_index()
          for group in index.group_by_gop(targets):
              for target in group:
                  self.seek(target)
                  frame = self._read_current(geometry)
                  if frame is None:
                      message = f"failed to decode frame {target} from {self._path}"
                      raise MediaProbeError(message)
                  self._last_index = target
                  yield target, frame
  ```

  Note: `seek(target)` sets `_target = target` and leaves `_decoder_pos` at the keyframe (respawn) or the live position (reuse); `_read_current` advances `_decoder_pos` to `target`, grabs, and increments it past `target`. The next target in the group then satisfies the `seek` reuse branch (`keyframe_index <= _decoder_pos <= next_target`), so the whole group is one forward decode pass.

- [ ] Run and observe pass:

  ```bash
  uv run pytest tests/io/test_reader_sparse.py -q
  ```

  Expected: `4 passed`.

- [ ] Commit: `Add GOP-grouped sparse frame reads to the video reader`.

---

## Task 8 -- io/writer.py: FFmpegVideoWriter

**Files:**
- `src/mosaic_media/io/writer.py` (create)
- `tests/io/test_writer.py` (create)

**Interfaces:**
- Consumes: `ffmpeg_available`, `encoder_available` from `mosaic_media.hwaccel`; `MediaProbeError`.
- Produces: `FFmpegVideoWriter(output_path: Path | str, width: int, height: int, fps: float = 30.0, crf: int = 23, preset: str = "medium", hwaccel: bool = False)` with properties `output_path -> Path`, `width -> int`, `height -> int`, `fps -> float`, `frames_written -> int`; methods `write(frame: numpy.ndarray) -> None`, `close() -> None`; context manager. Absorbed from `mosaic`'s `video_io.FFmpegVideoWriter`, adapted to repository style: no `typing.Optional`, `RuntimeError` replaced by `MediaProbeError`, hardware encode gated by `encoder_available("h264_nvenc")` rather than a private cache. Behavior (libx264 CPU path, NVENC path, `yuv420p` output) is unchanged.

### Steps

- [ ] Write failing tests. Create `tests/io/test_writer.py`:

  ```python
  from pathlib import Path

  import numpy

  from mosaic_media.io.reader import VideoReader
  from mosaic_media.io.writer import FFmpegVideoWriter


  def test_writer_roundtrip_counts_and_dimensions(
      tmp_path: Path, corpus_gop12: Path
  ) -> None:
      with VideoReader(corpus_gop12) as reader:
          frames = [frame for _index, frame in reader]
      height, width = frames[0].shape[0], frames[0].shape[1]
      output = tmp_path / "out.mp4"
      with FFmpegVideoWriter(output, width, height, fps=30.0) as writer:
          for frame in frames:
              writer.write(frame)
          written = writer.frames_written
      assert written == len(frames)
      assert output.exists()
      with VideoReader(output) as reader:
          reread = [frame for _index, frame in reader]
      assert len(reread) == len(frames)
      assert reread[0].shape == frames[0].shape


  def test_writer_roundtrip_content_is_close(
      tmp_path: Path, corpus_gop12: Path
  ) -> None:
      with VideoReader(corpus_gop12) as reader:
          frames = [frame for _index, frame in reader]
      height, width = frames[0].shape[0], frames[0].shape[1]
      output = tmp_path / "out.mp4"
      with FFmpegVideoWriter(output, width, height, fps=30.0) as writer:
          for frame in frames:
              writer.write(frame)
      with VideoReader(output) as reader:
          reread = [frame for _index, frame in reader]
      # libx264 at crf 23 is lossy, so compare content approximately.
      differences = [
          float(numpy.abs(a.astype(numpy.int16) - b.astype(numpy.int16)).mean())
          for a, b in zip(frames, reread)
      ]
      assert max(differences) < 15.0


  def test_writer_properties(tmp_path: Path) -> None:
      output = tmp_path / "props.mp4"
      writer = FFmpegVideoWriter(output, 320, 240, fps=25.0)
      try:
          assert writer.output_path == output.expanduser().resolve()
          assert writer.width == 320
          assert writer.height == 240
          assert writer.fps == 25.0
          assert writer.frames_written == 0
      finally:
          writer.close()
  ```

- [ ] Run and observe failure:

  ```bash
  uv run pytest tests/io/test_writer.py -q
  ```

  Expected: `ModuleNotFoundError: No module named 'mosaic_media.io.writer'`.

- [ ] Create `src/mosaic_media/io/writer.py`:

  ```python
  """BGR-frame video writer through a system-ffmpeg subprocess pipe. Requires numpy.

  Raw bgr24 frames are piped to ffmpeg's stdin and encoded with libx264, or with
  h264_nvenc when hwaccel is requested and the encoder is available. Absorbed from
  mosaic's video_io: it was already pure ffmpeg, so only the capability probing
  and error type change to match this package.
  """

  import subprocess
  from pathlib import Path

  import numpy

  from mosaic_media.hwaccel import encoder_available, ffmpeg_available
  from mosaic_media.probe.errors import MediaProbeError


  class FFmpegVideoWriter:
      def __init__(
          self,
          output_path: Path | str,
          width: int,
          height: int,
          fps: float = 30.0,
          crf: int = 23,
          preset: str = "medium",
          hwaccel: bool = False,
      ) -> None:
          if not ffmpeg_available():
              message = "ffmpeg not found on PATH; install ffmpeg to write frames"
              raise MediaProbeError(message)
          self._output_path = Path(output_path).expanduser().resolve()
          self._output_path.parent.mkdir(parents=True, exist_ok=True)
          self._width = width
          self._height = height
          self._fps = fps
          self._frames_written = 0
          self._closed = False

          use_nvenc = hwaccel and encoder_available("h264_nvenc")
          command = [
              "ffmpeg", "-y",
              "-f", "rawvideo",
              "-pix_fmt", "bgr24",
              "-s", f"{width}x{height}",
              "-r", str(fps),
              "-i", "pipe:0",
          ]
          if use_nvenc:
              command += ["-c:v", "h264_nvenc", "-preset", preset, "-cq", str(crf)]
          else:
              command += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]
          command += ["-pix_fmt", "yuv420p", str(self._output_path)]

          self._process: subprocess.Popen[bytes] | None = subprocess.Popen(
              command,
              stdin=subprocess.PIPE,
              stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL,
          )

      @property
      def output_path(self) -> Path:
          return self._output_path

      @property
      def width(self) -> int:
          return self._width

      @property
      def height(self) -> int:
          return self._height

      @property
      def fps(self) -> float:
          return self._fps

      @property
      def frames_written(self) -> int:
          return self._frames_written

      def write(self, frame: numpy.ndarray) -> None:
          if self._closed:
              message = "writer is closed"
              raise MediaProbeError(message)
          process = self._process
          if process is None or process.stdin is None:
              message = "ffmpeg process is not running"
              raise MediaProbeError(message)
          process.stdin.write(frame.tobytes())
          self._frames_written += 1

      def close(self) -> None:
          if self._closed:
              return
          self._closed = True
          process = self._process
          self._process = None
          if process is None:
              return
          if process.stdin is not None:
              process.stdin.close()
          try:
              process.wait(timeout=30)
          except subprocess.TimeoutExpired:
              process.kill()
              try:
                  process.wait(timeout=5)
              except subprocess.TimeoutExpired:
                  pass

      def __enter__(self) -> "FFmpegVideoWriter":
          return self

      def __exit__(self, *_args: object) -> None:
          self.close()

      def __del__(self) -> None:
          self.close()
  ```

- [ ] Run and observe pass:

  ```bash
  uv run pytest tests/io/test_writer.py -q
  ```

  Expected: `3 passed`.

- [ ] Commit: `Absorb the ffmpeg video writer from the toolkit`.

---

## Task 9 -- io/multi.py: MultiVideoReader

**Files:**
- `src/mosaic_media/io/multi.py` (create)
- `tests/io/test_multi.py` (create)

**Interfaces:**
- Consumes: `VideoReader` from `.reader`; `probe_media` from `mosaic_media.probe.probe`; `uniform_properties` from `mosaic_media.probe.sequence`.
- Produces:
  - `@dataclass(frozen=True, slots=True) class VideoSegment` with `path: Path`, `frame_count: int`, `fps: float`, `width: int`, `height: int`, `start_frame: int`.
  - `MultiVideoReader(video_paths: list[Path] | Path | str)` with the pinned properties and methods. It probes each file once via `probe_media` (retaining the `MediaFacts`), validates uniformity across the sequence with `uniform_properties` (raising `ValueError` on the first mismatched width, height, or frame rate), and constructs a per-segment `VideoReader` with the segment's facts injected so no file is re-probed on read.

**Semantics (from `mosaic`'s `MultiVideoReader`):** N ordered files form one global frame space; segment 0 owns `[0, N0)`, segment 1 owns `[N0, N0 + N1)`, and so on. `read()` advances sequentially and crosses segment boundaries automatically. `seek(global_frame)` maps to a `(segment, local)` pair via bisect, opens that segment, and seeks locally.

### Steps

- [ ] Write failing tests. Create `tests/io/test_multi.py`:

  ```python
  from pathlib import Path

  import pytest

  from mosaic_media.io.multi import MultiVideoReader
  from tests.helpers.corpus import decode_md5s, frame_md5, generate_video


  @pytest.fixture(scope="module")
  def two_clips(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
      root = tmp_path_factory.mktemp("multi")
      first = generate_video(root / "a.mp4", frames=20, fps=30.0, gop=12)
      second = generate_video(root / "b.mp4", frames=15, fps=30.0, gop=12)
      return first, second


  def test_global_frame_space_properties(two_clips: tuple[Path, Path]) -> None:
      first, second = two_clips
      with MultiVideoReader([first, second]) as reader:
          assert reader.total_frames == 35
          assert reader.video_count == 2
          assert reader.width == 320
          assert reader.height == 240
          assert len(reader) == 35
          assert [segment.start_frame for segment in reader.segments] == [0, 20]


  def test_segment_for_frame(two_clips: tuple[Path, Path]) -> None:
      first, second = two_clips
      with MultiVideoReader([first, second]) as reader:
          assert reader.segment_for_frame(0) == (0, 0)
          assert reader.segment_for_frame(19) == (0, 19)
          assert reader.segment_for_frame(20) == (1, 0)
          assert reader.segment_for_frame(34) == (1, 14)
          with pytest.raises(IndexError):
              reader.segment_for_frame(35)


  def test_sequential_read_crosses_boundary(two_clips: tuple[Path, Path]) -> None:
      first, second = two_clips
      expected = decode_md5s(first) + decode_md5s(second)
      produced: list[str] = []
      with MultiVideoReader([first, second]) as reader:
          while True:
              ok, frame = reader.read()
              if not ok:
                  break
              assert frame is not None
              produced.append(frame_md5(frame))
      assert produced == expected


  def test_seek_across_boundary_then_read(two_clips: tuple[Path, Path]) -> None:
      first, second = two_clips
      expected = decode_md5s(first) + decode_md5s(second)
      with MultiVideoReader([first, second]) as reader:
          reader.seek(25)  # global frame 25 -> segment 1, local 5
          assert reader.frame_position == 25
          produced = [frame_md5(reader.read()[1]) for _ in range(5)]
      assert produced == expected[25:30]


  def test_single_path_accepted(two_clips: tuple[Path, Path]) -> None:
      first, _second = two_clips
      with MultiVideoReader(first) as reader:
          assert reader.video_count == 1
          assert reader.total_frames == 20


  def test_resolution_mismatch_raises(
      tmp_path_factory: pytest.TempPathFactory,
  ) -> None:
      root = tmp_path_factory.mktemp("mismatch")
      big = generate_video(root / "big.mp4", frames=10, fps=30.0, size=(320, 240))
      small = generate_video(root / "small.mp4", frames=10, fps=30.0, size=(160, 120))
      with pytest.raises(ValueError):
          MultiVideoReader([big, small])
  ```

- [ ] Run and observe failure:

  ```bash
  uv run pytest tests/io/test_multi.py -q
  ```

  Expected: `ModuleNotFoundError: No module named 'mosaic_media.io.multi'`.

- [ ] Create `src/mosaic_media/io/multi.py`:

  ```python
  """Read N ordered video files as one global frame space. Requires numpy.

  Segment 0 owns global frames [0, N0), segment 1 owns [N0, N0 + N1), and so on.
  Each file is probed once; its MediaFacts are injected into a per-segment
  VideoReader so no file is re-measured on read. Uniformity across the sequence is
  validated with the probe's uniform_properties, the same check the arrangement
  layer uses, so a resolution or frame-rate mismatch is rejected at construction.
  """

  import bisect
  from dataclasses import dataclass
  from pathlib import Path

  import numpy

  from mosaic_media.probe.facts import MediaFacts
  from mosaic_media.probe.probe import probe_media
  from mosaic_media.probe.sequence import uniform_properties

  from .reader import VideoReader


  @dataclass(frozen=True, slots=True)
  class VideoSegment:
      path: Path
      frame_count: int
      fps: float
      width: int
      height: int
      start_frame: int


  class MultiVideoReader:
      def __init__(self, video_paths: list[Path] | Path | str) -> None:
          self._closed = False
          self._reader: VideoReader | None = None
          if isinstance(video_paths, (str, Path)):
              paths = [Path(video_paths)]
          else:
              paths = [Path(entry) for entry in video_paths]
          if not paths:
              message = "at least one video path is required"
              raise ValueError(message)

          self._segments: list[VideoSegment] = []
          self._segment_starts: list[int] = []
          self._facts: list[MediaFacts] = []
          cumulative = 0
          for path in paths:
              resolved = path.expanduser().resolve()
              facts = probe_media(resolved)
              self._facts.append(facts)
              self._segments.append(
                  VideoSegment(
                      path=resolved,
                      frame_count=facts.frame_count,
                      fps=facts.fps,
                      width=facts.width,
                      height=facts.height,
                      start_frame=cumulative,
                  )
              )
              self._segment_starts.append(cumulative)
              cumulative += facts.frame_count

          mismatch = uniform_properties(self._facts)
          if mismatch is not None:
              message = (
                  f"property mismatch across sequence: {mismatch.field} "
                  f"{mismatch.first} vs {mismatch.other}"
              )
              raise ValueError(message)

          self._total_frames = cumulative
          self._current_segment = 0
          self._global_frame = 0

      # --- Properties ---

      @property
      def total_frames(self) -> int:
          return self._total_frames

      @property
      def fps(self) -> float:
          return self._segments[0].fps

      @property
      def width(self) -> int:
          return self._segments[0].width

      @property
      def height(self) -> int:
          return self._segments[0].height

      @property
      def video_count(self) -> int:
          return len(self._segments)

      @property
      def segments(self) -> list[VideoSegment]:
          return list(self._segments)

      @property
      def frame_position(self) -> int:
          return self._global_frame

      # --- Frame-to-segment mapping ---

      def segment_for_frame(self, global_frame: int) -> tuple[int, int]:
          if global_frame < 0 or global_frame >= self._total_frames:
              message = (
                  f"global frame {global_frame} out of range "
                  f"[0, {self._total_frames})"
              )
              raise IndexError(message)
          index = bisect.bisect_right(self._segment_starts, global_frame) - 1
          return index, global_frame - self._segment_starts[index]

      # --- Open / seek / read ---

      def _open_segment(self, segment_index: int, local_seek: int) -> None:
          if self._reader is not None:
              self._reader.close()
          self._reader = VideoReader(
              self._segments[segment_index].path,
              facts=self._facts[segment_index],
          )
          self._current_segment = segment_index
          if local_seek:
              self._reader.seek(local_seek)

      def seek(self, global_frame: int) -> None:
          segment_index, local_frame = self.segment_for_frame(global_frame)
          self._open_segment(segment_index, local_frame)
          self._global_frame = global_frame

      def read(self) -> tuple[bool, numpy.ndarray | None]:
          if self._closed or self._global_frame >= self._total_frames:
              return False, None
          if self._reader is None:
              self._open_segment(self._current_segment, 0)
          reader = self._reader
          if reader is None:
              return False, None
          ok, frame = reader.read()
          if not ok:
              next_index = self._current_segment + 1
              if next_index >= len(self._segments):
                  return False, None
              self._open_segment(next_index, 0)
              reader = self._reader
              if reader is None:
                  return False, None
              ok, frame = reader.read()
              if not ok:
                  return False, None
          self._global_frame += 1
          return True, frame

      # --- Cleanup ---

      def close(self) -> None:
          if not self._closed:
              self._closed = True
              if self._reader is not None:
                  self._reader.close()
                  self._reader = None

      def __enter__(self) -> "MultiVideoReader":
          return self

      def __exit__(self, *_args: object) -> None:
          self.close()

      def __del__(self) -> None:
          self.close()

      def __len__(self) -> int:
          return self._total_frames
  ```

- [ ] Run and observe pass:

  ```bash
  uv run pytest tests/io/test_multi.py -q
  ```

  Expected: `6 passed`.

- [ ] Commit: `Add the multi-video reader over a global frame space`.

---

## Task 10 -- io/__init__.py exports and the import guard interaction

**Files:**
- `src/mosaic_media/io/__init__.py` (complete the exports)
- `tests/test_import_guard.py` (add two assertions)

**Interfaces:**
- Consumes: plan 1's `tests/test_import_guard.py`, which poisons `numpy`, `typer`, and `cv2` in a subprocess and imports the core modules (`mosaic_media`, `mosaic_media.probe.*`, `mosaic_media.thumbnail.*`, `mosaic_media.hwaccel`). Plan 1 also exposes a shared parameterized runner in that same file: `_run_guarded(body: str, *, forbidden_root: str) -> subprocess.CompletedProcess[str]` (fresh subprocess whose `sys.meta_path` finder raises `AssertionError` on any import whose top-level name equals `forbidden_root`; returns the completed process). This task calls that helper rather than defining its own.
- Produces: `mosaic_media.io` exporting `VideoReader`, `MultiVideoReader`, `VideoSegment`, `FFmpegVideoWriter`, `SeekIndex`, `build_seek_index`. Two additional guard assertions: the core facade imports with numpy poisoned, and `mosaic_media.io` fails cleanly without numpy. The `io` subpackage stays out of the guarded core set.

### Steps

- [ ] Write failing tests. Append the following two test functions to `tests/test_import_guard.py`, below its existing functions. Do not define a runner here: call plan 1's shared `_run_guarded(body, *, forbidden_root=...)`, already defined at the top of that file (it also imports `subprocess`/`sys` there, so add no imports -- a mid-file `import` would trip ruff E402). A later transcode plan appends its own guard tests to the same file through the same helper, so an end-of-file merge across plans is expected at integration; keep these two functions self-contained so the merge is a plain concatenation.

  ```python
  def test_core_facade_imports_without_numpy() -> None:
      result = _run_guarded("import mosaic_media\n", forbidden_root="numpy")
      assert result.returncode == 0, result.stderr


  def test_io_subpackage_requires_numpy() -> None:
      result = _run_guarded(
          "import mosaic_media.io\n"
          "raise SystemExit('io imported without numpy')\n",
          forbidden_root="numpy",
      )
      # Importing mosaic_media.io must fail because numpy is poisoned; the
      # SystemExit sentinel must never be reached. The failure traceback names the
      # forbidden `import numpy` from the io layer.
      assert result.returncode != 0
      assert "io imported without numpy" not in (result.stdout + result.stderr)
      assert "numpy" in (result.stdout + result.stderr).lower()
  ```

- [ ] Run and observe failure:

  ```bash
  uv run pytest tests/test_import_guard.py -k "facade or io_subpackage" -q
  ```

  Expected: `test_io_subpackage_requires_numpy` fails while `io/__init__.py` still only exports the numpy-free index (so importing `mosaic_media.io` does not yet pull numpy). This proves the assertion is meaningful before the exports land.

- [ ] Complete `src/mosaic_media/io/__init__.py`:

  ```python
  """Frame reading through system ffmpeg. Requires the [io] extra (numpy).

  This subpackage is not re-exported by the mosaic_media core facade: importing
  mosaic_media must not pull numpy. Import mosaic_media.io explicitly.
  """

  from .index import SeekIndex, build_seek_index
  from .multi import MultiVideoReader, VideoSegment
  from .reader import VideoReader
  from .writer import FFmpegVideoWriter

  __all__ = [
      "FFmpegVideoWriter",
      "MultiVideoReader",
      "SeekIndex",
      "VideoReader",
      "VideoSegment",
      "build_seek_index",
  ]
  ```

- [ ] Confirm the core facade does not import `io`. Open `src/mosaic_media/__init__.py` (created by plan 1) and verify it contains no `from . import io` or `from .io import ...`. If such a line exists, remove it: the facade must expose only the standard-library-only core. Do not add an `io` re-export.

- [ ] Run and observe pass:

  ```bash
  uv run pytest tests/test_import_guard.py -q
  ```

  Expected: the full guard file passes, including the two new assertions (`mosaic_media` imports with numpy poisoned; `mosaic_media.io` fails without numpy).

- [ ] Commit: `Export the io surface and guard it against leaking numpy into the core`.

---

## Task 11 -- cv2 equality suite

**Files:**
- `tests/io/test_reader_cv2_equality.py` (create)

**Interfaces:**
- Consumes: `VideoReader`; `cv2` behind `pytest.importorskip("cv2")` (present via the `bench` group; skipped when absent). These tests are correctness checks, not benchmarks, so they run in the default suite and must not carry the `bench` marker.
- Produces: frame-for-frame comparison against `cv2.VideoCapture` for the workflow patterns that share a pixel format (full-resolution BGR): sequential, strided, and seek. Resize and grayscale are excluded because ffmpeg's scale and luma extraction do not match cv2's INTER_AREA and BGR2GRAY; those are covered by the framemd5 goldens in Tasks 5 and 6.

**Tolerance rationale (recorded divergence from the spec):** the spec's testing section calls for "frame-hash equality against `cv2.VideoCapture`". A literal hash comparison is not achievable here: system ffmpeg (6.1.1) and the ffmpeg bundled in the installed `opencv-python` wheel differ by a swscale version, so the yuv-to-bgr conversion rounds a channel by up to a unit and the exact hashes disagree. This suite therefore bounds the maximum absolute per-channel difference at 2 instead of hashing -- a deliberate, documented divergence. It still catches a wrong frame, an off-by-N seek, or a BGR/RGB channel swap (all of which produce large differences). Exact-hash duty is carried by the framemd5 goldens in Tasks 4-9, which compare the reader against ffmpeg's own decode with no swscale-version fight; this suite only adds the cross-check that cv2 and the reader agree to within conversion rounding.

### Steps

- [ ] Write the tests. Create `tests/io/test_reader_cv2_equality.py`:

  Each test skips when `opencv-python` is absent (`pytest.importorskip`), then imports `cv2` inside the function body so `cv2` is statically typed by the stubs `opencv-python` ships (4.7+), not by `importorskip`'s dynamic return. A function-body import is not flagged by ruff (E402 applies to module-level imports only) and never runs at collection when the module is absent.

  ```python
  from pathlib import Path

  import numpy
  import pytest

  from mosaic_media.io.reader import VideoReader


  def _cv2_all_frames(path: Path) -> list[numpy.ndarray]:
      import cv2

      capture = cv2.VideoCapture(str(path))
      frames: list[numpy.ndarray] = []
      try:
          while True:
              ok, frame = capture.read()
              if not ok:
                  break
              frames.append(frame)
      finally:
          capture.release()
      return frames


  def _max_channel_difference(a: numpy.ndarray, b: numpy.ndarray) -> int:
      return int(numpy.abs(a.astype(numpy.int16) - b.astype(numpy.int16)).max())


  def test_sequential_matches_cv2(corpus_gop12: Path) -> None:
      pytest.importorskip("cv2")
      cv2_frames = _cv2_all_frames(corpus_gop12)
      with VideoReader(corpus_gop12) as reader:
          ours = [frame for _index, frame in reader]
      assert len(ours) == len(cv2_frames)
      for mine, theirs in zip(ours, cv2_frames):
          assert _max_channel_difference(mine, theirs) <= 2


  def test_strided_matches_cv2(corpus_gop12: Path) -> None:
      pytest.importorskip("cv2")
      cv2_frames = _cv2_all_frames(corpus_gop12)
      expected = cv2_frames[::4]
      with VideoReader(corpus_gop12, frame_step=4) as reader:
          ours = [frame for _index, frame in reader]
      assert len(ours) == len(expected)
      for mine, theirs in zip(ours, expected):
          assert _max_channel_difference(mine, theirs) <= 2


  def test_seek_matches_cv2(corpus_gop12: Path) -> None:
      pytest.importorskip("cv2")
      cv2_frames = _cv2_all_frames(corpus_gop12)
      with VideoReader(corpus_gop12) as reader:
          for target in (0, 7, 12, 15, 24, 40):
              reader.seek(target)
              ok, frame = reader.read()
              assert ok
              assert frame is not None
              assert _max_channel_difference(frame, cv2_frames[target]) <= 2
  ```

- [ ] Run and observe pass (or skip if `cv2` is not installed in the dev environment):

  ```bash
  uv run pytest tests/io/test_reader_cv2_equality.py -q
  ```

  Expected: `3 passed` when `opencv-python` is installed; `3 skipped` otherwise.

- [ ] Type-check the new test file with the `bench` group available, so `cv2`'s bundled stubs resolve:

  ```bash
  uv run --group bench basedpyright tests/io/test_reader_cv2_equality.py
  ```

  Expected: `0 errors`, no suppression. The `import cv2` sits in the function body precisely so the module is typed by its own stubs rather than by `importorskip`.

- [ ] Commit: `Compare the reader against cv2 for the shared-format workflows`.

---

## Task 12 -- README prior-art paragraph

**Files:**
- `README.md` (edit "Why the reader comes here too")

**Interfaces:**
- Consumes: nothing.
- Produces: a prior-art paragraph naming moviepy's `FFMPEG_VideoReader` (MIT) and imageio-ffmpeg (BSD-2) as the adopted subprocess architecture, and stating this reader's improvement (packet-index-exact seeking). This mirrors the attribution already carried in the reader module docstring so the design reads as adopted practice.

### Steps

- [ ] Add the paragraph. In `README.md`, at the end of the "Why the reader comes here too" section (after the sentence ending "a well-known source of off-by-N frame errors."), insert:

  ```markdown

  The reader's subprocess architecture -- one persistent ffmpeg process for
  sequential reads, respawned with an input `-ss` for a discontinuous seek -- is
  adopted from established practice: moviepy's `FFMPEG_VideoReader` (MIT) and
  imageio-ffmpeg (BSD-2) both read frames this way. It improves on both by seeking
  against the exact packet index rather than by timestamp guesswork: the preceding
  keyframe of a target frame is known, so a seek respawns at that keyframe and
  discards a known number of frames, landing frame-exact. That is the structural
  fix for OpenCV's off-by-N seeking.
  ```

- [ ] Confirm the paragraph reads correctly in context:

  ```bash
  uv run python -c "import pathlib; text = pathlib.Path('README.md').read_text(); assert 'imageio-ffmpeg (BSD-2)' in text and 'FFMPEG_VideoReader' in text; print('prior-art paragraph present')"
  ```

  Expected: `prior-art paragraph present`.

- [ ] Commit: `Credit the subprocess reader prior art in the README`.

---

## Task 13 -- full verification

**Files:** none (verification only).

**Interfaces:** the whole `io` surface, the divergent probe, the corpus helpers, and the import guard.

### Steps

- [ ] Format and lint:

  ```bash
  uv run ruff format src/ tests/
  uv run ruff check src/ tests/
  ```

  Expected: `ruff format` reports files unchanged (or reformats and is then clean); `ruff check` reports `All checks passed!`.

- [ ] Type-check the full source and test tree:

  ```bash
  uv run basedpyright src/ tests/
  ```

  Expected: `0 errors, 0 warnings, 0 informations`, with no `# noqa`, `# pyright: ignore`, `typing.Any`, `typing.Optional`, or `typing.cast` anywhere in the new code. If any suppression seems necessary, redesign the typing (narrow `subprocess.Popen[bytes].stdout` to `io.BufferedReader` with an `isinstance` check for `readinto`; import `cv2` in the function body so its own stubs type it). Run the whole-tree check with the `bench` group available (`uv run --group bench basedpyright src/ tests/`) so `cv2` resolves in the equality suite.

- [ ] Run the full default suite through the serialized heavy path:

  ```bash
  heavy uv run pytest
  ```

  Expected: every test passes (probe, io index, corpus, sequential, strided, seek, sparse, writer, multi, cv2-equality, import guard). The `bench` marker is excluded by default, so no perf test runs here. Trust the final `heavy-task: exit-status=0` line. If the harness auto-backgrounds this run, re-block on it in the foreground with `heavy --wait <logpath>`; never end the turn with a heavy run outstanding.

- [ ] Read the heavy log and confirm the exit status:

  ```bash
  cat <logpath>
  ```

  Expected: the pytest summary shows all tests passed (cv2-equality either passed or skipped depending on whether `opencv-python` is installed in the dev environment) and `heavy-task: exit-status=0`.

- [ ] Commit any formatting-only changes: `Format and finalize the io subpackage`.

---

## Self-review (performed before returning this plan)

1. **Spec coverage.** Every `io`-related spec and brief requirement maps to a task:
   - Packet `pos` divergence and probe-test updates -> Task 1.
   - Seek index (preceding keyframe, frame timestamps, GOP grouping) -> Task 2.
   - Corpus generator, `corpus_gop12`/`corpus_gop250`, framemd5 goldens -> Task 3.
   - `VideoReader` persistent process, `F_SETPIPE_SZ` pipe enlargement, `readinto` preallocated buffers, `select`/`scale`/`bgr24`/`gray`, injected facts/index with lazy probe -> Tasks 4, 5.
   - Rotation handling: autorotation kept on to match ffmpeg and cv2 defaults, displayed dimensions swapped for a quarter-turn source in both the facts-injected and self-probed paths, verified by shape and framemd5 on a rotated corpus clip -> Task 4.
   - Environment: the `io` extra is synced before the first numpy-importing test, and the standing environment is recorded in Global Constraints -> Task 3.
   - Frame-exact seek (respawn at keyframe vs live discard) -> Task 6.
   - GOP-grouped sparse batch -> Task 7.
   - `FFmpegVideoWriter` absorbed and restyled -> Task 8.
   - `MultiVideoReader` with `mosaic` semantics and `uniform_properties` -> Task 9.
   - `io/__init__` exports, lazy numpy, core facade does not re-export `io`, import-guard interaction -> Task 10.
   - Correctness via framemd5 goldens (sequential, strided, seek including on-keyframe, sparse, grayscale, resize shape, multi-boundary) -> Tasks 4-9; cv2-equality suite behind `importorskip` -> Task 11; writer round-trip -> Task 8.
   - README prior-art paragraph and reader-docstring attribution -> Tasks 4, 12.
   - Full verification (ruff, basedpyright, heavy pytest) -> Task 13.
2. **No placeholders.** Every code step contains complete code; there are no "TBD", "add error handling", or "similar to Task N" stubs.
3. **Signature consistency.** The `VideoReader`, `MultiVideoReader`, `FFmpegVideoWriter`, `SeekIndex`, `build_seek_index`, `generate_video`, and `decode_md5s` signatures are identical across the Interfaces blocks, the implementation code, and the tests, and match the pinned contracts in the brief.
4. **No duplicated infrastructure.** The corpus builder reuses `tests.helpers.media_fixtures.build` rather than a second ffmpeg-runner (`decode_md5s` keeps its own capture only because it must read stdout, documented in a comment); the import-guard tests call plan 1's shared `_run_guarded(body, *, forbidden_root=...)` rather than a private numpy-only copy.
