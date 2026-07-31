# Raw elementary stream frame rate

## Problem

A raw H.264 elementary stream (`.h264`) has no container and no packet
timestamps. `probe_media` detects this as a packet timestamp source of `"none"`
and reports `timing_measured=False`, with `fps`, `duration`, and
`constant_frame_rate` carrying `0.0`, `0.0`, and `False` as placeholders rather
than measurements; `frame_count` is the real packet count.

The package therefore has no measurement of such a stream's frame rate. Two
defects follow from that one gap.

### `declared_fps` carries a rate ffmpeg invented

`ffprobe` reports `avg_frame_rate=25/1` for every raw H.264 stream whatever its
content: the h264 demuxer's built-in default, not a claim the file makes.
`Header.declared_fps` takes that value (`src/mosaic_media/probe/ffprobe.py:236`)
and it reaches `MediaFacts.declared_fps` unchanged
(`src/mosaic_media/probe/probe.py:68`).

Measured on five generated streams, identical under ffprobe 6.1.1, 7.1, and 8.1:

| true rate | reported `avg_frame_rate` |
| --- | --- |
| 25 | `25/1` |
| 30 | `25/1` |
| 50 | `25/1` |
| 30000/1001 | `25/1` |
| 24000/1001 | `25/1` |

`_reencode_argv` falls back to `declared_fps` when `fps` is `0.0`
(`src/mosaic_media/transcode/commands.py:226`), which is exactly the raw-stream
case. A 30 fps raw stream therefore re-encodes under `-r 25.000000 -fps_mode
cfr` and is resampled to a rate the source never had, silently and with no
verdict reason recording it.

### Every copy remux of a raw stream relies on a deprecated muxer fallback

A copy remux hands the mp4 muxer packets whose timestamps are unset. The muxer
synthesizes them itself and logs

    Timestamps are unset in a packet for stream 0. This is deprecated and will
    stop working in the future. Fix your code to set the timestamps properly

The runner's `-v error` suppresses the message, so nothing surfaces.

This is not a regression at any FFmpeg version. Measured on
`tests/assets/raw.h264`, the current analysis command
(`-fflags +genpts -i S -c copy -movflags +faststart`, at
`src/mosaic_media/transcode/commands.py:261`) emits the warning and produces
byte-identical output under 6.1.1, 7.1, and 8.1 alike: `avg_frame_rate` of
`1000000/33333` on a time base of `1/1200000`. `-fflags +genpts` has never
generated demuxer-side timestamps for a raw H.264 stream on any version this
project targets; the muxer fallback has always done the work. The tracked issue
this specification closes records a FFmpeg 7.0 boundary for this path, and that
attribution is wrong -- correcting the record is part of closing it.

Both targets are affected, not one:

- **Analysis.** `unreliable_timing_metadata` selects `REMUX_TIMEBASE`
  (`src/mosaic_media/transcode/commands.py:145`).
- **Playback.** A raw stream also fires `unsupported_container`, which selects
  `REMUX_CONTAINER` (`commands.py:150`). Its argv carries neither `+genpts` nor
  any timestamp source. Measured under 8.1, that rewrap emits the same warning
  and declares the same invented `1000000/33333`.

The exposure is the wording: when the fallback is removed, both remuxes stop
producing timestamps rather than producing wrong ones, and the raw-stream path
breaks outright on an ffmpeg upgrade. Raw `.h264` files are a real input class --
tracking boxes record them, and the probe counts their frames by packet scan
precisely because no container can answer.

The synthesized rate is also inexact. Measured `avg_frame_rate` of today's
analysis command:

| source rate | declared `avg_frame_rate` | relative error |
| --- | --- | --- |
| 25 | `25/1` | none |
| 30 | `1000000/33333` | 1.0e-5 |
| 50 | `50/1` | none |
| 30000/1001 | `1000000/33367` | 1.0e-5 |
| 24000/1001 | `250000/10427` | 4.2e-6 |

## What the stream declares

The H.264 sequence parameter set carries `num_units_in_tick` and `time_scale` in
its video usability information. libavformat exposes their ratio as the stream's
`r_frame_rate`. H.264 counts two ticks per frame, so the frame rate is
`r_frame_rate / 2`.

