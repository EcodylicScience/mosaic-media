# Committed media assets

H.264 and HEVC clips the suite needs but can no longer generate.

FFmpeg's GPL software encoders for these codecs are `libx264`, `libx264rgb`, and
`libx265`, all external and GPL-2.0-or-later. The suite must run against the
same LGPL FFmpeg the consumers deploy, which rules out all three.

Two non-GPL software encoders exist -- `libopenh264` for H.264 and `libkvazaar`
for HEVC -- and neither rescues these clips. Neither is present in the Ubuntu
system FFmpeg the suite runs against on development machines, so neither can
generate a fixture there; and `libopenh264` is in any case built for real-time
conferencing, its rate control offering `off`, `quality`, `bitrate`, `buffer`
and `timestamp` with no constant-quality target, its input 8-bit 4:2:0 only
(`yuv420p` and `yuvj420p`), and its profiles stopping at High, which is not
what the deployment path wants for a permanent derivative. Every other encoder
for either codec is a hardware wrapper (`h264_nvenc`, `h264_qsv`,
`h264_v4l2m2m`, `h264_vaapi`, `hevc_nvenc`, `hevc_qsv`, `hevc_v4l2m2m`,
`hevc_vaapi`), so a machine without a suitable GPU has no way to produce these
clips, and they are committed instead. Decoding is unaffected either way --
FFmpeg's H.264 and HEVC decoders are both native and LGPL -- so these files
are consumed with no extra dependency.

Everything else the fixtures need is still generated at test time: `libvpx`,
`mjpeg`, `aac`, and `libsvtav1` are all non-GPL, and the `-c copy` remuxes
(`no_pts.avi`, `cfr.ts`, the rotated variant) need no encoder at all.

## Provenance

Generated with the FFmpeg below. The commands are the ones the fixtures used
before the files were committed, plus `-crf 40 -preset veryslow` for size: these
clips are probed for structure (codec, frame count, rate, GOP, `moov`
placement), never for picture quality.

    ffmpeg version 6.1.1-3ubuntu5 (Ubuntu, --enable-gpl)

