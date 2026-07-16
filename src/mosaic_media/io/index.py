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

from ..probe.ffprobe import Packet


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
                f"frame index {frame_index} out of range [0, {len(self.frame_times)})"
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
