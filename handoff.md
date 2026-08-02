# Handoff — development

Code work remaining on FoleyFlow. Written 2026-08-02 against commit `12343fe`.

Sizes: **XS** one edit · **S** one file or one route · **M** spans modules,
needs tests · **L** a build in its own right.

Everything here is a code change. Operational work — credentials, recording,
repo visibility, deleting cloud resources — is deliberately out of scope.

---

## Orientation

- One package, `src/foleyflow`. One CLI, `foleyflow`. The HTTP contract in
  `docs/api.md` is written ahead of the code and treated as source of truth; two
  endpoints in it are marked **NOT IMPLEMENTED** and are tasks 2 and 3 below.
- `uv run pytest -q` → **440 passed, 1 skipped**. No provider credentials
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

## 2. `POST /projects/{id}/events/{index}/regenerate` — M

The last unmet success criterion (SPEC §18 #5) and documented at
`docs/api.md:275`.

The engine already exists and is tested: `render_mix(variant_fn=…)` regenerates
exactly one stem while every other stem is still served from cache. What is
missing is the route that maps an event index to its `SfxRequest`, and the
version lineage that records *why* a stem was replaced.

Watch the addressing: a variant changes the cache key by appending a salt
segment, and `sfx_cache.cache_key` documents that the segment is appended *only*
when a variant is requested, so existing objects keep resolving. Do not
regularise that.

Acceptance: one stem's bytes change, every other stem's `b2_key` is identical
across the two runs, and the lineage names the superseded version.

## 3. `GET /projects/{id}/dossier` — M

Documented at `docs/api.md:357`. Composition, not new storage — source, scenes,
timeline versions, stem inventory, mixes, QA report, event log and the §11.6
manifest are all already in B2 and reachable through `ProjectStore`.

Return signed URLs, never raw storage keys — `docs/api.md` §1 is explicit that a
client never sees a key it could use. Reuse the existing `_sign` helper in
`api/app.py` and its expiry policy.

## 4. Route analysis through Genblaze — M

The one remaining §8.2 gap, and the only provider call still outside the spine:
`analyze.py` calls Google directly via `execute_with_ledger`.

`genblaze-google` exists on PyPI. This is the same lift `providers/genblaze.py`
already performed for the local and fal adapters, so follow that shape rather
than inventing a second one. Two things must survive unchanged: the tuned
`TIMELINE_PROMPT` with its validated event timings, and the timeline cache key —
re-analysing a clip that is already cached must stay free, because the free tier
is 20 calls/day and that budget is the binding constraint on all development.

## 5. Move the cost ledger into B2 — M

`FOLEYFLOW_LEDGER_PATH` currently points into `/tmp` on Cloud Run because the
container filesystem is read-only elsewhere. `/tmp` is per-instance, so quota
counting silently resets on restart and the ledger is not durable — which makes
it the only cost record in the system that is not an artifact.

Every other canonical artifact lives in B2. The wrinkle is that the ledger is
append-only and concurrent: `log_transaction` currently swallows write failures
by design, and that must not become a silent no-op against object storage.
`storage/local.py`'s `put_if_absent` write-then-link pattern is the precedent for
getting atomicity right.

Keep `ledger_writable()` honest — its whole purpose is to expose the case where
quota figures are fiction.

## 6. Control room UI — L

SPEC §6.1. Only the throwaway static dashboard (`api/static/index.html`) exists.

The API already serves everything it needs, including per-stage status: runs
carry `stages`, `current_stage` and `genblaze_run_id`, and the event log is the
history view. The two pieces with no UI at all are the silent-versus-mixed A/B
toggle and the readiness checklist.

Evidence-first, per SPEC §8.4: show the measured number and its source, never a
synthesised verdict. QA warnings are review prompts, not automatic corrections —
the visual continuity check in particular is an adjacent-shot mean-luma
comparison and must never be presented as a detected fault.

## 7. Expose stem pre-warming as a CLI verb — S — **DONE**

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
