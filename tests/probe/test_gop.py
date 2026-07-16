from mosaic_media.probe.ffprobe import Packet
from mosaic_media.probe.gop import measure_gop


def test_gop_bytes_and_frames_are_the_worst_interval() -> None:
    packets = (
        Packet(time=0.0, size=1000, keyframe=True),
        Packet(time=0.04, size=10, keyframe=False),
        Packet(time=0.08, size=10, keyframe=False),
        Packet(time=0.12, size=5000, keyframe=True),
        Packet(time=0.16, size=10, keyframe=False),
    )
    stats = measure_gop(packets)
    assert stats.max_gop_bytes == 5010
    assert stats.max_keyframe_interval_frames == 3


def test_a_file_with_no_keyframe_flag_is_one_interval() -> None:
    packets = tuple(
        Packet(time=index / 25.0, size=100, keyframe=False) for index in range(10)
    )
    stats = measure_gop(packets)
    assert stats.max_gop_bytes == 1000
    assert stats.max_keyframe_interval_frames == 10


def test_intervals_follow_decode_order_not_presentation_order() -> None:
    # scan_packets yields packets in decode order, which is not presentation
    # order once B-frames reorder the stream. A player fetches and decodes in
    # decode order, so the worst interval must be measured in it. Here a large
    # frame is decoded early (right after the first keyframe) but presented late,
    # placing it in the first GOP in decode order and the second GOP once sorted
    # by time. The two orders give different answers; only decode order is right.
    packets = (
        Packet(time=0.0, size=100, keyframe=True),
        Packet(time=0.20, size=9000, keyframe=False),
        Packet(time=0.10, size=100, keyframe=True),
        Packet(time=0.30, size=100, keyframe=False),
    )
    stats = measure_gop(packets)
    assert stats.max_gop_bytes == 9100
    assert stats.max_keyframe_interval_frames == 2
    # Sorting by time would move the large frame into the second GOP, yielding
    # 9200 bytes across 3 frames instead; the assertions above pin decode order.


def test_frequent_keyframes_and_a_high_bitrate_still_show_a_large_payload() -> None:
    # hex_3.mp4.bk carries keyframes every 24 frames and still costs 3.89 MiB per
    # seek, because it runs at roughly 60 Mbit/s. A keyframe-interval metric alone
    # would pass it. This is why max_gop_bytes is the primary signal.
    packets = tuple(
        Packet(time=index / 50.0, size=170_000, keyframe=index % 24 == 0)
        for index in range(48)
    )
    stats = measure_gop(packets)
    assert stats.max_keyframe_interval_frames == 24
    assert stats.max_gop_bytes > 2 * 1024 * 1024
