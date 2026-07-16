from pathlib import Path

from mosaic_media.io.reader import VideoReader
from tests.helpers.corpus import decode_md5s, frame_md5


def test_read_frames_sparse_is_exact(corpus_gop12: Path) -> None:
    goldens = decode_md5s(corpus_gop12)
    wanted = [5, 7, 13, 20, 25, 45]
    with VideoReader(corpus_gop12) as reader:
        produced = {
            index: frame_md5(frame) for index, frame in reader.read_frames(wanted)
        }
    assert set(produced) == set(wanted)
    for index in wanted:
        assert produced[index] == goldens[index]


def test_read_frames_sorts_and_deduplicates(corpus_gop12: Path) -> None:
    goldens = decode_md5s(corpus_gop12)
    with VideoReader(corpus_gop12) as reader:
        produced = list(reader.read_frames([20, 5, 20, 13, 5]))
    indices = [index for index, _ in produced]
    assert indices == [5, 13, 20]
    for index, frame in produced:
        assert frame_md5(frame) == goldens[index]


def test_read_frames_within_one_gop(corpus_gop12: Path) -> None:
    goldens = decode_md5s(corpus_gop12)
    wanted = [12, 14, 16, 18]  # all in the GOP starting at keyframe 12
    with VideoReader(corpus_gop12) as reader:
        produced = list(reader.read_frames(wanted))
    assert [index for index, _ in produced] == wanted
    for index, frame in produced:
        assert frame_md5(frame) == goldens[index]


def test_read_frames_empty(corpus_gop12: Path) -> None:
    with VideoReader(corpus_gop12) as reader:
        assert list(reader.read_frames([])) == []
