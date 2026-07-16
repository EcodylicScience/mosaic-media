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


def test_read_after_read_frames_continues_correctly(corpus_gop12: Path) -> None:
    # After draining a sparse read the positioned cursor must be consistent, so
    # the next read returns the frame following the last sparse target with a
    # matching reported index -- not the next source frame mislabeled. read_batch
    # drives read() and reports the index it read alongside the frame.
    goldens = decode_md5s(corpus_gop12)
    with VideoReader(corpus_gop12) as reader:
        sparse = list(reader.read_frames([10, 20]))
        assert [index for index, _ in sparse] == [10, 20]
        indices, frames = reader.read_batch(1)
    assert indices.shape == (1,)
    reported = int(indices[0])
    assert reported == 21
    assert frame_md5(frames[0]) == goldens[reported]


def test_read_after_abandoned_read_frames_continues_correctly(
    corpus_gop12: Path,
) -> None:
    goldens = decode_md5s(corpus_gop12)
    with VideoReader(corpus_gop12) as reader:
        for index, _frame in reader.read_frames([10, 20]):
            assert index == 10
            break  # abandon the generator after the first yield
        indices, frames = reader.read_batch(1)
    assert indices.shape == (1,)
    reported = int(indices[0])
    assert reported == 11
    assert frame_md5(frames[0]) == goldens[reported]
