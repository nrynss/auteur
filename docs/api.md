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
| 404 | `project_not_found` / `run_not_found` / `timeline_not_found` / `qa_not_found` / `audio_not_found` | Unknown id or missing audio |
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

Each event carries the placement fields plus optional freezes the editor
round-trips:

```json
{
  "version": 2,
  "duration": 15.04,
  "ambience": "desert wind",
  "events": [
    {
      "index": 0,
      "name": "holster creak",
      "kind": "continuous",
      "intensity": "low",
      "start_time": 0.0,
      "duration": 1.0,
      "seed": null,
      "source_duration_s": 1.5,
      "stem_key": "cache/fa1d803456c3c126.wav",
      "scene_id": "scene_01"
    }
  ]
}
```

`seed` is the frozen mix-variation seed (writable on commit).
`source_duration_s` is the frozen SFX request duration (a cache-key / cost
field). It is **stamped by the server when a mix run completes**, from the
durations that run actually requested and paid for, onto a new timeline version
written with `actor: "system"` and `payload.source: "mix_freeze"`. A commit may
set it on a previously-unfrozen event **only at the value that event already
routes to** — recording an address that exists, never inventing one — and may
never change or clear an existing freeze on content that is still in the
document. Full rules under Commit below.

`stem_key` is the storage address of the audio the event's stem lives at,
stamped at the same time and the same way (`source: "mix_freeze"`). Names
repeat and stems dedupe, so this is the only honest event→audio link — the
browser preview uses it via §3.13. It is refreshed when a repair moves the
address (new variant), and is `null` for an event that has never been mixed.
A commit may not write it (read-only; the server owns the address).

```
GET /api/v1/projects/{id}/timeline/versions
```

`200` — `{"versions": [1, 2], "latest": 2}`

Versions are immutable and coexist; a repair or a commit adds v2 without
destroying v1.

#### Commit (write a new version)

```
PUT /api/v1/projects/{id}/timeline
{
  "duration": 15.04,
  "ambience": "desert wind",
  "events": [
    {
      "name": "holster creak",
      "start_time": 0.2,
      "duration": 1.0,
      "intensity": "low",
      "kind": "continuous",
      "seed": null,
      "source_duration_s": 1.5
    }
  ]
}
```

**Synchronous** storage write (unlike analyze/mix/export). The client submits
the **whole timeline document**; the server allocates version N+1 beside the
prior immutable version via `put_if_absent`. There is no patch language — a
version *is* a document. The write does **not** claim a job slot.

`200` — same shape as GET for the **new** version (`version`, events with
`index`, `seed`, `source_duration_s`, `scene_id`).

| HTTP | `code` | When |
|---|---|---|
| 200 | — | New version stored |
| 400 | `invalid_request` | Malformed body, or `source_duration_s` freeze violated |
| 404 | `project_not_found` | Unknown project |
| 409 | `already_running` | Any run is in flight for this project (same lock as analyze/mix/export) |
| 422 | `precondition_failed` | No timeline yet — nothing to edit; analyze first |

**`duration` is not writable.** The timeline's `duration` is display-only with
the ffprobe measurement as its sole source of truth (`models.Timeline`). The
field is accepted in the body — the client round-trips the whole document — but
the server carries the previous version's value forward, along with `history`
and any other server-side annotations. A commit cannot diverge the document
from the probed clip length.

**Cost freezes (`source_duration_s`).** Bound to event *content*
`(name, intensity)`, not list index — reordering while keeping freezes on the
same sounds is legal; swapping freezes between different sounds is not.

`kind` is deliberately **not** part of freeze identity. Once a freeze is set,
`route_event` takes the request duration from it regardless of kind, and the
rest of the SFX cache key does not depend on kind either (endpoint is
keyword-matched from `name`, prompt is name + intensity qualifier, variant is a
per-regeneration salt). A kind change therefore moves no cache address and
bills nothing — it only changes how the existing clip is rendered — so it is a
free edit.

Against the latest existing timeline, the multiset of freeze identities
`(name, intensity, source_duration_s)` is conserved, with two exceptions:

| Edit | Result | Why |
|---|---|---|
| First freeze at the event's own routed duration | **allowed** | Records the address the next render would have generated anyway — no address moves, nothing re-bills |
| First freeze at any other value | `400` | Names a clip nobody has paid for |
| Change an existing freeze's value | `400` | Rebinds to a different cache address |
| Move a freeze to different content | `400` | Same |
| Clear a freeze while the content stays in the document | `400` | In-place unfreeze; the next render re-bills it |
| Delete the frozen event (content absent from the document) | **allowed** | The money is already spent and the sound is never rendered again, so conservation has no claim on it |
| Empty `events` while freezes exist | `400` | Not a conservation rule — a client-bug guard against a serialization failure wiping a plan. Delete events explicitly instead |
| Rename or change intensity on a frozen event | `400` | Both feed the prompt: a different generation |
| Reorder, nudge, resize, edit `seed` | **allowed** | Free |
| Add or remove unfrozen events | **allowed** | Free |

