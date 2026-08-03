# Auteur

> **An AI post-production copilot that turns a rough video into an auditable,
> production-ready deliverable.**

Built for the **Backblaze Generative AI Media Hackathon**. Live demo:
<https://foleyflow-142385844755.us-east4.run.app>.

Auteur analyzes a video, generates and mixes synchronized sound design (foley
is the first application), runs technical post-production checks, and preserves
the full production record in Backblaze B2 — through a **control-room UI** that
is a director’s tool, not a status page. Every billed second is traceable to
the call that produced it and the money it cost.

The product name is **Auteur** (film term: the director as author of the work).
The Python package, CLI entry, and env vars still use the historical
`foleyflow` / `FOLEYFLOW_*` identifiers where they are import paths or deploy
config — not user-facing brand (see handoff tasks 20 and 23).

---

## 1. Product summary

Auteur is a single-operator post-production control room: a creator sees what
is ready, what needs attention, why it was flagged, and can apply a targeted
repair without re-running the entire job. A silent clip goes in; a B2-backed
mix and provenance package come out — over the API or in the browser.

One package (`src/foleyflow`), one command (`foleyflow`), one Svelte 5 SPA built
into the same Docker image. Genblaze is the orchestration spine; B2 is the
system of record.

### One-line pitch

> Auteur is an AI post-production copilot that analyzes, enhances,
> quality-checks, and preserves every decision behind a finished video —
> with human approval before money is spent.

### Why “Auteur”

“FoleyFlow” described the first application, not the system. The domain model
(projects, timelines, events, stems, mixes, runs, manifests) is generic
post-production vocabulary. **Auteur** is modality-neutral and matches the
product thesis: the model *proposes*, a human *decides*.

---

## 2. Problem

AI-generated and low-budget video often reaches picture lock with incomplete
or inconsistent post:

- footage is silent or poorly designed for sound;
- effects do not line up with visible actions;
- shots have inconsistent exposure or color;
- audio misses delivery loudness / true-peak targets;
- files and the reasoning behind revisions are scattered across tools;
- fixing one detail often means rebuilding too much of the project;
- generative tools rarely prove **what was generated, by whom, at what cost**.

Professional teams solve this with departments and careful versioning. Solo
creators and small studios need a focused workflow that makes the next repair
obvious, limits recomputation, and retains a defensible production history.

---

## 3. Users and jobs to be done

### Primary users

- AI-video creators (short film, ads, social)
- Indie filmmakers and small post teams
- Animation / game teams preparing trailers or cutscenes
- Self-hosting partners who bring their own keys and brand (roadmap)

### Core job

“I have a mostly finished video. Help me identify the highest-value post issues,
generate the missing sound design, make targeted improvements, and retain a
defensible final package — without silent spend or fake certainty.”

---

## 4. Product principles

1. **Sound is the hero; foley is the first proof.** The primary reveal is silent
   video becoming convincingly designed. The architecture is not foley-only
   (see roadmap).
2. **Diagnose before generating.** Generation is tied to a scene, event,
   recommendation, and recorded decision.
3. **Repair only what changed.** One weak footstep regenerates one stem, not
   the whole production.
4. **Human stays in control.** Accept, ignore, lock, re-run; director mode
   proposes and never applies.
5. **B2 is the production system of record.** Assets, versions, checks, logs,
   ledger, and provenance live in durable project storage — not only the
   final export.
6. **Honest automation.** A step is an *agent* only when it evaluates evidence
   and chooses; deterministic work is a *check*. Evidence-first UI: measured
   numbers with sources, never a synthesised verdict.
7. **Money before motion.** Billable work states estimated cost and requires
   confirmation. When the ledger cannot be written, figures are withheld
   (`ledger_writable: false`) rather than shown as fiction.
8. **Determinism.** Same timeline in → byte-identical mix out. Free edits
   (nudge, resize) do not re-roll texture when seeds are frozen.

---

## 5. Architecture

### 5.1 Package layout

