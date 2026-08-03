# Handoff — development

Code work remaining on Auteur. Written 2026-08-02.

Sizes: **XS** one edit · **S** one file or one route · **M** spans modules,
needs tests · **L** a build in its own right.

Everything here is a code change. Operational work — credentials, recording,
repo visibility, deleting cloud resources — is deliberately out of scope.

---

## Orientation

- One package, `src/foleyflow`. One CLI, `foleyflow`. The HTTP contract in
  `docs/api.md` is written ahead of the code and treated as source of truth;
  every endpoint in it is live and covered by tests.
- Task numbers are the current queue; DONE items keep their titles. Remaining:
  **20** (rename to Auteur), **21** (configuration view), **22** (polish),
  then **23** (repo and image identity) once nothing else is in flight. Tasks
  **1–19** are **DONE**, including **7** — the control room is complete, and
  its polish pass was promoted out of `handoff-task7.md` into task 22 rather
  than staying a sub-unit of a finished build. `docs/roadmap.md` is the product
  document describing the two futures 18–21 enable; it is not an engineering
  plan and carries no task numbers.
- CI (10), build identity (11), post-deploy smoke (12), doctor (13), live paid
  + fallback SFX (14), demo evidence (15), and Secret Manager (16) are in place.
- `uv run pytest -q` → **800 passed, 1 skipped**. No provider credentials
  required for tests: the billed calls are stubbed at two seams,
  `stems.generate_many` and `sfx_cache.generate_canonical_wav`. Everything else
  — ffmpeg, the mix, the shared cache, storage, the Genblaze pipelines — runs
  for real.
- Genblaze is the orchestration spine. `providers/genblaze.py` wraps an Auteur
  `Adapter` in Genblaze's `SyncProvider` contract; `stems.py` is the fan-out;
  `stages.py` is the mix/mux/QA stage log; `tiers.py` resolves paid → fallback →
  test. Adding a provider means a connector plus a `providers.toml` entry, not a
  new code path.
- The GPU worker is a separate repo (`../moss-sfx-worker`), reached over HTTP as
  "one more provider with a base URL". Nothing here imports torch.

### Invariants — breaking these breaks the product

- **Determinism.** Same timeline in, byte-identical mix out. Mix randomness is
  seeded from a sha256 of each event's name and start time, never Python's
  per-process `hash()`.
- **ffprobe is the sole duration authority.** Not the model's self-reported
  duration, not the container metadata a provider returns.
- **Cache keys are addresses, not provenance.** The SFX cache key is
  `(endpoint, prompt, duration, variant)`. The worker bearer token is
  deliberately excluded — it is a transport credential, and rotating it must not
  orphan the ~170 warm stems in B2. A fallback-served clip is cached at the
  *requested* tier's address; who actually served it travels on
  `served_endpoint` in the stem inventory and the ledger event.
- **Manifests and completed generations are immutable.** `_put_json_immutable`,
  and `save_manifest` treats a re-export of the same run as a no-op.
- **`providers.toml` is resolved from the package source path**
  (`__file__/../../..`), not the working directory. It works in the container
  only because `uv sync` installs the project editable. A non-editable install
  breaks it at import.

---

## 1. Surface worker reachability in `/health` — S — **DONE**

`GET /api/v1/health?worker=true` probes via `LocalSfxAdapter.health()` and returns
a non-fatal `WorkerHealth` field. Off by default so platform liveness checks do
not depend on the GPU box; `foleyflow health` always asks for it. Unreachable or
auth-misconfigured workers print a warning but still exit 0.

## 2. `POST /projects/{id}/events/{index}/regenerate` — M — **DONE**

