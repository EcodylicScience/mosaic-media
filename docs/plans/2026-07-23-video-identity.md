# Video identity implementation plan

> Execute task by task with a fresh implementer per task and a combined
> specification-and-quality review between tasks. Steps use checkbox syntax for
> tracking.

**Goal:** Give the probe two derived values -- `video_uuid`, an exact identity
safe to name directories from, and `content_digest`, invariant across container
rewrites that preserve the elementary stream, used only as the duplicate
pathway's index key.

**Architecture:** Both come out of the packet scan the probe already runs. The
scan gains ffprobe's `-show_data_hash`, so each packet carries a payload hash
computed by the demuxer rather than read from a container-relative byte offset.
A new standard-library-only module folds facts, per-packet structure, and those
payload hashes into `content_digest`, then folds the packet timestamps on top to
reach `video_uuid`. A comparison function exported from the package facade owns
the tolerance test so no consumer reimplements it.

**Tech stack:** Python 3.12, standard library only in the probe core
(`hashlib`, `struct`, `uuid`, `dataclasses`, `typing`). System ffprobe. typer in
the CLI layer only. pytest.

**Source spec:** `docs/specs/2026-07-23-video-identity.md`. Read it before
starting; this plan implements it and may not diverge from it. On hitting a
genuine conflict -- a type error the design did not anticipate, an unexpected
constraint -- stop and report rather than working around it.

## Global constraints

- Python floor is 3.12. Nothing may require 3.13 or later.
- The probe core imports standard library only. Never numpy, typer, av, or cv2
  from `src/mosaic_media/probe/`.
- Never import `mosaic` or `mosaic_api`.
- No `typing.Any`, no `typing.Optional` (use `X | None`), no `typing.cast`.
- No `# noqa`, no `# pyright: ignore`, no `# type: ignore`. Fix the design.
- Closed sets of strings are `Literal` aliases, never bare `str`. This applies
  to test helpers too.
- ASCII only in source and comments. American spelling.
- No abbreviations in identifiers.
- No conventional-commit prefixes (`feat:`, `fix:`, `chore:`) in commit
  messages. Plain English. No `Co-Authored-By` trailers.
- No process language in code, comments, docstrings, or commits: no tool names,
  no task or phase references, no session language.
- Verification commands:
  ```bash
  uv run ruff format src/ tests/
  uv run ruff check src/ tests/
  uv run basedpyright src/ tests/
  uv run pytest tests/
  ```
  basedpyright scope includes `tests/`, not just `src/`. Run a full suite
  through `heavy` (for example `heavy uv run pytest tests/`); a single test file
  may be run bare.

---

## File structure

| File | Responsibility | Task |
| --- | --- | --- |
| `src/mosaic_media/probe/ffprobe.py` | Modify: `Packet.data_hash`, the `-show_data_hash` flag, the six-column parse. | 1 |
| `src/mosaic_media/probe/identity.py` | Create: encoding primitives, `content_digest`, `video_uuid`, tolerance, comparison. | 2, 4 |
| `src/mosaic_media/probe/facts.py` | Modify: two new defaulted fields. | 3 |
| `src/mosaic_media/probe/probe.py` | Modify: mint and fill the two fields. | 3 |
| `src/mosaic_media/__init__.py` | Modify: facade exports. | 4 |
| `src/mosaic_media/cli/application.py` | Modify: the `compare` command. | 5 |
| `src/mosaic_media/transcode/convert.py` | Modify: `TranscodeResult.source_video_uuid`. | 6 |
| `tests/helpers/media_fixtures.py` | Modify: remux and retime fixture variants. | 2 |
| `tests/probe/test_ffprobe.py` | Modify: payload hash column tests. | 1 |
| `tests/probe/test_identity.py` | Create: digest construction, invariance, sensitivity. | 2 |
| `tests/probe/test_identity_probe.py` | Create: probe-level minting. | 3 |
| `tests/probe/test_duplicate.py` | Create: the comparison pathway. | 4 |
| `tests/cli/test_compare.py` | Create: the CLI command. | 5 |
| `tests/test_import_guard.py` | Modify: guard the new core module. | 3 |
| `README.md` | Modify: the two values, pathways, limits, CLI. | 7 |

---

### Task 1: Payload hash in the packet scan

**Files:**
- Modify: `src/mosaic_media/probe/ffprobe.py`
- Test: `tests/probe/test_ffprobe.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Packet.data_hash: str` -- an `ALGO:hexdigest` string such as
  `"CRC32:a73878e0"`, populated by `scan_packets`, empty string on a `Packet`
  built anywhere else.

**Context.** `scan_packets` runs one ffprobe subprocess requesting
`packet=pts_time,dts_time,size,pos,flags` as headerless CSV and parses each row
positionally. Adding `data_hash` appends a sixth column at index 5. The row
guard is currently `if len(columns) < 5: continue`.

MPEG-TS emits a seventh column (an empty `[SIDE_DATA]` subsection prints after
`data_hash`), which does not move index 5, so a "fewer than six" guard admits
those rows correctly.

Three distinct failure modes. Two are already reported well; only the middle one
needs new handling:

| Failure | Behavior | Handled by |
| --- | --- | --- |
| ffprobe does not know `-show_data_hash` | Exits 1 with `Option not found` | `_run` already raises a named `MediaProbeError`. |
| ffprobe accepts the flag but drops the `data_hash` entry | Exits 0, five columns per row, every row skipped | Step 4's guard. Without it: zero packets and a misleading "no packets in the video stream". |
| `PAYLOAD_HASH_ALGORITHM` is misspelled | Exits 1 with `Unknown hash algorithm '<name>'` and a list of the valid ones | `_run` already raises a named `MediaProbeError` carrying that stderr. |

ffprobe silently drops an unrecognized `-show_entries` name rather than failing,
which is what makes the second row possible; requesting a bogus entry emits the
remaining columns and exits 0.

Zero-length payload packets need no special case: the row is hashed exactly as
scanned, whatever ffprobe prints for its `data_hash`. Do not add a filter. The
in-process scan in `io/packets.py` skips `size == 0`, and this one deliberately
does not -- `"0".isdigit()` is true, so such a row is scanned like any other.

**Version floor: settled, no action needed.** `-show_data_hash` first shipped in
FFmpeg 2.4 (commit `4f3e2f10`, committed 2014-08-17; absent from `ffprobe.c` at
`n2.3`, present at `n2.4`). The README's existing runtime floor is ffprobe 5.1
for `-fps_mode`, so the flag is already covered and this work raises no
requirement. Task 7 records that rather than a new minimum.

- [ ] **Step 1: Write the failing test**

Add to `tests/probe/test_ffprobe.py`:

```python
def test_scan_packets_populates_payload_hash(clips: dict[str, Path]) -> None:
    packets, _source = scan_packets(clips["cfr_mp4"], video_position=0)
    assert packets
    for packet in packets:
        assert packet.data_hash.startswith("CRC32:")
        assert len(packet.data_hash) > len("CRC32:")


def test_scan_packets_populates_payload_hash_on_awkward_containers(
    clips: dict[str, Path],
) -> None:
    # no_pts_avi reports pts_time=N/A and raw_h264 reports both timestamps
    # absent. Both must still carry a payload hash: the column is positional and
    # independent of which timestamp survived.
    for name in ("no_pts_avi", "raw_h264", "mjpeg_avi", "vp8_webm"):
        packets, _source = scan_packets(clips[name], video_position=0)
        assert packets, name
        assert all(packet.data_hash.startswith("CRC32:") for packet in packets), name
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/probe/test_ffprobe.py -k payload_hash -v
```

Expected: FAIL with `AttributeError: 'Packet' object has no attribute 'data_hash'`.

- [ ] **Step 3: Add the field and request the column**

In `src/mosaic_media/probe/ffprobe.py`, replace the `Packet` dataclass:

```python
@dataclass(frozen=True, slots=True)
class Packet:
    """One demultiplexed packet.

    `data_hash` is the demuxer-delivered payload hash ffprobe reports, an
    `ALGO:hexdigest` string. It is what the identity digests hash, rather than
    bytes read at `pos`: byte offsets are container-relative and in Matroska
    address the SimpleBlock header, not the payload.

    It defaults to the empty string because the in-process scan in
    `mosaic_media.io.packets` mirrors this one and does not populate it.
    Identity is minted once by the probe at ingestion and never by the reader,
    so the io layer has no reason to pay for the payload read.
    """

    time: float
    size: int
    keyframe: bool
    pos: int
    data_hash: str = ""
```

