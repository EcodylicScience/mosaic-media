"""Both identity values survive a change of the ffmpeg build underneath them.

`content_digest` folds in each packet's payload hash as libavformat hands it to
ffprobe, not as it sits on disk. An ffmpeg whose demuxer output moved therefore
re-mints every value in every corpus with no line of this package and no byte of
any file different, which is why the build is measured rather than assumed.

Every committed asset is probed under each build named by
MOSAIC_MEDIA_ALT_FFMPEG_PREFIXES and compared against the values the ffmpeg on
PATH mints. The comparison runs in a subprocess: the probe resolves ffprobe from
the environment, so pointing this process at another build would leak into every
other test in the session.
"""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

from mosaic_media.probe.facts import MediaFacts
from mosaic_media.probe.probe import probe_media
from tests.helpers.media_fixtures import ASSETS

PREFIX_VARIABLE = "MOSAIC_MEDIA_ALT_FFMPEG_PREFIXES"

_PREFIX_SHAPE = "a colon-separated list of ffmpeg prefixes (bin/ffprobe, lib/)"
_PURPOSE = "to compare minted identity values across ffmpeg builds"
_ABSENT = "no alternate ffmpeg build configured"
_SKIP_REASON = f"{_ABSENT}: set {PREFIX_VARIABLE} to {_PREFIX_SHAPE} {_PURPOSE}"

_PROVED_NOTHING = "no asset was compared against any build, so this proved nothing"

_FORMAT_BREAK = """an identity value moved with the ffmpeg build, so libavformat's
demuxer output for these containers changed. That is a format break: bump
IDENTITY_SCHEME and re-mint the corpus. It is not an expectation to adjust."""

# One probe per asset per build, over clips of a few dozen frames.
_PROBE_TIMEOUT_SECONDS = 300

IdentityField = Literal["content_digest", "video_uuid"]

# The child prints one value per line and takes the file as an argument, so no
# path is interpolated into the source it runs.
_PROBE_PROGRAM = """
import sys
from pathlib import Path

from mosaic_media.probe.probe import probe_media

facts = probe_media(Path(sys.argv[1]))
print(facts.video_uuid)
print(facts.content_digest)
print(facts.prober_version)
"""


@dataclass(frozen=True, slots=True)
class _Minted:
    """What one build minted for one file, with the build that minted it."""

    video_uuid: str
    content_digest: str
    prober_version: str


def _configured_prefixes() -> tuple[Path, ...]:
    return tuple(
        Path(entry)
        for entry in os.environ.get(PREFIX_VARIABLE, "").split(os.pathsep)
        if entry
    )


# The provenance document, the one committed file that is not an asset.
_NOT_AN_ASSET = "README.md"


def _committed_assets() -> tuple[Path, ...]:
    """Every committed asset, selected by exclusion rather than by suffix.

    Deliberately not filtered through the candidate-extension set. Which
    suffixes this package offers to ingest and which committed files this suite
    probes are different questions, and the two agree today only by coincidence:
    tying the corpus to the first means an asset can leave it silently the next
    time they diverge. A raw elementary stream is exactly what demuxer-output
    drift is most likely to move, so it belongs here whether or not anything
    would ingest a file of its name.

    The exclusion is a denylist of one, which is its own fragility: any future
    non-media file in that directory joins the corpus and fails at its first
    probe. That failure is loud and lands where someone is reading output, which
    is why it is left as a note rather than given machinery.
    """
    return tuple(
        sorted(
            path
            for path in ASSETS.iterdir()
            if path.name != _NOT_AN_ASSET and path.is_file()
        )
    )


def _prepended(entry: Path, existing: str) -> str:
    return f"{entry}{os.pathsep}{existing}" if existing else str(entry)


def _environment(prefix: Path) -> dict[str, str]:
    """The child's environment, with `prefix` ahead of the default build.

    Both variables matter: PATH decides which ffprobe runs, LD_LIBRARY_PATH
    decides which libavformat that ffprobe loads, and the digest is defined
    against the latter.
    """
    environment = dict(os.environ)
    environment["PATH"] = _prepended(prefix / "bin", environment.get("PATH", ""))
    environment["LD_LIBRARY_PATH"] = _prepended(
        prefix / "lib", environment.get("LD_LIBRARY_PATH", "")
    )
    return environment


def _require_build(prefix: Path) -> None:
    """Fail when a configured prefix is not the build the child will resolve.

    A prefix that does not answer is a misconfiguration, not a reason to skip:
    PATH is prepended rather than replaced, so an unresolved prefix would leave
    the child on the default ffmpeg and compare that build against itself.
    """
    resolved = shutil.which("ffprobe", path=_environment(prefix)["PATH"])
    assert resolved is not None, f"{PREFIX_VARIABLE} names {prefix}, with no ffprobe"
    assert Path(resolved).parent == prefix / "bin", (
        f"{prefix}/bin/ffprobe is missing, so the probe would run {resolved}"
    )
    libraries = prefix / "lib"
    assert libraries.is_dir(), (
        f"{prefix} carries no lib/, so libavformat is the default"
    )


def _probe_under(prefix: Path, asset: Path) -> _Minted:
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE_PROGRAM, str(asset)],
        capture_output=True,
        text=True,
        timeout=_PROBE_TIMEOUT_SECONDS,
        env=_environment(prefix),
        check=False,
    )
    detail = completed.stderr.strip()
    assert completed.returncode == 0, f"probing {asset.name} under {prefix}: {detail}"
    printed = completed.stdout.splitlines()
    assert len(printed) == 3, f"probing {asset.name} under {prefix} printed {printed}"
    return _Minted(
        video_uuid=printed[0], content_digest=printed[1], prober_version=printed[2]
    )


def _divergences(
    asset: Path, prefix: Path, expected: MediaFacts, minted: _Minted
) -> list[str]:
    """Every value this build minted differently, named by asset, build, and field."""
    compared: tuple[tuple[IdentityField, str, str], ...] = (
        ("content_digest", expected.content_digest, minted.content_digest),
        ("video_uuid", expected.video_uuid, minted.video_uuid),
    )
    found: list[str] = []
    for field, default_value, observed in compared:
        if default_value == observed:
            continue
        build = f"{prefix} ({minted.prober_version})"
        found.append(
            f"{asset.name} under {build}: {field} {default_value} -> {observed}"
        )
    return found


def test_identity_values_do_not_move_across_ffmpeg_builds() -> None:
    prefixes = _configured_prefixes()
    if not prefixes:
        pytest.skip(_SKIP_REASON)
    assets = _committed_assets()
    assert assets, f"no committed video under {ASSETS}, so there is nothing to compare"
    expected = {asset: probe_media(asset) for asset in assets}
    divergences: list[str] = []
    compared = 0
    for prefix in prefixes:
        _require_build(prefix)
        for asset in assets:
            divergences.extend(
                _divergences(
                    asset, prefix, expected[asset], _probe_under(prefix, asset)
                )
            )
            compared += 1
    wanted = len(prefixes) * len(assets)
    assert compared, _PROVED_NOTHING
    assert compared == wanted, f"compared {compared} of {wanted} asset-build pairs"
    report = "\n  ".join(divergences)
    assert not divergences, f"{_FORMAT_BREAK}\n  {report}"