```text
src/foleyflow/
  analyze.py                  video → event timeline (VLM)
  routing.py  sfx.py          event → provider request; generation + cache
  assemble.py mux.py          deterministic mix; picture-locked mux
  ledger.py                   per-call cost accounting (scope-keyed caches)
  direct.py  pipeline.py      director notes; local render orchestration
  peaks.py                    server-side waveform peaks
  storage/                    Backend protocol; Local + B2; scope_id()
  projects.py                 projects, runs, versions, event log, adopt
  ids.py                      monotonic ULIDs
  scenes.py                   scene detection, colour stats, thumbnails
  timeline_cache.py           content-addressed analysis
  sfx_cache.py                content-addressed stems + repair variants
  quota.py                    provider rate-limit accounting
  providers/                  adapters + providers.toml + Genblaze
  workflow.py  stems.py stages.py tiers.py
  api/                        FastAPI, jobs, schemas, brand, config, dashboard
  cli.py                      foleyflow CLI
ui/
  src/lib/
    api/                      OpenAPI-generated types
    brand.ts                  build-time product name / titles / favicon
    components/               design-system primitives + tooltips
    help/                     keyboard map, glossary
    theme.css  theme.ts       tokens; light/dark/system, explicit toggle
    control/                  control room (timeline, player, director, …)
    projects/                 index, create, unclaimed
    config/                   operator configuration screen
    spend/                    budget / ledger UI helpers
  App.svelte                  shell + router
```

### 5.2 Load-bearing boundaries

- **`providers.toml` is the only place a vendor is named.** Add or swap a
  provider with config + one adapter. SFX tier (`paid` / `fallback` / `test`)
  is one setting — same Genblaze path.
- **Storage is a small protocol** (`exists`, `head`, `get`/`get_bytes`, `put`/
  `put_bytes`/`put_if_absent`, `signed_url`, `list_keys`/`list_objects`,
  `scope_id`). Local and B2 share the key space so the offline suite matches
  production layout.
- **GPU worker is a separate process** (`../moss-sfx-worker`). HTTP + bearer;
  nothing in this repo imports torch.

### 5.3 Genblaze

Orchestration spine: ordered stages, provider invocation, timing/errors/retries,
SFX fan-out, stage artifacts and manifests on B2. Analysis is a one-step
pipeline (`AnalysisProvider` — Gemini default, OpenAI optional). Mix/mux/QA run
as first-class stages (`FFmpegStageProvider`) so they are timed and recorded
like any call.

### 5.4 Backblaze B2

Canonical project archive: source video, scenes, timelines, stems, mixes,
exports, QA, event log, runs, cost ledger, caches, Genblaze assets/manifests,
signed preview URLs. Pricing and S3 compatibility make bring-your-own-bucket a
credential change, not a rewrite (see `docs/roadmap.md`).

### 5.5 Brand surface

| Surface | Mechanism |
|---|---|
| SPA name, titles, favicon, chrome | `VITE_BRAND_*` → `ui/src/lib/brand.ts` (build time) |
| OpenAPI /docs title, UI-not-built page | `FOLEYFLOW_BRAND_NAME` → `api/brand.py` (runtime) |
| Default product name | **Auteur** (task 20) |
| CI | Default build must carry Auteur; rebranded build must contain zero `Auteur` in `api/static/` |

Internals left alone: package name, `FOLEYFLOW_*` env keys, storage prefixes,
`foley.mp4` mix key (address, not brand).

---

## 6. Cost-aware SFX routing

| Role | Provider | Tier | Unit cost |
|---|---|---|---|
| Bulk / ambience / default demo | Self-hosted MOSS-SoundEffect v2 | `test` | $0.00 |
| Hero discrete foley (user-approved) | ElevenLabs sound effects | `paid` | ~$0.10–0.30/call |
| Paid-path failure | fal ElevenLabs SFX | `fallback` | ~$0.05/call |

Configured via `[sfx_tiers]` / `$FOLEYFLOW_SFX_TIER`. `paid` may fall back;
`test` does not escalate to paid. Cache keys use the *requested* endpoint;
`served_endpoint` records who actually served.

### Repair policy

1. Cache by prompt, duration, model, parameters (and repair `variant`).
2. Independent stems for discrete events.
3. Technical failure: one automatic retry.
4. Weak take / user judgment: show estimate → regenerate, alternative, or keep.
5. Re-mix only approved stems; never regenerate picture or unaffected sounds.
6. `POST .../regenerate` requires `confirm_cost_usd` for billable work.

### Budgets and ledger

- Advisory per-project budget (`PUT /budget`); alerts at configurable fractions
  (incl. set-below-spent).
- Ledger: one immutable object per call on B2; process caches keyed by
  `Backend.scope_id()` (task 18).
- Attribution rescue for unattributed rows; unclaimed archive for orphan
  prefixes and project-less spend (task 7.17).
- Fail-closed: unwritable ledger → UI stops presenting numbers as real.

