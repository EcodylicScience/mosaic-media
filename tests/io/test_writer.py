from pathlib import Path

import numpy
import pytest

from mosaic_media.io.reader import VideoReader
from mosaic_media.io.writer import FFmpegVideoWriter
from mosaic_media.probe.errors import MediaProbeError


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


def test_write_rejects_wrong_shape(tmp_path: Path) -> None:
    output = tmp_path / "shape.mp4"
    with FFmpegVideoWriter(output, 320, 240, fps=30.0) as writer:
        wrong = numpy.zeros((240, 321, 3), dtype=numpy.uint8)
        with pytest.raises(MediaProbeError):
            writer.write(wrong)
        assert writer.frames_written == 0


def test_write_rejects_wrong_dtype(tmp_path: Path) -> None:
    output = tmp_path / "dtype.mp4"
    with FFmpegVideoWriter(output, 320, 240, fps=30.0) as writer:
        wrong = numpy.zeros((240, 320, 3), dtype=numpy.float32)
        with pytest.raises(MediaProbeError):
            writer.write(wrong)
        assert writer.frames_written == 0


def test_hardware_encode_falls_back_when_device_unusable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With permission granted but the device unusable, the writer must fall back
    # to libx264 rather than select an NVENC encoder that fails at startup.
    monkeypatch.setattr("mosaic_media.io.writer._nvenc_encoder_usable", lambda: False)
    output = tmp_path / "fallback.mp4"
    with FFmpegVideoWriter(output, 320, 240, fps=30.0, hwaccel=True) as writer:
        assert writer.encoder_name == "libx264"


def test_writer_surfaces_ffmpeg_startup_failure(tmp_path: Path) -> None:
    # An output extension av cannot map to a muxer makes av.open raise at
    # construction ("Unable to find a suitable output format"), before any frame
    # is encoded. That must surface as a MediaProbeError, never a silently
    # incremented frame count.
    output = tmp_path / "out.unknownext"
    frame = numpy.zeros((240, 320, 3), dtype=numpy.uint8)
    with pytest.raises(MediaProbeError):
        writer = FFmpegVideoWriter(output, 320, 240, fps=30.0)
        try:
            for _ in range(5):
                writer.write(frame)
        finally:
            writer.close()
