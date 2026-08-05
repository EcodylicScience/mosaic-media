# Container demuxers register video suffixes that are not candidate videos

## Problem

`VIDEO_EXTENSIONS` is this package's statement of what counts as a candidate
video, and it holds a partial subset of what the corresponding demuxers register. For the two raw elementary
stream demuxers that gap is closed: every suffix `h264` and `hevc` register is
now in the set, and every one of them denotes a raw video stream, so taking the
full list admitted nothing that is not video.

The container demuxers are the remaining gap, and their full lists cannot be
taken the same way. Measured from the demuxers themselves:

| Demuxer | Registers | In the set | Absent |
| --- | --- | --- | --- |
| `mov` / `mp4` | mov, mp4, m4a, 3gp, 3g2, mj2, psp, m4b, ism, ismv, isma, f4v, avif | `.mp4`, `.mov` | eleven |
| `matroska` | mkv, mk3d, mka, mks, webm | `.mkv`, `.webm` | `.mk3d`, `.mka`, `.mks` |
| `m4v` | m4v | `.m4v` | none |
| `avi` | avi | `.avi` | none |

Of the absent suffixes, seven denote video and seven do not:

| Absent suffix | What it is | Evidence |
| --- | --- | --- |
| `.3gp`, `.3g2` | 3GPP mobile video | default video codec `h263` |
| `.f4v` | Flash-era mp4 variant | default video codec `h264` |
| `.ismv` | smooth streaming video | mime type `video/mp4`, default video codec `h264` |
| `.psp` | portable console video | default video codec `h264` |
| `.mj2` | Motion JPEG 2000 | video, no muxer metadata to quote |
| `.mk3d` | stereoscopic Matroska | video, no muxer metadata to quote |
| `.m4a`, `.m4b` | audio, and audiobook | the audio spellings the `ipod` muxer registers beside `m4v` |
| `.isma` | smooth streaming audio | the `ismv` muxer registers `ismv,isma` as the video and audio pair |
| `.mka` | Matroska audio | no muxer of that name; a demuxer-side alias only |
| `.mks` | Matroska subtitles | no muxer of that name |
| `.avif` | still image | mime type `image/avif`, a default video codec and no audio codec at all |
| `.ism` | a manifest, not media | no muxer produces one; the media is `.ismv` and `.isma` |

So a rule of "take every suffix the demuxer registers", which is correct for the
raw codecs, admits audio files, a subtitle file, a still image and a manifest
when applied to the containers. Any widening here has to classify rather than
enumerate.

The set was never purely demuxer-derived in the first place, which is worth
knowing before treating registration as the authority. Five of its container
entries -- `.mts`, `.m2ts`, `.mpg`, `.mpeg` and `.wmv` -- are registered by no
demuxer on this build at all: the transport stream, program stream and advanced
systems format demuxers print no extension line whatsoever and are reached by
content probing instead. So for five of the twelve container entries, "take the
full registered list" is an empty instruction rather than a wider one. What the
set actually encodes is what this stack expects to be handed.

## Scope

Affected: the seven absent video suffixes above. A file carrying one is a video
this package can probe and does not call a candidate.

Not affected:

- The two raw elementary stream codecs. Their lists are complete.
- `m4v` and `avi`, whose registered lists are already complete in the set.
- `.mts`, `.m2ts`, `.mpg`, `.mpeg` and `.wmv`, which are in the set by
  expectation rather than by registration and are unaffected either way.
- Anything inside this package. Candidacy is the only thing an extension
  decides; no measurement, verdict or command construction reads it.

## Why deferred

**None of the seven has been observed.** That is why they are absent rather than
overlooked: the set records what this stack has actually been handed, which is
also why `.h264` is in it and why five members are there despite their demuxers
registering no extension at all. A suffix nobody has produced is a candidacy
claim with nothing behind it.

The work that surfaced this does not settle it either. The raw codec lists were
widened because a rate derivation had made a codec measurable but not
admissible, an asymmetry inside this package. Nothing here is asymmetric in that
way: these seven are neither measured nor claimed, which is consistent.

## What would close it

- Each of the seven is either in `VIDEO_EXTENSIONS` or recorded as deliberately
  excluded, with the reason stated where the set is defined.
- The seven non-video suffixes are named as excluded and why, so a later reader
  reaching for the demuxer's full list finds the classification already done
  rather than repeating it.
- A test pins the exclusions, not only the inclusions: a suffix wrongly called a
  candidate is a file this package invites and then cannot probe.
- Whichever way it goes, the comment where the set is defined stops implying
  that registration decides membership, since five current members contradict
  that.
