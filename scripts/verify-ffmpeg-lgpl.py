"""Assert that PyAV links an FFmpeg carrying no GPL-only component.

Run as a build gate and as the image's default command. Exits non-zero with the
failing condition named, so a wheel-installed PyAV or a GPL FFmpeg cannot pass
unnoticed.

Every check here is written to fail on absent, empty, or unparsed input. A
negative assertion ("x is not present") is paired with a positive one ("the
thing that would contain x was actually read"), because a negative alone
succeeds against nothing and turns a broken probe into a green build.
"""

import ctypes
import pathlib
import re
import shutil
import subprocess
import sys
from typing import Literal

import av
import av.codec
import av.codec.codec

FFMPEG_PREFIX = "/opt/ffmpeg"

CodecMode = Literal["r", "w"]

failures: list[str] = []
notes: list[str] = []


def check(condition: bool, ok: str, bad: str) -> None:
    (notes if condition else failures).append(ok if condition else bad)


def codec_usable(name: str, mode: CodecMode) -> bool:
    """Whether this build can construct the named codec.

    Only UnknownCodecError is treated as "absent". Any other exception means the
    probe itself is broken and must crash the gate rather than resolve to a
    False that reads as a pass in the negative checks below.
    """
    try:
        _ = av.codec.Codec(name, mode)
    except av.codec.codec.UnknownCodecError:
        return False
    return True


def encode_succeeds(codec: str, destination: str) -> bool:
    """Whether a two-frame encode through the named codec exits zero."""
    encode = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=64x48:rate=30",
            "-frames:v",
            "2",
            "-c:v",
            codec,
            "-f",
            "mp4",
            destination,
        ],
        capture_output=True,
        text=True,
    )
    return encode.returncode == 0


# --------------------------------------------------------------------------
# 1. Identify the libavcodec PyAV actually mapped into this process.
#
# Reading it from /proc/self/maps rather than dlopen-ing a soname: CDLL("libav
# codec.so.62") resolves through LD_LIBRARY_PATH and would find the /opt/ffmpeg
# copy even if PyAV had linked a wheel's private one. Auditwheel renames bundled
# libraries (libavcodec-<hash>.so.62), so that lookup can never reach them --
# every reading taken from it would describe the wrong library.
# --------------------------------------------------------------------------
maps = pathlib.Path("/proc/self/maps").read_text()
loaded = sorted(set(re.findall(r"\S*/libavcodec[^\s]*\.so[^\s]*", maps)))

check(
    len(loaded) == 1,
    f"exactly one libavcodec mapped: {loaded[0] if loaded else '-'}",
    f"expected one mapped libavcodec, found {len(loaded)}: {loaded}",
)
if not loaded:
    print("FAIL  no libavcodec mapped; PyAV did not load", file=sys.stderr)
    sys.exit(1)

check(
    all(path.startswith(f"{FFMPEG_PREFIX}/") for path in loaded),
    f"PyAV links {FFMPEG_PREFIX}",
    f"PyAV links a library outside {FFMPEG_PREFIX}: {loaded}",
)

handle = ctypes.CDLL(loaded[0])
handle.avcodec_license.restype = ctypes.c_char_p
handle.avcodec_configuration.restype = ctypes.c_char_p
license_text: str = handle.avcodec_license().decode()
configuration: str = handle.avcodec_configuration().decode()

# The configuration must have been read before any negative test against it.
check(
    "--prefix=" in configuration,
    "read the build configuration from the linked library",
    "the linked library reported no usable configuration string",
)

# --------------------------------------------------------------------------
# 2. Secondary: the wheel's private FFmpeg directory must be absent. A proxy
#    for "built from source" -- the mapping assertion above is the real proof.
#    No .resolve(): under a symlinked venv that walks out of site-packages.
# --------------------------------------------------------------------------
bundled = sorted(pathlib.Path(av.__path__[0]).parent.glob("av.libs"))
check(
    not bundled,
    "no bundled av.libs alongside the package",
    f"PyAV ships a private FFmpeg: {bundled}",
)

# --------------------------------------------------------------------------
# 3. License declaration. avcodec_license() returns "LGPL version N ...",
#    "GPL version N ...", or "nonfree and unredistributable"; match the prefix,
#    since a substring test for "GPL" also matches the desired "LGPL".
# --------------------------------------------------------------------------
check(
    license_text.startswith("LGPL"),
    f"libavcodec license: {license_text}",
    f"libavcodec is not LGPL: {license_text}",
)
for flag in ("--enable-gpl", "--enable-nonfree"):
    check(
        flag not in configuration,
        f"configuration carries no {flag}",
        f"configuration carries {flag}",
    )

