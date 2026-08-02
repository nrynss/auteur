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
  **9 (gate mix on the budget)**, then **7 (control-room UI)**.
- `uv run pytest -q` → **516 passed, 1 skipped**. No provider credentials
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

## 6. Per-project budgets — M — **DONE**

A project is approved with a dollar cap and every billed row is attributed
back to it. `log_transaction` rows carry `project_id` from a contextvar the
job runner sets per run — analysis, mix and repair all tag their rows with no
call-site change; a direct `foleyflow render` stays unattributed (global)
unless `--project` opts it in. `budget_usd` is set at creation (create body
or `providers.toml [budgets]` default) and fixed on the frozen project
record. `GET /projects/{id}/budget` reports configured, spent (failed calls
counted), remaining, per-model breakdown, `estimate: true` and
`ledger_writable`; the dossier carries a compact block. Analyze and
regenerate are gated at the route (422 with the numbers when `remaining <
estimate`); free calls and cap-less projects are never refused; no reservation
(documented). Mix is deliberately NOT gated — its cost depends on cache hits
and is unknowable at the door, so a paid-tier mix can outspend the cap (the
overage is reported, not prevented; the UI must not imply the cap covers
every dollar).

The CLI has a first-class current project — positional id > `--project` >
`$FOLEYFLOW_PROJECT` > `foleyflow use`'s persisted default — and `foleyflow
budget` reads the same endpoint the UI will. Project-less rows are never
stranded: `POST /projects/{id}/attributions` (and `foleyflow attribute`)
writes an immutable rescue override claiming unattributed rows written before
a time, first-come by `before` so a row can never land in two projects.

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

## 9. Gate `POST /mixes` on the project budget — M

### Why

The budget added in task 6 gates the two paths that cannot realistically breach
a cap, and leaves open the one that can. `_budget_gate` is called from exactly
two places — `create_mix` is not one of them, and there is no budget check
anywhere in the generation path (`render.py`, `stems.py`, `sfx.py`,
`sfx_cache.py`).

Against the shipped `[budgets] default = 5.0`:

| Path | Unit cost | Calls to breach $5 | Gated? |
|---|---|---|---|
| analyze | $0.005 | 1,000 — but Gemini's free tier is 20/day, so quota binds ~50 days first | yes |
| regenerate | $0.30/stem | ~16 | yes |
| **mix** | $0.30 × stems; 8 stems on the demo clip = **$2.40** | **2** | **no** |

So on the paid tier two mixes exceed the cap and nothing refuses them. On the
default `test` tier every stem is $0.00 and the whole system is inert, which is
why this has not been noticed in use.

### The current justification is wrong, and must be corrected too

`handoff.md` task 6 and the `create_mix` docstring both say a mix cost is
"unknowable at the door" because it depends on cache hits. It is knowable:
`assemble.plan_requests(timeline, duration, tier)` returns the deduped request
list, and probing `sfx_cache.cache_object_key(...)` against the backend for each
gives hits versus misses. `cmd_prewarm` in `cli.py` already does exactly this —
`foleyflow prewarm --dry-run` prints `8 stems planned: 8 cached, 0 to generate`.
Reuse that logic; do not write a second copy of it.

Even without probing, `len(planned) × cost_per_call` is a sound upper bound, and
a cap should gate the worst case anyway.

### What to build

A helper beside `_estimate` in `api/app.py` that, given a project, timeline
version and ladder tier, returns the planned requests, the subset that are cache
misses, and the summed estimate for those misses. Price each miss with
`ledger.estimate_cost(request.endpoint, …)` — pass the request's duration in the
arguments, because a per-second model prices on it and `{}` would silently
under-report.

Call `_budget_gate(store, project_id, estimate)` in `create_mix` before
`runner.submit`. Do not reimplement the gate: it already handles the cap-less
project, the free call, and the fail-closed-on-unwritable-ledger case, and those
semantics must stay identical across all three routes.

### Constraints

- **Only misses count.** A cache hit costs nothing; charging for it would make a
  re-mix of an unchanged timeline refusable, which contradicts the existing
  `create_mix` docstring and the "a re-mix generates nothing" guarantee.
- **Do not touch cache keys.** The probe reads `cache_object_key(endpoint,
  prompt, duration, "")` — variant is empty for a normal mix; variants belong to
  repair. Changing the key shape orphans the warm stems in B2.
- **Free tiers stay ungated.** `test` resolves to $0.00 per stem, the summed
  estimate is 0, and `_budget_gate` already returns early on `estimate <= 0`.
  There must be no behaviour change on the default configuration.
- **The probe is N existence checks against B2.** Bounded by stem count (single
  digits here), but it is network I/O on a route that currently does none —
  keep it to one pass and do not re-probe inside the job.
- **The race is pre-existing and stays.** Between probe and submit, stems can be
  generated by another run, and there is no reservation. Task 6 documents this
  for analyze/regenerate; say the same here rather than inventing a lock.
- **The estimate is an estimate.** A mix that outspends its gate-time estimate
  must still be reported honestly by `GET /budget`; the cap reduces overage, it
  does not eliminate it.

### Acceptance

- A capped project whose next mix would exceed the cap gets `422
  precondition_failed` with `budget_usd`, `remaining` and `estimated_cost_usd`
  in `detail` — the same shape analyze and regenerate already return.
- The same project on the `test` tier is never refused.
- A fully cached mix is never refused, whatever the tier or the remaining
  budget.
- A cap-less project is never refused.
- An unwritable ledger refuses a billable capped mix, consistent with the other
  two routes.
- `516 passed` does not regress.

### Also update

Three places state the opposite today. Grepped — this is the complete list:

- `docs/api.md:505` — "Mix is not gated: its cost depends on cache hits and is
  …". The contract is source of truth, so this one matters most.
- The `create_mix` docstring in `api/app.py`, which argues a gate here "would be
  noise".
- Task 6's paragraph above ("Mix is deliberately NOT gated").

SPEC.md does not repeat the claim, so it needs no change on this point.

## Out of scope

The GPU worker (`../moss-sfx-worker`) is **not part of this hackathon
submission**. It is a separate repo with its own README and `AGENTS.md`, and
FoleyFlow depends on it only as an HTTP endpoint behind
`FOLEYFLOW_SFX_WORKER_URL`. Do not vendor it, import from it, or add torch to
this project's dependencies — keeping the deployed app free of CUDA is the whole
point of the split (SPEC §9.1).

If a task here needs live generation, start the worker per that repo's README.
Everything in this repo's test suite runs without it.
