"""The reader delivers frames a default decode drops."""

from dataclasses import replace
from pathlib import Path
from typing import override

import av
import numpy
import pytest
from av.codec.context import Flags2
from av.video.frame import VideoFrame

import mosaic_media.io.reader as reader_module
from mosaic_media.io.reader import VideoReader
from mosaic_media.probe.errors import MediaProbeError
from mosaic_media.probe.policy import DEFAULT_THRESHOLDS
from mosaic_media.probe.probe import probe_media
from tests.helpers.indexes import index_for
from tests.helpers.scans import count_packet_scans


def test_show_all_is_a_no_op_on_a_source_opening_on_a_keyframe(
    clips: dict[str, Path],
) -> None:
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    with VideoReader(path, facts=facts) as reader:
        delivered = sum(1 for _index, _frame in reader)
    assert delivered == facts.frame_count


def test_show_all_is_a_no_op_on_an_open_gop_source(open_gop_clip: Path) -> None:
    # Open GOP is the shape that could refute the unconditional flag: every
    # keyframe after the first is followed in decode order by pictures that
    # precede it in presentation order, and a decoder suppresses those after a
    # seek. Measured: 50 frames either way, identical timestamps and pixels.
    facts = probe_media(open_gop_clip)
    with VideoReader(open_gop_clip, facts=facts) as reader:
        delivered = sum(1 for _index, _frame in reader)
    assert delivered == facts.frame_count == 50


def test_a_source_cut_mid_stream_delivers_its_leading_frames(
    avi_starting_on_non_keyframes: Path,
) -> None:
    facts = probe_media(avi_starting_on_non_keyframes)
    assert facts.leading_non_keyframe_frames == 24
    with VideoReader(avi_starting_on_non_keyframes, facts=facts) as reader:
        delivered = sum(1 for _index, _frame in reader)
    assert delivered == facts.frame_count == 49


def test_a_source_with_an_edit_list_delivers_its_pre_roll(preroll_mp4: Path) -> None:
    # The only fixture that fires the ignore_editlist gate. The demuxer
    # delivers all five either way; without the gate the decode honors their
    # "do not present" mark and emits five fewer frames than the facts count.
    facts = probe_media(preroll_mp4)
    assert facts.discard_flagged_packets == 5
    with VideoReader(preroll_mp4, facts=facts) as reader:
        delivered = sum(1 for _index, _frame in reader)
    assert delivered == facts.frame_count


def test_the_edit_list_gate_shifts_only_the_source_that_needs_it(
    preroll_mp4: Path, clips: dict[str, Path]
) -> None:
    # Applying ignore_editlist everywhere would move a benign edit list's origin
    # by its composition offset. Pin it behaviorally on both sides, on content
    # rather than on a flag: the gated source's frame 5 is the picture a default
    # decode calls frame 0, because the recovered pre-roll fills the five slots
    # ahead of it; the ungated source's frame 0 is unmoved.
    gated_facts = probe_media(preroll_mp4)
    assert gated_facts.discard_flagged_packets == 5
    default_first = _first_frame_without_the_gate(preroll_mp4)
    with VideoReader(preroll_mp4, facts=gated_facts) as reader:
        gated = [frame.copy() for _index, frame in reader]
    assert len(gated) == gated_facts.frame_count
    assert numpy.array_equal(gated[5], default_first)
    assert not numpy.array_equal(gated[0], default_first)

    ungated_path = clips["cfr_30fps_mp4"]
    ungated_facts = probe_media(ungated_path)
    assert ungated_facts.discard_flagged_packets == 0
    with VideoReader(ungated_path, facts=ungated_facts) as reader:
        _index, ungated_first = next(iter(reader))
    assert numpy.array_equal(ungated_first, _first_frame_without_the_gate(ungated_path))


def _first_frame_without_the_gate(path: Path) -> numpy.ndarray:
    """The first frame a default open decodes, bypassing the reader's options."""
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        for frame in container.decode(stream):
            return frame.to_ndarray(format="bgr24")
    message = f"no frame decoded from {path}"
    raise MediaProbeError(message)


