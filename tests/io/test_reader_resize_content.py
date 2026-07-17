"""The reader's resize path produces bicubic-scaled content.

The reader scales with bicubic interpolation to match system ffmpeg's `-vf
scale` default; av's own reformat default is bilinear. These tests check the
resized frames against ffmpeg's scale output.

Two paths, two tolerances, both measured on this machine (av 18 bundled libav
versus system ffmpeg 6.1 swscale):

- Grayscale resize skips the yuv-to-bgr color conversion, isolating the scale
  kernel. Bicubic lands within one gray level of the ffmpeg golden (a genuinely
  small tolerance); the old bilinear default drifts about 24 levels, so a
  tolerance of 2 separates a correct bicubic resize from the regression cleanly.
- The bgr resize path additionally crosses the color conversion, where the
  bundled-versus-system swscale major skew alone reaches into the fifties per
  channel regardless of the kernel. Bicubic measures a max per-channel
  difference of 57 against the golden and bilinear 73, so the tolerance sits
  between them: it admits the version skew while still rejecting the bilinear
  regression and any gross content error, which moves whole regions by far more.
"""

from pathlib import Path

import numpy

from mosaic_media.io.reader import VideoReader
from tests.helpers.corpus import scaled_frames


def _max_channel_difference(a: numpy.ndarray, b: numpy.ndarray) -> int:
    return int(numpy.abs(a.astype(numpy.int16) - b.astype(numpy.int16)).max())


def test_grayscale_resize_is_bicubic_against_ffmpeg_scale(corpus_gop12: Path) -> None:
    goldens = scaled_frames(corpus_gop12, 160, 120, grayscale=True)
    with VideoReader(corpus_gop12, resize=(160, 120), grayscale=True) as reader:
        produced = [frame for _index, frame in reader]
    assert len(produced) == len(goldens)
    for frame, golden in zip(produced, goldens):
        assert frame.shape == (120, 160)
        assert _max_channel_difference(frame, golden) <= 2


def test_bgr_resize_is_bicubic_against_ffmpeg_scale(corpus_gop12: Path) -> None:
    goldens = scaled_frames(corpus_gop12, 160, 120)
    with VideoReader(corpus_gop12, resize=(160, 120)) as reader:
        produced = [frame for _index, frame in reader]
    assert len(produced) == len(goldens)
    for frame, golden in zip(produced, goldens):
        assert frame.shape == (120, 160, 3)
        # Measured: bicubic max 57, bilinear (the regression) max 73. See the
        # module docstring for the swscale-skew rationale behind this tolerance.
        assert _max_channel_difference(frame, golden) <= 64
