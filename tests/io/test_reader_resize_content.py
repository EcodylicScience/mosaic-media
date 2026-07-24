"""The reader's resize path produces bicubic-scaled content matching ffmpeg.

The reader scales with bicubic interpolation to match system ffmpeg's `-vf
scale` default; libav's own reformat default is bilinear. These tests check the
resized frames against ffmpeg's scale output, upright and rotated.

Both the grayscale and the color path are held to a tolerance of 2, and both
measure 0 or 1 against the golden. The tolerance is small but deliberately
nonzero: scaling is arithmetic, and its result could differ by a rounding step
between the bundled libav the reader decodes with and the system ffmpeg the
goldens come from. It stays far below the bilinear regression, which drifts
about 24 gray levels.

Scaling inside the reader's filter graph is what makes the color path exact.
Driving libswscale through VideoFrame.reformat instead diverges from ffmpeg on
the chroma planes -- luma stays bit-identical, chroma reaches 20 to 27 -- and
that error is amplified once it crosses into BGR, reaching 57 upright and 76
rotated across the 48-frame corpus_gop12 clip. The color conversion on its own
is exact in both libraries, so the cause is chroma plane scaling, not a library
version difference and not the conversion.
"""

from pathlib import Path

import numpy
import pytest

from mosaic_media.io.reader import VideoReader
from tests.helpers.corpus import generate_video, scaled_frames

TOLERANCE = 2


def _max_channel_difference(a: numpy.ndarray, b: numpy.ndarray) -> int:
    return int(numpy.abs(a.astype(numpy.int16) - b.astype(numpy.int16)).max())


def test_grayscale_resize_is_bicubic_against_ffmpeg_scale(corpus_gop12: Path) -> None:
    goldens = scaled_frames(corpus_gop12, 160, 120, grayscale=True)
    with VideoReader(corpus_gop12, resize=(160, 120), grayscale=True) as reader:
        produced = [frame for _index, frame in reader]
    assert len(produced) == len(goldens)
    for frame, golden in zip(produced, goldens):
        assert frame.shape == (120, 160)
        assert _max_channel_difference(frame, golden) <= TOLERANCE


def test_bgr_resize_is_bicubic_against_ffmpeg_scale(corpus_gop12: Path) -> None:
    goldens = scaled_frames(corpus_gop12, 160, 120)
    with VideoReader(corpus_gop12, resize=(160, 120)) as reader:
        produced = [frame for _index, frame in reader]
    assert len(produced) == len(goldens)
    for frame, golden in zip(produced, goldens):
        assert frame.shape == (120, 160, 3)
        assert _max_channel_difference(frame, golden) <= TOLERANCE


@pytest.mark.parametrize("rotation_degrees", [90, 180, 270])
@pytest.mark.parametrize("grayscale", [False, True])
def test_rotated_resize_is_bicubic_against_ffmpeg_scale(
    tmp_path: Path, rotation_degrees: int, grayscale: bool
) -> None:
    # ffmpeg autorotates on decode, so the -vf scale golden is the rotated and
    # then scaled frame -- exactly the composition the reader emits.
    path = generate_video(
        tmp_path / f"rot{rotation_degrees}_{grayscale}.mp4",
        frames=12,
        fps=30.0,
        gop=12,
        rotation_degrees=rotation_degrees,
    )
    goldens = scaled_frames(path, 160, 120, grayscale=grayscale)
    with VideoReader(path, resize=(160, 120), grayscale=grayscale) as reader:
        produced = [frame for _index, frame in reader]
    assert len(produced) == len(goldens)
    expected_shape = (120, 160) if grayscale else (120, 160, 3)
    for frame, golden in zip(produced, goldens):
        assert frame.shape == expected_shape
        assert _max_channel_difference(frame, golden) <= TOLERANCE