`POST /api/v1/projects/{id}/events/{index}/regenerate` maps an event index to
its `SfxRequest`, allocates a new timeline version beside the original, and
re-mixes through `render_mix(variant_fn=…)` with a per-repair variant salt on
exactly one stem's cache address — one stem's bytes change, every other stem's
`b2_key` is identical across the two runs. The lineage names the superseded
take: `postproduction.repair.approved` (and the run's metadata) carry the
previous mix's address for that request, the new variant, and the version.
`confirm_cost_usd` must match the estimate for a billable regeneration (SPEC
§9.4); `provider` re-aims the request at a declared SFX tier. Acceptance
verified end to end through the route: target stem moves to a new key, the
rest of the inventory is byte-for-byte unchanged, the mixes differ, and the
lineage names the superseded `b2_key`.

## 3. `GET /projects/{id}/dossier` — M — **DONE**

`GET /api/v1/projects/{id}/dossier` composes every artifact already in B2 —
source, scene index and thumbnails, every timeline version, stem inventories and
the stem clips themselves, mixes, the QA report, the event log, run records,
manifests, and the export pointer — each with its storage key (provenance
only), size, recorded sha256, and a signed URL at the standard TTL.

Composition only: nothing writes, nothing downloads. Size comes from one
backend HEAD per object via the new `Backend.head` (`LocalBackend` hashes the
local file; B2's S3 API exposes no sha256, so that column is the recorded value
or absent in a deployed store). Hashes are provenance where the system records
one — the source video and each stem — never recomputed or invented. One entry
per physical object: content-addressed stems shared across runs are listed once.

## 4. Route analysis through Genblaze — M — **DONE**

Analysis is a one-step Genblaze pipeline via `AnalysisProvider` (Google and
OpenAI lifts in `providers/genblaze.py`). Same ledger seam as SFX; response
envelope stays `{"output": ...}`. Default VLM is Gemini (`gemini-2.5-flash`);
OpenAI `gpt-5.6-luna` is optional via `--model`. Video is frame-sampled in the
OpenAI adapter. TIMELINE_PROMPT and the timeline cache key are unchanged.

## 5. Move the cost ledger into B2 — M — **DONE**

Default ledger is `ledger/{ulid}.json` on the shared storage backend (one
immutable object per call via `put_if_absent`). `FOLEYFLOW_LEDGER_PATH` remains
as an optional local-JSONL override for tests; it is not the deploy path.
Failed storage writes mark the process unwritable so `ledger_writable()` is
honest without double-billing a successful generation.

## 6. Per-project budgets — M — **DONE**

*(Superseded by task 9: the budget is now advisory and mutable, not a hard
cap frozen at creation. Analyze/regenerate are not refused on budget
grounds. Mix pre-flight projection via `project_mix_cost` prices cache
misses at the door — read 9, not this paragraph, for current behaviour.)*

A project carries an advisory dollar figure and every billed row is attributed
back to it. `log_transaction` rows carry `project_id` from a contextvar the
job runner sets per run — analysis, mix and repair all tag their rows with no
call-site change; a direct `foleyflow render` stays unattributed (global)
unless `--project` opts it in. The CLI has a first-class current project —
positional id > `--project` > `$FOLEYFLOW_PROJECT` > `foleyflow use`'s
persisted default — and `foleyflow budget` reads the same endpoint the UI
will. Project-less rows are never stranded: `POST /projects/{id}/attributions`
(and `foleyflow attribute`) writes an immutable rescue override claiming
unattributed rows written before a time, first-come by `before` so a row can
never land in two projects.

## 7. Control room UI — L — **see `handoff-task7.md`** — **DONE**

SPEC §6.1, and the only thing left. Scoped in full in its own file because it
outgrew a section: it is not a status page but a director's tool — a directly
editable timeline, an AI addressed in text that proposes edits rather than
applying them, and a browser-side preview so the edit loop costs nothing.

**Backend progress:** units **7.1–7.5** are done (frozen seed, free resize,
timeline write, signed project URLs, waveform peaks). **7.6 landed**: Svelte 5
+ Vite 8 toolchain in `ui/`, TS types generated from the emitted OpenAPI
schema, `api/dashboard.py` serves the built SPA with a deep-link catch-all and
an explicit "UI not built" page, Dockerfile gains a Node stage (no Node ships
in the runtime image), CI gains a UI build job, the throwaway `index.html` is
deleted. **7.7 landed**: design tokens per `docs/design/control-room.md`
(`theme.css`, light/dark/system with explicit-toggle wins, theme persistence
+ pre-paint script) and twelve primitives in `ui/src/lib/components/` — Chip
(glyph+word, colour never alone), Evidence (source required structurally),
StageRow, SceneThumb, ConfirmDialog (cost-confirmed, focus-managed), Timecode
(`formatTimecode`/`formatCost`), Meter, Panel, Kbd, Spinner, Icon, Button —
plus Vitest (`npm test`, 20 tests, SSR contracts) and a dev-only gallery.
**7.8 landed**: the read-only control room (`ui/src/lib/control/`) —
`/projects/{id}` route, chrome with readiness meter + budget chip
(estimate tag, ledger-writable warning), explicit unfinished video slot
(player is 7.9), real scene rail, rail tabs Readiness / Evidence (loudness
with targets + continuity comparisons, never a fault) / History (versions,
runs, event log, latest mix's real peaks) / Dossier, and a read-only
kind-lane timeline with a resizable separator grip. Verified against the
live seeded `standoff_kling` project. `npm test` 54, svelte-check 0/0.
**7.9 landed**: A/B player (Original / Foley mix, playhead preserved,
K/Space + arrows) and the browser preview — real stems scheduled over the
original video at their transient offsets via Web Audio, labelled "not the
measured mix". Two honest enablers: stamped `Event.stem_key` (event→audio
link, refreshed per run) and `GET /preview/stems/{key}` (same-origin stem
streaming — B2 has no CORS). Transient hit-point ticks on the timeline
ruler. Suite 718, npm test 62, svelte-check 0/0. Remaining: frontend chain
from 7.10. **7.10 landed**: direct manipulation — drag to move, edge
handles to resize (10 ms snap, ⌥ to override), arrow-key nudges (10/100/1 ms),
inline rename and delete, all landing in a **persisted draft** (localStorage,
restored on reload) that commits as exactly one new immutable version
(`PUT /timeline`, whole document); version chips switch committed versions
read-only and "Edit from here" re-forks the draft from any of them — undo is
version switching, not a mutation stack. The editor freezes the mix `seed`
onto any rhythmic event it touches at the base position (Python-exact sha256
emulation — parity pinned against hashlib vectors, including the dyadic ties
where JS `toFixed` would diverge), and the mix now stamps the seed it
actually used for rhythmic events, so a nudge translates instead of
re-rolling the texture. Renames on frozen content are refused at commit with
the server's own explanation; a draft whose base went stale under a newer
version offers to merge the latest's recorded freezes. The browser preview
follows the draft, so the edit loop is free and instant. npm test 202,
svelte-check 0/0. **7.18 landed** (phase-2): write-through exact ledger
aggregates for spend/quota, event-log state checkpoint, shared I/O thread
pool; harness `scripts/latency_bench.py`. Earlier: concurrent multi-get,
cached `ledger_writable`, single-flight row cache, cheap GET /qa, parallel
project/budget I/O, concurrent dossier HEADs. **7.16 landed**: the ledger
rows endpoint `GET /projects/{id}/ledger` (docs/api.md §3.14) — per-project,
paginated, newest-first audit trail behind `/budget`, failed rows included,
rescue-claimed rows visible, reconciling with the summary; `iter_attributions`
cached (10 s, invalidated by the one writer) so pages don't pay a LIST each.
The Spend tab gains the "Ledger — individual transactions" table (est/actual
per row, failed chips, newer/older pagination) behind the same fail-closed
`ledger_writable` rule, loaded lazily on first open. **7.19 landed**: object
listing without HEADs — `Backend.list_objects(prefix)` returns
Size/LastModified/ETag (`list_objects_v2` already carried them; `list_keys`
threw them away), LocalBackend reads the same from `os.stat` plus its usual
content hash, and the Dossier composes from a few top-level LISTs instead of
one HEAD per object (the measured 56 s for 131 keys). `size_bytes` unchanged,
sha256 never derived from an ETag; docs/api.md §3.9 updated. **7.17 landed**:
the Unclaimed screen (docs/api.md §3.15) — the material no project claims.
Orphan ledger rows (no project_id, no rescue override) are listed with signed
URLs and claimable into an existing project or a newly-created spend archive;
orphan `projects/{id}/` prefixes (no readable `project.json`) are listed with
what they cost and adoptable by writing their record (an unreadable record is
the one deliberate overwrite, recorded); shared `cache/` stems are shown under
a "never claimable" caveat — keys are addresses, not provenance. UI at
`/unclaimed`, linked from the projects index; the ledger section fails closed
on an unwritable ledger. npm test 202.

The throwaway static dashboard (`api/static/index.html`) is deleted outright
when 7.6 lands.

Three things about the current state that the earlier version of this section
got wrong, and that shape the work:

- **Readiness is already rendered** — `index.html` draws the percent bar and
  checklist rows. What has no UI at all is far larger: no video player, no A/B,
  no QA report, no stage progress, no repair action, no dossier, no budget.
- **The API does not already serve everything it needs.** There is no timeline
  write path at all (7.3). Source and mix signed URLs now live on the project
  record (7.4); dossier remains the full inventory.
- **The timeline data model and write path are in** — frozen seed (7.1),
  `source_duration_s` / free placement resize (7.2), and `PUT /timeline`
  with freeze conservation (7.3).

Evidence-first, per SPEC §8.4: show the measured number and its source, never a
synthesised verdict. QA warnings are review prompts, not automatic corrections —
the visual continuity check in particular is an adjacent-shot mean-luma
comparison and must never be presented as a detected fault.

## 8. Expose stem pre-warming as a CLI verb — S — **DONE**

`foleyflow prewarm <timeline.json> [--sfx-tier paid|fallback|test] [--tier full]
[--dry-run]`. Sets `$FOLEYFLOW_SFX_TIER` before `plan_requests` (and restores it
after), probes hits/misses, generates only the misses via `sfx_cache.prewarm`.
`--sfx-tier` is the SFX provider tier; `--tier` is the mix degradation ladder —
they are not the same axis.

---

## 9. Budget alerts — M — **DONE**

Soft, advisory budget. Mutable via `postproduction.budget.set` events
(latest-wins); `PUT /projects/{id}/budget` and `foleyflow budget --set|--clear`.
Alerts fire from the ledger append at configurable fractions (default
50/80/100/120%) with `put_if_absent` idempotency per
`(project, budget figure, threshold)`. **9b addendum:** running per-project
spend is seeded once (before the triggering write) then incremented O(1) —
never a full ledger re-scan per stem (the row cache is invalidated on write, so
it cannot absorb re-summing). Mix 202 carries a cache-miss cost projection
(shared with `prewarm --dry-run`). Hard refusals removed; `confirm_cost_usd`
stays. Unwritable ledger → alerts unreliable, work continues.

## 10. Continuous integration — S — **DONE**

GitHub Actions (`.github/workflows/ci.yml`): ffmpeg required on PATH, `uv sync
--frozen --group dev`, then `scripts/check_test_count.py` which runs the full
suite and asserts handoff/SPEC match runtime `passed` and `skipped` exactly.
Permissions `contents: read`, concurrency cancel-in-progress, 25m job timeout.
No provider credentials. Offline suite only.

## 11. Report the deployed build in `/health` — S — **DONE**

`HealthResponse` carries `git_sha` / `build_time` from `FOLEYFLOW_GIT_SHA` /
`FOLEYFLOW_BUILD_TIME` (Dockerfile ARG for image builds; runtime
`--update-env-vars` for `gcloud run deploy --source`). Sentinels and non-hex
SHAs become `null`. `foleyflow health` prints both stamps. README documents
both deploy paths.

## 12. Post-deploy smoke check — S — **DONE**

`scripts/smoke_deploy.py --base-url URL [--expect-sha SHA]`: health, storage,
worker status present, git_sha match, ledger_writable true, projects list, and
dossier (or OpenAPI). Wired as the last step of `scripts/deploy.sh` (fail
deploy on smoke fail; `--skip-smoke` emergency only). Offline unit tests cover
stale SHA and unwritable ledger.

## 13. `foleyflow doctor` — M — **DONE**

`foleyflow doctor [--timeline PATH]`: checklist for API, storage, worker (always
probed), ledger_writable (strict True), any blocked quota model, git_sha vs
local HEAD (null on non-local API is STOP), optional local SFX cache coverage
(labelled process-local, not remote). Exit non-zero on STOP; health stays 0
when only the worker is down.

## 14. Exercise the paid tier and the fallback for real — M — **DONE**

Live exercise 2026-08-02 via `scripts/exercise_tiers.py` (results in
`scripts/task14_results.json`).

- **Paid:** unique prompt → ElevenLabs `eleven_text_to_sound_v2` served;
  stem at `cache/<key>.wav` under the paid endpoint; ledger row
  `$0.30` / `cost_source: estimate` / `project_id` attributed
  (`LedgerRecordingMixin`).
- **Fallback:** invalid ElevenLabs key → auth_failure → fal
  `fal-ai/elevenlabs/sound-effects/v2` serves; stem inventory and §11.6
  manifest both carry `served_endpoint` / `served_by: fallback` while
  `endpoint` stays the *requested* paid address.

Re-run: `FAL_KEY=… uv run python scripts/exercise_tiers.py` (loads
`ELEVENLABS_API_KEY` from `.env`; needs B2 + ffmpeg).

## 15. More evidence in the bucket — S — **DONE**

Three ready projects in B2 (standoff + duel_kling + inhaler_kling), each with
mix, export, and manifest. `inhaler` prewarmed 9/9 on test tier; duel already
warm. Helpers: `scripts/seed_demo.sh` (requires DUEL_VIDEO/INHALER_VIDEO),
`seed_timeline_cache.py`, `demo_evidence.md`. Timeline cache seed used so
analyze does not burn Gemini quota.

## 16. Move deployment secrets into Secret Manager — S — **DONE**

Live on `foleyflow` (us-east4 / backblaze-504305): plain env values replaced
with Secret Manager refs (`valueFrom.secretKeyRef`). Names:
`foleyflow-b2-application-key`, `foleyflow-b2-key-id`,
`foleyflow-gemini-api-key`, `foleyflow-sfx-worker-token`. Public config in
`env.public.yaml`; helpers `scripts/setup_secrets.sh` + `scripts/deploy.sh`
(`--set-secrets` replaces the full secret set). Verified via describe: four
secrets are refs, not values.

## 17. Alert when a budget is set below what is already spent — S — **DONE**

`set_budget` evaluates thresholds via `evaluate_budget_alerts` (full
`project_spend` scan, not the process-local running total). Same-value re-PUT
is a no-op. Clear/raise silent; lower beneath spend emits once per threshold
with `reason: "budget_set"`. Alert failures never fail the PUT.

---

Tasks 18–20 enable the two futures `docs/roadmap.md` describes —
self-hosted/white-label and multi-tenant SaaS, both bring-your-own-keys. These
three are the only enablers that **earn their place today**: each is deployable
now and useful now, independent of whether either option is ever taken.

**Deliberately not pulled forward** (internal reasoning; the roadmap is a
product document and does not carry it):

- *Routing store access through a single accessor.* The highest-leverage seam
  for multi-tenancy, but pure churn across every route with no present
  benefit. Do it when authentication lands, not before.
- *Writing `tenant_id` onto ledger rows and run records.* Under one tenant
  every row belongs to that tenant, so the field is dead weight and
  retroactive attribution is trivial anyway.
- *A tenant prefix in the key space.* Bring-your-own-storage makes the
  customer's bucket the boundary; the prefix scheme is moot.
- *Per-tenant quota reset timezone.* Arrives with tenant configuration.

## 18. Key the ledger's process caches by backend scope — S — **DONE**

`Backend.scope_id()` is the process-cache key: LocalBackend uses a canonical
absolute root (`local:{realpath}`), B2Backend uses `b2:{bucket}/{prefix}`,
file-mode ledger uses `file:{realpath}`. Ledger row/attr/spend/quota/
writable-probe caches and the fail-closed `write_ok` pin live on a per-scope
`_ScopeState` (epochs and single-flight preserved; LRU caps retained scopes).
Hot paths bind `(scope_id, backend)` once so a mid-flight rewire cannot
cross-install. Factory caches backends by config fingerprint so warm
`scope_id` hits do not rebuild boto3 clients. Event-log checkpoints are
keyed by `(scope_id, project_id)`. Fixtures no longer hand-invalidate ledger
caches on backend swap — a fresh temp-dir is a fresh scope. Isolation tests
cover A→B→A for rows, spend, attrs, quota, write_ok, and event logs.

## 19. Externalise the brand — S — **DONE**

**Depends on:** nothing. **Files:** `ui/index.html`, `ui/src/App.svelte`,
`ui/src/lib/control/ControlRoom.svelte`,
`ui/src/lib/projects/ProjectsIndex.svelte`,
`ui/src/lib/projects/CreateProject.svelte`, `ui/vite.config.ts`.

The whole brand surface is eight `FoleyFlow` strings: one internal error
message (`main.ts`), three `document.title` variants (`App.svelte:16-19`), two
chrome wordmarks (`ControlRoom.svelte:639`, `ProjectsIndex.svelte:81`), one
back-link (`CreateProject.svelte:432`), and the `<title>` plus inline SVG
favicon in `ui/index.html`.

Move the product name, title template and favicon behind build-time
configuration with the current values as defaults, so a deployment can rebrand
without a patch. Colours already need nothing: `ui/src/lib/theme.css` defines
97 tokens on `:root` and components consume tokens rather than raw values
(unit 7.7), so an accent override is a variable file.

Leave internals alone — the CLI (`foleyflow`), the package, `FOLEYFLOW_*` env
vars and the storage key space (`projects/`, `ledger/`, `cache/`) are
brand-neutral or never rendered. Externalise only what a user sees.

**Acceptance:** a build with the branding variables set renders no occurrence
of the default name anywhere in the served UI, and the default build is
byte-identical in appearance to today.

**Landed:** `ui/src/lib/brand.ts` (`BRAND_NAME`, `BRAND_TITLE_TEMPLATE`, a pure
`formatPageTitle`, and `brandPageTitle`) reading `VITE_BRAND_*` at build time;
`vite.config.ts` injects the static `<title>` and favicon from the same
template so pre-hydration and runtime titles cannot drift; the Dockerfile UI
stage takes `VITE_BRAND_*` build args. Ten rendered strings, not the eight
scoped here — Director mode's two chat sender labels landed after this file
list was written and render the name too.

CI enforces the acceptance at the **bundle** level, which is the only place it
is real: `import.meta.env` overrides cannot be exercised from vitest, so the
job builds twice and greps `api/static/` — the default build must carry the
brand and the exact default title, the rebranded build must contain zero
occurrences of it.

**Review follow-up (landed):** the brand also shipped from the *server*, which
the bundle grep does not cover — `FastAPI(title=...)` (served at
`/openapi.json` and rendered on `/docs`) and the "UI not built" fallback page,
which is what a self-hosting partner sees exactly when their build failed.
Both now read `FOLEYFLOW_BRAND_NAME` via `api/brand.py`, the same import-time
contract as `GIT_SHA`. Separately, `formatPageTitle` used `String.replace`
with a string pattern, which substitutes only the first occurrence — a
`{name} — {page} — {name}` template rendered the second placeholder
literally; `replaceAll` throughout, pinned by a test.

**Landed (19):** `ui/src/lib/brand.ts` — `BRAND_NAME`, `BRAND_TITLE_TEMPLATE`
and `brandPageTitle(page)` read `VITE_BRAND_NAME` / `VITE_BRAND_TITLE_TEMPLATE`
at build time (Vite statically replaces `import.meta.env.VITE_BRAND_*`), with
today's values as defaults. All eight strings — plus the two Director-mode
chat sender labels, which landed after this task's file list was written and
render the name too — now go through the brand module. `vite.config.ts` owns
the `<title>` and favicon: a `transformIndexHtml` plugin injects both from the
same env (default favicon is today's inline SVG waveform mark, moved into the
config), using the same title template as the SPA so the pre-hydration title
and the runtime titles cannot drift apart under a rebrand. The Dockerfile's
UI stage declares optional `VITE_BRAND_*` ARGs, so `docker build
--build-arg VITE_BRAND_NAME=…` rebrands the shipped image without patching it
(unset → defaults). Colours untouched.

**Review round (after landing):** two *server*-rendered surfaces still shipped
the name outside the task's file list, and the acceptance said "anywhere in
the served UI": the FastAPI title (`app.py`, served at `/api/v1/openapi.json`
and rendered by `/docs`) and the "UI not built" fallback page
(`dashboard.py` — the page a self-hosting partner sees when their build
failed). Both now read `FOLEYFLOW_BRAND_NAME` (default `FoleyFlow`) from
`src/foleyflow/api/brand.py`, a module constant with the same contract as
`GIT_SHA`/`BUILD_TIME`; the fallback page substitutes the name per request (the
CSS braces rule out `str.format`). A rebrand sets the runtime var here and the
build-time `VITE_BRAND_NAME` for the UI. Also: the title template now uses
`replaceAll` — `String.replace` with a string pattern substitutes only the
first occurrence, so a template that repeated a placeholder rendered the later
owne literally; every occurrence is replaced now, pinned by a unit test.