In `scan_packets`, replace the command with:

```python
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        f"v:{video_position}",
        "-show_data_hash",
        PAYLOAD_HASH_ALGORITHM,
        "-show_entries",
        "packet=pts_time,dts_time,size,pos,flags,data_hash",
        "-of",
        "csv=p=0",
        str(path.absolute()),
    ]
```

Add the constant near `SCAN_TIMEOUT_SECONDS`:

```python
# The per-packet payload hash algorithm. Chosen on the identity collision
# budget, not on cryptographic strength: the digests fold one hash per packet,
# so accidental aliasing needs every packet to collide. Changing this changes
# every minted digest.
PAYLOAD_HASH_ALGORITHM = "CRC32"
```

- [ ] **Step 4: Parse the sixth column and fail loudly when it is absent**

Replace the parse loop body in `scan_packets`:

```python
    pts_packets: list[Packet] = []
    dts_packets: list[Packet] = []
    untimed_packets: list[Packet] = []
    rows_without_payload_hash = 0
    for line in raw.splitlines():
        # ffprobe emits the requested entries in its own natural order:
        # pts_time, dts_time, size, pos, flags, data_hash. Byte offset (pos) is
        # N/A on containers that do not expose it; it is carried for io
        # consumers and is not used by the timestamp-based seek path, so -1 is a
        # safe unknown. MPEG-TS appends a seventh empty side-data column, which
        # does not move any index below it.
        columns = line.split(",")
        if len(columns) < 6:
            if len(columns) >= 5:
                rows_without_payload_hash += 1
            continue
        size_text, pos_text, flags = columns[2], columns[3], columns[4]
        data_hash = columns[5]
        if not size_text.isdigit():
            continue
        size = int(size_text)
        pos = int(pos_text) if pos_text.lstrip("-").isdigit() else -1
        keyframe = "K" in flags
        if columns[0] not in _ABSENT:
            pts_packets.append(
                Packet(
                    time=float(columns[0]),
                    size=size,
                    keyframe=keyframe,
                    pos=pos,
                    data_hash=data_hash,
                )
            )
        if columns[1] not in _ABSENT:
            dts_packets.append(
                Packet(
                    time=float(columns[1]),
                    size=size,
                    keyframe=keyframe,
                    pos=pos,
                    data_hash=data_hash,
                )
            )
        if columns[0] in _ABSENT and columns[1] in _ABSENT:
            untimed_packets.append(
                Packet(
                    time=0.0,
                    size=size,
                    keyframe=keyframe,
                    pos=pos,
                    data_hash=data_hash,
                )
            )

    if pts_packets:
        return tuple(pts_packets), "pts"
    if dts_packets:
        return tuple(dts_packets), "dts"
    if untimed_packets:
        return tuple(untimed_packets), "none"
    if rows_without_payload_hash:
        # Every row arrived without the payload-hash column. An ffprobe that
        # does not know -show_data_hash exits non-zero and never reaches here;
        # this is the quieter failure where the flag is accepted but the
        # data_hash entry is dropped, since ffprobe ignores an unrecognized
        # -show_entries name rather than failing. Naming it beats the generic
        # "no packets" message, which would send a reader looking at the file.
        message = (
            f"ffprobe returned packets without payload hashes for {path}: "
            f"the installed ffprobe does not report data_hash"
        )
        raise MediaProbeError(message)
    message = f"no packets in the video stream of {path}"
    raise MediaProbeError(message)
```

Update the `scan_packets` docstring: it currently states two ffprobe calls
request five entries. It must name the payload hash and say the scan now reads
every packet's payload, which it previously did not.

- [ ] **Step 5: Write the failing test for the missing-column error**

Add to `tests/probe/test_ffprobe.py`:

```python
def test_scan_packets_names_a_missing_payload_hash_column(
    clips: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # An ffprobe without -show_data_hash emits five columns. Every row is then
    # unusable, and the failure must name the cause rather than claiming the
    # file has no packets.
    def five_column_rows(
        command: list[str], timeout: int, action: str
    ) -> str:
        return "0.000000,0.000000,3837,48,K__\n0.040000,0.040000,120,3885,___\n"

    monkeypatch.setattr("mosaic_media.probe.ffprobe._run", five_column_rows)
    with pytest.raises(MediaProbeError, match="does not report data_hash"):
        _ = scan_packets(clips["cfr_mp4"], video_position=0)
```

Add `import pytest` and `from mosaic_media.probe.errors import MediaProbeError`
to the test module's imports if they are not already present.

- [ ] **Step 6: Run the full ffprobe test module**

```bash
uv run pytest tests/probe/test_ffprobe.py -v
```

Expected: PASS, including the pre-existing
`test_scan_packets_populates_byte_offset` and the DTS-fallback tests.

- [ ] **Step 7: Re-evaluate the scan timeout**

`SCAN_TIMEOUT_SECONDS` is 900 and was sized for a scan that did not read
payload. The scan now reads every payload byte, measured at between 1.5x and 2x
its previous cost on a 481 MB file. Decide whether 900 still covers the largest
expected input, and either leave it with a comment recording the decision or
raise it. Record the reasoning in the comment above the constant either way.

- [ ] **Step 8: Verify and commit**

```bash
uv run ruff format src/ tests/
uv run ruff check src/ tests/
uv run basedpyright src/mosaic_media/probe/ffprobe.py tests/probe/test_ffprobe.py
uv run pytest tests/probe/ -v
```

```bash
git add src/mosaic_media/probe/ffprobe.py tests/probe/test_ffprobe.py
git commit -m "Read a per-packet payload hash during the packet scan"
```

---

### Task 2: The digest and uuid

**Files:**
- Create: `src/mosaic_media/probe/identity.py`
- Modify: `tests/helpers/media_fixtures.py`
- Test: `tests/probe/test_identity.py`

**Interfaces:**
- Consumes: `Packet.data_hash` from Task 1; `Header` and `Packet` from
  `mosaic_media.probe.ffprobe`.
- Produces:
  ```python
  CONTENT_FORMAT_TAG: bytes          # b"mosaic-media/content/1"
  VIDEO_FORMAT_TAG: bytes            # b"mosaic-media/video/1"

  def content_digest_input(header: Header, packets: tuple[Packet, ...]) -> bytes
  def video_uuid_input(
      content_bytes: bytes, timing_measured: bool, packets: tuple[Packet, ...]
  ) -> bytes

  @dataclass(frozen=True, slots=True)
  class Identity:
      video_uuid: str
      content_digest: str

  def mint_identity(
      header: Header, packets: tuple[Packet, ...], *, timing_measured: bool
  ) -> Identity
  ```

**Context.** `mint_identity` takes `Header` rather than `MediaFacts` because
every fact the content digest hashes is already on `Header`, and `probe_media`
holds the header and the packets together before it builds `MediaFacts`. The
input-building functions are public, not underscore-prefixed, because the tests
import them for the golden vector; a private name imported by another module
would be a `reportPrivateUsage` finding.

- [ ] **Step 1: Add the remux fixture variants**

In `tests/helpers/media_fixtures.py`, add after the `clips` fixture:

```python
@pytest.fixture(scope="session")
def rewrites(clips: dict[str, Path], tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """The same coded content rewritten every way the identity values care about.

    `origin` is the reference. `faststart`, `repack`, and `tagged` are
    same-container rewrites that leave packet timestamps byte-identical.
    `matroska` and `roundtrip` change the container, which quantizes timestamps
    to milliseconds; the round trip inherits that quantization rather than
    undoing it. `transport` additionally reframes the elementary stream to
    Annex B, which changes the coded bytes themselves.

    The 30 fps source is load-bearing, not incidental. A 25 fps frame period is
    exactly 40 ms, so Matroska's millisecond timebase requantizes nothing and
    every timestamp survives a repack byte-identical -- on a 25 fps origin the
    container-change tests below cannot fail no matter how broken the
    implementation is. 30 fps gives a 33.333 ms period, which does requantize.
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
```

- [ ] **Step 2: Write the failing tests**

