# Transcode and CLI Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with a fresh implementer per task and a review between tasks. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the transcode layer (`transcode/commands.py`, `transcode/convert.py`) and
the typer CLI (`cli/`) to `mosaic_media`, on top of the extracted probe core from plan 1.
The command builder maps a `Verdict` to the minimum ffmpeg operation; the converter runs it
and re-probes the output as its acceptance test; the CLI exposes `probe` and `transcode`.

**Architecture:** `transcode/commands.py` is pure argv construction (no I/O): it reads a
`Verdict` plus `MediaFacts` plus injected `EncodingParameters` and emits a frozen
`TranscodeCommand`. `transcode/convert.py` executes that command through `subprocess`, writes
to a temp file beside the destination, renames atomically only after the output re-probes
clean for the requested target, and raises `TranscodeError` otherwise. `cli/` is the only
module that imports typer; it calls the probe and transcode Python APIs and prints JSON or a
short status line. Encoder selection (SVT-AV1 on CPU, av1_nvenc when allowed and present) goes
through the plan-1 `hwaccel` module. Nothing browser-specific is hardcoded: `PlaybackProfile`,
`Thresholds`, and `EncodingParameters` are all injected by the caller.

**Tech Stack:** Python 3.12+, uv, system ffmpeg/ffprobe on PATH, typer (cli extra only). No
numpy, no OpenCV, no third-party runtime dependency in `transcode/`.

**Sequencing note:** This plan builds the transcode feature, but the transcode must not ship
to production before the frame reader lands in the consumers -- otherwise the stack produces
AV1 files its own OpenCV-backed toolkit cannot decode. That invariant binds the later consumer
migration effort, not this plan's implementation; both the reader and the transcode are built
inside this overall extraction, and the reader is out of scope here (it is plan 2/3).

## Global Constraints

- Python floor is 3.12, never 3.13 -- this package sits upstream of both consumers.
- `transcode/` is stdlib-only (the core layer): it may import `mosaic_media.probe` and
  `mosaic_media.hwaccel`, and nothing else outside the standard library.
- typer imports live ONLY inside `cli/`. `mosaic_api` must never pull typer or numpy through
  the core; the plan-1 import guard imports every core module with numpy/typer poisoned, and
  Task 4 extends it to cover the transcode modules and add a cli-without-typer guard.
- Intra-package imports inside `transcode/` and `cli/` are package-relative (`from .. import
  hwaccel`, `from ..probe.facts import MediaFacts`, `from .commands import ...`), matching plan
  1's core convention so the purity guard stays green. Test files use absolute imports.
- Policy is injected, never encoded: builders take `PlaybackProfile`, `Thresholds`, and
  `EncodingParameters` as arguments. The only shipped constants are the media-domain encoder
  defaults and the named `CHROME_149` profile that already exists in `probe/policy.py`.
- The command selects the minimum operation, never a blanket re-encode: a lying timing header
  is a `-c copy` remux, a tail `moov` is `-movflags +faststart`, a supported stream in an
  unopenable container is a `-c copy` container rewrap, and only a broken pixel grid or frame
  clock (variable frame rate, rotation, non-square pixels, interlacing) or an undecodable
  stream forces a real AV1 re-encode.
- The transcoded output is re-probed as its acceptance test: both verdicts run on the output
  bytes, because a variable-rate source resampled to constant rate can still carry drift.
- ASCII only in code; no Unicode decorative characters.
- American spelling everywhere (`behavior`, `color`, `gray`).
- No `typing.Any`, no `typing.Optional` (use `X | None`), no `typing.cast`, no `# noqa`, no
  `# pyright: ignore`. Fix the underlying design, never suppress.
- No multi-line f-strings: assign the message to a variable first, then raise or echo.
- Full identifier names, no abbreviations.
- Commit messages are plain English, with NO conventional-commit prefixes (`feat:`, `fix:`)
  and NO `Co-Authored-By` trailers.
- Full-suite runs go through `heavy uv run pytest`; a single file or single test runs plain
  with `uv run pytest <path>`.

## Consumes (delivered by plan 1, relied on here)

- `mosaic_media.probe.facts.MediaFacts` -- frozen dataclass with fields: `container`,
  `codec_name`, `pixel_format`, `color_range`, `color_primaries`, `color_transfer`, `width`,
  `height`, `rotation_degrees`, `square_pixels`, `progressive`, `has_audio`,
  `video_stream_count`, `duration`, `fps`, `frame_count`, `start_time`, `constant_frame_rate`,
  `max_instantaneous_fps`, `declared_duration`, `declared_fps`, `declared_frame_count`,
  `moov_at_start`, `max_keyframe_interval_frames`, `max_gop_bytes`.
- `mosaic_media.probe.policy` -- `PlaybackProfile`, `Thresholds`, `CHROME_149`,
  `DEFAULT_THRESHOLDS`, and the `StreamReason` / `AnalysisReason` string-literal aliases and
  `HARD_STREAM_REASONS`.
- `mosaic_media.probe.verdict.Verdict` -- frozen dataclass with fields `playable: bool`,
  `stream_transcode: Literal["required", "recommended"] | None`,
  `analysis_transcode: Literal["required"] | None`,
  `stream_reasons: frozenset[StreamReason]`, `analysis_reasons: frozenset[AnalysisReason]`,
  `truncated: bool`; and `derive(facts, profile, thresholds) -> Verdict`.
- `mosaic_media.probe.probe.probe_media(path: Path, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> MediaFacts`.
- `mosaic_media.probe.errors.MediaProbeError`.
- `mosaic_media.hwaccel` -- exactly `ffmpeg_available() -> bool`, `nvdec_available() -> bool`,
  `encoder_available(name: str) -> bool`.
- Test scaffolding: `tests/helpers/media_fixtures.py` providing
  `build(destination: Path, *arguments: str, source: list[str] | None = None) -> Path` and the
  session-scoped `clips` fixture (keys used here: `cfr_mp4` -- moov at tail, `faststart_mp4` --
  clean with moov at start, `rotated_mp4` -- rotation 90); registered via the rootdir
  `tests/conftest.py` line `pytest_plugins = ["tests.helpers.media_fixtures"]`.
- pyproject already declares `cli = ["typer>=0.12"]` under `[project.optional-dependencies]`
  and has NO `[project.scripts]` table yet.

The real reason values enumerated from `probe/policy.py`:
`StreamReason` = `unsupported_container`, `unsupported_codec`, `variable_frame_rate`,
`rotated`, `non_square_pixels`, `non_zero_start_time`, `client_dependent_decode`,
`interlaced`, `moov_not_at_start`, `large_seek_payload`, `sparse_keyframes`.
`AnalysisReason` = `variable_frame_rate`, `unreliable_timing_metadata`, `rotated`,
`non_square_pixels`, `interlaced`.

---

## Task 1 -- transcode/commands.py: verdict to argv

**Files:**
- `src/mosaic_media/transcode/__init__.py` (created empty in this task, filled in Task 2)
- `src/mosaic_media/transcode/commands.py` (new)
- `tests/transcode/__init__.py` (new, empty)
- `tests/transcode/test_commands.py` (new)

**Interfaces:**
- Consumes: `MediaFacts`, `Verdict`, `derive`, `StreamReason`, `AnalysisReason`, `CHROME_149`,
  `DEFAULT_THRESHOLDS` (paths above), and `mosaic_media.hwaccel.encoder_available`.
- Produces (public API of `commands.py`, re-exported from `transcode/__init__.py`):
  - `Target = Literal["analysis", "playback"]` -- the single home for the target literal;
    `convert.py` imports it from `.commands`, `cli` imports it from `..transcode`.
  - `class Operation(StrEnum)` with members `REMUX_FASTSTART`, `REMUX_TIMEBASE`,
    `REMUX_CONTAINER`, `REENCODE_AV1`.
  - `@dataclass(frozen=True, slots=True) class EncodingParameters` with fields `quality: int`,
    `cpu_preset: int`, `nvenc_preset: str`, `pixel_format: str`, `keyframe_interval: int | None`,
    `keep_audio: bool`.
  - Constants `ANALYSIS_ENCODING` and `PLAYBACK_ENCODING`.
  - `@dataclass(frozen=True, slots=True) class TranscodeCommand` with fields
    `argv: tuple[str, ...]`, `operation: Operation`, `target: Target`,
    `reasons: frozenset[str]`, `output_path: Path`.
  - `build_command(verdict, facts, target, source, destination, *, encoding, allow_hardware=False) -> TranscodeCommand | None`.

**Reason routing (the complete mapping, using only real reason names):**

