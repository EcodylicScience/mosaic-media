import json
from pathlib import Path

import pytest

from mosaic_media.probe.errors import MediaProbeError
from mosaic_media.probe.ffprobe import (
    PAYLOAD_HASH_ALGORITHM,
    elementary_stream_fps,
    parse_fraction,
    prober_version,
    read_header,
    scan_command,
    scan_packets,
)


def stream_payload(**overrides: str) -> dict[str, object]:
    payload: dict[str, object] = {"r_frame_rate": "60/1"}
    payload.update(overrides)
    return payload


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


def test_parse_fraction_reads_an_absent_rate_as_zero() -> None:
    # ffprobe writes "N/A" for a value it has no answer for, and partitioning it
    # on "/" leaves "A" as the denominator.
    assert parse_fraction("N/A") == 0.0


def test_elementary_stream_fps_halves_the_h264_tick_rate() -> None:
    assert elementary_stream_fps(stream_payload(), "h264", "h264") == 30.0


def test_elementary_stream_fps_keeps_a_fractional_rate() -> None:
    payload = stream_payload(r_frame_rate="60000/1001")
    assert elementary_stream_fps(payload, "h264", "h264") == 30000 / 1001


def test_elementary_stream_fps_keeps_a_sub_one_rate() -> None:
    # A timelapse or long-observation recording is coded at a fraction of a
    # frame per second: 0.5 fps states 1/1 here. A lower plausibility bound of
    # 1.0 would discard it and send a real recording to the timestamp fallback.
    payload = stream_payload(r_frame_rate="1/1")
    assert elementary_stream_fps(payload, "h264", "h264") == 0.5


def test_elementary_stream_fps_rejects_the_demuxer_time_base() -> None:
    # A sequence parameter set with no timing makes libavformat report the
    # demuxer time base here, measured at 1200000/1 on FFmpeg 6.1, 7.1 and 8.1.
    payload = stream_payload(r_frame_rate="1200000/1")
    assert elementary_stream_fps(payload, "h264", "h264") == 0.0


def test_elementary_stream_fps_rejects_an_absent_rate() -> None:
    payload = stream_payload(r_frame_rate="N/A")
    assert elementary_stream_fps(payload, "h264", "h264") == 0.0


def test_elementary_stream_fps_rejects_a_missing_rate() -> None:
    payload: dict[str, object] = {}
    assert elementary_stream_fps(payload, "h264", "h264") == 0.0


def test_elementary_stream_fps_rejects_a_zero_denominator() -> None:
    payload = stream_payload(r_frame_rate="0/0")
    assert elementary_stream_fps(payload, "h264", "h264") == 0.0


def test_elementary_stream_fps_reads_one_tick_per_frame_for_a_raw_hevc_stream() -> None:
    # HEVC states one tick per frame where H.264 states two, so the H.264 divisor
    # does not carry over: halving here would report half the real rate. The rate
    # matters because a raw stream with none is refused rather than remuxed, and
    # a codec whose rate is never derived would be refused entirely.
    payload = stream_payload(r_frame_rate="30/1")
    assert elementary_stream_fps(payload, "hevc", "hevc") == 30.0


def test_elementary_stream_fps_rejects_the_demuxer_time_base_for_hevc() -> None:
    # The same guard H.264 has. A bitstream carrying no timing makes libavformat
    # report the demuxer time base, which is not a frame rate at all.
    payload = stream_payload(r_frame_rate="1200000/1")
    assert elementary_stream_fps(payload, "hevc", "hevc") == 0.0


def test_elementary_stream_fps_is_absent_for_a_containerized_stream() -> None:
    # A container states its own frame rate in r_frame_rate rather than a tick
    # rate, so halving it would report 12.5 for this 25 fps stream.
    payload = stream_payload(r_frame_rate="25/1")
    assert elementary_stream_fps(payload, "mov,mp4,m4a,3gp,3g2,mj2", "h264") == 0.0


def test_read_header_derives_the_rate_for_a_raw_stream(clips: dict[str, Path]) -> None:
    assert read_header(clips["raw_h264"]).elementary_stream_fps == 30.0


def test_read_header_leaves_a_containerized_stream_without_a_bitstream_rate(
    clips: dict[str, Path],
) -> None:
    # r_frame_rate is the container's own 25 fps here, not a tick rate; halving
    # it would report 12.5 for a 25 fps file.
    assert read_header(clips["cfr_mp4"]).elementary_stream_fps == 0.0


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


