"""The transcode error type is owned by its own module and exported by the package."""

from mosaic_media.transcode import TranscodeError as PackageExport
from mosaic_media.transcode.errors import TranscodeError


def test_the_package_exports_the_class_the_error_module_defines() -> None:
    # The command builder refuses a source that states no rate anywhere, and the
    # converter raises on a run that failed. Both need the same type, and the
    # converter already imports the command builder, so the type cannot live in
    # the converter without making that import circular. It is imported from the
    # package, which must show none of that.
    assert PackageExport is TranscodeError


def test_the_error_is_a_runtime_error() -> None:
    assert issubclass(TranscodeError, RuntimeError)