| Target | Reason set condition | Operation |
| --- | --- | --- |
| analysis | any of `variable_frame_rate`, `rotated`, `non_square_pixels`, `interlaced` | `REENCODE_AV1` |
| analysis | else `unreliable_timing_metadata` present | `REMUX_TIMEBASE` |
| analysis | else (empty) | None (no-op) |
| playback | any of `unsupported_codec`, `variable_frame_rate`, `rotated`, `non_square_pixels`, `non_zero_start_time`, `client_dependent_decode`, `interlaced`, `large_seek_payload`, `sparse_keyframes` | `REENCODE_AV1` |
| playback | else `unsupported_container` present | `REMUX_CONTAINER` |
| playback | else `moov_not_at_start` present | `REMUX_FASTSTART` |
| playback | else (empty) | None (no-op) |

`non_zero_start_time` is routed to the re-encode: it is a hard reason that already forces the
canonical output, and re-encoding with a reset timestamp origin resolves it; a bare start-time
remux is not defined by the spec and is deliberately not invented here.

### Steps

- [ ] Create `src/mosaic_media/transcode/__init__.py` as an empty file (Task 2 fills it).

- [ ] Create `tests/transcode/__init__.py` as an empty file.

- [ ] Write the failing test file `tests/transcode/test_commands.py`:

```python
"""Every reason-to-operation mapping, built from Verdict and MediaFacts values directly."""

from dataclasses import replace
from pathlib import Path

import pytest

from mosaic_media.probe.policy import CHROME_149, DEFAULT_THRESHOLDS
from mosaic_media.probe.verdict import derive
from mosaic_media.transcode import commands as commands_module
from mosaic_media.transcode.commands import (
    ANALYSIS_ENCODING,
    PLAYBACK_ENCODING,
    EncodingParameters,
    Operation,
    TranscodeCommand,
    build_command,
)
# The 25-field clean baseline lives once, in the copied probe suite; reuse it
# instead of restating it (repo precedent: tests/probe/test_sequence.py imports
# CLEAN the same way).
from tests.probe.test_verdict import CLEAN

SOURCE = Path("/tmp/in.mkv")
DESTINATION = Path("/tmp/out.mp4")


def command_for(
    target: str,
    encoding: EncodingParameters,
    *,
    allow_hardware: bool = False,
    **overrides: object,
) -> TranscodeCommand | None:
    facts = replace(CLEAN, **overrides)
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    return build_command(
        verdict,
        facts,
        target,
        SOURCE,
        DESTINATION,
        encoding=encoding,
        allow_hardware=allow_hardware,
    )


def arg_after(argv: tuple[str, ...], flag: str) -> str:
    index = argv.index(flag)
    return argv[index + 1]


def test_a_clean_file_needs_no_analysis_command() -> None:
    assert command_for("analysis", ANALYSIS_ENCODING) is None


def test_a_clean_file_needs_no_playback_command() -> None:
    assert command_for("playback", PLAYBACK_ENCODING) is None


def test_a_lying_header_is_a_copy_remux_with_regenerated_timestamps() -> None:
    command = command_for(
        "analysis", ANALYSIS_ENCODING, declared_fps=1000.0, declared_frame_count=0
    )
    assert command is not None
    assert command.operation is Operation.REMUX_TIMEBASE
    assert command.reasons == frozenset({"unreliable_timing_metadata"})
    assert "-c" in command.argv
    assert arg_after(command.argv, "-c") == "copy"
    assert arg_after(command.argv, "-fflags") == "+genpts"
    assert "libsvtav1" not in command.argv
    assert command.argv[-1] == str(DESTINATION)


def test_a_tail_moov_is_a_faststart_remux() -> None:
    command = command_for("playback", PLAYBACK_ENCODING, moov_at_start=False)
    assert command is not None
    assert command.operation is Operation.REMUX_FASTSTART
    assert command.reasons == frozenset({"moov_not_at_start"})
    assert arg_after(command.argv, "-c") == "copy"
    assert arg_after(command.argv, "-movflags") == "+faststart"
    assert "libsvtav1" not in command.argv


def test_an_unsupported_container_with_a_supported_codec_is_a_container_remux() -> None:
    command = command_for("playback", PLAYBACK_ENCODING, container="avi")
    assert command is not None
    assert command.operation is Operation.REMUX_CONTAINER
    assert command.reasons == frozenset({"unsupported_container"})
    assert arg_after(command.argv, "-c") == "copy"
    assert "libsvtav1" not in command.argv


def test_variable_frame_rate_drives_an_analysis_reencode() -> None:
    command = command_for(
        "analysis",
        ANALYSIS_ENCODING,
        constant_frame_rate=False,
        max_instantaneous_fps=30.0,
    )
    assert command is not None
    assert command.operation is Operation.REENCODE_AV1
    assert "variable_frame_rate" in command.reasons
    assert "libsvtav1" in command.argv
    assert arg_after(command.argv, "-fps_mode") == "cfr"
    assert "-r" in command.argv
    assert "-an" in command.argv


def test_variable_frame_rate_playback_reencode_keeps_audio_and_caps_gop() -> None:
    command = command_for(
        "playback",
        PLAYBACK_ENCODING,
        constant_frame_rate=False,
        max_instantaneous_fps=30.0,
        has_audio=True,
    )
    assert command is not None
    assert command.operation is Operation.REENCODE_AV1
    assert arg_after(command.argv, "-c:a") == "aac"
    assert arg_after(command.argv, "-g") == "50"


def test_rotation_drives_an_av1_reencode() -> None:
    command = command_for("playback", PLAYBACK_ENCODING, rotation_degrees=90)
    assert command is not None
    assert command.operation is Operation.REENCODE_AV1
    assert "rotated" in command.reasons
    assert "libsvtav1" in command.argv


def test_non_square_pixels_add_a_setsar_filter() -> None:
    command = command_for("analysis", ANALYSIS_ENCODING, square_pixels=False)
    assert command is not None
    assert command.operation is Operation.REENCODE_AV1
    chain = arg_after(command.argv, "-vf")
    assert "setsar=1" in chain
    assert "scale=" in chain


def test_interlacing_adds_a_deinterlace_filter() -> None:
    command = command_for("analysis", ANALYSIS_ENCODING, progressive=False)
    assert command is not None
    assert command.operation is Operation.REENCODE_AV1
    assert "interlaced" in command.reasons
    assert "yadif" in arg_after(command.argv, "-vf")


def test_hardware_selects_nvenc_when_allowed_and_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(commands_module.hwaccel, "encoder_available", lambda name: True)
    command = command_for(
        "playback", PLAYBACK_ENCODING, allow_hardware=True, rotation_degrees=90
    )
    assert command is not None
    assert "av1_nvenc" in command.argv
    assert "-cq" in command.argv
    assert "libsvtav1" not in command.argv


def test_hardware_is_ignored_when_not_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands_module.hwaccel, "encoder_available", lambda name: True)
    command = command_for(
        "playback", PLAYBACK_ENCODING, allow_hardware=False, rotation_degrees=90
    )
    assert command is not None
    assert "libsvtav1" in command.argv
    assert "av1_nvenc" not in command.argv


def test_analysis_and_playback_targets_are_independent() -> None:
    # A tail moov is a playback concern and not an analysis one.
    assert command_for("analysis", ANALYSIS_ENCODING, moov_at_start=False) is None
    playback = command_for("playback", PLAYBACK_ENCODING, moov_at_start=False)
    assert playback is not None
    assert playback.operation is Operation.REMUX_FASTSTART
```

- [ ] Run and confirm it fails on the missing module:

```
uv run pytest tests/transcode/test_commands.py
```

Expected: collection error `ModuleNotFoundError: No module named 'mosaic_media.transcode.commands'`.

- [ ] Implement `src/mosaic_media/transcode/commands.py`:

