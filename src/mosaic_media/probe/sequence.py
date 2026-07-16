"""Pure predicates over a group of files that will become one sequence."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

# Half a frame of drift across the shorter clip. The same-rig pair drifts 0.0000
# frames; a 25 fps clip beside a 29.97 fps clip drifts 1478.
_MAX_FRAME_DRIFT = 0.5


class VideoProperties(Protocol):
    """The four values uniformity needs. `MediaFacts` satisfies it structurally,
    and so does the arrangement layer's own per-file row, so neither caller has
    to fabricate a partial `MediaFacts`."""

    @property
    def fps(self) -> float: ...
    @property
    def width(self) -> int: ...
    @property
    def height(self) -> int: ...
    @property
    def frame_count(self) -> int: ...
    @property
    def duration(self) -> float: ...


@dataclass(frozen=True)
class MeasuredVideoProperties:
    """The measured properties uniform_properties compares across a sequence's
    videos. Satisfies the VideoProperties protocol structurally, and is used for
    a verified upload file's projected props, an existing sequence video's props,
    and the finalize gate's per-sequence uniformity check, so no caller has to
    fabricate a partial MediaFacts."""

    fps: float
    width: int
    height: int
    frame_count: int
    duration: float


def measured_or_none(
    fps: float | None,
    width: int | None,
    height: int | None,
    frame_count: int | None,
    duration: float | None,
) -> MeasuredVideoProperties | None:
    """Project the five measured properties into a MeasuredVideoProperties, or
    None when any is null. A partially probed upload file or a legacy Video row
    without a frame count cannot be compared, so it contributes nothing to the
    uniformity check. Frame rate and duration are coerced to float because Dolt
    loads its DOUBLE columns as Decimal, which does not mix with the float
    arithmetic in uniform_properties."""
    if (
        fps is None
        or width is None
        or height is None
        or frame_count is None
        or duration is None
    ):
        return None
    return MeasuredVideoProperties(
        fps=float(fps),
        width=width,
        height=height,
        frame_count=frame_count,
        duration=float(duration),
    )


@dataclass(frozen=True, slots=True)
class PropertyMismatch:
    field: str
    first: float
    other: float


def uniform_properties(videos: Sequence[VideoProperties]) -> PropertyMismatch | None:
    """The first measured frame rate, width or height that disagrees.

    Dimensions compare exactly. Frame rate compares with a tolerance, because a
    fitted average is not bit-reproducible: two clips from one rig at the same
    nominal rate fit 30.0 and 30.000000040365986. Exact equality would reject
    every multi-clip sequence in the project's own data.

    The tolerance is the property the sequence must satisfy. One canonical rate
    indexes every video, so video `k` is uniform when using the reference rate
    costs less than half a frame across the whole of `k`.
    """
    if not videos:
        return None
    reference = videos[0]
    for other in videos[1:]:
        for field in ("width", "height"):
            left = float(getattr(reference, field))
            right = float(getattr(other, field))
            if left != right:
                return PropertyMismatch(field=field, first=left, other=right)
        if reference.fps <= 0.0 or other.fps <= 0.0:
            return PropertyMismatch(field="fps", first=reference.fps, other=other.fps)
        frames = float(min(reference.frame_count, other.frame_count))
        drift = abs(other.fps - reference.fps) / reference.fps * frames
        if drift >= _MAX_FRAME_DRIFT:
            return PropertyMismatch(field="fps", first=reference.fps, other=other.fps)
    return None


def canonical_fps(videos: Sequence[VideoProperties]) -> float:
    """The frame-count-weighted mean rate. This is the sequence's `fps`, and the
    only rate any client should index frames with."""
    total_frames = sum(video.frame_count for video in videos)
    total_duration = sum(video.duration for video in videos)
    if total_duration <= 0.0:
        return 0.0
    return total_frames / total_duration
