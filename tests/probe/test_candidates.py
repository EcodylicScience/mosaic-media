from pathlib import Path

from mosaic_media import VIDEO_EXTENSIONS, is_candidate_video


def test_uppercase_extension_is_a_candidate() -> None:
    assert is_candidate_video(Path("DMS10248.MP4"))


def test_double_extension_uses_the_last_suffix() -> None:
    assert not is_candidate_video(Path("hex_3.mp4.bk"))


def test_non_video_extension_is_not_a_candidate() -> None:
    assert not is_candidate_video(Path("index.csv"))


def test_every_suffix_the_raw_demuxers_register_is_a_candidate() -> None:
    # ffmpeg registers h26l,h264,264,avc for H.264 and hevc,h265,265 for HEVC.
    # The whole of both lists is accepted, bare numbers included, because every
    # one of them denotes a raw video elementary stream -- neither demuxer
    # registers an audio-only, subtitle or still-image spelling that would have
    # to be filtered out first.
    registered = ("h26l", "h264", "264", "avc", "hevc", "h265", "265")
    for suffix in registered:
        assert is_candidate_video(Path(f"recording.{suffix}")), suffix


def test_raw_elementary_stream_suffixes_match_case_insensitively() -> None:
    # The same case-folding the container suffixes get, which a tool writing an
    # uppercase name depends on.
    assert is_candidate_video(Path("recording.HEVC"))
    assert is_candidate_video(Path("recording.H265"))
    assert is_candidate_video(Path("recording.AVC"))


def test_ogv_is_a_candidate() -> None:
    assert is_candidate_video(Path("clip.ogv"))


def test_the_other_ogg_spellings_are_not_candidates() -> None:
    for suffix in (".ogg", ".oga", ".spx", ".opus"):
        assert not is_candidate_video(Path(f"clip{suffix}")), suffix


def test_every_exported_extension_is_reachable_through_the_predicate() -> None:
    # The set is exported and the predicate is the only way to consult it, so
    # the two must agree on every member. They can disagree silently: the
    # predicate lowercases the suffix it looks up but not the set, and
    # `Path.suffix` always carries its leading dot, so a member spelled without
    # a dot or with any uppercase would sit in the exported set and never match
    # a real path. Derived from the set rather than from a list written out
    # here, so a member added in either wrong form fails here.
    assert VIDEO_EXTENSIONS
    for extension in VIDEO_EXTENSIONS:
        assert is_candidate_video(Path(f"clip{extension}")), extension
