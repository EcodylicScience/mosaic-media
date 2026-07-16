import gc
import sys
from pathlib import Path

import pytest

from mosaic_media.io.reader import VideoReader
from mosaic_media.probe.errors import MediaProbeError
from mosaic_media.probe.probe import probe_media
from tests.helpers.corpus import generate_video


def test_truncated_file_raises_instead_of_silently_ending(tmp_path: Path) -> None:
    # Probe the whole clip, then read a byte-truncated copy with those full-file
    # facts injected. ffmpeg aborts on the incomplete file; without a decoder
    # exit check that is indistinguishable from a short video and the read ends
    # silently, so the reader must surface it as an error instead.
    clip = generate_video(tmp_path / "full.mp4", frames=48, fps=30.0, gop=12)
    facts = probe_media(clip)
    payload = clip.read_bytes()
    truncated = tmp_path / "truncated.mp4"
    _ = truncated.write_bytes(payload[: len(payload) * 60 // 100])
    with VideoReader(truncated, facts=facts) as reader:
        with pytest.raises(MediaProbeError):
            _ = [frame for _index, frame in reader]


def test_full_read_of_untouched_clip_terminates_cleanly(tmp_path: Path) -> None:
    clip = generate_video(tmp_path / "clean.mp4", frames=24, fps=30.0, gop=12)
    facts = probe_media(clip)
    with VideoReader(clip, facts=facts) as reader:
        count = sum(1 for _pair in reader)
    assert count == facts.frame_count


def test_seek_after_close_raises(corpus_gop12: Path) -> None:
    # A seek on a closed reader must raise before spawning anything, so a
    # discarded reader cannot leave an orphaned ffmpeg child behind. The
    # raise-before-spawn ordering makes the no-orphan property structural.
    reader = VideoReader(corpus_gop12)
    reader.close()
    with pytest.raises(MediaProbeError):
        reader.seek(5)


def test_read_frames_after_close_raises(corpus_gop12: Path) -> None:
    reader = VideoReader(corpus_gop12)
    reader.close()
    with pytest.raises(MediaProbeError):
        _ = list(reader.read_frames([5, 6]))


def test_del_after_failed_init_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # When __init__ raises before finishing, finalization must not add
    # AttributeError noise from close() touching an unset attribute. Capture any
    # exception ffmpeg-less construction leaks through the garbage collector.
    monkeypatch.setattr("mosaic_media.io.reader.ffmpeg_available", lambda: False)
    unraisable: list[object] = []
    monkeypatch.setattr(
        sys, "unraisablehook", lambda hook_args: unraisable.append(hook_args)
    )
    with pytest.raises(MediaProbeError):
        _ = VideoReader("nonexistent.mp4")
    _ = gc.collect()
    assert not any(
        isinstance(getattr(entry, "exc_value", None), AttributeError)
        for entry in unraisable
    )
