from pathlib import Path

import pytest

from mosaic_media.probe.errors import MediaProbeError
from mosaic_media.probe.ffprobe import (
    PAYLOAD_HASH_ALGORITHM,
    read_header,
    scan_command,
    scan_packets,
)


def test_header_reads_container_codec_and_measured_geometry(
    clips: dict[str, Path],
) -> None:
    header = read_header(clips["cfr_mp4"])
    assert header.container == "mov,mp4,m4a,3gp,3g2,mj2"
    assert header.codec_name == "h264"
    assert (header.width, header.height) == (320, 240)
    assert header.rotation_degrees == 0
    assert header.square_pixels
    assert header.progressive
    assert not header.has_audio
    assert header.video_stream_count == 1


def test_header_reads_rotation_from_side_data_not_from_a_stream_tag(
    clips: dict[str, Path],
) -> None:
    # ffmpeg moved rotation into display-matrix side data; a `rotate` stream tag
    # does not exist on any current ffmpeg. This is the check loopy gets wrong.
    header = read_header(clips["rotated_mp4"])
    assert header.rotation_degrees == 90
    assert (header.width, header.height) == (320, 240)


def test_header_detects_non_square_pixels(clips: dict[str, Path]) -> None:
    assert not read_header(clips["anamorphic_mp4"]).square_pixels


def test_header_detects_audio(clips: dict[str, Path]) -> None:
    assert read_header(clips["audio_mp4"]).has_audio


def test_declared_fps_comes_from_avg_frame_rate(clips: dict[str, Path]) -> None:
    assert read_header(clips["cfr_mp4"]).declared_fps == pytest.approx(25.0)


def test_a_file_with_no_video_stream_raises(clips: dict[str, Path]) -> None:
    with pytest.raises(MediaProbeError, match="no video stream"):
        _ = read_header(clips["no_video"])


def test_a_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(MediaProbeError):
        _ = read_header(tmp_path / "absent.mp4")


def test_scan_packets_returns_times_sizes_and_keyframe_flags(
    clips: dict[str, Path],
) -> None:
    packets, source = scan_packets(clips["cfr_mp4"], video_position=0)
    assert source == "pts"
    assert len(packets) == 50
    assert sum(1 for packet in packets if packet.keyframe) >= 1
    assert all(packet.size > 0 for packet in packets)


def test_scan_packets_falls_back_to_dts_when_pts_is_absent(
    clips: dict[str, Path],
) -> None:
    # An AVI remuxed from mp4 reports `pts_time=N/A` on every packet. Without the
    # fallback, unmeasurable is silently read as variable, condemning a
    # constant-rate file to a lossless re-encode it does not need.
    packets, source = scan_packets(clips["no_pts_avi"], video_position=0)
    assert source == "dts"
    assert len(packets) == 50
    times = [packet.time for packet in packets]
    assert times == sorted(times)
    assert times[1] - times[0] == pytest.approx(0.04)


def test_scan_packets_prefers_pts_when_both_are_present(
    clips: dict[str, Path],
) -> None:
    # An AVI encoded straight from a filter source keeps its PTS, so it cannot
    # exercise the fallback. Pinning that here stops the fallback test from being
    # rewritten against a file that never takes the branch it names.
    _packets, source = scan_packets(clips["mjpeg_avi"], video_position=0)
    assert source == "pts"


def test_packet_csv_column_order_is_pts_dts_size_pos_flags_data_hash(
    clips: dict[str, Path],
) -> None:
    # ffprobe emits -show_entries fields in its own natural order, not the order
    # requested. If a future ffmpeg reorders them the parser silently mis-reads
    # a column. This test is the canary for the six-field scan, and it issues
    # the same command scan_packets does so the two can never diverge.
    import subprocess

    command = scan_command(clips["cfr_mp4"], video_position=0)
    first = subprocess.run(
        command, capture_output=True, text=True, timeout=60
    ).stdout.splitlines()[0]
    columns = first.split(",")
    assert len(columns) >= 6  # not == 6: MPEG-TS appends a seventh
    assert float(columns[0]) >= 0.0  # pts_time
    assert columns[2].isdigit()  # size
    assert columns[3].lstrip("-").isdigit()  # pos
    assert "K" in columns[4] or "_" in columns[4]  # flags
    assert columns[5].startswith(f"{PAYLOAD_HASH_ALGORITHM}:")  # data_hash


def test_scan_packets_populates_byte_offset(clips: dict[str, Path]) -> None:
    packets, _source = scan_packets(clips["cfr_mp4"], video_position=0)
    # The first packet of an mp4 sits at a small positive byte offset; every
    # packet in a well-formed mp4 has a known position.
    assert packets[0].pos >= 0
    assert all(packet.pos >= 0 for packet in packets)
    # Positions are distinct: no two packets share a byte offset.
    assert len({packet.pos for packet in packets}) == len(packets)


def test_scan_packets_populates_payload_hash(clips: dict[str, Path]) -> None:
    packets, _source = scan_packets(clips["cfr_mp4"], video_position=0)
    assert packets
    for packet in packets:
        assert packet.data_hash.startswith("CRC32:")
        assert len(packet.data_hash) > len("CRC32:")


def test_scan_packets_populates_payload_hash_on_awkward_containers(
    clips: dict[str, Path],
) -> None:
    # no_pts_avi reports pts_time=N/A and raw_h264 reports both timestamps
    # absent. Both must still carry a payload hash: the column is positional and
    # independent of which timestamp survived. mpegts_ts exercises the seven-
    # column row the row guard tolerates.
    for name in ("no_pts_avi", "raw_h264", "mjpeg_avi", "vp8_webm", "mpegts_ts"):
        packets, _source = scan_packets(clips[name], video_position=0)
        assert packets, name
        assert all(packet.data_hash.startswith("CRC32:") for packet in packets), name


def test_scan_packets_names_a_missing_payload_hash_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An ffprobe that accepts the flag but does not report the data_hash entry
    # emits five columns. Every row is then unusable, and the failure must name
    # the cause rather than claiming the file has no packets.
    def five_column_rows(_command: list[str], **_keywords: object) -> str:
        return "0.000000,0.000000,3837,48,K__\n0.040000,0.040000,120,3885,___\n"

    monkeypatch.setattr(
        "mosaic_media.probe.ffprobe.run_to_completion", five_column_rows
    )
    with pytest.raises(MediaProbeError, match="does not report data_hash"):
        _ = scan_packets(tmp_path / "any.mp4", video_position=0)
