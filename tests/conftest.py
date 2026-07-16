"""Shared test fixtures for the mosaic_media suite."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.helpers.corpus import generate_video

# The copied probe tests consume the generated media clips; pytest_plugins is
# honored only in the rootdir conftest. Preserved from plan 1.
pytest_plugins = ["tests.helpers.media_fixtures"]


@pytest.fixture(scope="session")
def corpus_gop12(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("corpus_gop12")
    yield generate_video(root / "gop12.mp4", frames=48, fps=30.0, gop=12)


@pytest.fixture(scope="session")
def corpus_gop250(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("corpus_gop250")
    yield generate_video(root / "gop250.mp4", frames=300, fps=30.0, gop=250)
