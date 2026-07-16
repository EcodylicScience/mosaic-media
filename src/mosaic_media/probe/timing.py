"""Does frame index `i` land at time `i / fps`?

That is the property the application depends on, and the only one that separates
a genuinely variable frame rate from a container that quantizes timestamps to
milliseconds. Neither ffmpeg's `vfrdet` filter nor raw packet deltas do.
"""

from dataclasses import dataclass

from .errors import MediaProbeError
from .ffprobe import Packet

_MINIMUM_PACKETS = 2


@dataclass(frozen=True, slots=True)
class Timing:
    fps: float
    frame_count: int
    duration: float
    constant_frame_rate: bool
    max_instantaneous_fps: float | None
    max_drift_frame_periods: float


def measure_timing(packets: tuple[Packet, ...], drift_frame_periods: float) -> Timing:
    """Fit a uniform grid over every packet timestamp and measure the worst
    deviation, in frame periods.

    Sorts, because B-frames arrive out of presentation order. Deduplicates,
    because VP8 invisible alt-ref frames share a timestamp with their successor.
    A bounded window is not enough: fitting the first 15 seconds misclassifies a
    quarter of the browser-decodable corpus, since a recording drops frames when
    the machine gets busy, not at the start.

    The grid fit is a single pass in plain Python. On the largest file measured,
    286256 packets, it takes 81 ms against 1.5 seconds for the ffprobe call that
    produced them, and 10 seconds when that read is cold. numpy would do the
    same arithmetic in 1 ms, but it would buy under one percent of the whole
    probe while adding a dependency this package does not declare and does not
    need. The core stays standard library only for the CLI's sake: the
    transcode runner has to start on a machine that has ffmpeg and nothing
    else -- a minimal container, or a tracking box without the analysis stack.
    """
    # Distinct and ascending: `sorted` because B-frames arrive out of
    # presentation order, `set` because VP8 alt-ref frames share a timestamp.
    # Both together guarantee the span below is strictly positive.
    times = sorted({packet.time for packet in packets})
    if len(times) < _MINIMUM_PACKETS:
        message = f"too few distinct packet timestamps to measure timing: {len(times)}"
        raise MediaProbeError(message)

    frame_count = len(times)
    span = times[-1] - times[0]
    # The average rate over the whole file. `frame_count / duration` reproduces
    # it exactly, which is what lets a sequence carry one canonical rate.
    fps = (frame_count - 1) / span

    # Deviation of each timestamp from a uniform grid anchored on the first,
    # measured in frame periods. This is exactly the question a client asks
    # when it seeks to `frame_index / fps`.
    origin = times[0]
    drift = max(
        abs((value - origin) - index / fps) * fps for index, value in enumerate(times)
    )
    constant = drift < drift_frame_periods

    max_instantaneous_fps: float | None = None
    if not constant:
        deltas = [later - earlier for earlier, later in zip(times, times[1:])]
        # A constant-rate file has max_instantaneous_fps equal to fps by
        # definition, so the value only carries information for a variable one.
        # Distinct ascending timestamps make every delta strictly positive.
        max_instantaneous_fps = 1.0 / min(deltas)

    duration = span * frame_count / (frame_count - 1)
    return Timing(
        fps=fps,
        frame_count=frame_count,
        duration=duration,
        constant_frame_rate=constant,
        max_instantaneous_fps=max_instantaneous_fps,
        max_drift_frame_periods=drift,
    )


def is_truncated(
    measured_duration: float, declared_duration: float, ratio: float
) -> bool:
    """True when content is missing, as distinct from a header that lies about
    content which is entirely present.

    The two look identical in the frame count and demand opposite repairs: a
    remux fixes a lying header, while re-encoding a truncated file faithfully
    preserves the truncation.
    """
    if declared_duration <= 0.0:
        return False
    return measured_duration / declared_duration < ratio
