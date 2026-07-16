"""Unit tests for the cached ffmpeg capability probes.

shutil and subprocess are stubbed so these run on a machine without ffmpeg and
pin the caching and output-parsing behavior directly.
"""

import shutil
import subprocess

import pytest

from mosaic_media import hwaccel


@pytest.fixture(autouse=True)
def reset_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the module-level capability caches before each case.

    setattr through monkeypatch both resets each cache and restores it
    afterwards, so no test leaks a cached probe result into the next.
    """
    monkeypatch.setattr(hwaccel, "_ffmpeg_ok", None)
    monkeypatch.setattr(hwaccel, "_nvdec_ok", None)
    monkeypatch.setattr(hwaccel, "_encoder_ok", {})


class FakeRun:
    """A subprocess.run stand-in that returns fixed stdout and records calls."""

    def __init__(self, stdout: str) -> None:
        self.stdout: str = stdout
        self.commands: list[list[str]] = []

    def __call__(
        self,
        command: list[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=self.stdout, stderr="")


class RaisingRun:
    """A subprocess.run stand-in that always times out."""

    def __call__(
        self,
        command: list[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(
            command, timeout if timeout is not None else 5.0
        )


def present(_name: str) -> str | None:
    return "/usr/bin/ffmpeg"


def absent(_name: str) -> str | None:
    return None


def test_ffmpeg_available_is_true_when_on_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", present)
    assert hwaccel.ffmpeg_available() is True


def test_ffmpeg_available_is_false_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", absent)
    assert hwaccel.ffmpeg_available() is False


def test_ffmpeg_availability_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def counting_which(_name: str) -> str | None:
        nonlocal calls
        calls += 1
        return "/usr/bin/ffmpeg"

    monkeypatch.setattr(shutil, "which", counting_which)
    assert hwaccel.ffmpeg_available() is True
    assert hwaccel.ffmpeg_available() is True
    assert calls == 1


def test_nvdec_available_reads_the_hwaccels_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", present)
    monkeypatch.setattr(
        subprocess, "run", FakeRun("Hardware acceleration methods:\ncuda\nvaapi\n")
    )
    assert hwaccel.nvdec_available() is True


def test_nvdec_available_is_false_without_cuda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", present)
    monkeypatch.setattr(
        subprocess, "run", FakeRun("Hardware acceleration methods:\nvaapi\n")
    )
    assert hwaccel.nvdec_available() is False


def test_nvdec_skips_the_subprocess_without_ffmpeg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRun("cuda")
    monkeypatch.setattr(shutil, "which", absent)
    monkeypatch.setattr(subprocess, "run", fake)
    assert hwaccel.nvdec_available() is False
    assert fake.commands == []


def test_nvdec_probe_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun("cuda\n")
    monkeypatch.setattr(shutil, "which", present)
    monkeypatch.setattr(subprocess, "run", fake)
    assert hwaccel.nvdec_available() is True
    assert hwaccel.nvdec_available() is True
    assert fake.commands == [["ffmpeg", "-hwaccels"]]


def test_encoder_available_finds_named_encoders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", present)
    monkeypatch.setattr(
        subprocess, "run", FakeRun(" V..... av1_nvenc x\n V..... libsvtav1 y\n")
    )
    assert hwaccel.encoder_available("av1_nvenc") is True
    assert hwaccel.encoder_available("libsvtav1") is True


def test_encoder_available_is_false_for_a_missing_encoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", present)
    monkeypatch.setattr(subprocess, "run", FakeRun(" V..... libx264 H.264\n"))
    assert hwaccel.encoder_available("av1_nvenc") is False


def test_encoder_probe_is_cached_per_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRun(" V..... av1_nvenc x\n V..... h264_nvenc y\n")
    monkeypatch.setattr(shutil, "which", present)
    monkeypatch.setattr(subprocess, "run", fake)
    assert hwaccel.encoder_available("av1_nvenc") is True
    assert hwaccel.encoder_available("av1_nvenc") is True
    assert fake.commands == [["ffmpeg", "-encoders"]]
    assert hwaccel.encoder_available("h264_nvenc") is True
    assert fake.commands == [["ffmpeg", "-encoders"], ["ffmpeg", "-encoders"]]


def test_encoder_name_in_description_text_does_not_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", present)
    monkeypatch.setattr(
        subprocess, "run", FakeRun(" V....D libaom-av1 libaom AV1 (codec av1)\n")
    )
    assert hwaccel.encoder_available("av1") is False
    assert hwaccel.encoder_available("libaom-av1") is True


def test_encoder_available_is_false_without_ffmpeg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRun("av1_nvenc")
    monkeypatch.setattr(shutil, "which", absent)
    monkeypatch.setattr(subprocess, "run", fake)
    assert hwaccel.encoder_available("av1_nvenc") is False
    assert fake.commands == []


def test_a_subprocess_failure_is_reported_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", present)
    monkeypatch.setattr(subprocess, "run", RaisingRun())
    assert hwaccel.nvdec_available() is False
    assert hwaccel.encoder_available("av1_nvenc") is False
