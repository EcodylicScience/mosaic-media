# syntax=docker/dockerfile:1
#
# FFmpeg without the GPL-only encoders, and PyAV linked against it rather than
# against the private copy its own wheel ships.
#
# PyAV's PyPI wheels bundle an FFmpeg under av.libs/ that links libx264 and
# libx265. Both are GPL-2.0-or-later, and PyAV links FFmpeg in-process, so a
# closed-source consumer inherits that link regardless of whether it ever calls
# an encoder. The BtbN lgpl variant omits the GPL-only libraries, libx264 and
# libx265 among them; every other component this stack uses, including libsvtav1
# for AV1 output, is built into all variants.
#
# The build verifies its own result and fails if the treatment did not take, so
# a silently-wheel-installed PyAV cannot ship from here.
#
# Build from the repository root; the test target runs the suite as it builds:
#
#   docker build --target runtime -t mosaic-media-lgpl .
#   docker build --target test .
#
# A consumer that wants only the FFmpeg prefix builds --target ffmpeg and copies
# /opt/ffmpeg and /usr/local/bin/verify-ffmpeg-lgpl out of it.

ARG PYTHON_VERSION=3.13

# Provenance of the binary, since this is a third-party GitHub release rather
# than a distribution package.
#
# BtbN is Timo Rothenpieler, an FFmpeg maintainer. FFmpeg's own MAINTAINERS
# file assigns him nvdec*/nvenc*, hwcontext_cuda*, cuviddec.c, the
# fate.ffmpeg.org test infrastructure, and project server operations including
# emergencies:
#   https://github.com/FFmpeg/FFmpeg/blob/master/MAINTAINERS
#
# These builds are what ffmpeg.org itself links for Linux and Windows; the
# project publishes no binaries of its own:
#   https://ffmpeg.org/download.html
#
# Variant definitions, quoting the repository: "lgpl -- Lacking libraries that
# are GPL-only. Most prominently libx264 and libx265.":
#   https://github.com/BtbN/FFmpeg-Builds
#
# It is still a third party. The digest below is the control that matters:
# mirror the asset to storage the build itself controls if it must not depend
# on GitHub being reachable.

# Pinned by release tag AND asset digest: the tag names one build, the digest
# catches a re-uploaded asset. Never track `latest` -- these are nightlies,
# and identity digests are defined against libavformat's demuxer output, so
# an ffmpeg change that alters that output re-mints every content_digest for
# the affected container, with no code change and no file altered.
#
# Pin only the last build of a month. The repository's retention policy keeps
# those for two years and deletes every other daily build after fourteen days,
# so a mid-month pin stops downloading two weeks after it is chosen.
ARG FFMPEG_RELEASE=autobuild-2026-07-31-14-10
ARG FFMPEG_ASSET=ffmpeg-n8.1.2-34-g9b6c8969e0-linux64-lgpl-shared-8.1.tar.xz
ARG FFMPEG_SHA256=c882a80f06617149198a98a07a0880a7e881953ae9f9cb931f5be09a4f93caae

# n8.1 matches the libavcodec 62 ABI PyAV 18 is written against.
ARG AV_VERSION=18.0.0