**Verification is build-level, not unit-level:** CI builds once with defaults
and once with `VITE_BRAND_NAME="KinoPost"` plus a custom template and favicon,
and greps the *bundles* — the default build must still name the brand, and the
rebranded build must contain no occurrence of `FoleyFlow` anywhere in the
served output (task 19's acceptance, checked on every PR). `brand.ts` has unit
tests pinning the defaults; the override path cannot be exercised in vitest
(env is loaded at startup), which is exactly why the CI check exists.

## 20. Rename the product to Auteur — S — **DONE**

**Depends on:** 19 (the brand mechanism). **Files:** `ui/src/lib/brand.ts`,
`ui/vite.config.ts`, `src/foleyflow/api/app.py`,
`src/foleyflow/api/dashboard.py`, `pyproject.toml` (description),
`README.md`, `SPEC.md`, `docs/*.md`, `.github/workflows/ci.yml`.

**Why the name changes.** "FoleyFlow" describes the first application, not the
system. The domain model contains no foley-specific concept — projects,
timelines, events, scenes, stems, mixes, runs and manifests are all generic
post-production vocabulary — so the name is the only thing binding the product
to one modality, and it fights `docs/roadmap.md`'s position that the design
reaches dialogue, ADR, voiceover, music beds, localisation and audio
description. **Auteur** is a film term rather than a coined one, it is
modality-neutral, and it matches how the product has described itself since
`docs/design/control-room.md`: a director's tool.

**This is a defaults change, not new machinery — hence S.** Task 19 already
made the name, the title template and the favicon build-time configuration,
and CI already builds twice to prove a rebranded bundle carries no trace of
the default. This unit changes what the default is.

- Brand defaults in `brand.ts` and `vite.config.ts`.
- **Invert the CI rebrand fixture.** The job currently proves "a build
  parameterised as KinoPost contains zero `FoleyFlow`". After this it must
  prove the reverse — the old name becomes the fixture — or the guarantee
  quietly stops pointing at anything.
- `FastAPI(title=...)` in `api/app.py`: served at `/openapi.json` and rendered
  on `/docs`, so it is user-visible and the task-19 grep does not cover it.
- The "UI not built" fallback in `api/dashboard.py` (`<title>` and `<h1>`) —
  the page a self-hosting operator sees exactly when a build has failed.
- `README.md`, `SPEC.md`, `docs/*.md`, and the `pyproject.toml` description.

**Why this goes before 21 and 7.14.** Both create new rendered surfaces: 21's
configuration view has headers and copy naming the product, and 7.14's polish
pass tunes the wordmark, the favicon and every piece of user-facing copy.
Polishing copy under one name and then changing it means reading that copy
twice. Task 19 hit this exact failure mode — DirectorPanel's two sender labels
landed after 19's file list was written and nearly failed its acceptance.

**Not in this unit:** repo and image identity (task 22); the Python package,
which is an import path and never rendered; `FOLEYFLOW_*` env vars, which are
deployment configuration and would break `env.public.yaml`, the Secret Manager
wiring from task 16 and `scripts/deploy.sh` in exchange for nothing anyone
sees; `mix_video_key`'s `foley.mp4` (see task 22); and `TIMELINE_PROMPT`.

**`TIMELINE_PROMPT` is not branding.** "You are a professional Foley artist" is
an instruction that shapes model output. Changing it moves the output
distribution, invalidates the timeline cache (the cache key includes a prompt
fingerprint) and needs re-validation against the sample timelines — real work,
unrelated to a rename, that burns Gemini quota to verify. When the product
genuinely addresses a second modality the prompt becomes per-modality
configuration; that is a feature, not a rename.

**Acceptance:** a default build serves the new name in the SPA title, the
chrome wordmark, `/docs` and the UI-not-built page; CI's rebrand job proves a
build parameterised with any other name contains zero occurrences of "Auteur"
in `api/static/`; and `grep -rni foley` returns only the residue this unit
names as out of scope.

**Landed (20):** defaults changed `FoleyFlow` → `Auteur` across the brand
surface — `ui/src/lib/brand.ts`, `ui/vite.config.ts` (UI), and
`src/foleyflow/api/brand.py` (`FOLEYFLOW_BRAND_NAME` default — the `/docs`
title and the UI-not-built page follow automatically). The CI rebrand job is
**inverted**: the old name is now the parameterised fixture (a build with
`VITE_BRAND_NAME=FoleyFlow` must contain zero `Auteur`, and the default build
must carry `Auteur` in the title and the bundle), so the guarantee points at
the current default. Product references swept in `README.md`, `SPEC.md`,
`docs/*.md` (incl. the §3.17 example), `foleyflow.md`, the mockup,
`ui/README.md`, `providers.toml`, the Dockerfile comment, the deploy/seed
scripts, and every code docstring/comment that named the product — plus the
two user-visible code surfaces: the CLI's argparse description and the
director-mode prompt's persona line (no cache, so the prompt tweak is
inert — unlike `TIMELINE_PROMPT`, which stays untouched for the reasons
above). `pyproject.toml` needed nothing: the description never named the
product and `name = "foleyflow"` is the package (out of scope). The
task-21 config screen needed nothing either — it consumes `BRAND_NAME`, and
the brand-agreement row now compares a default UI "Auteur" against a
default API "Auteur".

