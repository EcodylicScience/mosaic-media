"""Read N ordered video files as one global frame space. Requires numpy and av.

Segment 0 owns global frames [0, N0), segment 1 owns [N0, N0 + N1), and so on.
Each file is probed once at construction -- or not at all when the caller
injects `facts`, a sequence parallel to the paths: consumers hold MediaFacts
from ingestion and measurement is never re-derived, so an injected open pays
no ffprobe subprocess. `indices` likewise injects per-segment seek indices;
segments without one build it from an in-process packet scan on first use.
Uniformity across the sequence is validated with the probe's
uniform_properties, the same check the arrangement layer uses, so a resolution
or frame-rate mismatch is rejected at construction.
"""

import bisect
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy

from ..probe.errors import MediaProbeError
from ..probe.facts import MediaFacts
from ..probe.probe import probe_media
from ..probe.sequence import MeasuredVideoProperties, uniform_properties
from .index import SeekIndex, build_seek_index
from .packets import scan_packets_in_process
from .reader import VideoReader


def _displayed_dimensions(facts: MediaFacts) -> tuple[int, int]:
    """The (width, height) a VideoReader emits for `facts`. The reader
    autorotates in its conversion graph, so a quarter-turn source is
    displayed with its coded width and height swapped. The sequence must record
    and compare that displayed orientation, not the coded one: an upright clip
    and a quarter-turned clip of equal coded size are uniform on the coded
    numbers yet emit transposed frames, so the coded comparison would admit a
    sequence the reader cannot stitch."""
    if facts.rotation_degrees % 180 == 90:
        return facts.height, facts.width
    return facts.width, facts.height


@dataclass(frozen=True, slots=True)
class VideoSegment:
    path: Path
    frame_count: int
    fps: float
    width: int
    height: int
    start_frame: int


class MultiVideoReader:
    def __init__(
        self,
        video_paths: list[Path] | Path | str,
        *,
        facts: Sequence[MediaFacts] | None = None,
        indices: Sequence[SeekIndex] | None = None,
    ) -> None:
        self._closed: bool = False
        self._reader: VideoReader | None = None
        if isinstance(video_paths, (str, Path)):
            paths = [Path(video_paths)]
        else:
            paths = [Path(entry) for entry in video_paths]
        if not paths:
            message = "at least one video path is required"
            raise ValueError(message)
        if facts is not None and len(facts) != len(paths):
            message = (
                f"facts length {len(facts)} does not match "
                f"video path count {len(paths)}"
            )
            raise ValueError(message)
        if indices is not None and len(indices) != len(paths):
            message = (
                f"indices length {len(indices)} does not match "
                f"video path count {len(paths)}"
            )
            raise ValueError(message)

        self._segments: list[VideoSegment] = []
        self._segment_starts: list[int] = []
        self._facts: list[MediaFacts] = []
        self._indices: list[SeekIndex | None] = (
            [None] * len(paths) if indices is None else list(indices)
        )
        properties: list[MeasuredVideoProperties] = []
        cumulative = 0
        for position, path in enumerate(paths):
            resolved = path.expanduser().resolve()
            file_facts = facts[position] if facts is not None else probe_media(resolved)
            self._facts.append(file_facts)
            display_width, display_height = _displayed_dimensions(file_facts)
            self._segments.append(
                VideoSegment(
                    path=resolved,
                    frame_count=file_facts.frame_count,
                    fps=file_facts.fps,
                    width=display_width,
                    height=display_height,
                    start_frame=cumulative,
                )
            )
            properties.append(
                MeasuredVideoProperties(
                    fps=file_facts.fps,
                    width=display_width,
                    height=display_height,
                    frame_count=file_facts.frame_count,
                    duration=file_facts.duration,
                )
            )
            self._segment_starts.append(cumulative)
            cumulative += file_facts.frame_count

        mismatch = uniform_properties(properties)
        if mismatch is not None:
            message = (
                f"property mismatch across sequence: {mismatch.field} "
                f"{mismatch.first} vs {mismatch.other}"
            )
            raise ValueError(message)

        self._total_frames: int = cumulative
        self._current_segment: int = 0
        self._global_frame: int = 0

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
                f"global frame {global_frame} out of range [0, {self._total_frames})"
            )
            raise IndexError(message)
        index = bisect.bisect_right(self._segment_starts, global_frame) - 1
        return index, global_frame - self._segment_starts[index]

    # --- Open / seek / read ---

    def _segment_index(self, segment_index: int) -> SeekIndex:
        """The segment's packet index, built on first open and cached. Reopening
        a segment on a later seek or boundary crossing reuses the cached index
        instead of rescanning the file."""
        cached = self._indices[segment_index]
        if cached is not None:
            return cached
        packets, _source = scan_packets_in_process(self._segments[segment_index].path)
        built = build_seek_index(
            packets, source="in_process", space="container_default"
        )
        self._indices[segment_index] = built
        return built

    def _open_segment(self, segment_index: int, local_seek: int) -> None:
        if self._reader is not None:
            self._reader.close()
        self._reader = VideoReader(
            self._segments[segment_index].path,
            facts=self._facts[segment_index],
            index=self._segment_index(segment_index),
        )
        self._current_segment = segment_index
        if local_seek:
            self._reader.seek(local_seek)

    def seek(self, global_frame: int) -> None:
        if self._closed:
            message = "reader is closed"
            raise MediaProbeError(message)
        segment_index, local_frame = self.segment_for_frame(global_frame)
        if self._reader is not None and segment_index == self._current_segment:
            # The segment is already open: delegate to the open reader's seek,
            # which reuses the live decoder when the target lies between its
            # position and the target's keyframe, instead of closing and
            # reconstructing the reader on every call.
            self._reader.seek(local_frame)
        else:
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
