"""Two derived values minted from one packet scan.

`content_digest` pins the coded content and survives a container rewrite that
preserves the elementary stream. `video_uuid` pins content and exact timing and
survives nothing but the same content at the same timing -- it is the value safe
to name a directory from, and the only one that may be compared for identity.

Standard library only. This module is in the probe core, which must import on a
machine that has ffmpeg and nothing else.

The digest is defined against demuxer output, not raw file bytes: a packet's
payload hash is what libavformat hands ffprobe. An ffmpeg upgrade that changes
those bytes for a container is a format break, answered by bumping the format
tag and re-minting, not by silently producing values that no longer compare.

Every packet contributes its payload hash, so two files alias only if every
packet's hash collides. The weakest reachable case is a two-packet timed file --
a one-packet timed file cannot be probed at all, because measuring timing needs
two distinct timestamps -- which leaves an accidental-collision probability far
inside the target for any real recording.

This is not a security primitive. The payload hash is a checksum, not a
collision-resistant hash, so a party who can craft input files can produce a
colliding pair deliberately. The guarantee is against accidental collision
between independently produced recordings.
"""

import hashlib
import struct
import uuid
from dataclasses import dataclass
from typing import Literal

from .facts import MediaFacts
from .ffprobe import Header, Packet

CONTENT_FORMAT_TAG = b"mosaic-media/content/1"
VIDEO_FORMAT_TAG = b"mosaic-media/video/1"

DIGEST_BYTES = 16

# The coarsest timestamp granularity in common containers: Matroska stores
# durations on a millisecond scale, so a rewrite into it requantizes every
# timestamp to 1 ms.
TIMESTAMP_QUANTUM_SECONDS = 0.001

# Margin over the drift that quantization actually produces. Measured across
# mp4, Matroska, MPEG-TS, faststart, and a Matroska round trip, the observed
# drift ran 0.27 to 0.38 of one quantum per file second, so 4.0 leaves roughly a
# 10x margin. Provisional until re-measured across a real corpus.
DRIFT_SAFETY = 4.0

DuplicateVerdict = Literal[
    "duplicate", "different_timing", "timing_unknown", "unminted", "distinct"
]


def _encode_integer(value: int) -> bytes:
    return struct.pack("<q", value)


def _encode_boolean(value: bool) -> bytes:
    return b"\x01" if value else b"\x00"


def _encode_float(value: float) -> bytes:
    return struct.pack("<d", value)


def _encode_bytes(value: bytes) -> bytes:
    return struct.pack("<I", len(value)) + value


def _encode_string(value: str) -> bytes:
    return _encode_bytes(value.encode("utf-8"))


def content_digest_input(header: Header, packets: tuple[Packet, ...]) -> bytes:
    """The exact bytes hashed into `content_digest`.

    Public so a test can pin them against a golden vector: an encoding or
    ordering change that a same-machine round trip would hide is exactly the
    change that breaks every already-minted value.

    Every element is length-prefixed or fixed-width, so no two distinct inputs
    can serialize to the same byte string. Timestamps are absent on purpose --
    a container change requantizes them, and they enter `video_uuid` instead.
    """
    parts = [
        _encode_bytes(CONTENT_FORMAT_TAG),
        _encode_string(header.codec_name),
        _encode_string(header.pixel_format),
        _encode_string(header.color_range),
        _encode_string(header.color_primaries),
        _encode_string(header.color_transfer),
        _encode_integer(header.width),
        _encode_integer(header.height),
        _encode_integer(header.rotation_degrees),
        _encode_boolean(header.square_pixels),
        _encode_boolean(header.progressive),
        _encode_integer(len(packets)),
    ]
    for packet in packets:
        parts.append(_encode_integer(packet.size))
        parts.append(_encode_boolean(packet.keyframe))
        # Hashed whole, algorithm prefix included, so changing the algorithm
        # changes every digest instead of producing an incomparable one that
        # looks comparable.
        parts.append(_encode_string(packet.data_hash))
    return b"".join(parts)


def video_uuid_input(
    content_bytes: bytes, timing_measured: bool, packets: tuple[Packet, ...]
) -> bytes:
    """The exact bytes hashed into `video_uuid`."""
    parts = [
        _encode_bytes(VIDEO_FORMAT_TAG),
        _encode_bytes(content_bytes),
        _encode_boolean(timing_measured),
        _encode_integer(len(packets)),
    ]
    for packet in packets:
        parts.append(_encode_float(packet.time))
    return b"".join(parts)


def _digest(payload: bytes) -> bytes:
    return hashlib.blake2b(payload, digest_size=DIGEST_BYTES).digest()


def _as_uuid_version_eight(digest: bytes) -> str:
    """Set the RFC 9562 version and variant bits by hand.

    `uuid.UUID(bytes=..., version=8)` is not usable: CPython accepts only
    versions 1 through 5 until 3.14, and this package's floor is 3.12. 122 of
    the 128 hash bits survive.
    """
    octets = bytearray(digest)
    octets[6] = (octets[6] & 0x0F) | 0x80
    octets[8] = (octets[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(octets)))


@dataclass(frozen=True, slots=True)
class Identity:
    """The two derived values. See the module docstring for which to use where."""

    video_uuid: str
    content_digest: str


