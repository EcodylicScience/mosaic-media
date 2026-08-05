"""The transcode failure type, in its own module so both the command builder and
the converter can raise it.

The converter imports the command builder, so a type defined in the converter and
raised by the builder would be a circular import. This mirrors
`mosaic_media.probe.errors`, which exists for the same reason.
"""


class TranscodeError(RuntimeError):
    """A transcode could not be built, failed to run, or produced output that was
    not clean for its target.

    Terminal: a caller must not respond by scheduling another transcode.
    """
