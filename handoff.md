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
  **9 (budget alerts, in flight)**, **10–16**, then **7 (control-room UI)** last.
  Task 9 revises task 6: the budget becomes advisory and mutable rather than a
  cap frozen at creation, so read 6 as history and 9 as the intent.
- **10, 11 and 12 are independent and unblock the rest** — CI, build provenance,
  and a post-deploy smoke check. Do those first when running several agents in
  parallel: with no CI, two agents can each be green locally and broken
  together, and a human finds out. 13 depends on 11. 14 and 15 are demo-risk
  work and can run alongside.
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
## Addendum to 9b — relay to whoever holds task 9

Not folded into the task body because 9 is in flight. **`project_spend` is a
full ledger scan**, and hooking alerts on the ledger append makes that scan run
per billed call:

- `project_spend` (`ledger.py:463`) iterates *every* transaction row and filters
  by `project_id`. There is no per-project index.
- In storage mode each scan is a `list_keys` plus one GET per object against B2.
- `_read_storage_rows` has a process-local TTL cache, but its docstring says
  **successful writes invalidate it** — and a successful write is exactly what
  would trigger an alert check. So the cache cannot absorb this pattern.

An N-stem fan-out therefore becomes N full ledger scans over the network, each
O(total rows), growing for the life of the bucket. Options, cheapest first: keep
a running per-project total in the alert path rather than re-summing; or let the
alert check reuse one snapshot for a whole run; or add a per-project ledger
prefix so a scan is scoped. Do not leave it re-summing the world per stem.

## 10. Continuous integration — S

516 tests and nothing runs them automatically. With several agents landing work
in parallel this is the largest process risk in the repo: the failure mode is
two agents each green locally and broken together, discovered by a human.

- GitHub Actions on push and PR: `uv sync` then `uv run pytest -q`.
- **Install ffmpeg in the runner.** The suite skips rather than fails when it is
  absent (`HAS_FFMPEG` / `needs_ffmpeg` in `tests/test_render.py`), so without it
  CI goes green while the test that proves the milestone exit condition never
  runs. A silently-skipping suite is worse than no CI.
- Provide **no** provider credentials and no B2 config. The suite is offline by
  design; a test that starts needing the network should fail loudly in CI, which
  is the point.
- Add a docs check: the test count quoted in `handoff.md` and `SPEC.md` must
  match the suite. That drift has been corrected twice by hand already, and it
  is the number a reader uses to judge whether the docs are current.

Note `.dockerignore` excludes `tests/`, so the built image cannot self-verify —
run tests from the checkout, not the image, unless that changes.

**Acceptance:** a PR with a deliberately broken test fails CI; a run with ffmpeg
removed fails rather than skips.

## 11. Report the deployed build in `/health` — S

`VERSION` is hardcoded `"0.1.0"` (`api/app.py:66`). There is no way to ask a
running instance which code it has. Establishing that production was 15 commits
behind took probing individual endpoints for 404s — that should have been one
curl.

- Bake the git SHA and build timestamp into the image at build time (a
  `--build-arg`, or a generated file the app reads; do not shell out to `git` at
  runtime, the container has no repo).
- Add them to `HealthResponse` beside `version`.
- Absent build info must degrade gracefully — a local `uv run uvicorn` has no
  build stamp and must not crash or lie.

**Acceptance:** `curl /api/v1/health` on the deployed app names the commit it is
running, and it matches what was pushed.

## 12. Post-deploy smoke check — S

The same checks have been run by hand after every deploy: health, storage,
worker probe, a dossier 200, `ledger_writable`. Script it and make it the last
step of deploying.

It must have caught, and must in future catch, the two failures already seen:
production silently stale (task 11 gives it the signal), and
`FOLEYFLOW_LEDGER_PATH` set in the deploy env forcing the ledger back into
ephemeral local-file mode.

- Takes a base URL, exits non-zero on any failure, prints one line per check.
- Assert `ledger_writable: true`, `storage: ok`, worker `status` present, one
  read endpoint returning 200 with real content, and the reported build matching
  the commit just deployed.

**Acceptance:** running it against a deliberately misconfigured revision fails
with the specific reason.

## 13. `foleyflow doctor` — M

One command answering "can I demo right now?". Everything it needs already
exists; this is composition, not new capability.

- Worker reachable and authenticated — reuse `_worker_health()`.
- Active SFX tier, and warm-cache coverage for a given timeline — reuse the
  `plan_requests` + `cache_object_key` probe that `foleyflow prewarm --dry-run`
  already runs.
- Quota remaining and `ledger_writable`.
- Deployed build matches local HEAD (needs task 11).
- Exit non-zero when anything would break a live render; print a checklist.

The value is that a cold cache, an unplugged GPU, a stale deploy and an
exhausted quota all present identically today — as a render that fails partway.

**Acceptance:** with the worker stopped, `doctor` says so and exits non-zero
while `health` still reports the API healthy.

## 14. Exercise the paid tier and the fallback for real — M

Both are implemented and unit-tested against stubs, and **neither has ever made
a live call**. The first time the paid path runs should not be during a
recording.

Two halves, and the second needs no money:

- **Paid:** needs ElevenLabs credits. One real generation, end to end, checking
  the response envelope matches what `providers/genblaze.py` expects, the cost
  lands in the ledger with `cost_source: "actual"`, and the stem is cached under
  the paid tier's own key.
- **Fallback escalation, free to test now:** point the paid tier at an
  unreachable endpoint and confirm the chain escalates, the stem is served, and
  `served_endpoint` reaches the stem inventory and the §11.6 manifest. That
  provenance path was fixed this session and has only ever run under stubs — if
  it is wrong, a manifest credits ElevenLabs for audio fal produced.

**Acceptance:** a manifest from a fallback-served run names the endpoint that
actually generated the audio.

## 15. More evidence in the bucket — S

One real project is thin for a judge, and it is also a single point of failure
for the demo.

- Take a second clip end to end on the deployed app: create, analyze, mix,
  export. `demo-assets/` already holds `duel_kling` and `inhaler_kling` with
  validated timelines.
- Warm `inhaler_kling` — still cold at 0 of 9 stems, and free on the `test`
  tier: `foleyflow prewarm samples/timelines/inhaler_kling.json`.
  (`duel_kling` was warmed this session; verify with `--dry-run`.)
- Analysis spends real quota (20/day), so prefer `--from-timeline` where the
  validated timeline already exists.

**Acceptance:** two projects in B2 with a mix, an export and a manifest each.

## 16. Move deployment secrets into Secret Manager — S

The B2 application key, which grants bucket write, is a plain Cloud Run
environment variable and is readable by anyone with project access via `gcloud
run services describe`. Same for the Gemini key and the worker token.

Move them to Secret Manager and reference them with `--set-secrets`. Update the
README's deploy section, which currently documents the `--env-vars-file` form.

**Acceptance:** `gcloud run services describe` shows secret references, not
values.

## Out of scope

The GPU worker (`../moss-sfx-worker`) is **not part of this hackathon
submission**. It is a separate repo with its own README and `AGENTS.md`, and
FoleyFlow depends on it only as an HTTP endpoint behind
`FOLEYFLOW_SFX_WORKER_URL`. Do not vendor it, import from it, or add torch to
this project's dependencies — keeping the deployed app free of CUDA is the whole
point of the split (SPEC §9.1).

If a task here needs live generation, start the worker per that repo's README.
Everything in this repo's test suite runs without it.