Create `tests/probe/test_identity.py`:

```python
import hashlib
import uuid
from pathlib import Path

from mosaic_media.probe.ffprobe import Header, Packet, read_header, scan_packets
from mosaic_media.probe.identity import Identity, content_digest_input, mint_identity


def identity_of(path: Path) -> Identity:
    header = read_header(path)
    packets, source = scan_packets(path, header.video_position)
    return mint_identity(header, packets, timing_measured=source != "none")


def test_mint_identity_is_deterministic(clips: dict[str, Path]) -> None:
    first = identity_of(clips["cfr_mp4"])
    second = identity_of(clips["cfr_mp4"])
    assert first == second


def test_video_uuid_is_a_uuid_version_eight_string(clips: dict[str, Path]) -> None:
    minted = identity_of(clips["cfr_mp4"])
    parsed = uuid.UUID(minted.video_uuid)
    assert parsed.version == 8
    assert parsed.variant == uuid.RFC_4122


def test_content_digest_is_thirty_two_hex_characters(clips: dict[str, Path]) -> None:
    minted = identity_of(clips["cfr_mp4"])
    assert len(minted.content_digest) == 32
    assert int(minted.content_digest, 16) >= 0


def test_content_digest_survives_stream_preserving_rewrites(
    rewrites: dict[str, Path],
) -> None:
    reference = identity_of(rewrites["origin"]).content_digest
    for name in ("faststart", "repack", "tagged", "matroska", "roundtrip"):
        assert identity_of(rewrites[name]).content_digest == reference, name


def test_content_digest_changes_on_bitstream_reframing(
    rewrites: dict[str, Path],
) -> None:
    # mp4 to MPEG-TS applies Annex B conversion, which changes the coded bytes.
    # No digest over those bytes can be invariant to it; this asserts the limit
    # rather than leaving it undefined.
    reference = identity_of(rewrites["origin"]).content_digest
    assert identity_of(rewrites["transport"]).content_digest != reference


def test_video_uuid_survives_same_container_rewrites(
    rewrites: dict[str, Path],
) -> None:
    reference = identity_of(rewrites["origin"]).video_uuid
    for name in ("faststart", "repack", "tagged"):
        assert identity_of(rewrites[name]).video_uuid == reference, name


def test_video_uuid_changes_on_container_change_and_is_not_restored(
    rewrites: dict[str, Path],
) -> None:
    reference = identity_of(rewrites["origin"]).video_uuid
    matroska = identity_of(rewrites["matroska"]).video_uuid
    roundtrip = identity_of(rewrites["roundtrip"]).video_uuid
    assert matroska != reference
    # Repacking back to mp4 inherits the millisecond quantization rather than
    # undoing it, so the original uuid is gone for good.
    assert roundtrip != reference


def test_retime_keeps_the_content_digest_and_moves_the_uuid(
    rewrites: dict[str, Path],
) -> None:
    reference = identity_of(rewrites["origin"])
    retimed = identity_of(rewrites["retimed"])
    assert retimed.content_digest == reference.content_digest
    assert retimed.video_uuid != reference.video_uuid


def test_truncation_changes_the_content_digest(
    clips: dict[str, Path], truncated_faststart: Path
) -> None:
    assert (
        identity_of(truncated_faststart).content_digest
        != identity_of(clips["faststart_mp4"]).content_digest
    )


def test_flipping_a_payload_byte_changes_the_content_digest(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    # faststart_mp4 specifically: it is the only mp4 fixture whose moov is at
    # the front, so it has a header region big enough to distinguish from
    # payload. In a non-faststart mp4 the moov is at the end and mdat starts at
    # byte 44, which leaves no header bytes to target and makes the companion
    # test below impossible.
    source = clips["faststart_mp4"]
    payload = source.read_bytes()
    mdat = payload.find(b"mdat")
    assert mdat > 200, "fixture is not faststart: no header region to target"
    mutated = bytearray(payload)
    mutated[mdat + 64] ^= 0xFF
    corrupted = tmp_path / "corrupted.mp4"
    _ = corrupted.write_bytes(bytes(mutated))
    assert identity_of(corrupted).content_digest != identity_of(source).content_digest


def test_a_header_region_byte_does_not_change_the_content_digest(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    # The companion to the test above, and the reason it targets mdat: the
    # digest covers coded payload, not container bytes. Without this pair, a
    # test that flipped an arbitrary byte would pass against an implementation
    # that hashed the whole file.
    source = clips["faststart_mp4"]
    payload = source.read_bytes()
    mdat = payload.find(b"mdat")
    assert mdat > 200, "fixture is not faststart: no header region to target"
    mutated = bytearray(payload)
    # Byte 200 is inside moov metadata, well before the media data box.
    mutated[200] ^= 0xFF
    rewritten = tmp_path / "header_touched.mp4"
    _ = rewritten.write_bytes(bytes(mutated))
    assert identity_of(rewritten).content_digest == identity_of(source).content_digest
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
uv run pytest tests/probe/test_identity.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'mosaic_media.probe.identity'`.

- [ ] **Step 4: Write the module**

Create `src/mosaic_media/probe/identity.py`:

```python
"""Two derived values minted from one packet scan.

`content_digest` pins the coded content and survives a container rewrite that
preserves the elementary stream. `video_uuid` pins content and exact timing and
survives nothing but the same content at the same timing -- it is the value safe
to name a directory from, and the only one that may be compared for identity.

Standard library only. This module is in the probe core, which must import on a
machine that has ffmpeg and nothing else.

The digest is defined against demuxer output, not raw file bytes: a packet's
payload hash is what libavformat hands ffprobe. An ffmpeg upgrade that changes
those bytes for a container is a format break, answered by bumping the format
tag and re-minting, not by silently producing values that no longer compare.

Every packet contributes its payload hash, so two files alias only if every
packet's hash collides. The weakest reachable case is a two-packet timed file --
a one-packet timed file cannot be probed at all, because measuring timing needs
two distinct timestamps -- which leaves an accidental-collision probability far
inside the target for any real recording.

This is not a security primitive. The payload hash is a checksum, not a
collision-resistant hash, so a party who can craft input files can produce a
colliding pair deliberately. The guarantee is against accidental collision
between independently produced recordings.
"""

import hashlib
import struct
import uuid
from dataclasses import dataclass

from .ffprobe import Header, Packet

CONTENT_FORMAT_TAG = b"mosaic-media/content/1"
VIDEO_FORMAT_TAG = b"mosaic-media/video/1"

DIGEST_BYTES = 16


def encode_integer(value: int) -> bytes:
    return struct.pack("<q", value)


def encode_boolean(value: bool) -> bytes:
    return b"\x01" if value else b"\x00"


def encode_float(value: float) -> bytes:
    return struct.pack("<d", value)


def encode_bytes(value: bytes) -> bytes:
    return struct.pack("<I", len(value)) + value


def encode_string(value: str) -> bytes:
    return encode_bytes(value.encode("utf-8"))


def content_digest_input(header: Header, packets: tuple[Packet, ...]) -> bytes:
    """The exact bytes hashed into `content_digest`.

    Public so a test can pin them against a golden vector: an encoding or
    ordering change that a same-machine round trip would hide is exactly the
    change that breaks every already-minted value.

    Every element is length-prefixed or fixed-width, so no two distinct inputs
    can serialize to the same byte string. Timestamps are absent on purpose --
    a container change requantizes them, and they enter `video_uuid` instead.
    """
    parts = [
        encode_bytes(CONTENT_FORMAT_TAG),
        encode_string(header.codec_name),
        encode_string(header.pixel_format),
        encode_string(header.color_range),
        encode_string(header.color_primaries),
        encode_string(header.color_transfer),
        encode_integer(header.width),
        encode_integer(header.height),
        encode_integer(header.rotation_degrees),
        encode_boolean(header.square_pixels),
        encode_boolean(header.progressive),
        encode_integer(len(packets)),
    ]
    for packet in packets:
        parts.append(encode_integer(packet.size))
        parts.append(encode_boolean(packet.keyframe))
        # Hashed whole, algorithm prefix included, so changing the algorithm
        # changes every digest instead of producing an incomparable one that
        # looks comparable.
        parts.append(encode_string(packet.data_hash))
    return b"".join(parts)


def video_uuid_input(
    content_bytes: bytes, timing_measured: bool, packets: tuple[Packet, ...]
) -> bytes:
    """The exact bytes hashed into `video_uuid`."""
    parts = [
        encode_bytes(VIDEO_FORMAT_TAG),
        encode_bytes(content_bytes),
        encode_boolean(timing_measured),
        encode_integer(len(packets)),
    ]
    for packet in packets:
        parts.append(encode_float(packet.time))
    return b"".join(parts)


def _digest(payload: bytes) -> bytes:
    return hashlib.blake2b(payload, digest_size=DIGEST_BYTES).digest()


def _as_uuid_version_eight(digest: bytes) -> str:
    """Set the RFC 9562 version and variant bits by hand.

    `uuid.UUID(bytes=..., version=8)` is not usable: CPython accepts only
    versions 1 through 5 until 3.14, and this package's floor is 3.12. 122 of
    the 128 hash bits survive.
    """
    octets = bytearray(digest)
    octets[6] = (octets[6] & 0x0F) | 0x80
    octets[8] = (octets[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(octets)))


@dataclass(frozen=True, slots=True)
class Identity:
    """The two derived values. See the module docstring for which to use where."""

    video_uuid: str
    content_digest: str


def mint_identity(
    header: Header, packets: tuple[Packet, ...], *, timing_measured: bool
) -> Identity:
    """Mint both values from one scan's header and packets.

    Takes `Header` rather than `MediaFacts` because every fact hashed into
    `content_digest` is already on the header, and the caller holds the header
    and the packets together before it assembles the facts.
    """
    content_bytes = _digest(content_digest_input(header, packets))
    video_bytes = _digest(video_uuid_input(content_bytes, timing_measured, packets))
    return Identity(
        video_uuid=_as_uuid_version_eight(video_bytes),
        content_digest=content_bytes.hex(),
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/probe/test_identity.py -v
```

