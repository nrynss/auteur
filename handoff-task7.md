# Handoff — task 7, the control room

The one piece of FoleyFlow still unbuilt. Written 2026-08-02.

`handoff.md` carries the summary; this file is the whole thing, chunked into
units that can be picked up independently. **§A is background every unit
assumes.** Each unit after that states its own dependencies, files, acceptance
and non-goals, and restates the facts it needs, so an agent can take one
without reading the others.

Sizes follow `handoff.md`: **XS** one edit · **S** one file or one route ·
**M** spans modules, needs tests · **L** a build in its own right.

---

# A. Background

## A.1 What this is

SPEC §6.1 calls for a control room, and today there is only a throwaway static
dashboard. The target is not a status page with a video on it — it is a
**director's tool**: a timeline you edit directly, with an AI you talk to in
text, at a precision a working editor would accept.

Two things it must not be:

- **It must not look generated.** Colour-coded cards in a grid, flat rectangles
  standing in for audio, emoji as icons, a verdict where a measurement belongs.
  Density, real typography, monospace numerics, keyboard access, and honest
  waveforms are the difference.
- **It must not lie about precision.** Every number a director nudges is
  attached to something real — a transient position, a cache address, a dollar
  cost.

## A.2 Decisions already taken

Settled; not open unless a unit proves one impossible.

1. **A commit creates one timeline version.** Not one per action. Edits live in
   client-side draft state until the director commits, and each commit writes a
   new immutable timeline object. Undo is version switching, not a mutation
   stack.
2. **Resizing an event is free and never regenerates.** It windows the clip
   that already exists. Regeneration is its own explicit button with its own
   cost confirmation.
3. **An event's mix seed is frozen into the timeline as data** (unit 7.1).
4. **A run in flight locks editing**, and an expired run lease surfaces as a
   stuck-run error rather than an indefinite spinner.
5. **Stack: Svelte 5 + Vite + TypeScript** in a `ui/` directory, building to
   `src/foleyflow/api/static/`. Types are generated from the OpenAPI schema
   FastAPI already emits, so contract drift is a build failure rather than a
   demo-time surprise.
6. **Scope is the full app**, including project creation and upload.
7. **The old dashboard is deleted outright** — `api/static/index.html` and the
   single-file router in `api/dashboard.py`. Not kept behind a second route.
8. **Built assets are never committed.** FoleyFlow is a SaaS; the UI is built
   in the Docker image. A developer running the API without Node gets a clear
   "UI not built" response from the router — not a 404, and not a stale bundle
   in git pretending to be current.
9. **Director-mode model is `gemini-3.5-flash-lite`** (500 requests/day;
   `gemini-2.5-flash` allows 20 and would be exhausted in one session).
10. **The visual direction is locked** in `docs/design/control-room.md`, with a
    reference mockup beside it (`control-room-mockup.html`) built on the real
    `standoff_kling` project. Palette, type, layout, and the structural honesty
    rules are settled — 7.7 implements them, 7.8 onward consume them. Read it
    before writing any component.
11. **A commit submits the whole timeline document**, not a patch list. A
    version *is* a document, so submitting one makes the endpoint idempotent by
    construction, and director mode proposes a document diff rather than a
    patch language someone has to invent. Units 7.3 and 7.12 share this shape —
    it is a decision, not a per-unit choice.

## A.3 Rules that constrain every unit

- **Evidence-first (SPEC §8.4).** Show the measured number and its source,
  never a synthesised verdict. QA warnings are review prompts, not automatic
  corrections. The visual continuity check is an adjacent-shot mean-luma
  comparison and must never be presented as a detected fault.
- **Determinism.** Same timeline in, byte-identical mix out.
- **Immutability.** Manifests and completed generations are immutable; new
  work allocates a new version beside the old one.
- **`docs/api.md` is written before the code**, as every existing endpoint was.
- **Cost geometry is visible.** Free actions are instant and silent; anything
  that spends money says so before it does.

## A.4 The event round-trip trap

**Any unit adding a field to `Event` must read this.**

`Timeline` has an `extra_fields` catch-all. **`Event` does not.** It is a plain
dataclass of five required fields, and `dict_to_timeline` enumerates them by
hand (`timeline_io.py:33-41`). An event key that is not explicitly listed there
is **silently dropped on round-trip** — no error, no warning.