```bash
DEFAULT="-f lavfi -i testsrc=size=320x240:rate=25:duration=2"
Q="-crf 40 -preset veryslow"

# cfr.mp4 -- 50 frames, 25 fps, GOP 25. The root clip; several fixtures remux it.
ffmpeg -v error -y $DEFAULT -c:v libx264 -pix_fmt yuv420p -g 25 $Q cfr.mp4

# faststart.mp4 -- as cfr.mp4 with the moov box relocated to the head.
ffmpeg -v error -y $DEFAULT -c:v libx264 -pix_fmt yuv420p -g 25 -movflags +faststart $Q faststart.mp4

# cfr_30fps.mp4 -- 60 frames, 30 fps, GOP 30.
ffmpeg -v error -y -f lavfi -i "testsrc=size=320x240:rate=30:duration=2" \
    -c:v libx264 -pix_fmt yuv420p -g 30 $Q cfr_30fps.mp4

# raw.h264 -- 60 frames, no container, no timestamps. -bf 0 keeps a later
# -c copy remux free of B-frame reordering, matching how tracking boxes record.
ffmpeg -v error -y -f lavfi -i "testsrc2=size=320x240:rate=30:duration=2" \
    -c:v libx264 -bf 0 -pix_fmt yuv420p -g 12 -f h264 $Q raw.h264

# raw_fractional_rate.h264 -- 60 frames at 30000/1001, no container, no
# timestamps. The fractional rate is what the integer-rate clips do not
# exercise: it survives the float round trip only if the declared rate is
# carried exactly.
ffmpeg -v error -y -f lavfi -i "testsrc2=size=320x240:rate=30000/1001:duration=2" \
    -c:v libx264 -bf 0 -pix_fmt yuv420p -g 12 -f h264 $Q raw_fractional_rate.h264

# anamorphic.mp4 -- 50 frames with a 10:11 sample aspect ratio, coded 320x240.
ffmpeg -v error -y $DEFAULT -c:v libx264 -pix_fmt yuv420p -vf setsar=10/11 $Q anamorphic.mp4

# audio.mp4 -- video plus an aac track. Its video is coded independently rather
# than copied from cfr.mp4: a copied track would carry cfr.mp4's identity
# values, and the identity tests require every distinct fixture to hash
# distinctly.
ffmpeg -v error -y -f lavfi -i "testsrc=size=320x240:rate=25:duration=2" \
    -f lavfi -i "sine=frequency=440:duration=2" \
    -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac $Q audio.mp4

# long_gop.mp4 -- 300 frames, 25 fps, GOP 250. Two keyframes in twelve seconds.
ffmpeg -v error -y -f lavfi -i "testsrc=size=320x240:rate=25:duration=12" \
    -c:v libx264 -pix_fmt yuv420p -g 250 $Q long_gop.mp4

# h264.avi -- 50 frames in a container Chrome cannot open, for the rewrap-not-
# reencode playback case. -bf 0 keeps the -c copy to mp4 clean.
ffmpeg -v error -y -f lavfi -i "testsrc2=size=320x240:rate=25:duration=2" \
    -c:v libx264 -bf 0 -pix_fmt yuv420p -g 25 $Q h264.avi

# h264_gop12.mp4 -- 48 frames, 30 fps, GOP 12: the generated corpus's shape in a
# codec OpenCV can decode. Used only by the OpenCV parity tests. This clip has
# no pre-existing fixture recipe; it was written for those tests.
ffmpeg -v error -y -f lavfi -i "testsrc2=size=320x240:rate=30:duration=2.6" \
    -frames:v 48 -c:v libx264 -pix_fmt yuv420p -g 12 $Q h264_gop12.mp4

# hevc.mp4 -- 50 frames, 25 fps, GOP 25. HEVC exists here for the same reason
# the H.264 clips do: libx265 is GPL, so the suite cannot encode it, but the
# native decoder is LGPL and reads it with no extra dependency. It backs two
# measurements, so every member of both shipped sets is measured rather than
# assumed: the trusted-codec delivery test, and the mp4 carriage test that muxes
# it with `-c copy`. hvc1 tagging is what players and browsers expect in mp4.
# This clip has no pre-existing fixture recipe; it was written for those tests,
# and removing either one does not make it unused.
ffmpeg -v error -y -f lavfi -i "testsrc2=size=320x240:rate=25:duration=2" \
    -c:v libx265 -pix_fmt yuv420p -g 25 -tag:v hvc1 $Q hevc.mp4

# open_gop.mp4 -- 50 frames, 25 fps, GOP 12, open GOP with B-frames. Every
# keyframe after the first is followed in decode order by pictures that precede
# it in presentation order (measured: the keyframe at pts 0.480 is followed by
# pts 0.400 and 0.440). That is the shape a decoder legitimately suppresses
# after a seek, so it is the case that decides whether emitting frames before
# the first keyframe is safe to enable unconditionally, and whether the reader's
# presentation-order keyframe resolution lands frame-exact where a naive seek to
# the nearest keyframe does not. scenecut=0 keeps the GOP boundaries where -g
# puts them, so the structure is deterministic rather than content-dependent.
# This clip has no pre-existing fixture recipe; it was written for those tests.
ffmpeg -v error -y -f lavfi -i "testsrc2=size=320x240:rate=25:duration=2" \
    -c:v libx264 -pix_fmt yuv420p -g 12 -bf 2 -x264-params open-gop=1:scenecut=0 \
    $Q open_gop.mp4
```

## Regenerating

These recipes need an FFmpeg built with `--enable-gpl`, because they name a GPL
encoder; the deployment image deliberately does not have it. Any regeneration
re-mints `video_uuid` and `content_digest` for these files, so the golden-digest
expectations move with them: treat it as a corpus change, not a refresh.
