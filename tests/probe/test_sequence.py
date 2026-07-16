from dataclasses import replace

import pytest

from mosaic_media.probe.sequence import canonical_fps, uniform_properties

from .test_verdict import CLEAN


def test_identical_properties_are_uniform() -> None:
    assert uniform_properties([CLEAN, replace(CLEAN)]) is None


def test_a_frame_rate_mismatch_is_reported() -> None:
    mismatch = uniform_properties([CLEAN, replace(CLEAN, fps=30.0)])
    assert mismatch is not None
    assert mismatch.field == "fps"


def test_a_same_rig_near_equal_frame_rate_is_uniform() -> None:
    # Two clips from one rig at the same nominal 30 fps fit 30.0 and
    # 30.000000040365986. Exact equality would reject every multi-clip sequence in
    # the project's own data; the sub-frame drift tolerance must accept them.
    reference = replace(CLEAN, fps=30.0, frame_count=13222, duration=440.733)
    other = replace(CLEAN, fps=30.000000040365986, frame_count=7433, duration=247.766)
    assert uniform_properties([reference, other]) is None


def test_a_width_mismatch_is_reported() -> None:
    mismatch = uniform_properties([CLEAN, replace(CLEAN, width=640)])
    assert mismatch is not None
    assert mismatch.field == "width"


def test_a_one_pixel_width_difference_is_rejected() -> None:
    # Width compares exactly: no coordinate space indexes 1920 and 1921 the same,
    # so a pixel-scale tolerance would silently misalign pose overlays.
    mismatch = uniform_properties([CLEAN, replace(CLEAN, width=CLEAN.width + 1)])
    assert mismatch is not None
    assert mismatch.field == "width"


def test_canonical_fps_is_the_frame_count_weighted_mean() -> None:
    # sum(frame_count) / sum(duration), not the first clip's rate: a short 25 fps
    # clip beside a long 30 fps clip pulls the canonical rate below 30.
    videos = [
        replace(CLEAN, fps=30.0, frame_count=3000, duration=100.0),
        replace(CLEAN, fps=25.0, frame_count=250, duration=10.0),
    ]
    assert canonical_fps(videos) == pytest.approx(3250 / 110.0)
    assert canonical_fps(videos) != videos[0].fps


def test_an_empty_sequence_is_uniform() -> None:
    assert uniform_properties([]) is None