def mint_identity(
    header: Header, packets: tuple[Packet, ...], *, timing_measured: bool
) -> Identity:
    """Mint both values from one scan's header and packets.

    Takes `Header` rather than `MediaFacts` because every fact hashed into
    `content_digest` is already on the header, and the caller holds the header
    and the packets together before it assembles the facts.
    """
    content_bytes = _digest(content_digest_input(header, packets))
    video_bytes = _digest(video_uuid_input(content_bytes, timing_measured, packets))
    return Identity(
        video_uuid=_as_uuid_version_eight(video_bytes),
        content_digest=content_bytes.hex(),
    )


def timing_tolerance(value: float, duration: float) -> float:
    """The absolute tolerance for `value` on a file of `duration` seconds.

    Absolute, but derived per file rather than fixed. The drift a container
    rewrite introduces is quantization noise averaged over the file, so it
    scales as one quantum per file second: a single constant for the whole
    corpus is either too tight for long files or too loose for short ones.

    Comparing `duration` against itself collapses this to a flat
    `DRIFT_SAFETY * TIMESTAMP_QUANTUM_SECONDS`, which is the intended behavior.
    """
    return DRIFT_SAFETY * value * TIMESTAMP_QUANTUM_SECONDS / duration


@dataclass(frozen=True, slots=True)
class DuplicateComparison:
    """Why two probed files are, or are not, the same video.

    Deltas are absolute differences and tolerances are the values actually
    applied, derived or overridden. All four are None for `"unminted"`,
    `"distinct"`, and `"timing_unknown"`, where no comparison ran. The applied
    tolerance travels with the delta so a caller can explain a verdict without
    recomputing it.
    """

    verdict: DuplicateVerdict
    fps_delta: float | None
    duration_delta: float | None
    fps_tolerance: float | None
    duration_tolerance: float | None


def compare_for_duplicate(
    left: MediaFacts,
    right: MediaFacts,
    *,
    fps_tolerance: float | None = None,
    duration_tolerance: float | None = None,
) -> DuplicateComparison:
    """Decide whether two probed files are the same video.

    This is the only supported way to ask. Never compare `video_uuid` for it:
    that value moves on a container rewrite, so it answers "the same bytes at
    the same timing", not "the same video".

    Takes `MediaFacts` rather than a digest and loose floats because the two
    must come from the same probe; a signature accepting them separately would
    allow pairing one file's digest with another's timing.

    `left` is the reference -- both tolerances are derived from it -- so the
    result is not symmetric when the two durations differ. A caller sweeping a
    group holds `left` fixed.

    Each tolerance is an absolute value in its own unit, frames per second and
    seconds. None derives it from `timing_tolerance`, which is correct for any
    caller without a specific reason to override.

    `start_time` is deliberately not compared: it is a container property, not a
    content one. A rewrite into MPEG-TS moves it by over a second on content
    that did not change, because that container starts its clock at the first
    program clock reference.
    """
    if not (left.content_digest and right.content_digest):
        # Not defensive: without this the function reports false duplicates.
        # An absent digest is the empty string, so two facts that both lack one
        # compare EQUAL below, clear the timing guard whenever both were built
        # with timing_measured=True, and reach the tolerance comparison -- where
        # two unrelated recordings at the same rate and length come back
        # "duplicate". Facts persisted before these fields existed have that
        # shape, and so do facts hand-built for a source the probe never saw.
        #
        # "unminted" rather than "distinct": the two may well be the same, and
        # the caller is told which input to fix rather than given an answer the
        # function cannot support.
        return DuplicateComparison(
            verdict="unminted",
            fps_delta=None,
            duration_delta=None,
            fps_tolerance=None,
            duration_tolerance=None,
        )
    if left.content_digest != right.content_digest:
        return DuplicateComparison(
            verdict="distinct",
            fps_delta=None,
            duration_delta=None,
            fps_tolerance=None,
            duration_tolerance=None,
        )
    if not (left.timing_measured and right.timing_measured) or left.duration <= 0.0:
        # A guard, not a comparison. Both floats are 0.0 placeholders on an
        # untimed stream, so the tolerance test is meaningless and the division
        # by duration below is undefined.
        #
        # The duration clause guards that division directly rather than trusting
        # the flag to imply it. The two come apart on facts a consumer builds
        # rather than probes: `timing_measured` defaults to True, so a row
        # reconstructed from persisted columns that predate the field reports
        # True whatever was actually probed. This function is exported as the
        # only supported way to ask, so it stays total over every `MediaFacts` a
        # caller can construct.
        return DuplicateComparison(
            verdict="timing_unknown",
            fps_delta=None,
            duration_delta=None,
            fps_tolerance=None,
            duration_tolerance=None,
        )
    applied_fps_tolerance = (
        timing_tolerance(left.fps, left.duration)
        if fps_tolerance is None
        else fps_tolerance
    )
    applied_duration_tolerance = (
        timing_tolerance(left.duration, left.duration)
        if duration_tolerance is None
        else duration_tolerance
    )
    fps_delta = abs(left.fps - right.fps)
    duration_delta = abs(left.duration - right.duration)
    within = (
        fps_delta <= applied_fps_tolerance
        and duration_delta <= applied_duration_tolerance
    )
    return DuplicateComparison(
        verdict="duplicate" if within else "different_timing",
        fps_delta=fps_delta,
        duration_delta=duration_delta,
        fps_tolerance=applied_fps_tolerance,
        duration_tolerance=applied_duration_tolerance,
    )
