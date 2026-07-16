from pathlib import Path

import numpy
import pytest

from mosaic_media.io.reader import VideoReader
from mosaic_media.probe.probe import probe_media
from tests.helpers.corpus import decode_md5s, frame_md5, generate_video


def test_sequential_read_equals_framemd5_golden(corpus_gop12: Path) -> None:
    goldens = decode_md5s(corpus_gop12)
    produced: list[str] = []
    with VideoReader(corpus_gop12) as reader:
        while True:
            ok, frame = reader.read()
            if not ok:
                break
            assert frame is not None
            produced.append(frame_md5(frame))
    assert produced == goldens


def test_iter_yields_index_and_frame(corpus_gop12: Path) -> None:
    goldens = decode_md5s(corpus_gop12)
    with VideoReader(corpus_gop12) as reader:
        pairs = [(index, frame_md5(frame)) for index, frame in reader]
    assert [index for index, _ in pairs] == list(range(len(goldens)))
    assert [digest for _, digest in pairs] == goldens


def test_frame_shape_and_dtype_are_bgr_uint8(corpus_gop12: Path) -> None:
    with VideoReader(corpus_gop12) as reader:
        ok, frame = reader.read()
    assert ok
    assert frame is not None
    assert frame.shape == (reader.height, reader.width, 3)
    assert frame.dtype == numpy.uint8


def test_properties_from_injected_facts_avoid_probing(corpus_gop12: Path) -> None:
    facts = probe_media(corpus_gop12)
    reader = VideoReader(corpus_gop12, facts=facts)
    assert reader.width == facts.width
    assert reader.height == facts.height
    assert reader.fps == pytest.approx(facts.fps)
    assert reader.frame_count == facts.frame_count
    reader.close()


def test_read_batch_returns_indices_and_stacked_frames(corpus_gop12: Path) -> None:
    with VideoReader(corpus_gop12) as reader:
        indices, frames = reader.read_batch(8)
    assert indices.shape == (8,)
    assert frames.shape == (8, reader.height, reader.width, 3)
    assert list(indices) == list(range(8))


def test_read_batch_empty_at_end(corpus_gop12: Path) -> None:
    with VideoReader(corpus_gop12) as reader:
        # Drain the reader.
        while reader.read()[0]:
            pass
        indices, frames = reader.read_batch(4)
    assert indices.shape == (0,)
    assert frames.shape[0] == 0


def test_len_is_output_frame_count(corpus_gop12: Path) -> None:
    facts = probe_media(corpus_gop12)
    with VideoReader(corpus_gop12, facts=facts) as reader:
        assert len(reader) == facts.frame_count


def test_rotated_video_reports_displayed_orientation_self_probe(
    tmp_path: Path,
) -> None:
    # A 90-degree 320x240 source displays as 240 wide by 320 tall. ffmpeg
    # autorotates by default, so decode_md5s and the reader both emit the
    # rotated frame; only the reported shape distinguishes a correct reader
    # from one that reshapes the bytes with the coded dimensions.
    path = generate_video(
        tmp_path / "rot.mp4", frames=24, fps=30.0, gop=12, rotation_degrees=90
    )
    goldens = decode_md5s(path)
    produced: list[str] = []
    with VideoReader(path) as reader:
        assert reader.width == 240
        assert reader.height == 320
        for _index, frame in reader:
            assert frame.shape == (320, 240, 3)
            produced.append(frame_md5(frame))
    assert produced == goldens


def test_rotated_video_reports_displayed_orientation_injected_facts(
    tmp_path: Path,
) -> None:
    path = generate_video(
        tmp_path / "rot.mp4", frames=24, fps=30.0, gop=12, rotation_degrees=90
    )
    facts = probe_media(path)
    assert facts.rotation_degrees == 90
    goldens = decode_md5s(path)
    produced: list[str] = []
    with VideoReader(path, facts=facts) as reader:
        # Coded dimensions are 320x240; the reader swaps them for the display.
        assert reader.width == 240
        assert reader.height == 320
        for _index, frame in reader:
            assert frame.shape == (320, 240, 3)
            produced.append(frame_md5(frame))
    assert produced == goldens