```python
"""Verdict to ffmpeg argv. Pure construction, no I/O.

The command selects the minimum operation that clears a target's reasons, never a
blanket re-encode. A header that lies about timing needs a `-c copy` remux that
regenerates timestamps; a `moov` at the tail needs `-movflags +faststart`; a
supported stream trapped in an unopenable container needs a `-c copy` container
rewrap; only a defect that breaks the pixel grid or the frame clock -- variable
frame rate, rotation, non-square pixels, interlacing -- or a stream a browser
cannot decode needs a real AV1 re-encode.

Policy is injected. `EncodingParameters` and the two shipped defaults are
media-domain encoder settings; the browser policy lives in the `PlaybackProfile`
the caller passes to `derive`. This module holds no opinion about any browser.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from .. import hwaccel
from ..probe.facts import MediaFacts
from ..probe.policy import AnalysisReason, StreamReason
from ..probe.verdict import Verdict

Target = Literal["analysis", "playback"]

_BASE: tuple[str, ...] = ("ffmpeg", "-hide_banner", "-v", "error", "-y")


class Operation(StrEnum):
    REMUX_FASTSTART = "remux_faststart"
    REMUX_TIMEBASE = "remux_timebase"
    REMUX_CONTAINER = "remux_container"
    REENCODE_AV1 = "reencode_av1"


# Reasons a copy remux cannot fix: the pixel grid or the frame clock is wrong, or
# the stream itself cannot be decoded or streamed economically. Each forces a real
# AV1 re-encode. Names are the real StreamReason / AnalysisReason literals.
_REENCODE_STREAM_REASONS: frozenset[StreamReason] = frozenset(
    {
        "unsupported_codec",
        "variable_frame_rate",
        "rotated",
        "non_square_pixels",
        "non_zero_start_time",
        "client_dependent_decode",
        "interlaced",
        "large_seek_payload",
        "sparse_keyframes",
    }
)
_REENCODE_ANALYSIS_REASONS: frozenset[AnalysisReason] = frozenset(
    {
        "variable_frame_rate",
        "rotated",
        "non_square_pixels",
        "interlaced",
    }
)


@dataclass(frozen=True, slots=True)
class EncodingParameters:
    """Injected AV1 encoder settings.

    `quality` is the constant-quality target on the 0-63 scale shared by SVT-AV1
    `-crf` and NVENC `-cq`; lower is higher quality. `cpu_preset` is the SVT-AV1
    speed preset (0 slowest, 13 fastest); `nvenc_preset` is the NVENC speed preset
    (`p1` slowest, `p7` fastest). `keyframe_interval` bounds the GOP so a seek
    fetches a bounded payload; None leaves the encoder default. `keep_audio`
    re-encodes an audio track to AAC when the source has one; False drops audio.
    """

    quality: int
    cpu_preset: int
    nvenc_preset: str
    pixel_format: str
    keyframe_interval: int | None
    keep_audio: bool


# The analysis derivative is measured, not watched: higher quality, encoder-default
# GOP, no audio.
ANALYSIS_ENCODING = EncodingParameters(
    quality=20,
    cpu_preset=6,
    nvenc_preset="p5",
    pixel_format="yuv420p",
    keyframe_interval=None,
    keep_audio=False,
)

# The playback derivative is streamed and scrubbed: smaller, a capped GOP for
# seeking, audio preserved.
PLAYBACK_ENCODING = EncodingParameters(
    quality=32,
    cpu_preset=8,
    nvenc_preset="p5",
    pixel_format="yuv420p",
    keyframe_interval=50,
    keep_audio=True,
)


@dataclass(frozen=True, slots=True)
class TranscodeCommand:
    """A fully formed ffmpeg invocation plus the intent behind it."""

    argv: tuple[str, ...]
    operation: Operation
    target: Target
    reasons: frozenset[str]
    output_path: Path


def _target_reasons(verdict: Verdict, target: Target) -> frozenset[str]:
    if target == "analysis":
        return frozenset(verdict.analysis_reasons)
    return frozenset(verdict.stream_reasons)


def _select_operation(verdict: Verdict, target: Target) -> Operation | None:
    if target == "analysis":
        if verdict.analysis_reasons & _REENCODE_ANALYSIS_REASONS:
            return Operation.REENCODE_AV1
        if "unreliable_timing_metadata" in verdict.analysis_reasons:
            return Operation.REMUX_TIMEBASE
        return None
    if verdict.stream_reasons & _REENCODE_STREAM_REASONS:
        return Operation.REENCODE_AV1
    if "unsupported_container" in verdict.stream_reasons:
        return Operation.REMUX_CONTAINER
    if "moov_not_at_start" in verdict.stream_reasons:
        return Operation.REMUX_FASTSTART
    return None


def _copy_remux_argv(
    source: Path, destination: Path, *, input_flags: tuple[str, ...] = ()
) -> tuple[str, ...]:
    return (
        *_BASE,
        *input_flags,
        "-i",
        str(source),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(destination),
    )


def _encoder_args(
    encoding: EncodingParameters, *, allow_hardware: bool
) -> tuple[str, ...]:
    if allow_hardware and hwaccel.encoder_available("av1_nvenc"):
        return (
            "-c:v",
            "av1_nvenc",
            "-preset",
            encoding.nvenc_preset,
            "-cq",
            str(encoding.quality),
        )
    return (
        "-c:v",
        "libsvtav1",
        "-preset",
        str(encoding.cpu_preset),
        "-crf",
        str(encoding.quality),
    )


def _video_filters(facts: MediaFacts) -> str | None:
    filters: list[str] = []
    if not facts.progressive:
        filters.append("yadif")
    if not facts.square_pixels:
        # Bake the sample aspect ratio into square pixels; keep dimensions even.
        filters.append("scale=trunc(iw*sar/2)*2:trunc(ih/2)*2")
        filters.append("setsar=1")
    if not filters:
        return None
    return ",".join(filters)


def _reencode_argv(
    source: Path,
    destination: Path,
    facts: MediaFacts,
    encoding: EncodingParameters,
    *,
    allow_hardware: bool,
) -> tuple[str, ...]:
    argv: list[str] = [*_BASE, "-i", str(source)]
    chain = _video_filters(facts)
    if chain is not None:
        argv.extend(["-vf", chain])
    # Constant frame rate at the measured average resamples a variable source.
    # Rotation is baked by ffmpeg's default autorotation on re-encode, which also
    # clears the display-matrix side data; no explicit transpose is needed.
    fps = f"{facts.fps:.6f}"
    argv.extend(["-r", fps, "-fps_mode", "cfr"])
    argv.extend(_encoder_args(encoding, allow_hardware=allow_hardware))
    argv.extend(["-pix_fmt", encoding.pixel_format])
    if encoding.keyframe_interval is not None:
        argv.extend(["-g", str(encoding.keyframe_interval)])
    if encoding.keep_audio and facts.has_audio:
        argv.extend(["-c:a", "aac"])
    else:
        argv.append("-an")
    argv.extend(["-movflags", "+faststart", str(destination)])
    return tuple(argv)


def build_command(
    verdict: Verdict,
    facts: MediaFacts,
    target: Target,
    source: Path,
    destination: Path,
    *,
    encoding: EncodingParameters,
    allow_hardware: bool = False,
) -> TranscodeCommand | None:
    """The minimum ffmpeg command that clears `target`'s reasons, or None when the
    file is already clean for that target."""
    operation = _select_operation(verdict, target)
    if operation is None:
        return None
    if operation is Operation.REENCODE_AV1:
        argv = _reencode_argv(
            source, destination, facts, encoding, allow_hardware=allow_hardware
        )
    elif operation is Operation.REMUX_TIMEBASE:
        argv = _copy_remux_argv(
            source, destination, input_flags=("-fflags", "+genpts")
        )
    else:
        # REMUX_FASTSTART and REMUX_CONTAINER share the copy-remux argv; the
        # operation kind records which reason selected it.
        argv = _copy_remux_argv(source, destination)
    return TranscodeCommand(
        argv=argv,
        operation=operation,
        target=target,
        reasons=_target_reasons(verdict, target),
        output_path=destination,
    )
```

- [ ] Run and confirm it passes:

```
uv run pytest tests/transcode/test_commands.py
```

Expected: `13 passed`.

- [ ] Commit: `Add transcode command construction mapping verdicts to ffmpeg argv`

---

## Task 2 -- transcode/convert.py: runner, output resolution, and re-probe acceptance

**Files:**
- `src/mosaic_media/transcode/convert.py` (new)
- `src/mosaic_media/transcode/__init__.py` (fill in public exports)
- `tests/transcode/conftest.py` (new -- the variable-frame-rate fixture)
- `tests/transcode/test_convert.py` (new)

**Interfaces:**
- Consumes: `build_command`, `TranscodeCommand`, `Operation`, `Target`, `EncodingParameters` (Task 1);
  `probe_media`, `derive`, `Verdict`, `MediaFacts`, `PlaybackProfile`, `Thresholds`,
  `MediaProbeError` (plan 1); the `build` helper and `clips` fixture (plan-1 test scaffolding).
- Produces (public API of `convert.py`, re-exported from `transcode/__init__.py`):
  - `DEFAULT_TRANSCODE_TIMEOUT_SECONDS: float`.
  - `class TranscodeError(RuntimeError)`.
  - `@dataclass(frozen=True, slots=True) class TranscodeResult` with fields `performed: bool`,
    `operation: Operation | None`, `output_path: Path | None`, `output_facts: MediaFacts | None`,
    `output_verdict: Verdict | None`, `reasons_addressed: frozenset[str]`,
    `residual_recommended: bool` (playback only: the output plays but still carries a soft reason).
  - `run_transcode(source, output, target, facts, verdict, *, profile, thresholds, encoding, allow_hardware=False, timeout=DEFAULT_TRANSCODE_TIMEOUT_SECONDS) -> TranscodeResult`, where `output` is a file path or an existing directory (filename derived from the source stem with an `.mp4` container); it refuses a resolved destination equal to `source` and replaces an existing output atomically. The acceptance gate is terminal and requires the output fully clean for the target (analysis: `analysis_transcode` is None; playback: `stream_transcode` is not `"required"`), else it raises `TranscodeError`; a residual `"recommended"` is surfaced via `residual_recommended`, not raised.
