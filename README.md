# Auteur

> An AI post-production copilot that turns a rough video into an auditable, production-ready deliverable.

Built for the **Backblaze Generative AI Media Hackathon**. See [auteur.md](auteur.md) for what the product is and where it goes, and [docs/api.md](docs/api.md) for the HTTP contract.

Auteur analyzes a video, generates and mixes synchronized Foley, runs technical
post-production checks, and preserves the full production record in Backblaze B2.

## Status

**Live: <https://foleyflow-142385844755.us-east4.run.app>**

A silent clip goes in and a B2-backed Foley export comes out, over the API, on
the deployed app. One package (`src/foleyflow`), one command (`auteur`, still
installed as `foleyflow` too).

| Layer | State |
|---|---|
| Media pipeline — VLM analysis, routing, SFX, mix, mux | Built |
| Genblaze orchestration — ingest, analysis, stem fan-out, mix/mux/QA stages | Built |
| Storage — content-addressed cache, B2 artifacts | Built |
| Projects, runs, versions | Built |
| Deployment — Cloud Run + tunnelled GPU worker | Built |
| HTTP API + CLI | Built |
| QA — loudness, true peak, visual continuity | Built (audio when mix measured) |
| Self-hosted SFX worker — MOSS-SoundEffect v2 | Built (separate GPU process, bearer-auth) |
| Control room UI — Svelte SPA served by the app, timeline editing, config view | Built |

The build log and every decision behind it are itemised in
[handoff.md](handoff.md).

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
uv run auteur --help
```

`foleyflow` is installed as an alias of `auteur` and will keep working — same
entry point, same verbs — so existing scripts and runbooks need no edit. The
identifier is lowercase everywhere; capitalised `Auteur` is the product name in
prose only.

### Projects (needs the API running)

```bash
uv run auteur create path/to/clip.mp4 --title "Standoff" --analyze --wait
uv run auteur show proj_…
uv run auteur timeline proj_…
uv run auteur scenes proj_…
uv run auteur qa proj_…
uv run auteur mix proj_… --wait
uv run auteur export proj_… --wait
uv run auteur quota
```

### Local render (no server)

Deterministic re-render from a saved timeline — no analysis call, $0:

```bash
uv run auteur render path/to/clip.mp4 --from-timeline samples/timelines/clip_1.json
```

Fresh analysis + render (spends provider quota):

```bash
uv run auteur render path/to/clip.mp4
```

Same timeline in, byte-identical mix out — mix randomness is seeded from a
sha256 of each event's name and start time, never Python's per-process `hash()`.

## Providers

Configured in [providers.toml](providers.toml) — adding or swapping a model is a
config edit, not a code change.

| Role | Provider / model | Cost |
|---|---|---|
| Video understanding → event timeline | `gemini-2.5-flash` (default); `gpt-5.6-luna` optional | ~$0.005 / ~$0.02 |
| SFX, tier `test` (default) | `openmoss/moss-soundeffect-v2` (self-hosted) | $0.00 |
| SFX, tier `paid` | `eleven_text_to_sound_v2` (`genblaze-elevenlabs`) | ~$0.10–0.30/call |
| SFX, tier `fallback` | `fal-ai/elevenlabs/sound-effects/v2` (fal) | ~$0.05/call |

All three SFX tiers are invoked through Genblaze, so choosing one is a config
edit (`[sfx_tiers] active`, or `$FOLEYFLOW_SFX_TIER`) and not a different code
path. `paid` falls back to `fallback` on error; `test` deliberately does not
escalate to a paid API. The default is `test`, which is also what keeps the
shared B2 stem cache warm — the cache is keyed on the endpoint, so switching
tiers means generating fresh clips under fresh keys.

See the archived spec ([`docs/design/development/archives/SPEC.md`](docs/design/development/archives/SPEC.md))
§9.1 for why routing is "free local vs paid hero sound" rather than "which paid
API", and §8.2 for what Genblaze actually orchestrates.

### Self-hosted SFX worker

MOSS-SoundEffect v2 runs as a separate CUDA process outside this repo — nothing
here imports torch, and the deployed app stays free of CUDA. It needs a GPU with
~10 GB free and an 11 GB model from Hugging Face.

```bash
uvicorn server:app --host 127.0.0.1 --port 8600
```

Point Auteur at it with `FOLEYFLOW_SFX_WORKER_URL`, or leave it unset for
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

### Secrets vs public config

Credentials must **not** sit in plain Cloud Run env vars: anyone with project
access can read them via `gcloud run services describe`. They live in
**Secret Manager** and are mounted with `--set-secrets`. Non-secret config
(bucket name, endpoints, worker URL, deploy stamps) stays in
[`env.public.yaml`](env.public.yaml).

| Cloud Run env | Secret Manager name | Notes |
|---|---|---|
| `B2_APPLICATION_KEY` | `foleyflow-b2-application-key` | Bucket write — highest impact |
| `B2_KEY_ID` | `foleyflow-b2-key-id` | Paired with the application key |
| `GEMINI_API_KEY` | `foleyflow-gemini-api-key` | Analysis |
| `FOLEYFLOW_SFX_WORKER_TOKEN` | `foleyflow-sfx-worker-token` | Bearer for the tunnelled GPU worker |
| `ELEVENLABS_API_KEY` | `foleyflow-elevenlabs-api-key` | Paid SFX tier only — optional until set |

`ELEVENLABS_API_KEY` is created when present in `.env` (`setup_secrets.sh`) and
mounted by `deploy.sh` only if that secret exists. Other optional keys
(`FAL_KEY`, `OPENAI_API_KEY`) are not required on the default `test` tier —
same pattern if you add them later. Never put any of these in
`env.public.yaml`.

**Create / rotate secrets** (values from local `.env` or the environment; never
printed or committed):

```bash
# one-time: enable API + create secrets + grant the Cloud Run runtime SA
./scripts/setup_secrets.sh --env-file .env
# defaults: project backblaze-504305, service foleyflow, region us-east4
```

That script:

1. Enables `secretmanager.googleapis.com` if needed
2. Creates each secret (if missing) and adds a new version from stdin
3. Grants `roles/secretmanager.secretAccessor` on each secret to the service's
   runtime service account (required or the revision fails to start)

**Manual equivalent** (values via stdin — never on the argv):

```bash
gcloud services enable secretmanager.googleapis.com --project backblaze-504305

