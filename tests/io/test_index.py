from mosaic_media.io.index import build_seek_index
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
        _ = index.preceding_keyframe(40)
    with pytest.raises(IndexError):
        _ = index.preceding_keyframe(-1)


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
