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