# --------------------------------------------------------------- ffmpeg -----
FROM python:${PYTHON_VERSION}-slim AS ffmpeg
ARG FFMPEG_RELEASE
ARG FFMPEG_ASSET
ARG FFMPEG_SHA256

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl xz-utils && \
    rm -rf /var/lib/apt/lists/*

RUN curl -fsSL --retry 3 -o /tmp/ffmpeg.tar.xz \
      "https://github.com/BtbN/FFmpeg-Builds/releases/download/${FFMPEG_RELEASE}/${FFMPEG_ASSET}" && \
    echo "${FFMPEG_SHA256}  /tmp/ffmpeg.tar.xz" | sha256sum -c - && \
    mkdir -p /opt/ffmpeg && \
    tar -xJf /tmp/ffmpeg.tar.xz -C /opt/ffmpeg --strip-components=1 && \
    rm /tmp/ffmpeg.tar.xz

# Reject a GPL build here rather than discovering it downstream. The absence of
# --enable-gpl is not on its own the test: PyAV's own bundled FFmpeg reports LGPL
# and carries --enable-libx264 and --enable-libx265 without it, and libx264
# encodes in that build. Name the two encoders as well.
#
# This is a shared build, so its libraries must be resolvable before the binary
# will run at all. Without that the command fails, the capture is empty, and the
# negative greps succeed against nothing -- a gate that passes every build
# including the one it exists to reject. Redirect rather than pipe (a pipeline
# reports tee's status, not ffmpeg's) and assert positively that a configuration
# line was captured.
ENV LD_LIBRARY_PATH="/opt/ffmpeg/lib"
RUN /opt/ffmpeg/bin/ffmpeg -hide_banner -version > /opt/ffmpeg/BUILD-CONFIG.txt && \
    grep -q -- '--prefix=' /opt/ffmpeg/BUILD-CONFIG.txt && \
    ! grep -q -- '--enable-gpl' /opt/ffmpeg/BUILD-CONFIG.txt && \
    ! grep -q -- '--enable-libx264' /opt/ffmpeg/BUILD-CONFIG.txt && \
    ! grep -q -- '--enable-libx265' /opt/ffmpeg/BUILD-CONFIG.txt && \
    grep -m1 'configuration:' /opt/ffmpeg/BUILD-CONFIG.txt

# Installed, not run: a consumer inheriting this stage gets the check alongside
# the binary it verifies. Running it here would fail -- it imports av at module
# level, and no PyAV exists until the build stage.
COPY scripts/verify-ffmpeg-lgpl.py /usr/local/bin/verify-ffmpeg-lgpl


# ---------------------------------------------------------------- build -----
FROM python:${PYTHON_VERSION}-slim AS build
ARG AV_VERSION

COPY --from=ffmpeg /opt/ffmpeg /opt/ffmpeg
COPY --from=ghcr.io/astral-sh/uv:0.11.11@sha256:798712e57f879c5393777cbda2bb309b29fcdeb0532129d4b1c3125c5385975a /uv /uvx /bin/

# Building av from source needs a toolchain: it compiles a Cython extension
# against the headers pkg-config points at.
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential pkg-config && \
    rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/ffmpeg/bin:${PATH}" \
    PKG_CONFIG_PATH="/opt/ffmpeg/lib/pkgconfig" \
    LD_LIBRARY_PATH="/opt/ffmpeg/lib"

# `--no-binary av` forces the source distribution. Without it uv installs the
# wheel, whose bundled av.libs/ is linked by RPATH and silently wins over
# everything set above.
RUN uv venv /opt/venv && \
    uv pip install --python /opt/venv/bin/python \
        --no-binary av "av==${AV_VERSION}"


# ----------------------------------------------------------------- test -----
# The suite runs here, at build time, against the exact FFmpeg the runtime stage
# ships. It is a separate stage so pytest, the checkout, and the corpus never
# reach the runtime image: `--target runtime` builds without any of it, and
# without paying for it.
FROM python:${PYTHON_VERSION}-slim AS test
# The value cfr.mp4 mints under this FFmpeg and identity scheme 2. Measured
# identical on FFmpeg 6.1, 7.1 and 8.1 and on two machines, so it pins the
# demuxer rather than the build host. A mismatch is a format break: bump
# IDENTITY_SCHEME and re-mint, never edit this to make a build pass. A scheme
# bump re-mints this value too, since the scheme is hashed into the digest.
ARG EXPECTED_CONTENT_DIGEST=34993be8d696870a365a3dfcd3fa8b84

COPY --from=ffmpeg /opt/ffmpeg /opt/ffmpeg
COPY --from=build /opt/venv /opt/venv
COPY --from=ghcr.io/astral-sh/uv:0.11.11@sha256:798712e57f879c5393777cbda2bb309b29fcdeb0532129d4b1c3125c5385975a /uv /uvx /bin/

ENV PATH="/opt/venv/bin:/opt/ffmpeg/bin:${PATH}" \
    LD_LIBRARY_PATH="/opt/ffmpeg/lib"

COPY scripts/verify-ffmpeg-lgpl.py /usr/local/bin/verify-ffmpeg-lgpl
RUN python /usr/local/bin/verify-ffmpeg-lgpl

# The source is the build context: this Dockerfile lives in the repository it
# tests. The [io] extra is the PyAV path under test; av is already the source
# build from the previous stage and must not be replaced by a wheel, which is
# why the verify gate runs again afterwards.
COPY . /opt/mosaic_media
RUN uv pip install --python /opt/venv/bin/python \
        --no-binary av \
        -e "/opt/mosaic_media[io,cli]" pytest pytest-xdist numpy && \
    python /usr/local/bin/verify-ffmpeg-lgpl

WORKDIR /opt/mosaic_media

# pyproject's addopts (-m 'not bench') deselects the performance gate. It could
# not run here in any case: its corpus is H.264 because it baselines against
# OpenCV, which cannot decode AV1, and this image carries no GPL encoder to
# produce one.
RUN python -m pytest tests/ -q

# Unset EXPECTED_CONTENT_DIGEST reports without asserting, which is how the
# value is first obtained.
COPY scripts/golden-digest.py /usr/local/bin/golden-digest
RUN EXPECTED_CONTENT_DIGEST="${EXPECTED_CONTENT_DIGEST}" python /usr/local/bin/golden-digest


# -------------------------------------------------------------- runtime -----
# The LGPL FFmpeg and the source-built PyAV linked against it, and nothing else.
# This package is not installed here: the stage is a base to install onto, not a
# shipping image. Last in the file, so it is the default target -- but BuildKit
# builds only the stages the target depends on, and this one does not depend on
# test, so the suite runs only under --target test.
FROM python:${PYTHON_VERSION}-slim AS runtime

COPY --from=ffmpeg /opt/ffmpeg /opt/ffmpeg
COPY --from=build /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:/opt/ffmpeg/bin:${PATH}" \
    LD_LIBRARY_PATH="/opt/ffmpeg/lib"

COPY scripts/verify-ffmpeg-lgpl.py /usr/local/bin/verify-ffmpeg-lgpl
RUN python /usr/local/bin/verify-ffmpeg-lgpl

CMD ["python", "/usr/local/bin/verify-ffmpeg-lgpl"]
