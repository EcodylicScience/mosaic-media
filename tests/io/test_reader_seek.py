from pathlib import Path

import pytest

from mosaic_media.io.reader import VideoReader
from mosaic_media.probe.probe import probe_media
from tests.helpers.corpus import decode_md5s, frame_md5


def test_seek_into_gop_is_frame_exact(corpus_gop12: Path) -> None:
    goldens = decode_md5s(corpus_gop12)
    with VideoReader(corpus_gop12) as reader:
        reader.seek(15)
        ok, frame = reader.read()
    assert ok
    assert frame is not None
    assert frame_md5(frame) == goldens[15]


def test_seek_exactly_on_keyframe(corpus_gop12: Path) -> None:
    goldens = decode_md5s(corpus_gop12)
    with VideoReader(corpus_gop12) as reader:
        for keyframe in (0, 12, 24, 36):
            reader.seek(keyframe)
            ok, frame = reader.read()
            assert ok
            assert frame is not None
            assert frame_md5(frame) == goldens[keyframe]


def test_seek_then_sequential_continues(corpus_gop12: Path) -> None:
    goldens = decode_md5s(corpus_gop12)
    with VideoReader(corpus_gop12) as reader:
        reader.seek(20)
        produced: list[str] = []
        for _ in range(5):
            ok, frame = reader.read()
            assert ok
            assert frame is not None
            produced.append(frame_md5(frame))
    assert produced == goldens[20:25]


def test_monotonic_forward_seeks_reuse_live_process(corpus_gop12: Path) -> None:
    goldens = decode_md5s(corpus_gop12)
    with VideoReader(corpus_gop12) as reader:
        for target in (13, 14, 18, 30, 31):
            reader.seek(target)
            ok, frame = reader.read()
            assert ok
            assert frame is not None
            assert frame_md5(frame) == goldens[target]


def test_backward_seek_respawns_and_is_exact(corpus_gop12: Path) -> None:
    goldens = decode_md5s(corpus_gop12)
    with VideoReader(corpus_gop12) as reader:
        reader.seek(30)
        ok, frame = reader.read()
        assert ok
        assert frame is not None
        assert frame_md5(frame) == goldens[30]
        reader.seek(5)
        ok, frame = reader.read()
        assert ok
        assert frame is not None
        assert frame_md5(frame) == goldens[5]


def test_seek_out_of_range_raises(corpus_gop12: Path) -> None:
    facts = probe_media(corpus_gop12)
    with VideoReader(corpus_gop12, facts=facts) as reader:
        with pytest.raises(IndexError):
            reader.seek(facts.frame_count)
        with pytest.raises(IndexError):
            reader.seek(-1)


def test_positioned_read_stops_at_end_frame(corpus_gop12: Path) -> None:
    # A seek inside a windowed reader must still honor the window: reads step by
    # frame_step and stop at end_frame, exactly like sequential exhaustion,
    # rather than running to the end of the file.
    goldens = decode_md5s(corpus_gop12)
    expected_indices = [12, 15, 18, 21, 24, 27]
    with VideoReader(
        corpus_gop12, start_frame=10, end_frame=30, frame_step=3
    ) as reader:
        reader.seek(12)
        produced: list[str] = []
        while True:
            ok, frame = reader.read()
            if not ok:
                break
            assert frame is not None
            produced.append(frame_md5(frame))
    assert produced == [goldens[index] for index in expected_indices]


def test_seek_outside_window_raises(corpus_gop12: Path) -> None:
    with VideoReader(
        corpus_gop12, start_frame=10, end_frame=30, frame_step=3
    ) as reader:
        with pytest.raises(IndexError):
            reader.seek(9)  # below start_frame
        with pytest.raises(IndexError):
            reader.seek(30)  # at the exclusive end bound
        with pytest.raises(IndexError):
            reader.seek(31)  # above the end bound


def test_deep_seek_into_long_gop_is_frame_exact(corpus_gop250: Path) -> None:
    # gop=250 over 300 frames: frame 240 sits deep inside the first keyframe's
    # group, so the seek respawns at frame 0 and discards 240 frames. The land
    # must still be frame-exact against the golden.
    goldens = decode_md5s(corpus_gop250)
    with VideoReader(corpus_gop250) as reader:
        reader.seek(240)
        ok, frame = reader.read()
    assert ok
    assert frame is not None
    assert frame_md5(frame) == goldens[240]


def test_sparse_read_within_long_gop(corpus_gop250: Path) -> None:
    # All three targets share the frame-0 keyframe, so read_frames decodes them
    # in a single forward pass through the giant GOP.
    goldens = decode_md5s(corpus_gop250)
    targets = [180, 210, 240]
    with VideoReader(corpus_gop250) as reader:
        produced = {
            index: frame_md5(frame) for index, frame in reader.read_frames(targets)
        }
    assert produced == {index: goldens[index] for index in targets}
