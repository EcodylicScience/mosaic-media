"""Media fixtures, built by the ffmpeg on PATH, copied from `tests/assets/`, or
built with PyAV.

ffmpeg is a documented system dependency, so a missing encoder is a failure,
not a skip. The encoders used here are the ones an LGPL build carries: libvpx,
mjpeg, aac, and the native `-c copy` remuxes.

H.264 and HEVC clips are copied from `tests/assets/` rather than encoded.
Their decoders are native and LGPL, so a committed clip costs nothing to
consume. See `tests/assets/README.md` for the encoder reasoning and
provenance, and `tests/test_encoder_guard.py` for the rule.

Copies land in the tmp root because tests remux and truncate from these clips;
the committed originals are never handed out directly.
"""

import shutil
import subprocess
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path
from typing import Literal

import av
import pytest

SOURCE = ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2"]
ASSETS = Path(__file__).parent.parent / "assets"

AssetName = Literal[
    "anamorphic.mp4",
    "audio.mp4",
    "cfr.mp4",
    "cfr_30fps.mp4",
    "faststart.mp4",
    "h264.avi",
    "h264_gop12.mp4",
    "hevc.mp4",
    "long_gop.mp4",
    "open_gop.mp4",
    "raw.h264",
    "raw_fractional_rate.h264",
]


def asset(name: AssetName, destination: Path) -> Path:
    """Copy a committed clip to `destination`, which the caller may then mutate."""
    source = ASSETS / name
    if not source.is_file():
        message = f"missing committed asset {source}; see tests/assets/README.md"
        raise RuntimeError(message)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copyfile(source, destination)
    return destination


def build(destination: Path, *arguments: str, source: list[str] | None = None) -> Path:
    command = ["ffmpeg", "-hide_banner", "-v", "error", "-y"]
    command.extend(source if source is not None else SOURCE)
    command.extend(arguments)
    command.append(str(destination))
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        message = (
            f"fixture build failed for {destination.name}: {result.stderr.strip()}"
        )
        raise RuntimeError(message)
    return destination


