# Transcode provenance is minted and then dropped

`TranscodeResult.source_video_uuid` records which source a derivative came from.
Nothing persists it, so the value exists only for as long as the result object
does.

## Why the other two fields are fine and this one is not

`video_uuid` and `content_digest` are `MediaFacts` fields. A consumer that
persists facts as a serialized blob -- `json.dumps(dataclasses.asdict(facts))`
into one column, reconstructed with `MediaFacts(**json.loads(payload))` -- stores
and restores them with no schema change at all.

`source_video_uuid` is not a `MediaFacts` field, and cannot be one: it describes
a relationship between two files, not a measurement of either. It lives on
`TranscodeResult`. A consumer flattening a completed transcode into its media
index reads the derivative's facts and verdict and has no slot for a field
belonging to neither.

## What it is for, given the edge is already recorded

The consumer already persists this relationship as paths, in both directions: the
derivative row carries `source_path`, and the source row carries
`analysis_derivative_path` and `playback_derivative_path`.

`source_video_uuid` is the move-resilient form of that same edge, not a
replacement for it. A path breaks when the file behind it is moved, renamed, or
re-imported from a different mount -- which is precisely the case the identity
values exist to survive. Keeping both means the link degrades gracefully: the
path resolves fast in the common case, and the uuid still identifies the source
after the path stops resolving.

## What closing it needs

One column on the derivative row, written from
`TranscodeResult.source_video_uuid` where the rest of the row is built. No change
in this repository -- the value is already minted and already returned.

Existing rows backfill cleanly, because the edge is already on disk: for each
derivative row, follow `source_path` to the source row and copy that row's
`video_uuid`. A migration that re-probes to populate the identity fields has
everything it needs to fill this one in the same pass, in either order.

## Scope of the loss until then

Only move resilience. Provenance itself is not lost -- `source_path` answers the
question today. Duplicate detection and identity are untouched: both rest on
`MediaFacts` fields that already round-trip.
