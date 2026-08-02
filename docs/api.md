# FoleyFlow HTTP API v1

The contract. Written before the implementation and treated as the source of
truth: the Python packages under `src/foleyflow/` are an implementation detail
behind this surface, not the other way round. If a capability is not reachable
here, the product does not have it.

Base path: `/api/v1`

Deployed at <https://foleyflow-142385844755.us-east4.run.app>.

> **Not everything here is built.** This document is the contract and is
> deliberately written ahead of the code. Everything in this file is live and
> covered by tests.

---

## 1. Principles

**Every mutation is asynchronous.** SPEC.md §14 is explicit: a mutation returns
a run id so the UI can show stage-level status "rather than pretending work
completed synchronously." Analysis takes ~25s and generation longer; no request
blocks on them. Mutations return `202 Accepted` with a `run_id`, and the client
polls the run.

**The client never sees a storage key it can use.** Artifacts are returned as
short-lived signed URLs (SPEC.md §8.3). Raw B2 keys appear in responses for
provenance display, but they are not addressable without a signature.

**Video bytes never pass through the API.** `POST /projects` returns a presigned
PUT URL; the client uploads straight to B2. This is SPEC.md §8.3's "direct video
upload destination through presigned URLs", and it keeps a 26 MB upload off the
application server.

**Immutability is visible in the contract.** There is no `PUT /projects/{id}`
and no `DELETE`. Timelines are versioned and additive; the only mutable pointer
in the system is the current export, and it is moved by creating a new one.

**Errors have one shape,** always, including validation failures.

---

## 2. Error shape

Every non-2xx response:

```json
{
  "error": {
    "code": "project_not_found",
    "message": "No project with id proj_01KYJD1RKETS3KTX5HA3H5JPDQ",
    "detail": {"project_id": "proj_01KYJD1RKETS3KTX5HA3H5JPDQ"}
  }
}
```

`code` is a stable machine-readable string; `message` is for humans; `detail` is
optional structured context. Clients branch on `code`, never on `message`.

| HTTP | `code` | When |
|---|---|---|
| 400 | `invalid_request` | Malformed body or parameter |
| 400 | `invalid_id` | An id that is not a well-formed ULID of the right kind |
| 404 | `project_not_found` / `run_not_found` / `timeline_not_found` / `qa_not_found` | Unknown id |
| 409 | `already_running` | A run of this kind is already in flight for the project |
| 409 | `immutable_conflict` | An attempt to overwrite a completed artifact |
| 422 | `precondition_failed` | Valid request, wrong state (e.g. export before analysis) |
| 429 | `quota_exhausted` | The provider's daily budget is spent — includes `resets_at` |
| 502 | `provider_error` | An upstream provider failed |
| 503 | `storage_unavailable` | B2 unreachable |

`404` means the object genuinely is not there. A backend that fails to answer
is `503` — never `404`, which would tell a user their project no longer exists
during what is actually an outage.

`429` is a first-class case, not an afterthought: the Gemini free tier allows
20 analysis requests per day, so a client must be able to distinguish "you are
out of budget until tomorrow" from "something broke."

---

## 3. Resources

### 3.1 Health

```
GET /api/v1/health
GET /api/v1/health?worker=true
```

`200`

```json
{
  "status": "ok",
  "storage": "ok",
  "version": "0.1.0",
  "git_sha": "a1b2c3d4e5f6789012345678901234567890abcd",
  "build_time": "2026-08-02T12:00:00Z",
  "worker": null
}
```

Reports storage reachability because an API that is up while B2 is unreachable
can serve reads from nothing and accept writes that will fail later.
`status` is `"ok"` when storage is reachable and `"degraded"` otherwise; a
worker problem never flips the top-level status (a fully cached render does
not need the GPU).

`version` is the product version (`0.1.0`). `git_sha` and `build_time` name
the deployed commit and when the image was built (ISO8601 UTC). They are
baked into the image via `FOLEYFLOW_GIT_SHA` / `FOLEYFLOW_BUILD_TIME` (see
Dockerfile); both are `null` when unset — a local `uv run uvicorn` has no
build stamp and does not invent one.

`?worker=true` additionally probes the SFX worker behind the active tier.
Off by default: this endpoint is what a platform liveness check hits, and a
liveness check that depends on a third-party GPU box will restart a healthy
service when that box is off. Operators ask for the probe explicitly
(`foleyflow health` always does). With the probe requested, `worker` is:

```json
{
  "status": "ok",
  "tier": "test",
  "endpoint": "openmoss/moss-soundeffect-v2",
  "model": "openmoss/moss-soundeffect-v2",
  "device": "NVIDIA GeForce RTX 4070 SUPER",
  "auth_required": true,
  "auth_configured": true,
  "detail": null
}
```

`status` is one of:

| value | meaning |
|---|---|
| `ok` | reachable and loaded |
| `loading` | reachable; model still building (generation returns 503) |
| `unreachable` | the probe failed; `detail` says how |
| `not_applicable` | the active tier is a hosted API (paid/fallback), so there is no worker to probe |

`auth_configured` is `false` when the worker demands a bearer token and this
process holds none — that combination reads as healthy from the outside and
fails every generation, so it is called out rather than folded into `ok`.

---

### 3.2 Projects

#### Create

```
POST /api/v1/projects
{ "title": "Western standoff", "filename": "standoff.mp4", "content_type": "video/mp4", "budget_usd": 2.00 }
```

`201`

```json
{
  "project": { "id": "proj_01K…", "title": "Western standoff", "status": "awaiting_upload", "budget_usd": 2.0, … },
  "upload": {
    "url": "https://s3.us-east-005.backblazeb2.com/…?X-Amz-Signature=…",
    "method": "PUT",
    "expires_at": "2026-07-28T12:10:00Z",
    "headers": {"Content-Type": "video/mp4"}
  }
}
```

`budget_usd` is optional: an advisory dollar figure for this project (see
§3.11). When omitted, the `providers.toml [budgets]` default applies. It is
seeded as the first `postproduction.budget.set` event and can be changed later
via `PUT /projects/{id}/budget` — it is not a hard cap.

The project exists before its bytes do. That is deliberate: the upload can fail,
be retried, or be abandoned, and the project record plus its event log describe
exactly what happened.

#### Confirm upload

```
POST /api/v1/projects/{id}/uploaded
```

`202` — `{"run_id": "run_01K…"}`

Called by the client once the PUT succeeds. Verifies the object exists, records
its hash and duration, ingests it through Genblaze, and emits
`postproduction.project.created`. Asynchronous because ingest hashes and
re-uploads a video.

#### Read

```
GET /api/v1/projects/{id}
```

`200` — project record, derived status, readiness checklist, latest run,
timeline version, scene count, and whether an export exists. This is the single
call the control-room screen needs on load.

Signed artifact URLs (same TTL as every other artifact link):