def test_an_index_from_the_wrong_scanner_is_rejected(clips: dict[str, Path]) -> None:
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    honest = index_for(path)
    mismatched = replace(honest, source="probe")
    with VideoReader(path, facts=facts, index=mismatched) as reader:
        with pytest.raises(MediaProbeError, match="probe scanner"):
            reader.seek(10)


def test_an_index_from_the_wrong_timestamp_space_is_rejected(
    clips: dict[str, Path],
) -> None:
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    honest = index_for(path)
    mismatched = replace(honest, space="edit_list_ignored")
    with VideoReader(path, facts=facts, index=mismatched) as reader:
        with pytest.raises(MediaProbeError, match="edit_list_ignored timestamp space"):
            reader.seek(10)


class _ReaderWithObservableFlag(VideoReader):
    """A reader that reports its own decoder flag, so the scoping rule can be
    pinned against decoder state.

    Subclassing keeps this out of VideoReader's public surface: whether the
    frames-preserving flag is set is decoder configuration, not part of what a
    caller reading frames needs to know. Protected state is reachable from a
    subclass without any suppression.
    """

    def show_all_enabled(self) -> bool:
        stream = self._stream
        if stream is None:
            return False
        return bool(stream.codec_context.flags2 & Flags2.show_all)


def test_show_all_is_set_only_for_a_segment_starting_at_the_stream_start(
    open_gop_clip: Path,
) -> None:
    # The flag emits packets preceding the first keyframe of a decode segment,
    # and those exist only at the stream start. Asserted against decoder state
    # rather than against pixels or a raised error: a later change makes an
    # early landing legitimate and decodes forward from it, which masks both of
    # those consequences while the corrupt pictures are still produced.
    facts = probe_media(open_gop_clip)
    index = index_for(open_gop_clip)

    # Sequential: _start_reading's non-seeking path, reached only without a
    # prior seek. No seek-driven target below exercises it.
    with _ReaderWithObservableFlag(open_gop_clip, facts=facts) as sequential_reader:
        ok, _frame = sequential_reader.read()
        assert ok
        assert sequential_reader.show_all_enabled()

    for target in (0, 1, 9, 10, 11, 12, 13, 24, 30, 36, 48, facts.frame_count - 1):
        keyframe_index, _keyframe_time = index.preceding_keyframe(target)
        with _ReaderWithObservableFlag(open_gop_clip, facts=facts) as reader:
            reader.seek(target)
            expected = keyframe_index == 0
            message = (
                f"target {target}: resolved keyframe {keyframe_index}, expected "
                f"show_all {expected}"
            )
            assert reader.show_all_enabled() == expected, message


def test_open_gop_seeks_land_frame_exact(open_gop_clip: Path) -> None:
    # A seek to the nearest keyframe at or before the target in DECODE order
    # returns an open-GOP leading picture at the right timestamp with the wrong
    # pixels, because its references live in the previous GOP. Measured on this
    # clip: targets 10 and 11 land wrong that way. The reader resolves the
    # preceding keyframe in PRESENTATION order and decodes the chain, so every
    # target must match a sequential read.
    facts = probe_media(open_gop_clip)
    with VideoReader(open_gop_clip, facts=facts) as reader:
        truth = [frame.copy() for _index, frame in reader]
    for target in (0, 1, 9, 10, 11, 12, 13, 24, 30, 36, 48, len(truth) - 1):
        with VideoReader(open_gop_clip, facts=facts) as reader:
            reader.seek(target)
            ok, frame = reader.read()
        assert ok
        assert frame is not None
        assert numpy.array_equal(frame, truth[target]), f"target {target} misread"


def test_a_read_ending_before_its_window_raises(clips: dict[str, Path]) -> None:
    # Facts declaring more frames than the file delivers stand in for a source
    # carrying packets that decode to no frame: the reader reports the shortfall
    # rather than stopping silently inside its own window. The stand-in also
    # desynchronizes the facts from the seek index, which such a source does not
    # -- so it exercises this sequential backstop, and the sparse path's behavior
    # does not follow from it.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    overstated = replace(facts, frame_count=facts.frame_count + 5)
    with VideoReader(path, facts=overstated) as reader:
        with pytest.raises(MediaProbeError, match="delivered 60 of 65"):
            for _index, _frame in reader:
                pass