The "first freeze" value is exactly `route_event(previous_event).duration_s` —
i.e. `source_duration_s` if already set, else `RHYTHMIC_LOOP_S` for rhythmic
events or placement `duration + HEADROOM_S` otherwise, clamped to the
endpoint's declared bounds.

Deleting frozen content and re-adding it unfrozen in **one** commit is refused
(the content is present in the submitted document, so it reads as an in-place
unfreeze). Doing it across two commits is not detectable per-commit and is
accepted: the deletion was legal, and what is re-added is new content that
bills whatever it bills.

`source_duration_s: 0.0` is a real freeze, not an absent one — clearing it to
`null` is an unfreeze. It does not mean a zero-length request: `route_event`
clamps to the endpoint minimum, so a frozen `0.0` addresses the
endpoint-minimum clip.

A violation is `400 invalid_request` with `detail.violations`.

**Regenerate** re-rolls the SFX *variant* for one event; it does **not** rewrite
`source_duration_s`. Generation duration stays frozen for that content until a
future path owns an explicit duration change. Commit never absorbs a freeze
edit.

**Run lock.** `409 already_running` when the in-process job runner has work for
this project **or** storage still has a non-stale `running` run (lease not
expired). Stale runs do not lock the editor forever.

**Audit.** `plan.proposed` for a commit carries `actor: "user"` and
`payload.source: "commit"` so History can tell editor commits from analysis
(no `source`), repair (no `source`), and the server's post-mix freeze stamp
(`actor: "system"`, `source: "mix_freeze"`).

**Post-mix freeze stamp.** When a mix run completes, the server writes a new
timeline version carrying `source_duration_s` for every event whose stem the
run actually recorded, and the mix is recorded against *that* version. The
stamped document routes to identical `SfxRequest`s and renders byte-identically
— it only writes down which cache addresses the mix used. Events whose stem is
absent stay unfrozen. Discrete events also have their placement `duration`
widened to the frozen source length, because a freeze switches on windowing:
without it the stamp would silently cut the one-shot's headroom. Shortening it
afterwards is then a free resize.

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

### 3.12 Waveform peaks

Server-side peak envelopes for control-room waveforms. Computing peaks once
on the server beats every client downloading full audio just to draw a line.

**Resolution:** 100 peaks per second (10 ms bins). Dense enough for timeline
zoom, small enough that a 30 s stem is ~3000 floats per `min`/`max` array.

**Storage:** peaks live next to the audio object as `{audio_key}.peaks.json`
(e.g. `cache/03f8c512074fd16b.wav.peaks.json`, or
`projects/{id}/runs/{run_id}/mix.wav.peaks.json`). Written with
`put_if_absent` so concurrent first paints do not race. A cache hit returns
the stored JSON without re-decoding audio.

**Peak algorithm:** for each 10 ms bin, `min` and `max` across every sample
and channel in that bin (two-sided envelope). Values are raw float samples
from the canonical 48 kHz stereo float WAV (typically in `[-1, 1]`).

```
GET /api/v1/projects/{id}/peaks/stem?key=<b2_key>
GET /api/v1/projects/{id}/peaks/stems?run_id=<run_id>
GET /api/v1/projects/{id}/peaks/mix?run_id=<run_id>
```

`200`

```json
{
  "peaks_per_second": 100,
  "channels": 2,
  "sample_rate": 48000,
  "duration_s": 1.75,
  "min": [-0.12, -0.08, …],
  "max": [0.15, 0.11, …]
}
```

| Route | Selects audio | Auth |
|---|---|---|
| `/peaks/stem?key=` | Content-addressed stem at `key` | `key` must appear as a `b2_key` in some stem inventory for this project |
| `/peaks/stems?run_id=` | Every stem in that run's inventory (omit `run_id` → latest mix) | The run's own inventory *is* the authorisation — no client-supplied keys |
| `/peaks/mix?run_id=` | `mix.wav` for that run (omit `run_id` → latest mix) | Run must belong to the project |

#### Batch stems (first paint)

`/peaks/stem` is per-key, and each call re-reads the project's stem inventory
to authorise the key — a 40-event timeline painted with 40 downloads of the
same inventory object. `/peaks/stems` reads that inventory once and answers
with every envelope for the run, keyed by `b2_key`. The per-key route stays for
incremental needs (a stem that arrives after first paint).

`200`

```json
{
  "run_id": "run_01J…",
  "stems": {
    "cache/03f8c512074fd16b.wav": {
      "b2_key": "cache/03f8c512074fd16b.wav",
      "peaks": { "peaks_per_second": 100, "channels": 2, "…": "…" },
      "error": null,
      "message": null
    },
    "cache/9ab1e0c4d5f60071.wav": {
      "b2_key": "cache/9ab1e0c4d5f60071.wav",
      "peaks": null,
      "error": "audio_not_found",
      "message": "No audio object at cache/9ab1e0c4d5f60071.wav"
    }
  }
}
```

