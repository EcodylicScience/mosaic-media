"""The `mosaic-media` command line app. The only module that imports typer;
the package facade in `__init__.py` stays standard library so a core-only
install can report the missing extra instead of dying on the import.

Three commands mirror the library: `probe` prints MediaFacts and both verdicts
as JSON; `compare` prints a duplication comparison as JSON and exits with the
verdict as its exit code, so the command doubles as a shell test; `transcode`
runs the minimum operation for a target and honors the opt-out semantics. Job
infrastructure calls the Python API directly, never this CLI -- structured
exceptions, no argv escaping, no output parsing. A host CLI mounts this app
with `app.add_typer(media_app, name="media")`.
"""

import dataclasses
import enum
import json
from pathlib import Path
from typing import Annotated

import typer

from ..probe.errors import MediaProbeError
from ..probe.facts import MediaFacts
from ..probe.identity import DuplicateVerdict, compare_for_duplicate
from ..probe.policy import CHROME_149, DEFAULT_THRESHOLDS, PlaybackProfile
from ..probe.probe import probe_media
from ..probe.verdict import derive
from ..transcode import (
    ANALYSIS_ENCODING,
    PLAYBACK_ENCODING,
    Target as TranscodeTarget,
    TranscodeError,
    run_transcode,
)

app = typer.Typer(
    name="mosaic-media",
    help=(
        "Probe a video, compare two videos for duplication, and run the "
        "minimum ffmpeg transcode a verdict calls for."
    ),
    no_args_is_help=True,
    add_completion=False,
)
# Alias for mounting into a host CLI: app.add_typer(media_app, name="media").
media_app = app


class Target(str, enum.Enum):
    analysis = "analysis"
    playback = "playback"


class Profile(str, enum.Enum):
    chrome_149 = "chrome-149"


_PROFILES: dict[Profile, PlaybackProfile] = {Profile.chrome_149: CHROME_149}

_ALLOW_HARDWARE_HELP = (
    "Permit av1_nvenc hardware encoding. Taken only when this machine can "
    "actually open that encoder: a build listing it on a device that cannot "
    "run it encodes on the CPU instead. Off by default."
)

# The verdict is the exit code, so the command works as a shell test without
# parsing stdout. 1 stays the probe-failure code the other commands use, and 2
# is skipped because click already exits 2 on a usage error such as a mistyped
# option -- a verdict there would be indistinguishable from it.
COMPARE_EXIT_CODES: dict[DuplicateVerdict, int] = {
    "duplicate": 0,
    "distinct": 3,
    "different_timing": 4,
    "timing_unknown": 5,
    # Unreachable here -- this command probes both files, so both always carry a
    # digest. Mapped anyway so the lookup is total over the verdict type and a
    # future caller cannot fall off it.
    "unminted": 6,
}


def _json_default(value: object) -> object:
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if isinstance(value, enum.Enum):
        return value.value
    message = f"cannot serialize {type(value).__name__} to JSON"
    raise TypeError(message)