- `transcode/__init__.py` exports the full transcode surface (commands + convert).

### Steps

- [ ] Write `tests/transcode/conftest.py` providing a genuinely variable-frame-rate clip:

```python
"""Defect fixtures for the transcode acceptance tests.

The tail-moov and rotated corpora reuse the shared `clips` fixture (`cfr_mp4`,
`rotated_mp4`). Three defect files the shared corpus lacks are generated here: a
genuinely variable-frame-rate clip, a lying-timing-header clip, and an
h264-in-avi clip (a supported codec in an unopenable container).
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.helpers.media_fixtures import build


@pytest.fixture(scope="session")
def variable_frame_rate_mp4(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("vfr")
    # A single encode whose presentation timestamps switch period midway: the
    # first 50 frames are spaced 0.02 s and the remaining 50 are spaced 0.04 s.
    # `-fps_mode passthrough` keeps those timestamps instead of resampling to a
    # constant rate, so the whole-file grid fit measures genuine drift. `testsrc2`
    # emits 100 frames at rate 50 over 2 s; `setpts` relabels their presentation
    # times. `N` is the frame index and `TB` the output timebase.
    expression = "setpts='if(lt(N,50), N/50/TB, (1 + (N-50)/25)/TB)'"
    yield build(
        root / "vfr.mp4",
        "-vf",
        expression,
        "-fps_mode",
        "passthrough",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        source=["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=50:duration=2"],
    )


@pytest.fixture(scope="session")
def lying_header_mkv(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("lying_header")
    # A raw H.264 elementary stream whose SPS VUI advertises 25 fps.
    raw = build(
        root / "raw25.h264",
        "-c:v",
        "libx264",
        "-bf",
        "0",
        "-pix_fmt",
        "yuv420p",
        "-f",
        "h264",
        source=["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=3"],
    )
    # Mux that stream into Matroska at 30 fps: the packet timestamps run at 30
    # while avg_frame_rate keeps the advertised 25 -- a header that lies about the
    # rate (a 16.7% discrepancy) over otherwise-uniform, constant-rate timing.
    # `-c copy -fflags +genpts` into mp4 recomputes avg_frame_rate from the real
    # packets (to within 0.3% of 30) and clears unreliable_timing_metadata.
    yield build(
        root / "lying_header.mkv",
        "-c",
        "copy",
        source=["-r", "30", "-i", str(raw)],
    )


@pytest.fixture(scope="session")
def h264_in_avi(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("h264_avi")
    # h264 is a codec Chrome decodes; AVI is a container it cannot open. The
    # playback fix is a `-c copy` rewrap into mp4, not a re-encode. `-bf 0` keeps
    # the copy to mp4 free of B-frame reordering trouble.
    yield build(
        root / "h264.avi",
        "-c:v",
        "libx264",
        "-bf",
        "0",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "25",
        source=["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=2"],
    )
```

- [ ] Write the failing test file `tests/transcode/test_convert.py`:

```python
"""Converter acceptance: the transcoded output re-probes clean on both verdicts."""

from pathlib import Path

import pytest

from mosaic_media import hwaccel
from mosaic_media.probe.facts import MediaFacts
from mosaic_media.probe.policy import CHROME_149, DEFAULT_THRESHOLDS
from mosaic_media.probe.probe import probe_media
from mosaic_media.probe.verdict import Verdict, derive
from mosaic_media.transcode import (
    ANALYSIS_ENCODING,
    PLAYBACK_ENCODING,
    EncodingParameters,
    Operation,
    TranscodeError,
    TranscodeResult,
    run_transcode,
)
from mosaic_media.transcode import convert as convert_module

# libsvtav1 is a system-ffmpeg build option; skip the re-encode acceptance tests
# with an actionable message when it is absent. The copy-remux tests do not need it.
requires_svtav1 = pytest.mark.skipif(
    not hwaccel.encoder_available("libsvtav1"),
    reason=(
        "libsvtav1 encoder missing from system ffmpeg; install an ffmpeg built "
        "with --enable-libsvtav1 to run the AV1 re-encode acceptance tests"
    ),
)


def transcode(
    source: Path, output: Path, target: str, encoding: EncodingParameters
) -> TranscodeResult:
    facts = probe_media(source)
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    return run_transcode(
        source,
        output,
        target,
        facts,
        verdict,
        profile=CHROME_149,
        thresholds=DEFAULT_THRESHOLDS,
        encoding=encoding,
    )


@requires_svtav1
def test_variable_frame_rate_source_reencodes_to_constant_rate(
    variable_frame_rate_mp4: Path, tmp_path: Path
) -> None:
    # Guard the fixture: if generation ever stops producing VFR, fail loudly
    # rather than pass a vacuous no-op.
    assert probe_media(variable_frame_rate_mp4).constant_frame_rate is False
    result = transcode(
        variable_frame_rate_mp4, tmp_path / "out.mp4", "analysis", ANALYSIS_ENCODING
    )
    assert result.performed
    assert result.operation is Operation.REENCODE_AV1
    assert result.output_path is not None
    assert result.output_path.exists()
    assert result.output_facts is not None
    assert result.output_facts.constant_frame_rate is True
    assert result.output_verdict is not None
    assert "variable_frame_rate" not in result.output_verdict.analysis_reasons
    assert result.output_verdict.analysis_transcode is None


def test_tail_moov_source_remuxes_to_faststart(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    source = clips["cfr_mp4"]
    assert probe_media(source).moov_at_start is False
    result = transcode(source, tmp_path / "out.mp4", "playback", PLAYBACK_ENCODING)
    assert result.performed
    assert result.operation is Operation.REMUX_FASTSTART
    assert result.output_facts is not None
    assert result.output_facts.moov_at_start is True
    assert result.output_verdict is not None
    assert result.output_verdict.stream_transcode is None
    assert result.residual_recommended is False


@requires_svtav1
def test_rotated_source_bakes_rotation_in_an_av1_reencode(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    source = clips["rotated_mp4"]
    assert probe_media(source).rotation_degrees == 90
    result = transcode(source, tmp_path / "out.mp4", "playback", PLAYBACK_ENCODING)
    assert result.performed
    assert result.operation is Operation.REENCODE_AV1
    assert result.output_facts is not None
    assert result.output_facts.rotation_degrees == 0
    assert result.output_verdict is not None
    assert result.output_verdict.stream_transcode is None
    assert result.residual_recommended is False


def test_a_clean_playback_source_is_a_no_op(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    destination = tmp_path / "out.mp4"
    result = transcode(clips["faststart_mp4"], destination, "playback", PLAYBACK_ENCODING)
    assert result.performed is False
    assert result.output_path is None
    assert not destination.exists()


def test_a_clean_analysis_source_is_a_no_op(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    destination = tmp_path / "out.mp4"
    result = transcode(clips["faststart_mp4"], destination, "analysis", ANALYSIS_ENCODING)
    assert result.performed is False
    assert result.output_path is None
    assert not destination.exists()


def test_output_directory_derives_a_filename_from_the_source_stem(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    source = clips["cfr_mp4"]
    result = transcode(source, tmp_path, "playback", PLAYBACK_ENCODING)
    assert result.performed
    assert result.output_path is not None
    assert result.output_path == (tmp_path / f"{source.stem}.mp4").absolute()
    assert result.output_path.exists()


def test_an_explicit_file_output_is_written_verbatim(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    source = clips["cfr_mp4"]
    destination = tmp_path / "chosen_name.mp4"
    result = transcode(source, destination, "playback", PLAYBACK_ENCODING)
    assert result.output_path == destination.absolute()
    assert destination.exists()


def test_it_refuses_to_overwrite_the_source(clips: dict[str, Path]) -> None:
    source = clips["cfr_mp4"]
    with pytest.raises(TranscodeError, match="refusing to overwrite"):
        _ = transcode(source, source, "playback", PLAYBACK_ENCODING)


def test_lying_header_source_remuxes_with_a_corrected_timebase(
    lying_header_mkv: Path, tmp_path: Path
) -> None:
    # Guard: the fixture must actually trip the timing-metadata lie, or the test
    # proves nothing about the remux.
    source_verdict = derive(
        probe_media(lying_header_mkv), CHROME_149, DEFAULT_THRESHOLDS
    )
    assert "unreliable_timing_metadata" in source_verdict.analysis_reasons
    result = transcode(
        lying_header_mkv, tmp_path / "out.mp4", "analysis", ANALYSIS_ENCODING
    )
    assert result.performed
    assert result.operation is Operation.REMUX_TIMEBASE
    assert result.output_verdict is not None
    assert "unreliable_timing_metadata" not in result.output_verdict.analysis_reasons
    assert result.output_verdict.analysis_transcode is None


def test_h264_in_avi_rewraps_into_a_supported_container(
    h264_in_avi: Path, tmp_path: Path
) -> None:
    # Guard: h264-in-avi must fire exactly the container reason.
    source_verdict = derive(probe_media(h264_in_avi), CHROME_149, DEFAULT_THRESHOLDS)
    assert "unsupported_container" in source_verdict.stream_reasons
    result = transcode(h264_in_avi, tmp_path / "out.mp4", "playback", PLAYBACK_ENCODING)
    assert result.performed
    assert result.operation is Operation.REMUX_CONTAINER
    assert result.output_facts is not None
    assert result.output_facts.container == "mov,mp4,m4a,3gp,3g2,mj2"
    assert result.output_facts.codec_name == "h264"
    assert result.output_verdict is not None
    assert result.output_verdict.stream_transcode is None


def test_a_still_red_playback_output_is_a_terminal_failure(
    clips: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force the output re-probe to report an unplayable verdict; the gate must
    # raise and leave no output behind (a retry is never the answer).
    def still_required(
        _facts: MediaFacts, _profile: object, _thresholds: object
    ) -> Verdict:
        return Verdict(
            playable=False,
            stream_transcode="required",
            analysis_transcode=None,
            stream_reasons=frozenset({"unsupported_codec"}),
            analysis_reasons=frozenset(),
            truncated=False,
        )

    monkeypatch.setattr(convert_module, "derive", still_required)
    source = clips["cfr_mp4"]
    facts = probe_media(source)
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    destination = tmp_path / "out.mp4"
    with pytest.raises(TranscodeError, match="still cannot play"):
        _ = run_transcode(
            source,
            destination,
            "playback",
            facts,
            verdict,
            profile=CHROME_149,
            thresholds=DEFAULT_THRESHOLDS,
            encoding=PLAYBACK_ENCODING,
        )
    assert not destination.exists()


def test_a_still_red_analysis_output_is_a_terminal_failure(
    lying_header_mkv: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def still_required(
        _facts: MediaFacts, _profile: object, _thresholds: object
    ) -> Verdict:
        return Verdict(
            playable=True,
            stream_transcode=None,
            analysis_transcode="required",
            stream_reasons=frozenset(),
            analysis_reasons=frozenset({"variable_frame_rate"}),
            truncated=False,
        )

    monkeypatch.setattr(convert_module, "derive", still_required)
    facts = probe_media(lying_header_mkv)
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    with pytest.raises(TranscodeError, match="still needs an analysis transcode"):
        _ = run_transcode(
            lying_header_mkv,
            tmp_path / "out.mp4",
            "analysis",
            facts,
            verdict,
            profile=CHROME_149,
            thresholds=DEFAULT_THRESHOLDS,
            encoding=ANALYSIS_ENCODING,
        )


def test_a_residual_recommended_playback_output_is_surfaced_not_failed(
    clips: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Output that plays but still trips a soft reason is not a failure; the result
    # records it so a caller can report the transcode was optional.
    def still_recommended(
        _facts: MediaFacts, _profile: object, _thresholds: object
    ) -> Verdict:
        return Verdict(
            playable=True,
            stream_transcode="recommended",
            analysis_transcode=None,
            stream_reasons=frozenset({"moov_not_at_start"}),
            analysis_reasons=frozenset(),
            truncated=False,
        )

    monkeypatch.setattr(convert_module, "derive", still_recommended)
    source = clips["cfr_mp4"]
    facts = probe_media(source)
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    result = run_transcode(
        source,
        tmp_path / "out.mp4",
        "playback",
        facts,
        verdict,
        profile=CHROME_149,
        thresholds=DEFAULT_THRESHOLDS,
        encoding=PLAYBACK_ENCODING,
    )
    assert result.performed
    assert result.residual_recommended is True
    assert result.output_path is not None
    assert result.output_path.exists()
```

