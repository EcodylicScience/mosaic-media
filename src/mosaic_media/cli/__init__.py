"""The `mosaic-media` command line app. The only module that imports typer.

Two commands mirror the library: `probe` prints MediaFacts and both verdicts as
JSON; `transcode` runs the minimum operation for a target and honors the opt-out
semantics. Job infrastructure calls the Python API directly, never this CLI --
structured exceptions, no argv escaping, no output parsing. `mosaic` mounts this
app with `app.add_typer(media_app, name="media")`.
"""

import dataclasses
import enum
import json
from pathlib import Path
from typing import Annotated

import typer

from ..probe.errors import MediaProbeError
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
    help="Probe a video and run the minimum ffmpeg transcode its verdict calls for.",
    no_args_is_help=True,
    add_completion=False,
)
# Alias for mounting into the mosaic CLI: app.add_typer(media_app, name="media").
media_app = app


class Target(str, enum.Enum):
    analysis = "analysis"
    playback = "playback"


class Profile(str, enum.Enum):
    chrome_149 = "chrome-149"


_PROFILES: dict[Profile, PlaybackProfile] = {Profile.chrome_149: CHROME_149}


def _json_default(value: object) -> object:
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if isinstance(value, enum.Enum):
        return value.value
    message = f"cannot serialize {type(value).__name__} to JSON"
    raise TypeError(message)


@app.command()
def probe(file: Path) -> None:
    """Probe FILE and print its MediaFacts and both verdicts as JSON on stdout."""
    try:
        facts = probe_media(file)
    except MediaProbeError as exc:
        message = f"probe failed: {exc}"
        typer.echo(message, err=True)
        raise typer.Exit(code=1) from exc
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    document = {
        "facts": dataclasses.asdict(facts),
        "verdict": dataclasses.asdict(verdict),
    }
    typer.echo(json.dumps(document, default=_json_default, indent=2, sort_keys=True))


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
            help=(
                "Permit av1_nvenc hardware encoding; used only when the system "
                "ffmpeg offers it. Off by default (permitting enables it only when "
                "detected)."
            ),
        ),
    ] = False,
) -> None:
    """Transcode FILE for the analysis or playback target, running the minimum operation."""
    try:
        facts = probe_media(file)
    except MediaProbeError as exc:
        message = f"probe failed: {exc}"
        typer.echo(message, err=True)
        raise typer.Exit(code=1) from exc

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
    typer.echo(f"wrote {result.output_path} ({operation_name}).")


__all__ = ["app", "media_app"]
