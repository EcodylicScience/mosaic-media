"""Shared helpers for the CLI smoke tests."""

from typer.testing import Result


def combined(result: Result) -> str:
    """stdout and stderr together, tolerant of click's version differences.

    Typer vendors its own click fork and re-exports the testing surface, so
    `Result` comes from `typer.testing` rather than the (now optional,
    possibly absent) `click` package. `Result.output` is stdout-only and error
    text lands on `Result.stderr`; some click versions merge the streams and
    raise on `Result.stderr` instead. Reading both defensively matches error
    text on either.
    """
    text = result.output
    try:
        stderr = result.stderr
    except ValueError:
        stderr = ""
    return text + (stderr or "")