So adding `seed` (7.1) or `source_duration_s` (7.2) means editing `models.py`,
`timeline_to_dict` *and* `dict_to_timeline` together. Adding it to the
dataclass alone produces a field that vanishes on save and returns as `None` on
load, and the symptom surfaces units later as an inexplicable determinism bug.

Every unit touching `Event` needs a round-trip test: build a timeline, write
it, read it back, assert the new field survived.

## A.5 Corrections to earlier notes

The previous version of task 7 in `handoff.md` was wrong in three ways that
matter:

- **Readiness is already rendered.** `index.html` draws the percent bar and
  checklist rows. What has no UI at all is larger: no video player, no A/B, no
  QA report, no stage progress, no repair action, no dossier, no budget.
- **The API does not already serve everything the UI needs.** There is no
  timeline write path at all, and the source video's signed URL exists only
  inside the dossier.
- **The timeline data model must change first**, or the editor lies about what
  it is doing (units 7.1 and 7.2).

---

# B. Backend units

These are pure Python and testable with no UI. They are the backbone: the
editor is not buildable, or not honest, without them.

## 7.1 Freeze the mix seed into the event — M — **DONE**

**Depends on:** nothing. **Files:** `models.py`, `assemble.py`, `timeline_io.py`,
`analyze.py` (parse path), tests.

Rhythmic events (rain, continuing footsteps, engine rattle) are not one sound —
`assemble.py` tiles a 1.75 s loop across the event's span and jitters each
repetition's gain so it does not sound mechanical. Determinism forbids real
randomness, so the jitter is drawn from a seed: today
`sha256(event name + start time)` (`assemble.py:_event_seed`).

**Start time is therefore an ingredient in the sound.** Nudging a rhythmic
event 10 ms redraws every tile's gain: the texture changes, not just the
placement. In an editor that is intolerable — nudging must translate a sound,
not reshape it, and a director who liked a variation has no way back to it
except by landing on the exact original timestamp.

Add an optional `seed` field to the event:

- **Absent** → computed exactly as today, so every existing timeline and every
  stored manifest still reproduces byte-for-byte. This is why this route was
  chosen over dropping `start_time` from the hash.
- **Present** → used verbatim.

**Acceptance:** a timeline with no `seed` renders byte-identically to today; an
event carrying a frozen `seed` renders identically at two different
`start_time` values apart from its placement.

**Landed:** optional `Event.seed`; absent → legacy `_event_seed(name, start_time)`;
present → used verbatim for rhythmic `tile_loop`. Round-trip through
`timeline_io` and `parse_timeline_json` / `_validate_event` (A.4 trap closed on
both doors). `seed=0` is frozen, not absent. Director rules tell the model to
preserve seed when present.

**Not in this unit:** the editor writing the seed down (that is 7.10), and any
"reroll the variation" UI.

## 7.2 Split requested duration from placed span — M — **DONE**

**Depends on:** nothing (land with or after 7.1). **Files:** `models.py`,
`routing.py`, `assemble.py`, `timeline_io.py`, `analyze.py`, tests.

`routing.py` builds the SFX request duration differently per kind
(`routing.py:138-140`):

| kind | requested duration | resizing today |
|---|---|---|
| rhythmic | fixed `RHYTHMIC_LOOP_S` (1.75 s) | already free — changes tiling span only |
| discrete | `event.duration + HEADROOM_S` | **billable** — duration is in the cache key |
| continuous | `event.duration + HEADROOM_S` | **billable** — same reason |

Resize must be free (decision 2), so the field splits:

- **`source_duration_s`** — what was actually generated. Part of the SFX cache
  key `(endpoint, prompt, duration, variant)`. Changes **only** through the
  Regenerate button.
- **placement span** (`start_time` + `duration`) — freely editable, windows the
  existing clip, never re-bills.

For discrete events this is a genuinely new renderer capability: today a
discrete clip plays out in full and `duration` does not affect placement at all
(`assemble.py:284`), so "trim a one-shot" does not exist yet. Rhythmic and
continuous are span changes to paths that already exist.

