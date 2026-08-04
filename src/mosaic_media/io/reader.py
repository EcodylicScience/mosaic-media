"""Frame reading through in-process libav bindings (PyAV). Requires numpy and av.

The reader decodes in an open av container: sequential reads decode forward;
a seek resolves the target's preceding keyframe from the packet index, calls
container.seek to that keyframe's presentation timestamp with backward
resolution, accepts a landing at or before that keyframe, resolves where the
decoder actually landed against the index, and counts forward from there,
landing frame-exact -- there is no average-rate index-to-timestamp conversion
to land off target the way OpenCV's CAP_PROP_POS_FRAMES does on variable-rate
files (pinned by the seek suites, including the variable-rate one). The codec
table is a tested invariant (the codec guard), not a trusted bundled binary.
Rotation, scaling, and the output pixel format are applied in process through
one libav filter graph per reader (a transpose for the quarter-turns, hflip
plus vflip for 180), golden-verified against system ffmpeg: bit-exact for
rotation, and within a rounding step for scaling, where driving libswscale
directly instead diverges on the chroma planes.
"""

import bisect
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

import av
import av.error
import numpy
from av.codec.context import Flags2
from av.container import InputContainer
from av.filter.graph import Graph
from av.video.frame import VideoFrame
from av.video.stream import VideoStream

from ..probe.errors import MediaProbeError
from ..probe.facts import MediaFacts
from .index import IndexSpace, SeekIndex, build_seek_index
from .packets import scan_packets_in_process

# idle: no container open. sequential: decoding forward from the window start.
# positioned: decoding forward from an explicit seek target.
_ReaderMode = Literal["idle", "sequential", "positioned"]

# Filter chain per display rotation, each entry a (filter name, argument)
# pair. The quarter-turns are single transposes; 180 composes hflip and vflip,
# matching ffmpeg autorotation's own 180 path. All three are exact pixel
# permutations, golden-verified bit-exact against system-ffmpeg autorotation
# by the rotation suite. A rotation this mapping does not cover raises a clear
# MediaProbeError rather than emitting a wrong orientation.
_ROTATION_FILTERS: dict[int, tuple[tuple[str, str | None], ...]] = {
    90: (("transpose", "cclock"),),
    180: (("hflip", None), ("vflip", None)),
    270: (("transpose", "clock"),),
}


@dataclass(frozen=True, slots=True)
class _Geometry:
    fps: float
    source_frame_count: int
    out_width: int
    out_height: int


