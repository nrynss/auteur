# Handoff

Remaining work for the Backblaze Generative AI Media Hackathon submission
(due 3 August 2026). Written 2026-08-02.

Sizes are **XS** (minutes, one obvious edit) · **S** (contained, one file or one
route) · **M** (spans modules, needs tests) · **L** (a build in its own right).

---

## State of the world

Read this before picking up a task; several tasks below only make sense against
it.

- **Deployed and working:** <https://foleyflow-142385844755.us-east4.run.app>
  (Cloud Run, `us-east4`, project `backblaze-504305`, revision `foleyflow-00002-z42`).
- **Verified end to end on 2026-08-02:** the deployed app mixed and exported
  `proj_01KYJD1RKETS3KTX5HA3H5JPDQ`, generating 5 stems live by reaching through
  the Cloudflare tunnel at `sfx.nryn.dev` to the GPU worker on the dev machine.
  Mix measured −21.4 LUFS / −1.0 dBTP. Manifest written. Project status `ready`.
- **The GPU worker is not part of this repo.** It lives at
  `../moss-sfx-worker`, needs CUDA and a 21 GB model, and must be running —
  along with `cloudflared` — for any render that is not fully cached.
- **Test suite: 429 passed, 1 skipped.** No provider credentials needed.
- **Nothing is billed today.** The active SFX tier is `test` (self-hosted MOSS,
  $0). The `paid` and `fallback` tiers are wired and unit-tested but have never
  made a live call, because no credits have been bought.

### Traps that have already cost time

- `providers.toml` is resolved from the *package source path*, not the working
  directory. It only works in the container because `uv sync` installs the
  project editable.
- Cloud Run's filesystem is read-only outside `/tmp`. An unwritable ledger makes
  `GET /quota` report a full budget while the real one drains — check
  `ledger_writable` in that response after every deploy.
- `cloudflared` ingress is first-match and it does not reload config on its own.
  A rule below the `http_status:404` catch-all is dead, and an edit without
  `systemctl restart cloudflared` does nothing.
- Switching SFX tier changes cache keys, by design. The ~170 warm stems in B2
  belong to the `test` tier only.

---

## Blocking the submission

### 1. Make the GitHub repo public — XS
§16 requires a public repository. The repo is currently **private**. One toggle,
but confirm `.env` is absent from history first (it is gitignored and has never
been committed — verify, don't assume).

### 2. Record the 3-minute demo video — L
§15 has the shot list. It depends on tasks 4 and 5 for its last two beats; decide
early whether to cut those beats or build them, because that choice changes the
script. Must show the deployed app, not localhost.

### 3. Write the limitations and roadmap section — S
Last unchecked content item in §16. Must be explicit that visual continuity is a
mean-luma comparison between adjacent shots, that it produces a *review warning*
and never an automatic correction, and that quota figures are estimates. Most of
the honest wording already exists in `quota.py`'s module docstring and SPEC §13
— this is collection, not invention.

---

## Unmet success criterion

### 4. `POST /projects/{id}/events/{index}/regenerate` — M
The only unmet criterion in §18 (#5), and demo beat 5. The engine exists and is
tested: `render_mix(variant_fn=…)` already regenerates exactly one stem and
re-mixes, with every other stem still served from cache. What is missing is the
route that selects the event, and the version lineage recording *why* it was
regenerated. Documented in `docs/api.md:253` as though it exists.

### 5. `GET /projects/{id}/dossier` — M
Demo beat 6, the closing shot. One read view assembling source, timeline
versions, stems, mix, QA report, workflow log and the provenance manifest.
Documented in `docs/api.md:333` as though it exists. All the underlying data is
already in B2 and reachable through `ProjectStore`; this is composition, not new
storage.

---

## Demo reliability

### 6. Decide the demo tier, then pre-warm it — M
Currently `test` (MOSS, free, warm). If the demo runs on `paid`, **the cache is
cold** — switching tier changes every cache key, so the first demo render would
generate every stem live against a paid API. Either stay on `test`, or switch
early and re-warm.

### 7. Buy ElevenLabs credits and set `ELEVENLABS_API_KEY` — S
Only if task 6 chooses `paid`. Needs the key in `.env` *and* in the Cloud Run
env vars. The `paid` tier has never made a live call — budget time to discover
whatever is wrong with it the first time.

### 8. `FAL_KEY` is absent — XS
The `fallback` tier cannot work without it. Harmless while the active tier is
`test` (which deliberately does not escalate), but if task 6 chooses `paid`, its
fallback is a dead branch until this is set.

### 9. Warm `duel_kling` and `inhaler_kling`, or don't demo them — S
`standoff_kling` is fully warm. The other two demo assets in B2 have **0 of 4**
and **0 of 9** stems cached respectively.

### 10. Pre-demo checklist — S
Write it down and run it: GPU worker up, `cloudflared` up, `sfx.nryn.dev/health`
returns 200 with `auth_required: true`, `GET /quota` shows `ledger_writable:
true` and remaining daily analyses, demo project status `ready`.

---

## Known gaps, non-blocking

### 11. Analysis does not run through Genblaze — M
The one remaining §8.2 gap. `analyze.py` calls Google directly via
`execute_with_ledger`. `genblaze-google` exists on PyPI and this is the same
lift the fal and local adapters already got (`providers/genblaze.py`). SPEC §8.2
names it explicitly rather than glossing it, so the docs are honest — this is
about completeness, not correctness.

### 12. Control room UI — L
§6.1. Only the throwaway static dashboard exists. Demo beats 3 and 4 (the
silent-vs-mixed A/B toggle, the readiness checklist) currently have nothing to
film. The API already serves everything it would need, including per-stage
status.

### 13. Secrets are plain Cloud Run env vars — S
Including the B2 application key, which grants bucket write. Anyone with project
access can read them via `gcloud run services describe`. Move to Secret Manager.

### 14. The cost ledger is per-instance and ephemeral — S
`FOLEYFLOW_LEDGER_PATH` points into `/tmp`, so quota counting resets whenever
the instance restarts. Correct fix is to keep the ledger in B2 like every other
canonical artifact.

### 15. Delete the Cloud Run service after judging — XS
`--min-instances 1` with CPU always allocated bills continuously. Both flags are
required (see README), so the cost is structural, not a misconfiguration.

---

## Cross-repo

### 16. Commit and push the worker repo — XS
`../moss-sfx-worker` has uncommitted changes: bearer auth, the CPU-build fix for
the startup OOM, and a README rewritten for Linux. Its `.env` is gitignored.

### 17. The worker's Windows-era numbers are stale — XS
Already corrected in that repo's README, but if anything else quotes "~16 s per
stem" or "9.88 GiB resident", those were measured on Windows with the pipeline
resident on the GPU. On Linux it is built on CPU and pages per stage: ~20 s cold,
~15 s warm.