**Acceptance:** changing an event's placement span produces a different mix
with an identical stem inventory — every `b2_key` unchanged, nothing
regenerated. Existing timelines without `source_duration_s` behave exactly as
today.

**Landed:** optional `Event.source_duration_s`. Absent → legacy
`duration + HEADROOM` / `RHYTHMIC_LOOP_S` and full discrete play-out. Present →
that value is the SFX request length (no second headroom); discrete windows
the stem to placement `duration` (empty placement → silence). Round-trip via
`timeline_io` and analyze parse; invalid freezes drop the event rather than
soft-unfreeze (re-bill).

**Not in this unit:** the Regenerate button's behaviour, which already exists.

## 7.3 Timeline write endpoint — M

**Depends on:** 7.1 and 7.2 (the schema must be final first).
**Files:** `docs/api.md` first, then `api/app.py`, `api/schemas.py`,
`projects.py`, tests.

There is **no timeline write path**. `GET /timeline` and
`GET /timeline/versions` are the only routes (`api/app.py:614`, `:639`).
Timelines are produced by analysis and forked by repair; nothing lets a client
say "this event moves 200 ms left".

The client submits **the whole timeline document** for the new version, not a
patch list (decision 10). The server allocates version N+1 beside the original.
Immutability is not an obstacle here — it is the design. One commit, one
version (decision 1).

Validation is the substance of this unit: the submitted document must be a
well-formed timeline, and the fields that carry cost — `source_duration_s`
above all — must not be silently altered by an edit that is supposed to be
free. An edit that changes a cache-key field without going through Regenerate
is a client bug and should be refused, not absorbed.

**Acceptance:** committing a document creates a new version; the prior version
is unchanged and still readable; a commit attempted while a run is in flight is
refused consistently with the existing `409 already_running` rule; a document
that alters `source_duration_s` is rejected.

## 7.4 Signed URLs on the project record — S — **DONE**

**Depends on:** nothing. **Files:** `docs/api.md`, `api/schemas.py`,
`api/app.py`, tests.

Add `source_url` and `mix_url` to `GET /projects/{id}`. The A/B player needs a
signed URL for the original video and for the foley mix. Today the source URL
exists only inside `/dossier` (`api/app.py:1028`), which HEADs every object in
the project to compose its listing — far too heavy for first paint.
`export_url` only exists post-export, so pre-export A/B needs the mix run's
`foley.mp4`.

**Landed:** `ProjectDetail.source_url` / `mix_url` via `_sign_if_exists` (HEAD
then sign — B2's `signed_url` is pure crypto and would otherwise return a URL
for a reserved-but-empty `input_key`). `mix_url` is the latest mix's
`video_key` (`foley.mp4`), not the wav. Standard `ARTIFACT_URL_TTL_S`.

**Acceptance:** a project with a completed mix serves both URLs, signed at the
standard TTL; a project before analysis serves neither and does not error.

## 7.5 Waveform peaks endpoint — M — **DONE**

**Depends on:** nothing. **Files:** `docs/api.md`, `peaks.py`, `api/app.py`,
`api/schemas.py`, tests.

Precomputed peak data per stem and per mix. A timeline of flat coloured
rectangles is the visual signature of generated software; waveforms are what
make it read as an instrument. Computing peaks server-side once beats every
client downloading and decoding full audio.

**Decisions landed:**
- **Resolution:** 100 peaks/second (`PEAKS_PER_SECOND`).
- **Storage:** sibling `{audio_key}.peaks.json` via `put_if_absent`; corrupt
  or wrong-resolution cache is treated as a miss and recomputed.
- **Format:** JSON with parallel `min` / `max` float lists (two-sided envelope).
- **Routes:** `GET .../peaks/stem?key=` and `GET .../peaks/mix?run_id=`
  (latest mix if omitted). Stem keys must appear in a project inventory
  (latest mix first, then short-circuiting scan).

**Acceptance:** peaks for a known clip match its actual envelope at the chosen
resolution, and the endpoint does not re-download audio it has already
summarised.

---

# C. Frontend foundation

## 7.6 UI toolchain and SPA serving — M

**Depends on:** nothing. **Files:** new `ui/`, `api/dashboard.py`, `Dockerfile`,
`.github/workflows/ci.yml`, delete `api/static/index.html`.