- [ ] Run and confirm it fails on the missing exports:

```
uv run pytest tests/transcode/test_convert.py
```

Expected: collection error `ImportError: cannot import name 'run_transcode' from 'mosaic_media.transcode'`.

- [ ] Implement `src/mosaic_media/transcode/convert.py`:

```python
"""Run a TranscodeCommand and prove the output clean.

Writes to a temporary file beside the destination and renames atomically only
after the output re-probes clean for the requested target. The re-probe is the
acceptance test: a variable-rate source resampled to a constant rate can still
carry residual drift, so the verdict runs on the transcoded bytes, not just on
the source. Cleanliness is judged against the whole target, not only the reasons
the command set out to fix: the analysis output must need no analysis transcode,
and the playback output must not be unplayable. A transcode that clears its
target reason but introduces a new one therefore still fails. A playback output
that plays but still carries a soft reason is not a failure; it is reported to
the caller. That output probe also mints the derivative's authoritative
MediaFacts, measured once here because consumers never re-measure.

A red verdict on the transcoded output is a terminal failure, not a transient
one. The converter raises and stops; a caller must not respond by scheduling
another transcode, because re-running the same deterministic command on the
same input yields the same red output and a retry would loop. Retries are
reserved for transient faults such as a killed subprocess or a full disk.
Confidence that a command produces clean output for a class of defect comes
from the corpus acceptance tests exercised during development, not from runtime
retrying.

Output destinations are caller-owned. The resolved output is never allowed to
equal the source: the original is preserved in every case, so the converter
refuses to write over the input and raises instead. Given a directory, the
output filename is derived from the source stem with an .mp4 container; given a
file path, that path is used verbatim. This package knows nothing of any
dataset directory layout. Re-running a transcode to an existing output path
replaces it atomically.
"""

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..probe.facts import MediaFacts
from ..probe.policy import PlaybackProfile, Thresholds
from ..probe.probe import probe_media
from ..probe.verdict import Verdict, derive

from .commands import EncodingParameters, Operation, Target, build_command

DEFAULT_TRANSCODE_TIMEOUT_SECONDS = 3600.0


class TranscodeError(RuntimeError):
    """A transcode failed to run, or its output was not clean for the target.

    Terminal: a caller must not respond by scheduling another transcode.
    """


@dataclass(frozen=True, slots=True)
class TranscodeResult:
    performed: bool
    operation: Operation | None
    output_path: Path | None
    output_facts: MediaFacts | None
    output_verdict: Verdict | None
    reasons_addressed: frozenset[str]
    # Playback only: the output plays but still carries a soft reason. Not a
    # failure; surfaced so a caller can report the transcode was optional.
    residual_recommended: bool


def _run_ffmpeg(argv: tuple[str, ...], source: Path, timeout: float) -> None:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        message = f"ffmpeg binary not found on PATH: {exc}"
        raise TranscodeError(message) from exc
    except subprocess.TimeoutExpired as exc:
        message = f"ffmpeg timed out after {timeout:g}s transcoding {source}"
        raise TranscodeError(message) from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown error"
        message = f"ffmpeg failed transcoding {source}: {detail}"
        raise TranscodeError(message)


def _resolve_output(source: Path, output: Path) -> Path:
    """Resolve `output` to a concrete destination and refuse to overwrite `source`.

    An existing directory means "write the derivative here under the source stem
    with an .mp4 container"; anything else is used as the file path verbatim. A
    resolved destination equal to the source raises, because originals are always
    preserved.
    """
    destination = output / f"{source.stem}.mp4" if output.is_dir() else output
    destination = destination.absolute()
    if destination.resolve() == source.resolve():
        message = (
            f"refusing to overwrite the source {source}: "
            "choose an --output path other than the input file"
        )
        raise TranscodeError(message)
    return destination


def run_transcode(
    source: Path,
    output: Path,
    target: Target,
    facts: MediaFacts,
    verdict: Verdict,
    *,
    profile: PlaybackProfile,
    thresholds: Thresholds,
    encoding: EncodingParameters,
    allow_hardware: bool = False,
    timeout: float = DEFAULT_TRANSCODE_TIMEOUT_SECONDS,
) -> TranscodeResult:
    """Transcode `source` to `output` for `target`, or report a no-op when the
    source is already clean for that target.

    `output` is a file path, or an existing directory the derivative is written
    into under the source stem with an .mp4 container. The resolved destination may
    never equal the source: originals are preserved, so a resolved destination equal
    to `source` raises rather than overwriting it. Re-running to an existing output
    replaces it atomically.

    `facts` and `verdict` are the authoritative measurement of `source`; this
    function never re-measures the source (measurement is not re-derived), only the
    output. On success the output is renamed into place atomically. The acceptance
    gate is terminal and requires the output fully clean for the target: analysis
    output must need no analysis transcode, and playback output must not be
    unplayable. A playback output that still carries a soft reason is not a failure
    but is reported through `TranscodeResult.residual_recommended`. Raises
    `TranscodeError` when ffmpeg fails, when the resolved destination equals the
    source, or when the output is not clean for the target.
    """
    destination = _resolve_output(source, output)
    command = build_command(
        verdict,
        facts,
        target,
        source.absolute(),
        destination,
        encoding=encoding,
        allow_hardware=allow_hardware,
    )
    if command is None:
        return TranscodeResult(
            performed=False,
            operation=None,
            output_path=None,
            output_facts=None,
            output_verdict=None,
            reasons_addressed=frozenset(),
            residual_recommended=False,
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=destination.parent,
        prefix=f".{destination.stem}.",
        suffix=".mp4",
        delete=False,
    )
    temporary = Path(handle.name)
    handle.close()
    argv = (*command.argv[:-1], str(temporary))
    try:
        _run_ffmpeg(argv, source, timeout)
        output_facts = probe_media(temporary, thresholds)
        output_verdict = derive(output_facts, profile, thresholds)
        # Terminal acceptance gate: the output must be fully clean for the target,
        # not merely free of the reasons this command set out to fix. A transcode
        # that clears its target reason but introduces a new one still fails here.
        residual_recommended = False
        if target == "analysis":
            if output_verdict.analysis_transcode is not None:
                listing = ", ".join(sorted(output_verdict.analysis_reasons))
                message = (
                    f"transcode of {source} produced output that still needs an "
                    f"analysis transcode ({listing})"
                )
                raise TranscodeError(message)
        else:
            if output_verdict.stream_transcode == "required":
                listing = ", ".join(sorted(output_verdict.stream_reasons))
                message = (
                    f"transcode of {source} produced output that still cannot play "
                    f"in the browser ({listing})"
                )
                raise TranscodeError(message)
            # A residual soft reason is not a failure, but the caller must see it.
            residual_recommended = output_verdict.stream_transcode == "recommended"
        _ = temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return TranscodeResult(
        performed=True,
        operation=command.operation,
        output_path=destination,
        output_facts=output_facts,
        output_verdict=output_verdict,
        reasons_addressed=command.reasons,
        residual_recommended=residual_recommended,
    )
```

