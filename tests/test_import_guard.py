"""Layered modules import without a forbidden dependency present.

`_run_guarded` is the shared seam for every "import X with root Y poisoned"
check in this package. It runs the import statements under test in a fresh
interpreter whose sys.meta_path carries a finder that raises AssertionError on
any import whose top-level name is the poisoned root. Subprocess isolation is
essential: a module already imported into this test process would be served from
sys.modules and never consult the finder, masking a violation. AssertionError --
not ImportError -- so a `try/except ImportError` optional-import guard inside a
module under test cannot swallow the violation. The core is kept dependency-free
on purpose: the transcode CLI must start on a machine that has ffmpeg and nothing
else.

Beyond the core checks, each layer's dependency ceiling is pinned from both
sides: the io subpackage imports without typer and cv2 but must fail with numpy
poisoned, and the cli app fails with typer poisoned while importing without
numpy. Every check calls `_run_guarded`; the helper is the single home for the
poison-finder idiom.
"""

import subprocess
import sys

_POISON_FINDER = """
import sys
from importlib.abc import MetaPathFinder


class Poison(MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split(".")[0] == FORBIDDEN_ROOT:
            raise AssertionError(
                "a module under test imported the forbidden dependency: " + fullname
            )
        return None


sys.meta_path.insert(0, Poison())
"""

_CORE_IMPORTS = """
import mosaic_media
import mosaic_media.ffmpeg
import mosaic_media.hwaccel
import mosaic_media.probe
import mosaic_media.probe.boxes
import mosaic_media.probe.candidates
import mosaic_media.probe.errors
import mosaic_media.probe.facts
import mosaic_media.probe.ffprobe
import mosaic_media.probe.gop
import mosaic_media.probe.identity
import mosaic_media.probe.policy
import mosaic_media.probe.probe
import mosaic_media.probe.sequence
import mosaic_media.probe.timing
import mosaic_media.probe.verdict
import mosaic_media.thumbnail
import mosaic_media.thumbnail.downscale
import mosaic_media.thumbnail.extract
import mosaic_media.transcode
import mosaic_media.transcode.commands
import mosaic_media.transcode.convert
import mosaic_media.transcode.errors
"""


def _run_guarded(body: str, *, forbidden_root: str) -> subprocess.CompletedProcess[str]:
    """Import `body` in a fresh interpreter with `forbidden_root` poisoned.

    A meta-path finder raising AssertionError on the poisoned root is installed
    before `body` runs, so any import of that root -- direct or transitive --
    aborts the child with a non-zero exit. Runs in a subprocess on purpose: a
    module already resident in this test process's sys.modules would never
    consult the finder, masking a violation. Returns the completed process so
    the caller can assert on returncode and stderr.
    """
    program = "FORBIDDEN_ROOT = " + repr(forbidden_root) + "\n" + _POISON_FINDER + body
    return subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_the_core_imports_without_numpy() -> None:
    result = _run_guarded(_CORE_IMPORTS, forbidden_root="numpy")
    assert result.returncode == 0, result.stderr


def test_the_core_imports_without_typer() -> None:
    result = _run_guarded(_CORE_IMPORTS, forbidden_root="typer")
    assert result.returncode == 0, result.stderr


def test_the_core_imports_without_cv2() -> None:
    result = _run_guarded(_CORE_IMPORTS, forbidden_root="cv2")
    assert result.returncode == 0, result.stderr


def test_the_core_imports_without_av() -> None:
    result = _run_guarded(_CORE_IMPORTS, forbidden_root="av")
    assert result.returncode == 0, result.stderr


def test_core_facade_imports_without_numpy() -> None:
    result = _run_guarded("import mosaic_media\n", forbidden_root="numpy")
    assert result.returncode == 0, result.stderr


def test_io_subpackage_requires_numpy() -> None:
    result = _run_guarded(
        "import mosaic_media.io\nraise SystemExit('io imported without numpy')\n",
        forbidden_root="numpy",
    )
    # Importing mosaic_media.io must fail because numpy is poisoned; the
    # SystemExit sentinel must never be reached. The failure traceback names the
    # forbidden `import numpy` from the io layer.
    assert result.returncode != 0
    assert "io imported without numpy" not in (result.stdout + result.stderr)
    assert "numpy" in (result.stdout + result.stderr).lower()


def test_io_subpackage_requires_av() -> None:
    result = _run_guarded(
        "import mosaic_media.io\nraise SystemExit('io imported without av')\n",
        forbidden_root="av",
    )
    # Importing mosaic_media.io must fail because av is poisoned; the SystemExit
    # sentinel must never be reached. av is as mandatory to io as numpy -- one
    # decode stack, no half-alive import mode whose reader cannot open anything.
    assert result.returncode != 0
    assert "io imported without av" not in (result.stdout + result.stderr)
    assert "av" in (result.stdout + result.stderr).lower()


def test_the_io_subpackage_imports_without_typer() -> None:
    result = _run_guarded("import mosaic_media.io\n", forbidden_root="typer")
    assert result.returncode == 0, result.stderr


def test_the_io_subpackage_imports_without_cv2() -> None:
    result = _run_guarded("import mosaic_media.io\n", forbidden_root="cv2")
    assert result.returncode == 0, result.stderr


def test_the_cli_needs_typer_and_the_core_does_not() -> None:
    # In a fresh subprocess with typer poisoned, a core module and the cli
    # facade still import -- the facade stays standard library so the console
    # script can report a missing extra -- but the actual application, whether
    # imported as a module or resolved through the facade's lazy `app`
    # attribute, fails: typer is confined to mosaic_media.cli.application.
    # Running in a subprocess (not in-process) is what makes this real: an
    # in-process import would find the modules already cached in sys.modules
    # and prove nothing.
    core = _run_guarded(
        "import mosaic_media.transcode.commands", forbidden_root="typer"
    )
    assert core.returncode == 0, core.stderr
    facade = _run_guarded("import mosaic_media.cli", forbidden_root="typer")
    assert facade.returncode == 0, facade.stderr
    application = _run_guarded(
        "import mosaic_media.cli.application", forbidden_root="typer"
    )
    assert application.returncode != 0
    assert "typer" in application.stderr
    lazy = _run_guarded("from mosaic_media.cli import app", forbidden_root="typer")
    assert lazy.returncode != 0
    assert "typer" in lazy.stderr


def test_the_cli_imports_without_numpy() -> None:
    result = _run_guarded("import mosaic_media.cli.application", forbidden_root="numpy")
    assert result.returncode == 0, result.stderr


def test_the_cli_imports_without_av() -> None:
    result = _run_guarded("import mosaic_media.cli.application", forbidden_root="av")
    assert result.returncode == 0, result.stderr