Svelte 5 + Vite + TypeScript in `ui/`, building to
`src/foleyflow/api/static/`. Types generated from the OpenAPI schema FastAPI
already emits. `api/dashboard.py` goes from serving one file to serving the
built SPA with a catch-all so client-side routes deep-link, and returns a clear
"UI not built" response when the bundle is absent.

Multi-stage Dockerfile: a Node stage builds `ui/`, the Python image copies only
the built assets, so no Node ships to Cloud Run. CI gains a UI build job.
Built assets are never committed (decision 8).

**Acceptance:** `docker build` produces an image serving the SPA with no Node in
it; a deep link to a client-side route returns the app; the API without a built
bundle explains itself rather than 404ing.

Note the test suite currently covers the dashboard route **not at all** —
deleting `index.html` breaks nothing, which also means there is no regression
net. This unit adds the coverage that does not exist today.

## 7.7 Component layer and design tokens — M

**Depends on:** 7.6. **Files:** `ui/src/lib/`.
**Read first:** `docs/design/control-room.md` — the locked direction — and open
`docs/design/control-room-mockup.html` in a browser. Do not re-derive the
palette, type scale or layout; they are settled. Where the doc and the mockup
disagree, the doc wins.

The screen's vocabulary repeats hard — state chips in four states
(`ok`/`warn`/`blocked`/`pending`), "measured number + its source" evidence
rows, scene thumbnails, stage progress with elapsed and retries,
cost-confirmation dialogs. Define tokens and roughly ten primitives before
building screens.

Enforce evidence-first structurally rather than by memory: if the only
component that renders a QA number requires its source alongside it, no screen
can accidentally show a verdict.

Accessibility is not decoration here — a control room that is all colour-coded
state must not encode meaning in colour alone.

*(Claude Design / `DesignSync` was considered and set aside: it syncs an
existing library to claude.ai as standalone preview HTML, it does not design
anything, and with Svelte it would mean duplicate previews that drift or a
render step. Revisit only if the library must be shared outside the repo.)*

---

# D. Screens

## 7.8 Control room, read-only — L

**Depends on:** 7.6, 7.7; 7.5 for waveforms.

Readiness header (percent, checklist rows, budget chip carrying `estimate:
true` and a visible warning when `ledger_writable` is false), scene rail,
timeline rendered but not yet editable, QA evidence, history (event log,
timeline versions, prior mixes), dossier.

QA evidence renders continuity warnings as the two thumbnails compared, side by
side, with confidence — a comparison, never a fault.

**Acceptance:** a real seeded demo project renders completely with no
placeholder or invented values.

## 7.9 A/B player and browser preview — L

**Depends on:** 7.4, 7.8.

One transport, two sources, toggle preserving playhead, keyboard shortcut.

Then the preview loop, which is what decides whether this feels like a tool: if
every nudge needs a server mix, the edit loop costs money and minutes and the
tool is dead. Stems are already individually addressable with signed URLs, so
preview schedules them at edited offsets via Web Audio against the video —
instant scrubbing, free, no server round trip.

**The rule is honesty.** A browser mix skips `rms_normalize`, ducking and
true-peak measurement, so it is labelled a preview, and **QA numbers only ever
come from the server's measured mix.** Never present the approximation as the
measurement.

**Also in this unit:** the playhead shows the **transient**, not `start_time`.
Discrete events are placed at `start_time + DISCRETE_BIAS_S - onset_s`
(`assemble.py:284`) — a deliberate +50 ms late bias minus the clip's trimmed
onset. A ruler labelling `start_time` as the hit point is wrong by tens of
milliseconds, exactly the resolution a director works at.

## 7.10 Direct manipulation — L

**Depends on:** 7.2, 7.3, 7.8, 7.9.

Move, resize, draft state (persisted so a reload does not lose work), commit to
a new version, switch versions as undo. Writes the frozen `seed` (7.1) onto any
event it touches.

Cost geometry visible throughout: moving is free, resizing is free, **renaming
is billable and possibly re-routed** (the prompt derives from the name, and
`route_event` keyword-matches the name to pick the endpoint), regenerating is
billable.

**Acceptance:** a director can nudge a rhythmic event repeatedly and hear only
translation; committing produces exactly one new version.

