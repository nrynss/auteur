# Auteur

> **An AI post-production copilot that turns a rough video into an auditable, production-ready deliverable.**

Built for the **Backblaze Generative AI Media Hackathon**. Deployed at
<https://foleyflow-142385844755.us-east4.run.app>.

Auteur analyzes a video, generates and mixes synchronized Foley, runs technical
post-production checks, and preserves the full production record in Backblaze B2 —
all through a control-room UI that is a director's tool, not a status page.

---

## 1. Product summary

Auteur is a post-production control room: a creator sees what is ready, what
needs attention, why it was flagged, and can apply a targeted repair without
re-running the entire job. A silent clip goes in and a B2-backed Foley export
comes out, over the API or through the browser.

One package (`src/foleyflow`), one command (`foleyflow`). One Svelte 5 SPA built
into the same Docker image.

### One-line pitch

> Auteur is an AI post-production copilot that analyzes, enhances, quality-checks, and preserves every decision behind a finished video.

---

## 2. Problem

AI-generated and low-budget video commonly reaches picture lock with incomplete or
inconsistent post-production:

- footage is silent or has poor sound design;
- effects do not line up with visible actions;
- shots have inconsistent exposure or color;
- audio clips or misses delivery loudness targets;
- files and the reasoning behind revisions are scattered across tools;
- a fix to one detail often means rebuilding too much of the project.

Professional teams solve this through separate departments and careful versioning.
Solo creators and small studios need a focused workflow that makes the next repair
obvious, limits recomputation, and retains a reliable production history.

---

## 3. Users and jobs to be done

### Primary users

- AI-video creators publishing short films, ads, or social video
- Indie filmmakers and small post-production teams
- Animation and game teams preparing trailers or cutscenes

### Core job

"I have a mostly finished video. Help me identify the highest-value post-production
issues, generate the missing sound design, make targeted improvements, and retain a
defensible final package."

---

## 4. Product principles

1. **Foley is the hero.** The primary reveal is silent video becoming convincingly
   sound-designed.
2. **Diagnose before generating.** Every generation is tied to a scene, event,
   recommendation, and recorded decision.
3. **Repair only what changed.** A weak footstep should regenerate one stem, not
   restart the production.
4. **Human stays in control.** Users can accept, ignore, lock, or re-run
   recommendations.
5. **B2 is the production system of record.** Assets, versions, checks, logs, and
   provenance live in durable project storage — not merely the final export.
6. **Honest automation.** A step is called an agent only when it evaluates
   evidence and chooses an action or escalation. Deterministic analyses are
   labelled checks.

---

## 5. Architecture

### 5.1 Package layout

```text
src/foleyflow/
  analyze.py                  video → event timeline (VLM)
  routing.py  sfx.py          event → provider request; generation + cache
  assemble.py mux.py          deterministic mix; picture-locked mux
  ledger.py                   per-call cost accounting
  direct.py  pipeline.py      director's notes; local render orchestration
  peaks.py                    server-side waveform peak computation
  storage/                    Backend protocol; LocalBackend + B2Backend
  projects.py                 projects, runs, versions, event log, derived state
  ids.py                      monotonic ULIDs (sort order is the log's order)
  scenes.py                   scene detection, colour stats, thumbnails
  timeline_cache.py           content-addressed analysis results
  sfx_cache.py                content-addressed stems + repair variant nonce
  quota.py                    provider rate-limit accounting
  providers/                  adapter layer, declared in providers.toml
  workflow.py                 Genblaze seam
  api/                        FastAPI app, job runner, schemas, error contract
  cli.py                      foleyflow — the single command
ui/
  src/lib/
    api/                      generated TypeScript types from the OpenAPI schema
    components/               design-system primitives (Button, Chip, Evidence, …)
    theme.css                 design tokens (light/dark, explicit toggle)
    format.ts                 timecode and cost formatting
    icons.ts                  inline SVG catalog
  src/routes/                 Svelte 5 pages (control room, projects, …)
  App.svelte                  shell + router
```

### 5.2 Two load-bearing boundaries