| Field | When present |
|---|---|
| `source_url` | Source video is uploaded (object exists at the project's input key) |
| `mix_url` | A mix has completed — the latest mix's muxed `foley.mp4`, for pre-export A/B |
| `export_url` / `manifest_url` | An export has been published |

`source_url` and `mix_url` are optional: `null`/absent before upload or before
the first mix. They do not error when missing. `export_url` is post-export only;
`mix_url` is what the A/B player uses before publish.

#### List

```
GET /api/v1/projects?limit=25&cursor=proj_01K…
```

`200` — `{"projects": [...], "next_cursor": "proj_01K…" | null}`

Newest first. Cursor-paginated on the id, which works because ULIDs sort by
creation time — offset pagination over object storage would re-list the whole
prefix on every page.

---

### 3.3 Analysis

```
POST /api/v1/projects/{id}/analyze
{ "model": "gemini-2.5-flash", "force": false }
```

`202` — `{"run_id": "run_01K…", "kind": "analyze", "estimated_cost_usd": 0.005}`

| Status | When |
|---|---|
| `409 already_running` | Any run is in flight for this project |
| `429 quota_exhausted` | The model's daily budget is spent |
| `422 precondition_failed` | The upload has not been confirmed yet |
| `400 invalid_request` | The named model declares no request budget |

`force: true` bypasses the timeline cache.

The 202 carries **no** `cached` field: whether the timeline cache is hit is not
known until the worker runs, so any value here would be a guess. The completed
run's `metadata.cached` reports it once it is actually true.

A model with no declared `requests_per_day` is refused rather than allowed. An
undeclared budget means *unknown*, not *unlimited*, and treating it as the
latter let a client route analysis past the quota gate to a provider whose
credits are gone.

---

### 3.4 Timeline

```
GET /api/v1/projects/{id}/timeline?version=2
```

`200` — the timeline, its version, and the scene id each event falls in.
Omitting `version` returns the latest. `404 timeline_not_found` before analysis.

```
GET /api/v1/projects/{id}/timeline/versions
```

`200` — `{"versions": [1, 2], "latest": 2}`

Versions are immutable and coexist; a repair adds v2 without destroying v1.

---

### 3.5 Scenes

```
GET /api/v1/projects/{id}/scenes
```

`200` — each scene with `start_s`, `end_s`, colour statistics, and a signed
thumbnail URL. Feeds the scene rail in §6.1.

---

### 3.6 Runs

```
GET /api/v1/projects/{id}/runs
GET /api/v1/projects/{id}/runs/{run_id}
```

`200` — run record with `status` (`running` | `completed` | `failed`), `kind`,
`error_code`, `parent_run_id`, `genblaze_run_id`, and `metadata`.

`metadata` is what the run produced — artifact keys, stem and scene counts,
`ledger_delta_usd`, `qa_status` — and is served verbatim because it is written
by the server's own work functions. It is not a stable contract field by field:
each `kind` puts different keys in it.

**Workflow stages.** `genblaze_run_id`, `current_stage`, and `stages` are the
job history SPEC §8.2 asks for. `stages` is an ordered array; each entry carries
`name` (`ingest` | `stems` | `mix` | `mux` | `qa`), `status`
(`succeeded` | `failed`), `elapsed_s`, `provider`, `retries`, `detail`, its own
`genblaze_run_id`, and `error` when it failed. `current_stage` is the last stage
recorded — for a run still in flight, the one to render as active.

Unlike `error`, a stage's `error` is Genblaze's own step error rather than an
arbitrary exception message, so it is safe to display.

Stages are readable *while the run is in flight*: each one is appended to the
event log as it completes, and the finished run also carries the whole list on
its immutable record. That is why a client polling a long render sees progress
instead of nothing until the end.

This is the polling target for every `202`. A running run is reported from the
event log; a terminal run from its immutable record.

`error` on a failed run is a **classification, not the exception text**.
Provider and storage exceptions routinely embed presigned URLs, bucket names,
and absolute server paths, and this field is both written immutably to B2 and
served to clients. Branch on `error_code`; the detail is in the server log.

A run whose process died leaves a start event and no terminal record. After
`RUN_LEASE_S` it is reported as stale rather than running forever, and the
project's `stale_run_ids` names it.

```
GET /api/v1/projects/{id}/events
```

`200` — the append-only lifecycle log, oldest first. This is the audit trail
SPEC.md §6.3 calls "History", served directly rather than reconstructed.

---

### 3.7 Repair

```
POST /api/v1/projects/{id}/events/{event_index}/regenerate
{ "provider": "…", "confirm_cost_usd": 0.02 }
```

`202` — `{"run_id": "run_01K…", "estimated_cost_usd": 0.02}`

Addressed by **event index**, not name. Event names are descriptions and repeat
within a timeline — the same clip has "leather holster creaking" twice — so a
name is not an identifier.

`confirm_cost_usd` is required when the regeneration is billable and must match
the estimate the client was shown. SPEC.md §9.4 requires the estimate be shown
before a billable regeneration; requiring it back closes the loop, so a client
cannot spend money it never displayed.

The repair allocates a new timeline version beside the original and re-mixes it
with a variant salt on exactly one stem's cache address: that stem's bytes
change, every other stem is still served from cache, and the mix run records
the lineage — the superseded take, the new variant, and the version — in the
event log (`postproduction.repair.approved`) and on the run's metadata.

The stem is addressed by its routed request (endpoint, prompt, duration), not
by the timeline row: the SFX cache is content-addressed on exactly that tuple,
so two events routing to the same request already share one clip, and
regenerating either regenerates the shared stem. The lineage records the
requested event index either way.

The `provider` field names a declared SFX tier to regenerate through; the request
is re-aimed at that tier and the estimate is its cost. `422 precondition_failed`
when there is no plan yet or a billable regeneration is not confirmed
(`confirm_cost_usd`); the project's advisory budget never refuses work.
`400 invalid_request` for an out-of-range index or an unknown provider.

```
POST /api/v1/projects/{id}/mixes
{ "timeline_version": 2, "tier": "full" }
```

`202` — pre-flight projection of cache-miss cost only:

```json
{
  "run_id": "run_01K…",
  "kind": "mix",
  "estimated_cost_usd": 2.40,
  "projected_cost_usd": 2.40,
  "spent_usd": 3.70,
  "budget_usd": 5.00,
  "projected_total_usd": 6.10,
  "cache_hits": 4,
  "cache_misses": 8
}
```

Renders every stem the timeline needs, mixes, muxes against the source, uploads
`mix.wav` and `foley.mp4` under `runs/{run_id}/`, and re-measures QA against the
WAV that was actually written. `timeline_version` defaults to the latest;
`tier` is `ambience` | `discrete` | `full` and is the §9 degradation ladder.

The projection prices only cache misses via `plan_requests` + a cache probe
(same rule as `foleyflow prewarm --dry-run`). A fully cached re-mix projects
`$0.00`. Label it a projection: a mid-flight fallback can change the bill. The
advisory budget never refuses the mix.
What a run did spend is in its `metadata.ledger_delta_usd`.

`422 precondition_failed` before a timeline exists. `404 timeline_not_found`
for a version that does not.

---

### 3.8 QA

```
GET /api/v1/projects/{id}/qa
```

`200` — the latest QA report: loudness and true-peak measurements against
targets, and visual continuity warnings with the two scenes compared and a
confidence. Matches SPEC.md §11.5.

`404 qa_not_found` before analysis has written a report.

Blocking issues and warnings are distinguished in the payload, because §13 makes
a true-peak overage block export while a continuity difference only warns.
Continuity is written at analysis time from scene statistics. Audio metrics are
present only after a mix has been measured (`audio.measured`). When continuity
is refreshed without re-measuring, `audio.measured_run_id` keeps the provenance
of the preserved mix check — distinct from the report-level `run_id`.

---

### 3.9 Export and dossier

```
POST /api/v1/projects/{id}/exports
{ "run_id": "run_01K…", "override_blocking": false }
```

`202` — `{"run_id": "run_01K…", "kind": "export"}`

Writes the §11.6 provenance manifest and moves the `exports/latest.json`
pointer. It does **not** re-render: it publishes the MP4 a mix run already
produced and QA already measured, so the bytes a judge downloads are the bytes
that passed. `run_id` selects which mix to publish and defaults to the latest.

`422 precondition_failed` when readiness blocks export, listing the blockers
under `detail.blockers`. `override_blocking: true` is the explicit human
override §13 allows; it is recorded in the event log with the actor and the
manifest carries `qa_override: true`.

The published video and manifest come back as signed `export_url` and
`manifest_url` on `GET /projects/{id}`. Pre-export A/B uses the signed
`mix_url` on the same record (latest mix's `foley.mp4`), which is available as
soon as a mix completes and does not require publish.

```
GET /api/v1/projects/{id}/dossier
```

`200` — every artifact: source, scene index and thumbnails, timeline versions,
stem inventories and the stem clips themselves, mixes, the QA report, run
records, the event log, manifests, and the export pointer — each with its
storage key (provenance only), `size_bytes`, `sha256` where the system records
one, and a signed URL. The response also carries a compact `budget` block
(configured, spent, remaining, `estimate: true`, `ledger_writable` — §3.11)
so the closing beat shows the money as well as the media. This is the §15
demo's closing beat, so it is a single call that returns everything rather
than something the UI assembles.

Composition only: no artifacts are written and nothing is downloaded to build
the listing. Size comes from one backend HEAD per object; sha256 comes from
provenance where one is recorded (the source video, each stem) or from a local
backend's own hash. B2's S3 API exposes no sha256, so in a deployed store that
column is the recorded value or absent — never a guess. `url` is signed with
the same TTL as every other artifact link, so a dossier is browsable but never
a durable public handle.

---

### 3.10 Quota

```
GET /api/v1/quota
```

`200` — per-model usage and remaining budget with `ok` | `warn` | `blocked`
status, the next reset time, and `ledger_writable`.

`ledger_writable: false` means the figures are fiction rather than merely
imprecise: nothing is being recorded, so usage reads zero no matter how much
budget is actually spent. A UI seeing this must stop presenting the numbers at
all. The ledger itself is one immutable object per call under
`ledger/{ulid}.json` on the shared storage backend (not a process-local file),
so a healthy deploy keeps counting across restarts.

**These figures are estimates and the response says so** (`"estimate": true`).
Google exposes no remaining-balance field, so they are counted from our own
ledger and can drift — a shared key, or a request that failed after the server
counted it. The UI must present them as a guide, never as an authoritative
balance.

---

### 3.11 Budget

The project budget is **advisory**: set a figure whenever you like, get told
when spend approaches or passes it. Nothing is refused on budget grounds.
Billable repair still requires `confirm_cost_usd` (explicit per-action consent).

```
GET /api/v1/projects/{id}/budget
PUT /api/v1/projects/{id}/budget
{ "budget_usd": 5.00 }   // or null to clear
```

`200` — spend against the advisory figure, counted from the cost ledger (not a
provider balance):

```json
{
  "project_id": "proj_01K…",
  "budget_usd": 2.0,
  "spent": 0.87,
  "remaining": 1.13,
  "spent_estimated": 0.30,
  "spent_actual": 0.57,
  "estimate": true,
  "ledger_writable": true,
  "thresholds": [
    { "threshold": 0.5, "crossed": true },
    { "threshold": 0.8, "crossed": false },
    { "threshold": 1.0, "crossed": false },
    { "threshold": 1.2, "crossed": false }
  ],
  "by_model": [
    { "endpoint": "gemini-2.5-flash", "spent": 0.42 },
    { "endpoint": "openmoss/moss-soundeffect-v2", "spent": 0.0 }
  ]
}
```

`budget_usd` is latest-wins from `postproduction.budget.set` events (seeded at
creation from the body or `providers.toml [budgets] default`); `None` when
cleared or never set. `PUT` appends a new set event with the actor — the
project record is never rewritten. An unchanged re-`PUT` is a no-op (no new
event, no re-alerts). Lowering the figure beneath current spend evaluates
thresholds immediately and emits alerts with `reason: "budget_set"`; clearing
or raising above spend is silent. Alert failures never fail the `PUT`.
`spent` sums every attributed ledger row (failed calls included). `remaining`
is `budget_usd - spent` and may be negative when overspent. `spent_estimated`
/ `spent_actual` split by `cost_source`. Threshold fractions default to
50/80/100/120% (`providers.toml [budgets] thresholds`); each newly crossed
threshold emits exactly one `postproduction.budget.alert` lifecycle event
(idempotent under concurrent fan-out via `put_if_absent`). Alerts carry the
estimated-vs-actual split and a `reason` of `"spend"` or `"budget_set"`.
`ledger_writable: false` means figures are fiction and alerts are unreliable
— work is not blocked.

CLI: `foleyflow budget`, `foleyflow budget --set 5`, `foleyflow budget --clear`.

**Adopting project-less work.** A direct `foleyflow render` writes ledger rows
with no project (`project_id: null`), and rows written before attribution
existed have no such field at all. Rows are immutable, so rescuing them is an
adoption, not an edit:

```
POST /api/v1/projects/{id}/attributions
{ "before": 1785993600 }
```

`201` — `{"project_id": …, "before": …, "claimed": 7}` — every unattributed
row written before `before` (unix seconds; default now) is counted for this
project from now on. Overrides apply first-come by `before`, so a row can
never land in two projects. `foleyflow --project <id> render` opts future
project-less renders into a project instead, and `foleyflow attribute <id>`
writes the adoption.

---

## 4. Concurrency

Handler functions are synchronous (`def`, not `async def`) so Starlette runs
them in a worker threadpool. Every call into storage, ffmpeg, or a provider
blocks, and putting blocking work on the event loop would stall the whole
server on one B2 round-trip.

Long work does not run in the request at all. It is submitted to a background
runner and the request returns `202`. Run state lives in the B2 event log, not
in process memory, so a client polling a run gets the same answer regardless of
which worker serves it.

**Single-instance assumption.** The in-process job runner means concurrent runs
are safe within one instance but not across several. The deployment target is a
single Cloud Run instance, which matches SPEC.md's MVP scope, and `409
already_running` is enforced per project. Scaling out needs an external queue —
noted here so the constraint is visible in the contract rather than discovered
later.
