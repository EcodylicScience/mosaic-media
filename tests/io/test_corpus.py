from pathlib import Path

from mosaic_media.probe.probe import probe_media
from tests.helpers.corpus import decode_md5s, generate_video


def test_generate_video_produces_exact_frame_count(tmp_path: Path) -> None:
    path = generate_video(tmp_path / "clip.mp4", frames=40, fps=30.0, gop=12)
    facts = probe_media(path)
    assert facts.frame_count == 40
    assert (facts.width, facts.height) == (320, 240)
    assert facts.max_keyframe_interval_frames <= 12


def test_generate_video_embeds_rotation_as_side_data(tmp_path: Path) -> None:
    path = generate_video(
        tmp_path / "rot.mp4", frames=24, fps=30.0, gop=12, rotation_degrees=90
    )
    facts = probe_media(path)
    assert facts.rotation_degrees == 90
    # Rotation is metadata only: coded dimensions are unchanged.
    assert (facts.width, facts.height) == (320, 240)


def test_decode_md5s_returns_one_hash_per_frame(tmp_path: Path) -> None:
    path = generate_video(tmp_path / "clip.mp4", frames=40, fps=30.0, gop=12)
    goldens = decode_md5s(path)
    assert len(goldens) == 40
    assert all(len(digest) == 32 for digest in goldens)
    gray = decode_md5s(path, grayscale=True)
    assert len(gray) == 40
    assert gray != goldens


def test_corpus_fixtures_available(corpus_gop12: Path, corpus_gop250: Path) -> None:
    assert probe_media(corpus_gop12).max_keyframe_interval_frames <= 12
    assert probe_media(corpus_gop250).frame_count >= 250