- **`providers.toml` is the only place a vendor is named.** Adding or swapping a
  provider is a config edit plus one adapter class. Changing the SFX tier is a
  single config value, not a new code path — all three tiers (paid, fallback,
  test) go through the same Genblaze pipeline.
- **Storage is a five-method protocol.** The same key space works against the
  local filesystem and B2, so the whole suite runs offline against exactly the
  layout production uses.

### 5.3 Genblaze responsibilities

Genblaze is the orchestration spine. It:

- defines the ordered stages and their inputs/outputs;
- invokes configured media providers for generation and analysis;
- records execution state, timing, errors, and retries;
- fans out independent sound-effect generation where safe;
- retries only the failed or low-confidence stem;
- publishes stage artifacts and provenance manifests to B2.

Every provider call in the system goes through a pipeline, including the
self-hosted worker on the `test` tier — so the paid path's orchestration is
exercised by every offline test run. Analysis is a one-step Genblaze pipeline
through `AnalysisProvider` (Google Gemini by default; OpenAI `gpt-5.6-luna`
optional). Local deterministic work (mix, mux, QA) is first-class: `FFmpegStageProvider`
runs them as Genblaze steps, so they are timed, retried and recorded like any
provider call.

### 5.4 Backblaze B2 responsibilities

B2 is Auteur's canonical project archive:

- direct video upload destination through presigned URLs;
- original input and immutable content checksum;
- scene thumbnails and analysis JSON;
- prompts, provider responses, WAV stems, mixes, and final MP4s;
- QA reports and workflow logs;
- versioned provenance manifest for every export;
- the cost ledger (one immutable object per provider call);
- short-lived signed links for preview and download.

### 5.5 The GPU worker

The SFX worker is a separate CUDA process (`../moss-sfx-worker`) running
MOSS-SoundEffect v2 behind HTTP + bearer auth. From Auteur's side it is "one
more provider with a base URL." Nothing here imports torch — the split keeps the
deployed app free of CUDA. The worker is reached over a Cloudflare tunnel at
`sfx.nryn.dev` and declared in `providers.toml`.

---

## 6. Cost-aware SFX routing

Auteur selects sound-generation providers according to the role of the sound,
the available budget, and the need for precise repair.

| Sound role | Provider | Why | Unit cost |
|---|---|---|---|
| Ambience beds and the bulk of discrete Foley | Self-hosted MOSS-SoundEffect v2 (default `test` tier) | Open-weight, free per generation, duration-conditioned | $0.00 |
| Hero discrete Foley, on explicit user approval | ElevenLabs Sound Effects (`paid` tier) | Reserved for the one or two sounds a viewer notices most | ~$0.10–0.30/call |
| Fallback on paid-tier failure | `fal-ai/elevenlabs/sound-effects/v2` (`fallback` tier) | Automatic escalation; `test` deliberately does not escalate | ~$0.05/call |

All three tiers are invoked through Genblaze. Choosing one is a config edit
(`[sfx_tiers] active`, or `$FOLEYFLOW_SFX_TIER`), not a different code path.
`paid` falls back to `fallback` on error; `test` deliberately does not escalate
to a paid API.

The default is `test`, which is also what keeps the shared B2 stem cache warm —
the cache is keyed on the endpoint, so switching tiers means generating fresh
clips under fresh keys.

### Repair and escalation policy

1. Generate one low-cost ambience bed and cache it by prompt, duration, model, and
   parameters.
2. Generate individual high-salience Foley events as independent stems.
3. If a stem fails technically, retry the same provider once automatically.
4. If a stem is judged weak by QA or the user, offer the estimated cost and let
   the user choose: regenerate with the same provider, try an approved alternative,
   or retain the current take.
5. Re-mix only the selected approved stems; never regenerate picture or unaffected
   sounds.

### User-facing cost and provenance

The project dashboard includes a compact production-cost panel. Every generated
asset records the provider, model, requested duration, pricing estimate shown at
approval, incurred cost when available, and the routing rationale. These fields
are included in the B2-backed provenance manifest.