Measured on the committed `tests/assets/raw.h264` and six generated streams,
identical under ffprobe 6.1.1, 7.1, and 8.1:

| true rate | `r_frame_rate` | `r_frame_rate / 2` |
| --- | --- | --- |
| 25 | `50/1` | 25 |
| 30 | `60/1` | 30 |
| 50 | `100/1` | 50 |
| 12.5 | `25/1` | 12.5 |
| 7 | `14/1` | 7 |
| 30000/1001 | `60000/1001` | 30000/1001 |
| 24000/1001 | `48000/1001` | 24000/1001 |

This is the only rate the file states about itself, and it is what the mp4
muxer's fallback already reads to build its approximation.

A stream whose sequence parameter set carries no timing has nothing to read.
libavformat then reports `r_frame_rate` as the demuxer time base echoed back --
measured `1200000/1` under all three versions -- which is not a frame rate.

## Design

Two changes: one in the probe, one in the transcode command builder.

### The probe carries the rate the elementary stream declares

`Header` gains one field, `elementary_stream_fps: float`. It is populated in
`read_header` as `r_frame_rate / 2` when the format name is `h264`, the codec
name is `h264`, and the halved value is positive and within the upper bound; it
is `0.0` otherwise -- for any containerized stream, for a non-h264 codec, for an
absent or zero-denominator `r_frame_rate`, and for a halved value above the
bound.

The format condition is what keeps the field honest rather than
precondition-bound. `r_frame_rate` means different things by demuxer: the raw
H.264 demuxer reports the sequence parameter set's tick rate, while a container
reports the container's own frame rate. Halving the latter yields half the true
rate -- measured `12.5` on a 25 fps mp4 and `15.0` on a 30 fps one. The format
name is `h264` for the raw demuxer alone, so gating on it means no value this
field ever carries is half of a real rate, and no reader has to remember a
condition to use it safely.

There is one upper bound, `1000.0`, applied to the halved value and to nothing
else. Its purpose is to reject the demuxer time base a timing-less stream echoes
back, which exceeds it by three orders of magnitude. There is deliberately no
lower bound: any positive rate is admitted, including the sub-one rates a
timelapse or long-observation recording uses -- measured, a 0.5 fps stream
reports `r_frame_rate=1/1` and derives `0.5`. The comparison against zero is the
absence convention, not a judgment about which rates are real.

`Header` is an internal structure, not persisted and not hashed; the field costs
no schema change anywhere.

`probe_media` then selects the value that reaches `MediaFacts.declared_fps`:

- Packet timestamp source is `"none"`: `header.elementary_stream_fps`.
- Otherwise: `header.declared_fps`, which is `avg_frame_rate`, unchanged.

The selection lives in `probe_media` rather than in `read_header` because the
timestamp source is `scan_packets`' return value and `read_header` runs before
it (`src/mosaic_media/probe/probe.py:21-22`). `read_header` keeps returning
`avg_frame_rate` in `declared_fps`, so its direct callers are unaffected.

The field's meaning does not change: `declared_fps` is what the file claims
about itself. For a container that claim is `avg_frame_rate`. For a timestamped
elementary stream today it is a number ffmpeg invented -- the value is not in
the file anywhere -- which is the anomaly this removes. Afterwards the field
means the same thing in both cases, and for a stream with no packet timestamps
no value it carries is a guess: either the stream declared a rate and the field
reports it, or it did not and the field is `0.0`.

Absence is `0.0`, already this dataclass's convention for a raw stream (`fps`
and `duration` carry it) and already handled by both readers of the field.
`_timing_metadata_lies` guards on `declared_fps > 0.0`
(`src/mosaic_media/probe/verdict.py:46`) and returns early on `fps <= 0.0`
anyway, so the declared-against-measured comparison is inert for a raw stream
either way. `_reencode_argv` omits `-r` entirely when neither rate is positive,
which its comment already documents as keeping the input timing.

