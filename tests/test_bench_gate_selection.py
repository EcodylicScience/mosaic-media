"""The performance gate ends the session only when it is actually selected.

The gate needs an ffmpeg that encodes H.264, which the deployment's LGPL build
deliberately lacks. Ending the session is right when someone asks for the gate
and cannot have it; ending it during an ordinary run would make the whole suite
unrunnable on exactly the machine the package targets.

Marker deselection is itself implemented in `pytest_collection_modifyitems`, so
the gate's check must run after it. Both directions are driven in a subprocess:
the hook decides during collection, which cannot be re-entered in-process.
"""

import subprocess
import sys
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).parent.parent
_ABSENT = {"MOSAIC_MEDIA_BENCH_CODEC": "an-encoder-no-build-has"}


def _collect(*arguments: str) -> subprocess.CompletedProcess[str]:
    import os

    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *arguments],
        cwd=_REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, **_ABSENT},
    )


def test_default_run_survives_a_machine_without_the_gate_encoder() -> None:
    result = _collect("tests/")
    assert "Exit:" not in result.stdout + result.stderr, (
        "the gate ended an ordinary run; it must only fire when bench is selected"
    )
    assert result.returncode == 0, result.stdout[-800:]


def test_selecting_the_gate_without_its_encoder_ends_the_session() -> None:
    result = _collect("-m", "bench")
    combined = result.stdout + result.stderr
    assert "Exit:" in combined, combined[-800:]
    assert "encodes an-encoder-no-build-has" in combined, combined[-800:]