Expected: PASS, all eleven tests written in Step 2. Step 6 adds two more.

- [ ] **Step 6: Pin the golden vector against constructed inputs, not a fixture**

The golden vector must not be generated from an encoded file. Packet sizes and
payload hashes are outputs of one libx264 build, so a distro encoder bump would
fail a test that says nothing about this package's serialization -- the same
objection the spec raises against pinning the drift floats.

Build the input by hand instead. Add to `tests/probe/test_identity.py`:

```python
GOLDEN_HEADER = Header(
    container="mov,mp4,m4a,3gp,3g2,mj2",
    codec_name="h264",
    pixel_format="yuv420p",
    color_range="tv",
    color_primaries="bt709",
    color_transfer="bt709",
    width=320,
    height=240,
    rotation_degrees=0,
    square_pixels=True,
    progressive=True,
    has_audio=False,
    video_stream_count=1,
    video_position=0,
    start_time=0.0,
    declared_duration=2.0,
    declared_fps=25.0,
    declared_frame_count=50,
)

GOLDEN_PACKETS = (
    Packet(time=0.0, size=3837, keyframe=True, pos=48, data_hash="CRC32:759f21ea"),
    Packet(time=0.04, size=120, keyframe=False, pos=3885, data_hash="CRC32:1a2b3c4d"),
)


def test_content_digest_input_matches_the_golden_vector() -> None:
    # Pins the serialized bytes, not just the digest: an encoding or field-order
    # change that a same-machine round trip would hide is exactly the change
    # that invalidates every already-minted value in every consumer.
    #
    # Built from constructed values rather than an encoded fixture, so the
    # vector depends only on this module's serialization. A fixture-derived
    # vector would additionally depend on the local libx264 build.
    payload = content_digest_input(GOLDEN_HEADER, GOLDEN_PACKETS)
    assert len(payload) == GOLDEN_LENGTH
    assert hashlib.sha256(payload).hexdigest() == GOLDEN_SHA256
```

Generate the two constants once and freeze them:

```bash
uv run python -c "
import hashlib
from tests.probe.test_identity import GOLDEN_HEADER, GOLDEN_PACKETS
from mosaic_media.probe.identity import content_digest_input
payload = content_digest_input(GOLDEN_HEADER, GOLDEN_PACKETS)
print('GOLDEN_LENGTH =', len(payload))
print('GOLDEN_SHA256 =', repr(hashlib.sha256(payload).hexdigest()))
"
```

Run it with `PYTHONPATH=.` if `tests` does not resolve. Paste both lines in at
module level.

Then add the fixture round trip as a separate, weaker check -- it catches a
break in the scan wiring that constructed inputs cannot see:

```python
def test_the_digest_input_length_tracks_the_packet_count(
    clips: dict[str, Path],
) -> None:
    # 112 fixed bytes of tag and header fields, then 27 per packet: 8 for size,
    # 1 for the keyframe flag, and 4 + 14 for the length-prefixed CRC32 string.
    header = read_header(clips["cfr_mp4"])
    packets, _source = scan_packets(clips["cfr_mp4"], header.video_position)
    payload = content_digest_input(header, packets)
    assert len(payload) == 112 + 27 * len(packets)
```

If the arithmetic in that comment does not match what the implementation
produces, the per-packet stride is what changed -- recompute it from the
encoding primitives and correct the comment rather than the assertion.

- [ ] **Step 7: Verify and commit**

```bash
uv run ruff format src/ tests/
uv run ruff check src/ tests/
uv run basedpyright src/mosaic_media/probe/identity.py tests/probe/test_identity.py
uv run pytest tests/probe/test_identity.py -v
```

```bash
git add src/mosaic_media/probe/identity.py tests/probe/test_identity.py tests/helpers/media_fixtures.py
git commit -m "Mint a content digest and a video uuid from the packet scan"
```

---

### Task 3: Wire the values into the probe

**Files:**
- Modify: `src/mosaic_media/probe/facts.py`
- Modify: `src/mosaic_media/probe/probe.py`
- Modify: `tests/test_import_guard.py`
- Test: `tests/probe/test_identity_probe.py`

**Interfaces:**
- Consumes: `mint_identity` and `Identity` from Task 2.
- Produces: `MediaFacts.video_uuid: str` and `MediaFacts.content_digest: str`,
  both populated by `probe_media`.

- [ ] **Step 1: Write the failing tests**

Create `tests/probe/test_identity_probe.py`:

```python
import subprocess
import sys
from pathlib import Path

import pytest

from mosaic_media.probe.errors import MediaProbeError
from mosaic_media.probe.probe import probe_media
from tests.helpers.media_fixtures import build


def test_probe_media_fills_both_identity_fields(clips: dict[str, Path]) -> None:
    facts = probe_media(clips["cfr_mp4"])
    assert len(facts.content_digest) == 32
    assert len(facts.video_uuid) == 36


def test_probe_media_mints_identity_for_an_untimed_stream(
    clips: dict[str, Path],
) -> None:
    # A raw elementary stream carries no timestamps. It still gets both values;
    # timing_measured records that the uuid's timing half is a placeholder.
    facts = probe_media(clips["raw_h264"])
    assert facts.timing_measured is False
    assert len(facts.content_digest) == 32
    assert len(facts.video_uuid) == 36


def test_faststart_and_plain_fixtures_agree_on_both_values(
    clips: dict[str, Path],
) -> None:
    # cfr_mp4 and faststart_mp4 are built from one filter source with the same
    # encoder settings and differ only in -movflags +faststart. They are the
    # same video, so both values must match.
    plain = probe_media(clips["cfr_mp4"])
    faststart = probe_media(clips["faststart_mp4"])
    assert plain.content_digest == faststart.content_digest
    assert plain.video_uuid == faststart.video_uuid


def test_identity_is_stable_across_processes(clips: dict[str, Path]) -> None:
    # Determinism within one interpreter would still pass if the digest depended
    # on hash randomization, dict ordering, or any other per-process state. A
    # fresh interpreter is what proves the value travels.
    program = (
        "from pathlib import Path\n"
        "from mosaic_media.probe.probe import probe_media\n"
        # probe_media requires a Path: read_header calls path.absolute(), so a
        # bare string raises AttributeError.
        "facts = probe_media(Path(" + repr(str(clips["cfr_mp4"])) + "))\n"
        "print(facts.video_uuid)\n"
        "print(facts.content_digest)\n"
    )
    first = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=120
    )
    assert first.returncode == 0, first.stderr
    second = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=120
    )
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout
    facts = probe_media(clips["cfr_mp4"])
    assert first.stdout.split() == [facts.video_uuid, facts.content_digest]


def test_a_two_packet_file_mints_both_values(tmp_path: Path) -> None:
    # The shortest probeable timed file. A single-packet timed file is not
    # probeable at all -- measure_timing needs two distinct timestamps -- so two
    # packets is the real floor, and the collision budget is stated against it.
    target = build(
        tmp_path / "two_frames.mp4",
        "-frames:v",
        "2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        source=["-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=1"],
    )
    facts = probe_media(target)
    assert facts.timing_measured is True
    assert len(facts.content_digest) == 32
    assert len(facts.video_uuid) == 36


def test_a_single_packet_timed_file_is_still_unprobeable(tmp_path: Path) -> None:
    # Unchanged behavior, pinned here because the collision budget's floor
    # argument depends on it: a one-packet timed file cannot reach the identity
    # code, so the weakest reachable timed case is two packets.
    target = build(
        tmp_path / "one_frame.mp4",
        "-frames:v",
        "1",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        source=["-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=1"],
    )
    with pytest.raises(MediaProbeError, match="too few distinct packet timestamps"):
        _ = probe_media(target)


def test_genuinely_distinct_fixtures_have_distinct_uuids(
    clips: dict[str, Path],
) -> None:
    # Scoped to exclude cfr_mp4/faststart_mp4, which are the same video by
    # construction and are covered by the test above.
    names = (
        "cfr_mp4",
        "cfr_30fps_mp4",
        "vp8_webm",
        "mjpeg_avi",
        "anamorphic_mp4",
        "audio_mp4",
        "raw_h264",
    )
    minted = [probe_media(clips[name]).video_uuid for name in names]
    assert len(set(minted)) == len(names)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/probe/test_identity_probe.py -v
```

