"""mosaic-media: media probing, verdicts, and thumbnails through system ffmpeg.

The public import path. Consumers import from `mosaic_media`, not from the
subpackages. This facade and the `probe`, `thumbnail`, and `hwaccel` modules are
standard library only; the frame reader (`[io]`, numpy) and the command line app
(`[cli]`, typer) are separate optional layers and are not re-exported here, so
`import mosaic_media` never pulls numpy or typer.
"""

from .probe.candidates import VIDEO_EXTENSIONS, is_candidate_video
from .probe.errors import MediaProbeError
from .probe.facts import MediaFacts
from .probe.identity import (
    DRIFT_SAFETY,
    TIMESTAMP_QUANTUM_SECONDS,
    DuplicateComparison,
    DuplicateVerdict,
    compare_for_duplicate,
    timing_tolerance,
)
from .probe.policy import (
    CHROME_149,
    DEFAULT_THRESHOLDS,
    HARD_STREAM_REASONS,
    AnalysisReason,
    PlaybackProfile,
    StreamReason,
    StreamTranscode,
    Thresholds,
)
from .probe.probe import probe_media
from .probe.sequence import (
    MeasuredVideoProperties,
    PropertyMismatch,
    VideoProperties,
    canonical_fps,
    measured_or_none,
    uniform_properties,
)
from .probe.verdict import Verdict, derive
from .thumbnail import downscale_to_jpeg, extract_first_frame, thumbnail_dimensions

__all__ = [
    "CHROME_149",
    "DEFAULT_THRESHOLDS",
    "DRIFT_SAFETY",
    "HARD_STREAM_REASONS",
    "TIMESTAMP_QUANTUM_SECONDS",
    "VIDEO_EXTENSIONS",
    "AnalysisReason",
    "DuplicateComparison",
    "DuplicateVerdict",
    "MeasuredVideoProperties",
    "MediaFacts",
    "MediaProbeError",
    "PlaybackProfile",
    "PropertyMismatch",
    "StreamReason",
    "StreamTranscode",
    "Thresholds",
    "Verdict",
    "VideoProperties",
    "canonical_fps",
    "compare_for_duplicate",
    "derive",
    "downscale_to_jpeg",
    "extract_first_frame",
    "is_candidate_video",
    "measured_or_none",
    "probe_media",
    "thumbnail_dimensions",
    "timing_tolerance",
    "uniform_properties",
]