- [ ] Fill `src/mosaic_media/transcode/__init__.py` with the public exports:

```python
"""Verdict to ffmpeg execution: command construction and the re-probe-verified runner."""

from .commands import (
    ANALYSIS_ENCODING,
    PLAYBACK_ENCODING,
    EncodingParameters,
    Operation,
    Target,
    TranscodeCommand,
    build_command,
)
from .convert import (
    DEFAULT_TRANSCODE_TIMEOUT_SECONDS,
    TranscodeError,
    TranscodeResult,
    run_transcode,
)

__all__ = [
    "ANALYSIS_ENCODING",
    "DEFAULT_TRANSCODE_TIMEOUT_SECONDS",
    "EncodingParameters",
    "Operation",
    "PLAYBACK_ENCODING",
    "Target",
    "TranscodeCommand",
    "TranscodeError",
    "TranscodeResult",
    "build_command",
    "run_transcode",
]
```

- [ ] Run and confirm it passes (libsvtav1 is present on this machine, so no skips):

```
uv run pytest tests/transcode/test_convert.py
```

Expected: `13 passed`. On a machine without libsvtav1: `11 passed, 2 skipped`.

- [ ] Commit: `Add transcode converter with re-probe acceptance test`

---

## Task 3 -- cli/: the typer app and the console script

**Files:**
- `src/mosaic_media/cli/__init__.py` (new)
- `pyproject.toml` (add `[project.scripts]`)
- `tests/cli/__init__.py` (new, empty)
- `tests/cli/test_cli.py` (new)

**Interfaces:**
- Consumes: `probe_media`, `derive`, `CHROME_149`, `DEFAULT_THRESHOLDS`, `PlaybackProfile`,
  `MediaProbeError` (plan 1); `run_transcode`, `TranscodeError`, `Target` (imported as
  `TranscodeTarget`), `ANALYSIS_ENCODING`, `PLAYBACK_ENCODING` (Task 2); `clips` fixture (plan-1
  test scaffolding); typer.
- Produces:
  - Module-level `app: typer.Typer`, importable as `mosaic_media.cli:app` for the console
    script, and aliased `media_app = app` for `mosaic`'s `app.add_typer(media_app, name="media")`.
  - Command `probe(file: Path)` -- prints `{"facts": ..., "verdict": ...}` as JSON on stdout;
    exits 1 with a stderr message on probe failure.
  - Command `transcode(file, --target analysis|playback, --output PATH, --profile chrome-149,
    --allow-hardware/--no-hardware)` -- `--output` is REQUIRED and is a file path or an existing
    directory (filename derived from the source stem); the package holds no dataset-layout
    knowledge and never writes over the source. Honors the opt-out semantics; exits 0 on success
    or a reported no-op, 1 with a stderr message on probe or transcode failure, 2 on a missing
    required option.

**Opt-out semantics:** analysis has no opt-out (run whenever `analysis_transcode` is set);
playback runs on any stream reason -- for a hard reason (`stream_transcode == "required"`) it
is mandatory, for a soft reason (`stream_transcode == "recommended"`) it proceeds but prints
that the transcode was optional. A file already clean for the target is reported as a no-op.

**Hardware encoding is opt-in (spec-conformant).** The spec selects av1_nvenc "when the caller
permits hardware AND hwaccel detects support," so the CLI keeps `--no-hardware` as the default
and requires `--allow-hardware` to permit it. Permission is a separate gate from detection on
purpose: `hwaccel.encoder_available("av1_nvenc")` inspects `ffmpeg -encoders`, which lists the
encoder whenever ffmpeg was *built* with NVENC -- it does not prove a working NVIDIA GPU is
present, and no frozen hwaccel function (`encoder_available`, `nvdec_available`,
`ffmpeg_available`) does. The package's stated deployment target includes ffmpeg-only and
CPU-only machines (minimal containers, tracking boxes) whose distro ffmpeg commonly carries
NVENC support with no GPU behind it; permitting hardware by default would make every transcode
there fail at runtime. Once permitted, `build_command` selects av1_nvenc iff `allow_hardware and
encoder_available("av1_nvenc")` -- exactly the spec's "caller permits AND hwaccel detects." The
library API (`build_command`, `run_transcode`) likewise defaults `allow_hardware=False`.

### Steps

- [ ] Install the cli extra first, so typer is importable for every step that follows. The RED
  run below must fail on the missing `mosaic_media.cli` module, not on a missing typer:

```
uv sync --extra cli
```

- [ ] Create `tests/cli/__init__.py` as an empty file.

- [ ] Write the failing test file `tests/cli/test_cli.py`:

```python
"""CLI smoke tests via typer's CliRunner. Error messages are matched against the
combined output, which is version-robust across click's stderr handling."""

import json
from pathlib import Path

from click.testing import Result
from typer.testing import CliRunner

from mosaic_media.cli import app

runner = CliRunner()


def combined(result: Result) -> str:
    """stdout and stderr together, tolerant of click's version differences.

    click 8.2 removed `mix_stderr`, so `Result.output` is stdout-only and error
    text lands on `Result.stderr`; older click merges the streams and raises on
    `Result.stderr`. Reading both defensively matches error text on either.
    """
    text = result.output
    try:
        stderr = result.stderr
    except ValueError:
        stderr = ""
    return text + (stderr or "")


def test_probe_prints_parseable_json_with_expected_keys(clips: dict[str, Path]) -> None:
    result = runner.invoke(app, ["probe", str(clips["cfr_mp4"])])
    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert set(document) == {"facts", "verdict"}
    assert document["facts"]["codec_name"] == "h264"
    assert document["facts"]["constant_frame_rate"] is True
    assert isinstance(document["verdict"]["stream_reasons"], list)
    assert isinstance(document["verdict"]["analysis_reasons"], list)


def test_probe_of_a_missing_file_exits_nonzero_with_a_message() -> None:
    result = runner.invoke(app, ["probe", "/nonexistent/definitely_missing.mp4"])
    assert result.exit_code == 1
    assert "probe failed" in combined(result)


def test_transcode_of_a_clean_file_reports_a_no_op(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    destination = tmp_path / "out.mp4"
    result = runner.invoke(
        app,
        [
            "transcode",
            str(clips["faststart_mp4"]),
            "--target",
            "playback",
            "--output",
            str(destination),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "nothing to do" in result.output
    assert not destination.exists()


def test_transcode_of_a_missing_file_exits_nonzero(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "transcode",
            "/nonexistent/missing.mp4",
            "--target",
            "analysis",
            "--output",
            str(tmp_path / "out.mp4"),
        ],
    )
    assert result.exit_code == 1
    assert "probe failed" in combined(result)


def test_transcode_requires_an_output_option(clips: dict[str, Path]) -> None:
    result = runner.invoke(
        app, ["transcode", str(clips["faststart_mp4"]), "--target", "playback"]
    )
    assert result.exit_code == 2
    assert "--output" in combined(result)
```

