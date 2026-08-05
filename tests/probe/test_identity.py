import hashlib
import uuid
from pathlib import Path

from mosaic_media.probe.ffprobe import (
    Header,
    Packet,
    read_header,
    scan_packets,
    timing_source_for,
    timing_supplied_by_source,
)
from mosaic_media.probe.identity import (
    CONTENT_FORMAT_TAG,
    IDENTITY_SCHEME,
    VIDEO_FORMAT_TAG,
    Identity,
    content_digest_input,
    mint_identity,
    video_uuid_input,
)


# Every hashed field carries a value distinct from its neighbors, so
# transposing any two adjacent fields changes the serialized bytes. Equal
# values on an adjacent pair would leave the golden vector blind to exactly
# the field-order error it exists to catch.
GOLDEN_HEADER = Header(
    container="mov,mp4,m4a,3gp,3g2,mj2",
    codec_name="h264",
    pixel_format="yuv420p",
    color_range="tv",
    color_primaries="bt709",
    color_transfer="smpte170m",
    width=320,
    height=240,
    rotation_degrees=0,
    square_pixels=False,
    progressive=True,
    has_audio=False,
    video_stream_count=1,
    video_position=0,
    start_time=0.0,
    declared_duration=2.0,
    declared_fps=25.0,
    elementary_stream_fps=0.0,
    declared_frame_count=50,
    # Not hashed: content_digest_input reads an explicit list of header fields
    # and this is not on it, and video_uuid_input never sees a header at all.
    # Nonzero for the same reason every other value here is distinct, so a
    # transposition shows up in the vectors below.
    coded_reordering_depth=2,
)

GOLDEN_PACKETS = (
    Packet(time=0.0, size=3837, keyframe=True, pos=48, data_hash="CRC32:759f21ea"),
    Packet(time=0.04, size=120, keyframe=False, pos=3885, data_hash="CRC32:1a2b3c4d"),
)

GOLDEN_LENGTH = 161
GOLDEN_SHA256 = "54809a4354cd40fc15c3cfa3832062f9622b2849cd6cde0ea3d780efea5db799"

GOLDEN_VIDEO_CONTENT_BYTES = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
GOLDEN_VIDEO_LENGTH = 69
GOLDEN_VIDEO_SHA256 = "3f090225a940c8d4e9c00b34aaa7c73201edabbdf21f46e929c6d8446ed850e8"


def test_both_format_tags_are_built_from_one_scheme_version() -> None:
    assert CONTENT_FORMAT_TAG.endswith(IDENTITY_SCHEME.encode())
    assert VIDEO_FORMAT_TAG.endswith(IDENTITY_SCHEME.encode())
    assert CONTENT_FORMAT_TAG == b"mosaic-media/content/2"
    assert VIDEO_FORMAT_TAG == b"mosaic-media/video/2"


def identity_of(path: Path) -> Identity:
    header = read_header(path)
    packets, source = scan_packets(path, header.video_position)
    # Derived the same way the probe derives it, so this helper cannot disagree
    # with what a probed file was actually minted with.
    supplied = timing_supplied_by_source(timing_source_for(source, header.container))
    return mint_identity(header, packets, timing_supplied_by_source=supplied)


def test_mint_identity_is_deterministic(clips: dict[str, Path]) -> None:
    first = identity_of(clips["cfr_mp4"])
    second = identity_of(clips["cfr_mp4"])
    assert first == second


def test_video_uuid_is_a_uuid_version_eight_string(clips: dict[str, Path]) -> None:
    minted = identity_of(clips["cfr_mp4"])
    parsed = uuid.UUID(minted.video_uuid)
    assert parsed.version == 8
    assert parsed.variant == uuid.RFC_4122


def test_content_digest_is_thirty_two_hex_characters(clips: dict[str, Path]) -> None:
    minted = identity_of(clips["cfr_mp4"])
    assert len(minted.content_digest) == 32
    assert int(minted.content_digest, 16) >= 0


def test_content_digest_survives_stream_preserving_rewrites(
    rewrites: dict[str, Path],
) -> None:
    reference = identity_of(rewrites["origin"]).content_digest
    for name in ("faststart", "repack", "tagged", "matroska", "roundtrip"):
        assert identity_of(rewrites[name]).content_digest == reference, name


def test_content_digest_changes_on_bitstream_reframing(
    rewrites: dict[str, Path],
) -> None:
    # mp4 to MPEG-TS applies Annex B conversion, which changes the coded bytes.
    # No digest over those bytes can be invariant to it; this asserts the limit
    # rather than leaving it undefined.
    reference = identity_of(rewrites["origin"]).content_digest
    assert identity_of(rewrites["transport"]).content_digest != reference