def test_the_shortfall_is_counted_from_where_a_seek_left_the_cursor(
    clips: dict[str, Path],
) -> None:
    # After a seek the counter restarts, so the reported expectation must be the
    # frames remaining from the seek target -- not the whole window, which the
    # reader never attempted.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    overstated = replace(facts, frame_count=facts.frame_count + 5)
    with VideoReader(path, facts=overstated) as reader:
        reader.seek(50)
        with pytest.raises(MediaProbeError, match="delivered 10 of 15"):
            while True:
                ok, _frame = reader.read()
                if not ok:
                    break


def test_the_shortfall_is_rebased_onto_the_cursor_a_sparse_read_leaves(
    clips: dict[str, Path],
) -> None:
    # read_frames resumes the cursor past its own target, and the shortfall is
    # counted from there -- the same rule seek() follows. The sparse frame is
    # excluded from both sides: the sequential read did not deliver it, and its
    # target need not lie on the stride grid the count enumerates.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    overstated = replace(facts, frame_count=facts.frame_count + 5)
    with VideoReader(path, facts=overstated) as reader:
        sparse = [index for index, _frame in reader.read_frames([10])]
        assert sparse == [10]
        sequential = 0
        with pytest.raises(MediaProbeError, match="delivered 49 of 54") as excinfo:
            while True:
                ok, _frame = reader.read()
                if not ok:
                    break
                sequential += 1
    assert f"delivered {sequential} of" in str(excinfo.value)


class _ReaderWhoseSourceRunsOut(VideoReader):
    """A reader whose source delivers no frame at or beyond `runs_out_at`.

    Gated on each frame's own presentation time rather than on a count of decode
    calls, because a seek decodes only its own group of pictures: a call cap
    would never be reached on the sparse path, and the source would look healthy
    exactly where this fixture needs it short.
    """

    runs_out_at: int = 0

    @override
    def _decode_next(self) -> VideoFrame | None:
        frame = super()._decode_next()
        if frame is None:
            return None
        if frame.time * self.fps >= self.runs_out_at - 0.5:
            return None
        return frame


def test_a_strided_read_after_a_sparse_one_reports_a_missing_slot(
    clips: dict[str, Path],
) -> None:
    # A source one stride slot short, with honest facts. Counting the sparse
    # frame toward the strided grid would leave exactly the surplus that closes
    # a one-slot gap, and the shortfall would go unreported.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    with _ReaderWhoseSourceRunsOut(path, facts=facts, frame_step=3) as reader:
        reader.runs_out_at = facts.frame_count - 2
        sparse = [index for index, _frame in reader.read_frames([10])]
        assert sparse == [10]
        sequential = 0
        with pytest.raises(MediaProbeError, match="delivered 16 of 17") as excinfo:
            while True:
                ok, _frame = reader.read()
                if not ok:
                    break
                sequential += 1
    assert f"delivered {sequential} of" in str(excinfo.value)


def test_a_seek_restarts_the_delivery_count(clips: dict[str, Path]) -> None:
    # Reading before the seek is what distinguishes this from the test above:
    # deliveries carried across the seek would satisfy the post-seek
    # expectation on their own and suppress a genuine shortfall.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    overstated = replace(facts, frame_count=facts.frame_count + 5)
    with VideoReader(path, facts=overstated) as reader:
        for _ in range(10):
            ok, _frame = reader.read()
            assert ok
        reader.seek(50)
        with pytest.raises(MediaProbeError, match="delivered 10 of 15"):
            while True:
                ok, _frame = reader.read()
                if not ok:
                    break