---

## 7. B2 object layout

```text
projects/{project_id}/
  project.json                 identity (immutable once written)
  input/source.mp4
  events/{evt_ulid}.json       append-only lifecycle log
  scenes/…  qa/…  timelines/timeline.v{n}.json
  runs/{run_id}/…              mix, stems, run.json at terminal state
  exports/latest.json          single mutable pointer
cache/{content_hash}.wav       shared SFX
timelines/{content_hash}.json  analysis cache
ledger/{ulid}.json             cost rows
ledger/attr/…                  attribution overrides
assets/…  manifests/…          Genblaze
```

- One object per event (sorted ULID listing = ordered log).
- Immutable writes use `put_if_absent`.
- Status is **derived** by folding the event log — never a parallel status store.
- Listing uses `list_objects` metadata (no HEAD storm for dossier).

---

## 8. Data contracts (summary)

Full shapes live in code and `docs/api.md`. Core ideas:

| Concept | Notes |
|---|---|
| **Project** | Identity only; status/readiness derived |
| **Lifecycle event** | Domain-neutral types (`postproduction.*`); monotonic ULIDs |
| **Run** | Written at terminal state; lease bounds “running” |
| **Timeline event** | Placement, prompt, kind; optional frozen `seed` and `source_duration_s` |
| **QA report** | Loudness / true peak with targets; continuity as adjacent-shot comparison, never “fault” |
| **Provenance manifest** | Hashed inventory + lineage for export |

**Free edit vs billable:** placement and windowing never re-bill; regeneration
changes stem address and cost. Frozen seed makes a nudge *translate* texture
instead of re-rolling it.

---

## 9. HTTP API

Base path: `/api/v1`. Contract: `docs/api.md`.

### Principles

- Mutations that start work return **202** + `run_id` (poll the run).
- Video bytes go **client → B2** via presigned PUT.
- No general project DELETE / overwrite of completed generations.
- Errors: single envelope `{ error: { code, message, detail } }`.

### Endpoints (current)

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness, storage, build stamps; `?worker=true` probes GPU worker |
| `GET` | `/config` | Read-only operator config (integrations present/absent, never secrets) |
| `GET` | `/quota` | Per-model usage; `ledger_writable` |
| `POST` | `/projects` | Create + presigned upload |
| `POST` | `/projects/{id}/uploaded` | Confirm upload; hash + ffprobe |
| `GET` | `/projects`, `/projects/{id}` | List / detail + readiness + signed media URLs |
| `POST` | `/projects/{id}/analyze` | Analysis run |
| `GET`/`PUT` | `/projects/{id}/timeline` | Read / commit new immutable version |
| `GET` | `/timeline/versions`, `/scenes`, `/runs`, `/events` | Versions, rail, jobs, audit log |
| `POST` | `/projects/{id}/events/{index}/regenerate` | Targeted stem repair + cost confirm |
| `POST` | `/projects/{id}/mixes` | Mix/mux/QA; cache-miss projection |
| `GET` | `/projects/{id}/qa` | Latest QA |
| `POST` | `/projects/{id}/exports` | Publish mix + manifest |
| `GET` | `/projects/{id}/dossier` | Full artifact inventory + signed URLs |
| `GET`/`PUT` | `/projects/{id}/budget` | Advisory budget |
| `GET` | `/projects/{id}/ledger` | Per-project transaction trail |
| `POST` | `/projects/{id}/attributions` | Claim unattributed rows into project |
| `GET` | `/peaks/stem`, `/peaks/stems`, `/peaks/mix` | Waveform peaks |
| `GET` | `/preview/stems/{key}` | Same-origin stem stream (no B2 CORS) |
| `POST` | `/projects/{id}/director` | Director chat: proposed timeline diff, never auto-apply |
| `GET` | `/unclaimed/ledger`, `/projects`, `/cache` | Material no project claims |
| `POST` | `/unclaimed/projects/{id}/adopt`, `/unclaimed/ledger/claim` | Recovery |

---

## 10. Invariants

Breaking these breaks the product:

- **Determinism** — mix randomness from event name + start (or frozen seed), not
  process-local `hash()`.
- **ffprobe is the sole duration authority.**
- **Cache keys are addresses, not provenance** — bearer tokens not in SFX keys;
  fallback served under requested tier address + `served_endpoint`.