def test_video_uuid_survives_same_container_rewrites(
    rewrites: dict[str, Path],
) -> None:
    reference = identity_of(rewrites["origin"]).video_uuid
    for name in ("faststart", "repack", "tagged"):
        assert identity_of(rewrites[name]).video_uuid == reference, name


def test_video_uuid_changes_on_container_change_and_is_not_restored(
    rewrites: dict[str, Path],
) -> None:
    reference = identity_of(rewrites["origin"]).video_uuid
    matroska = identity_of(rewrites["matroska"]).video_uuid
    roundtrip = identity_of(rewrites["roundtrip"]).video_uuid
    assert matroska != reference
    # Repacking back to mp4 inherits the millisecond quantization rather than
    # undoing it, so the original uuid is gone for good.
    assert roundtrip != reference


def test_retime_keeps_the_content_digest_and_moves_the_uuid(
    rewrites: dict[str, Path],
) -> None:
    reference = identity_of(rewrites["origin"])
    retimed = identity_of(rewrites["retimed"])
    assert retimed.content_digest == reference.content_digest
    assert retimed.video_uuid != reference.video_uuid


def test_truncation_changes_the_content_digest(
    clips: dict[str, Path], truncated_faststart: Path
) -> None:
    assert (
        identity_of(truncated_faststart).content_digest
        != identity_of(clips["faststart_mp4"]).content_digest
    )


def test_flipping_a_payload_byte_changes_the_content_digest(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    # faststart_mp4 specifically: it is the only mp4 fixture whose moov is at
    # the front, so it has a header region big enough to distinguish from
    # payload. In a non-faststart mp4 the moov is at the end and mdat starts at
    # byte 44, which leaves no header bytes to target and makes the companion
    # test below impossible.
    source = clips["faststart_mp4"]
    payload = source.read_bytes()
    mdat = payload.find(b"mdat")
    assert mdat > 200, "fixture is not faststart: no header region to target"
    mutated = bytearray(payload)
    mutated[mdat + 64] ^= 0xFF
    corrupted = tmp_path / "corrupted.mp4"
    _ = corrupted.write_bytes(bytes(mutated))
    assert identity_of(corrupted).content_digest != identity_of(source).content_digest


def test_a_header_region_byte_does_not_change_the_content_digest(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    # The companion to the test above, and the reason it targets mdat: the
    # digest covers coded payload, not container bytes. Without this pair, a
    # test that flipped an arbitrary byte would pass against an implementation
    # that hashed the whole file.
    source = clips["faststart_mp4"]
    payload = source.read_bytes()
    mdat = payload.find(b"mdat")
    assert mdat > 200, "fixture is not faststart: no header region to target"
    mutated = bytearray(payload)
    # Byte 200 is inside moov metadata, well before the media data box.
    mutated[200] ^= 0xFF
    rewritten = tmp_path / "header_touched.mp4"
    _ = rewritten.write_bytes(bytes(mutated))
    assert identity_of(rewritten).content_digest == identity_of(source).content_digest


def test_content_digest_input_matches_the_golden_vector() -> None:
    # Pins the serialized bytes, not just the digest: an encoding or field-order
    # change that a same-machine round trip would hide is exactly the change
    # that invalidates every already-minted value in every consumer.
    #
    # Built from constructed values rather than an encoded fixture, so the
    # vector depends only on this module's serialization. A fixture-derived
    # vector would additionally depend on the local libx264 build.
    payload = content_digest_input(GOLDEN_HEADER, GOLDEN_PACKETS)
    assert len(payload) == GOLDEN_LENGTH
    assert hashlib.sha256(payload).hexdigest() == GOLDEN_SHA256


def test_video_uuid_input_matches_the_golden_vector() -> None:
    # video_uuid is the value that names directories, so its serialization is
    # pinned at least as tightly as the digest it derives from. Without this,
    # dropping the content digest from the input merges two distinct videos
    # onto one uuid with every other test still passing.
    payload = video_uuid_input(GOLDEN_VIDEO_CONTENT_BYTES, True, GOLDEN_PACKETS)
    assert len(payload) == GOLDEN_VIDEO_LENGTH
    assert hashlib.sha256(payload).hexdigest() == GOLDEN_VIDEO_SHA256


def test_the_digest_input_length_tracks_the_packet_count(
    clips: dict[str, Path],
) -> None:
    # 27 bytes per packet: 8 for size, 1 for the keyframe flag, and 4 + 14 for
    # the length-prefixed CRC32 string. The fixed part varies with the header's
    # string lengths, so it is measured rather than hardcoded.
    header = read_header(clips["cfr_mp4"])
    packets, _source = scan_packets(clips["cfr_mp4"], header.video_position)
    fixed = len(content_digest_input(header, ()))
    payload = content_digest_input(header, packets)
    assert len(payload) == fixed + 27 * len(packets)
