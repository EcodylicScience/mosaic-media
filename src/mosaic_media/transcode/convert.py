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
MediaFacts, measured once here because measurement is not re-derived.

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

import queue
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from ..ffmpeg import failed_message, not_found_message, timed_out_message
from ..probe.errors import MediaProbeError
from ..probe.facts import MediaFacts
from ..probe.policy import PlaybackProfile, Thresholds
from ..probe.probe import probe_media
from ..probe.verdict import Verdict, derive

from .commands import EncodingParameters, Operation, Target, build_command
from .errors import TranscodeError

DEFAULT_TRANSCODE_TIMEOUT_SECONDS = 3600.0

# How often ffmpeg emits a -progress block. A fine period gives a heartbeat
# smooth progress and bounds how long a caller waits for the first update on a
# short encode; the readings are cheap to parse, so a few per second costs
# nothing.
_PROGRESS_INTERVAL_SECONDS = 0.1
# How often the run loop wakes to re-check the timeout deadline and the cancel
# token while waiting for the next progress block. It only matters when the
# encoder stalls or produces no output; it bounds how long a stalled or
# canceled run keeps running.
_PROGRESS_POLL_SECONDS = 0.1
# Grace given to a terminated child to exit before it is killed outright.
_TERMINATE_GRACE_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class TranscodeProgress:
    """One progress sample emitted while ffmpeg runs.

    `fraction` is the completed fraction in [0, 1], or None when the source
    duration is unknown: a timestampless stream (a raw elementary stream) probes a
    duration of 0.0, and those are exactly the files the analysis verdict
    transcodes. `out_time` (seconds encoded so far), `speed` (the realtime
    multiple), and `fps` are ffmpeg's own readings, carried through unchanged.
    Each is None when ffmpeg reports it as N/A, which it does for any reading it
    cannot compute for the run in hand: a copy remux of packets that reach the
    muxer without timestamps reports none of the three, so an update can arrive
    with every field None. A caller therefore drives an indeterminate display off
    the arrival of updates and annotates it with whichever readings are present.
    """

    fraction: float | None
    out_time: float | None
    speed: float | None
    fps: float | None


# Injected by the caller and polled during the run: a progress sink and a
# cooperative cancel token. Both are optional; the CLI passes neither.
ProgressCallback = Callable[[TranscodeProgress], None]
CancelCheck = Callable[[], bool]


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
    # The input's identity, carried because no hash of the output can recover
    # it: a transcode changes the pixels and therefore every measured fact.
    # Populated on the no-op branch too -- it describes the input, not the
    # output, and the input facts are in hand there.
    source_video_uuid: str


def _parse_reading(value: str | None) -> float | None:
    """Parse one ffmpeg progress reading to a float, or None when it is absent.

    Handles the `speed` value's trailing `x` (`1.23x`) and ffmpeg's `N/A`
    placeholder for a reading it cannot yet report.
    """
    if value is None:
        return None
    stripped = value.strip().removesuffix("x")
    if not stripped or stripped == "N/A":
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


