"""Codec guard: the installed av binary decodes and encodes this stack's codecs.

A bundled decoder's codec table is curated and shifts between releases (PyAV
v17 dropped libaom from its wheels). This test turns that table from a trusted
property into a tested one: system ffmpeg -- the producer of record for every
transcode -- encodes the codecs this stack writes, and av must decode a frame
of each; av must round-trip its own av1 encode; and av must open and decode a
frame from every container format the fixture corpus exercises. A failure means
the installed av cannot serve this package's codec set; the remedy is to pin a
different av release or build `av --no-binary av` against system libav.
"""

from pathlib import Path

import pytest

from tests.helpers.media_fixtures import asset, build

av = pytest.importorskip("av")

_REMEDY = (
    "the installed av binary cannot serve this codec; pin a different av "
    "release or build 'av --no-binary av' against system libav"
)


def _decode_one(path: Path) -> int:
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        for frame in container.decode(stream):
            return frame.width
    return 0


def test_av_decodes_system_ffmpeg_h264(tmp_path: Path) -> None:
    # H.264 is read, never written: the decoder is native and LGPL, while the
    # software H.264 encoders this build carries are GPL. The clip is committed
    # for that reason.
    clip = asset("cfr.mp4", tmp_path / "h264.mp4")
    assert _decode_one(clip) > 0, _REMEDY


def test_av_decodes_system_ffmpeg_av1(tmp_path: Path) -> None:
    clip = build(tmp_path / "av1.mp4", "-c:v", "libsvtav1", "-pix_fmt", "yuv420p")
    assert _decode_one(clip) > 0, _REMEDY


def test_av_round_trips_its_own_av1_encode(tmp_path: Path) -> None:
    import numpy

    output = tmp_path / "roundtrip.mp4"
    with av.open(str(output), mode="w") as container:
        stream = container.add_stream("libsvtav1", rate=30)
        stream.width = 160
        stream.height = 120
        stream.pix_fmt = "yuv420p"
        for _ in range(5):
            frame = av.VideoFrame.from_ndarray(
                numpy.zeros((120, 160, 3), dtype=numpy.uint8), format="bgr24"
            )
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    assert _decode_one(output) > 0, _REMEDY


def test_av_decodes_each_corpus_container(clips: dict[str, Path]) -> None:
    # mp4/h264, webm/vp8, avi/mjpeg -- the formats the fixture corpus and probe
    # fixtures exercise. The enumeration mirrors the corpus and grows with it.
    for key in ("cfr_mp4", "vp8_webm", "mjpeg_avi"):
        assert _decode_one(clips[key]) > 0, f"{key}: {_REMEDY}"
