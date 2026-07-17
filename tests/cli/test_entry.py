"""The console script degrades cleanly on a core-only install.

`[project.scripts]` cannot be conditioned on an extra, so the script lands on
machines without typer. The package facade must import without typer, and
`main` must exit with install guidance instead of a raw traceback. Runs in a
subprocess: typer is installed in this test environment, so the missing-extra
condition is simulated by a meta-path finder that refuses to resolve it.
"""

import subprocess
import sys
import tomllib
from pathlib import Path

_CORE_ONLY_PROGRAM = """
import sys
from importlib.abc import MetaPathFinder


class Block(MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split(".")[0] == "typer":
            raise ModuleNotFoundError("No module named 'typer'", name="typer")
        return None


sys.meta_path.insert(0, Block())

import mosaic_media.cli

mosaic_media.cli.main()
"""


def test_main_without_typer_exits_with_install_guidance() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _CORE_ONLY_PROGRAM],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "mosaic-media[cli]" in result.stderr
    assert "Traceback" not in result.stderr


def test_the_script_entry_point_targets_the_facade() -> None:
    # The entry must point at the standard-library facade's main, not at the
    # typer application: resolving the app at script load is exactly the
    # core-only crash being prevented.
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with pyproject.open("rb") as handle:
        payload = tomllib.load(handle)
    assert payload["project"]["scripts"]["mosaic-media"] == "mosaic_media.cli:main"