Each project carries an advisory dollar budget (`budget_usd`, mutable via
`PUT /budget` as latest-wins events). Every billed row is attributed to the
project that triggered it. Adopted work from a direct `foleyflow render` is
claimed through an attribution override — rows are immutable, so adoption is a
record, never a rewrite.

The budget never refuses work; ledger alerts fire at configurable fractions
(default 50/80/100/120%). Billable repair still requires `confirm_cost_usd`.

---

## 7. B2 object layout

```text
foleyflow/
  projects/{project_id}/
    project.json                    immutable identity, written once
    input/source.mp4                the client's presigned PUT lands here
    events/{evt_ulid}.json          append-only lifecycle log, one object per event
    scenes/scenes.json              breakdown + per-scene colour statistics
    scenes/{scene_id}/thumbnail.jpg
    qa/qa-report.json               latest QA report (refreshable)
    timelines/timeline.v{n}.json    immutable versions; a repair adds, never replaces
    runs/{run_id}/run.json          immutable, written only at a terminal state
    exports/latest.json             the single mutable pointer
  cache/{content_hash}.wav          SFX, shared across every project
  timelines/{content_hash}.json     analysis results, keyed by video+model+prompt
  ledger/{ulid}.json                cost ledger, one object per provider call
  assets/{sha}/{sha}/{sha}.ext      Genblaze content-addressable sink
  manifests/{genblaze_run_id}.json  Genblaze provenance manifests
```

Key design decisions:

- **One object per event, not a shared log.** Object storage has no append; per-event
  objects with monotonic ULID names make a sorted listing an ordered log.
- **Immutable artifacts are written with `put_if_absent`.** No existence-check-then-write
  race.
- **Two caches, different keys.** `cache/` and `timelines/` key on the *request*
  so the decision to skip a paid call happens before making it. Genblaze's `assets/`
  keys on the sha256 of the result.
- **Status is derived, never stored.** Folding the event log makes the audit trail
  *be* the state, so the two cannot drift.

---

## 8. Data contracts

### 8.1 Project

The stored record carries identity only. `status` and `latest_run_id` are derived
by folding the event log on read.

```json
{
  "id": "proj_01KYJD1RKETS3KTX5HA3H5JPDQ",
  "title": "Western standoff",
  "created_at": "2026-07-27T18:23:00Z",
  "input_key": "projects/proj_01KYJD.../input/source.mp4",
  "source_filename": "standoff_kling.mp4",
  "source_sha256": "",
  "duration_s": null
}
```

### 8.2 Lifecycle event

The append-only log with domain-neutral event types.

```json
{
  "id": "evt_01KYJD1SVTG7PGQS457MC6Y3XD",
  "project_id": "proj_01KYJD...",
  "type": "postproduction.foley.plan.proposed",
  "created_at": "2026-07-27T18:23:07Z",
  "actor": "user",
  "run_id": "run_01KYJD1TF9MZVBYWMY0EHKP3B5",
  "payload": {"timeline_version": 1, "event_count": 4}
}
```

Event ids are **monotonic** ULIDs — several events routinely land inside one
millisecond, and with independently random suffixes the log would come back shuffled.

### 8.3 Run

Written to storage only at a terminal state. Liveness comes from the log; durability from
the record. A run whose start event is older than `RUN_LEASE_S` with no terminal
record reads as stale rather than running forever.

```json
{
  "id": "run_01KYJD1TF9MZVBYWMY0EHKP3B5",
  "project_id": "proj_01KYJD...",
  "kind": "analyze",
  "status": "completed",
  "created_at": "2026-07-27T18:23:02Z",
  "timeline_version": 1,
  "parent_run_id": null,
  "genblaze_run_id": "35168837-3de9-4805-8ff0-78e050488c29",
  "error": null,
  "error_code": null,
  "stages": [ … ],
  "metadata": {"model": "gemini-2.5-flash", "cached": false, "scene_count": 5}
}
```

`error` is a classification, not the exception text — provider and storage exceptions
embed presigned URLs, bucket names, and absolute server paths.

