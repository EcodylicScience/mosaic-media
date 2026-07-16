from pathlib import Path

import numpy

from mosaic_media.io.reader import VideoReader
from mosaic_media.io.writer import FFmpegVideoWriter


def test_writer_roundtrip_counts_and_dimensions(
    tmp_path: Path, corpus_gop12: Path
) -> None:
    with VideoReader(corpus_gop12) as reader:
        frames = [frame for _index, frame in reader]
    height, width = frames[0].shape[0], frames[0].shape[1]
    output = tmp_path / "out.mp4"
    with FFmpegVideoWriter(output, width, height, fps=30.0) as writer:
        for frame in frames:
            writer.write(frame)
        written = writer.frames_written
    assert written == len(frames)
    assert output.exists()
    with VideoReader(output) as reader:
        reread = [frame for _index, frame in reader]
    assert len(reread) == len(frames)
    assert reread[0].shape == frames[0].shape


def test_writer_roundtrip_content_is_close(tmp_path: Path, corpus_gop12: Path) -> None:
    with VideoReader(corpus_gop12) as reader:
        frames = [frame for _index, frame in reader]
    height, width = frames[0].shape[0], frames[0].shape[1]
    output = tmp_path / "out.mp4"
    with FFmpegVideoWriter(output, width, height, fps=30.0) as writer:
        for frame in frames:
            writer.write(frame)
    with VideoReader(output) as reader:
        reread = [frame for _index, frame in reader]
    # libx264 at crf 23 is lossy, so compare content approximately.
    differences = [
        float(numpy.abs(a.astype(numpy.int16) - b.astype(numpy.int16)).mean())
        for a, b in zip(frames, reread)
    ]
    assert max(differences) < 15.0


def test_writer_properties(tmp_path: Path) -> None:
    output = tmp_path / "props.mp4"
    writer = FFmpegVideoWriter(output, 320, 240, fps=25.0)
    try:
        assert writer.output_path == output.expanduser().resolve()
        assert writer.width == 320
        assert writer.height == 240
        assert writer.fps == 25.0
        assert writer.frames_written == 0
    finally:
        writer.close()
