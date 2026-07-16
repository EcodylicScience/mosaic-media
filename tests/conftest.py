# The generated media fixtures (clips, truncated_faststart, long_gop_clip) are
# registered as a plugin from the top-level conftest, because pytest honors
# pytest_plugins only there.
pytest_plugins = ["tests.helpers.media_fixtures"]
