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
  **10–17**, then **7 (control-room UI)** last. Task 9 (budget alerts) is
  **DONE** — advisory mutable budget; task 6 is history. **17 is a small
  follow-up to 9 found in review**; whoever holds the task-9 context should
  take it, since it is one call site and the traps are all in that code.
- **10, 11 and 12 are independent and unblock the rest** — CI, build provenance,
  and a post-deploy smoke check. Do those first when running several agents in
  parallel: with no CI, two agents can each be green locally and broken
  together, and a human finds out. 13 depends on 11. 14 and 15 are demo-risk
  work and can run alongside.
- `uv run pytest -q` → **522 passed, 1 skipped**. No provider credentials
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

## 17. Alert when a budget is set below what is already spent — S

Follow-up to task 9, found in review. Task 9 is otherwise sound: sequential
spend fires 50/80/100/120 once each, and twelve concurrent rows crossing every
threshold at once produce exactly four alerts with no duplicates.

### The gap

Alerts fire only on ledger append (`_maybe_emit_budget_alerts`, called from
`log_transaction`). `put_budget` → `store.set_budget` appends the
`budget.set` event and returns the panel; it never evaluates thresholds. So
lowering a budget past current spend announces nothing.

Reproduced against a `LocalBackend` store:

```
spent: $4.00
alerts before setting budget: 0
budget set to $3.00 against $4.00 already spent
alerts after setting budget:  0
```

Every threshold is crossed at 133%. `GET /budget` is correct — `remaining:
-1.0`, all four `crossed: true` — so a UI that polls sees the truth. The
lifecycle event the History view renders never happens, and a user who sets a
budget and then stops spending is never told.

This is the feature's headline case. The whole point is a figure the user can
set *whenever they like* and be alerted against; the moment they set it is the
moment they asked to be told.

### What to build

Evaluate thresholds after a successful `set_budget`, reusing
`_maybe_emit_budget_alerts` rather than writing a second evaluator.

### Traps

- **Use the full `project_spend`, not `_running_spend`.** The running total is
  seeded per project per process by the *ledger write* path. The API process
  handling a `PUT /budget` may never have seeded for that project — it has
  written no rows — so the cache would read zero and alert on nothing. This is
  a metadata route, not a hot loop; one accurate scan is the right cost.
- **Clearing must not alert.** `budget_usd: null` removes the figure; there is
  nothing to cross.
- **Raising must not alert.** Setting a budget comfortably above spend crosses
  nothing and must stay silent.
- **Never raise.** Task 9 holds the line that an alerting bug must not become a
  render bug; the same applies here — a failed alert must not fail the `PUT`,
  which has already durably appended the budget event.
- **Setting the same value twice.** `set_budget` appends an event every call,
  and generation is the latest event id, so an unchanged re-`PUT` mints a new
  generation and re-fires every crossed threshold. Decide deliberately: either
  make `set_budget` a no-op when the value is unchanged, or key generation on
  the value rather than the event id. Do not leave a UI that saves an unedited
  form able to spam the log.
- Keep the file-mode ledger skip consistent with task 9 — no project event log
  on that backend, so no alerts.

### Worth considering

The payload could say *why* a threshold was crossed — spending into it versus
lowering the budget beneath it. Same event, materially different sentence for a
UI to render, and the information is free at the call site.

### Acceptance

- Spending $4.00 then setting a $3.00 budget emits alerts for every crossed
  threshold, once each.
- Setting $3.00 again immediately does not double-announce (subject to the
  same-value decision above).
- Clearing the budget emits nothing.
- Raising the budget above spend emits nothing.
- Lowering it again below spend alerts again — that is a genuinely new
  crossing, not a duplicate.
- An alert failure does not fail the `PUT`.
- `522 passed` does not regress.

### While in there

`handoff.md` task 6 still reads "its cost depends on cache hits and is
unknowable at the door". Task 9c disproved it — the pre-flight probe in
`project_mix_cost` prices exactly that. Orientation already says to read 6 as
history, so this is cosmetic, but strike the sentence rather than leave a wrong
claim in the file agents are told to trust.

## Out of scope

The GPU worker (`../moss-sfx-worker`) is **not part of this hackathon
submission**. It is a separate repo with its own README and `AGENTS.md`, and
FoleyFlow depends on it only as an HTTP endpoint behind
`FOLEYFLOW_SFX_WORKER_URL`. Do not vendor it, import from it, or add torch to
this project's dependencies — keeping the deployed app free of CUDA is the whole
point of the split (SPEC §9.1).

If a task here needs live generation, start the worker per that repo's README.
Everything in this repo's test suite runs without it.
