"""In-process packet scan for the io seek path. Requires av.

Demux packets without decoding, mirroring probe.ffprobe.scan_packets so the
seek index built here matches the probe's frame model: pts preferred, dts a
whole-file fallback used only when no packet in the stream carries pts, times
in float seconds via the stream time base, the demuxer's trailing flush packet
(pts None, size 0) excluded. The probe's scan_packets stays the authoritative
measurement scanner; this function feeds seeking only, and the two need not
agree packet-for-packet on containers where libavformat synthesizes pts that
ffprobe reports as absent.

`ignore_edit_list` opens the container with libavformat's `ignore_editlist`
option. The demuxer delivers every packet either way -- a container edit list
does not withhold packets, it marks them "do not present" -- but a default
decode honors that mark and emits fewer frames than this scan has timestamps.
The index is complete and the decode is short, so index rank N stops
corresponding to decoded frame N. Measured on the suite's edit-list fixture,
built by cutting a committed clip with `-ss 0.2 -c copy`: 50 packets
demultiplexed either way, 5 of them flagged, 45 frames decoded by default and
50 with the option set.

The option clears the marks, and it also moves the timestamps themselves,
because the edit list's shift is applied at demultiplex time. Either way the
consequence is the same: a source carrying the flag must have its index built
in the space its decode runs in, so both are gated or neither is.
"""

from pathlib import Path

import av
import av.error

from ..probe.errors import MediaProbeError
from ..probe.ffprobe import Packet, TimestampSource


def scan_packets_in_process(
    path: Path, *, ignore_edit_list: bool = False
) -> tuple[tuple[Packet, ...], TimestampSource]:
    pts_packets: list[Packet] = []
    dts_packets: list[Packet] = []
    options = {"ignore_editlist": "1"} if ignore_edit_list else {}
    try:
        container = av.open(str(path), options=options)
    except av.error.FFmpegError as exc:
        message = f"failed to open {path}: {exc}"
        raise MediaProbeError(message) from exc
    with container:
        if not container.streams.video:
            message = f"no video stream in {path}"
            raise MediaProbeError(message)
        stream = container.streams.video[0]
        time_base = stream.time_base
        if time_base is None:
            message = f"video stream in {path} has no time base for seeking"
            raise MediaProbeError(message)
        saw_payload = False
        for packet in container.demux(stream):
            if packet.size == 0:
                # The demuxer's trailing flush packet: pts None, size 0.
                continue
            saw_payload = True
            keyframe = bool(packet.is_keyframe)
            position = int(packet.pos) if packet.pos is not None else -1
            size = int(packet.size)
            if packet.pts is not None:
                seconds = float(packet.pts * time_base)
                pts_packets.append(
                    Packet(
                        time=seconds,
                        size=size,
                        keyframe=keyframe,
                        pos=position,
                        discard=bool(packet.is_discard),
                    )
                )
            if packet.dts is not None:
                seconds = float(packet.dts * time_base)
                dts_packets.append(
                    Packet(
                        time=seconds,
                        size=size,
                        keyframe=keyframe,
                        pos=position,
                        discard=bool(packet.is_discard),
                    )
                )
    # Whole-file fallback, mirroring scan_packets: dts only when NO packet in
    # the stream carried pts. Never a per-packet mix.
    if pts_packets:
        source: TimestampSource = "pts"
        return tuple(pts_packets), source
    if dts_packets:
        source = "dts"
        return tuple(dts_packets), source
    if saw_payload:
        # A raw elementary stream: packets exist but none carries a timestamp,
        # so no seek index can be built. Refusing loudly beats the alternative
        # of a reader that silently reports zero frames.
        message = (
            f"no packet timestamps in {path}: a raw elementary stream has no "
            "seek index; read it sequentially with facts= injected, or remux "
            "it into a container first"
        )
        raise MediaProbeError(message)
    source = "dts"
    return tuple(dts_packets), source
