"""The suite runs against an FFmpeg that carries no GPL-only encoder.

libx264 and libx265 are GPL-2.0-or-later. An FFmpeg built without them still
decodes H.264 and HEVC -- those decoders are native and LGPL -- so nothing this
package reads is affected. Only encoding to those codecs is lost, and this
package encodes AV1.

The suite must be runnable against the same LGPL FFmpeg the consumers deploy: a
run against a different build is not testing the deployment, and `content_digest`
folds in libavformat's demuxer output, so the build is not an implementation
detail. A call site that requests `libx264` breaks that. It fails at fixture
creation, before any code under test is reached, and the failure reads like a
probe regression rather than a missing encoder.

Media that must genuinely be H.264 or HEVC is committed under `tests/assets/`
instead; consuming it needs only the native decoder.

The scan is over string literals, not lines: an encoder is requested as a bare
name (`"-c:v", "libx264"` split across lines, `add_stream("libx264")`, a keyword
default), and every form is the same literal. Codec *names* in assertions are
unaffected -- a probe reports `"h264"`, which is not `"libx264"`.
"""

import ast
from pathlib import Path

# A denylist of exact names, not a proof. It catches the literal a call site
# actually writes; it does not catch a name assembled by concatenation, read
# from a variable, or buried inside a joined argument string. Treat a green run
# as "nobody wrote one down", not as "no GPL encoder is reachable" -- the
# reachability proof is the deployment image's own check against the linked
# library, not this test.
GPL_ENCODERS = frozenset({"libx264", "libx264rgb", "libx265", "libx262", "libxvid"})

_TESTS_ROOT = Path(__file__).parent
_REPOSITORY_ROOT = _TESTS_ROOT.parent

# The performance gate baselines against OpenCV, which cannot decode AV1, so its
# corpus is H.264 and needs a GPL encoder. It is deselected by default, never
# distributed, and ends the session rather than failing when that encoder is
# absent (tests/bench/conftest.py).
_EXEMPT = (_TESTS_ROOT / "bench",)


def _string_literals(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _offenders(root: Path) -> tuple[list[str], int]:
    """Every GPL encoder name written down under `root`, and the files scanned."""
    offenders: list[str] = []
    scanned = 0
    for path in sorted(root.rglob("*.py")):
        if path == Path(__file__) or any(exempt in path.parents for exempt in _EXEMPT):
            continue
        scanned += 1
        for line, value in _string_literals(path):
            if value in GPL_ENCODERS:
                offenders.append(
                    f"{path.relative_to(_REPOSITORY_ROOT)}:{line}: {value}"
                )
    return offenders, scanned


def test_no_test_names_a_gpl_encoder() -> None:
    assert _TESTS_ROOT.is_dir(), f"nothing to scan: {_TESTS_ROOT} is not a directory"
    offenders, scanned = _offenders(_TESTS_ROOT)
    assert scanned, "the guard scanned no files, so it proved nothing"
    joined = "\n  ".join(offenders)
    assert not offenders, (
        f"these call sites name a GPL-only encoder, so the suite cannot run "
        f"against an LGPL FFmpeg:\n  {joined}"
    )


def test_no_source_module_names_a_gpl_encoder() -> None:
    source_root = _REPOSITORY_ROOT / "src"
    assert source_root.is_dir(), f"nothing to scan: {source_root} is not a directory"
    offenders, scanned = _offenders(source_root)
    assert scanned, "the guard scanned no files, so it proved nothing"
    joined = "\n  ".join(offenders)
    assert not offenders, (
        f"these modules name a GPL-only encoder, which links GPL code into "
        f"every consumer of this package:\n  {joined}"
    )