# One list, two tests. The guard below derives the expected set from it, so a
# member added without a delivery case fails there -- which is what makes the
# comment on FRAME_EXACT_CODECS true rather than aspirational.
_MEASURED_CODECS = [
    ("vp8_webm_clip", "vp8"),
    ("vp9_webm_clip", "vp9"),
    ("cfr_mp4_clip", "h264"),
    ("hevc_clip", "hevc"),
    ("corpus_gop12", "av1"),
]


@pytest.mark.parametrize(("fixture_name", "expected_codec"), _MEASURED_CODECS)
def test_every_trusted_codec_delivers_one_frame_per_packet(
    request: pytest.FixtureRequest, fixture_name: str, expected_codec: str
) -> None:
    # Every member of the shipped default, measured. Resolved by fixture name
    # because the AV1 corpus is its own session fixture, not a `clips` key.
    path = request.getfixturevalue(fixture_name)
    facts = probe_media(path)
    assert facts.codec_name == expected_codec
    assert facts.codec_name in DEFAULT_THRESHOLDS.frame_exact_codecs
    with VideoReader(path, facts=facts) as reader:
        delivered = sum(1 for _index, _frame in reader)
    assert delivered == facts.frame_count


def test_the_trusted_set_is_exactly_what_the_suite_measures() -> None:
    # Derived, not restated: the shipped default must equal the set the tests
    # above actually exercise, so neither can drift from the other.
    assert DEFAULT_THRESHOLDS.frame_exact_codecs == frozenset(
        codec for _fixture_name, codec in _MEASURED_CODECS
    )


def test_injected_facts_sequential_read_runs_no_packet_scan(
    clips: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The performance gate measures this path against OpenCV. Its metadata-open,
    # sequential-full-decode and strided-decode payloads run no packet scan at
    # all: they inject facts and never seek. A scan added here would fail no
    # correctness test; it would fail the gate on a machine this suite never
    # runs on. Pin it where it is cheap to see.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    with count_packet_scans(reader_module, monkeypatch) as scans:
        with VideoReader(path, facts=facts) as reader:
            delivered = sum(1 for _index, _frame in reader)
        assert delivered == facts.frame_count
        assert scans() == 0


def test_injected_facts_metadata_access_opens_no_container(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    # What the metadata-open payloads measure. Point the reader at a path with no
    # file behind it: geometry still resolves from the facts, and any container
    # open -- including the rotation probe's second one -- would raise
    # "failed to open" here instead.
    facts = probe_media(clips["cfr_30fps_mp4"])
    with VideoReader(tmp_path / "absent.mp4", facts=facts) as reader:
        assert (reader.width, reader.height, reader.fps, reader.frame_count) == (
            facts.width,
            facts.height,
            facts.fps,
            facts.frame_count,
        )


def test_injected_facts_strided_read_runs_no_packet_scan(
    clips: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The strided-decode payloads read with a frame step and no injected index.
    # Serving a large step by seeking to each target instead of decoding through
    # is the obvious optimization here, and it would put a scan on this path
    # that the unstrided pin above cannot see.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    with count_packet_scans(reader_module, monkeypatch) as scans:
        with VideoReader(path, facts=facts, frame_step=5) as reader:
            delivered = sum(1 for _index, _frame in reader)
        assert delivered == len(range(0, facts.frame_count, 5))
        assert scans() == 0


def test_injected_facts_seek_runs_one_packet_scan(
    clips: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Seeking resolves the landing against the packet index, which facts alone
    # do not carry, so this path builds it once. Pinned at the one it already
    # runs: a second scan would not fail a correctness test either.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    with count_packet_scans(reader_module, monkeypatch) as scans:
        with VideoReader(path, facts=facts) as reader:
            reader.seek(10)
            ok, _frame = reader.read()
            assert ok
        assert scans() == 1


def test_injected_facts_sparse_read_runs_one_packet_scan(
    clips: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The sparse-extraction payload, which seeks per target through one index.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    with count_packet_scans(reader_module, monkeypatch) as scans:
        with VideoReader(path, facts=facts) as reader:
            targets = [index for index, _frame in reader.read_frames([5, 20, 40])]
        assert targets == [5, 20, 40]
        assert scans() == 1