Expected: FAIL with `AttributeError: 'MediaFacts' object has no attribute 'content_digest'`.

- [ ] **Step 3: Add the fields**

In `src/mosaic_media/probe/facts.py`, append two required fields to
`MediaFacts` and drop the default from `timing_measured`, which must lose it
because a required field cannot follow a defaulted one:

```python
    timing_measured: bool
    video_uuid: str
    content_digest: str
```

All three are required so an absent identity or an unmeasured timing flag is
something a caller states rather than something that fills itself in.
`timing_measured` defaulting to `True` was the worse of the two shapes: an
absent digest is detectably absent, an absent boolean asserts a measurement that
never happened.

Every construction site must pass all three. In this package that is
`probe_media` and the `CLEAN` fixture in `tests/probe/test_verdict.py`.

Extend the class docstring with:

```
    `video_uuid` and `content_digest` are the two derived identity values.
    `video_uuid` pins content and exact timing and is the only one that may be
    compared for identity or used to name anything. `content_digest` pins
    content alone, survives a container rewrite that preserves the elementary
    stream, and is the duplicate pathway's index key -- never an identity. Both
    default to the empty string so facts persisted before the fields existed
    round-trip unchanged.
```

- [ ] **Step 4: Mint during the probe**

In `src/mosaic_media/probe/probe.py`, add the import:

```python
from .identity import mint_identity
```

Insert before the `return MediaFacts(...)`:

```python
    identity = mint_identity(header, packets, timing_measured=timing_measured)
```

Add the two fields to the `MediaFacts(...)` call, after `timing_measured`:

```python
        video_uuid=identity.video_uuid,
        content_digest=identity.content_digest,
```

- [ ] **Step 5: Guard the new core module**

In `tests/test_import_guard.py`, add to `_CORE_IMPORTS`, keeping the list
alphabetical, between `mosaic_media.probe.gop` and `mosaic_media.probe.policy`:

```python
import mosaic_media.probe.identity
```

A new core module absent from that literal list is never guarded: the existing
tests would pass while proving nothing about it.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
uv run pytest tests/probe/test_identity_probe.py tests/test_import_guard.py -v
```

Expected: PASS.

- [ ] **Step 7: Run the whole suite**

```bash
heavy uv run pytest tests/
```

Expected: PASS. This is the first point where every pre-existing probe test sees
the new fields; a `dataclasses.asdict` comparison or a field-count assertion
elsewhere would surface here.

- [ ] **Step 8: Verify and commit**

```bash
uv run ruff format src/ tests/
uv run ruff check src/ tests/
uv run basedpyright src/ tests/
```

```bash
git add src/mosaic_media/probe/facts.py src/mosaic_media/probe/probe.py tests/probe/test_identity_probe.py tests/test_import_guard.py
git commit -m "Carry the identity values on MediaFacts"
```

---

### Task 4: The duplicate comparison

**Files:**
- Modify: `src/mosaic_media/probe/identity.py`
- Modify: `src/mosaic_media/__init__.py`
- Test: `tests/probe/test_duplicate.py`

**Interfaces:**
- Consumes: `MediaFacts.content_digest`, `.timing_measured`, `.fps`,
  `.duration` from Task 3.
- Produces:
  ```python
  TIMESTAMP_QUANTUM_SECONDS: float  # 0.001
  DRIFT_SAFETY: float               # 4.0
  DuplicateVerdict = Literal["duplicate", "different_timing", "timing_unknown", "distinct"]

  def timing_tolerance(value: float, duration: float) -> float

  @dataclass(frozen=True, slots=True)
  class DuplicateComparison:
      verdict: DuplicateVerdict
      fps_delta: float | None
      duration_delta: float | None
      fps_tolerance: float | None
      duration_tolerance: float | None

  def compare_for_duplicate(
      left: MediaFacts,
      right: MediaFacts,
      *,
      fps_tolerance: float | None = None,
      duration_tolerance: float | None = None,
  ) -> DuplicateComparison
  ```

- [ ] **Step 1: Write the failing tests**

Create `tests/probe/test_duplicate.py`:

```python
import dataclasses
from pathlib import Path

from mosaic_media import compare_for_duplicate, timing_tolerance
from mosaic_media.probe.probe import probe_media


def test_a_container_rewrite_reports_duplicate(rewrites: dict[str, Path]) -> None:
    left = probe_media(rewrites["origin"])
    for name in ("faststart", "repack", "tagged", "matroska", "roundtrip"):
        result = compare_for_duplicate(left, probe_media(rewrites[name]))
        assert result.verdict == "duplicate", name


def test_a_retime_reports_different_timing(rewrites: dict[str, Path]) -> None:
    left = probe_media(rewrites["origin"])
    result = compare_for_duplicate(left, probe_media(rewrites["retimed"]))
    assert result.verdict == "different_timing"
    assert result.fps_delta is not None
    assert result.fps_tolerance is not None


def test_an_unrelated_file_reports_distinct(clips: dict[str, Path]) -> None:
    result = compare_for_duplicate(
        probe_media(clips["cfr_mp4"]), probe_media(clips["vp8_webm"])
    )
    assert result.verdict == "distinct"
    assert result.fps_delta is None
    assert result.duration_delta is None
    assert result.fps_tolerance is None
    assert result.duration_tolerance is None


def test_an_untimed_side_reports_timing_unknown(clips: dict[str, Path]) -> None:
    # A raw elementary stream has fps and duration as 0.0 placeholders, so a
    # tolerance test on them is meaningless. The guard must fire before the
    # division by duration, which would otherwise be a division by zero.
    untimed = probe_media(clips["raw_h264"])
    result = compare_for_duplicate(untimed, untimed)
    assert result.verdict == "timing_unknown"
    assert result.fps_delta is None
    assert result.duration_delta is None


def test_an_explicit_tolerance_flips_the_verdict(rewrites: dict[str, Path]) -> None:
    left = probe_media(rewrites["origin"])
    right = probe_media(rewrites["matroska"])
    assert compare_for_duplicate(left, right).verdict == "duplicate"
    strict = compare_for_duplicate(left, right, fps_tolerance=1e-12)
    assert strict.verdict == "different_timing"
    assert strict.fps_tolerance == 1e-12