# for each secret name above:
printf '%s' "$B2_APPLICATION_KEY" | gcloud secrets create foleyflow-b2-application-key \
  --project=backblaze-504305 --replication-policy=automatic --data-file=-
# or, if the secret already exists:
printf '%s' "$B2_APPLICATION_KEY" | gcloud secrets versions add foleyflow-b2-application-key \
  --project=backblaze-504305 --data-file=-

# runtime SA needs accessor (default compute SA unless the service overrides it)
SA="$(gcloud run services describe foleyflow --region us-east4 --project backblaze-504305 \
  --format='value(spec.template.spec.serviceAccountName)')"
gcloud secrets add-iam-policy-binding foleyflow-b2-application-key \
  --project=backblaze-504305 \
  --member="serviceAccount:${SA}" \
  --role="roles/secretmanager.secretAccessor"
# repeat for foleyflow-b2-key-id, foleyflow-gemini-api-key, foleyflow-sfx-worker-token
```

### Deploy

Preferred path — helper stamps `FOLEYFLOW_GIT_SHA` / `FOLEYFLOW_BUILD_TIME`,
passes public env + secret refs, and refuses an env file that still lists
secret keys:

```bash
./scripts/deploy.sh
# config-only (no image rebuild):
./scripts/deploy.sh --no-source
# emergency only — skip post-deploy smoke_deploy.py:
./scripts/deploy.sh --skip-smoke
```

After a successful `gcloud run deploy`, `deploy.sh` resolves the service URL and
runs `scripts/smoke_deploy.py --base-url … --expect-sha $GIT_SHA`. A smoke
failure fails the deploy.

Equivalent `gcloud` form:

```bash
gcloud run deploy foleyflow --source . --region us-east4 --project backblaze-504305 \
  --allow-unauthenticated \
  --no-cpu-throttling --min-instances 1 --max-instances 1 \
  --memory 4Gi --cpu 2 --timeout 300 \
  --env-vars-file env.public.yaml \
  --set-secrets=\