def _parse_clock(value: str | None) -> float | None:
    """Parse an ffmpeg `HH:MM:SS.ffffff` timestamp to seconds, or None."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped or stripped == "N/A":
        return None
    parts = stripped.split(":")
    if len(parts) != 3:
        return None
    hours, minutes, seconds = parts
    try:
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError:
        return None


def _out_time_seconds(block: dict[str, str]) -> float | None:
    """Seconds encoded so far: `out_time_us` first, the `out_time` clock as fallback."""
    micros = _parse_reading(block.get("out_time_us"))
    if micros is not None:
        return micros / 1_000_000
    return _parse_clock(block.get("out_time"))


def _progress_from_block(block: dict[str, str], duration: float) -> TranscodeProgress:
    out_time = _out_time_seconds(block)
    # A timestampless source probes duration 0.0; then no completion fraction is
    # knowable and the update stays indeterminate, carrying only the raw readings.
    fraction = out_time / duration if out_time is not None and duration > 0 else None
    return TranscodeProgress(
        fraction=fraction,
        out_time=out_time,
        speed=_parse_reading(block.get("speed")),
        fps=_parse_reading(block.get("fps")),
    )


def _terminate(process: "subprocess.Popen[str]") -> None:
    """Stop a running ffmpeg child, escalating to a kill if it ignores the signal."""
    process.terminate()
    try:
        _ = process.wait(timeout=_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        _ = process.wait()


def _drain_stdout(stream: IO[str], sink: "queue.Queue[str | None]") -> None:
    """Feed ffmpeg's progress lines to `sink`, then a None end marker at EOF.

    Reading on a thread lets the run loop keep re-checking the timeout deadline
    and the cancel token even while no progress line is arriving. The stream is
    closed here rather than by the run loop, because this is the thread reading
    it: a close from anywhere else can land on an in-flight read, where it blocks
    on the buffer lock the reader holds.

    The end marker is posted even when the read fails, because it is what stops
    the run loop waiting. A read that dies without one leaves the loop polling
    until the transcode deadline expires, which defaults to an hour.
    """
    try:
        with stream:
            for line in stream:
                sink.put(line)
    finally:
        sink.put(None)


def _run_ffmpeg(
    argv: tuple[str, ...],
    source: Path,
    timeout: float,
    duration: float,
    on_progress: ProgressCallback | None,
    cancel_check: CancelCheck | None,
) -> None:
    # -progress writes a key=value stream to stdout; the -v error stderr keeps its
    # failure detail. -progress and -stats_period are global options, so they go
    # right after the ffmpeg binary, ahead of the inputs.
    progress_argv = (
        argv[0],
        "-progress",
        "pipe:1",
        "-stats_period",
        f"{_PROGRESS_INTERVAL_SECONDS:g}",
        *argv[1:],
    )
    binary = argv[0]
    action = f"transcoding {source}"
    with tempfile.TemporaryFile(mode="w+") as stderr_file:
        try:
            process = subprocess.Popen(
                progress_argv,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                text=True,
            )
        except FileNotFoundError as exc:
            message = not_found_message(binary, exc)
            raise TranscodeError(message) from exc
        assert process.stdout is not None
        stdout = process.stdout
        reader: threading.Thread | None = None
        try:
            deadline = time.monotonic() + timeout
            block: dict[str, str] = {}
            lines: queue.Queue[str | None] = queue.Queue()
            drain = threading.Thread(
                target=_drain_stdout, args=(stdout, lines), daemon=True
            )
            drain.start()
            # Bound only once the drain is running. Starting a thread can fail
            # outright -- a process limit in the minimal container this runner is
            # built to start in is enough -- and a thread that never started
            # neither closes the stream nor can be joined, so the cleanup below
            # has to tell that case apart from a running drain.
            reader = drain
            while True:
                if cancel_check is not None and cancel_check():
                    message = f"transcode of {source} was canceled"
                    raise TranscodeError(message)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    message = timed_out_message(binary, action, timeout=timeout)
                    raise TranscodeError(message)
                try:
                    line = lines.get(timeout=min(remaining, _PROGRESS_POLL_SECONDS))
                except queue.Empty:
                    continue
                if line is None:
                    break
                key, separator, value = line.strip().partition("=")
                if not separator:
                    continue
                if key != "progress":
                    block[key] = value
                    continue
                # A `progress=continue|end` line closes one block.
                if on_progress is not None:
                    on_progress(_progress_from_block(block, duration))
                block = {}
                if value == "end":
                    break
            remaining = deadline - time.monotonic()
            try:
                returncode = process.wait(timeout=max(remaining, 0.0))
            except subprocess.TimeoutExpired:
                message = timed_out_message(binary, action, timeout=timeout)
                raise TranscodeError(message)
            if returncode != 0:
                _ = stderr_file.seek(0)
                message = failed_message(binary, action, stderr_file.read())
                raise TranscodeError(message)
        finally:
            # The runner owns the child and the pipe, and releases both on the
            # way out. The order is forced: the child has to be gone before the
            # drain is waited on, because only its exit closes the pipe's write
            # end and lets the drain reach EOF and release the read end. Waiting
            # on a process does not close the pipe it was handed, so a runner
            # that merely waits leaves a descriptor to the garbage collector.
            # Stopping the child belongs here rather than at each raise, because
            # the paths that need it most are the ones the runner does not write:
            # a caller's on_progress or cancel_check that raises leaves the
            # encode running with no handle left to stop it, while the partial
            # output is unlinked out from under it.
            if process.poll() is None:
                _terminate(process)
            if reader is None:
                stdout.close()
            else:
                reader.join(timeout=_TERMINATE_GRACE_SECONDS)


def _resolve_output(source: Path, output: Path) -> Path:
    """Resolve `output` to a concrete destination and refuse to overwrite `source`.

    An existing directory means "write the derivative here under the source stem
    with an .mp4 container"; anything else is used as the file path verbatim. A
    resolved destination equal to the source raises, because originals are always
    preserved. A file destination whose suffix is not .mp4 also raises: the
    converter always produces an mp4 container, so any other extension would
    misdescribe the bytes it writes. Directory destinations are unaffected, since
    the derived filename already carries an .mp4 container.
    """
    destination = output / f"{source.stem}.mp4" if output.is_dir() else output
    destination = destination.absolute()
    if destination.suffix.lower() != ".mp4":
        message = (
            f"refusing to write {destination} with suffix {destination.suffix!r}: "
            "the converter always produces an mp4 container, so a file destination "
            "must end in .mp4"
        )
        raise TranscodeError(message)
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
    on_progress: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
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
    `TranscodeError` when ffmpeg fails, when a file destination's suffix is not
    .mp4, when the resolved destination equals the source, when the source states
    no frame rate in either its container or its bitstream, or when the output is
    not clean for the target.

    `on_progress`, when given, is called with a `TranscodeProgress` for each block
    ffmpeg emits during the encode. `cancel_check`, when given, is polled during
    the run; a true result stops the child, cleans up the partial output, and
    raises a `TranscodeError` naming the run canceled. Both default to None, so a
    caller that wants neither -- including the CLI -- is unaffected.
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
            source_video_uuid=facts.video_uuid,
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
        _run_ffmpeg(argv, source, timeout, facts.duration, on_progress, cancel_check)
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
        source_video_uuid=facts.video_uuid,
    )
