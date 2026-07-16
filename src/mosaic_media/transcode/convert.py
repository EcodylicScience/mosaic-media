"""Run a TranscodeCommand and prove the output clean.

Writes to a temporary file beside the destination and renames atomically only
after the output re-probes clean for the requested target. The re-probe is the
acceptance test: a variable-rate source resampled to a constant rate can still
carry residual drift, so the verdict runs on the transcoded bytes, not just on
the source. Cleanliness is judged against the whole target, not only the reasons
the command set out to fix: the analysis output must need no analysis transcode,
and the playback output must not be unplayable. A transcode that clears its
target reason but introduces a new one therefore still fails. A playback output
that plays but still carries a soft reason is not a failure; it is reported to
the caller. That output probe also mints the derivative's authoritative
MediaFacts, measured once here because consumers never re-measure.

A red verdict on the transcoded output is a terminal failure, not a transient
one. The converter raises and stops; a caller must not respond by scheduling
another transcode, because re-running the same deterministic command on the
same input yields the same red output and a retry would loop. Retries are
reserved for transient faults such as a killed subprocess or a full disk.
Confidence that a command produces clean output for a class of defect comes
from the corpus acceptance tests exercised during development, not from runtime
retrying.

Output destinations are caller-owned. The resolved output is never allowed to
equal the source: the original is preserved in every case, so the converter
refuses to write over the input and raises instead. Given a directory, the
output filename is derived from the source stem with an .mp4 container; given a
file path, that path is used verbatim. This package knows nothing of any
dataset directory layout. Re-running a transcode to an existing output path
replaces it atomically.
"""

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..probe.errors import MediaProbeError
from ..probe.facts import MediaFacts
from ..probe.policy import PlaybackProfile, Thresholds
from ..probe.probe import probe_media
from ..probe.verdict import Verdict, derive

from .commands import EncodingParameters, Operation, Target, build_command

DEFAULT_TRANSCODE_TIMEOUT_SECONDS = 3600.0


class TranscodeError(RuntimeError):
    """A transcode failed to run, or its output was not clean for the target.

    Terminal: a caller must not respond by scheduling another transcode.
    """


@dataclass(frozen=True, slots=True)
class TranscodeResult:
    performed: bool
    operation: Operation | None
    output_path: Path | None
    output_facts: MediaFacts | None
    output_verdict: Verdict | None
    reasons_addressed: frozenset[str]
    # Playback only: the output plays but still carries a soft reason. Not a
    # failure; surfaced so a caller can report the transcode was optional.
    residual_recommended: bool


def _run_ffmpeg(argv: tuple[str, ...], source: Path, timeout: float) -> None:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        message = f"ffmpeg binary not found on PATH: {exc}"
        raise TranscodeError(message) from exc
    except subprocess.TimeoutExpired as exc:
        message = f"ffmpeg timed out after {timeout:g}s transcoding {source}"
        raise TranscodeError(message) from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown error"
        message = f"ffmpeg failed transcoding {source}: {detail}"
        raise TranscodeError(message)


def _resolve_output(source: Path, output: Path) -> Path:
    """Resolve `output` to a concrete destination and refuse to overwrite `source`.

    An existing directory means "write the derivative here under the source stem
    with an .mp4 container"; anything else is used as the file path verbatim. A
    resolved destination equal to the source raises, because originals are always
    preserved.
    """
    destination = output / f"{source.stem}.mp4" if output.is_dir() else output
    destination = destination.absolute()
    if destination.resolve() == source.resolve():
        message = (
            f"refusing to overwrite the source {source}: "
            "choose an --output path other than the input file"
        )
        raise TranscodeError(message)
    return destination


def run_transcode(
    source: Path,
    output: Path,
    target: Target,
    facts: MediaFacts,
    verdict: Verdict,
    *,
    profile: PlaybackProfile,
    thresholds: Thresholds,
    encoding: EncodingParameters,
    allow_hardware: bool = False,
    timeout: float = DEFAULT_TRANSCODE_TIMEOUT_SECONDS,
) -> TranscodeResult:
    """Transcode `source` to `output` for `target`, or report a no-op when the
    source is already clean for that target.

    `output` is a file path, or an existing directory the derivative is written
    into under the source stem with an .mp4 container. The resolved destination may
    never equal the source: originals are preserved, so a resolved destination equal
    to `source` raises rather than overwriting it. Re-running to an existing output
    replaces it atomically.

    `facts` and `verdict` are the authoritative measurement of `source`; this
    function never re-measures the source (measurement is not re-derived), only the
    output. On success the output is renamed into place atomically. The acceptance
    gate is terminal and requires the output fully clean for the target: analysis
    output must need no analysis transcode, and playback output must not be
    unplayable. A playback output that still carries a soft reason is not a failure
    but is reported through `TranscodeResult.residual_recommended`. Raises
    `TranscodeError` when ffmpeg fails, when the resolved destination equals the
    source, or when the output is not clean for the target.
    """
    destination = _resolve_output(source, output)
    command = build_command(
        verdict,
        facts,
        target,
        source.absolute(),
        destination,
        encoding=encoding,
        allow_hardware=allow_hardware,
    )
    if command is None:
        return TranscodeResult(
            performed=False,
            operation=None,
            output_path=None,
            output_facts=None,
            output_verdict=None,
            reasons_addressed=frozenset(),
            residual_recommended=False,
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=destination.parent,
        prefix=f".{destination.stem}.",
        suffix=".mp4",
        delete=False,
    )
    temporary = Path(handle.name)
    handle.close()
    argv = (*command.argv[:-1], str(temporary))
    try:
        _run_ffmpeg(argv, source, timeout)
        try:
            output_facts = probe_media(temporary, thresholds)
        except MediaProbeError as exc:
            message = (
                f"transcode of {source} produced output that could not be probed: {exc}"
            )
            raise TranscodeError(message) from exc
        output_verdict = derive(output_facts, profile, thresholds)
        # Terminal acceptance gate: the output must be fully clean for the target,
        # not merely free of the reasons this command set out to fix. A transcode
        # that clears its target reason but introduces a new one still fails here.
        residual_recommended = False
        if target == "analysis":
            if output_verdict.analysis_transcode is not None:
                listing = ", ".join(sorted(output_verdict.analysis_reasons))
                message = (
                    f"transcode of {source} produced output that still needs an "
                    f"analysis transcode ({listing})"
                )
                raise TranscodeError(message)
        else:
            if output_verdict.stream_transcode == "required":
                listing = ", ".join(sorted(output_verdict.stream_reasons))
                message = (
                    f"transcode of {source} produced output that still cannot play "
                    f"in the browser ({listing})"
                )
                raise TranscodeError(message)
            # A residual soft reason is not a failure, but the caller must see it.
            residual_recommended = output_verdict.stream_transcode == "recommended"
        _ = temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return TranscodeResult(
        performed=True,
        operation=command.operation,
        output_path=destination,
        output_facts=output_facts,
        output_verdict=output_verdict,
        reasons_addressed=command.reasons,
        residual_recommended=residual_recommended,
    )
