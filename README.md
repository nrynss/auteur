# FoleyFlow

> An AI post-production copilot that turns a rough video into an auditable, production-ready deliverable.

Built for the **Backblaze Generative AI Media Hackathon**. See [SPEC.md](SPEC.md) for the full build specification and [docs/api.md](docs/api.md) for the HTTP contract.

FoleyFlow analyzes a video, generates and mixes synchronized Foley, runs technical
post-production checks, and preserves the full production record in Backblaze B2.

## Status

Milestone 2 complete: a silent clip goes in and a B2-backed Foley export comes
out, over the API. One package (`src/foleyflow`), one command (`foleyflow`).

| Layer | State |
|---|---|
| Media pipeline — VLM analysis, routing, SFX, mix, mux | Built |
| Storage — content-addressed cache, B2 artifacts | Built |
| Projects, runs, versions | Built |
| HTTP API + CLI | Partial (analyze, mix, export; repair and dossier pending) |
| QA — loudness, true peak, visual continuity | Built (audio when mix measured) |
| Self-hosted SFX worker — MOSS-SoundEffect v2 | Built (separate GPU process) |
| Control room UI | Not started |

## Prerequisites

- Python 3.14+ and [uv](https://docs.astral.sh/uv/)
- `ffmpeg` and `ffprobe` on `PATH`
- A [Google AI Studio](https://aistudio.google.com/apikey) key (`GEMINI_API_KEY`) for analysis
- For SFX, the self-hosted worker (see below). A [fal.ai](https://fal.ai) key
  (`FAL_KEY`) is needed only for the paid hero-sound tier
- A Backblaze B2 bucket and an application key scoped to it

## Setup

```bash
uv sync
cp .env.example .env
```

Fill in `GEMINI_API_KEY`, `FAL_KEY`, and the `B2_*` values, then run the offline suite:

```bash
uv run pytest -q
```

## Run the API

```bash
uv run uvicorn foleyflow.api.main:app --reload
```

The API listens on `http://127.0.0.1:8000` by default. Point the CLI elsewhere with `--api` or `$FOLEYFLOW_API`.

## CLI

One command. Project verbs talk to the API; file verbs run locally with no server.

```bash
uv run foleyflow --help
```

### Projects (needs the API running)

```bash
uv run foleyflow create path/to/clip.mp4 --title "Standoff" --analyze --wait
uv run foleyflow show proj_…
uv run foleyflow timeline proj_…
uv run foleyflow scenes proj_…
uv run foleyflow qa proj_…
uv run foleyflow mix proj_… --wait
uv run foleyflow export proj_… --wait
uv run foleyflow quota
```

### Local render (no server)

Deterministic re-render from a saved timeline — no analysis call, $0:

```bash
uv run foleyflow render path/to/clip.mp4 --from-timeline samples/timelines/clip_1.json
```

Fresh analysis + render (spends provider quota):

```bash
uv run foleyflow render path/to/clip.mp4
```

Same timeline in, byte-identical mix out — mix randomness is seeded from a
sha256 of each event's name and start time, never Python's per-process `hash()`.

## Providers

Configured in [providers.toml](providers.toml) — adding or swapping a model is a
config edit, not a code change.

| Role | Provider / model | Cost |
|---|---|---|
| Video understanding → event timeline | `gemini-2.5-flash` (Google GenAI) | ~$0.005/call |
| Ambience and the bulk of Foley | `openmoss/moss-soundeffect-v2` (self-hosted) | $0.00 |
| Hero discrete Foley, on approval | `fal-ai/elevenlabs/sound-effects/v2` (fal) | ~$0.02/10s |

The fal entries need credits this project no longer has; the self-hosted tier is
the default path. See SPEC.md §9.1 for why routing is "free local vs paid hero
sound" rather than "which paid API".

### Self-hosted SFX worker

MOSS-SoundEffect v2 runs as a separate CUDA process outside this repo — nothing
here imports torch, and the deployed app stays free of CUDA. It needs a GPU with
~10 GB free and an 11 GB model from Hugging Face.

```bash
uvicorn server:app --host 127.0.0.1 --port 8600
```

Point FoleyFlow at it with `FOLEYFLOW_SFX_WORKER_URL`, or leave it unset for
`http://127.0.0.1:8600`. Generation is ~16 s per stem regardless of clip length
(the model always denoises a 30-second latent and crops), and the worker
serialises requests — one GPU.
