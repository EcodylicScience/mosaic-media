"""Run a system ffmpeg or ffprobe command to completion. Standard library only.

The failure vocabulary lives here rather than at each call site: a missing
binary, a timeout, and a nonzero exit read the same whichever command produced
them, so they are worded once. `run_to_completion` covers the blocking calls.
The transcode converter drives its own `Popen` lifecycle instead, because it
streams a progress protocol and polls a cancel token, but it builds its failure
messages from these same helpers so the wording keeps one source.

`hwaccel` shells out too but stays away from all of this on purpose: it folds
every subprocess failure into a bool and never raises, so it has no error to
inject and no message to word.

The raised error type is injected: the probe and thumbnail paths raise
`MediaProbeError` and the converter raises `TranscodeError`, both of which live
above this module and neither of which it may import.
"""

import subprocess
from pathlib import Path

_UNKNOWN_ERROR = "unknown error"


def not_found_message(binary: str, exc: OSError) -> str:
    return f"{binary} binary not found on PATH: {exc}"


def timed_out_message(binary: str, action: str, *, timeout: float | None = None) -> str:
    """Word a timeout, naming the limit only when the caller chose it.

    A transcode times out against a caller-supplied limit, so naming it tells
    the operator which number to raise. The probe and thumbnail limits are
    module constants no caller can set, and naming those would print a figure
    nobody can act on.
    """
    if timeout is None:
        return f"{binary} timed out {action}"
    return f"{binary} timed out after {timeout:g}s {action}"


def failed_message(binary: str, action: str, stderr_text: str) -> str:
    """Word a nonzero exit, falling back when the command wrote no stderr."""
    detail = stderr_text.strip() or _UNKNOWN_ERROR
    return f"{binary} failed {action}: {detail}"


def run_to_completion(
    command: list[str],
    *,
    timeout: float,
    action: str,
    error_type: type[RuntimeError],
) -> str:
    """Run `command`, returning its stdout and raising `error_type` on any failure.

    `action` is a verb phrase naming what the command was doing, read straight
    into the failure message ("scanning the packets of /video.mp4"). The binary
    named in that message is taken from `command`, so it cannot disagree with
    the process actually run.
    """
    binary = command[0]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as exc:
        message = not_found_message(binary, exc)
        raise error_type(message) from exc
    except subprocess.TimeoutExpired as exc:
        message = timed_out_message(binary, action)
        raise error_type(message) from exc
    if result.returncode != 0:
        message = failed_message(binary, action, result.stderr)
        raise error_type(message)
    return result.stdout


def require_output(
    destination: Path, *, binary: str, error_type: type[RuntimeError]
) -> None:
    """Raise unless `destination` exists and is non-empty.

    A zero exit is not proof a still-image command wrote anything: ffmpeg can
    decline the frame and exit clean, leaving an empty file the caller would
    otherwise publish.
    """
    if not destination.exists() or destination.stat().st_size == 0:
        message = f"{binary} produced no output at {destination}"
        raise error_type(message)
