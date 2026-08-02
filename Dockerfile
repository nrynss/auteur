# FoleyFlow on Cloud Run.
#
# Two stages so the ~200 MB of build tooling does not ride along into the image
# that Cloud Run cold-starts. ffmpeg is a runtime dependency, not a build one:
# scene detection, mix, mux and the ebur128 QA measurements all shell out to it,
# and `analyze.get_video_duration` treats ffprobe as the sole duration authority.

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies resolve from the lockfile in their own layer, so editing source
# does not re-resolve them. --frozen fails loudly if uv.lock has drifted from
# pyproject.toml rather than silently deploying a different dependency set.
# No `--mount=type=cache`: that is BuildKit-only syntax, and this has to build
# under Cloud Build's default builder as well as a local daemon without buildx.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# README.md is not documentation here — pyproject declares `readme = "README.md"`
# and hatchling reads it while building the project, so omitting it fails the
# build rather than merely shipping a docless image.
COPY src/ ./src/
COPY providers.toml README.md ./
RUN uv sync --frozen --no-dev


FROM python:3.14-slim-bookworm

# ffmpeg pulls in the codecs; ca-certificates is needed for B2, Gemini and the
# tunnelled SFX worker, all of which are HTTPS.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Non-root. Cloud Run does not require it, but the container also runs locally
# and in CI, and nothing here needs privilege.
RUN useradd --create-home --uid 1000 foley
USER foley
WORKDIR /app

COPY --from=build --chown=foley:foley /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# providers.toml is NOT found via the working directory. config.py resolves it
# as `__file__/../../../providers.toml` — the repo root relative to the *package
# source*. That lands on /app/providers.toml only because `uv sync` installs the
# project editable, so the package resolves to /app/src/foleyflow/. Switch to a
# non-editable install and the lookup climbs into site-packages and fails at
# import. Keep src/ and providers.toml side by side under /app.
#
# Cloud Run injects $PORT and ignores EXPOSE; the shell form is deliberate so
# the variable is expanded at start rather than passed through as a literal.
# Single worker on purpose: the job runner's slot table is in-process, and a
# second worker would let two runs start for the same project. See
# api/jobs.py's "Single-instance constraint".
CMD exec uvicorn foleyflow.api.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
