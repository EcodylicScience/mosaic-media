"""The stdlib-only core stays importable with nothing but the standard library.

A static companion to tests/test_import_guard.py: that test proves the modules
import cleanly with numpy, typer, and cv2 poisoned; this one reads the source and
rejects any absolute import of a third-party root, catching a dependency hidden
inside a function body that the runtime guard would only see once that function
runs. The core is kept dependency-free for the CLI's sake -- the transcode runner
must start on a machine that has ffmpeg and nothing else.

The same static reader also covers the io layer, whose ceiling is one dependency
higher: the standard library plus numpy, and no reach into a heavier layer. An
io module that imports the core does so with a relative import so this guard can
tell an allowed reach into probe or hwaccel from a forbidden reach into
transcode or cli, which a bare `mosaic_media` root could not distinguish.
"""

import ast
import sys
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[2] / "src" / "mosaic_media"
STDLIB_ONLY_TARGETS: tuple[Path, ...] = (
    CORE_ROOT / "__init__.py",
    CORE_ROOT / "probe",
    CORE_ROOT / "thumbnail",
    CORE_ROOT / "hwaccel.py",
    CORE_ROOT / "transcode",
)

# The io layer adds numpy on top of the stdlib-only core. It reads the core's
# packet scans and facts, so it may import the core, but it must reach nothing
# heavier (transcode, cli) and no third party beyond numpy.
NUMPY_LAYER_TARGETS: tuple[Path, ...] = (CORE_ROOT / "io",)

# The one-way layering across the checked layers, keyed by the first path
# component under src/mosaic_media (module stem for top-level files). Each
# entry lists the layers a file there may reach with a relative import. io and
# transcode may reach the core (probe, hwaccel) and themselves; the
# still-heavier cli layer is absent on purpose: reaching it is a violation,
# and a new layer must be added explicitly.
CORE_RELATIVE_IMPORT_ALLOWANCES: dict[str, frozenset[str]] = {
    "__init__": frozenset({"probe", "thumbnail"}),
    "hwaccel": frozenset(),
    "io": frozenset({"io", "probe", "hwaccel"}),
    "probe": frozenset({"probe"}),
    "thumbnail": frozenset({"thumbnail", "probe"}),
    "transcode": frozenset({"transcode", "probe", "hwaccel"}),
}


def _files_under(targets: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for target in targets:
        if target.is_dir():
            files.extend(sorted(target.rglob("*.py")))
        else:
            files.append(target)
    return files


def source_files() -> list[Path]:
    return _files_under(STDLIB_ONLY_TARGETS)


def numpy_layer_files() -> list[Path]:
    return _files_under(NUMPY_LAYER_TARGETS)


def layered_files() -> list[Path]:
    """Every module under a checked layer: the stdlib-only core plus the io
    layer. The dynamic-import and relative-layering guards span both, since a
    dynamic import or a wrong-direction edge is a violation from either tier."""
    return source_files() + numpy_layer_files()


def imported_roots(source: str) -> set[str]:
    """Top-level module names this source imports absolutely.

    Relative imports carry `level > 0` and are inside the package, so they are
    not reported.
    """
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module is not None:
                roots.add(node.module.split(".")[0])
    return roots


def dynamic_import_calls(source: str) -> set[str]:
    """Names of dynamic-import mechanisms this source reaches for.

    `imported_roots` reads the import statements an abstract syntax tree makes
    visible. A module name assembled at runtime is invisible to it, and
    `importlib` is itself in the standard library, so a dependency smuggled in
    through `importlib.import_module("numpy")` would pass unnoticed. Nothing here
    needs to import anything dynamically, so reaching for the mechanism at all is
    the violation.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "importlib":
                    found.add("importlib")
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module is not None and node.module.split(".")[0] == "importlib":
                found.add("importlib")
        elif isinstance(node, ast.Name) and node.id == "__import__":
            found.add("__import__")
    return found


def relative_import_targets(source: str, package_parts: tuple[str, ...]) -> set[str]:
    """First path component under mosaic_media each relative import lands in.

    A relative import with level N climbs N-1 packages up from the importing
    module's package before descending into its module path, so the resolved
    target names the layer the edge reaches. `from .. import hwaccel` carries
    its target in the alias names instead of the module path; both forms are
    resolved.
    """
    targets: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ImportFrom) or node.level == 0:
            continue
        base = package_parts[: len(package_parts) - (node.level - 1)]
        module_parts = tuple(node.module.split(".")) if node.module else ()
        resolved = base + module_parts
        if resolved:
            targets.add(resolved[0])
        else:
            for alias in node.names:
                targets.add(alias.name.split(".")[0])
    return targets


def test_the_core_imports_only_the_standard_library() -> None:
    modules = source_files()
    assert modules, "stdlib-only core modules not found"
    offenders: dict[str, set[str]] = {}
    for module in modules:
        outside = imported_roots(module.read_text()) - sys.stdlib_module_names
        if outside:
            offenders[module.name] = outside
    message = f"the stdlib-only core must import only the standard library, found: {offenders}"
    assert offenders == {}, message


def test_the_io_layer_imports_only_the_standard_library_and_numpy() -> None:
    modules = numpy_layer_files()
    assert modules, "io layer modules not found"
    allowed = sys.stdlib_module_names | {"numpy"}
    offenders: dict[str, set[str]] = {}
    for module in modules:
        outside = imported_roots(module.read_text()) - allowed
        if outside:
            offenders[module.name] = outside
    message = f"the io layer must import only the standard library and numpy, found: {offenders}"
    assert offenders == {}, message


def test_the_core_never_imports_dynamically() -> None:
    modules = layered_files()
    assert modules, "layered modules not found"
    offenders: dict[str, set[str]] = {}
    for module in modules:
        dynamic = dynamic_import_calls(module.read_text())
        if dynamic:
            offenders[module.name] = dynamic
    message = f"the layered modules must not import dynamically, found: {offenders}"
    assert offenders == {}, message


def test_core_relative_imports_follow_the_layering() -> None:
    """Relative imports between the checked layers stay one-way.

    The absolute-import tests above cannot see these edges (relative imports
    carry `level > 0` and are skipped), and the poisoned-import guard only
    fires on third-party roots, so a wrong-direction edge such as probe
    importing thumbnail, or io importing cli, would otherwise pass both guards.
    """
    modules = layered_files()
    assert modules, "layered modules not found"
    offenders: dict[str, set[str]] = {}
    for module in modules:
        relative_path = module.relative_to(CORE_ROOT)
        package_parts = relative_path.parts[:-1]
        layer = package_parts[0] if package_parts else relative_path.stem
        allowed = CORE_RELATIVE_IMPORT_ALLOWANCES[layer]
        outside = relative_import_targets(module.read_text(), package_parts) - allowed
        if outside:
            offenders[str(relative_path)] = outside
    message = (
        f"core relative imports must follow the one-way layering, found: {offenders}"
    )
    assert offenders == {}, message