def test_facts_without_a_digest_report_unminted_not_duplicate(
    clips: dict[str, Path],
) -> None:
    # The regression this guard exists for. Two unrelated recordings, each with
    # no minted digest but matching timing -- the shape of every fact row
    # persisted before these fields existed, and of facts hand-built for a
    # source the probe never saw. Without step 0 both reach the tolerance
    # comparison and come back "duplicate".
    probed = probe_media(clips["cfr_mp4"])
    left = dataclasses.replace(probed, content_digest="", video_uuid="")
    right = dataclasses.replace(
        probed, content_digest="", video_uuid="", width=1920, height=1080
    )
    assert left.timing_measured is True
    assert right.timing_measured is True
    assert left.fps == right.fps
    assert left.duration == right.duration
    result = compare_for_duplicate(left, right)
    assert result.verdict == "unminted"
    assert result.fps_delta is None
    assert result.duration_delta is None


def test_one_side_without_a_digest_reports_unminted(clips: dict[str, Path]) -> None:
    # Asymmetric case: a freshly probed file compared against a stored row that
    # predates the fields. Order must not matter.
    probed = probe_media(clips["cfr_mp4"])
    stale = dataclasses.replace(probed, content_digest="", video_uuid="")
    assert compare_for_duplicate(probed, stale).verdict == "unminted"
    assert compare_for_duplicate(stale, probed).verdict == "unminted"


def test_a_duplicate_carries_its_deltas_and_applied_tolerances(
    rewrites: dict[str, Path],
) -> None:
    # The spec requires deltas on both compared verdicts, not only on the
    # failing one, so a caller can report how close a duplicate was.
    left = probe_media(rewrites["origin"])
    result = compare_for_duplicate(left, probe_media(rewrites["matroska"]))
    assert result.verdict == "duplicate"
    assert result.fps_delta is not None
    assert result.duration_delta is not None
    # Omitting a tolerance must use the derived value, not some other default.
    assert result.fps_tolerance == timing_tolerance(left.fps, left.duration)
    assert result.duration_tolerance == timing_tolerance(left.duration, left.duration)


