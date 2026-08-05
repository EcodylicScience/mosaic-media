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
from mosaic_media.io.index import build_seek_index
from mosaic_media.io.packets import scan_packets_in_process
from mosaic_media.io.reader import VideoReader
from mosaic_media.probe.errors import MediaProbeError
from mosaic_media.probe.policy import CHROME_149, DEFAULT_THRESHOLDS
from mosaic_media.probe.probe import probe_media
from mosaic_media.probe.verdict import derive
from tests.helpers.corpus import decode_md5s, frame_md5
from tests.helpers.media_fixtures import (
    QUANTIZED_RATES,
    requires_av1_frame_split,
    requires_svtav1,
)
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


def test_an_injected_index_is_not_re_scanned_for_a_gated_source(
    preroll_mp4: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A reader given an index but no facts scans once, to decide whether to
    # open with the edit list ignored -- that answer is not in the index, and
    # having it independently is what lets an ungated index handed to a gated
    # source be rejected. It must not scan a second time: the gated re-scan
    # exists only to build an index, so for a caller who brought one it is a
    # whole demux whose result is discarded. Measured at two before the
    # re-scan moved inside the build.
    gated = build_seek_index(
        scan_packets_in_process(preroll_mp4, ignore_edit_list=True)[0],
        source="in_process",
        space="edit_list_ignored",
    )
    with count_packet_scans(reader_module, monkeypatch) as scans:
        with VideoReader(preroll_mp4, index=gated) as reader:
            reader.seek(10)
            ok, _frame = reader.read()
            assert ok
        assert scans() == 1


def test_a_factless_reader_still_re_scans_a_gated_source_it_must_index(
    preroll_mp4: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The other side of that branch: with no index to reuse, the gated space
    # has to be scanned, because the ungated packets cannot be indexed for a
    # decode that ignores the edit list.
    with count_packet_scans(reader_module, monkeypatch) as scans:
        with VideoReader(preroll_mp4) as reader:
            reader.seek(10)
            ok, _frame = reader.read()
            assert ok
        assert scans() == 2


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


def test_the_flag_is_cleared_and_restored_on_one_live_container(
    open_gop_clip: Path,
) -> None:
    # The test above builds a fresh reader per target, so it only ever observes
    # a decoder going from its default-clear state to set-or-clear once. What
    # makes the per-segment rule work is the other two transitions, on a
    # container that stays open: set to clear when a seek lands on an internal
    # keyframe, and clear back to set when a later one returns to the stream
    # start. A rule applied only on the way down would leave a reader that
    # seeks backward to frame 0 decoding without the flag and silently short of
    # the leading frames.
    facts = probe_media(open_gop_clip)
    index = index_for(open_gop_clip)
    internal = [rank for rank in index.keyframe_indices if rank != 0]
    assert internal, "the fixture must have a keyframe after the first"
    # try/finally rather than `with`: __enter__ is annotated with the concrete
    # class, so a subclass loses its own type inside a with block.
    reader = _ReaderWithObservableFlag(open_gop_clip, facts=facts)
    try:
        reader.seek(0)
        assert reader.show_all_enabled(), "stream start: set"
        reader.seek(internal[0])
        assert not reader.show_all_enabled(), "internal keyframe: cleared"
        reader.seek(0)
        assert reader.show_all_enabled(), "back to the stream start: restored"
        # The restored flag still delivers, rather than merely reading as set.
        ok, _frame = reader.read()
        assert ok
    finally:
        reader.close()


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


class _ReaderSkippingPresentationRanks(VideoReader):
    """A reader whose decoder never emits certain presentation ranks.

    The null-frame shape: the index and the facts declare more timestamps than
    the decode produces, with the missing ones in the middle rather than at the
    end. Distinct from `_ReaderWhoseSourceRunsOut`, which truncates -- there
    every delivered frame still sits at its own rank, and only the total falls
    short. Here the forward count completes on a later frame and hands it back
    under the requested index, which no total can see.

    Ranks are resolved from each frame's own presentation time rather than by
    counting decode calls, so a seek that decodes only its own group of
    pictures skips the same ranks a sequential decode does -- and so the same
    fixture serves a reader holding no index, where the rank is not otherwise
    knowable. Constant-rate sources only, which every caller here uses.
    """

    skipped_ranks: frozenset[int] = frozenset()

    @override
    def _decode_next(self) -> VideoFrame | None:
        frame = super()._decode_next()
        while frame is not None and self._rank_of(frame) in self.skipped_ranks:
            frame = super()._decode_next()
        return frame

    def _rank_of(self, frame: VideoFrame) -> int:
        return round(float(frame.time) * self.fps)


@pytest.mark.parametrize("target", [5, 8, 20])
def test_a_seek_onto_a_skipped_rank_raises_rather_than_returning_a_neighbor(
    clips: dict[str, Path], target: int
) -> None:
    # The seek path's landing check resolves where the decoder LANDED, which is
    # a real packet timestamp and therefore always in the index. Nothing checked
    # the forward count that follows, so a source skipping ranks inside the
    # group of pictures returned the frame two ranks later under the requested
    # index -- no raise, wrong pixels. Measured before the per-frame check:
    # seek(5) returned truth index 7, seek(8) index 10, seek(20) index 22.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    index = index_for(path)
    with _ReaderSkippingPresentationRanks(path, facts=facts, index=index) as reader:
        reader.skipped_ranks = frozenset({2, 3})
        reader.seek(target)
        with pytest.raises(MediaProbeError, match=f"frame {target}:"):
            _ok, _frame = reader.read()


def test_every_entry_point_rejects_a_frame_the_index_does_not_place_there(
    clips: dict[str, Path],
) -> None:
    # read_batch and iteration reach the same delivery through read(), and
    # read_frames reaches it directly; a check installed on one public method
    # rather than on the shared delivery would leave the others returning the
    # neighboring frame.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    index = index_for(path)
    skipped = frozenset({2, 3})

    def reader() -> _ReaderSkippingPresentationRanks:
        made = _ReaderSkippingPresentationRanks(path, facts=facts, index=index)
        made.skipped_ranks = skipped
        return made

    with reader() as sequential:
        with pytest.raises(MediaProbeError, match="frame 2:"):
            while True:
                ok, _frame = sequential.read()
                if not ok:
                    break
    with reader() as iterated:
        with pytest.raises(MediaProbeError, match="frame 2:"):
            for _index, _frame in iterated:
                pass
    with reader() as batched:
        with pytest.raises(MediaProbeError, match="frame 2:"):
            _indices, _frames = batched.read_batch(10)
    with reader() as sparse:
        with pytest.raises(MediaProbeError, match="frame 5:"):
            for _index, _frame in sparse.read_frames([5, 20, 40]):
                pass


@pytest.mark.parametrize("frame_step", [1, 3])
def test_a_bounded_window_over_a_skipped_rank_raises(
    clips: dict[str, Path], frame_step: int
) -> None:
    # The delivery count reports a shortfall only when the window runs out
    # early, and a bounded one does not: it ends on a frame the source still
    # had. Measured with facts injected and no index, before the gap check:
    # end_frame=40 delivered 40 frames, 38 of them the wrong picture, and
    # terminated clean; at frame_step=3, 14 delivered and 13 wrong. This is the
    # shape that proves the count insufficient, and no index is built on it.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    with _ReaderSkippingPresentationRanks(
        path, facts=facts, end_frame=40, frame_step=frame_step
    ) as reader:
        reader.skipped_ranks = frozenset({2, 3})
        with pytest.raises(MediaProbeError, match="frame periods later"):
            for _index, _frame in reader:
                pass


def test_the_gap_check_covers_the_reader_that_builds_no_index(
    clips: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # An index exists only when the reader was built without facts or has since
    # seeked, so a caller injecting facts and reading forward has no index --
    # and that is the sequential and strided read the gate measures, which must
    # keep running no packet scan. The gap between consecutive decoded frames
    # is what covers it, and it must raise without building one.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    with count_packet_scans(reader_module, monkeypatch) as scans:
        with _ReaderSkippingPresentationRanks(path, facts=facts) as reader:
            reader.skipped_ranks = frozenset({2, 3})
            with pytest.raises(MediaProbeError, match="frame periods later"):
                for _index, _frame in reader:
                    pass
        assert scans() == 0


@pytest.mark.parametrize("name", [name for name, _fps, _scale in QUANTIZED_RATES])
def test_a_coarse_timescale_source_reads_clean(
    quantized_clips: dict[str, Path], name: str
) -> None:
    # A container too coarse to express its own frame rate quantizes the
    # timestamps, so a constant-rate file's neighbors land unevenly -- measured
    # at 1.66 periods for 30 fps in a 1/36 timescale. Every one of these is
    # analysis-ready by this package's own verdict, so the reader must not fail
    # on it. A fixed threshold of 1.5 raised on all five; the corpus is all
    # 1/15360 mp4 and could not see it.
    path = quantized_clips[name]
    facts = probe_media(path)
    assert facts.constant_frame_rate
    assert derive(facts, CHROME_149, DEFAULT_THRESHOLDS).analysis_transcode is None
    assert facts.max_timestamp_gap_frame_periods > 1.5
    for start_frame in (0, 1, 7):
        for end_frame in (None, 40):
            with VideoReader(
                path, facts=facts, start_frame=start_frame, end_frame=end_frame
            ) as reader:
                delivered = sum(1 for _index, _frame in reader)
            window_end = (
                facts.frame_count
                if end_frame is None
                else min(end_frame, facts.frame_count)
            )
            assert delivered == len(range(min(start_frame, window_end), window_end))


def test_the_check_declines_where_the_files_own_spacing_reaches_the_signal(
    quantized_clips: dict[str, Path],
) -> None:
    # One missing frame puts two neighbors at the sum of the steps it spanned;
    # on this clip the legitimate steps alternate 0.83 and 1.66, so a frame lost
    # between two short ones produces exactly a step the file takes anyway. No
    # comparison of spacings can separate them, and the check declines rather
    # than guessing either way. Pinned as a deliberate outcome: the shortfall is
    # still reported, by the delivery count, and what must NOT happen is the gap
    # check inventing a verdict it cannot support.
    path = quantized_clips["30_in_36"]
    facts = probe_media(path)
    assert facts.max_timestamp_gap_frame_periods + 0.5 >= 2.0
    with _ReaderSkippingPresentationRanks(path, facts=facts) as reader:
        reader.skipped_ranks = frozenset({2, 3})
        with pytest.raises(MediaProbeError) as excinfo:
            for _index, _frame in reader:
                pass
    assert "frame periods later" not in str(excinfo.value)
    assert "delivered" in str(excinfo.value)


def test_a_ramped_rate_source_is_checked_rather_than_exempted(
    ramped_rate_clip: Path,
) -> None:
    # The threshold comes from the file's own spacing, not from its constant-rate
    # flag, and this clip is where those disagree: the whole-file grid fit calls
    # it variable, while no two neighbors sit far apart. A constant-rate gate
    # would exempt it from the check entirely; its own measured spacing keeps it
    # covered. Re-adding that gate fails here.
    facts = probe_media(ramped_rate_clip)
    assert not facts.constant_frame_rate
    assert facts.max_timestamp_gap_frame_periods + 0.5 < 2.0
    with VideoReader(ramped_rate_clip, facts=facts) as reader:
        assert sum(1 for _index, _frame in reader) == facts.frame_count
    with _ReaderSkippingPresentationRanks(ramped_rate_clip, facts=facts) as reader:
        reader.skipped_ranks = frozenset({20, 21})
        with pytest.raises(MediaProbeError, match="frame periods later"):
            for _index, _frame in reader:
                pass


def test_a_variable_rate_source_declines_on_its_own_spacing(
    corpus_vfr: Path,
) -> None:
    # A genuinely variable source declines through the same rule as the coarse
    # timescale one -- its own measured spacing reaches the signal, at 2.2
    # periods -- rather than through a separate constant-rate exemption. One
    # rule covers both, and reading with facts injected and no seek is the
    # configuration where the gap check is the active one.
    facts = probe_media(corpus_vfr)
    assert not facts.constant_frame_rate
    assert facts.max_timestamp_gap_frame_periods + 0.5 >= 2.0
    with VideoReader(corpus_vfr, facts=facts) as reader:
        delivered = sum(1 for _index, _frame in reader)
    assert delivered == facts.frame_count


def test_an_unmeasured_spacing_declines_rather_than_checking(
    clips: dict[str, Path],
) -> None:
    # A required field with no default still arrives unmeasured down one path: a
    # persisted row filled in rather than probed. Zero is not inert there -- it
    # sets the threshold at half a period, which every healthy file exceeds on
    # its second frame, so the read raised at frame 1 and delivered 1 of 60. No
    # measurement produces it: the widest step between distinct ascending
    # timestamps is strictly positive, every fixture in this suite measures at
    # least 1.0, and a file without timestamps has no frame rate and returns at
    # the guard above. Declining costs no coverage and is the honest reading of
    # a value that was never taken; the shortfall is still reported, by the
    # delivery count.
    path = clips["cfr_30fps_mp4"]
    facts = probe_media(path)
    assert facts.max_timestamp_gap_frame_periods > 0.0
    unmeasured = replace(facts, max_timestamp_gap_frame_periods=0.0)
    with VideoReader(path, facts=unmeasured) as reader:
        assert sum(1 for _index, _frame in reader) == facts.frame_count
    with _ReaderSkippingPresentationRanks(path, facts=unmeasured) as reader:
        reader.skipped_ranks = frozenset({2, 3})
        with pytest.raises(MediaProbeError) as excinfo:
            for _index, _frame in reader:
                pass
    assert "frame periods later" not in str(excinfo.value)
    assert "delivered" in str(excinfo.value)


def test_an_untimed_source_carries_no_frame_rate_to_reach_the_spacing_guard(
    clips: dict[str, Path], raw_starting_on_non_keyframes: Path
) -> None:
    # The spacing measurement is zero on a source with no packet timestamps, and
    # that zero never reaches the spacing guard: the frame rate is zero too, and
    # the guard above returns on it. Measured by defeating that guard -- the
    # sequential raw read then reaches the frame's own time, which is None on
    # such a source, and fails. What the spacing guard covers is therefore only
    # a timed file whose value was never taken, never a file that has none.
    for path in (
        clips["raw_h264"],
        clips["raw_fractional_rate_h264"],
        raw_starting_on_non_keyframes,
    ):
        facts = probe_media(path)
        assert facts.max_timestamp_gap_frame_periods == 0.0
        assert facts.fps == 0.0
        with VideoReader(path, facts=facts) as reader:
            assert sum(1 for _index, _frame in reader) == facts.frame_count


@requires_svtav1
@requires_av1_frame_split
def test_a_source_declaring_more_frames_than_it_decodes_raises(
    av1_split_clips: dict[str, Path],
) -> None:
    # The shape both delivery checks exist for, as a file rather than a
    # subclass: a bare AV1 stream whose hidden frames each took a synthesized
    # timestamp, so the frame model declares 74 frames over 50 pictures. A
    # subclass cannot pin this half of it -- it stands in for the decoder and so
    # begins after the probe. Both reader paths raise, on different mechanisms:
    # with facts and no index the spacing between decoded frames, without facts
    # the index entry for the frame being returned.
    #
    # The probe cannot see the packet-to-picture divergence itself: every
    # structural measurement below reads clean. What it does see is that this
    # container's timing was invented rather than carried by the file, and it
    # routes the source on that ground alone. The reader's raise is the backstop
    # for a source that reaches it anyway.
    path = av1_split_clips["obu"]
    facts = probe_media(path)
    pictures = decode_md5s(path)
    assert facts.frame_count > len(pictures)
    assert facts.constant_frame_rate
    assert facts.max_timestamp_gap_frame_periods == pytest.approx(1.0)
    assert facts.discard_flagged_packets == 0
    assert facts.leading_non_keyframe_frames == 0
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    assert verdict.analysis_transcode == "required"
    assert "presentation_timing_requires_decode" in verdict.analysis_reasons
    for label, reader in (
        ("frame periods later", VideoReader(path, facts=facts)),
        ("frame 1:", VideoReader(path)),
    ):
        delivered: list[str] = []
        with reader:
            with pytest.raises(MediaProbeError, match=label):
                for _index, frame in reader:
                    delivered.append(frame_md5(frame))
        # It raised rather than handing back a picture belonging elsewhere:
        # everything delivered up to the raise is the frame its index names.
        assert delivered == pictures[: len(delivered)]
        assert len(delivered) < facts.frame_count


@requires_svtav1
@requires_av1_frame_split
def test_a_source_whose_hidden_frames_share_a_timestamp_reads_clean(
    av1_split_clips: dict[str, Path],
) -> None:
    # The same encode, split the same way, written to a container that gives
    # each hidden frame the timestamp of the visible frame it belongs to. The
    # frame model counts distinct timestamps, so 74 packets over 50 timestamps
    # declare 50 frames, and the decoder produces exactly those 50. This file is
    # healthy and every read of it must stay clean.
    #
    # It is also the reason a check comparing packet count against distinct
    # timestamp count cannot be the answer. This file carries more packets than
    # timestamps and is sound; its sibling carries one packet per timestamp and
    # is broken. On the only measured instances of the shape, that comparison
    # points at the healthy file and away from the defective one.
    path = av1_split_clips["matroska"]
    facts = probe_media(path)
    packets, _source = scan_packets_in_process(path)
    assert len(packets) > len({packet.time for packet in packets})
    assert facts.frame_count == len({packet.time for packet in packets})
    assert facts.constant_frame_rate
    assert derive(facts, CHROME_149, DEFAULT_THRESHOLDS).analysis_transcode is None
    pictures = decode_md5s(path)
    assert len(pictures) == facts.frame_count
    for reader in (VideoReader(path, facts=facts), VideoReader(path)):
        with reader:
            assert [frame_md5(frame) for _index, frame in reader] == pictures
    with VideoReader(path, facts=facts) as reader:
        for target in (0, 1, 7, 12, 24, 25, 33, facts.frame_count - 1):
            reader.seek(target)
            ok, frame = reader.read()
            assert ok
            assert frame is not None
            assert frame_md5(frame) == pictures[target]


def test_the_delivery_check_stays_silent_across_a_healthy_source(
    clips: dict[str, Path], open_gop_clip: Path, corpus_vfr: Path
) -> None:
    # The clause neither check may break: a reader must never fail on a source
    # that is fine. Offsets, bounds and strides move the target sequence, a seek
    # and a sparse read move the count origin, and an open-GOP and a genuinely
    # variable-rate source are where a landing legitimately resolves to a rank
    # the seek did not name. Both mechanisms are exercised, because an offset
    # start builds an index and a start at zero does not. None may raise, and
    # each window must deliver its full grid.
    for path in (clips["cfr_30fps_mp4"], open_gop_clip, corpus_vfr):
        facts = probe_media(path)
        for start_frame in (0, 1, 7):
            for end_frame in (None, 1, 7, 40):
                for frame_step in (1, 2, 3, 5, 7):
                    with VideoReader(
                        path,
                        facts=facts,
                        start_frame=start_frame,
                        end_frame=end_frame,
                        frame_step=frame_step,
                    ) as reader:
                        delivered = sum(1 for _index, _frame in reader)
                    window_end = (
                        facts.frame_count
                        if end_frame is None
                        else min(end_frame, facts.frame_count)
                    )
                    expected = len(
                        range(min(start_frame, window_end), window_end, frame_step)
                    )
                    shape = f"{start_frame}/{end_frame}/{frame_step}"
                    assert delivered == expected, f"{path.name} {shape}"
        targets = [0, 1, facts.frame_count // 2, facts.frame_count - 1]
        with VideoReader(path, facts=facts) as reader:
            for target in targets:
                reader.seek(target)
                ok, frame = reader.read()
                assert ok and frame is not None, f"{path.name} seek {target}"
        with VideoReader(path, facts=facts) as reader:
            assert [index for index, _frame in reader.read_frames(targets)] == targets


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
