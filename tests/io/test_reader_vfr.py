"""Frame-exact seeking on a genuinely variable-rate file.

A frame-index seek that converts the index to a timestamp through one average
frame rate lands wrong wherever the local rate differs from the average. On
this fixture shape, OpenCV's CAP_PROP_POS_FRAMES landed 12 of 14 seeks off by
-35..+25 frames (measured once against pixel-content ground truth,
opencv-python 5.0.0, while staying frame-exact on a constant-rate control).
The reader seeks through the packet index instead, so its landing carries no
rate conversion at all. This suite pins that on the file class this stack
actually ingests: recordings drop frames when the machine gets busy.
"""

from pathlib import Path

from mosaic_media.io.reader import VideoReader
from mosaic_media.probe.probe import probe_media
from tests.helpers.corpus import decode_md5s, frame_md5


def test_fixture_is_genuinely_variable_rate(corpus_vfr: Path) -> None:
    facts = probe_media(corpus_vfr)
    assert not facts.constant_frame_rate
    assert facts.frame_count == 300


def test_seek_on_variable_rate_is_frame_exact(corpus_vfr: Path) -> None:
    # Targets straddle the rate change: inside the leading 30 fps run, inside
    # the 10 fps stretch, and after the rate resumes -- where an average-rate
    # index-to-timestamp conversion lands furthest off.
    goldens = decode_md5s(corpus_vfr)
    targets = (0, 5, 40, 95, 99, 100, 110, 125, 149, 150, 175, 200, 250, 299)
    with VideoReader(corpus_vfr) as reader:
        for target in targets:
            reader.seek(target)
            ok, frame = reader.read()
            assert ok
            assert frame is not None
            assert frame_md5(frame) == goldens[target]


def test_seek_then_sequential_across_rate_change(corpus_vfr: Path) -> None:
    goldens = decode_md5s(corpus_vfr)
    with VideoReader(corpus_vfr) as reader:
        reader.seek(95)
        produced: list[str] = []
        for _ in range(10):
            ok, frame = reader.read()
            assert ok
            assert frame is not None
            produced.append(frame_md5(frame))
    assert produced == goldens[95:105]


def test_sparse_read_on_variable_rate(corpus_vfr: Path) -> None:
    goldens = decode_md5s(corpus_vfr)
    targets = [40, 110, 149, 200, 299]
    with VideoReader(corpus_vfr) as reader:
        produced = {
            index: frame_md5(frame) for index, frame in reader.read_frames(targets)
        }
    assert produced == {index: goldens[index] for index in targets}