### 8.4 Timeline event

```json
{
  "id": "evt_armor_steps_01",
  "scene_id": "scene_02",
  "name": "heavy armor footsteps on wet stone",
  "kind": "rhythmic",
  "start_s": 3.2,
  "end_s": 5.7,
  "intensity": "medium",
  "prompt": "heavy armored boots walking on rain-soaked cobblestone, close and weighty",
  "status": "approved",
  "stem_version": 2
}
```

**Event round-trip integrity.** `Event` is a plain dataclass with five required
fields plus optional `seed` and `source_duration_s`. `dict_to_timeline` explicitly
enumerates every field — a key not listed there is silently dropped on round-trip.
Adding a field means editing `models.py`, `timeline_to_dict`, *and* `dict_to_timeline`
together.

#### Mix seed (freeze)

An optional `seed` field on the event:

- **Absent** → computed as `sha256(event name + start time)`, as before. Every
  existing timeline and stored manifest still reproduces byte-for-byte.
- **Present** → used verbatim for mix-variation jitter. Nudging a rhythmic event
  no longer redraws its tile gains — the sound translates, it doesn't reshape.

#### Source duration (freeze)

`source_duration_s` splits the SFX request duration from the placement span:

- **`source_duration_s`** — what was actually generated. Part of the SFX cache key
  `(endpoint, prompt, duration, variant)`. Changes only through Regenerate.
- **Placement span** (`start_time` + `duration`) — freely editable, windows the
  existing clip, never re-bills.