`grep -rni foley` now returns only the named residue: the package/CLI
(`foleyflow`, module paths), `FOLEYFLOW_*` env vars (including
`FOLEYFLOW_BRAND_NAME`, which keeps its name — it is deployment
configuration), `mix_video_key`'s `foley.mp4` (task 22), the domain term
"foley" the art form (incl. `TIMELINE_PROMPT`'s "professional Foley
artist"), and the work logs (`handoff*.md`, whose task-20 spec text names the
old name by design). Tests updated to the new default (brand module,
ConfigScreen fixture + assertions, dashboard default-brand assertions, the
OpenAPI title, smoke fixture); the KinoPost override tests now also assert
`Auteur` does not leak into a rebranded page.

## 21. Read-only configuration view — M — **DONE**

**Depends on:** nothing. **Files:** `docs/api.md` first, `api/schemas.py`,
`api/app.py`, a UI route. Builds on the existing component layer and design
tokens (`Chip`, `StageRow`, `Panel`, `ui/src/lib/theme.css`) — the checklist
rows this needs are the ones readiness already renders.

`foleyflow doctor` (task 13) already computes exactly what an operator needs to
know — API reachable, storage reachable, worker probed, `ledger_writable`,
blocked quota models, `git_sha` against local HEAD, optional SFX cache coverage
— and it exists only as a terminal command. Nothing in the UI answers "is this
deployment wired up correctly?", which is the first question after any deploy
and the first question a self-hosting partner will ask.