- [ ] Run and confirm it fails on the missing module:

```
uv run pytest tests/cli/test_cli.py
```

Expected: collection error `ModuleNotFoundError: No module named 'mosaic_media.cli'`.

- [ ] Implement `src/mosaic_media/cli/__init__.py`:

```python
"""The `mosaic-media` command line app. The only module that imports typer.

Two commands mirror the library: `probe` prints MediaFacts and both verdicts as
JSON; `transcode` runs the minimum operation for a target and honors the opt-out
semantics. Job infrastructure calls the Python API directly, never this CLI --
structured exceptions, no argv escaping, no output parsing. `mosaic` mounts this
app with `app.add_typer(media_app, name="media")`.
"""

import dataclasses
import enum
import json
from pathlib import Path

import typer

from ..probe.errors import MediaProbeError
from ..probe.policy import CHROME_149, DEFAULT_THRESHOLDS, PlaybackProfile
from ..probe.probe import probe_media
from ..probe.verdict import derive
from ..transcode import (
    ANALYSIS_ENCODING,
    PLAYBACK_ENCODING,
    Target as TranscodeTarget,
    TranscodeError,
    run_transcode,
)

app = typer.Typer(
    name="mosaic-media",
    help="Probe a video and run the minimum ffmpeg transcode its verdict calls for.",
    no_args_is_help=True,
    add_completion=False,
)
# Alias for mounting into the mosaic CLI: app.add_typer(media_app, name="media").
media_app = app


class Target(str, enum.Enum):
    analysis = "analysis"
    playback = "playback"


class Profile(str, enum.Enum):
    chrome_149 = "chrome-149"


_PROFILES: dict[Profile, PlaybackProfile] = {Profile.chrome_149: CHROME_149}


def _json_default(value: object) -> object:
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, enum.Enum):
        return value.value
    message = f"cannot serialize {type(value).__name__} to JSON"
    raise TypeError(message)


@app.command()
def probe(file: Path) -> None:
    """Probe FILE and print its MediaFacts and both verdicts as JSON on stdout."""
    try:
        facts = probe_media(file)
    except MediaProbeError as exc:
        message = f"probe failed: {exc}"
        typer.echo(message, err=True)
        raise typer.Exit(code=1) from exc
    verdict = derive(facts, CHROME_149, DEFAULT_THRESHOLDS)
    document = {
        "facts": dataclasses.asdict(facts),
        "verdict": dataclasses.asdict(verdict),
    }
    typer.echo(json.dumps(document, default=_json_default, indent=2, sort_keys=True))


@app.command()
def transcode(
    file: Path,
    target: Target = typer.Option(
        ..., "--target", help="Which derivative to produce: analysis or playback."
    ),
    output: Path = typer.Option(
        ...,
        "--output",
        help=(
            "Output file path, or an existing directory the derivative is written "
            "into under the source stem. Required: the CLI knows no dataset layout."
        ),
    ),
    profile: Profile = typer.Option(
        Profile.chrome_149, "--profile", help="Playback policy profile."
    ),
    allow_hardware: bool = typer.Option(
        False,
        "--allow-hardware/--no-hardware",
        help=(
            "Permit av1_nvenc hardware encoding; used only when the system ffmpeg "
            "offers it. Off by default (permitting enables it only when detected)."
        ),
    ),
) -> None:
    """Transcode FILE for the analysis or playback target, running the minimum operation."""
    try:
        facts = probe_media(file)
    except MediaProbeError as exc:
        message = f"probe failed: {exc}"
        typer.echo(message, err=True)
        raise typer.Exit(code=1) from exc

    playback_profile = _PROFILES[profile]
    verdict = derive(facts, playback_profile, DEFAULT_THRESHOLDS)

    optional = False
    if target is Target.analysis:
        if verdict.analysis_transcode is None:
            typer.echo(f"{file} is already analysis-clean; nothing to do.")
            return
        encoding = ANALYSIS_ENCODING
    else:
        if verdict.stream_transcode is None:
            typer.echo(f"{file} already plays well; nothing to do.")
            return
        optional = verdict.stream_transcode == "recommended"
        encoding = PLAYBACK_ENCODING

    target_literal: TranscodeTarget = (
        "analysis" if target is Target.analysis else "playback"
    )
    try:
        result = run_transcode(
            file,
            output,
            target_literal,
            facts,
            verdict,
            profile=playback_profile,
            thresholds=DEFAULT_THRESHOLDS,
            encoding=encoding,
            allow_hardware=allow_hardware,
        )
    except (TranscodeError, MediaProbeError) as exc:
        message = f"transcode failed: {exc}"
        typer.echo(message, err=True)
        raise typer.Exit(code=1) from exc

    if not result.performed or result.output_path is None:
        typer.echo(f"{file} is already clean for the {target.value} target; nothing to do.")
        return
    if optional:
        note = (
            f"note: the {target.value} transcode was optional "
            "(a soft playback reason, not a hard one)."
        )
        typer.echo(note)
    operation_name = "" if result.operation is None else result.operation.value
    typer.echo(f"wrote {result.output_path} ({operation_name}).")


__all__ = ["app", "media_app"]
```

- [ ] Add the console script to `pyproject.toml` by inserting this block immediately after the
  `[project.optional-dependencies]` table (before `[dependency-groups]`):

```toml
[project.scripts]
mosaic-media = "mosaic_media.cli:app"
```

- [ ] Run and confirm the CLI tests pass:

```
uv run pytest tests/cli/test_cli.py
```

Expected: `5 passed`.

- [ ] Confirm the console script resolves (re-sync so the entry point installs):

```
uv sync --extra cli
uv run mosaic-media --help
```

Expected: exit 0, help text listing the `probe` and `transcode` commands.

- [ ] Commit: `Add mosaic-media probe and transcode CLI`

---

## Task 4 -- extend the layering guards for transcode and cli

**Files:**
- `tests/test_import_guard.py` (delivered by plan 1; extended here)
- `tests/probe/test_purity.py` (delivered by plan 1; extended here)

**Interfaces:** none (test-only). Consumes plan 1's guards:
- `tests/test_import_guard.py` runs each core-module import in a fresh subprocess through
  `_run_guarded(body: str, *, forbidden_root: str) -> subprocess.CompletedProcess[str]` -- a
  `sys.meta_path` finder that raises `AssertionError` on any import of `forbidden_root`. The
  core-import check is a single program body of bare `import` statements, run once per poisoned
  root (`numpy`, `typer`, `cv2`); there is no module list to append to, only that body.
- `tests/probe/test_purity.py` scans standard-library-only source targets listed in
  `STDLIB_ONLY_TARGETS`, a tuple of `Path`s rooted at `CORE_ROOT`.

The reader plan appends its own guard test to `tests/test_import_guard.py` through the same
`_run_guarded` helper, so an end-of-file merge on that file is expected at integration.

The transcode subpackage is new core (standard-library-only, plus `mosaic_media.probe` and
`mosaic_media.hwaccel`) and must be covered by both guards; the cli layer is the only place
`typer` may appear and needs its own clean-failure guard.

### Steps

- [ ] In `tests/test_import_guard.py`, extend the shared core-import program body -- the `body`
  string of bare `import` statements that `_run_guarded` runs under each poisoned root -- with
  the three transcode modules, so they are exercised under numpy, typer, and cv2 poisoning:

```python
import mosaic_media.transcode
import mosaic_media.transcode.commands
import mosaic_media.transcode.convert
```

- [ ] In `tests/test_import_guard.py`, add a cli-fails-cleanly-without-typer guard built on the
  same subprocess helper, mirroring the reader plan's io-without-numpy guard. Append:

```python
def test_the_cli_needs_typer_and_the_core_does_not() -> None:
    # In a fresh subprocess with typer poisoned, a core module still imports but
    # the cli layer does not -- typer is confined to cli. Running in a subprocess
    # (not in-process) is what makes this real: an in-process import would find the
    # modules already cached in sys.modules and prove nothing.
    core = _run_guarded(
        "import mosaic_media.transcode.commands", forbidden_root="typer"
    )
    assert core.returncode == 0, core.stderr
    cli = _run_guarded("import mosaic_media.cli", forbidden_root="typer")
    assert cli.returncode != 0
    assert "typer" in cli.stderr
```

