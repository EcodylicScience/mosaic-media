import subprocess
import sys
from pathlib import Path

import pytest

from mosaic_media.probe import ffprobe, identity
from mosaic_media.probe.errors import MediaProbeError
from mosaic_media.probe.ffprobe import read_header, scan_packets
from mosaic_media.probe.identity import mint_identity
from mosaic_media.probe.probe import probe_media
from tests.helpers.media_fixtures import build


def test_probe_media_fills_both_identity_fields(clips: dict[str, Path]) -> None:
    facts = probe_media(clips["cfr_mp4"])
    assert len(facts.content_digest) == 32
    assert len(facts.video_uuid) == 36


def test_a_probed_file_records_the_scheme_and_the_prober(
    clips: dict[str, Path],
) -> None:
    facts = probe_media(clips["cfr_mp4"])
    assert facts.identity_scheme == identity.IDENTITY_SCHEME
    assert facts.prober_version == ffprobe.prober_version()


def test_probe_media_mints_identity_for_an_untimed_stream(
    clips: dict[str, Path],
) -> None:
    # A raw elementary stream carries no timestamps. It still gets both values;
    # timing_measured records that the uuid's timing half is a placeholder. The
    # re-mint below checks that the probe actually passed its own computed
    # timing_measured value through to mint_identity, rather than a hardcoded
    # one that happens to also be False.
    facts = probe_media(clips["raw_h264"])
    assert facts.timing_measured is False
    assert len(facts.content_digest) == 32
    assert len(facts.video_uuid) == 36
    header = read_header(clips["raw_h264"])
    packets, _source = scan_packets(clips["raw_h264"], header.video_position)
    assert (
        facts.video_uuid
        == mint_identity(header, packets, timing_measured=False).video_uuid
    )


def test_faststart_and_plain_fixtures_agree_on_both_values(
    clips: dict[str, Path],
) -> None:
    # cfr_mp4 and faststart_mp4 are built from one filter source with the same
    # encoder settings and differ only in -movflags +faststart. They are the
    # same video, so both values must match.
    plain = probe_media(clips["cfr_mp4"])
    faststart = probe_media(clips["faststart_mp4"])
    assert plain.content_digest == faststart.content_digest
    assert plain.video_uuid == faststart.video_uuid


def test_identity_is_stable_across_processes(clips: dict[str, Path]) -> None:
    # Determinism within one interpreter would still pass if the digest depended
    # on hash randomization, dict ordering, or any other per-process state. A
    # fresh interpreter is what proves the value travels.
    program = (
        "from pathlib import Path\n"
        + "from mosaic_media.probe.probe import probe_media\n"
        # probe_media requires a Path: read_header calls path.absolute(), so a
        # bare string raises AttributeError.
        + "facts = probe_media(Path("
        + repr(str(clips["cfr_mp4"]))
        + "))\n"
        + "print(facts.video_uuid)\n"
        + "print(facts.content_digest)\n"
    )
    first = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=120
    )
    assert first.returncode == 0, first.stderr
    second = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=120
    )
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout
    facts = probe_media(clips["cfr_mp4"])
    assert first.stdout.split() == [facts.video_uuid, facts.content_digest]


def test_a_two_packet_file_mints_both_values(tmp_path: Path) -> None:
    # The shortest probeable timed file. A single-packet timed file is not
    # probeable at all -- measure_timing needs two distinct timestamps -- so two
    # packets is the real floor, and the collision budget is stated against it.
    target = build(
        tmp_path / "two_frames.mp4",
        "-frames:v",
        "2",
        "-c:v",
        "mpeg4",
        "-pix_fmt",
        "yuv420p",
        source=["-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=1"],
    )
    facts = probe_media(target)
    assert facts.timing_measured is True
    assert len(facts.content_digest) == 32
    assert len(facts.video_uuid) == 36


def test_a_single_packet_timed_file_is_still_unprobeable(tmp_path: Path) -> None:
    # Unchanged behavior, pinned here because the collision budget's floor
    # argument depends on it: a one-packet timed file cannot reach the identity
    # code, so the weakest reachable timed case is two packets.
    target = build(
        tmp_path / "one_frame.mp4",
        "-frames:v",
        "1",
        "-c:v",
        "mpeg4",
        "-pix_fmt",
        "yuv420p",
        source=["-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=1"],
    )
    with pytest.raises(MediaProbeError, match="too few distinct packet timestamps"):
        _ = probe_media(target)


def test_genuinely_distinct_fixtures_have_distinct_uuids(
    clips: dict[str, Path],
) -> None:
    # Excludes faststart_mp4, which is the same video as cfr_mp4 by
    # construction and is covered by the equality test above, and no_video,
    # which has no video stream to probe.
    names = (
        "cfr_mp4",
        "cfr_30fps_mp4",
        "vp8_webm",
        "mjpeg_avi",
        "anamorphic_mp4",
        "audio_mp4",
        "raw_h264",
    )
    minted = [probe_media(clips[name]).video_uuid for name in names]
    assert len(set(minted)) == len(names)