## 7.11 Spend paths and run locking — M

**Depends on:** 7.8.

Regenerate, mix, and export, each with its confirmation. `confirm_cost_usd` is
echoed back per SPEC §9.4, so the UI cannot spend money it never displayed.
Export lists blockers and puts `override_blocking` behind an explicit confirm
that records the actor.

Locking: editing is disabled while a run is in flight, live stages render as
they arrive (`stages` carries `name`, `status`, `elapsed_s`, `retries`,
`provider` and is readable mid-flight), and an expired `RUN_LEASE_S` becomes a
real stuck-run error — the API already reports these in `stale_run_ids`. There
is no SSE; this polls.

## 7.12 Director mode — L

**Depends on:** 7.3, 7.10.

Text in, timeline edits out. The discipline that keeps it from being a gimmick:
**chat proposes, never applies.** The model emits a schema-validated edit set,
the timeline renders it as a diff — ghosted old positions against new — and the
director approves or rejects. That is SPEC §8.4's stated principles (schema
validated outputs, explicit human approval for consequential actions), and it
is better UX: "move all the footsteps 40 ms earlier" is worth reviewing before
it costs anything.

Model is `gemini-3.5-flash-lite` (decision 9). Every turn is a provider call:
it goes through the ledger and counts against quota like everything else.

## 7.13 Project creation and upload — M

**Depends on:** 7.6, 7.7.

Projects index (status, readiness, budget), then create: title, budget, file
picker → `POST /projects` → presigned `PUT` with real upload progress →
confirm → kick off analysis. No UI exists for any of this today.

## 7.14 Polish — M

**Depends on:** everything.

Accessibility pass, keyboard map, empty and error states, the pass that decides
whether it reads as a tool.

---

# E. Running this with multiple agents

**Logically independent is not the same as safely parallel.** Several units
reason independently but edit the same files, and handing those to concurrent
agents buys merge conflicts rather than speed.

Two contention clusters:

- `models.py`, `timeline_io.py`, `assemble.py` — units **7.1 and 7.2**.
- `api/app.py`, `api/schemas.py`, `docs/api.md` — units **7.3, 7.4 and 7.5**.

Give each cluster to one agent, sequenced internally.

**Wave 1 — three agents, genuinely disjoint:**

| agent | units | why it is safe |
|---|---|---|
| frontend | 7.6 | touches no Python the others touch |
| backbone | 7.1 → 7.2 | the whole timeline-model cluster, sequenced |
| api | 7.4 → 7.5 | 7.4 is small; landing it first keeps `app.py` churn ordered |

**Wave 2:** 7.3 (needs 7.1 and 7.2 landed) ‖ 7.7 (needs 7.6). Different
languages, different files.

**Wave 3:** 7.8 ‖ 7.13. Both need only 7.6 and 7.7, and they touch different
screens.

**Wave 4:** 7.9 ‖ 7.11.

**Wave 5 onward:** 7.10 → 7.12 → 7.14. These genuinely serialise; 7.10 depends
on four prior units.

**Critical path:** `7.6 → 7.7 → 7.8 → 7.9 → 7.10 → 7.12 → 7.14`. The frontend
chain is the long pole, not the Python. Start 7.6 first and never let it block
on a backend unit; the backbone (7.1 → 7.2 → 7.3) has slack against it, as long
as 7.3 lands before 7.10 needs it.

# F. Testing and bookkeeping

Python-side tests for every new field, the write endpoint, the two project
URLs, the peaks endpoint, and SPA serving. **The suite count in `handoff.md`
and `SPEC.md` moves with each backend unit and CI asserts it**
(`scripts/check_test_count.py`) — update both in the same commit.

Vitest for the UI logic actually worth testing — A/B playhead sync, the
cost-confirmation round trip, draft-to-commit version allocation — rather than
blanket component tests.

# G. Still open

- The states the mockup does not cover — loading, empty, error, the in-flight
  run lock, draft-versus-committed, and the director-mode diff drawn on the
  timeline itself. Listed in `docs/design/control-room.md` §9. These are design
  work, not improvisation from the mockup, and they belong to the units that
  need them (7.8, 7.10, 7.11, 7.12).
