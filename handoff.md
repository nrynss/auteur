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
  **6 (per-project budgets) → 7 (control-room UI)**.
- `uv run pytest -q` → **483 passed, 1 skipped**. No provider credentials
  required: the billed calls are stubbed at two seams, `stems.generate_many`
  and `sfx_cache.generate_canonical_wav`. Everything else — ffmpeg, the mix, the
  shared cache, storage, the Genblaze pipelines — runs for real.
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

## 6. Per-project budgets — M

Budgets are the product's spine: a project is approved with a dollar cap, and
the ledger work (task 5) is what makes spend queryable. But a ledger row has
no project attribution, so "what has this project spent" is unanswerable —
run metadata covers only mix runs, and analysis spend is invisible per
project. The control-room UI (task 7) should render a budget, so this lands
before it.

API-first, like every feature here: `docs/api.md` changes first (the budget
endpoint, the create-body field), and the CLI is a client of that contract.

### The gate policy (nailed before coding)

Refuse at the route, against the estimate, when a billable call would take the
project past its budget: `remaining < estimate` → `422 precondition_failed`
with the numbers (configured, spent, estimated, remaining). Free calls
(estimate $0.00 — the self-hosted tier) skip the gate entirely.

There is no reservation: two concurrent jobs can both pass the check and
overspend. That window is accepted and documented — the same posture B2's
conditional-write weakening already takes for a single-instance deployment.

Spend is estimated from the ledger and labelled `estimate: true` like
`/quota`; a real provider bill can drift from it, and the payload says so.
Overage is therefore possible but visible: the budget payload always carries
`ledger_writable`, and `false` means the figures are fiction.

### What ships

1. **Attribution.** `log_transaction` gains an optional `project_id`;
   `execute_with_ledger` reads it from a contextvar the job runner sets per
   run, so the deep provider seam keeps its signature. Every run kind
   inherits it automatically — analysis, mix, and the repair route all tag
   their rows without touching their call sites. Old rows stay valid (the
   field is optional). A direct `foleyflow render` has no project: its rows
   stay unattributed and the CLI footer still sums globally.
2. **Definition.** `budget_usd` on the project, set at creation (optional in
   the create body) or from the `providers.toml [budgets]` default. Fixed at
   creation for v1 — the project record is frozen, so any later change would
   be an appended event (`postproduction.budget.set`) folded into state,
   noted but not built. Models priced at $0.00 simply do not consume the
   budget.
3. **Read.** `GET /projects/{id}/budget` — configured, spent (sum of
   attributed rows), estimated, remaining, per-model breakdown,
   `ledger_writable`, `estimate: true`. The dossier gains a compact budget
   block that reuses the same read; the UI (task 7) renders it.
4. **CLI project context.** The CLI gets a first-class notion of *the current
   project*: `FOLEYFLOW_PROJECT` env, a `--project` flag, and `foleyflow use
   <id>` persisting a default, so project verbs (`show`, `mix`, `budget`,
   `regenerate`, …) can omit the id and act on it. `foleyflow budget` reads
   the same endpoint the UI will — nothing is computed in the CLI.

### Acceptance

- Analysis, mix and repair rows for project A never appear in project B's
  spent (two projects that each spend $0.30 report their own numbers).
- With `budget_usd = 0.50` and a $0.30 estimate, a second $0.30 call is
  refused with the numbers; a $0.00 estimate is never refused.
- `ledger_writable: false` → the budget payload still returns, figures marked
  fiction (same as `/quota`).
- `foleyflow use <id>` then `foleyflow budget` (no id) reports the current
  project's budget.

## 7. Control room UI — L

SPEC §6.1. Only the throwaway static dashboard (`api/static/index.html`) exists.

The API already serves everything it needs, including per-stage status: runs
carry `stages`, `current_stage` and `genblaze_run_id`, and the event log is the
history view. The two pieces with no UI at all are the silent-versus-mixed A/B
toggle and the readiness checklist.

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

## Out of scope

The GPU worker (`../moss-sfx-worker`) is **not part of this hackathon
submission**. It is a separate repo with its own README and `AGENTS.md`, and
FoleyFlow depends on it only as an HTTP endpoint behind
`FOLEYFLOW_SFX_WORKER_URL`. Do not vendor it, import from it, or add torch to
this project's dependencies — keeping the deployed app free of CUDA is the whole
point of the split (SPEC §9.1).

If a task here needs live generation, start the worker per that repo's README.
Everything in this repo's test suite runs without it.
