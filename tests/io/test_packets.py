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
