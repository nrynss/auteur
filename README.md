# FoleyFlow

> An AI post-production copilot that turns a rough video into an auditable, production-ready deliverable.

Built for the **Backblaze Generative AI Media Hackathon**. See [SPEC.md](SPEC.md) for the full build specification and [docs/api.md](docs/api.md) for the HTTP contract.

FoleyFlow analyzes a video, generates and mixes synchronized Foley, runs technical
post-production checks, and preserves the full production record in Backblaze B2.

## Status

**Live: <https://foleyflow-142385844755.us-east4.run.app>**

A silent clip goes in and a B2-backed Foley export comes out, over the API, on
the deployed app. One package (`src/foleyflow`), one command (`foleyflow`).

| Layer | State |
|---|---|
| Media pipeline — VLM analysis, routing, SFX, mix, mux | Built |
| Genblaze orchestration — ingest, stem fan-out, mix/mux/QA stages | Built (analysis is the one call still outside) |
| Storage — content-addressed cache, B2 artifacts | Built |
| Projects, runs, versions | Built |
| Deployment — Cloud Run + tunnelled GPU worker | Built |
| HTTP API + CLI | Partial (repair pending) |
| QA — loudness, true peak, visual continuity | Built (audio when mix measured) |
| Self-hosted SFX worker — MOSS-SoundEffect v2 | Built (separate GPU process, bearer-auth) |
| Control room UI | Not started |

Remaining work is itemised in [handoff.md](handoff.md).

## Prerequisites

- Python 3.14+ and [uv](https://docs.astral.sh/uv/)
- `ffmpeg` and `ffprobe` on `PATH`
- A [Google AI Studio](https://aistudio.google.com/apikey) key (`GEMINI_API_KEY`) for analysis
- For SFX, the self-hosted worker (see below) — the default `test` tier resolves
  to it, so it must be reachable for any render that is not fully cached.
  `ELEVENLABS_API_KEY` (paid tier) and `FAL_KEY` (its fallback) are needed only
  when `FOLEYFLOW_SFX_TIER` selects them; neither is required to import, test,
  or start the app
- A Backblaze B2 bucket and an application key scoped to it

## Setup

```bash
uv sync
cp .env.example .env
```

Fill in `GEMINI_API_KEY` and the `B2_*` values, then run the offline suite —
which needs no provider credentials at all:

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
| SFX, tier `test` (default) | `openmoss/moss-soundeffect-v2` (self-hosted) | $0.00 |
| SFX, tier `paid` | `eleven_text_to_sound_v2` (`genblaze-elevenlabs`) | ~$0.10–0.30/call |
| SFX, tier `fallback` | `fal-ai/elevenlabs/sound-effects/v2` (fal) | ~$0.05/call |

All three SFX tiers are invoked through Genblaze, so choosing one is a config
edit (`[sfx_tiers] active`, or `$FOLEYFLOW_SFX_TIER`) and not a different code
path. `paid` falls back to `fallback` on error; `test` deliberately does not
escalate to a paid API. The default is `test`, which is also what keeps the
shared B2 stem cache warm — the cache is keyed on the endpoint, so switching
tiers means generating fresh clips under fresh keys.

See SPEC.md §9.1 for why routing is "free local vs paid hero sound" rather than
"which paid API", and §8.2 for what Genblaze actually orchestrates.

### Self-hosted SFX worker

MOSS-SoundEffect v2 runs as a separate CUDA process outside this repo — nothing
here imports torch, and the deployed app stays free of CUDA. It needs a GPU with
~10 GB free and an 11 GB model from Hugging Face.

```bash
uvicorn server:app --host 127.0.0.1 --port 8600
```

Point FoleyFlow at it with `FOLEYFLOW_SFX_WORKER_URL`, or leave it unset for
`http://127.0.0.1:8600`. Generation is ~20 s per stem regardless of clip length
(the model always denoises a 30-second latent and crops), and the worker
serialises requests — one GPU. Independent stems still fan out: Genblaze issues
them concurrently and the worker queues them.

When the worker is exposed through a tunnel it requires a bearer token. Set
`FOLEYFLOW_SFX_WORKER_TOKEN` to the same value on both sides; it is sent on
`/generate` and on the `/files/{name}` download, and on nothing else — the token
is never attached to a vendor CDN URL. Leave it empty for a loopback worker
running open, and note that it is deliberately **not** part of the SFX cache key:
rotating a transport credential must not orphan cached stems.

## Deployment

The app runs on Cloud Run. The GPU worker cannot — it needs CUDA and an 11 GB
model — so it stays on a machine you control and is reached over a Cloudflare
tunnel. That split is the whole reason the worker is "one more provider with a
base URL" (SPEC §9.1).

```bash
gcloud run deploy foleyflow --source . --region us-east4 --allow-unauthenticated --no-cpu-throttling --min-instances 1 --max-instances 1 --memory 4Gi --cpu 2 --timeout 300 --env-vars-file env.yaml
```

Four of those flags are load-bearing, not defaults worth tuning:

| Flag | Why |
|---|---|
| `--no-cpu-throttling` | Every mutation returns 202 and continues on a thread pool. Cloud Run's default throttles CPU to near zero between requests, which stalls a mix the instant the response is sent. |
| `--max-instances 1` | Run state lives in B2 so polling is instance-agnostic, but the slot table that stops two runs starting for one project is in-process (`api/jobs.py`). |
| `--min-instances 1` | Scale-to-zero would kill in-flight jobs. This bills continuously — delete the service after judging. |
| `--memory 4Gi` | `/tmp` is tmpfs and counts against memory. The source video, every stem, the mix WAV and the muxed MP4 all live there during a run. |

`env.yaml` carries the same variables as `.env` (see `.env.example`). The cost
ledger lives on the shared storage backend under `ledger/{ulid}.json` — the
same B2 bucket as every other artifact — so quota counting survives restarts
and does not need a writable container filesystem. Do not set
`FOLEYFLOW_LEDGER_PATH` to a path under `/tmp`: that is per-instance and resets
on every cold start. Check `ledger_writable` in `GET /quota` after any deploy;
`false` means the figures are fiction and the UI must stop presenting them.

The image is built from the `Dockerfile` rather than buildpacks, because the
pipeline needs `ffmpeg` at runtime. `providers.toml` is resolved relative to the
*package source* (`__file__/../../..`), which lands correctly only because `uv
sync` installs the project editable — keep `src/` and `providers.toml` side by
side under `/app`.

### Tunnelling the worker

```bash
cloudflared tunnel route dns <tunnel-id> sfx.example.com
```

Then add an ingress rule **above** the catch-all — ingress is first-match, so
anything below `http_status:404` is dead:

```yaml
ingress:
  - hostname: sfx.example.com
    service: http://127.0.0.1:8600
  - service: http_status:404
```

Restart `cloudflared` afterwards; it does not reload config on its own. Set
`FOLEYFLOW_SFX_WORKER_URL` to the hostname and give both sides the same
`FOLEYFLOW_SFX_WORKER_TOKEN` — a tunnel puts a free GPU endpoint on the public
internet, and it will be found.
