"""Pin the identity values a known clip probes to, against the pinned FFmpeg.

`content_digest` folds in each packet's payload as libavformat hands it to
ffprobe, not as it sits on disk, so an FFmpeg upgrade that changes demuxer
output re-mints every value in the corpus with no code change and no byte of
any file different. That is a format break rather than a rebuild, and this
check turns it from something an unattended base-image rebuild causes silently
into a build that fails and says so.

The subject is a committed clip from this repository's test assets, chosen
because its bytes cannot shift: a generated file would move with the encoder
and confound the encoder's behavior with the demuxer's, which is the thing
under test.

Run with no EXPECTED_CONTENT_DIGEST to print the values this build produces;
set it to assert them.
"""

import os
import sys
from pathlib import Path

from mosaic_media import probe_media

SUBJECT = Path("/opt/mosaic_media/tests/assets/cfr.mp4")


def main() -> int:
    facts = probe_media(SUBJECT)
    print(f"subject:         {SUBJECT}")
    print(f"identity_scheme: {facts.identity_scheme}")
    print(f"content_digest:  {facts.content_digest}")
    print(f"video_uuid:      {facts.video_uuid}")

    expected = os.environ.get("EXPECTED_CONTENT_DIGEST", "").strip()
    if not expected:
        print("\nEXPECTED_CONTENT_DIGEST unset: reporting only, asserting nothing.")
        return 0
    if facts.content_digest != expected:
        moved = f"content_digest moved: expected {expected}, got {facts.content_digest}"
        why = (
            "The FFmpeg in this image demuxes differently from the one these "
            "values were recorded against. Every identity value in every corpus "
            "is re-minted by that change; treat it as a format break, not a "
            "rebuild."
        )
        print(f"\nFAIL  {moved}\n{why}", file=sys.stderr)
        return 1
    print(f"\nok  content_digest matches {expected}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