- **Manifests and completed generations are immutable.**
- **`providers.toml` from package path**, not CWD.
- **Regenerate indexes events by index**, not by name (names repeat).
- **Evidence-first UI** — no synthetic pass/fail for continuity.
- **Ledger process caches are backend-scoped** (`scope_id`) so two stores never
  share cached money or rows.

---

## 11. The control room (UI)

Svelte 5 + Vite + TypeScript; built into the image; served by FastAPI. Design
system: tokens, Chip/Evidence/ConfirmDialog/Kbd/…, light/dark/system with
explicit toggle.

### Routes

| Route | Role |
|---|---|
| `/` | Projects index — status, readiness, budget |
| `/projects/new` | Create, budget, file → presigned PUT → confirm → analyze |
| `/projects/{id}` | Control room |
| `/unclaimed` | Orphan projects, unclaimed ledger, shared cache listing |
| `/config` | Read-only deployment wiring (doctor-in-the-UI) |

### Control room capabilities

| Area | What shipped |
|---|---|
| **Readiness** | Checklist, percent meter, budget chip, ledger-writable warning |
| **Scene rail** | Thumbnails, spans, stats |
| **Timeline** | Kind lanes, real peaks, resizable grip, draft in localStorage, commit = new version, version chips as undo |
| **Direct edit** | Drag move, edge resize, arrow nudges (10/100/1 ms), rename, delete; free placement; frozen seed on touch |
| **A/B + preview** | Original vs foley mix; browser preview schedules stems on video (free loop); measured mix remains server truth |
| **Evidence / QA** | Loudness + targets; continuity as paired thumbs, not a fault |
| **History** | Versions, runs, event log |
| **Dossier** | Full B2 inventory with sizes and signed URLs |
| **Spend** | Budget, alerts, paginated ledger table |
| **Director mode** | Chat proposes schema-validated edits; ghost diff; human approve → `PUT /timeline`; every turn ledgered |
| **Regenerate / mix / export** | Cost confirmation; run lock while in flight; stuck-run on expired lease |
| **Polish** | Keyboard map (`?`), glossary/tooltips, consistent loading/empty/error, focus order, a11y gates in CI |

---

## 12. CLI

```bash
uv run foleyflow --help
```

**Project-scoped (API):** `create`, `show`, `timeline`, `scenes`, `qa`, `mix`,
`export`, `quota`, `budget`, `attribute`, `regenerate`, `dossier`, `prewarm`,
`health`, `doctor`, …

**File-scoped (local):** `render`, `direct`.

Task 23 may add an `auteur` console script alias without dropping `foleyflow`.

---

## 13. Providers

| Role | Model / service | Notes |
|---|---|---|
| Analysis | `gemini-2.5-flash` (default); OpenAI optional | Timeline cache by video+model+prompt |
| SFX test | MOSS worker | Default tier; free; warms shared cache |
| SFX paid / fallback | ElevenLabs / fal | Live-exercised (task 14) |
| Director | Gemini lite-class | High daily quota; ledgered per turn |

`GET /quota` and doctor surface usage and blocked models. Free-tier analysis
limits make cache + seed scripts load-bearing for demos.

---

## 14. Deployment

- **Cloud Run** (us-east4), single instance, CPU always allocated, 4Gi memory.
- **Secrets** in Secret Manager (`B2_*`, `GEMINI_API_KEY`, worker token).
- **Public config** in `env.public.yaml`; `scripts/deploy.sh` + post-deploy
  `smoke_deploy.py`.
- **Build identity** on `/health` (`git_sha`, `build_time`).
- **Dockerfile:** Node stage builds UI; runtime image is Python-only + static.
- **Brand:** image/UI build args `VITE_BRAND_*`; runtime `FOLEYFLOW_BRAND_NAME`.

---

## 15. Readiness and export policy

| Condition | Effect |
|---|---|
| No analysis / no plan / required stem failed | Block export |
| True peak above target | Block until fix or explicit override |
| Loudness outside target | Warning; normalize/re-mix |
| Exposure/color discontinuity | Warning + comparison evidence only |
| Artifacts + manifest in B2 | Enables export |

Automatic retry only for provider/network failure once. Artistic choices bill
only after confirmation.

---

## 16. What is built (feature map)