The format condition is what carries the codec correctness, including if the
extension set grows. Two ticks per frame is an H.264 convention: a raw HEVC
stream reports `r_frame_rate=30/1` for 30 fps content, one tick per frame, so
halving it would be wrong by half. Were `.hevc` to join `VIDEO_EXTENSIONS`
(`src/mosaic_media/probe/candidates.py:22`), its format name would be `hevc` and
the format condition would reject it before any halving.

The `codec_name == "h264"` condition is therefore redundant, and is kept
deliberately rather than removed. It states, at the point of the halving, the
precondition the halving depends on, and it becomes load-bearing the moment
anyone widens the format condition -- which is exactly the edit where losing it
would silently halve a rate. Redundancy that documents a precondition is worth
its cost; the reason it is redundant belongs here so no reader mistakes it for
the guard doing the work.

### A copy remux of a timestamp-less source sets its own timestamps

Where `timing_measured` is `False` and `declared_fps` is positive, every copy
remux carries `-bsf:v setts=ts=N/<rate>/TB`. `setts` computes each packet's
timestamp from its frame index, so the packets reach the muxer timestamped and
the fallback is never entered.

This covers both operations that a timestamp-less source can select:

| operation | target | argv when the gate passes |
| --- | --- | --- |
| `REMUX_TIMEBASE` | analysis | `-i S -c copy -bsf:v setts=ts=N/<rate>/TB -movflags +faststart D` |
| `REMUX_CONTAINER` | playback | `-i S -c copy -bsf:v setts=ts=N/<rate>/TB -movflags +faststart D` |

`-fflags +genpts` is dropped from this form. Measured under 8.1, `setts` alone
produces zero warnings and an exact `30/1` on `tests/assets/raw.h264`: once
`setts` sets the timestamps there is nothing for `+genpts` to do.

Where the gate does not pass, every argv is exactly today's: `REMUX_TIMEBASE`
keeps `-fflags +genpts`, `REMUX_CONTAINER` keeps the plain copy, and
`REMUX_FASTSTART` is untouched.

Measured, seven rates by three FFmpeg versions, zero `Timestamps are unset`
warnings in all 21 combinations:

| source rate | declared `avg_frame_rate` | relative error |
| --- | --- | --- |
| 25 | `25/1` | none |
| 30 | `30/1` | none |
| 50 | `50/1` | none |
| 12.5 | `25/2` | none |
| 30000/1001 | `30000/1001` | none |
| 24000/1001 | `24000/1001` | none |
| 7 | `8000000/1142857` | 6.1e-8 |

No output time base is set. The raw h264 demuxer's fixed `1/1200000` divides
evenly by every standard frame rate, so the computed timestamps land on exact
ticks; 7 fps is the one listed rate that does not divide it, and it still lands
within 6.1e-8, two orders of magnitude closer than today's fallback manages for
30 fps. Forcing an output time base derived from the rate is what makes the
result inexact: measured, `-video_track_timescale 29970` on a 30000/1001 source
declares `2997/100`, a rate the source never had.

The rate is rendered into the argv with `f"{rate:.6f}"`, matching the sibling
`_reencode_argv` (`src/mosaic_media/transcode/commands.py:228`). Six decimals
and full float precision were measured to produce identical output for every
rate in the table above.

### Why the rate must be the stream's own

The mp4 muxer derives `avg_frame_rate` from the packet durations `setts`
produces, so the result is exact only when the rate given to `setts` is the rate
the packets were actually coded at. A mismatch does not degrade gracefully:
measured, forcing 23.976024 onto a 30000/1001 stream declares `4800000/199199`,
a 0.5 percent error, far worse than the fallback it replaced.

This is the same mechanism as the gate on `timing_measured`, and the reason that
gate is on `timing_measured` rather than on `declared_fps` alone. `REMUX_TIMEBASE`
is not the raw-stream operation: `_timing_metadata_lies` also selects it, for a
timestamped container file whose header rate disagrees with its measured rate
(`src/mosaic_media/probe/verdict.py:107`, exercised by
`test_lying_header_source_remuxes_with_a_corrected_timebase`). On such a file
`declared_fps` is by definition not the packet rate -- it is the lie the remux
exists to correct -- so applying `setts` at that rate would write the lie into
the timestamps as fact. The gate must exclude every file whose timing was
measured.

