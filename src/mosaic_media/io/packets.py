"""In-process packet scan for the io seek path. Requires av.

Demux packets without decoding, mirroring probe.ffprobe.scan_packets so the
seek index built here matches the probe's frame model: pts preferred, dts a
whole-file fallback used only when no packet in the stream carries pts, times
in float seconds via the stream time base, the demuxer's trailing flush packet
(pts None, size 0) excluded. The probe's scan_packets stays the authoritative
measurement scanner; this function feeds seeking only, and the two need not
agree packet-for-packet on containers where libavformat synthesizes pts that
ffprobe reports as absent.
"""

from pathlib import Path

import av

from ..probe.errors import MediaProbeError
from ..probe.ffprobe import Packet, TimestampSource


def scan_packets_in_process(
    path: Path,
) -> tuple[tuple[Packet, ...], TimestampSource]:
    pts_packets: list[Packet] = []
    dts_packets: list[Packet] = []
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        time_base = stream.time_base
        if time_base is None:
            message = f"video stream in {path} has no time base for seeking"
            raise MediaProbeError(message)
        for packet in container.demux(stream):
            if packet.size == 0:
                # The demuxer's trailing flush packet: pts None, size 0.
                continue
            keyframe = bool(packet.is_keyframe)
            position = int(packet.pos) if packet.pos is not None else -1
            size = int(packet.size)
            if packet.pts is not None:
                seconds = float(packet.pts * time_base)
                pts_packets.append(
                    Packet(time=seconds, size=size, keyframe=keyframe, pos=position)
                )
            if packet.dts is not None:
                seconds = float(packet.dts * time_base)
                dts_packets.append(
                    Packet(time=seconds, size=size, keyframe=keyframe, pos=position)
                )
    # Whole-file fallback, mirroring scan_packets: dts only when NO packet in
    # the stream carried pts. Never a per-packet mix.
    if pts_packets:
        source: TimestampSource = "pts"
        return tuple(pts_packets), source
    source = "dts"
    return tuple(dts_packets), source