class VideoReader:
    """Decode frames from one video through in-process libav bindings (PyAV).

    Injecting `facts` suppresses the metadata probe that sequential reads would
    otherwise run. Seeking and sparse reads additionally need the packet index,
    which `facts` does not carry; inject `index` as well to suppress all probing.
    An injected index must have been built by `scan_packets_in_process` in this
    source's own timestamp space, which `build_seek_index` records; one built
    otherwise is rejected rather than resolved into. A reader constructed without
    facts runs one packet scan when it first opens the container, because the
    source's measured counts select the decoder and demuxer options.

    The source frame count is resolved in this order: `facts.frame_count` when
    facts are injected, then the stream's declared frame count when that is
    positive, then the length of the packet index.

    `read` returns `(False, None)` only once it has delivered every frame its
    window declares; ending short of that raises instead, so `__iter__` and
    `read_batch` cannot mistake a short delivery for a clean end. A `seek`
    restarts the count, which is then measured from the seek target.

    A source whose packets do not all decode raises rather than handing back
    the frame the forward count happened to land on, which the count itself
    cannot see: it completes. Where an index is available each delivered frame
    is checked against the entry for the index it is returned under; where one
    is not, consecutive decoded frames of a constant-rate source are checked
    for the gap a missing frame leaves. Neither builds an index, so injecting
    facts and reading forward still runs no packet scan.

    Every array returned by this reader -- from `read`, `read_batch`,
    `read_frames`, or iteration -- is writable, C-contiguous, and never aliases
    another returned array, so a caller may draw onto one without affecting
    another.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        start_frame: int = 0,
        end_frame: int | None = None,
        frame_step: int = 1,
        resize: tuple[int, int] | None = None,
        grayscale: bool = False,
        hwaccel: bool = False,
        facts: MediaFacts | None = None,
        index: SeekIndex | None = None,
    ) -> None:
        # Set first so __del__ -> close() is safe even if a later line raises:
        # close() reads _closed and _container, so both must exist before the
        # path resolution below can raise.
        self._closed: bool = False
        self._container: InputContainer | None = None
        self._path: Path = Path(path).expanduser().resolve()
        self._start_frame: int = max(0, int(start_frame))
        self._end_frame: int | None = None if end_frame is None else int(end_frame)
        self._frame_step: int = max(1, int(frame_step))
        self._resize: tuple[int, int] | None = (
            None if resize is None else (int(resize[0]), int(resize[1]))
        )
        if self._resize is not None and min(self._resize) <= 0:
            # The scale filter reads a non-positive dimension as "keep the
            # source size", so an unchecked degenerate resize would read
            # successfully while the reported geometry disagreed with the
            # emitted frames. Reject it at construction instead.
            message = (
                f"resize {self._resize} for {self._path} must have a positive "
                "width and height"
            )
            raise MediaProbeError(message)
        self._grayscale: bool = bool(grayscale)
        # hwaccel is retained for signature compatibility and is a no-op: decode
        # is always software. No consumer requests hardware decode today, and the
        # GPU download path's bit-exactness against the framemd5 goldens is
        # unverified. The implementation seam if that changes is
        # av.codec.hwaccel.HWAccel("cuda").
        self._want_hwaccel: bool = bool(hwaccel)
        self._facts: MediaFacts | None = facts
        self._index: SeekIndex | None = index
        self._ignore_edit_list: bool = False
        self._index_space: IndexSpace = "container_default"
        self._recovery_resolved: bool = False
        self._geometry: _Geometry | None = None
        self._stream: VideoStream | None = None
        self._decode_iterator: Iterator[VideoFrame] | None = None
        # The first frame after a seek, decoded eagerly to verify and resolve
        # the landing and then held so _read_current returns it rather than a
        # second decode.
        self._pending_frame: VideoFrame | None = None
        self._rotation_degrees: int = 0
        self._conversion_graph: Graph | None = None
        self._mode: _ReaderMode = "idle"
        self._decoder_pos: int = 0  # next absolute source frame the decoder emits
        self._target: int = 0  # next absolute frame read() returns
        self._last_index: int = 0  # index of the most recently returned frame
        self._delivered: int = 0
        self._count_origin: int = 0  # frame index the delivery count starts at
        # The previous frame emitted in the current decode segment, for the
        # consecutive-gap check. None at the start of a segment.
        self._previous_decoded_time: float | None = None

    # --- Container lifecycle ---

    def _resolve_recovery_options(self) -> None:
        """Decide the decoder and demuxer options before the container opens.

        Injected facts answer this without a scan, which is what keeps the
        injected-facts path free of the demux pass it does not run today. Without
        facts the scan runs here and its index is built immediately, so no packet
        tuple outlives this method and `_ensure_index` has nothing to repeat.

        A reader given an index but no facts still scans once. That scan is not
        redundant with the index: it decides whether to open the container with
        the edit list ignored, and it is the only statement about the source
        independent of the index's own recorded space, so it is what lets
        `_ensure_index` reject a container-default index handed to a source whose
        decode ignores the edit list. The
        index's `space` could answer the question on its own, and then nothing
        would be left to check it against.
        """
        if self._recovery_resolved:
            return
        if self._facts is not None:
            self._ignore_edit_list = self._facts.discard_flagged_packets > 0
            self._recovery_resolved = True
            if self._ignore_edit_list:
                self._index_space = "edit_list_ignored"
            return
        packets, _source = scan_packets_in_process(self._path)
        self._ignore_edit_list = any(packet.discard for packet in packets)
        if self._ignore_edit_list:
            self._index_space = "edit_list_ignored"
        # Build the index here rather than keeping the packets for a later
        # _ensure_index. A sequential read of a container that declares its frame
        # count never reaches _ensure_index, so holding the tuple would retain
        # every packet of the file for the reader's lifetime to serve a call that
        # never comes. The index is a fraction of its size.
        if self._index is None:
            if self._ignore_edit_list:
                # A source decoded with the edit list ignored must be indexed
                # in that same space, so the container-default scan just paid
                # cannot be reused. The re-scan sits inside this branch, not
                # beside the space assignment above: its packets feed nothing
                # but the index, so a caller who brought one would pay a whole
                # second demux for a tuple that is discarded.
                packets, _source = scan_packets_in_process(
                    self._path, ignore_edit_list=True
                )
            self._index = build_seek_index(
                packets, source="in_process", space=self._index_space
            )
        self._recovery_resolved = True

    def _ensure_container(self) -> tuple[InputContainer, VideoStream]:
        container = self._container
        stream = self._stream
        if container is not None and stream is not None:
            return container, stream
        self._resolve_recovery_options()
        options = {"ignore_editlist": "1"} if self._ignore_edit_list else {}
        try:
            container = av.open(str(self._path), options=options)
        except av.error.FFmpegError as exc:
            message = f"failed to open {self._path}: {exc}"
            raise MediaProbeError(message) from exc
        if not container.streams.video:
            container.close()
            message = f"no video stream in {self._path}"
            raise MediaProbeError(message)
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"  # frame threading; the decode loop drains on EOF
        self._container = container
        self._stream = stream
        return container, stream

    def _apply_show_all(self, stream: VideoStream, *, at_stream_start: bool) -> None:
        """Emit packets preceding the first keyframe of this decode segment
        rather than discarding them, but only when the segment begins at the
        start of the stream -- where a source's own leading non-keyframes live.
        They decode to the decoder's own output rather than to pictures, because
        the reference they difference against is not in the file -- but that
        output is what the file contains, and dropping it leaves indices the
        frame model declares unreachable.

        A segment that begins elsewhere, after a backward seek, must decode with
        the flag clear. It would otherwise emit the previous group's leading
        pictures, decoded against references the seek discarded -- and those
        pictures carry the timestamps of real frames, so they occupy the index
        ranks the frame model assigns to them. A caller reading at one of those
        indices would get content decoded from nothing.
        """
        if at_stream_start:
            stream.codec_context.flags2 |= Flags2.show_all
        else:
            stream.codec_context.flags2 &= ~Flags2.show_all

    def _probe_rotation(self) -> int:
        # The av stream exposes no rotation getter before decode, so open a
        # short-lived container, read the first frame's rotation, and close it,
        # leaving the reader's own decode position untouched.
        try:
            with av.open(str(self._path)) as container:
                stream = container.streams.video[0]
                for frame in container.decode(stream):
                    return int(frame.rotation)
        except av.error.FFmpegError as exc:
            message = f"failed to decode {self._path}: {exc}"
            raise MediaProbeError(message) from exc
        return 0

    # --- Metadata resolution ---

    def _ensure_index(self) -> SeekIndex:
        # Above both branches, not inside the first. The space an injected index
        # is validated against is only known once the options are resolved, and
        # _position_at reaches here before _ensure_container -- so with facts
        # injected the resolver has not otherwise run, and the comparison below
        # would use __init__'s placeholder. A reader decoding with the edit list
        # ignored, handed an index built in the container-default space, would
        # then pass validation and decode in one timestamp space against an
        # index built in the other.
        self._resolve_recovery_options()
        if self._index is None:
            # Only reached with facts injected: the factless path built the index
            # while resolving its options, because it had scanned already.
            packets, _source = scan_packets_in_process(
                self._path, ignore_edit_list=self._ignore_edit_list
            )
            self._index = build_seek_index(
                packets, source="in_process", space=self._index_space
            )
        elif (
            self._index.source != "in_process" or self._index.space != self._index_space
        ):
            message = (
                f"seek index for {self._path} was built by the "
                f"{self._index.source} scanner in the {self._index.space} "
                f"timestamp space, but this reader decodes in the "
                f"{self._index_space} space with the in-process scanner; "
                "resolving across the two returns the wrong frame"
            )
            raise MediaProbeError(message)
        return self._index

    def _ensure_ready(self) -> _Geometry:
        if self._geometry is not None:
            return self._geometry
        if self._facts is not None:
            source_width = self._facts.width
            source_height = self._facts.height
            fps = self._facts.fps
            source_frame_count = self._facts.frame_count
            rotation_degrees = self._facts.rotation_degrees
        else:
            _container, stream = self._ensure_container()
            source_width = int(stream.width)
            source_height = int(stream.height)
            fps = float(stream.average_rate or 0)
            declared_frame_count = int(stream.frames)
            if declared_frame_count > 0:
                source_frame_count = declared_frame_count
            else:
                source_frame_count = self._ensure_index().frame_count
            rotation_degrees = self._probe_rotation()
        self._rotation_degrees = int(rotation_degrees)
        normalized_rotation = self._rotation_degrees % 360
        if normalized_rotation != 0 and normalized_rotation not in _ROTATION_FILTERS:
            message = f"unsupported rotation {self._rotation_degrees} for {self._path}"
            raise MediaProbeError(message)
        if self._resize is not None:
            # A resize wins over the rotation swap; the scale filter runs after
            # the transpose, so the output is exactly the requested
            # (width, height).
            out_width, out_height = self._resize
        elif self._rotation_degrees % 180 == 90:
            # A quarter-turn source is emitted in displayed orientation: the
            # reader rotates each frame in its conversion graph, so displayed
            # width and height are the coded dimensions swapped. Reporting and
            # shaping in that orientation matches ffmpeg autorotation and cv2
            # auto-orientation; the byte count is unchanged (w*h*3 is symmetric),
            # so only the reported shape distinguishes the two.
            out_width, out_height = source_height, source_width
        else:
            out_width, out_height = source_width, source_height
        self._geometry = _Geometry(
            fps=fps,
            source_frame_count=source_frame_count,
            out_width=out_width,
            out_height=out_height,
        )
        return self._geometry

    def _window_end(self, geometry: _Geometry) -> int:
        """The exclusive upper frame bound of the reader's window, clamped to
        the source length. Sequential and positioned reads both stop here, and
        seek() rejects a target at or beyond it."""
        if self._end_frame is None:
            return geometry.source_frame_count
        return min(self._end_frame, geometry.source_frame_count)

    # --- Properties ---

    @property
    def width(self) -> int:
        return self._ensure_ready().out_width

    @property
    def height(self) -> int:
        return self._ensure_ready().out_height

    @property
    def fps(self) -> float:
        return self._ensure_ready().fps

    @property
    def frame_count(self) -> int:
        geometry = self._ensure_ready()
        end = self._window_end(geometry)
        start = min(self._start_frame, end)
        return len(range(start, end, self._frame_step))

    # --- Decode primitives ---

    def _decode_next(self) -> VideoFrame | None:
        """Pull the next frame from the decode iterator in presentation order,
        or None at a clean end of stream. A truncated or otherwise undecodable
        file raises FFmpegError here, which maps to MediaProbeError, so a decode
        that dies mid-stream surfaces as an error here rather than later, as a
        shortfall counted at end of stream."""
        pending = self._pending_frame
        if pending is not None:
            self._pending_frame = None
            return pending
        iterator = self._decode_iterator
        if iterator is None:
            return None
        try:
            return next(iterator)
        except StopIteration:
            return None
        except av.error.FFmpegError as exc:
            message = f"failed to decode {self._path}: {exc}"
            raise MediaProbeError(message) from exc

    def _build_conversion_graph(self, geometry: _Geometry) -> Graph:
        """Build the reader's one conversion graph: rotation, then scaling, then
        the output pixel format.

        Stage order is load-bearing. The transpose runs first so a quarter-turn
        source is emitted in displayed orientation, and the scale runs after it
        so a requested resize wins over the rotation dimension swap and the
        output is exactly the requested size.

        Scaling here rather than through VideoFrame.reformat is what keeps the
        color path exact: reformat drives libswscale with different chroma plane
        handling and lands up to 76 levels per channel away from ffmpeg's `-vf
        scale`, while this filter is what ffmpeg itself runs.
        """
        stream = self._stream
        if stream is None:
            message = f"cannot build the conversion graph before opening {self._path}"
            raise MediaProbeError(message)
        stages: list[tuple[str, str | None]] = list(
            _ROTATION_FILTERS.get(self._rotation_degrees % 360, ())
        )
        if self._resize is not None:
            # The scale filter already defaults to bicubic, matching ffmpeg's
            # own -vf scale; setting it explicitly pins that against a
            # libswscale default change. Bilinear drifts about 24 gray levels
            # off the goldens.
            scale_arguments = (
                f"{geometry.out_width}:{geometry.out_height}:flags=bicubic"
            )
            stages.append(("scale", scale_arguments))
        stages.append(("format", "gray" if self._grayscale else "bgr24"))
        graph = Graph()
        previous = graph.add_buffer(template=stream)
        for name, argument in stages:
            node = graph.add(name) if argument is None else graph.add(name, argument)
            previous.link_to(node)
            previous = node
        sink = graph.add("buffersink")
        previous.link_to(sink)
        try:
            graph.configure()
        except av.error.FFmpegError as exc:
            message = f"failed to build the conversion graph for {self._path}: {exc}"
            raise MediaProbeError(message) from exc
        self._conversion_graph = graph
        return graph

    def _emit(self, geometry: _Geometry, frame: VideoFrame) -> numpy.ndarray:
        # The graph is built here rather than during geometry resolution because
        # _ensure_ready does not open the container when probe facts are
        # injected, and the buffer source is templated from the stream. By the
        # first emit a frame has been decoded, so the container is open.
        graph = self._conversion_graph
        if graph is None:
            graph = self._build_conversion_graph(geometry)
        graph.vpush(frame)
        converted = graph.vpull()
        # to_ndarray takes no format argument: the graph already emits the
        # output pixel format, and passing one would ask libswscale for a no-op
        # conversion and rebuild a scaling context for every frame.
        # ascontiguousarray is a no-op when the line size already matches and
        # copies when the graph's line size exceeds the row length, which the
        # quarter-turn rotations and scaling commonly cause and full-width
        # output does not.
        return numpy.ascontiguousarray(converted.to_ndarray())

    def _to_stream_offset(self, stream: VideoStream, keyframe_time: float) -> int:
        time_base = stream.time_base
        if time_base is None:
            message = f"video stream in {self._path} has no time base for seeking"
            raise MediaProbeError(message)
        return int(round(keyframe_time / float(time_base)))

    def _position_at(self, target: int) -> None:
        """Position the decoder so its next emitted frame lies at or before
        `target`. Reuse the live decoder when it is already positioned between the
        target's preceding keyframe and the target; otherwise seek to that
        keyframe's presentation timestamp with backward resolution."""
        index = self._ensure_index()
        keyframe_index, keyframe_time = index.preceding_keyframe(target)
        reusable = (
            self._container is not None
            and self._decode_iterator is not None
            and self._mode == "positioned"
            and keyframe_index <= self._decoder_pos <= target
        )
        if reusable:
            return
        geometry = self._ensure_ready()
        container, stream = self._ensure_container()
        self._apply_show_all(stream, at_stream_start=keyframe_index == 0)
        offset = self._to_stream_offset(stream, keyframe_time)
        try:
            container.seek(offset, stream=stream, backward=True)
        except av.error.FFmpegError as exc:
            message = f"failed to seek {self._path} to frame {target}: {exc}"
            raise MediaProbeError(message) from exc
        self._decode_iterator = container.decode(stream)
        self._decoder_pos = keyframe_index
        self._pending_frame = None
        # A new decode segment: the frame about to be decoded has no predecessor
        # in it, and the gap to whatever the previous segment last emitted is a
        # seek, not a missing frame. Set after the reuse return above, which
        # continues the live segment rather than starting one.
        self._previous_decoded_time = None
        # A landing at or before the requested keyframe is legitimate:
        # container seek granularity is coarser than the keyframe list on some
        # formats, so the decoder can land on an earlier picture than the
        # index named. A landing after the requested keyframe means the
        # target's own references were skipped, so counting forward would
        # decode from the wrong prefix, and that still raises below. The
        # eager decode resolves the landing's index rank rather than merely
        # checking it, so the arithmetic frame count that discards forward to
        # the target starts from where the decoder actually is. The decoded
        # frame is held for _read_current.
        first = self._decode_next()
        if first is not None:
            observed = float(first.time)
            tolerance = 0.5 / geometry.fps if geometry.fps > 0 else 0.0
            if geometry.fps > 0 and observed > keyframe_time + tolerance:
                message = (
                    f"seek landing for {self._path} at frame {target}: expected "
                    f"keyframe time {keyframe_time} or earlier but decoded "
                    f"{observed}"
                )
                raise MediaProbeError(message)
            # Landing earlier is legitimate: container seek granularity is
            # coarser than the keyframe list on some formats. Resolve where the
            # decoder actually is, then count forward from there.
            self._decoder_pos = self._index_rank_at(observed, tolerance)
            self._pending_frame = first

    def _index_rank_at(self, observed: float, tolerance: float) -> int:
        """The index rank of the frame the decoder just emitted.

        The index and the decode are built by one scanner in one timestamp
        space, so their times agree exactly and the tolerance is margin, not
        noise coverage.

        An index's recorded space is provenance, not a check on its timestamps:
        nothing re-derives them, so an index carrying the wrong label passes
        that check and shifts every landing it resolves. What catches a shift is
        where the seek lands, not which way the label is wrong. A landing later
        than the index claimed is rejected by `_position_at`. A landing at or
        before it is legitimate -- an earlier keyframe is decoded forward from
        -- and reaches here instead; the tolerance is half the index spacing, so
        the acceptance windows tile the timeline without gaps and any landing
        inside the index span resolves to some rank, silently the wrong one.
        Only a landing outside that span matches no entry, which is what raises
        here.
        """
        index = self._ensure_index()
        position = bisect.bisect_left(index.frame_times, observed - tolerance)
        if position < len(index.frame_times) and (
            abs(index.frame_times[position] - observed) <= tolerance
        ):
            return position
        message = (
            f"seek landing for {self._path} decoded a frame at {observed}, "
            "which matches no entry in its seek index"
        )
        raise MediaProbeError(message)

    def _verify_delivery(self, geometry: _Geometry, frame: VideoFrame) -> None:
        """Raise unless `frame` is the one `self._target` names.

        `_read_current` reaches its target by counting decoded frames, which
        assumes every decode advances exactly one presentation rank. A source
        carrying packets that decode to no frame breaks that assumption without
        breaking the count: the loop still completes, and it completes on a
        later frame, which is then returned under the requested index. Nothing
        upstream catches it. The seek path's only other backstop resolves the
        *landing*, which is a real packet timestamp and therefore always in the
        index, and the delivery count sees a shortfall only once the window
        runs out -- after the wrong frames have been handed back.

        The frame's own presentation time against the index entry for
        `self._target` is what separates the two cases, and it is the whole
        mechanism. The reader holds no verdict and must not acquire one: policy
        is injected here, `MediaFacts` carries no verdict, and `derive` needs a
        profile and thresholds this class never receives.

        `self._target` is an absolute source frame index on every path that
        reaches here -- `read` walks it from `_start_frame` by `_frame_step`,
        `seek` assigns it outright, and `read_frames` seeks per target -- and
        the index is in absolute source ranks too, so the entry is
        `frame_times[self._target]`. `_start_frame`, `_frame_step` and
        `_count_origin` choose which targets are visited and where the delivery
        count starts; none of them shifts this mapping.

        Skipped wherever there is nothing to compare against, because firing on
        a healthy source would be a worse defect than the one this catches:

        - No index. Building one here would put a packet scan on the injected
          facts sequential and strided reads, which are pinned at zero scans
          because the performance gate measures them. `_check_decode_gap`
          covers that region instead, from the decoded timestamps alone.
        - No measured frame rate, so no frame period to size a tolerance with.
          A stream whose packets carry no timestamps has none, and its frame
          times are placeholders. `_position_at`'s landing check is guarded the
          same way.
        - A target past the end of the index, which offers no entry to compare
          against. The two scanners can disagree on a stream where libavformat
          synthesizes timestamps ffprobe reports as absent, so the declared
          frame count and the index length are not guaranteed equal even though
          they are equal on every source measured here.

        The tolerance is half a frame period, as it is for the landing check: a
        frame one period away is a different frame, and the two scanners agree
        exactly on a source whose index and decode share a timestamp space, so
        the margin is never carrying float noise.
        """
        index = self._index
        if index is None or geometry.fps <= 0:
            return
        if self._target >= len(index.frame_times):
            return
        observed = float(frame.time)
        expected = index.frame_times[self._target]
        if abs(observed - expected) <= 0.5 / geometry.fps:
            return
        message = (
            f"{self._path} frame {self._target}: its seek index places that "
            f"frame at {expected} but the decoder delivered one at {observed}; "
            "the source carries packets that decode to no frame, so counting "
            "forward runs past the requested frame, and it must be transcoded "
            "before it can be read per frame"
        )
        raise MediaProbeError(message)

    def _check_decode_gap(self, geometry: _Geometry, frame: VideoFrame) -> None:
        """Raise when consecutive decoded frames sit further apart than one
        frame period, which is a frame the decoder did not produce.

        The half of the delivery contract `_verify_delivery` cannot reach.
        These two never both run: this one applies only when there is no index,
        because an index makes `_verify_delivery` available and that check is
        strictly stronger -- it compares each delivered frame against the entry
        for its own index, so it catches a mislabel wherever it happens rather
        than inferring one from a spacing. This check exists because building
        an index costs a packet scan the injected-facts sequential and strided
        reads must not pay, and those reads are otherwise covered by nothing:
        an index exists only when the reader was constructed without facts or
        has since seeked, so a caller that injects facts and only reads forward
        has neither check without this one.

        `end_frame` is why the delivery count is not enough on its own. That
        count reports a shortfall when a window runs out early, and a bounded
        window does not: measured on a source missing two frames,
        `end_frame=40` delivered 40 frames, 38 of them the wrong picture, and
        ended clean.

        The threshold comes from the file, not from a constant. A fixed one
        cannot be sound: a container that quantizes timestamps to a coarse tick
        carries a constant rate whose neighbors are unevenly spaced -- measured
        at 1.66 periods for 30 fps written into a 1/36 timescale, and at 1.60
        for 23.976 into 1/30 -- and every such file is analysis-ready by this
        package's own verdict. A constant at 1.5 raises on all of them, which
        is a worse defect than the one this catches. `MediaFacts` carries the
        widest step the file's own timestamps take, and the threshold is that
        plus the half-period margin the landing and index checks also carry.

        A non-positive spacing declines too, at the other end of the same range.
        No source measures one: the value is the widest step between distinct
        ascending timestamps, so it is strictly positive wherever a file has
        timestamps at all, and a file without them carries no frame rate and
        returns at the guard above. Every fixture in this suite measures at
        least 1.0. A zero therefore says the field was never measured -- a
        persisted row filled in rather than probed, which is the one path that
        can still supply a required no-default field unmeasured -- and it is not
        an inert placeholder: acting on it sets the threshold at half a period,
        which every healthy file exceeds on its second frame.

        Above 2.0 periods the check declines rather than guesses. One missing
        frame puts two neighbors at the sum of the two steps it spanned, which
        on a uniform file is 2.0, so once a file's own spacing plus margin
        reaches 2.0 the signal and the tolerance overlap and no comparison of
        spacings can separate them. Measured on the quantized files above: the
        legitimate steps alternate 0.83 and 1.66, so a frame dropped between two
        short ones produces exactly 1.66 -- indistinguishable from a step the
        file takes anyway. Declining is the honest outcome, and those files are
        left to the index check whenever they reach it.

        The same rule retires the variable-rate exemption this check first
        carried. A genuinely variable source declines here on its own measured
        spacing -- 2.25 periods on a 30 fps recording with a 10 fps stretch --
        rather than through `constant_frame_rate`, and a mildly variable one is
        now checked instead of exempted: measured on a file whose rate ramps
        from 30 fps to 24, the grid fit calls it variable at 1.61 periods of
        drift while no two neighbors sit more than 1.12 apart, so it is checked
        at 1.62 where the flag would have exempted it entirely.
        """
        facts = self._facts
        if self._index is not None or facts is None:
            return
        if geometry.fps <= 0:
            return
        if facts.max_timestamp_gap_frame_periods <= 0.0:
            return
        threshold = facts.max_timestamp_gap_frame_periods + 0.5
        if threshold >= 2.0:
            return
        # Read after those conditions, never before. A frame carries no time when
        # its packet carried none, and `av` types that as a float it does not
        # always hold; the sources it happens on are exactly the ones a positive
        # frame rate excludes, so the condition above is what makes this safe.
        observed = float(frame.time)
        previous = self._previous_decoded_time
        self._previous_decoded_time = observed
        if previous is None:
            return
        gap = (observed - previous) * geometry.fps
        if gap <= threshold:
            return
        message = (
            f"{self._path} decoded a frame at {observed} directly after one at "
            f"{previous}, {gap:.2f} frame periods later, where its own "
            f"timestamps step at most {threshold - 0.5:.2f}; the source carries "
            "packets that decode to no frame, so the frames between them are "
            "missing and every later index is mislabeled. It must be "
            "transcoded before it can be read per frame"
        )
        raise MediaProbeError(message)

    def _read_current(self, geometry: _Geometry) -> numpy.ndarray | None:
        """Decode forward to `self._target` and return that frame, advancing the
        decoder position. None at end of stream."""
        while self._decoder_pos < self._target:
            skipped = self._decode_next()
            if skipped is None:
                return None
            # Checked on the discarded frames too: a stride steps over the gap,
            # so a check that saw only delivered frames would measure the
            # stride rather than the spacing.
            self._check_decode_gap(geometry, skipped)
            self._decoder_pos += 1
        frame = self._decode_next()
        if frame is None:
            return None
        self._check_decode_gap(geometry, frame)
        self._verify_delivery(geometry, frame)
        self._decoder_pos += 1
        return self._emit(geometry, frame)

    # --- Public reads ---

    def read(self) -> tuple[bool, numpy.ndarray | None]:
        if self._closed:
            return False, None
        geometry = self._ensure_ready()
        window_end = self._window_end(geometry)
        if self._mode == "idle":
            self._target = self._start_frame
            self._count_origin = self._start_frame
            self._mode = "sequential"
            if self._start_frame < window_end:
                self._start_reading()
        if self._target >= window_end:
            return False, None
        self._last_index = self._target
        frame = self._read_current(geometry)
        if frame is None:
            expected = len(range(self._count_origin, window_end, self._frame_step))
            if self._delivered < expected:
                # Where the count came from, rather than always crediting facts:
                # a factless reader takes it from the stream's declared frame
                # count or from the packet index, and naming the wrong source
                # sends whoever reads this to check a value that never applied.
                declared_by = (
                    "its facts declare"
                    if self._facts is not None
                    else "its container and packet index declare"
                )
                message = (
                    f"{self._path} delivered {self._delivered} of {expected} "
                    f"frames {declared_by}; the source carries packets that "
                    "decode to no frame, and its analysis verdict requires a "
                    "transcode before it can be read"
                )
                raise MediaProbeError(message)
            return False, None
        self._delivered += 1
        self._target += self._frame_step
        return True, frame

    def _start_reading(self) -> None:
        if self._start_frame > 0:
            # Position the first read through the seek path rather than decoding
            # the discarded prefix.
            self._position_at(self._start_frame)
        else:
            container, stream = self._ensure_container()
            self._apply_show_all(stream, at_stream_start=True)
            self._decode_iterator = container.decode(stream)
            self._decoder_pos = 0
            self._previous_decoded_time = None

    def read_batch(self, batch_size: int) -> tuple[numpy.ndarray, numpy.ndarray]:
        geometry = self._ensure_ready()
        indices: list[int] = []
        frames: list[numpy.ndarray] = []
        for _ in range(batch_size):
            ok, frame = self.read()
            if not ok or frame is None:
                break
            indices.append(self._last_index)
            frames.append(frame)
        if not frames:
            if self._grayscale:
                empty = numpy.empty(
                    (0, geometry.out_height, geometry.out_width), dtype=numpy.uint8
                )
            else:
                empty = numpy.empty(
                    (0, geometry.out_height, geometry.out_width, 3),
                    dtype=numpy.uint8,
                )
            return numpy.empty(0, dtype=numpy.int64), empty
        return numpy.asarray(indices, dtype=numpy.int64), numpy.stack(frames)

    def seek(self, frame_index: int) -> None:
        if self._closed:
            message = "reader is closed"
            raise MediaProbeError(message)
        geometry = self._ensure_ready()
        target = int(frame_index)
        window_end = self._window_end(geometry)
        if target < self._start_frame or target >= window_end:
            message = (
                f"frame index {target} out of range [{self._start_frame}, {window_end})"
            )
            raise IndexError(message)
        self._position_at(target)
        self._mode = "positioned"
        self._target = target
        self._delivered = 0
        self._count_origin = target

    def read_frames(
        self, indices: Sequence[int]
    ) -> Iterator[tuple[int, numpy.ndarray]]:
        if self._closed:
            message = "reader is closed"
            raise MediaProbeError(message)
        geometry = self._ensure_ready()
        targets = sorted({int(index) for index in indices})
        if not targets:
            return
        index = self._ensure_index()
        for group in index.group_by_gop(targets):
            for target in group:
                self.seek(target)
                frame = self._read_current(geometry)
                if frame is None:
                    message = f"failed to decode frame {target} from {self._path}"
                    raise MediaProbeError(message)
                self._last_index = target
                # Leave the positioned cursor at the decoder's true next frame,
                # set before the yield so it holds whether the caller consumes
                # the whole generator or abandons it mid-iteration. A following
                # read() then returns that frame with the correct index instead
                # of mislabeling it as this sparse target.
                self._target = self._decoder_pos
                # Rebase the shortfall count onto that cursor. read() counts one
                # delivery per grid slot from _count_origin, and a sparse target
                # need not lie on the grid, so counting this frame would leave a
                # surplus that hides a one-slot shortfall at a stride above one.
                # Excluding it from both sides is what seek() already does.
                self._delivered = 0
                self._count_origin = self._target
                yield target, frame

    def __iter__(self) -> Iterator[tuple[int, numpy.ndarray]]:
        while True:
            ok, frame = self.read()
            if not ok or frame is None:
                break
            yield self._last_index, frame

    # --- Cleanup and context management ---

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            container = self._container
            self._container = None
            # Released alongside the container: the graph holds a frame pool,
            # which a closed but still referenced reader would otherwise keep
            # alive.
            self._conversion_graph = None
            if container is not None:
                container.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def __len__(self) -> int:
        return self.frame_count