It also reconciles this design with the tracked issue it closes, which records
`-bsf:v setts=ts=N/25/TB` as measured and rejected for writing a wrong average
rate of `9000/359`. That trial forced 25 onto a 30 fps stream: the mismatch case.
Sourcing the rate from the stream's own sequence parameter set is what the
earlier trial lacked.

## Identity is unaffected

`declared_fps` is hashed into neither derived identity value.
`content_digest_input` covers the content format tag, `codec_name`,
`pixel_format`, the three color fields, `width`, `height`, `rotation_degrees`,
`square_pixels`, `progressive`, the packet count, and each packet's size,
keyframe flag, and payload hash. `video_uuid_input` adds the video format tag,
the content digest bytes, `timing_measured`, the packet count, and each packet's
time (`src/mosaic_media/probe/identity.py`). `mint_identity` takes only the
header, the packets, and `timing_measured`.

Changing how `declared_fps` is derived therefore re-mints nothing, needs no
`IDENTITY_SCHEME` bump, and moves no consumer's stored identity values. Adding
`elementary_stream_fps` to `Header` is likewise inert: `content_digest_input`
enumerates the fields it hashes explicitly rather than iterating the structure.

## Documentation corrections

Establishing what a raw stream declares required measuring the encoder sets of
four FFmpeg builds. `tests/assets/README.md` states them wrongly, in the
paragraph that explains why the clips this work adds to are committed rather
than generated. It is corrected here because this work adds a clip to that set.

### Which software encoders exist

`tests/assets/README.md` says every H.264 encoder besides `libx264` and
`libx264rgb` is a hardware wrapper. Measured from full encoder listings:

| build | software H.264 encoders |
| --- | --- |
| standalone 8.1, `--enable-gpl` | `libx264`, `libx264rgb`, `libopenh264` |
| deployment image, no `--enable-gpl` | `libopenh264` |
| Ubuntu 24.04 system 6.1.1 | `libx264`, `libx264rgb` |

`libopenh264` is a software encoder under a non-GPL license, so the claim is
false as written.

### Why the clips stay committed

The committed clips stay committed, for a reason the assets README does not
currently give: the Ubuntu 24.04 system ffmpeg the suite runs against on both
development machines carries no `libopenh264`, so generating H.264 at test time
would fail there. The new clip is produced the same way as every existing one.

The existing corpus is not regenerated. Different encoders produce different
bitstreams, so regenerating would re-mint every fixture's `video_uuid` and
`content_digest` and move the golden digest pinned in the deployment image.

## Scope

Affected:

- `src/mosaic_media/probe/ffprobe.py` -- the new `Header.elementary_stream_fps`
  field and its derivation.
- `src/mosaic_media/probe/probe.py` -- the selection between the declared rate
  and the elementary stream rate.
- `src/mosaic_media/probe/facts.py` -- the `MediaFacts` docstring, which
  currently states that `declared_fps` is `avg_frame_rate` and that
  `r_frame_rate` "is not stored". Both become false for a stream with no packet
  timestamps and must be rewritten to state the two-case derivation.
- `src/mosaic_media/transcode/commands.py` -- the `setts` expression on both
  copy-remux operations. The re-encode rate fallback is corrected by the probe
  change alone and needs no edit here.
- `tests/assets/` and `tests/assets/README.md` -- one added clip and its recipe,
  and the encoder-availability correction above.
- `tests/helpers/media_fixtures.py` -- the added clip's entry in the `AssetName`
  literal alias and its registration in the `clips` fixture.
- `docs/issues/raw-stream-remux-relies-on-a-deprecated-muxer-fallback.md` --
  closed by this work, its FFmpeg 7.0 attribution corrected in the closing.

Not affected: every source that arrives with packet timestamps. Both changes are
gated on the absence of those timestamps, so a container file's probe output and
every container file's remux argv are byte-identical to today. No layer boundary
is crossed and no dependency is added: every touched module is core, standard
library only.

`README.md` is deliberately untouched here. Separate work following this branch
covers it, with its own design.

## Testing

