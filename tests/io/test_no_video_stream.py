"""A container with no video stream is a MediaProbeError, not a stray IndexError.

Every libav boundary in the io layer maps to the probe layer's error type. An
audio-only container opens cleanly, so the failure surfaces only when the code
reaches for its (absent) video stream; both the reader and the packet scanner
must report that as MediaProbeError, matching the probe's "no video stream in
{path}" wording, rather than leaking av's IndexError.
"""

from pathlib import Path

import pytest

from mosaic_media.io.packets import scan_packets_in_process
from mosaic_media.io.reader import VideoReader
from mosaic_media.probe.errors import MediaProbeError


def test_video_reader_on_audio_only_raises_media_probe_error(
    clips: dict[str, Path],
) -> None:
    with VideoReader(clips["no_video"]) as reader:
        with pytest.raises(MediaProbeError):
            _ = reader.read()


def test_scan_packets_on_audio_only_raises_media_probe_error(
    clips: dict[str, Path],
) -> None:
    with pytest.raises(MediaProbeError):
        _ = scan_packets_in_process(clips["no_video"])
