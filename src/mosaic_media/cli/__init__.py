"""Standard-library facade over the typer application in `application.py`.

The console script installs with the package whether or not the `[cli]` extra
(typer) is present, because `[project.scripts]` cannot be conditioned on an
extra. This facade keeps `import mosaic_media.cli` free of typer so `main` can
exit with install guidance on a core-only environment instead of dying with a
raw ModuleNotFoundError. `app` and `media_app` resolve lazily to the real
typer application and still require the extra, which `mosaic` (the mounting
consumer) always has.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .application import app, media_app

_MISSING_EXTRA_MESSAGE = (
    "the mosaic-media command line app requires the [cli] extra "
    "(typer is not installed); install mosaic-media[cli] to use it"
)


def main() -> None:
    """Console-script entry point: run the typer app, or exit with guidance
    when the [cli] extra is not installed."""
    try:
        from .application import app as cli_app
    except ModuleNotFoundError as exc:
        if exc.name != "typer":
            raise
        import sys

        print(_MISSING_EXTRA_MESSAGE, file=sys.stderr)
        raise SystemExit(1) from exc
    cli_app()


def __getattr__(name: str) -> object:
    if name in ("app", "media_app"):
        from . import application

        return getattr(application, name)
    message = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(message)


__all__ = ["app", "main", "media_app"]
