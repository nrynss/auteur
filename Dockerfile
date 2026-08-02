# FoleyFlow on Cloud Run.
#
# Three stages: a Node stage builds the Svelte control-room SPA, the uv stage
# installs the Python app, and the runtime image copies both — so the ~200 MB
# of build tooling and every byte of Node stay out of the image that Cloud Run
# cold-starts. ffmpeg is a runtime dependency, not a build one: scene
# detection, mix, mux and the ebur128 QA measurements all shell out to it, and
# `analyze.get_video_duration` treats ffprobe as the sole duration authority.

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
COPY scripts/emit_openapi.py ./scripts/emit_openapi.py
RUN uv sync --frozen --no-dev

# Emit the OpenAPI contract for the UI build. FastAPI builds the schema from
# the app object, so no server and no network are needed. emit_openapi.py
# pins the local backend so no credentials are required either.
RUN .venv/bin/python scripts/emit_openapi.py --out /schema/openapi.json


FROM node:22-slim AS ui

# ui/ is built here and only its output is copied onward — no Node ships to
# Cloud Run. Node 22 is the floor for Vite 8 (^20.19 || >=22.12).
WORKDIR /app
COPY ui/package.json ui/package-lock.json ./
RUN npm ci

COPY ui/ ./ui/
COPY --from=build /schema/openapi.json ./ui/openapi.json
# vite.config.ts writes the bundle to ../src/foleyflow/api/static — i.e.
# /app/src/foleyflow/api/static in this stage — so the runtime image can copy
# the built assets straight into the installed package.
RUN cd ui && npm run build


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
# Built SPA assets: the uv stage copied src/ (with an empty static dir); the
# Node stage's output now overwrites it with the real bundle.
COPY --from=ui --chown=foley:foley /app/src/foleyflow/api/static /app/src/foleyflow/api/static/
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Build identity reported by GET /api/v1/health. Pass at image build so a
# running container can name the commit it is serving without a .git tree:
#
#   docker build \
#     --build-arg FOLEYFLOW_GIT_SHA="$(git rev-parse HEAD)" \
#     --build-arg FOLEYFLOW_BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
#     -t foleyflow .
#
# Empty defaults: a build that omits the args still runs; health just reports
# null for git_sha / build_time (same as a local `uv run uvicorn`).
ARG FOLEYFLOW_GIT_SHA=
ARG FOLEYFLOW_BUILD_TIME=
ENV FOLEYFLOW_GIT_SHA=$FOLEYFLOW_GIT_SHA \
    FOLEYFLOW_BUILD_TIME=$FOLEYFLOW_BUILD_TIME

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