When a mix run completes, the server stamps `source_duration_s` for every event
whose stem was actually recorded, and the mix is recorded against that stamped
version. Discrete events also have their placement `duration` widened to the frozen
source length (the freeze switches on windowing; without it the stamp would silently
cut the one-shot's headroom).

### 8.5 QA report

```json
{
  "run_id": "run_01J...",
  "overall_status": "needs_review",
  "audio": {
    "integrated_lufs": -13.2,
    "true_peak_dbtp": 1.4,
    "target_lufs": -14,
    "target_true_peak_dbtp": -1.0,
    "issues": [
      {"severity": "blocking", "scene_id": "scene_05", "code": "true_peak_exceeded"}
    ]
  },
  "visual": {
    "issues": [
      {
        "severity": "warning",
        "scene_id": "scene_03",
        "code": "exposure_discontinuity",
        "confidence": 0.82,
        "compared_with": "scene_02"
      }
    ]
  }
}
```

Continuity is an adjacent-shot mean-luma comparison — a review prompt, never
presented as a detected fault.

### 8.6 Provenance manifest

The final manifest contains a complete, hashed inventory of each artifact and the
lineage necessary to reproduce or audit it — source, every stem (prompt, endpoint,
tier, sha256), mix, export, and QA.

---

## 9. HTTP API

Base path: `/api/v1`. The full contract is in `docs/api.md`.

### Principles

- **Every mutation is asynchronous.** Mutations return `202 Accepted` with a `run_id`;
  the client polls the run.
- **Video bytes never pass through the API.** `POST /projects` returns a presigned
  PUT URL; the client uploads straight to B2.
- **Immutability is visible in the contract.** There is no `PUT /projects/{id}` and
  no `DELETE`. Timelines are versioned and additive.
- **Errors have one shape**, including validation failures. Clients branch on a
  stable `code`.

### Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness + storage reachability + build identity + optional worker probe |
| `POST` | `/projects` | Create project, return a presigned B2 PUT |
| `POST` | `/projects/{id}/uploaded` | Confirm the upload; hash and ffprobe it |
| `GET` | `/projects` | List, newest first, cursor-paginated |
| `GET` | `/projects/{id}` | Project, derived status, readiness checklist, signed source/mix/export URLs |
| `POST` | `/projects/{id}/analyze` | Start analysis |
| `GET` | `/projects/{id}/timeline` | Timeline with per-event `scene_id`, `seed`, `source_duration_s` |
| `PUT` | `/projects/{id}/timeline` | Commit a new immutable version; freeze conservation enforced |
| `GET` | `/projects/{id}/timeline/versions` | Coexisting versions |
| `GET` | `/projects/{id}/scenes` | Scene rail with stats and signed thumbnails |
| `GET` | `/projects/{id}/runs`, `/runs/{run_id}` | Poll a run; stages readable mid-flight |
| `GET` | `/projects/{id}/events` | The audit trail (History) |
| `POST` | `/projects/{id}/events/{index}/regenerate` | Targeted stem regeneration with `confirm_cost_usd` |
| `POST` | `/projects/{id}/mixes` | Render stems, mix, mux, re-measure QA; cache-miss cost projection |
| `GET` | `/projects/{id}/qa` | Latest QA report |
| `POST` | `/projects/{id}/exports` | Publish a rendered mix + provenance manifest; `override_blocking` for QA override |
| `GET` | `/projects/{id}/dossier` | Every artifact with signed links + budget summary |
| `GET` | `/projects/{id}/budget` | Spend against advisory budget, threshold alerts |
| `PUT` | `/projects/{id}/budget` | Set or clear the advisory budget |
| `POST` | `/projects/{id}/attributions` | Adopt unattributed ledger rows |
| `GET` | `/projects/{id}/peaks/stem` | Waveform peaks for one content-addressed stem |
| `GET` | `/projects/{id}/peaks/stems` | Waveform peaks for every stem in a run's inventory (batch, for first paint) |
| `GET` | `/projects/{id}/peaks/mix` | Waveform peaks for the mix WAV |
| `GET` | `/quota` | Per-model usage and remaining budget, estimate-flagged |

### Error shape

Every non-2xx response:

```json
{
  "error": {
    "code": "project_not_found",
    "message": "No project with id proj_01KYJD1RKETS3KTX5HA3H5JPDQ",
    "detail": {"project_id": "proj_01KYJD1RKETS3KTX5HA3H5JPDQ"}
  }
}
```

| HTTP | `code` | When |
|---|---|---|
| 400 | `invalid_request` | Malformed body or parameter |
| 404 | `project_not_found` / `run_not_found` / `timeline_not_found` / `qa_not_found` | Genuinely absent |
| 409 | `already_running` | A run of this kind is already in flight |
| 409 | `immutable_conflict` | Attempt to overwrite a completed artifact |
| 422 | `precondition_failed` | Valid request, wrong state |
| 429 | `quota_exhausted` | Provider's daily budget is spent — includes `resets_at` |
| 502 | `provider_error` | An upstream provider failed |
| 503 | `storage_unavailable` | B2 unreachable |

---

## 10. Invariants

Breaking these breaks the product:

- **Determinism.** Same timeline in, byte-identical mix out. Mix randomness is
  seeded from a sha256 of each event's name and start time, never Python's
  per-process `hash()`.
- **ffprobe is the sole duration authority.** Not the model's self-reported
  duration, not container metadata.
- **Cache keys are addresses, not provenance.** The SFX cache key is
  `(endpoint, prompt, duration, variant)`. The worker bearer token is excluded —
  rotating a transport credential must not orphan cached stems.
- **Manifests and completed generations are immutable.** `_put_json_immutable`,
  and `save_manifest` treats a re-export of the same run as a no-op.
- **`providers.toml` is resolved from the package source path**, not the working
  directory.
- **`POST /projects/{id}/events/{index}/regenerate` maps an event index** to its
  `SfxRequest`. Event names repeat within a timeline; a name is not an identifier.
- **Evidence-first.** Show the measured number and its source, never a synthesised
  verdict. QA warnings are review prompts, not automatic corrections. The visual
  continuity check is an adjacent-shot mean-luma comparison and must never be
  presented as a detected fault.

---

## 11. The control room

The UI is a Svelte 5 SPA, built into the Docker image, served by the FastAPI
router. It is a director's tool — a timeline you edit directly, with an AI you
talk to in text, at a precision a working editor would accept.

### 11.1 Design language

| Aspect | Direction |
|---|---|
| Palette | Near-neutral grounds with a faint blue-green bias, single desaturated teal accent. Full light/dark sets; explicit toggle wins over system preference. |
| Typography | System UI stack; monospace numerics with `tabular-nums` for every timecode, measurement, duration, and cost. Base 13px at tool density. |
| Form | Radii 5/7/10px by role; pills at 999. One shadow, reserved for the video frame. Separation by hairline. No gradients. |
| Waveforms | Canvas, not SVG. Discrete one-shots get transient-led decay; sustained events get a sustained envelope. Real peak data from the server at 100 peaks/second. |
| Honesty | A QA number cannot render without its source (enforced structurally via the Evidence component). The transport shows the transient, not `start_time`. Continuity is a comparison, never a fault. Cost geometry is on screen. |

### 11.2 Layout

```
┌──────────────────────────────────────────────────────────┐
│ chrome  46px — title · readiness · budget · theme · act  │
├───────────────────────────────┬──────────────────────────┤
│ 16:9 preview (flex)           │ rail 344px               │
│ transport                     │  Readiness │ Evidence │  │
│ scene rail (thin strip)       │  Director                │
├───────────────────────────────┴──────────────────────────┤
│ ⇕ timeline — resizable, min 148px                        │
│   header · ruler · lanes (scroll) · inspector            │
└──────────────────────────────────────────────────────────┘
```

The scene rail is attached to the picture it describes. The timeline is resizable
with a keyboard-operable grip (`role="separator"`). Below 1000px the rail moves
under the viewer.

### 11.3 Component layer

Twelve design-system primitives, all exposed in a dev-only gallery:

- **Button** — secondary/primary/ghost/danger, `pending` (spinner + disabled), sm size.
- **Chip** — four states (`ok`/`warn`/`blocked`/`pending`); every chip renders a glyph
  AND the word (colour never carries meaning alone). `pressed`/`live` variants.
- **Evidence** — `source` is a required prop, so a screen showing a QA number without
  naming the artifact it was measured from does not compile.
- **SceneThumb** — index badge + honest span timecodes, current ring, lazy images.
- **StageRow** — name, status chip, elapsed, retries, provider; renders mid-flight.
- **ConfirmDialog** — modal, focus-managed, Escape/backdrop cancel, cost line, pending
  spend path cannot be cancelled.
- **Timecode** — `formatTimecode` (m:ss.mmm, carries rounding across second boundaries)
  + `formatCost`.
- **Meter, Panel, Kbd, Spinner** (reduced-motion aware), **Icon** (inline SVG, no emoji).

### 11.4 Screens

| Screen | Description |
|---|---|
| **Readiness (read-only)** | Percent bar, checklist rows, budget chip (estimate-flagged, warned when `ledger_writable` is false), scene rail, non-editable timeline, QA evidence, history, dossier. QA continuity renders as two thumbnails side by side with confidence. |
| **A/B player** | One transport, two sources, toggle preserving playhead, keyboard shortcut. Browser-side preview: stems scheduled at edited offsets via Web Audio against the video — instant, free, no server round trip. Labelled a preview; QA numbers only ever come from the server's measured mix. The playhead shows the transient (the true hit point), not `start_time`. |
| **Direct manipulation** | Move, resize, draft state (persisted to localStorage), commit to a new version, switch versions as undo. Writes the frozen `seed` onto any event it touches. Cost geometry visible throughout. An event's placement span is freely editable and never re-bills. Resonating is tied to the Regenerate button with its own cost confirmation. |
| **Spend paths and run locking** | Regenerate, mix, export — each with `confirm_cost_usd` echoed back per the API contract. Export lists blockers; `override_blocking` behind an explicit confirm. In-flight run lock disables editing; live stages render as they arrive. An expired lease becomes a real stuck-run error. Polls; no SSE. |
| **Director mode** | Text in, timeline edits out. Chat proposes, never applies: the model emits a schema-validated edit set, the timeline renders it as a diff (ghosted old positions against new), and the director approves or rejects. Model is `gemini-3.5-flash-lite` (500/day). Every turn is a provider call through the ledger. |
| **Project creation and upload** | Projects index (status, readiness, budget), create form (title, budget, file picker → `POST /projects` → presigned PUT with real upload progress → confirm → kick off analysis). |
| **Polish** | Accessibility pass, keyboard map, empty/error/loading states for every panel, draft-versus-committed treatment, version-switching presentation, full focus order. |

---

## 12. CLI

One command. Project verbs talk to the API; file verbs run locally with no server.

```bash
uv run foleyflow --help
```

### Project-scoped (needs the API running)

```bash
uv run foleyflow create path/to/clip.mp4 --title "Standoff" --analyze --wait
uv run foleyflow show proj_…
uv run foleyflow timeline proj_…
uv run foleyflow scenes proj_…
uv run foleyflow qa proj_…
uv run foleyflow mix proj_… --wait
uv run foleyflow export proj_… --wait
uv run foleyflow quota
uv run foleyflow budget proj_… [--set 5 | --clear]
uv run foleyflow attribute proj_… [--before …]
uv run foleyflow regenerate proj_… --event 2
uv run foleyflow dossier proj_…
uv run foleyflow prewarm timeline.json [--sfx-tier paid|fallback|test] [--tier full] [--dry-run]
uv run foleyflow health [--worker]
uv run foleyflow doctor [--timeline PATH]
```

### File-scoped (no server)

```bash
uv run foleyflow render path/to/clip.mp4 --from-timeline samples/timelines/clip_1.json
uv run foleyflow direct path/to/timeline.json "move footsteps 40 ms earlier"
```

---

## 13. Providers

Configured in `providers.toml` — adding or swapping a model is a config edit, not
a code change.

| Role | Provider / model | Cost |
|---|---|---|
| Video understanding → event timeline | `gemini-2.5-flash` (default); `gpt-5.6-luna` optional | ~$0.005 / ~$0.02 |
| SFX, tier `test` (default) | `openmoss/moss-soundeffect-v2` (self-hosted) | $0.00 |
| SFX, tier `paid` | `eleven_text_to_sound_v2` (genblaze-elevenlabs) | ~$0.10–0.30/call |
| SFX, tier `fallback` | `fal-ai/elevenlabs/sound-effects/v2` (fal) | ~$0.05/call |

The analysis budget is the binding constraint: Gemini's free tier allows 20
requests per day on Flash models. Analysis results are cached by
`(video_sha256, model, prompt_fingerprint)`; re-analysing a clip costs nothing.
`GET /quota` reports usage from the durable B2 ledger with `estimate: true`.

---

## 14. Deployment

The app runs on Cloud Run (us-east4). The GPU worker cannot — it needs CUDA — so
it stays on a machine you control and is reached over a Cloudflare tunnel.

### Secrets

Credentials live in Secret Manager and are mounted with `--set-secrets`. Never
put secrets in `env.public.yaml` or `--env-vars-file`.

| Cloud Run env | Secret Manager name | Notes |
|---|---|---|
| `B2_APPLICATION_KEY` | `foleyflow-b2-application-key` | Bucket write |
| `B2_KEY_ID` | `foleyflow-b2-key-id` | Paired with the application key |
| `GEMINI_API_KEY` | `foleyflow-gemini-api-key` | Analysis |
| `FOLEYFLOW_SFX_WORKER_TOKEN` | `foleyflow-sfx-worker-token` | Bearer for GPU worker |

### Deploy

```bash
./scripts/deploy.sh
# config-only (no image rebuild):
./scripts/deploy.sh --no-source
```

Helper stamps `FOLEYFLOW_GIT_SHA` / `FOLEYFLOW_BUILD_TIME`, passes public env +
secret refs, refuses an env file that lists secret keys, and runs
`scripts/smoke_deploy.py` post-deploy (fail deploy on smoke fail).

### Load-bearing flags

| Flag | Why |
|---|---|
| `--no-cpu-throttling` | Mutations return 202 and continue on a thread pool. CPU throttling to zero stalls a mix the instant the response is sent. |
| `--max-instances 1` | Run state lives in B2 but the slot table is in-process. |
| `--min-instances 1` | Scale-to-zero would kill in-flight jobs. |
| `--memory 4Gi` | `/tmp` is tmpfs and counts against memory. |

### Image

Multi-stage Dockerfile: a Node stage builds the UI, the Python image copies only
the built assets. No Node ships to Cloud Run. Built assets are never committed.
`docker build` fails on contract drift (svelte-check runs inside the Dockerfile's
Node stage).

---

## 15. Readiness scoring and repair policy

The score is a transparent convenience, not a black-box quality claim.

| Condition | Effect |
|---|---|
| Scene analysis absent | Block final export |
| No approved Foley plan | Block final export |
| Required stem failed | Block final export |
| True peak above target | Block final export until limiting/re-mix is applied or user overrides |
| Loudness outside target range | Warning; user can normalize/re-mix |
| Exposure/color discontinuity | Warning; opens comparison evidence |
| All required artifacts and manifest in B2 | Enables final approval/export |

The repair router may automatically retry a provider/network failure once.
Artistic or continuity assessments require user confirmation before a new
generation is billed.

---

## 16. Success criteria

Auteur is ready to submit when a judge can verify:

| # | Criterion |
|---|---|
| 1 | A real video enters a B2-backed project |
| 2 | Genblaze orchestrates visible, meaningful workflow stages |
| 3 | The app produces an audible, synchronized Foley transformation |
| 4 | The app surfaces at least one useful technical or visual QA finding |
| 5 | A targeted repair updates only the relevant asset/mix version |
| 6 | The final export traces to source, prompts, models, QA, and B2 objects |

---

## 17. Future roadmap

After the hackathon, Auteur can expand into:

- dialogue cleanup and transcription confidence checks;
- assisted color matching with user-approved grade suggestions;
- caption/accessibility analysis;
- multi-clip campaign consistency checks;
- collaborative review/approval states;
- searchable B2 media library and semantic asset reuse;
- policy-aware rights, model, and licensing metadata.

The architectural boundary remains the same: each module contributes versioned
evidence and artifacts to a single durable post-production record.

---

## 18. Demo plan (about 3 minutes)

**Clip:** `standoff_kling.mp4` — 15.04 s, genuinely silent, five detected scenes.

1. **0:00–0:20 — Problem:** Play the silent source. A picture-locked clip that is
   visually finished but not post-production ready.
2. **0:20–0:55 — Analyze:** Upload/enter the project. Show Genblaze workflow stages
   and B2 project creation. Scene analysis finds the rain, walking, and environmental
   context.
3. **0:55–1:30 — Foley reveal:** Show the editable plan, event timeline, and instant
   silent-versus-mixed A/B toggle. Play the generated rain, armor/boot steps, wind,
   and thunder.
4. **1:30–2:05 — QA:** Open the readiness checklist. Show a flagged overly hot mix
   and a visual exposure mismatch between two shots. Clarify the latter is a review
   warning, not an invented automatic correction.
5. **2:05–2:35 — Targeted repair:** Regenerate one weak footstep stem or re-mix to
   fix the peak. Show that only one asset changes and QA updates.
6. **2:35–3:00 — B2 proof:** Open the dossier: original video, timeline versions,
   stem v1/v2, mix, report, workflow log, and hash-backed provenance manifest.
   Export the final MP4.

---

## 19. Test suite

689 passed, 1 skipped. Offline: the only stubs in the render path are the billed
provider calls, so ffmpeg, the mix, the shared cache, storage, and the Genblaze
pipelines all run for real. No provider credentials required.

Vitest suite: 32 tests covering SSR component contracts (timecode formatting,
theme precedence, Chip a11y) and jsdom tests for ConfirmDialog DOM rules.

CI: GitHub Actions — ffmpeg on PATH, `uv sync --frozen --group dev`,
`scripts/check_test_count.py` (asserts handoff/SPEC match runtime counts), UI
build job (typegen → check → test → build, `docker build` verified on
master). Permissions `contents: read`, concurrency cancel-in-progress.