def test_prober_version_names_the_program_and_libavformat() -> None:
    value = prober_version()
    program, _, libavformat = value.partition(" ")
    assert program
    assert libavformat.startswith("Lavf")


def test_prober_version_is_read_once_per_process() -> None:
    assert prober_version() is prober_version()


def test_prober_version_names_a_missing_libavformat_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def without_libavformat(_command: list[str], **_keywords: object) -> str:
        return '{"program_version": {"version": "7.0"}, "library_versions": []}'

    # Cleared on both sides: warm, the body never runs and this passes against
    # any implementation; left warm afterwards, the identity test above would
    # measure a poisoned read.
    prober_version.cache_clear()
    monkeypatch.setattr(
        "mosaic_media.probe.ffprobe.run_to_completion", without_libavformat
    )
    try:
        with pytest.raises(MediaProbeError, match="libavformat"):
            _ = prober_version()
    finally:
        prober_version.cache_clear()


def test_prober_version_names_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def not_json(_command: list[str], **_keywords: object) -> str:
        return "not json"

    prober_version.cache_clear()
    monkeypatch.setattr("mosaic_media.probe.ffprobe.run_to_completion", not_json)
    try:
        with pytest.raises(MediaProbeError, match="invalid JSON"):
            _ = prober_version()
    finally:
        prober_version.cache_clear()


def test_prober_version_names_a_missing_program_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def without_program_version(_command: list[str], **_keywords: object) -> str:
        library_versions = [{"name": "libavformat", "ident": "Lavf60.16.100"}]
        return json.dumps({"library_versions": library_versions})

    prober_version.cache_clear()
    monkeypatch.setattr(
        "mosaic_media.probe.ffprobe.run_to_completion", without_program_version
    )
    try:
        with pytest.raises(MediaProbeError, match="no program version"):
            _ = prober_version()
    finally:
        prober_version.cache_clear()


def test_prober_version_does_not_mint_a_null_program_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A JSON null decoded by Python is None, and str(None) is the truthy
    # string "None" -- a value that looks measured and is not. The leaf must
    # be read through a type check, not coerced with str(...).
    def null_version(_command: list[str], **_keywords: object) -> str:
        library_versions = [{"name": "libavformat", "ident": "Lavf60.16.100"}]
        return json.dumps(
            {"program_version": {"version": None}, "library_versions": library_versions}
        )

    prober_version.cache_clear()
    monkeypatch.setattr("mosaic_media.probe.ffprobe.run_to_completion", null_version)
    try:
        with pytest.raises(MediaProbeError, match="no program version"):
            _ = prober_version()
    finally:
        prober_version.cache_clear()


def test_prober_version_does_not_mint_a_null_libavformat_ident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The companion to the null program version test, on the other leaf. Every
    # other ident in this module is either the valid string or absent through
    # an empty library_versions list, which raises before ever reaching the
    # isinstance narrowing on raw_ident -- so only an explicit null here
    # exercises that check.
    def null_ident(_command: list[str], **_keywords: object) -> str:
        library_versions = [{"name": "libavformat", "ident": None}]
        return json.dumps(
            {
                "program_version": {"version": "7.0"},
                "library_versions": library_versions,
            }
        )

    prober_version.cache_clear()
    monkeypatch.setattr("mosaic_media.probe.ffprobe.run_to_completion", null_ident)
    try:
        with pytest.raises(MediaProbeError, match="libavformat"):
            _ = prober_version()
    finally:
        prober_version.cache_clear()


def test_prober_version_names_a_non_dict_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A JSON top-level list. Without the isinstance(decoded, dict) guard,
    # payload.get(...) below would be list.get(...) and raise AttributeError,
    # which escapes every except MediaProbeError in every consumer -- the one
    # failure mode this function exists to prevent. The message matches the
    # missing-program-version test because both detect the same absence; this
    # test's job is to prove the non-dict branch is what produces it here,
    # not the dict branch.
    def non_dict_payload(_command: list[str], **_keywords: object) -> str:
        return "[]"

    prober_version.cache_clear()
    monkeypatch.setattr(
        "mosaic_media.probe.ffprobe.run_to_completion", non_dict_payload
    )
    try:
        with pytest.raises(MediaProbeError, match="no program version"):
            _ = prober_version()
    finally:
        prober_version.cache_clear()
