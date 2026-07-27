# FoleyFlow HTTP API v1

The contract. Written before the implementation and treated as the source of
truth: the Python packages under `src/foleyflow/` are an implementation detail
behind this surface, not the other way round. If a capability is not reachable
here, the product does not have it.

Base path: `/api/v1`

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
| 404 | `project_not_found` / `run_not_found` / `timeline_not_found` | Unknown id |
| 409 | `already_running` | A run of this kind is already in flight for the project |
| 409 | `immutable_conflict` | An attempt to overwrite a completed artifact |
| 422 | `precondition_failed` | Valid request, wrong state (e.g. export before analysis) |
| 429 | `quota_exhausted` | The provider's daily budget is spent — includes `resets_at` |
| 502 | `provider_error` | An upstream provider failed |
| 503 | `storage_unavailable` | B2 unreachable |

`429` is a first-class case, not an afterthought: the Gemini free tier allows
20 analysis requests per day, so a client must be able to distinguish "you are
out of budget until tomorrow" from "something broke."

---

## 3. Resources

### 3.1 Health

```
GET /api/v1/health
```

`200` — `{"status": "ok", "storage": "ok", "version": "0.1.0"}`

Reports storage reachability because an API that is up while B2 is unreachable
can serve reads from nothing and accept writes that will fail later.

---

### 3.2 Projects

#### Create

```
POST /api/v1/projects
{ "title": "Western standoff", "filename": "standoff.mp4", "content_type": "video/mp4" }
```

`201`

```json
{
  "project": { "id": "proj_01K…", "title": "Western standoff", "status": "awaiting_upload", … },
  "upload": {
    "url": "https://s3.us-east-005.backblazeb2.com/…?X-Amz-Signature=…",
    "method": "PUT",
    "expires_at": "2026-07-28T12:10:00Z",
    "headers": {"Content-Type": "video/mp4"}
  }
}
```

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

`202` — `{"run_id": "run_01K…", "cached": false, "estimated_cost_usd": 0.005}`

`409 already_running` if an analysis run is in flight.
`429 quota_exhausted` if the model's daily budget is spent.

`force: true` bypasses the timeline cache. `cached: true` in the response means
no provider request was spent and the run will complete almost immediately.

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
`stages`, `parent_run_id`, and `genblaze_run_id`.

This is the polling target for every `202`. A running run is reported from the
event log; a terminal run from its immutable record.

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

```
POST /api/v1/projects/{id}/mixes
{ "timeline_version": 2, "stem_selection": {…} }
```

`202` — re-mix from approved stems, producing a new mix version.

---

### 3.8 QA

```
GET /api/v1/projects/{id}/qa
```

`200` — the latest QA report: loudness and true-peak measurements against
targets, and visual continuity warnings with the two scenes compared and a
confidence. Matches SPEC.md §10.3.

Blocking issues and warnings are distinguished in the payload, because §13 makes
a true-peak overage block export while a continuity difference only warns.

---

### 3.9 Export and dossier

```
POST /api/v1/projects/{id}/exports
{ "run_id": "run_01K…", "override_blocking": false }
```

`202` — renders the final MP4, writes the provenance manifest, and moves the
`exports/latest.json` pointer.

`422 precondition_failed` when readiness blocks export, listing the blockers.
`override_blocking: true` is the explicit human override §13 allows, and is
recorded in the event log with the actor.

```
GET /api/v1/projects/{id}/dossier
```

`200` — every artifact: source, scenes, timeline versions, stems, mixes,
exports, QA reports, workflow logs, and the manifest — each with its B2 key,
sha256, size, and a signed URL. This is the §15 demo's closing beat, so it is a
single call that returns everything rather than something the UI assembles.

---

### 3.10 Quota

```
GET /api/v1/quota
```

`200` — per-model usage and remaining budget with `ok` | `warn` | `blocked`
status and the next reset time.

**These figures are estimates and the response says so** (`"estimate": true`).
Google exposes no remaining-balance field, so they are counted from our own
ledger and can drift — a shared key, or a request that failed after the server
counted it. The UI must present them as a guide, never as an authoritative
balance.

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
