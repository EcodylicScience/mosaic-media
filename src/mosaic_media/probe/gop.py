"""What a seek costs: bytes to fetch, and frames to decode.

Two independent costs. A 5 second keyframe interval in a 0.14 MiB payload is
free; a 16.7 second interval in an 18.6 MiB payload stalls for a second and a
half. Keyframe interval alone is a poor proxy for either.
"""

from dataclasses import dataclass

from .ffprobe import Packet


@dataclass(frozen=True, slots=True)
class GopStats:
    max_gop_bytes: int
    max_keyframe_interval_frames: int


def measure_gop(packets: tuple[Packet, ...]) -> GopStats:
    """Walk packets in decode order, accumulating between keyframes.

    Decode order is what a player fetches and decodes, so the intervals are
    measured in it rather than in presentation order.
    """
    worst_bytes = 0
    worst_frames = 0
    current_bytes = 0
    current_frames = 0
    for packet in packets:
        if packet.keyframe:
            worst_bytes = max(worst_bytes, current_bytes)
            worst_frames = max(worst_frames, current_frames)
            current_bytes = 0
            current_frames = 0
        current_bytes += packet.size
        current_frames += 1
    return GopStats(
        max_gop_bytes=max(worst_bytes, current_bytes),
        max_keyframe_interval_frames=max(worst_frames, current_frames),
    )
