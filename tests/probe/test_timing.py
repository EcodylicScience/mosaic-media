import pytest

from mosaic_media.probe.errors import MediaProbeError
from mosaic_media.probe.ffprobe import Packet
from mosaic_media.probe.timing import is_truncated, measure_timing


def uniform(count: int, fps: float) -> tuple[Packet, ...]:
    return tuple(
        Packet(time=index / fps, size=100, keyframe=index == 0)
        for index in range(count)
    )


def test_a_uniform_grid_is_constant_rate() -> None:
    timing = measure_timing(uniform(250, 25.0), drift_frame_periods=0.5)
    assert timing.constant_frame_rate
    assert timing.fps == pytest.approx(25.0)
    assert timing.frame_count == 250
    assert timing.duration == pytest.approx(10.0)
    assert timing.max_instantaneous_fps is None


def test_millisecond_quantization_stays_constant_rate() -> None:
    # board.wmv and zeromaze.wmv are ordinary constant-rate recordings whose
    # container quantizes timestamps to milliseconds. vfrdet calls them 73% and
    # 88% variable; the drift fit must not.
    packets = tuple(
        Packet(time=round(index / 29.97, 3), size=100, keyframe=index == 0)
        for index in range(450)
    )
    assert measure_timing(packets, drift_frame_periods=0.5).constant_frame_rate


def test_a_dropped_frame_makes_it_variable() -> None:
    times = [index / 25.0 for index in range(100)]
    times = times[:50] + [value + 3 / 25.0 for value in times[50:]]
    packets = tuple(Packet(time=value, size=100, keyframe=False) for value in times)
    timing = measure_timing(packets, drift_frame_periods=0.5)
    assert not timing.constant_frame_rate
    assert timing.max_instantaneous_fps is not None


def test_duplicate_timestamps_are_deduplicated() -> None:
    # VP8 invisible alt-ref frames share a presentation timestamp with the frame
    # they precede. Behavioral Despair...webm has 533 of them in 9603 packets.
    packets = uniform(100, 25.0) + (Packet(time=0.0, size=10, keyframe=False),)
    assert measure_timing(packets, drift_frame_periods=0.5).frame_count == 100


def test_out_of_order_timestamps_are_sorted() -> None:
    packets = tuple(reversed(uniform(100, 25.0)))
    assert measure_timing(packets, drift_frame_periods=0.5).fps == pytest.approx(25.0)


def test_too_few_packets_raises() -> None:
    with pytest.raises(MediaProbeError):
        _ = measure_timing(uniform(1, 25.0), drift_frame_periods=0.5)


def test_truncation_is_measured_against_declared_duration() -> None:
    # A truncated mp4 reads 0.504, a truncated AVI 0.920 because AVI rebuilds its
    # duration from the surviving index. Every genuine file is at or above 0.980.
    assert is_truncated(5.04, 10.0, ratio=0.95)
    assert is_truncated(5.04, 5.48, ratio=0.95)
    assert not is_truncated(23.29, 23.76, ratio=0.95)
    assert not is_truncated(1959.37, 1959.29, ratio=0.95)


def test_an_absent_declared_duration_is_not_truncation() -> None:
    assert not is_truncated(10.0, 0.0, ratio=0.95)


def test_a_jittery_boundary_frame_does_not_flip_a_constant_file() -> None:
    # A grid anchored on the first and last timestamp is forced through both, so
    # eight milliseconds of jitter on one boundary frame tilts the line and
    # inflates the deviation everywhere else. The fit is least-squares for
    # exactly this reason.
    times = [index / 25.0 for index in range(99)]
    times[-1] += 0.2 / 25.0
    packets = tuple(Packet(time=value, size=100, keyframe=False) for value in times)
    assert measure_timing(packets, drift_frame_periods=0.5).constant_frame_rate


def test_a_jittery_first_frame_does_not_flip_a_constant_file() -> None:
    times = [index / 25.0 for index in range(99)]
    times[0] -= 0.2 / 25.0
    packets = tuple(Packet(time=value, size=100, keyframe=False) for value in times)
    assert measure_timing(packets, drift_frame_periods=0.5).constant_frame_rate


def test_a_non_uniform_middle_is_variable_even_with_endpoints_on_the_grid() -> None:
    # Least squares must not forgive a real defect. Both endpoints sit exactly on
    # a 25 fps grid while the interior is crammed into fifty milliseconds.
    times = [index / 25.0 for index in range(50)]
    times += [2.0 + (index + 1) * 0.001 for index in range(48)]
    times += [4.0]
    packets = tuple(Packet(time=value, size=100, keyframe=False) for value in times)
    timing = measure_timing(packets, drift_frame_periods=0.5)
    assert not timing.constant_frame_rate
    assert timing.max_drift_frame_periods > 10.0


def test_millisecond_quantization_stays_constant_over_a_long_file() -> None:
    # Quantization error does not accumulate: the fitted slope absorbs it.
    times = [round(index / (30000 / 1001), 3) for index in range(9000)]
    packets = tuple(Packet(time=value, size=100, keyframe=False) for value in times)
    timing = measure_timing(packets, drift_frame_periods=0.5)
    assert timing.constant_frame_rate
    assert timing.max_drift_frame_periods < 0.1
