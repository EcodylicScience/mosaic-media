"""Read the first few ISOBMFF top-level boxes. Costs a few hundred bytes.

A file whose `moov` follows its `mdat` cannot begin playing until the player has
fetched the tail, which a `-movflags +faststart` remux fixes without re-encoding
a frame.
"""

import struct
from pathlib import Path

_HEADER_SIZE = 8
_LARGE_SIZE_MARKER = 1
_TO_END_OF_FILE_MARKER = 0
_MAX_BOXES = 16


def moov_at_start(path: Path) -> bool | None:
    """True when `moov` precedes `mdat`. None when the file is not ISOBMFF.

    Absence of `mdat` after `moov` is reported as True: a valid ISOBMFF file
    whose media data is external still begins with its index.
    """
    order: list[str] = []
    try:
        with path.open("rb") as handle:
            while len(order) < _MAX_BOXES:
                header = handle.read(_HEADER_SIZE)
                if len(header) < _HEADER_SIZE:
                    break
                size = struct.unpack(">I", header[:4])[0]
                box_type = header[4:8].decode("latin-1")
                order.append(box_type)
                if size == _LARGE_SIZE_MARKER:
                    extended = handle.read(8)
                    if len(extended) < 8:
                        break
                    size = struct.unpack(">Q", extended)[0]
                    payload = size - 16
                elif size == _TO_END_OF_FILE_MARKER:
                    break
                else:
                    payload = size - _HEADER_SIZE
                if "moov" in order and "mdat" in order:
                    break
                if payload < 0:
                    break
                _ = handle.seek(payload, 1)
    except OSError:
        return None

    if not order or order[0] != "ftyp" or "moov" not in order:
        return None
    if "mdat" not in order:
        return True
    return order.index("moov") < order.index("mdat")