**Partial failure is reported, never skipped.** A stem that is absent
(`audio_not_found`) or not a decodable WAV (`invalid_request`) becomes an entry
with `peaks: null` and that `error` code — the same codes the per-key route
raises. Omitting it would leave the client unable to tell a hole in the bucket
from a stem the server never looked at, which is the synthesised-verdict
failure evidence-first exists to prevent. Storage being *unreachable* is not a
per-stem fact: it fails the whole route with `503 storage_unavailable`, like
every other read. An address repeated in the inventory (several events sharing
one stem) appears once.

The route-level `404`s match the mix route: `mix_not_found` when the project
(or the named run) has no completed mix, `run_not_found` for an unknown run,
`project_not_found` for an unknown project, `400 invalid_id` for a malformed
`run_id`.

| Status | When |
|---|---|
| `404 project_not_found` | Unknown project |
| `404 audio_not_found` | Authorised key but the WAV is not in storage |
| `404 mix_not_found` | Mix route: project/run exists but has no completed mix |
| `404 run_not_found` | Mix route: unknown `run_id` |
| `400 invalid_request` | Stem key not in this project's inventories (foreign keys refused); or authorised key is not a valid WAV |
| `400 invalid_id` | Malformed project or run id |

### 3.13 Browser preview stems

```
GET /api/v1/projects/{id}/preview/stems/{key}
```

`200` — the stem WAV streamed server-side, `Content-Type: audio/wav`.

This is the same-origin fetch the browser preview (unit 7.9) uses to play
stems in Web Audio. B2's signed URLs carry no CORS headers, so a browser
cannot decode them directly; this route re-serves the object from storage,
and because the UI and the API are same-origin no CORS is needed here either.
The client already holds `key` from the event's stamped `stem_key` (§3.4).

`key` must appear as a `b2_key` in some stem inventory for this project —
the same rule as the peaks stem route (§3.12). Arbitrary bucket keys are
refused: the preview may only ever play audio this project actually generated.

| Status | When |
|---|---|
| `200` | WAV streamed |
| `404 project_not_found` | Unknown project |
| `404 not_found` | Authorised key but the object is not in storage |
| `400 invalid_request` | Stem key not in this project's inventories (foreign keys refused) |
| `400 invalid_id` | Malformed project id |

`min` and `max` are the same length: `ceil(duration_s * peaks_per_second)`
bins (empty audio → empty arrays). The client draws a two-sided waveform
from the pair; mono max-abs is recoverable as `max(abs(min[i]), abs(max[i]))`.

---

### 3.14 Ledger rows

```
GET /api/v1/projects/{id}/ledger?limit=50&cursor=0
```

`200` — individual cost transactions for this project, newest first. `/budget`
summarises; this is the audit trail so every number in the summary is traceable
to a specific call.

```json
{
  "project_id": "proj_01K…",
  "total_count": 142,
  "rows": [
    {
      "timestamp": 1753305781.234,
      "endpoint": "gemini-2.5-flash",
      "cost_estimate": 0.005,
      "cost_source": "estimate",
      "success": true,
      "error": null,
      "project_id": "proj_01K…",
      "params_hash": "a1b2c3d4e5…"
    }
  ],
  "next_cursor": 50,
  "estimate": true,
  "ledger_writable": true
}
```

| Field | Type | Notes |
|---|---|---|
| `total_count` | int | Total rows attributed to this project across all pages |
| `next_cursor` | int\|null | Index for the next page; null when on the last page |
| `rows` | list | Subset of rows, newest first |
| `rows[].timestamp` | float | Unix seconds |
| `rows[].cost_estimate` | float | Historical field name kept for backward compatibility |
| `rows[].cost_source` | str | `"estimate"` or `"actual"` — whether the value came from the response |
| `rows[].project_id` | str\|null | The project the row was written for; null when it was claimed by a rescue override (§3.11) rather than tagged at write time |
| `rows[].params_hash` | str\|null | Hash of the call's arguments for deduplication visibility |
| `estimate` | bool | Same flag as `/quota` — true, never false |
| `ledger_writable` | bool | `/quota`'s flag: false means figures are fiction |

**Failed rows are included.** A request that failed may still have consumed
provider quota, and excluding it would produce a subset that does not reconcile
with `/budget`.

Rows written before project attribution existed, and rows written in a direct
`foleyflow render` without `--project`, carry `project_id: null` in storage.
A rescue override (§3.11) claims them; rows from that project fetch through the
same override so they are visible here as well. Rows that belong to no project
(no matching override) are not returned by this endpoint.

**Pagination** is index-based on the sorted list. `cursor=0` is the first page.
The response's `next_cursor` is the index one past the last returned row; pass
it as `cursor` on the next request. Newer rows written between page fetches may
shift results (the same bound as any pagination over append-only data where the
cursor is not a stable key), but the full ledger is re-fetched from the backend
at most every 10 s (the row cache TTL), so the window is bounded. The totals
in `/budget` are always the authoritative figures.

| Status | When |
|---|---|
| `404 project_not_found` | Unknown project |
| `400 invalid_id` | Malformed project id |
| `503 storage_unavailable` | Ledger backend unreachable |

---

## 4. Concurrency
