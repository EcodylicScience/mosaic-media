from pathlib import Path

import pytest

import mosaic_media.io.multi as multi_module
from mosaic_media.io.index import SeekIndex, build_seek_index
from mosaic_media.io.multi import MultiVideoReader
from mosaic_media.io.packets import scan_packets_in_process
from mosaic_media.io.reader import VideoReader
from mosaic_media.probe.errors import MediaProbeError
from mosaic_media.probe.facts import MediaFacts
from mosaic_media.probe.ffprobe import Packet, TimestampSource
from mosaic_media.probe.probe import probe_media
from tests.helpers.corpus import decode_md5s, frame_md5, generate_video


def _failing_probe(path: Path) -> MediaFacts:
    message = f"probe_media must not run for {path}"
    raise AssertionError(message)


@pytest.fixture(scope="module")
def two_clips(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("multi")
    first = generate_video(root / "a.mp4", frames=20, fps=30.0, gop=12)
    second = generate_video(root / "b.mp4", frames=15, fps=30.0, gop=12)
    return first, second


def test_global_frame_space_properties(two_clips: tuple[Path, Path]) -> None:
    first, second = two_clips
    with MultiVideoReader([first, second]) as reader:
        assert reader.total_frames == 35
        assert reader.video_count == 2
        assert reader.width == 320
        assert reader.height == 240
        assert len(reader) == 35
        assert [segment.start_frame for segment in reader.segments] == [0, 20]


def test_segment_for_frame(two_clips: tuple[Path, Path]) -> None:
    first, second = two_clips
    with MultiVideoReader([first, second]) as reader:
        assert reader.segment_for_frame(0) == (0, 0)
        assert reader.segment_for_frame(19) == (0, 19)
        assert reader.segment_for_frame(20) == (1, 0)
        assert reader.segment_for_frame(34) == (1, 14)
        with pytest.raises(IndexError):
            _ = reader.segment_for_frame(35)


def test_sequential_read_crosses_boundary(two_clips: tuple[Path, Path]) -> None:
    first, second = two_clips
    expected = decode_md5s(first) + decode_md5s(second)
    produced: list[str] = []
    with MultiVideoReader([first, second]) as reader:
        while True:
            ok, frame = reader.read()
            if not ok:
                break
            assert frame is not None
            produced.append(frame_md5(frame))
    assert produced == expected


def test_seek_across_boundary_then_read(two_clips: tuple[Path, Path]) -> None:
    first, second = two_clips
    expected = decode_md5s(first) + decode_md5s(second)
    with MultiVideoReader([first, second]) as reader:
        reader.seek(25)  # global frame 25 -> segment 1, local 5
        assert reader.frame_position == 25
        produced: list[str] = []
        for _ in range(5):
            ok, frame = reader.read()
            assert ok
            assert frame is not None
            produced.append(frame_md5(frame))
    assert produced == expected[25:30]


def test_single_path_accepted(two_clips: tuple[Path, Path]) -> None:
    first, _second = two_clips
    with MultiVideoReader(first) as reader:
        assert reader.video_count == 1
        assert reader.total_frames == 20


def test_resolution_mismatch_raises(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    root = tmp_path_factory.mktemp("mismatch")
    big = generate_video(root / "big.mp4", frames=10, fps=30.0, size=(320, 240))
    small = generate_video(root / "small.mp4", frames=10, fps=30.0, size=(160, 120))
    with pytest.raises(ValueError):
        _ = MultiVideoReader([big, small])


def test_empty_path_list_rejected() -> None:
    with pytest.raises(ValueError):
        _ = MultiVideoReader([])


def test_rotated_sequence_reports_displayed_dimensions(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    root = tmp_path_factory.mktemp("rot_uniform")
    first = generate_video(
        root / "a.mp4", frames=12, fps=30.0, gop=12, rotation_degrees=90
    )
    second = generate_video(
        root / "b.mp4", frames=12, fps=30.0, gop=12, rotation_degrees=90
    )
    with MultiVideoReader([first, second]) as reader:
        # Coded size is 320x240; a quarter turn is displayed as 240x320.
        assert reader.width == 240
        assert reader.height == 320
        ok, frame = reader.read()
        assert ok
        assert frame is not None
        # The emitted frame carries the displayed orientation the reader reports.
        assert frame.shape == (reader.height, reader.width, 3)


def test_mixed_rotation_with_equal_coded_dimensions_raises(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    root = tmp_path_factory.mktemp("rot_mixed")
    upright = generate_video(root / "up.mp4", frames=10, fps=30.0, gop=12)
    rotated = generate_video(
        root / "rot.mp4", frames=10, fps=30.0, gop=12, rotation_degrees=90
    )
    # Equal coded dimensions but opposite displayed orientation. The uniformity
    # check compares displayed width and height, so this sequence is rejected.
    with pytest.raises(ValueError):
        _ = MultiVideoReader([upright, rotated])


def test_seek_after_close_raises(two_clips: tuple[Path, Path]) -> None:
    first, second = two_clips
    reader = MultiVideoReader([first, second])
    reader.close()
    with pytest.raises(MediaProbeError):
        reader.seek(5)


def test_segment_packet_index_is_scanned_once(
    two_clips: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two seeks into the same segment must scan that segment's packets once: the
    # index is built on first open and cached, then injected on every reopen.
    from mosaic_media.io.packets import scan_packets_in_process as real_scan

    calls = 0

    def counting_scan(path: Path) -> tuple[tuple[Packet, ...], TimestampSource]:
        nonlocal calls
        calls += 1
        return real_scan(path)

    monkeypatch.setattr("mosaic_media.io.multi.scan_packets_in_process", counting_scan)
    first, second = two_clips
    with MultiVideoReader([first, second]) as reader:
        reader.seek(3)
        ok, _frame = reader.read()
        assert ok
        reader.seek(9)
        ok, _frame = reader.read()
        assert ok
    assert calls == 1


def test_seeks_within_the_open_segment_reuse_the_reader(
    two_clips: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Three seeks into one segment must construct one VideoReader: the second
    # and third delegate to the open reader's own seek instead of closing and
    # reconstructing it (and with it, its live decoder).
    constructed = 0

    def counting_reader(
        path: Path,
        *,
        facts: MediaFacts | None = None,
        index: SeekIndex | None = None,
    ) -> VideoReader:
        nonlocal constructed
        constructed += 1
        return VideoReader(path, facts=facts, index=index)

    monkeypatch.setattr(multi_module, "VideoReader", counting_reader)
    first, second = two_clips
    expected = decode_md5s(first)
    with MultiVideoReader([first, second]) as reader:
        for target in (3, 9, 1):
            reader.seek(target)
            ok, frame = reader.read()
            assert ok
            assert frame is not None
            assert frame_md5(frame) == expected[target]
    assert constructed == 1


def test_seek_back_after_exhaustion(two_clips: tuple[Path, Path]) -> None:
    first, second = two_clips
    expected = decode_md5s(first) + decode_md5s(second)
    with MultiVideoReader([first, second]) as reader:
        while True:
            ok, _frame = reader.read()
            if not ok:
                break
        reader.seek(30)
        ok, frame = reader.read()
        assert ok
        assert frame is not None
        assert frame_md5(frame) == expected[30]


def test_fps_mismatch_raises(tmp_path_factory: pytest.TempPathFactory) -> None:
    root = tmp_path_factory.mktemp("fps_mismatch")
    fast = generate_video(root / "fast.mp4", frames=10, fps=30.0)
    slow = generate_video(root / "slow.mp4", frames=10, fps=25.0)
    with pytest.raises(ValueError, match="fps"):
        _ = MultiVideoReader([fast, slow])


def test_injected_facts_suppress_probing(
    two_clips: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Consumers hold MediaFacts from ingestion; an injected construction must
    # not re-measure (probe_media is poisoned here) and must still read
    # frame-exact across the segment boundary.
    first, second = two_clips
    pre_facts = [probe_media(first), probe_media(second)]
    monkeypatch.setattr(multi_module, "probe_media", _failing_probe)
    expected = decode_md5s(first) + decode_md5s(second)
    with MultiVideoReader([first, second], facts=pre_facts) as reader:
        reader.seek(18)
        produced: list[str] = []
        for _ in range(4):
            ok, frame = reader.read()
            assert ok
            assert frame is not None
            produced.append(frame_md5(frame))
    assert produced == expected[18:22]


def test_injected_indices_suppress_the_packet_scan(
    two_clips: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = two_clips
    pre_facts = [probe_media(first), probe_media(second)]
    pre_indices = [
        build_seek_index(scan_packets_in_process(first)[0]),
        build_seek_index(scan_packets_in_process(second)[0]),
    ]

    def failing_scan(path: Path) -> tuple[tuple[Packet, ...], TimestampSource]:
        message = f"packet scan must not run for {path}"
        raise AssertionError(message)

    monkeypatch.setattr(multi_module, "probe_media", _failing_probe)
    monkeypatch.setattr(multi_module, "scan_packets_in_process", failing_scan)
    expected = decode_md5s(first) + decode_md5s(second)
    with MultiVideoReader(
        [first, second], facts=pre_facts, indices=pre_indices
    ) as reader:
        for target in (5, 25):
            reader.seek(target)
            ok, frame = reader.read()
            assert ok
            assert frame is not None
            assert frame_md5(frame) == expected[target]


def test_injection_length_mismatches_raise(two_clips: tuple[Path, Path]) -> None:
    first, second = two_clips
    with pytest.raises(ValueError, match="facts length"):
        _ = MultiVideoReader([first, second], facts=[probe_media(first)])
    with pytest.raises(ValueError, match="indices length"):
        _ = MultiVideoReader([first, second], indices=[])
