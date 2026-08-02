# Handoff — development

Code work remaining on FoleyFlow. Written 2026-08-02.

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
  **7 (control-room UI)** last. Tasks **9–17** are **DONE**.
- CI (10), build identity (11), post-deploy smoke (12), doctor (13), live paid
  + fallback SFX (14), demo evidence (15), and Secret Manager (16) are in place.
- `uv run pytest -q` → **695 passed, 1 skipped**. No provider credentials
  required for tests: the billed calls are stubbed at two seams,
  `stems.generate_many` and `sfx_cache.generate_canonical_wav`. Everything else
  — ffmpeg, the mix, the shared cache, storage, the Genblaze pipelines — runs
  for real.
- Genblaze is the orchestration spine. `providers/genblaze.py` wraps a FoleyFlow
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

## 7. Control room UI — L — **see `handoff-task7.md`** (in progress)

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
Remaining: frontend chain from 7.9.

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

## Out of scope

The GPU worker (`../moss-sfx-worker`) is **not part of this hackathon
submission**. It is a separate repo with its own README and `AGENTS.md`, and
FoleyFlow depends on it only as an HTTP endpoint behind
`FOLEYFLOW_SFX_WORKER_URL`. Do not vendor it, import from it, or add torch to
this project's dependencies — keeping the deployed app free of CUDA is the whole
point of the split (SPEC §9.1).

If a task here needs live generation, start the worker per that repo's README.
Everything in this repo's test suite runs without it.