def _probe_or_exit(path: Path) -> MediaFacts:
    """Probe PATH or exit 1 with a message naming which file failed.

    Shared by every command that probes: on a two-file command such as
    `compare`, a message without the path cannot tell the caller which side
    failed.
    """
    try:
        return probe_media(path)
    except MediaProbeError as exc:
        message = f"probe failed for {path}: {exc}"
        typer.echo(message, err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def probe(file: Path) -> None:
    """Probe FILE and print its MediaFacts and both verdicts as JSON on stdout."""
    facts = _probe_or_exit(file)
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    document = {
        "facts": dataclasses.asdict(facts),
        "verdict": dataclasses.asdict(verdict),
    }
    typer.echo(json.dumps(document, default=_json_default, indent=2, sort_keys=True))


@app.command()
def compare(
    left: Path,
    right: Path,
    fps_tolerance: Annotated[
        float | None,
        typer.Option(
            "--fps-tolerance",
            help=(
                "Absolute frames-per-second tolerance. Omit to derive it from "
                "the reference file's duration, which is correct unless you "
                "have a specific reason to override it."
            ),
        ),
    ] = None,
    duration_tolerance: Annotated[
        float | None,
        typer.Option(
            "--duration-tolerance",
            help="Absolute duration tolerance in seconds. Omit to derive it.",
        ),
    ] = None,
) -> None:
    """Report whether LEFT and RIGHT are the same video, as JSON and an exit code.

    Probes both files. The ingestion pathway compares stored facts instead and
    never re-probes; this command takes paths for ad-hoc use. LEFT is the
    reference: both tolerances are derived from it, so the comparison is not
    symmetric when the two files' durations differ.

    The verdict is also the exit code: duplicate is 0, distinct is 3,
    different_timing is 4, and timing_unknown is 5. 1 is the probe-failure
    code the other commands use, and 2 is skipped because click already exits
    2 on a usage error such as a mistyped option.
    """
    left_facts = _probe_or_exit(left)
    right_facts = _probe_or_exit(right)

    comparison = compare_for_duplicate(
        left_facts,
        right_facts,
        fps_tolerance=fps_tolerance,
        duration_tolerance=duration_tolerance,
    )
    typer.echo(
        json.dumps(
            dataclasses.asdict(comparison),
            default=_json_default,
            indent=2,
            sort_keys=True,
        )
    )
    raise typer.Exit(code=COMPARE_EXIT_CODES[comparison.verdict])


@app.command()
def transcode(
    file: Path,
    target: Annotated[
        Target,
        typer.Option(
            "--target", help="Which derivative to produce: analysis or playback."
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            help=(
                "Output file path, or an existing directory the derivative is "
                "written into under the source stem. Required: the CLI knows no "
                "dataset layout."
            ),
        ),
    ],
    profile: Annotated[
        Profile, typer.Option("--profile", help="Playback policy profile.")
    ] = Profile.chrome_149,
    allow_hardware: Annotated[
        bool,
        typer.Option(
            "--allow-hardware/--no-hardware",
            help=_ALLOW_HARDWARE_HELP,
        ),
    ] = False,
) -> None:
    """Transcode FILE for the analysis or playback target, running the minimum operation."""
    facts = _probe_or_exit(file)

    playback_profile = _PROFILES[profile]
    verdict = derive(facts, playback_profile, DEFAULT_THRESHOLDS)

    optional = False
    if target is Target.analysis:
        if verdict.analysis_transcode is None:
            typer.echo(f"{file} is already analysis-clean; nothing to do.")
            return
        encoding = ANALYSIS_ENCODING
    else:
        if verdict.stream_transcode is None:
            typer.echo(f"{file} already plays well; nothing to do.")
            return
        optional = verdict.stream_transcode == "recommended"
        encoding = PLAYBACK_ENCODING

    target_literal: TranscodeTarget = (
        "analysis" if target is Target.analysis else "playback"
    )
    try:
        result = run_transcode(
            file,
            output,
            target_literal,
            facts,
            verdict,
            profile=playback_profile,
            thresholds=DEFAULT_THRESHOLDS,
            encoding=encoding,
            allow_hardware=allow_hardware,
        )
    except (TranscodeError, MediaProbeError) as exc:
        message = f"transcode failed: {exc}"
        typer.echo(message, err=True)
        raise typer.Exit(code=1) from exc

    if not result.performed or result.output_path is None:
        typer.echo(
            f"{file} is already clean for the {target.value} target; nothing to do."
        )
        return
    if optional:
        note = (
            f"note: the {target.value} transcode was optional "
            "(a soft playback reason, not a hard one)."
        )
        typer.echo(note)
    operation_name = "" if result.operation is None else result.operation.value
    # A copy remux names no encoder, so the detail stays a bare operation there.
    # On a re-encode the encoder is worth saying: permitting hardware on a machine
    # whose device cannot open av1_nvenc encodes on the CPU, and a run that took
    # tens of times longer than expected should not leave the reader guessing.
    detail = operation_name
    if result.encoder_name:
        detail = f"{operation_name}, {result.encoder_name}"
    typer.echo(f"wrote {result.output_path} ({detail}).")


__all__ = ["app", "media_app"]
