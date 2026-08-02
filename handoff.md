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
  **9 (budget alerts)**, then **7 (control-room UI)**. Task 9 revises task 6:
  the budget becomes advisory and mutable rather than a cap frozen at creation,
  so read 6 as history and 9 as the intent.
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

## 9. Budget alerts — M

Task 6 built a **hard cap**. The intent is a **soft, advisory budget**: the user
sets a figure whenever they like, and gets told when spend approaches or passes
it. Nothing is refused. This task converts what exists and fills the gaps.

### Where accurate cost actually lives

Three moments, and each knows something different. The design turns on this.

| Moment | Accuracy | Use |
|---|---|---|
| Pre-flight (`plan_requests` + cache probe) | A projection. Wrong when a stem fails, or when a fallback escalation changes the price mid-flight — paid ElevenLabs $0.30 becomes fal $0.05 — so the door cannot know what will serve | The only warning that arrives in time to cancel |
| **Per call — `ledger.execute_with_ledger`** | **Real.** Takes `result.actual_cost_usd`, else extracts from the raw response, else the estimate, and records which via `cost_source` | The running total that alerts fire from |
| Post-run | Exact | Too late to act on |

`execute_with_ledger` is the single choke point every billed call funnels
through, and `log_transaction` already stamps `project_id` from a contextvar. So
alerting hooks the ledger append: it fires within one provider call of a
threshold being crossed — latency of one stem, not one run. Nothing else in the
system has attribution, actual cost and timeliness at once.

### 9a. Make the budget mutable — S

Today `budget_usd` is resolved at creation and frozen on the immutable project
record (`api/app.py`, `create_project`). "Set it whenever" means it becomes an
**event** in the append-only log, latest-wins, with state derived — the
discipline the rest of the system already follows. It also preserves the history
of someone raising the threshold right after an alert fired, which is exactly
the audit trail this product is about.

- New lifecycle event carrying the new figure and the actor. `None` clears it.
- `PUT /projects/{id}/budget` (or `POST`; match the surrounding style), plus a
  `foleyflow budget --set` on the existing verb.
- `[budgets] default` still seeds a project at creation; it is now the first
  event rather than a frozen field.
- Derived state reads the latest event. Do not add a mutable pointer object —
  §10 allows exactly one and `exports/latest.json` is it.

### 9b. Fire alerts from the ledger append — M

- Thresholds as fractions of the budget — **50 / 80 / 100 / 120%**. A single
  alert at 100% of a soft budget reports something the user can no longer act
  on. Make the set configurable in `providers.toml [budgets]`.
- After a row is appended, compare the project's running spend against each
  threshold and emit a lifecycle event for any newly crossed.
- **Idempotency is the hard part.** Stems fan out concurrently, so several rows
  can cross one threshold simultaneously. Alert once per `(project, threshold)`,
  written with `put_if_absent` — the primitive already exists and already backs
  run records. Two workers must not both announce 80%.
- **Carry `cost_source` into the alert.** "$4.90 spent, $3.10 of it estimated"
  is a materially different claim from "$4.90 spent, all actual", and this
  codebase is careful about that distinction everywhere else.
- **Failed calls count.** `log_transaction` records failures with the estimate
  and task 6 already counts them as spend — a paid call that errored still
  charged. Do not quietly exclude them.
- Never raise. `log_transaction` is documented as never failing a generation
  because the cost record failed; an alerting bug must not become a render bug.

### 9c. Pre-flight projection on mix — S

Keep the helper described in the previous version of this task — `plan_requests`
plus a `cache_object_key` probe for hits versus misses, priced with
`estimate_cost` — but report it instead of enforcing it. `foleyflow prewarm
--dry-run` already computes exactly this (`8 stems planned: 8 cached, 0 to
generate`); reuse it rather than writing a second copy.

Surface it on the mix response and in the CLI: *"projected $2.40, taking this
project to $6.10 against a $5.00 budget."* Label it a projection. Only misses
cost anything — a fully cached re-mix projects $0.00 and must say so.

Pass each request's duration into `estimate_cost`; a per-second model prices on
it and `{}` silently under-reports.

### 9d. Decide what happens to the existing refusals — XS, but it is a decision

`_budget_gate` currently raises `422 precondition_failed` on analyze and
regenerate. Under an advisory budget those refusals contradict the intent.

**Recommendation:** make the budget purely advisory — drop the refusals, keep
`confirm_cost_usd` on regenerate, which is a better mechanism anyway because it
is explicit per-action consent rather than a cap. Then an unwritable ledger
means "alerts are unreliable", surfaced loudly, rather than "work is refused".

If instead some projects should still be capped, that is a separate opt-in flag
and should be named as one — not the same field doing two jobs.

**This is the user's call. Ask before changing shipped behaviour.**

### Surfacing

The event log already exists and the UI's History view already renders it. Put
alerts there, and expose current state on `GET /projects/{id}/budget` (spent,
budget, remaining, thresholds crossed, `estimate`, `ledger_writable`) and on run
status. Anything push — email, webhook — needs an outbound integration this
project does not have; leave it out.

### Acceptance

- A budget can be set, changed and cleared at any point in a project's life, and
  the log shows every change with its actor.
- Crossing 80% during a fan-out emits exactly one alert, not one per concurrent
  stem.
- An alert states how much of the figure is estimated rather than actual.
- A fully cached mix projects $0.00 and emits nothing.
- Nothing is refused on budget grounds (subject to 9d).
- An unwritable ledger is reported as "alerts unreliable" and does not block work.
- `516 passed` does not regress.

### Docs that currently say otherwise

Grepped — complete list:

- `docs/api.md:505` — "Mix is not gated: its cost depends on cache hits and is
  …". The contract is source of truth, so this matters most. Its reasoning is
  also wrong: the cost *is* knowable at the door, as 9c shows.
- The `create_mix` docstring in `api/app.py`, which argues a gate "would be
  noise".
- Task 6 above, which describes a frozen `budget_usd` and route-level gating.
- SPEC.md does not repeat the claim and needs no change on this point.
## Out of scope

The GPU worker (`../moss-sfx-worker`) is **not part of this hackathon
submission**. It is a separate repo with its own README and `AGENTS.md`, and
FoleyFlow depends on it only as an HTTP endpoint behind
`FOLEYFLOW_SFX_WORKER_URL`. Do not vendor it, import from it, or add torch to
this project's dependencies — keeping the deployed app free of CUDA is the whole
point of the split (SPEC §9.1).

If a task here needs live generation, start the worker per that repo's README.
Everything in this repo's test suite runs without it.
