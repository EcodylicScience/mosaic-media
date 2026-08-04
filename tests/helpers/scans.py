"""Counting packet scans, for tests that assert a path does not probe."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

import pytest

from mosaic_media.io.packets import scan_packets_in_process
from mosaic_media.probe.ffprobe import Packet, TimestampSource


@contextmanager
def count_packet_scans(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> Generator[Callable[[], int], None, None]:
    """Count `scan_packets_in_process` calls made through `module`.

    Patches the name where it is looked up rather than where it is defined, so
    each caller module is counted independently and a path crossing two of them
    is counted by nesting one of these per module.

    `monkeypatch` undoes the patch when the test ends rather than when the block
    exits, so a call made after the block still counts.

    The counting stand-in mirrors `scan_packets_in_process` and forwards to it
    by name rather than through the module attribute it replaces, so a renamed
    or newly required parameter is a type error here instead of a `TypeError`
    raised inside whichever test installed the count. A parameter added with a
    default still passes silently, which is why the signature is also mirrored
    by hand.
    """
    calls = 0

    def counting(
        path: Path, *, ignore_edit_list: bool = False
    ) -> tuple[tuple[Packet, ...], TimestampSource]:
        nonlocal calls
        calls += 1
        return scan_packets_in_process(path, ignore_edit_list=ignore_edit_list)

    monkeypatch.setattr(module, "scan_packets_in_process", counting)
    yield lambda: calls