# --------------------------------------------------------------------------
# 4. The GPL-only encoders must be gone. Enumeration is the primary test: a
#    name lookup can fail for reasons unrelated to absence, but the registered
#    codec set cannot report a codec that is not built in.
# --------------------------------------------------------------------------
available = set(av.codec.codecs_available)
check(
    len(available) > 100,
    f"codec enumeration returned {len(available)} names",
    f"codec enumeration returned {len(available)} names; the probe is broken",
)
for name in ("libx264", "libx264rgb", "libx265"):
    check(
        name not in available,
        f"{name} not registered",
        f"{name} is registered: the build is effectively GPL",
    )
    check(
        not codec_usable(name, "w"),
        f"{name} encoder not constructible",
        f"{name} encoder constructs",
    )
for flag in ("--enable-libx264", "--enable-libx265"):
    check(
        flag not in configuration,
        f"configuration does not reference {flag}",
        f"configuration references {flag}",
    )

# --------------------------------------------------------------------------
# 5. Nothing this stack depends on may have been lost. These positive checks
#    also serve as the canary for a broken probe: if codec construction were
#    failing generally, they fail here rather than silently passing above.
# --------------------------------------------------------------------------
for name in ("h264", "hevc", "av1", "vp9", "mpeg4", "prores"):
    check(codec_usable(name, "r"), f"{name} decoder present", f"{name} decoder MISSING")
check(
    codec_usable("libsvtav1", "w"),
    "libsvtav1 encoder present: AV1 transcode intact",
    "libsvtav1 encoder MISSING: AV1 transcode would break",
)

# --------------------------------------------------------------------------
# 6. The CLI the subprocess paths call must be the SAME build, not merely a
#    non-GPL one. Comparing configuration strings is what "same build" means,
#    and the configuration is already in hand.
# --------------------------------------------------------------------------
resolved_cli = shutil.which("ffmpeg")
check(
    resolved_cli == f"{FFMPEG_PREFIX}/bin/ffmpeg",
    f"ffmpeg on PATH is {resolved_cli}",
    f"ffmpeg on PATH is {resolved_cli}, not {FFMPEG_PREFIX}/bin/ffmpeg",
)
cli = subprocess.run(
    ["ffmpeg", "-hide_banner", "-version"], capture_output=True, text=True, check=True
).stdout
cli_configuration = next(
    (
        line.split("configuration:", 1)[1].strip()
        for line in cli.splitlines()
        if line.startswith("configuration:")
    ),
    "",
)
check(
    bool(cli_configuration),
    "ffmpeg CLI reported its configuration",
    "ffmpeg CLI produced no configuration line",
)
check(
    cli_configuration == configuration.strip(),
    "ffmpeg CLI is the same build as the linked library",
    "ffmpeg CLI is a different build from the linked library",
)

# --------------------------------------------------------------------------
# 7. End-to-end: encoding H.264 through libx264 must actually fail. This is the
#    only test that exercises the encoder rather than inspecting metadata, so a
#    zero exit here means the licensing premise has failed outright.
# --------------------------------------------------------------------------
check(
    not encode_succeeds("libx264", "/tmp/gpl-probe.mp4"),
    "encoding with libx264 fails, as it must",
    "encoding with libx264 SUCCEEDED: a GPL encoder is reachable",
)
check(
    encode_succeeds("mpeg4", "/tmp/native-probe.mp4"),
    "encoding with the native mpeg4 encoder works",
    "encoding with mpeg4 failed: the CLI is unusable, so the libx264 result proves nothing",
)

libavcodec_version = ".".join(str(part) for part in av.library_versions["libavcodec"])
print(f"PyAV {av.__version__}")
print(f"libavcodec {libavcodec_version} from {loaded[0]}")
for note in notes:
    print(f"  ok    {note}")
for failure in failures:
    print(f"  FAIL  {failure}", file=sys.stderr)

if failures:
    print(f"\n{len(failures)} check(s) failed", file=sys.stderr)
    sys.exit(1)
print(f"\nall {len(notes)} checks passed")
