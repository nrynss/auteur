# Task 15 — More evidence in the bucket

One project is thin for a judge. This documents how B2 was seeded with
additional full project dossiers (mix + export + §11.6 manifest) without
burning Gemini's 20/day analysis quota.

## Deployed surface

| Item | Value |
|---|---|
| API | `https://foleyflow-hinblugpsa-uk.a.run.app` |
| gcloud project | `backblaze-504305` |
| Service | `foleyflow` (Cloud Run, `us-east4`) |
| CLI | `export FOLEYFLOW_API=https://foleyflow-hinblugpsa-uk.a.run.app` |

## Source videos (not in this repo)

`demo-assets/` is **not** checked into FoleyFlow. Validated timelines live under
`samples/timelines/`; silent source clips were found outside the repo:

| Clip | Timeline | Source video (local) |
|---|---|---|
| Desert duel | `samples/timelines/duel_kling.json` | `../fal-ai/scratch/output/New folder/duel_kling.mp4` (24 MB) |
| The Inhaler | `samples/timelines/inhaler_kling.json` | `../fal-ai/scratch/output/New folder/inhaler_kling.mp4` (22 MB) |

Do **not** commit these binaries.

## Why not `foleyflow create … --from-timeline`?

The HTTP API has no timeline-injection route. Intake is always
create → upload → analyze → mix → export. Analysis is gated by the shared
content-addressed timeline cache (`timelines/{key}.json` in B2), keyed on
`(video_sha256, model_id, prompt_fingerprint)`.

To prefer validated sample timelines (handoff: prefer `--from-timeline`) without
spending Gemini:

1. Hash the exact source bytes that will be uploaded.
2. Seed B2 at the cache key `get_or_analyze` would read, with the sample
   timeline payload.
3. Run `create --analyze --wait`. The analyze run reports `metadata.cached: true`
   and Gemini quota stays at `0/20`.

Equivalent local offline path (no project dossier):  
`foleyflow render clip.mp4 --from-timeline samples/timelines/….json`.

## Prewarm (SFX cache, free on `test` tier)

```bash
export FOLEYFLOW_API=https://foleyflow-hinblugpsa-uk.a.run.app
export FOLEYFLOW_SFX_TIER=test   # also needed for local prewarm routing

# Required: inhaler was cold (0/9)
FOLEYFLOW_SFX_TIER=test uv run foleyflow prewarm samples/timelines/inhaler_kling.json

# Verify duel already warm
FOLEYFLOW_SFX_TIER=test uv run foleyflow prewarm samples/timelines/duel_kling.json --dry-run
```

### Results (2026-08-02)

| Timeline | Before | After prewarm / verify |
|---|---|---|
| `inhaler_kling.json` | 0 cached / 9 to generate | **9/9 cached** (generated 9 of 9) |
| `duel_kling.json` | 4/4 cached | **4/4 cached** (`--dry-run` no-op) |

Both project `$0.00` on mix because every stem was a cache hit.

## Seed timeline cache (quota-safe analyze)

```bash
# Uses FOLEYFLOW_CACHE_BACKEND=b2 + B2_* from .env
uv run python scripts/seed_timeline_cache.py \
  --video "/path/to/duel_kling.mp4" \
  --timeline samples/timelines/duel_kling.json
uv run python scripts/seed_timeline_cache.py \
  --video "/path/to/inhaler_kling.mp4" \
  --timeline samples/timelines/inhaler_kling.json
```

Or run the all-in-one `scripts/seed_demo.sh` (below).

Cache keys written this session (model `gemini-2.5-flash`, prompt fp `835ee25b4772`):

| Video | Object key |
|---|---|
| duel | `timelines/da68017a0f1b912c8654cdbb.json` |
| inhaler | `timelines/6559eec858a18dee7410200a.json` |

## End-to-end create / mix / export

```bash
export FOLEYFLOW_API=https://foleyflow-hinblugpsa-uk.a.run.app

uv run foleyflow create /path/to/duel_kling.mp4 \
  --title "Desert duel (Kling)" --analyze --wait
# note project id from stdout

uv run foleyflow mix proj_… --tier full --wait
uv run foleyflow export proj_… --wait
# If readiness blocks on true-peak, re-run with --override-blocking

uv run foleyflow create /path/to/inhaler_kling.mp4 \
  --title "The Inhaler (Kling)" --analyze --wait
uv run foleyflow mix proj_… --tier full --wait
uv run foleyflow export proj_… --wait
```

Both analyze runs returned `"cached": true`. Gemini remaining: **0/20 used**.

## Projects in B2 (acceptance)

Acceptance: **two** projects with mix, export, and manifest each. Achieved: **three**.

| Project ID | Title | Status | mix | final_export | manifest | stems |
|---|---|---|---|---|---|---|
| `proj_01KYJD1RKETS3KTX5HA3H5JPDQ` | Western standoff (silent) | ready | yes | yes | yes | 5 |
| `proj_01KZ1151RJ9GP7MSRGHB0X77ET` | Desert duel (Kling) | ready | yes | yes | yes | 4 |
| `proj_01KZ117JH73KC0E442E79KSCW2` | The Inhaler (Kling) | ready | yes | yes | yes | 9 |

Verify any time:

```bash
export FOLEYFLOW_API=https://foleyflow-hinblugpsa-uk.a.run.app
uv run foleyflow list
uv run foleyflow show proj_01KZ1151RJ9GP7MSRGHB0X77ET
uv run foleyflow --json dossier proj_01KZ117JH73KC0E442E79KSCW2
```

## Reproduce

```bash
# From repo root, with .env (B2 + keys) and source videos available:
export FOLEYFLOW_API=https://foleyflow-hinblugpsa-uk.a.run.app
export FOLEYFLOW_SFX_TIER=test
export DUEL_VIDEO="/path/to/duel_kling.mp4"
export INHALER_VIDEO="/path/to/inhaler_kling.mp4"
bash scripts/seed_demo.sh
```

`seed_demo.sh` is idempotent on the SFX / timeline caches; it always creates
**new** projects (do not delete existing ones).

## Gaps / notes

- Source MP4s are not in-repo; operators must supply paths via env vars.
- There is no public API for `--from-timeline`; timeline-cache seeding is the
  server-side stand-in used here.
- QA may warn `loudness_out_of_range` / `exposure_discontinuity`; export still
  succeeded without override in this session. Use `--override-blocking` if a
  true-peak finding blocks readiness.
- Existing project `proj_01KYJD1RKETS3KTX5HA3H5JPDQ` was left untouched.