B2_APPLICATION_KEY=foleyflow-b2-application-key:latest,\
B2_KEY_ID=foleyflow-b2-key-id:latest,\
GEMINI_API_KEY=foleyflow-gemini-api-key:latest,\
FOLEYFLOW_SFX_WORKER_TOKEN=foleyflow-sfx-worker-token:latest
```

Do **not** put secrets in `--env-vars-file` / `--set-env-vars`. The old
all-in-one `env.yaml` pattern is retired; use `env.public.yaml` for non-secrets
only (and keep any local file that still has credentials out of git —
`env.yaml` is gitignored as a safety net).

**`--set-secrets` replaces the full secret set.** gcloud does not merge: the
list you pass becomes the complete secret-backed env for the next revision.
Omitting a secret removes it. `scripts/deploy.sh` always passes the core four
plus `ELEVENLABS_API_KEY` when that secret exists; if you deploy by hand, pass
the same full CSV every time.

**Add ElevenLabs (paid SFX tier):**

```bash
# 1. Put the key in local .env (never commit):
#    ELEVENLABS_API_KEY=sk_...
./scripts/setup_secrets.sh --env-file .env
# 2. Remount secrets on Cloud Run (no image rebuild):
./scripts/deploy.sh --no-source
# 3. Local paid-tier runs also need the same key exported / in .env
```
`./scripts/deploy.sh --skip-smoke` skips the post-deploy smoke check in an
emergency only — default is to fail the deploy when smoke fails.

Four of the deploy flags are load-bearing, not defaults worth tuning:

| Flag | Why |
|---|---|
| `--no-cpu-throttling` | Every mutation returns 202 and continues on a thread pool. Cloud Run's default throttles CPU to near zero between requests, which stalls a mix the instant the response is sent. |
| `--max-instances 1` | Run state lives in B2 so polling is instance-agnostic, but the slot table that stops two runs starting for one project is in-process (`api/jobs.py`). |
| `--min-instances 1` | Scale-to-zero would kill in-flight jobs. This bills continuously — delete the service after judging. |
| `--memory 4Gi` | `/tmp` is tmpfs and counts against memory. The source video, every stem, the mix WAV and the muxed MP4 all live there during a run. |

The cost ledger lives on the shared storage backend under `ledger/{ulid}.json`
— the same B2 bucket as every other artifact — so quota counting survives
restarts and does not need a writable container filesystem. Do not set
`FOLEYFLOW_LEDGER_PATH` to a path under `/tmp`: that is per-instance and resets
on every cold start. Check `ledger_writable` in `GET /quota` after any deploy;
`false` means the figures are fiction and the UI must stop presenting them.

The image is built from the `Dockerfile` rather than buildpacks, because the
pipeline needs `ffmpeg` at runtime. `providers.toml` is resolved relative to the
*package source* (`__file__/../../..`), which lands correctly only because `uv
sync` installs the project editable — keep `src/` and `providers.toml` side by
side under `/app`.

**Acceptance check** — describe must show secret *references*, never values:

```bash
gcloud run services describe foleyflow --region us-east4 --project backblaze-504305 \
  --format='yaml(spec.template.spec.containers[0].env)'
```

Each of `B2_APPLICATION_KEY`, `B2_KEY_ID`, `GEMINI_API_KEY`, and
`FOLEYFLOW_SFX_WORKER_TOKEN` should look like:

```yaml
- name: GEMINI_API_KEY
  valueFrom:
    secretKeyRef:
      key: latest
      name: foleyflow-gemini-api-key
```

not `value: "…"`.

To stamp a deploy so `GET /api/v1/health` reports the commit it is running,
set `FOLEYFLOW_GIT_SHA` and `FOLEYFLOW_BUILD_TIME`. How you set them depends
on the deploy path:

**A) This repo's `gcloud run deploy --source` flow** — Cloud Build builds the
Dockerfile without custom build-args, so stamp at runtime via the public env
file. `scripts/deploy.sh` does this automatically; manually:

```bash
printf 'FOLEYFLOW_GIT_SHA: "%s"\nFOLEYFLOW_BUILD_TIME: "%s"\n' \
  "$(git rev-parse HEAD)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> env.public.yaml
```

Then deploy with `--env-vars-file env.public.yaml` and `--set-secrets=…` as
above. Verified end to end: `GET /api/v1/health` on the deployed service
returns the same sha as `git rev-parse HEAD`.

> **Not `--update-env-vars` alongside `--env-vars-file`.** gcloud rejects
> mixing those env flags: *"At most one of --clear-env-vars | --env-vars-file
> | --set-env-vars | --remove-env-vars | --update-env-vars can be specified."*
> Identity stamps therefore live in the same public env file as the other
> non-secret variables. `--set-secrets` is a separate flag and **is** allowed
> next to `--env-vars-file`.

**B) Image-based deploys** (`docker build` / registry push) can bake the
stamps into the image via Dockerfile `ARG`/`ENV`:

```bash
docker build \
  --build-arg FOLEYFLOW_GIT_SHA="$(git rev-parse HEAD)" \
  --build-arg FOLEYFLOW_BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -t auteur .
```

Omit them and health still works — `git_sha` / `build_time` are just `null`.

**After deploy** — run the post-deploy smoke check (health, storage, worker
probe, `git_sha` vs HEAD, `ledger_writable`, one read endpoint). It exits
non-zero on any failure and names the reason:

```bash
uv run python scripts/smoke_deploy.py --base-url https://YOUR-SERVICE.run.app
# or pin the SHA you just shipped:
uv run python scripts/smoke_deploy.py --base-url https://… --expect-sha "$(git rev-parse HEAD)"
```

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
