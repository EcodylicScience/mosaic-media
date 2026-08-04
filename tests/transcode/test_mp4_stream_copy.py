"""Which codecs the mp4 muxer carries through a stream copy, measured.

The converter always writes mp4, so a copy remux preserving a codec the muxer
refuses dies before a header is written. MP4_STREAM_COPY_CODECS is the allowlist
that prevents that, and a wrong member is a transcode that fails. Every codec the
suite can sample is muxed here against the real muxer, and the set is compared
against the outcome rather than restated beside it.
"""

import subprocess
from pathlib import Path

import pytest

from mosaic_media.transcode.commands import MP4_STREAM_COPY_CODECS
from tests.helpers.media_fixtures import build

# The codec name as ffprobe reports it, the encoder arguments that produce a
# sample of it, and the container that sample needs. Every encoder named is
# native or otherwise non-GPL, which is what lets the suite generate these at
# all. Codecs a fixture already provides are absent from this table and taken
# from that fixture below; a second encode of the same clip is duplication that
# drifts the moment the fixture's own parameters change.
#
# The refusals are corpus codecs that actually reach this selector, not arbitrary
# negatives: every codec the hardening corpus carries outside the trusted set is
# measured here, and msmpeg4v2, wmv2 and rpza are the ones the muxer refuses --
# mpeg4 is in the same position and carries. indeo5 is decode-only in this
# FFmpeg, so no sample of it can be produced and it is not measured here.
_GENERATED_SAMPLES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("mpeg4", ("-c:v", "mpeg4", "-pix_fmt", "yuv420p"), "avi"),
    ("mpeg2video", ("-c:v", "mpeg2video", "-pix_fmt", "yuv420p"), "mpg"),
    ("mpeg1video", ("-c:v", "mpeg1video", "-pix_fmt", "yuv420p"), "mpg"),
    ("msmpeg4v2", ("-c:v", "msmpeg4v2", "-pix_fmt", "yuv420p"), "avi"),
    ("wmv2", ("-c:v", "wmv2", "-pix_fmt", "yuv420p"), "asf"),
    ("rpza", ("-c:v", "rpza", "-pix_fmt", "yuv420p"), "mov"),
)


def _mp4_carries(sample: Path, destination: Path) -> bool:
    """Whether `-c copy` muxes `sample` into mp4 without the muxer refusing it."""
    command = [
        "ffmpeg",
        "-hide_banner",
        "-v",
        "error",
        "-y",
        "-i",
        str(sample),
        "-c",
        "copy",
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    return result.returncode == 0


@pytest.fixture(scope="session")
def mp4_carriage(
    tmp_path_factory: pytest.TempPathFactory,
    clips: dict[str, Path],
    hevc_clip: Path,
    vp9_webm_clip: Path,
    corpus_gop12: Path,
) -> dict[str, bool]:
    """Every codec the suite can sample, muxed into mp4 with `-c copy`.

    Six samples come from fixtures the session already builds, and the rest are
    encoded here because nothing else in the suite needs them.
    """
    root = tmp_path_factory.mktemp("mp4_carriage")
    samples: dict[str, Path] = {
        "h264": clips["cfr_mp4"],
        "hevc": hevc_clip,
        "av1": corpus_gop12,
        "vp9": vp9_webm_clip,
        "vp8": clips["vp8_webm"],
        "mjpeg": clips["mjpeg_avi"],
    }
    for codec_name, arguments, extension in _GENERATED_SAMPLES:
        samples[codec_name] = build(root / f"{codec_name}.{extension}", *arguments)
    return {
        codec_name: _mp4_carries(sample, root / f"{codec_name}_copy.mp4")
        for codec_name, sample in samples.items()
    }


@pytest.mark.parametrize("codec_name", sorted(MP4_STREAM_COPY_CODECS))
def test_every_member_of_the_copy_set_is_carried_by_the_mp4_muxer(
    codec_name: str, mp4_carriage: dict[str, bool]
) -> None:
    # A member the muxer refuses is the expensive error of the two: the copy
    # remux the selector picks for it dies before a header is written, and the
    # source can never be prepared for its target.
    assert codec_name in mp4_carriage, f"no sample measures {codec_name}"
    assert mp4_carriage[codec_name]


def test_the_copy_set_is_exactly_what_the_muxer_carries(
    mp4_carriage: dict[str, bool],
) -> None:
    # Derived, not restated: a codec measured as refused cannot sit in the set,
    # and one measured as carried cannot be quietly left out of it. This is what
    # makes the comment on MP4_STREAM_COPY_CODECS true rather than aspirational.
    carried = frozenset(codec_name for codec_name, ok in mp4_carriage.items() if ok)
    assert MP4_STREAM_COPY_CODECS == carried