Surface the same checks as a screen: which integrations are configured
(present/absent and which source — Secret Manager reference, env var — **never
a value**), worker reachability and whether it wants a token this deployment
lacks, deployed `git_sha` and build time from `/health`, and ledger writability
with the §3.10 fail-closed consequence stated where it is read.

**Read-only, and that is the point.** Writing credentials through the API is
out of scope and stays out: the service deploys `--allow-unauthenticated`
(`scripts/deploy.sh:143`) with no authentication anywhere in the app, so a
mutation route would be an open credential endpoint. Rotation stays a Secret
Manager operation (task 16), and stays that way until authentication exists.

**Any rendered product name comes from `ui/src/lib/brand.ts`** (`BRAND_NAME` /
`brandPageTitle`), never a literal — task 19 made the brand build-time config
and CI proves a rebranded build carries no trace of the default. A hardcoded
name here would fail that job, and this screen is one a white-label operator
looks at first.

**Report the brand configuration, and detect a split one.** Task 19 left the
name configured in two places by two mechanisms at two different times:
`VITE_BRAND_NAME` is baked into the bundle at `docker build`, and
`FOLEYFLOW_BRAND_NAME` is read from the environment at runtime
(`api/brand.py`). A rebranding deployment must set both, and they are separated
by a deploy step — so setting one and forgetting the other is the *normal*
failure, not an exotic one. Set only the build arg and `/docs` plus the
UI-not-built page keep the old name; set only the env var and the entire SPA
does. Nothing detects it today.

