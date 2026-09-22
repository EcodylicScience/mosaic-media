"""What the three codec sets each mean, and why they are not one set.

``policy.py`` carries three frozensets of codec names that look
interchangeable and answer different questions. Two of them contain ``"av1"``
and are right to; the third must not, and a future edit that "tidies" them
together would silently hand AV1 back to readers that cannot open it.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from mosaic_media import (
    CHROME_149,
    FRAME_EXACT_CODECS,
    SOFTWARE_DECODABLE_CODECS,
)


def test_av1_is_frame_exact_and_playable_but_not_software_decodable() -> None:
    """The one codec where the three answers come apart.

    ``libavcodec/av1dec.c`` is a hardware-accelerator wrapper with no software
    path. AV1's software decoders are the external ``libdav1d`` and
    ``libaom-av1``, which a build may omit -- and the manylinux
    ``opencv-python`` wheel does omit them, compiling in no hwaccel either, so
    it cannot decode AV1 on any Linux machine. Its macOS wheel bundles libdav1d
    and can. Nothing about the stream differs; only the reader does.
    """
    assert "av1" in FRAME_EXACT_CODECS, "its decoder emits one frame per packet"
    assert "av1" in CHROME_149.codecs, "and a browser plays it"
    assert "av1" not in SOFTWARE_DECODABLE_CODECS, (
        "but a reader we did not build may hold no decoder for it at all"
    )


def test_the_sets_are_not_collapsed_into_one_another() -> None:
    """Byte-identical literals would let a reader pick the wrong one by accident."""
    assert SOFTWARE_DECODABLE_CODECS != FRAME_EXACT_CODECS
    assert SOFTWARE_DECODABLE_CODECS != CHROME_149.codecs


@pytest.mark.parametrize("codec", sorted(SOFTWARE_DECODABLE_CODECS))
def test_every_member_has_a_native_decoder(codec: str) -> None:
    """Membership is a claim about FFmpeg's own C, so ask FFmpeg.

    A native decoder is one whose name is the codec's name: ``h264``, not
    ``libdav1d``. A ``lib``-prefixed decoder is an external library a build may
    have been compiled without, which is precisely the property that disqualifies
    AV1.
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not on PATH")
    listed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-decoders"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    names = {line.split()[1] for line in listed.splitlines() if len(line.split()) > 1}
    assert codec in names, f"{codec} has no decoder named after it in this build"