The rate derivation is unit-tested against synthetic probe payloads: an integer
rate, a fractional rate, a sub-one rate that must be admitted rather than
discarded, a timing-less stream whose halved `r_frame_rate` exceeds the upper
bound, an absent and a missing `r_frame_rate`, a
containerized H.264 stream whose field must read `0.0`, a raw HEVC stream that
the format condition rejects before the codec condition is reached, and a
container stream whose derivation must not change.

The command builder is unit-tested for every branch of the gate: a
timestamp-less source with a positive rate produces the `setts` expression on
both `REMUX_TIMEBASE` and `REMUX_CONTAINER`; a timestamp-less source with a rate
of `0.0` produces today's argv; and a measured-timing source produces today's
argv on both operations. The lying-header case is asserted explicitly, because
it is the one that silently corrupts if the gate is written wrongly.

End to end, against real clips:

- `raw.h264` (30 fps, committed) probes `declared_fps` at 30 rather than 25, and
  its analysis remux declares `avg_frame_rate` of exactly `30/1` where today it
  declares `1000000/33333`. Its playback rewrap declares the same.
  `test_raw_h264_remuxes_into_measured_timing` continues to pass: the remux
  still yields `timing_measured=True`, `constant_frame_rate=True`, and the
  source's frame count.
- A new committed clip at 30000/1001 pins that a fractional rate survives the
  float round trip and declares exactly `30000/1001`. This is the arithmetic the
  integer rates do not exercise.
- One test runs the built command at `-v warning` and asserts the string
  `Timestamps are unset` is absent from its output. The issue's first acceptance
  criterion requires the deprecation to be verified gone by its absence rather
  than by the output alone, and no assertion on the output can establish that.

No existing test asserts the old raw-stream derivation:
`tests/probe/test_ffprobe.py:48` covers a container, whose behavior is unchanged.
The tests that change are those asserting the old argv, and they are rewritten to
assert the new one rather than removed.

## Residual limitations

A raw stream carrying no sequence parameter set timing keeps `declared_fps` at
`0.0` and still routes to the deprecated fallback, so it still breaks when that
fallback is removed. Nothing can recover a rate the file does not state; when
the fallback goes, such a stream turns a silently guessed rate into a visible
failure, which is the better of the two outcomes. This is the bounded remainder
of the closed issue and is recorded in the code rather than left to be
rediscovered.

One adoption precondition. `MediaFacts` minted for a raw elementary stream
before this change carry `timing_measured=False` with `declared_fps=25.0`, the
fabricated demuxer default rather than anything read from the file. Such facts
would select the timestamp-setting remux and write 25 fps timestamps into
content of whatever rate it actually is. In every environment this package has
been used in to date the set of such rows is empty -- no raw elementary stream
has been probed into a persisted index -- so the condition is empty rather than
mitigated, and every row minted from here carries a bitstream-derived rate or
`0.0`. An adopter cannot verify that for their own environment, which is what
the next paragraph is for.

An environment that did accumulate such rows re-probes its raw elementary
streams before enabling the transcode. Re-probing moves no identity value --
neither `video_uuid` nor `content_digest` depends on `declared_fps` -- so
nothing else about those rows changes.

The timestamps are written in decode order, which is presentation order only
for a stream coded without frame reordering. A raw stream coded with
bidirectionally predicted frames therefore receives timestamps shuffled
relative to the pictures they label, and the acceptance re-probe cannot see it:
the values remain uniform, complete, and starting at zero. This is not a
regression -- the muxer fallback produced the same ordering and a negative
start time besides -- and it has not been observed in the raw elementary
streams this package has seen, which are coded without reordering because the
Baseline and Constrained Baseline profiles forbid it. That is a property of
those profiles rather than of capture hardware generally: a device emitting
Main or High profile can reorder.
Correcting it means routing such a source to a re-encode, since only a decode
recovers presentation order; that is tracked separately because it costs a
persisted-fact schema change and a new verdict reason.

The derivation trusts what the stream declares. A sequence parameter set stating
a rate the recording did not honor yields a `declared_fps` that is wrong in the
same way any header claim can be wrong -- which is what `declared_*` means, and
why the field is never treated as a measurement.