The check is cheap and needs no new mechanism: the SPA already knows its own
compiled-in `BRAND_NAME`, so it compares that against the value the API
reports and says so when they disagree. Same shape as `doctor`'s `git_sha`
versus local HEAD, and the same reason — a deployment that half-applied a
change should say so rather than look fine from one angle.

**Acceptance:** every check `foleyflow doctor` performs is visible in the UI
with the same verdict, no credential value appears in any response or in the
rendered page, a deployment whose build-time and runtime brand disagree is
reported as such rather than rendering as two different products, and CI's
rebrand job still finds zero occurrences of the default brand in the built
output.

**Landed (21):** `GET /api/v1/config` (docs/api.md §3.17) + a `/config` route
rendering `ui/src/lib/config/ConfigScreen.svelte`.

- **One read-only sheet, same verdicts as doctor — enforced mechanically, not
  asserted in prose.** The endpoint computes the five shared checks
  (api/storage/worker/ledger/quota) with doctor's exact rules — a strict-`true`
  `ledger_writable` (§3.10's "spend figures are fiction"), ANY quota-blocked
  model as a STOP, a worker that demands a bearer token this deployment lacks
  as a STOP even though `/health` reads healthy — and a parity test runs
  `foleyflow doctor --json` and `/config` against the same app and requires
  identical key/state/label/detail for every shared row. `ok` is true exactly
  when no server check is blocked.
- **Integrations with presence-only, never a value.**
  `src/foleyflow/api/integrations.py` declares each integration (B2, Gemini,
  OpenAI, ElevenLabs, fal, SFX worker URL, SFX worker token), its env var
  names, and its contract source — `secret-manager` for the vars
  `deploy.sh`/`setup_secrets.sh` bind via `--set-secrets`, `env` for
  `env.public.yaml` — and reports `configured`/`missing`/`unresolved` with
  NAMES only. A var whose value is a literal Secret Manager reference
  (`projects/{p}/secrets/{s}/versions/{n}` — the classic mistake of putting
  the reference in env.public.yaml) is detected by shape (numeric version or
  `latest`) and called out by name. The "no credential value" acceptance is
  tested both sides: the API test sets real-looking secrets and asserts the
  body carries none, and the UI test pins the page never renders them.
- **Build identity, browser-honest.** The screen shows deployed `git_sha`/
  `build_time` from the stamp, with doctor's origin-based severity when
  unstamped (warn on a loopback origin, blocked on a non-local one). The
  CLI's "matches local HEAD" comparison stays CLI-only — a browser is not a
  git checkout — and the optional cache-coverage probe stays CLI-only too (it
  reads the operator's local storage for a timeline file); both are stated in
  the docs.
- **Brand agreement (this section's own acceptance clause).** The response
  carries the API's runtime name (`FOLEYFLOW_BRAND_NAME` — the name `/docs`
  and the UI-not-built page render); the screen compares it against the UI
  bundle's compiled-in `BRAND_NAME` and renders a blocked "brand agreement"
  row naming both when they disagree — a deployment that half-applied a
  rebrand renders as two different products, and this is where it says so.
- **The screen** reuses `ChecklistRow` for the checks (the readiness rows), a
  neutral "Absent" tag for missing integrations (never overloading a semantic
  chip state), the source as a plain tag, monospace numerics, and a
  read-only note stating rotation stays a Secret Manager operation.
- **Tests:** 16 API contract/verdict tests (storage down, worker unreachable,
  worker auth-missing, ledger unwritable, quota blocked/warn, a failed
  quota/ledger read degrading to blocked rows — never a 500 — through both a
  stubbed `get_all_quotas` and the real storage fetch path, integration
  presence + any-of keys, unresolved-reference detection, never-a-value,
  brand in the payload) + 1 doctor↔config parity test + 9 ConfigScreen render
  tests (checks, integrations, unresolved reference, unstamped-build
  severity, brand agreement + disagreement, never-a-value) + route tests.

**Review round (after landing):** the docs promised `503 storage_unavailable`
when storage cannot answer, but a full B2 outage surfaced as an unhandled
`StorageError` from the quota read — a 500 — the one failure mode the sheet
must survive (it is the screen for diagnosing outages). Fixed by folding a
failed quota/ledger read into blocked "quota probe failed" rows — the same
rows `foleyflow doctor` prints when its `/quota` probe fails — so a failure
renders the same states and labels; the docs' 503 row is gone (the sheet now
always 200s with the outage visible as blocked rows).

## 22. Polish — L

**Depends on:** 20 (the rename, so copy is written once) and 21 (the
configuration view is a screen this pass covers). **Files:** every route and
component under `ui/src`, a new tooltip component and a glossary module,
`docs/design/control-room.md`, CI.

**Sized L, not M.** It began as a pass over existing surfaces; it now also
builds two things that do not exist — a real tooltip/popover and the glossary
behind it — and a terminology review touches every rendered string. That is a
build in its own right, and calling it M would have under-planned the last
task before a demo.

Promoted out of `handoff-task7.md`, where it sat as unit 7.14 behind a
one-line stub. The control room is otherwise complete — timeline editing,
A/B preview, spend paths, director mode, the unclaimed archive — so this is
no longer a sub-task of building it. **This is the pass that decides whether
it reads as a tool**, and it is the last thing between the app and a demo.

Four routes are in scope: the projects index (`/`), create (`/projects/new`),
the control room (`/projects/{id}`), and the unclaimed archive (`/unclaimed`).

**The keyboard map, which does not exist.** Bindings were added per unit and
never assembled: arrow nudges on a timeline block (10 ms, Shift 100 ms, Alt
1 ms), arrows on the resize handles, arrows on the panel grip, Enter/Escape in
inline rename, and the dialogs' own focus rules. Nothing tells a director any
of this exists. The `Kbd` component was built in 7.7 **for exactly this** and
is used nowhere outside `PrimitivesGallery.svelte` — a component built for a
hint line and a keyboard map, with neither ever built. This unit writes the
map, makes it reachable (`?` is the convention), and adds the hint line the
component was designed for. A binding that is not in the map is a bug in one
of the two.

**Focus order.** `docs/design/control-room.md` §9 lists it as open, and it has
stayed open. Tab through each route: the order must follow the visual order,
nothing interactive may be unreachable, and nothing may trap focus except a
dialog that means to.

**Loading, empty, and error states, made consistent.** §9 asked for these per
panel and each unit answered locally, so the vocabulary drifted. The rule this
codebase already enforces is the hard part and is not negotiable: **an error
must never render as emptiness.** `ControlRoom` says so explicitly — an empty
timeline must not claim analysis has not run — and `ancillaryFailed` exists to
keep that honest. Extend the same discipline uniformly: every panel has three
defined states, an error names what failed, and an empty state says what would
fill it.

**Contrast and both themes.** 97 tokens on `:root`, redefined for dark and for
the explicit toggle. Verify AA against every foreground/background pair a
component actually produces, in both, and fix by moving tokens rather than
patching components.

**Copy, once, in one voice.** The product's differentiator is that it does not
overstate — measurements not verdicts, absent data rendered absent, money
stated before it is spent. The copy accumulated across a dozen units by
different hands. Read all of it in one sitting and make it sound like one
product. This is why the unit depends on 20: writing the copy before the
rename means reading it twice.

**Terminology — precision is not comprehension.** This product speaks a dense
private vocabulary, and a census of what it renders shows how dense: *ledger*
38 times, *unclaimed* 16, *dossier* 15, *readiness* 7, *quota* 6, *continuity*
6, *envelope* 4, *transient* 1 — plus stems, drift, freeze, draft, commit,
version, attribution and spend. Three different registers are mixed. Some are
real craft terms a sound editor knows cold (*stems*, *mix*, *transient*). Some
are ours and defined nowhere (*readiness*, *dossier*, *unclaimed*). Some are
engineering that leaked through the API into the interface (*ledger*,
*attribution*, *quota*). The evidence-first rule is about not overstating; it
was never a licence to be obscure, and a term the reader cannot decode is not
more honest than a plain one — it just moves the failure.

Go through every rendered term and decide one of three things: keep it because
the audience genuinely knows it, keep it and define it, or rename it. Craft
terms stay. Ours stay only if a one-line definition earns them. Engineering
terms that name an internal mechanism rather than a thing the user has should
be renamed — a director has costs and spending, not a *ledger*.

**Help and hover tips — the mechanism is currently the wrong one.** There is no
tooltip component, and help is delivered through native `title=` attributes,
several carrying substantive multi-line explanations: *"Delete this event —
free: the money for generated content is already spent"*, *"Send — one billed
director-model call"*. Native `title` never appears on keyboard focus, does not
exist on touch, cannot be styled, is delayed, and is inconsistently announced
by screen readers. So the product's **cost geometry — the thing it is proudest
of — is being explained through the least accessible mechanism available.**

Build a real tooltip/popover: focus- and hover-triggered, dismissible,
`aria-describedby`-wired, positioned so it never covers what it explains. Then
a definition lives in one place and is reused — the glossary entry, the
tooltip, and the help overlay all read from the same source, so a term cannot
be defined two ways. Pair it with the keyboard map behind `?` as one help
surface rather than two.

**Copy stays inline; the glossary does not.** There is no strings or i18n
module today — every string lives in its component, which is the right default
for Svelte and keeps a component readable. **Do not externalise all copy as
part of this pass:** extracting several hundred strings is churn that buys
nothing for a copy read-through, and if translation is ever wanted it is its
own unit with its own tooling. What *should* be centralised is narrower and
different: the **defined terms** — one module holding each term's label and
one-line definition, consumed by the tooltips, the glossary and the help
overlay. That is shared content, not scattered copy.

**Also worth a look, no promises attached:** behaviour at narrow widths (the
control room is a fixed `100vh` layout with a resizable timeline frame);
`prefers-reduced-motion` against the spinner and transitions; and the timeline
with an unusually dense event list.

**Not in this unit:** new features, new endpoints, new panels. If polish
uncovers something missing, it becomes its own task rather than growing this
one.

**Acceptance:** every interactive control on all four routes is reachable and
operable by keyboard, and the documented map matches the bindings exactly;
every panel has a defined loading, empty and error state, with error
distinguishable from empty; every term in the glossary is defined once and
reachable from where it is used, and no rendered term is undefined; help is
available to a keyboard user without a pointer, and no `title=` attribute
carries information available nowhere else; contrast passes AA in both themes;
no console errors or unhandled rejections on any route; and CI runs an
automated accessibility check (there is none today) so the pass does not decay.

## 23. Repo and image identity — S

**Depends on:** 20, and best done after 22. **Files:** `pyproject.toml` (`[project.scripts]`),
`Dockerfile`, `scripts/deploy.sh`, `scripts/smoke_deploy.py`, `README.md`.

The operator-facing half of the rename, deliberately separated because it is
disruptive in a way the rendered surfaces are not: renaming the GitHub
repository rewrites every clone's remote, and the image name touches the
deploy and smoke scripts. **Do it when nothing else is in flight** — it
unblocks no other work, so there is no cost to waiting and a real cost to
doing it while several agents are committing.

- The GitHub repository name and the Docker image name, with `deploy.sh` and
  the post-deploy smoke check following.
- **The CLI gains `auteur` and keeps `foleyflow` as an alias.** Both entries in
  `[project.scripts]` resolve to `foleyflow.cli:main`. Every script in
  `scripts/`, every README example and every runbook says `foleyflow` today;
  breaking them to save one line is a bad trade, and an operator mid-incident
  should not learn about a rename from `command not found`.

**`mix_video_key` — decide here, and it is the only irreversible thing.**
`ProjectStore.mix_video_key` writes the muxed video to
`runs/{run_id}/foley.mp4`. Every such object already in B2 is immutable (SPEC
§10 forbids overwriting a completed generation), so changing the constant
cannot rename what exists — it only makes new artifacts disagree with old
ones. A bucket holding both is *correct* under this repo's own "cache keys are
addresses, not provenance" rule, merely untidy. The trade is a permanent seam
in the archive against a filename nobody reads, and the seam grows with every
mix, so it is cheapest now and never cheaper again. **Recommendation: leave
it.** Addresses are not names, and a demo bucket carrying a filename from an
earlier product name costs nothing a viewer will ever see.

**Acceptance:** `auteur --help` and `foleyflow --help` both work; a deploy
built from the renamed image passes `scripts/smoke_deploy.py`; and the
`mix_video_key` decision is recorded here either way.

## Out of scope

The GPU worker (`../moss-sfx-worker`) is **not part of this hackathon
submission**. It is a separate repo with its own README and `AGENTS.md`, and
Auteur depends on it only as an HTTP endpoint behind
`FOLEYFLOW_SFX_WORKER_URL`. Do not vendor it, import from it, or add torch to
this project's dependencies — keeping the deployed app free of CUDA is the whole
point of the split (SPEC §9.1).

If a task here needs live generation, start the worker per that repo's README.
Everything in this repo's test suite runs without it.