def test_timing_tolerance_scales_inversely_with_duration() -> None:
    short = timing_tolerance(30.0, 1.0)
    long = timing_tolerance(30.0, 60.0)
    assert short == 60.0 * long
    # Comparing duration against itself yields the flat one-quantum figure.
    assert timing_tolerance(7.5, 7.5) == timing_tolerance(100.0, 100.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/probe/test_duplicate.py -v
```

Expected: FAIL with `ImportError: cannot import name 'compare_for_duplicate' from 'mosaic_media'`.

- [ ] **Step 3: Extend the identity module**

Add `from typing import Literal` and `from .facts import MediaFacts` to the
imports.

Put `TIMESTAMP_QUANTUM_SECONDS`, `DRIFT_SAFETY`, and `DuplicateVerdict` in the
module's existing constant block at the top, beside `CONTENT_FORMAT_TAG`,
`VIDEO_FORMAT_TAG`, and `DIGEST_BYTES` -- not at the bottom, or the file ends up
with constants in two places. `timing_tolerance`, `DuplicateComparison`, and
`compare_for_duplicate` go at the end, after `mint_identity`.

```python
# The coarsest timestamp granularity in common containers: Matroska stores
# durations on a millisecond scale, so a rewrite into it requantizes every
# timestamp to 1 ms.
TIMESTAMP_QUANTUM_SECONDS = 0.001

# Margin over the drift that quantization actually produces. Measured across
# mp4, Matroska, MPEG-TS, faststart, and a Matroska round trip, the observed
# drift ran 0.27 to 0.38 of one quantum per file second, so 4.0 leaves roughly a
# 10x margin. Provisional until re-measured across a real corpus.
DRIFT_SAFETY = 4.0

DuplicateVerdict = Literal[
    "duplicate", "different_timing", "timing_unknown", "unminted", "distinct"
]


def timing_tolerance(value: float, duration: float) -> float:
    """The absolute tolerance for `value` on a file of `duration` seconds.

    Absolute, but derived per file rather than fixed. The drift a container
    rewrite introduces is quantization noise averaged over the file, so it
    scales as one quantum per file second: a single constant for the whole
    corpus is either too tight for long files or too loose for short ones.

    Comparing `duration` against itself collapses this to a flat
    `DRIFT_SAFETY * TIMESTAMP_QUANTUM_SECONDS`, which is the intended behavior.
    """
    return DRIFT_SAFETY * value * TIMESTAMP_QUANTUM_SECONDS / duration


@dataclass(frozen=True, slots=True)
class DuplicateComparison:
    """Why two probed files are, or are not, the same video.

    Deltas are absolute differences and tolerances are the values actually
    applied, derived or overridden. Both are None for `"distinct"` and
    `"timing_unknown"`, where no comparison ran. The applied tolerance travels
    with the delta so a caller can explain a verdict without recomputing it.
    """

    verdict: DuplicateVerdict
    fps_delta: float | None
    duration_delta: float | None
    fps_tolerance: float | None
    duration_tolerance: float | None


def compare_for_duplicate(
    left: MediaFacts,
    right: MediaFacts,
    *,
    fps_tolerance: float | None = None,
    duration_tolerance: float | None = None,
) -> DuplicateComparison:
    """Decide whether two probed files are the same video.

    This is the only supported way to ask. Never compare `video_uuid` for it:
    that value moves on a container rewrite, so it answers "the same bytes at
    the same timing", not "the same video".

    Takes `MediaFacts` rather than a digest and loose floats because the two
    must come from the same probe; a signature accepting them separately would
    allow pairing one file's digest with another's timing.

    `left` is the reference -- both tolerances are derived from it -- so the
    result is not symmetric when the two durations differ. A caller sweeping a
    group holds `left` fixed.

    Each tolerance is an absolute value in its own unit, frames per second and
    seconds. None derives it from `timing_tolerance`, which is correct for any
    caller without a specific reason to override.

    `start_time` is deliberately not compared: it is a container property, not a
    content one. A rewrite into MPEG-TS moves it by over a second on content
    that did not change, because that container starts its clock at the first
    program clock reference.
    """
    if not (left.content_digest and right.content_digest):
        # Not defensive: without this the function reports false duplicates.
        # An absent digest is the empty string, so two facts that both lack one
        # compare EQUAL below, clear the timing guard whenever both were built
        # with timing_measured=True, and reach the tolerance comparison -- where
        # two unrelated recordings at the same rate and length come back
        # "duplicate". Facts persisted before these fields existed have that
        # shape, and so do facts hand-built for a source the probe never saw.
        #
        # "unminted" rather than "distinct": the two may well be the same, and
        # the caller is told which input to fix rather than given an answer the
        # function cannot support.
        return DuplicateComparison(
            verdict="unminted",
            fps_delta=None,
            duration_delta=None,
            fps_tolerance=None,
            duration_tolerance=None,
        )
    if left.content_digest != right.content_digest:
        return DuplicateComparison(
            verdict="distinct",
            fps_delta=None,
            duration_delta=None,
            fps_tolerance=None,
            duration_tolerance=None,
        )
    if not (left.timing_measured and right.timing_measured):
        # A guard, not a comparison. Both floats are 0.0 placeholders on an
        # untimed stream, so the tolerance test is meaningless and the division
        # by duration below is undefined.
        return DuplicateComparison(
            verdict="timing_unknown",
            fps_delta=None,
            duration_delta=None,
            fps_tolerance=None,
            duration_tolerance=None,
        )
    applied_fps_tolerance = (
        timing_tolerance(left.fps, left.duration)
        if fps_tolerance is None
        else fps_tolerance
    )
    applied_duration_tolerance = (
        timing_tolerance(left.duration, left.duration)
        if duration_tolerance is None
        else duration_tolerance
    )
    fps_delta = abs(left.fps - right.fps)
    duration_delta = abs(left.duration - right.duration)
    within = (
        fps_delta <= applied_fps_tolerance
        and duration_delta <= applied_duration_tolerance
    )
    return DuplicateComparison(
        verdict="duplicate" if within else "different_timing",
        fps_delta=fps_delta,
        duration_delta=duration_delta,
        fps_tolerance=applied_fps_tolerance,
        duration_tolerance=applied_duration_tolerance,
    )
```

- [ ] **Step 4: Export from the facade**

In `src/mosaic_media/__init__.py`, add the import alongside the other probe
imports:

```python
from .probe.identity import (
    DRIFT_SAFETY,
    TIMESTAMP_QUANTUM_SECONDS,
    DuplicateComparison,
    DuplicateVerdict,
    compare_for_duplicate,
    timing_tolerance,
)
```

Add these names to `__all__`, keeping it sorted as the existing list is:
`"DRIFT_SAFETY"`, `"DuplicateComparison"`, `"DuplicateVerdict"`,
`"TIMESTAMP_QUANTUM_SECONDS"`, `"compare_for_duplicate"`, `"timing_tolerance"`.

`Identity`, `mint_identity`, the format tags, and the encoding helpers are not
exported: a consumer reading them is reimplementing the digest.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/probe/test_duplicate.py -v
```

Expected: PASS, all six tests.

- [ ] **Step 6: Verify and commit**

```bash
uv run ruff format src/ tests/
uv run ruff check src/ tests/
uv run basedpyright src/ tests/
uv run pytest tests/probe/ -v
```

```bash
git add src/mosaic_media/probe/identity.py src/mosaic_media/__init__.py tests/probe/test_duplicate.py
git commit -m "Compare two probed files for duplication behind one exported function"
```

---

### Task 5: The compare command

**Files:**
- Modify: `src/mosaic_media/cli/application.py`
- Test: `tests/cli/test_compare.py`

**Interfaces:**
- Consumes: `compare_for_duplicate` and `DuplicateComparison` from Task 4.
- Produces: the `mosaic-media compare` command.

**Context.** Existing commands exit 1 on a probe failure. Click reserves exit
code 2 for a usage error such as a mistyped option, and typer surfaces that
unchanged, so no verdict may use 2 -- a script could not tell "these are
different videos" from "you typed the flag wrong".

Both options are optional floats, annotated `Annotated[float | None,
typer.Option(...)] = None`. At runtime `float | None` is a `types.UnionType`
rather than `typing.Optional`, and typer versions differ in whether their click
parameter conversion accepts it. No existing parameter in
`cli/application.py` is optional, so there is no local precedent.

Verified working on typer 0.21.1 / click 8.4.2: the option binds to `None` when
omitted and to the parsed float when supplied, `typer.Exit(code=6)` raised after
`typer.echo` emits the output *and* sets the code, and an unrecognized flag
exits 2. Confirm it still holds on the installed version before writing the rest
of the command:

```bash
uv run --extra cli python -c "
from typer.testing import CliRunner
from mosaic_media.cli import app
print(CliRunner().invoke(app, ['--help']).exit_code)
"
```

If a future typer rejects the union, stop and report rather than working around
it: the fallback (a sentinel float such as a negative value standing in for
None) changes the exported signature the spec fixes, so it is a spec question,
not an implementation choice.

- [ ] **Step 1: Write the failing tests**

Create `tests/cli/test_compare.py`. `tests/cli/test_cli.py` imports `app` from
`mosaic_media.cli` (the facade, not `.application`) and defines a `combined`
helper that joins stdout and stderr, because `Result.output` alone is
stdout-only and click's stderr handling varies by version. Match that module:
use `combined(result)` for any error-message assertion and `result.output` for
the JSON, exactly as it does.

```python
import json
from pathlib import Path

from typer.testing import CliRunner

from mosaic_media.cli import app

runner = CliRunner()


def test_compare_exits_zero_on_a_duplicate(rewrites: dict[str, Path]) -> None:
    result = runner.invoke(
        app, ["compare", str(rewrites["origin"]), str(rewrites["faststart"])]
    )
    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["verdict"] == "duplicate"


def test_compare_exits_three_on_distinct(clips: dict[str, Path]) -> None:
    result = runner.invoke(
        app, ["compare", str(clips["cfr_mp4"]), str(clips["vp8_webm"])]
    )
    assert result.exit_code == 3, result.output
    assert json.loads(result.output)["verdict"] == "distinct"


def test_compare_exits_four_on_different_timing(rewrites: dict[str, Path]) -> None:
    result = runner.invoke(
        app, ["compare", str(rewrites["origin"]), str(rewrites["retimed"])]
    )
    assert result.exit_code == 4, result.output
    assert json.loads(result.output)["verdict"] == "different_timing"


def test_compare_exits_five_on_timing_unknown(clips: dict[str, Path]) -> None:
    result = runner.invoke(
        app, ["compare", str(clips["raw_h264"]), str(clips["raw_h264"])]
    )
    assert result.exit_code == 5, result.output
    assert json.loads(result.output)["verdict"] == "timing_unknown"


def test_compare_exits_one_when_a_probe_fails(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    missing = tmp_path / "not_a_video.mp4"
    _ = missing.write_bytes(b"not a video")
    result = runner.invoke(app, ["compare", str(clips["cfr_mp4"]), str(missing)])
    assert result.exit_code == 1


def test_compare_honors_an_explicit_fps_tolerance(rewrites: dict[str, Path]) -> None:
    duplicate = runner.invoke(
        app, ["compare", str(rewrites["origin"]), str(rewrites["matroska"])]
    )
    assert duplicate.exit_code == 0, duplicate.output
    strict = runner.invoke(
        app,
        [
            "compare",
            str(rewrites["origin"]),
            str(rewrites["matroska"]),
            "--fps-tolerance",
            "1e-12",
        ],
    )
    assert strict.exit_code == 4, strict.output
    assert json.loads(strict.output)["fps_tolerance"] == 1e-12
```

The `clips` and `rewrites` fixtures resolve from
`tests/helpers/media_fixtures.py` through the root `conftest.py`'s
`pytest_plugins` list, so no local conftest is needed.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/cli/test_compare.py -v
```

Expected: FAIL, `compare` is not a command (exit code 2, "No such command").

- [ ] **Step 3: Add the command**

In `src/mosaic_media/cli/application.py`, add the import:

```python
from ..probe.identity import DuplicateVerdict, compare_for_duplicate
```

Add the exit-code mapping near `_PROFILES`:

```python
# The verdict is the exit code, so the command works as a shell test without
# parsing stdout. 1 stays the probe-failure code the other commands use, and 2
# is skipped because click already exits 2 on a usage error such as a mistyped
# option -- a verdict there would be indistinguishable from it.
_COMPARE_EXIT_CODES: dict[DuplicateVerdict, int] = {
    "duplicate": 0,
    "distinct": 3,
    "different_timing": 4,
    "timing_unknown": 5,
    # Unreachable here -- this command probes both files, so both always carry a
    # digest. Mapped anyway so the lookup is total over the verdict type and a
    # future caller cannot fall off it.
    "unminted": 6,
}
```

Add the command after `probe`:

```python
@app.command()
def compare(
    left: Path,
    right: Path,
    fps_tolerance: Annotated[
        float | None,
        typer.Option(
            "--fps-tolerance",
            help=(
                "Absolute frames-per-second tolerance. Omit to derive it from "
                "the reference file's duration, which is correct unless you "
                "have a specific reason to override it."
            ),
        ),
    ] = None,
    duration_tolerance: Annotated[
        float | None,
        typer.Option(
            "--duration-tolerance",
            help="Absolute duration tolerance in seconds. Omit to derive it.",
        ),
    ] = None,
) -> None:
    """Report whether LEFT and RIGHT are the same video, as JSON and an exit code.

    Probes both files. The ingestion pathway compares stored facts instead and
    never re-probes; this command takes paths for ad-hoc use.
    """
    try:
        left_facts = probe_media(left)
        right_facts = probe_media(right)
    except MediaProbeError as exc:
        message = f"probe failed: {exc}"
        typer.echo(message, err=True)
        raise typer.Exit(code=1) from exc

    comparison = compare_for_duplicate(
        left_facts,
        right_facts,
        fps_tolerance=fps_tolerance,
        duration_tolerance=duration_tolerance,
    )
    typer.echo(
        json.dumps(
            dataclasses.asdict(comparison),
            default=_json_default,
            indent=2,
            sort_keys=True,
        )
    )
    raise typer.Exit(code=_COMPARE_EXIT_CODES[comparison.verdict])
```

- [ ] **Step 4: Update the app help text and module docstring**

The `Typer` app's `help=` currently reads "Probe a video and run the minimum
ffmpeg transcode its verdict calls for." Extend it to name comparison.

The module docstring opens "Two commands mirror the library: `probe` prints
MediaFacts and both verdicts as JSON; `transcode` runs the minimum operation for
a target and honors the opt-out semantics." Rewrite it for three commands,
naming `compare` and stating that its verdict is also its exit code.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/cli/test_compare.py -v
```

Expected: PASS, all six tests.

- [ ] **Step 6: Verify and commit**

```bash
uv run ruff format src/ tests/
uv run ruff check src/ tests/
uv run basedpyright src/ tests/
uv run pytest tests/cli/ -v
```

```bash
git add src/mosaic_media/cli/application.py tests/cli/test_compare.py
git commit -m "Add a compare command reporting duplication as JSON and an exit code"
```

---

### Task 6: Transcode provenance

**Files:**
- Modify: `src/mosaic_media/transcode/convert.py`
- Test: `tests/transcode/test_convert.py`

**Interfaces:**
- Consumes: `MediaFacts.video_uuid` from Task 3.
- Produces: `TranscodeResult.source_video_uuid: str`.

**Context.** No content hash can link a derivative to its source: a transcode
changes the pixels and therefore every fact. The edge has to be recorded, so the
transcode carries it and the identity module only mints.

`run_transcode` constructs `TranscodeResult` at two sites: the no-op branch when
`build_command` returns None, and the success return.

- [ ] **Step 1: Write the failing tests**

`tests/transcode/test_convert.py` has a module-level `transcode(source, output,
target, encoding)` helper at line 62 that probes, derives, and calls
`run_transcode`. Use it. `faststart_mp4` is the fixture the two existing no-op
tests use, because it is already clean for both targets.

Add:

```python
def test_the_no_op_branch_records_the_source_video_uuid(
    clips: dict[str, Path], tmp_path: Path
) -> None:
    # The no-op branch returns every optional field as None, but this one is not
    # optional: it describes the input, and the input facts are in hand here.
    source = clips["faststart_mp4"]
    result = transcode(source, tmp_path / "out.mp4", "analysis", ANALYSIS_ENCODING)
    assert result.performed is False
    assert result.source_video_uuid == probe_media(source).video_uuid
    assert result.source_video_uuid != ""


@requires_svtav1
def test_a_performed_transcode_records_the_source_video_uuid(
    corpus_vfr: Path, tmp_path: Path
) -> None:
    # The derivative's own facts are freshly probed and share nothing with the
    # source's, which is exactly why the edge has to be carried rather than
    # recomputed.
    source_uuid = probe_media(corpus_vfr).video_uuid
    result = transcode(corpus_vfr, tmp_path / "out.mp4", "analysis", ANALYSIS_ENCODING)
    assert result.performed is True
    assert result.source_video_uuid == source_uuid
    assert result.output_facts is not None
    assert result.output_facts.video_uuid != source_uuid
```

`requires_svtav1` is the module's existing skip marker for tests that re-encode;
the variable-rate corpus fixture forces a real re-encode rather than a remux.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/transcode/ -k source_video_uuid -v
```

Expected: FAIL with `AttributeError: 'TranscodeResult' object has no attribute 'source_video_uuid'`.

- [ ] **Step 3: Add the field**

In `src/mosaic_media/transcode/convert.py`, add to `TranscodeResult`:

```python
    # The input's identity, carried because no hash of the output can recover
    # it: a transcode changes the pixels and therefore every measured fact.
    # Populated on the no-op branch too -- it describes the input, not the
    # output, and the input facts are in hand there.
    source_video_uuid: str
```

Append it as the last field. Both construction sites pass every field by
keyword, so position does not affect them, and appending keeps the diff to one
added line per site.

- [ ] **Step 4: Populate both construction sites**

In the no-op branch:

```python
    if command is None:
        return TranscodeResult(
            performed=False,
            operation=None,
            output_path=None,
            output_facts=None,
            output_verdict=None,
            reasons_addressed=frozenset(),
            residual_recommended=False,
            source_video_uuid=facts.video_uuid,
        )
```

And in the success return, add the same keyword argument.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/transcode/ -v
```

Expected: PASS. Every other `TranscodeResult` construction in tests must also
pass the new field; a required field with no default will surface them all.

- [ ] **Step 6: Verify and commit**

```bash
uv run ruff format src/ tests/
uv run ruff check src/ tests/
uv run basedpyright src/ tests/
```

```bash
git add src/mosaic_media/transcode/convert.py tests/transcode/
git commit -m "Record the source video uuid on the transcode result"
```

---

### Task 7: Documentation and the full run

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the identity section to the README**

`README.md` is this repository's user-facing authority. Add a section covering:

- The two values, as a table: what each pins, what each survives, what each is
  for, and what each must never be used for.
- That `video_uuid` moves on a container change and a directory named from it is
  not recoverable across one, not even by repacking back.
- That `content_digest` is not invariant across a bitstream reframing such as
  mp4 to MPEG-TS, and why: Annex B conversion changes the coded bytes, and
  canonicalizing that needs a per-codec bitstream parser the standard-library
  core cannot host.
- The duplicate pathway: group by `content_digest`, then `compare_for_duplicate`.
  Consumers never implement the tolerance test themselves.
- That the probe now reads every packet's payload, between 1.5x and 2x its
  previous scan cost, and that this is paid once at ingestion.
- That `-show_data_hash` needs no new ffprobe version: it first shipped in
  FFmpeg 2.4, well below the existing 5.1 runtime floor. Record it as covered by
  the current requirement, not as a new minimum.

Write it at the same altitude as the surrounding sections: what a reader needs
to adopt the package, not a restatement of the spec.

- [ ] **Step 2: Extend "Transcode semantics" and "CLI composition"**

"Transcode semantics" gains the identity split and `source_video_uuid`.
"CLI composition" gains the `compare` command with its exit-code table.

- [ ] **Step 3: Run the full suite**

```bash
heavy uv run pytest tests/
```

Expected: PASS.

- [ ] **Step 4: Run the performance gate**

```bash
heavy uv run pytest -m bench -n0 -s
```

Expected: PASS. The probe-cost benchmark is non-gating, so the added payload
read cannot fail this. If a gate does fail, apply the contention diagnosis --
check whether any change touched the measured path at the claimed magnitude,
compare absolute medians on both sides rather than the ratio alone, check system
load, and re-run the failed gates isolated at idle. Never tune a threshold;
report the numbers instead.

- [ ] **Step 5: Full verification and commit**

```bash
uv run ruff format src/ tests/
uv run ruff check src/ tests/
uv run basedpyright src/ tests/
```

```bash
git add README.md
git commit -m "Document the two identity values and the duplicate pathway"
```

---

## Spec coverage

| Spec section | Task |
| --- | --- |
| The two values, invariance table | 2, 3 |
| What `content_digest` does not survive | 2 |
| The two pathways, `compare_for_duplicate` | 4 |
| Tolerance, `timing_tolerance`, both constants | 4 |
| Payload from the demuxer, `-show_data_hash`, CRC32 | 1 |
| Digest defined against demuxer output | 2 (module docstring) |
| Minimum ffprobe version | Settled: FFmpeg 2.4, below the existing 5.1 floor. 7 records it. |
| Zero-length payloads | 1 (Context: hashed as scanned, no filter to be added) |
| Encoding primitives | 2 |
| `content_digest` input and exclusions | 2 |
| `video_uuid` input, UUIDv8 by hand | 2 |
| Collision budget, including the not-a-security-primitive warning | 2 (module docstring) |
| `MediaFacts` fields | 3 |
| `Packet.data_hash` | 1 |
| `scan_packets` changes | 1 |
| `identity.py`, import guard | 2, 3 |
| Facade exports | 4 |
| CLI `compare`, exit codes | 5 |
| `TranscodeResult.source_video_uuid` | 6 |
| README | 7 |
| Cost, scan timeout | 1 (step 8), 7 |
| Every test row in the spec's test table | 1-6 |

## Open items carried from the spec

Both are recorded in the spec as open; neither blocks implementation, and
neither may be silently resolved.

- `DRIFT_SAFETY = 4.0` rests on synthetic fixtures on one ffmpeg build. It is
  provisional until re-measured across a real corpus.
- The Cost figures are provisional: two serialized runs on one machine disagreed
  by 25%, in the signature of an I/O-bound measurement.
