"""The seek path resolves where the container actually landed.

A backward seek is not guaranteed to reach the keyframe the index named: some
containers index keyframes more coarsely than the stream carries them, and land
earlier. Landing earlier is correct and the reader decodes forward from it.
Landing later means the target's own references were skipped, so counting
forward would return a frame decoded from the wrong prefix, and that raises.
"""

from dataclasses import replace
from pathlib import Path
from typing import override

import numpy
import pytest
from av.video.stream import VideoStream

from mosaic_media.io.reader import VideoReader
from mosaic_media.probe.errors import MediaProbeError
from mosaic_media.probe.probe import probe_media
from tests.helpers.indexes import index_for


def test_a_landing_before_the_requested_keyframe_returns_the_right_frame(
    clips: dict[str, Path],
) -> None:
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    with VideoReader(path, facts=facts) as reader:
        truth = [frame.copy() for _index, frame in reader]
    # An index naming a keyframe the container does not have forces a landing
    # earlier than requested, which is the case under test.
    real = index_for(path)
    coarse = replace(real, keyframe_indices=(0, 6))
    with VideoReader(path, facts=facts, index=coarse) as reader:
        reader.seek(6)
        ok, frame = reader.read()
    assert ok
    assert frame is not None
    assert numpy.array_equal(frame, truth[6])


# Stand-ins installed over a real callable mirror that callable's own parameter
# names, kinds and defaults, and delete what they do not read rather than
# renaming it. tests/probe/test_ffprobe.py's module docstring says why.


class _ReaderLandingPastTheNamedKeyframe(VideoReader):
    """A reader whose container lands one keyframe later than the index named.

    Written as a subclass rather than an attribute swap so the type checker
    holds the override to the base method: a parameter renamed on either side
    is an error here rather than a call that stops binding silently.
    """

    landing_time: float = 0.0

    @override
    def _to_stream_offset(self, stream: VideoStream, keyframe_time: float) -> int:
        del keyframe_time
        time_base = stream.time_base
        assert time_base is not None
        return int(round(self.landing_time / float(time_base)))


def test_a_landing_after_the_requested_keyframe_raises(
    clips: dict[str, Path],
) -> None:
    # An index claiming a keyframe earlier than the container will land on makes
    # the decoder start past the requested position, so the frames before it were
    # never decoded and counting forward would misread.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    real = index_for(path)
    with _ReaderLandingPastTheNamedKeyframe(path, facts=facts, index=real) as reader:
        # Land one keyframe later than the index named, without touching the
        # index -- which is the condition under test.
        reader.landing_time = real.frame_times[30]
        with pytest.raises(MediaProbeError, match="or earlier but decoded"):
            reader.seek(5)


def test_a_landing_matching_no_index_entry_raises(clips: dict[str, Path]) -> None:
    # The tolerance is half the index spacing, so acceptance windows tile the
    # timeline: inside the index's span a landing always resolves to the nearest
    # entry, by design, and the raise is the out-of-span backstop. Shifting every
    # time by a full period rather than a half is what pushes the landing before
    # the first entry, where it matches nothing at all -- a half-period shift is
    # accepted, because period * 0.5 and the tolerance are the same double.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    real = index_for(path)
    period = 1.0 / facts.fps
    shifted = replace(
        real, frame_times=tuple(time + period for time in real.frame_times)
    )
    with VideoReader(path, facts=facts, index=shifted) as reader:
        with pytest.raises(MediaProbeError, match="matches no entry in its seek index"):
            reader.seek(10)