- [ ] In `tests/probe/test_purity.py`, add the transcode package to the source-level
  standard-library-only scan by extending `STDLIB_ONLY_TARGETS` with `CORE_ROOT / "transcode"`.

- [ ] Run both guard files and confirm the new coverage passes:

```
uv run pytest tests/test_import_guard.py tests/probe/test_purity.py
```

Expected: all guard tests pass, including `test_the_cli_needs_typer_and_the_core_does_not`, the
three transcode modules imported under each poisoned root, and the transcode source tree scanned
standard-library-only.

- [ ] Commit: `Cover transcode and cli under the package layering guards`

---

## Task 5 -- README: correct the CLI surface and pin the transcode invariants

**Files:**
- `README.md`

**Interfaces:** none (documentation only).

`README.md` is the package's user-facing document; three transcode invariants must be pinned in
its prose so spec, README, and implementation never drift. The already-pinned rule that job
infrastructure calls the Python API directly (never the CLI) stays exactly as written.

### Steps

- [ ] In the "CLI composition" section, correct the example command. Change:

```bash
mosaic media transcode video.mp4 --target streaming
```

to:

```bash
mosaic media transcode video.mp4 --target playback --output media/
```

- [ ] In the "CLI composition" section, immediately after that command example (and before the
  "The dependency runs `mosaic -> mosaic-media`, one way, no cycle." line), insert the following
  paragraph as plain running prose (not a code block, not a blockquote). It pins caller-owned
  output destinations:

```text
`--output` is required and takes a file path or a directory. Given a directory,
the derivative's filename is derived from the source stem with an `.mp4`
container; given a file path, that path is used as-is. The resolved output may
never equal the source -- the original upload is preserved in every case, so the
converter refuses to write over it. The package holds no knowledge of any
dataset directory layout: a convention like keeping originals in `media_raw/`
and transcodes in `media/` belongs to the caller, exactly like browser policy.
Re-running the same transcode replaces its output atomically.
```

- [ ] In the "Transcode semantics" section, immediately after the existing paragraph that ends
  "...so the verdict runs on both sides of the transcode.", insert the following paragraph as
  plain running prose (not a code block, not a blockquote). It pins the terminal
  acceptance-failure semantics:

```text
A red verdict on the transcoded output is a terminal failure. The converter
raises and the job is marked failed for a human to see; nothing in the stack
ever responds to acceptance failure by scheduling another transcode. Re-running
the same deterministic command on the same input would only reproduce the same
red output, so a retry could loop. Retries are reserved for transient faults (a
killed subprocess, a full disk). Confidence that a command produces clean output
is established before any job runs, by the development-time corpus acceptance
tests.
```

- [ ] Leave the pinned rule "**The job infrastructure calls the Python API directly, not the
  CLI.**" and its surrounding paragraph exactly as they are.

- [ ] Confirm the corrected surface and both pins are present and consistent:

```
grep -n "target streaming" README.md
grep -n "terminal failure" README.md
grep -n "never equal the source" README.md
```

Expected: the first grep prints nothing (exit 1); the second and third each print one line.

- [ ] Commit: `Correct README CLI example and pin transcode output and failure invariants`

---

## Task 6 -- full verification

**Files:** none (verification only).

### Steps

- [ ] Format, then confirm the formatter reports nothing to change:

```
uv run ruff format src/ tests/
```

Expected: `... files left unchanged` (nothing reformatted after the implementation is complete).

- [ ] Lint:

```
uv run ruff check src/ tests/
```

Expected: `All checks passed!`.

- [ ] Type-check (scope includes `tests/`):

```
uv run basedpyright src/ tests/
```

Expected: `0 errors, 0 warnings, 0 notes`.

- [ ] Run the full suite through `heavy` (serialized, foreground):

```
heavy uv run pytest
```

Expected: all tests pass, ending with `heavy-task: exit-status=0`. The transcode and CLI tests
(`13 + 13 + 5`) pass alongside the copied probe suite and the extended layering guards; no
`bench`-marked tests run by default.

- [ ] Smoke-run the console script against a freshly generated clip:

```
smoke_dir="$(mktemp -d)"
ffmpeg -hide_banner -v error -y -f lavfi -i testsrc2=size=320x240:rate=25:duration=2 -c:v libx264 -pix_fmt yuv420p "${smoke_dir}/smoke.mp4"
uv run mosaic-media probe "${smoke_dir}/smoke.mp4"
```

Expected: exit 0; JSON on stdout with top-level keys `facts` and `verdict`,
`facts.codec_name == "h264"`, `facts.constant_frame_rate == true`, and empty
`verdict.stream_reasons` / `verdict.analysis_reasons` lists.

- [ ] If all green, the branch is ready. No commit for this task (verification only).

---

## Coverage self-review

- Spec reason table fully mapped: `unreliable_timing_metadata` -> `-c copy` remux with
  regenerated timestamps; `moov_not_at_start` -> `-movflags +faststart`; `variable_frame_rate` /
  `rotated` / `non_square_pixels` / `interlaced` -> AV1 re-encode with CFR output, baked rotation,
  square pixels, deinterlace. Every remaining real StreamReason is routed explicitly in the
  routing table (container remux for `unsupported_container`; re-encode for `unsupported_codec`,
  `non_zero_start_time`, `client_dependent_decode`, `large_seek_payload`, `sparse_keyframes`).
  All four operation kinds have a real-file corpus acceptance test in Task 2 (VFR re-encode,
  tail-moov faststart, rotated re-encode, lying-header timebase remux, h264-in-avi container
  remux), not only the argv-level unit tests in Task 1.
- Encoder selection: libsvtav1 CPU default, av1_nvenc when `allow_hardware` and
  `hwaccel.encoder_available("av1_nvenc")` (Task 1 + hardware tests). Spec-conformant: the spec
  selects av1_nvenc "when the caller permits hardware AND hwaccel detects support," so hardware is
  opt-in (`--no-hardware` default) because `encoder_available` proves the encoder is compiled in,
  not that a usable GPU exists, and the package targets ffmpeg-only/CPU-only machines (Task 3
  rationale).
- Policy injected: `PlaybackProfile`, `Thresholds`, `EncodingParameters` are all parameters;
  the only shipped constants are media-domain encoder defaults and the existing `CHROME_149`.
- Re-probe acceptance is a terminal full-target gate: `run_transcode` re-probes the output and
  requires it fully clean for the target (analysis: `analysis_transcode` None; playback:
  `stream_transcode` not `"required"`), so a transcode that introduces a new defect fails; a
  residual soft playback reason is surfaced via `residual_recommended`, not raised (Task 2 + gate
  tests for still-red analysis, still-red playback, and residual-recommended playback).
- Opt-out semantics: analysis no opt-out; playback mandatory on hard, optional-but-proceeds on
  soft; clean file reported as a no-op (Task 3 CLI + no-op tests).
- JSON probe output via `dataclasses.asdict` with frozensets to sorted lists and enums to
  values (Task 3 + JSON parse test).
- Console script `mosaic-media = "mosaic_media.cli:app"` (Task 3 pyproject).
- Output destinations caller-owned: `run_transcode` resolves a file-or-directory `--output`
  (`_resolve_output`), derives the filename from the source stem for a directory, and refuses a
  resolved destination equal to the source (Task 2 helper + directory/explicit-file/overwrite
  tests; Task 3 required `--output` + missing-option test).
- Terminal acceptance-failure: a red output verdict raises `TranscodeError` and never triggers a
  reschedule; the rationale is pinned in the `convert.py` module docstring (Task 2) and in the
  README (Task 5).
- README corrected to the real `--target playback --output` surface and pins both the
  terminal-failure and caller-owned-output invariants; the Python-API-not-CLI rule is preserved
  untouched (Task 5).
- Layering: `transcode/` and `cli/` use package-relative imports (matching plan 1's core
  convention); Task 4 extends the subprocess import guard's core-import body with the three
  transcode modules (run under numpy/typer/cv2 poisoning), adds a cli-needs-typer guard via the
  same `_run_guarded` helper, and adds `CORE_ROOT / "transcode"` to `test_purity.py`'s
  `STDLIB_ONLY_TARGETS` source scan.
- DRY: `test_commands.py` imports the shared 25-field `CLEAN` from `tests/probe/test_verdict`
  rather than restating it (repo precedent: `tests/probe/test_sequence.py`); the `Target` target
  literal is defined once in `commands.py` and imported by `convert.py` and `cli` (Tasks 1-3).
- CliRunner error-text assertions read stdout and stderr together, tolerant of click 8.2 dropping
  `mix_stderr` (Task 3 `combined` helper).
- No placeholders; every code step is complete; every run step names the command and expected
  output.