| Area | Status |
|---|---|
| Analyze → timeline (Genblaze) | Done |
| SFX tiers, cache, prewarm | Done |
| Deterministic mix/mux/QA | Done |
| Targeted regenerate + lineage | Done |
| B2 projects, runs, dossier | Done |
| Cost ledger, budgets, alerts, attributions | Done |
| Scope-safe ledger caches | Done (task 18) |
| Control room edit + A/B + preview | Done |
| Director mode (propose, don’t apply) | Done |
| Unclaimed archive | Done |
| Externalised brand → default **Auteur** | Done (19–20) |
| Operator `/config` screen | Done (21) |
| Polish, help, glossary, a11y CI | Done (22) |
| Repo/CLI/image rename to `auteur` | Pending (task 23) |
| Multi-user auth / SaaS tenancy | Not built (roadmap) |

Test suite (offline): **800 passed, 1 skipped** (handoff/SPEC). UI: vitest +
Playwright gates in CI. No provider credentials required for the offline suite.

---

## 17. Future roadmap

Product direction is detailed in **`docs/roadmap.md`**. Summary:

### Bring your own keys

Customers supply B2 and provider credentials. Auteur does not resell inference,
store customer masters on our behalf, or take a cut. Content, cost, and limits
stay with the customer.

### Option A — self-hosted / white-label (near)

- Single container, config-driven secrets, no phone-home.
- Brand already externalised (tasks 19–20).
- Customer perimeter (SSO/VPN) supplies access control Auteur does not yet
  implement.
- Needs: documented deploy (app + GPU worker split), licensing choice.

### Option B — multi-tenant SaaS (farther)

Requires **identity** (users, orgs, enforcement) and an **account store** that
is not the customer’s bucket (encrypted credentials cannot live in the bucket
those credentials open). Then: per-request storage resolution, isolated
ledger/quota, safe rotation, generation workers outside the web process.

### Design is not foley-specific

Without architectural change the same timeline + ledger + providers pattern
reaches:

- dialogue / ADR, voiceover, music and ambience beds;
- audio description;
- further out: timed b-roll, VFX passes, captions;
- **localisation / dubbing** as versioned timelines per territory with full
  cost and provenance (strongest non-foley case).

Foley is a demanding first proof: frame-accurate placement, deterministic
re-render, honest money.

### Deliberately not pulled forward yet

(From handoff — engineering sequencing, not product denial.)

- Single storage accessor for multi-tenancy (do with auth).
- `tenant_id` on every row under one tenant (dead weight today).
- Tenant key prefixes when BYO bucket is the boundary.
- Per-tenant quota timezones before tenant config exists.

---

## 18. Demo

Judges should see the **real deployed app**, a **seeded warm project**, and:

1. Control room on real timeline (not a slide of the UI).
2. **Listen** — silent picture → mixed foley alone (product audio, not only VO).
3. Director discipline — propose / ghost / human approve (or honest draft/commit).
4. Free nudge vs billable confirm.
5. Evidence (loudness, continuity as comparison).
6. Dossier / ledger provenance on B2.

Operational media plan (VO-first, time budget, ffmpeg assembly, OBS capture)
lives in local **`demoplan.md`** (gitignored). Do not burn analysis quota on
re-takes; use seed/cache scripts.

---

## 19. Success criteria (hackathon)

| # | Criterion |
|---|---|
| 1 | Real video in a B2-backed project |
| 2 | Genblaze stages visible and meaningful |
| 3 | Audible, synchronized sound design transformation |
| 4 | Useful technical or visual QA finding (honest presentation) |
| 5 | Targeted repair updates only the relevant stem/mix version |
| 6 | Export traces to source, prompts, models, QA, and B2 objects |

---

## 20. Related documents

| Doc | Role |
|---|---|
| `SPEC.md` | Product/engineering specification |
| `docs/api.md` | HTTP contract (source of truth for routes) |
| `docs/roadmap.md` | Self-host vs SaaS and modality future |
| `docs/design/control-room.md` | UI design decisions |
| `handoff.md` | Engineering task queue and landed work |
| `demoplan.md` | Local-only demo video production plan |
| `README.md` | Run / deploy entry |

---

## 21. Test and CI (summary)

- **pytest:** full offline suite; billed calls stubbed at stem/SFX seams;
  ffmpeg, mix, cache, storage, Genblaze run for real.
- **`scripts/check_test_count.py`:** handoff + SPEC counts must match runtime.
- **UI:** typecheck, vitest, production build; Playwright a11y/nav/performance
  gate in CI against built bundle.
- **Docker build** verifies UI contract on master path.

---

*Package and deploy identifiers may still say `foleyflow` where they are code
or ops surface; the product a user sees is **Auteur**.*
