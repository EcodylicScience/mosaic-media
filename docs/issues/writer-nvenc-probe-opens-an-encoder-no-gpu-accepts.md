# The writer's NVENC probe opens a 16x16 encoder that no GPU accepts

## Problem

`FFmpegVideoWriter` (`src/mosaic_media/io/writer.py`) takes `av1_nvenc` when
`hwaccel=True` and `_nvenc_encoder_usable()` is true, and `libsvtav1` otherwise. The
probe opens an `av1_nvenc` codec context at 16x16 and treats any `FFmpegError` as "not
usable":

```python
context = CodecContext.create("av1_nvenc", "w")
context.width = 16
context.height = 16
...
context.open()
```

NVENC refuses a frame that small, so `open()` raises `avcodec_open2("av1_nvenc") returned
22` (EINVAL) on a machine that can encode AV1. The probe answers False everywhere, the
result is cached for the process, and the writer never selects `av1_nvenc`.

Measured 2026-10-17 on an RTX 4060 (Ada, driver 580.126, AV1 NVENC capable), with av 18.1.0
and av 19.0.1 from PyPI, which bundle libavcodec 62.28 and 63.1:

| Check | Result under both av versions |
| --- | --- |
| `_nvenc_encoder_usable()` | `False` |
| `CodecContext.create("av1_nvenc", "w")` opened at 16x16 | EINVAL (22) |
| the same context opened at 256x256 | opens |
| system `ffmpeg -c:v av1_nvenc` on a 1280x720 test source | encodes |
| writer with the probe bypassed (`_nvenc_usable_cache = True`), 1920x1080, 120 frames | `encoder_name == "av1_nvenc"`, all frames decode, output size within 1 byte across the two av versions |
| writer unmodified, `hwaccel=True` | `encoder_name == "libsvtav1"` |

On Pascal cards (GTX 1080 Ti) `av1_nvenc` cannot open at any size, so the probe is right
there and the fallback is correct. The defect shows only on a GPU that can encode AV1.

`hwaccel.py` already knows this. Its subprocess probe encodes a 256x256 frame, and its
comment says NVENC's minimum encode size is largest for AV1 and a smaller frame gives a
false negative. That fix did not reach the second probe in `writer.py`.

## Scope

- In scope: `_nvenc_encoder_usable()` in `writer.py`, and a test that exercises a probe
  size an encoder can accept. `tests/io/test_writer.py` stubs the function, so no test
  reads its answer.
- Not in scope: `hwaccel.encoder_usable`, which already uses 256x256, and the transcode
  command builder.
- Not measured: the smallest size at which `av1_nvenc` opens. 256x256 is known good and 16x16
  known bad.

## Why deferred

Found while checking PyAV 19 against PyAV 18 on a GPU host. The two versions behave alike, so the
finding does not bear on that change. Fixing it changes output: a caller passing
`hwaccel=True` on a GPU host then gets NVENC-encoded files, whose bytes and quality differ from
the SVT-AV1 files the same call produces today and from the files a CPU-only host produces. The
choice among probe sizes, one shared probe or two, and how the writer records the encoder
belongs with that decision.

## What would close it

- On a machine whose GPU can encode AV1, `FFmpegVideoWriter(hwaccel=True)` reports
  `encoder_name == "av1_nvenc"`, and on one that cannot (Pascal, no GPU) it reports
  `libsvtav1`, pinned by a test that skips where no device exists.
- The probe size is stated beside the probe with the reason, as in `hwaccel.py`, or the writer
  asks `hwaccel.encoder_usable("av1_nvenc")` and the document says why a subprocess answer is
  enough for the in-process wheel (the wheel's bundled ffmpeg can differ from the PATH one).
- The README says that `hwaccel=True` output differs by host, or the encoder choice is made
  visible to callers so a derivative records which encoder wrote it.