@pytest.fixture(scope="session")
def clips(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("media_probe_clips")
    made: dict[str, Path] = {}
    made["cfr_mp4"] = asset("cfr.mp4", root / "cfr.mp4")
    made["cfr_30fps_mp4"] = asset("cfr_30fps.mp4", root / "cfr_30fps.mp4")
    made["faststart_mp4"] = asset("faststart.mp4", root / "faststart.mp4")
    made["vp8_webm"] = build(root / "vp8.webm", "-c:v", "libvpx", "-pix_fmt", "yuv420p")
    made["mjpeg_avi"] = build(
        root / "mjpeg.avi", "-c:v", "mjpeg", "-pix_fmt", "yuvj420p"
    )
    # Remuxing into AVI drops the presentation timestamps: every packet reports
    # `pts_time=N/A` and only `dts_time` survives. This is the file the DTS
    # fallback exists for. An AVI encoded directly from a filter source keeps its
    # PTS, so it cannot exercise the fallback and must not be used to claim it.
    made["no_pts_avi"] = build(
        root / "no_pts.avi", "-c", "copy", source=["-i", str(made["cfr_mp4"])]
    )
    # Pins the seven-column side-data behavior the packet scan's row guard
    # tolerates: MPEG-TS appends an empty side-data column after data_hash.
    made["mpegts_ts"] = build(
        root / "cfr.ts", "-c", "copy", source=["-i", str(made["cfr_mp4"])]
    )
    made["anamorphic_mp4"] = asset("anamorphic.mp4", root / "anamorphic.mp4")
    # `-c copy` is load-bearing. It writes side_data_list[].rotation and leaves the
    # coded dimensions at 320x240. Re-encoding instead bakes the rotation into the
    # pixels, yielding a 240x320 file with no side data and nothing to detect.
    made["rotated_mp4"] = build(
        root / "rotated.mp4",
        "-c",
        "copy",
        source=["-display_rotation", "90", "-i", str(made["cfr_mp4"])],
    )
    # Committed with its own coded video rather than muxed from cfr.mp4: a copied
    # video track would carry cfr.mp4's identity values, and the identity tests
    # require every fixture with genuinely distinct content to hash distinctly.
    made["audio_mp4"] = asset("audio.mp4", root / "audio.mp4")
    made["no_video"] = build(
        root / "audio_only.m4a",
        "-c:a",
        "aac",
        source=["-f", "lavfi", "-i", "sine=frequency=440:duration=2"],
    )
    # A raw H.264 elementary stream: no container, no packet timestamps. 60
    # frames at a nominal 30 fps; -bf 0 keeps a later -c copy remux free of
    # B-frame reordering trouble, matching how tracking boxes record.
    made["raw_h264"] = asset("raw.h264", root / "raw.h264")
    # The same, at 30000/1001. The fractional rate is what the integer-rate
    # clips do not exercise: it survives the float round trip only if the
    # declared rate is carried exactly.
    made["raw_fractional_rate_h264"] = asset(
        "raw_fractional_rate.h264", root / "raw_fractional_rate.h264"
    )
    return made


@pytest.fixture(scope="session")
def rewrites(
    clips: dict[str, Path], tmp_path_factory: pytest.TempPathFactory
) -> dict[str, Path]:
    """The same coded content rewritten every way the identity values care about.

    `origin` is the reference. `faststart`, `repack`, and `tagged` are
    same-container rewrites that leave packet timestamps byte-identical.
    `matroska` and `roundtrip` change the container, which quantizes timestamps
    to milliseconds; the round trip inherits that quantization rather than
    undoing it. `transport` additionally reframes the elementary stream to
    Annex B, which changes the coded bytes themselves.

    The 30 fps source is load-bearing, not incidental. A 25 fps frame period is
    exactly 40 ms, so Matroska's millisecond timebase requantizes nothing and
    every timestamp survives a repack byte-identical. On a 25 fps origin the
    test that the content digest survives a container change therefore goes
    vacuous -- it cannot fail no matter how broken the implementation is -- and
    the test that the uuid moves on a container change fails against a correct
    implementation, because the uuid genuinely does not move. 30 fps gives a
    33.333 ms period, which does requantize, and both tests then mean what they
    say.
    """
    root = tmp_path_factory.mktemp("rewrites")
    origin = clips["cfr_30fps_mp4"]
    made: dict[str, Path] = {"origin": origin}
    made["faststart"] = build(
        root / "faststart.mp4",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        source=["-i", str(origin)],
    )
    made["repack"] = build(
        root / "repack.mp4", "-c", "copy", source=["-i", str(origin)]
    )
    made["tagged"] = build(
        root / "tagged.mp4",
        "-c",
        "copy",
        "-metadata",
        "title=changed",
        source=["-i", str(origin)],
    )
    made["matroska"] = build(
        root / "repack.mkv", "-c", "copy", source=["-i", str(origin)]
    )
    made["roundtrip"] = build(
        root / "roundtrip.mp4",
        "-c",
        "copy",
        source=["-i", str(made["matroska"])],
    )
    made["transport"] = build(
        root / "repack.ts", "-c", "copy", source=["-i", str(origin)]
    )
    # -itsscale rewrites presentation timestamps without touching a coded byte:
    # the same pictures at a different rate. This is the retime case.
    made["retimed"] = build(
        root / "retimed.mp4",
        "-c",
        "copy",
        source=["-itsscale", "1.25", "-i", str(origin)],
    )
    return made


@pytest.fixture(scope="session")
def truncated_faststart(
    clips: dict[str, Path], tmp_path_factory: pytest.TempPathFactory
) -> Path:
    source = clips["faststart_mp4"]
    destination = tmp_path_factory.mktemp("truncated") / "truncated.mp4"
    payload = source.read_bytes()
    _ = destination.write_bytes(payload[: len(payload) * 55 // 100])
    return destination


@pytest.fixture(scope="session")
def h264_gop12_clip(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """48 frames, 30 fps, keyframe every 12 -- the shape of `corpus_gop12`, but
    H.264 rather than AV1.

    Only the OpenCV parity tests use it. OpenCV's bundled FFmpeg cannot
    software-decode AV1, so it cannot read the AV1 corpus at all; a codec both
    decoders handle is what makes that comparison possible. Committed rather
    than encoded for the reason every H.264 asset here is.
    """
    root = tmp_path_factory.mktemp("h264_gop12")
    yield asset("h264_gop12.mp4", root / "h264_gop12.mp4")


@pytest.fixture(scope="session")
def long_gop_clip(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("long_gop")
    yield asset("long_gop.mp4", root / "long_gop.mp4")


@pytest.fixture(scope="session")
def avi_starting_on_non_keyframes(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Path]:
    """An AVI whose first packets precede its first keyframe.

    A recording cut mid-stream has this shape, and a copy remux of it does not
    round trip: ffmpeg does not copy initial non-keyframes, so the leading
    packets are dropped and the output clock keeps their offset, producing a
    non-zero `start_time` the source never had.

    Built by muxing the committed clip's packets from index 1, which drops its
    leading keyframe and leaves 24 non-keyframes ahead of the keyframe at 25.
    ffmpeg cannot produce this shape: seeking a `-c copy` cut lands on a
    keyframe by construction, which is the very behavior under test.

    Timestamps are renumbered contiguously from zero rather than carried over.
    Carrying them leaves the dropped packet's period as a gap between the first
    two, which the whole-file grid fit measures as genuine drift -- the clip then
    probes variable-rate and takes the re-encode path, testing something other
    than the copy remux it exists for.
    """
    root = tmp_path_factory.mktemp("headless_avi")
    source = asset("h264.avi", root / "h264.avi")
    destination = root / "starts_on_non_keyframes.avi"
    with (
        av.open(str(source)) as input_container,
        av.open(str(destination), mode="w") as output_container,
    ):
        input_stream = input_container.streams.video[0]
        output_stream = output_container.add_stream_from_template(input_stream)
        written = 0
        for position, packet in enumerate(input_container.demux(input_stream)):
            if packet.size == 0 or position == 0:
                continue
            packet.stream = output_stream
            packet.pts = written
            packet.dts = written
            written += 1
            output_container.mux(packet)
    yield destination


def _restamp(
    source: Path, destination: Path, timescale: int, seconds: list[float]
) -> Path:
    """Rewrite a clip's timestamps, placing presentation frame `i` at
    `seconds[i]` on a `1/timescale` tick.

    Placement is by presentation RANK, recovered by sorting the source's own
    presentation timestamps. Every committed clip carries B-frames, so writing
    new timestamps in demultiplex order would shuffle the presentation order and
    produce a file that measures the shuffle rather than the timing under test.
    Decode timestamps are shifted back by the largest amount decode order ever
    runs ahead of presentation order, which keeps them ascending and never above
    their own packet's presentation timestamp -- both of which the mp4 muxer
    rejects outright rather than warning about.
    """
    with av.open(str(source)) as probe_container:
        probe_stream = probe_container.streams.video[0]
        presentations = [
            packet.pts
            for packet in probe_container.demux(probe_stream)
            if packet.size and packet.pts is not None
        ]
    rank = {value: index for index, value in enumerate(sorted(presentations))}
    lead = max(
        seconds[position] - seconds[rank[value]]
        for position, value in enumerate(presentations)
    )
    with (
        av.open(str(source)) as input_container,
        av.open(str(destination), mode="w") as output_container,
    ):
        input_stream = input_container.streams.video[0]
        output_stream = output_container.add_stream_from_template(input_stream)
        output_stream.time_base = Fraction(1, timescale)
        position = 0
        for packet in input_container.demux(input_stream):
            if packet.size == 0 or packet.pts is None:
                continue
            packet.stream = output_stream
            packet.pts = round(seconds[rank[packet.pts]] * timescale)
            packet.dts = round((seconds[position] - lead) * timescale)
            packet.time_base = Fraction(1, timescale)
            position += 1
            output_container.mux(packet)
    return destination


# Frame rate and container timescale, for clips whose timestamps a coarse tick
# quantizes. Each is a rate a real recorder produces written into a timescale
# too coarse to express it exactly, so consecutive frames land unevenly while
# the file is still constant-rate. The committed corpus is all 1/15360 mp4 and
# cannot express this class at all.
QUANTIZED_RATES: tuple[tuple[str, float, int], ...] = (
    ("30_in_36", 30.0, 36),
    ("25_in_30", 25.0, 30),
    ("23_976_in_30", 24000 / 1001, 30),
    ("50_in_60", 50.0, 60),
    ("10_in_12", 10.0, 12),
)


@pytest.fixture(scope="session")
def quantized_clips(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """One clip per `QUANTIZED_RATES` entry, timestamps on that coarse tick.

    Frame `i` sits at `round(i * timescale / fps)` ticks -- what a muxer writing
    that rate into that timescale does. Every one probes constant-rate with no
    analysis reason, so the reader must read each cleanly.
    """
    root = tmp_path_factory.mktemp("quantized")
    source = asset("cfr_30fps.mp4", root / "cfr_30fps.mp4")
    made: dict[str, Path] = {}
    for name, fps, timescale in QUANTIZED_RATES:
        frames = 60
        seconds = [
            round(index * timescale / fps) / timescale for index in range(frames)
        ]
        made[name] = _restamp(source, root / f"{name}.mp4", timescale, seconds)
    return made


@pytest.fixture(scope="session")
def ramped_rate_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A clip whose rate slides from 30 fps to 24 over its length.

    The whole-file grid fit calls it variable, because the deviation from a
    single uniform grid accumulates; no two neighbors are far apart. That
    combination is what separates a per-file spacing threshold from a
    constant-rate exemption: this clip is checked by the first and would be
    exempted wholesale by the second.
    """
    root = tmp_path_factory.mktemp("ramped")
    source = asset("cfr_30fps.mp4", root / "cfr_30fps.mp4")
    frames = 60
    seconds = [0.0]
    for step in range(frames):
        seconds.append(seconds[-1] + 1.0 / (30.0 - 6.0 * step / frames))
    return _restamp(source, root / "ramped.mp4", 15360, seconds)


@pytest.fixture(scope="session")
def raw_starting_on_non_keyframes(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Path]:
    """A raw H.264 elementary stream whose first frames precede its keyframe.

    The two shapes that lose frames to a copy remux, in one file: no packet
    timestamps at all, and 24 non-keyframes ahead of the keyframe at 25. A
    tracking box writing a bare bitstream and starting its recording mid-stream
    produces exactly this.

    Built by concatenating the committed AVI's packet payloads from index 1 --
    the same cut `avi_starting_on_non_keyframes` makes, written without a
    container so the stream carries no timing of its own. Remuxing the AVI into
    a raw stream with ffmpeg would not produce it: that path drops the leading
    non-keyframes, which is the behavior under test.
    """
    root = tmp_path_factory.mktemp("headless_raw")
    source = asset("h264.avi", root / "h264.avi")
    destination = root / "starts_on_non_keyframes.h264"
    payload = bytearray()
    with av.open(str(source)) as input_container:
        input_stream = input_container.streams.video[0]
        for position, packet in enumerate(input_container.demux(input_stream)):
            if packet.size == 0 or position == 0:
                continue
            payload += bytes(packet)
    _ = destination.write_bytes(bytes(payload))
    yield destination


@pytest.fixture(scope="session")
def vp8_webm_clip(clips: dict[str, Path]) -> Path:
    return clips["vp8_webm"]


@pytest.fixture(scope="session")
def vp9_webm_clip(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """VP9, so every member of the shipped trusted codec set is measured.

    libvpx-vp9 is non-GPL and present in the FFmpeg this suite runs against, so
    this member is generated rather than committed.
    """
    root = tmp_path_factory.mktemp("vp9")
    yield build(root / "vp9.webm", "-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p")


@pytest.fixture(scope="session")
def hevc_clip(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """HEVC, committed for the reason every H.264 asset here is.

    Its decoder is native and LGPL, so the suite reads it with no extra
    dependency. It backs the trusted-codec delivery test and the mp4 carriage
    measurement, so both sets are measured on this codec rather than assuming it.
    """
    root = tmp_path_factory.mktemp("hevc")
    yield asset("hevc.mp4", root / "hevc.mp4")


@pytest.fixture(scope="session")
def cfr_mp4_clip(clips: dict[str, Path]) -> Path:
    return clips["cfr_mp4"]


@pytest.fixture(scope="session")
def open_gop_clip(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """50 frames, 25 fps, GOP 12, open GOP with B-frames.

    Every keyframe after the first is followed in decode order by pictures that
    precede it in presentation order -- the shape a decoder suppresses after a
    seek. Committed rather than encoded for the reason every H.264 asset here is.
    """
    root = tmp_path_factory.mktemp("open_gop")
    yield asset("open_gop.mp4", root / "open_gop.mp4")


@pytest.fixture(scope="session")
def preroll_mp4(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """An mp4 whose edit list marks its leading packets "do not present".

    A `-c copy` cut at 0.2 s writes an edit list that skips the pre-roll rather
    than re-encoding it, so the demuxer delivers five discard-flagged packets at
    negative timestamps: `-0.200000,KD_` followed by four `_D_`. That is the
    shape `ignore_editlist` exists for, and the only fixture in the suite that
    fires the gate. Built from a committed asset, so no encoder is involved.
    """
    root = tmp_path_factory.mktemp("preroll")
    source = asset("cfr.mp4", root / "cfr.mp4")
    yield build(
        root / "preroll.mp4", "-c", "copy", source=["-ss", "0.2", "-i", str(source)]
    )
