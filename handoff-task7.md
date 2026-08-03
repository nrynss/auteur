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

**Who sets one.** For a while nobody could: analysis is told not to invent a
freeze, and the commit endpoint refused to create one, so the mechanism was
unreachable in production. `render_mix` now stamps it — when a mix completes it
writes a new timeline version recording, per event, the duration that run
actually requested and paid for (read back from the stem inventory, which is
the post-clamp value the cache address was computed from), with
`actor="system"` and `source="mix_freeze"`. The mix is recorded against the
stamped version, since that is the document naming its addresses and it renders
byte-identically. Events whose stem is absent stay unfrozen — a freeze with no
provenance would be a fabricated number. Discrete events also have their
placement span widened to the frozen source length, because the freeze switches
on windowing and the stamp must not shorten the sound. A commit may then set a
*first* freeze itself, but only at the value the event already routes to.

**Not in this unit:** the Regenerate button's behaviour, which already exists.

## 7.3 Timeline write endpoint — M — **DONE**

**Depends on:** 7.1 and 7.2 (the schema must be final first).
**Files:** `docs/api.md` first, then `api/app.py`, `api/schemas.py`,
`projects.py`, tests.

**Landed:** `PUT /api/v1/projects/{id}/timeline` submits the whole timeline
document; server allocates immutable version N+1 via `save_timeline` with
`actor=user` and `payload.source=commit`. GET events expose `seed` and
`source_duration_s`.

**Freeze conservation:** multiset of identities
`(name, intensity, source_duration_s)` — no invent at an arbitrary value, no
rebind, no in-place unfreeze, no empty-document wipe. Two deliberate holes:
a *first* freeze at the event's own routed duration is allowed (otherwise 7.2
is unreachable through the API), and **deleting** frozen content is allowed
(the money is already spent; refusing it meant a director could never remove a
generated sound). `kind` is not in the identity — with a freeze set it changes
no part of the cache key, so a kind change is free. Reorder and
placement/`seed` edits are free. Unfrozen events may be added/removed.

**`duration` is server-owned** on commit: carried forward from the previous
version like `history`, since ffprobe is its sole source of truth.

**One log read.** The commit folds `list_events` once and hands it to the state
load, the run-lock check and the live-run scan, rather than reading it three
times (one object GET per event).

**Run lock:** `409 already_running` from in-process JobRunner **or** a
non-stale storage-side running run (event-log fold; `list_runs` has no
in-flight records).

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
- **Routes:** `GET .../peaks/stem?key=`, `GET .../peaks/stems?run_id=` and
  `GET .../peaks/mix?run_id=` (latest mix if omitted). Stem keys must appear in
  a project inventory (latest mix first, then short-circuiting scan).
- **Batch form for first paint:** `/peaks/stems` reads the run's inventory once
  and returns every envelope keyed by `b2_key`. The per-key route pays for an
  inventory download on each call, so a 40-event paint fetched the same object
  40 times. A stem that is absent or undecodable is reported as an entry with
  an `error` code rather than skipped — a hole must be visible as a hole.

**Acceptance:** peaks for a known clip match its actual envelope at the chosen
resolution, and the endpoint does not re-download audio it has already
summarised.

---

# C. Frontend foundation

## 7.6 UI toolchain and SPA serving — M — **DONE**

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

**Landed:** `ui/` is Svelte 5.56 + Vite 8 (rolldown) + TypeScript 5.9 — TS 7 is
not supported by svelte-check/openapi-typescript yet, and both pin `^5`, so the
pinned TS is deliberate, not stale. `scripts/emit_openapi.py` writes the schema
from `create_app().openapi()` offline (local backend pinned; no credentials, no
server); `npm run typegen` (openapi-typescript) turns it into
`ui/src/lib/api/schema.d.ts`; `npm run check` (svelte-check) is the contract-
drift tripwire and runs in CI. `api/dashboard.py` serves the built SPA at `/`
with a catch-all that (a) refuses `api/` paths so unknown API routes keep the
API's JSON 404, (b) serves real files (hashed `/assets/*`) only, with a
resolve/relative_to traversal guard, (c) returns an explanatory "UI not built"
page when the bundle is absent. `FOLEYFLOW_STATIC_DIR` overrides the static dir
for tests. Dockerfile: `node:22-slim` stage runs `npm ci` + `npm run build`
(bundle → `/app/src/foleyflow/api/static`), runtime image copies only that
output. CI `ui-build` job: sync → emit schema → `npm ci` → `check` → `build`,
plus a `git ls-files src/foleyflow/api/static/` assertion that no built asset
got tracked. Old `index.html` deleted; `tests/test_dashboard.py` adds the
regression net that did not exist (10 tests: unbuilt explanation, SPA at root,
deep links, hashed assets, API 404s not swallowed, traversal refused).

**Not in this unit:** design tokens/components (7.7), any screen (7.8+). The
`App.svelte` shell is deliberately minimal — a health + project-list readout
that proves the schema-driven client round trip.

**Hardened post-landing (review fixes):**

- **Contract drift fails the artifact build, not just CI** (decision 5).
  `npm run build` is `typegen && check && vite build` — svelte-check runs
  inside the Dockerfile's Node stage, so `docker build` fails on drifted
  types. `build:fast` (typegen + vite build, no check) exists for iteration;
  the default build is the safe one. CI's explicit check step stays so a
  failure is attributable to a named step.
- **The Dockerfile's Node stage is conventional and verified.** `WORKDIR
  /app/ui`, `npm ci` at the stage root, build from the same directory — no
  ancestor node_modules resolution — and `node:22.12-slim` is pinned so the
  Vite 8 engines floor (^20.19 || >=22.12) is real, not incidental. CI gains
  a `docker-build` job (master pushes + `workflow_dispatch`, not PRs) that
  builds the image and asserts the acceptance criterion: container starts,
  `GET /` serves the built SPA (not the unbuilt page), a deep link returns
  the SPA, and node/npm are absent from the runtime image.
- **Cache headers are deliberate in both directions** (RFC 9111).
  `index.html` (root, deep links, and the unbuilt page) is `Cache-Control:
  no-cache` so heuristic freshness cannot outlive a deploy and reference
  hashed assets that no longer exist; content-hashed `/assets/*` are `public,
  max-age=31536000, immutable`. Both carry `X-Content-Type-Options: nosniff`.
- **The catch-all guard has no hole:** bare `/api` and `/api/*` are refused
  (the API's JSON 404), and both routes answer `HEAD` as well as `GET` so
  HEAD-based uptime monitors see the root as healthy.
- **Post-deploy smoke detects a bundle-less image** (task 12's
  `scripts/smoke_deploy.py`): the unbuilt page stays a 200 for local dev
  (tests pin it), so `check_spa` asserts `GET /` serves the built SPA
  specifically — mount point `<div id="app">` present, "not built" absent.
  A structurally broken image now fails the smoke even though /health is
  green.

## 7.7 Component layer and design tokens — M — **DONE**

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

**Landed:** tokens in `ui/src/lib/theme.css` exactly per control-room.md §2–§5
— full light/dark sets on `:root`, redefined under `@media
(prefers-color-scheme: dark)` and again under `:root[data-theme="dark"]` /
`[data-theme="light"]` so an explicit toggle wins in both directions; one
shadow (the video frame only); mono/tabular numerics via `.num`. Theme
mechanism: `theme.ts` (resolve/set/toggle, persisted to localStorage) + an
inline pre-paint script in `ui/index.html` (no light-flash) + `ThemeToggle`.

Twelve primitives in `ui/src/lib/components/`:

- **Button** — secondary/primary/ghost/danger, `pending` (spinner + disabled),
  sm size, data-* passthrough.
- **Chip** — the four states; every chip renders a glyph AND the word (colour
  never carries meaning alone), `aria-label` is word + detail, `pressed`/
  `live` variants for the version selector.
- **Evidence** — the structural honesty rule: `source` is a **required** prop,
  so a screen showing a QA number without naming the artifact it was measured
  from does not compile. Default phrase "measured from", overridable.
- **SceneThumb** — index badge + honest span timecodes (9.5px mono), current
  ring, lazy images, placeholder for missing thumbs.
- **StageRow** — name, status chip (running/failed/done), elapsed, retries,
  provider; renders mid-flight (7.11 polls).
- **ConfirmDialog** — modal, focus-managed (confirm button on open, return on
  close, Tab trap), Escape/backdrop cancel, cost line (7.11's "cannot spend
  money it never displayed"), border + backdrop instead of a drop shadow (§4).
- **Timecode / format.ts** — `formatTimecode` (m:ss.mmm, carries rounding
  across second boundaries, absent → "—") + `formatCost`.
- **Meter, Panel, Kbd, Spinner** (reduced-motion aware), **Icon** (inline SVG,
  no emoji; catalog in `lib/icons.ts`).

**Vitest landed** (§F): `npm test` — 32 tests: node-env SSR `render()`
contracts (`format.test.ts` rounding/carry/absent + scene-seconds,
`theme.test.ts` precedence, Chip a11y: word present for every state + toggle
chips are real buttons, Evidence render contract) and one jsdom file
(`ConfirmDialog.test.ts`) for the DOM rules 7.11 depends on — cost shown,
Escape/backdrop cancel, **a pending spend path cannot be cancelled**
(busy guard), focus moves to confirm on open. CI ui-build job runs `npm test`
between check and build. `ui/vitest.config.ts` (`resolve.conditions:
["browser"]` so `mount()` resolves the client build; "svelte/server" is
unaffected).

Dev-only gallery (`PrimitivesGallery`, rendered under `import.meta.env.DEV`
only, never ships — verified tree-shaken out of the prod bundle) with honest
sample values from the mockup's real `standoff_kling` project.

**Review fixes folded in:** Chip's `pressed` variant renders a real `<button>`
(keyboard-activatable, never a span pretending to be a button); SceneThumb
uses the mockup's raw-seconds format (`10.62–15.04` via `formatSeconds`, not
m:ss); ConfirmDialog's Escape/backdrop handlers are inert while `busy`, and
Escape is handled once (a backdrop keydown would double-fire via bubbling —
the jsdom test caught it).

**Second review pass fixed:**

- **StageRow inverts its default** — only `succeeded` earns the ok/"Done"
  chip; failed → blocked/"Failed", running → pending/"Running", and any
  unknown status falls to pending/"Pending" (it used to render everything
  that was not running/failed as a green "Done", the exact failure mode
  §7 exists to prevent). New SSR tests cover "", "queued", "starting".
- **Chip separates toggle from pressed** — `toggle` renders a real button
  regardless of its state (an OFF toggle is the one you click to turn on);
  `pressed` is just its state; `onclick` + data-* passthrough added. jsdom
  tests click on/off toggles and confirm a display chip is inert.
- **Evidence's warn is a note, not a boolean** — `warn` now takes the
  measured comparison ("7.4 LU under target") and renders an alert glyph + the
  word with the warn colour: no way to get the colour without the word (§2).
- **ConfirmDialog ids are document-unique** — the counter moved to `<script
  module>`, so two dialogs mounted together no longer both emit
  `dialog-title-1` (test asserts differing aria-labelledby).
- **Transport timecode matches the mockup** — `formatTimecode` zero-pads
  minutes (`00:11.420`, not `0:11.420`), same class of divergence the
  SceneThumb fix caught one component over.

**Not in this unit:** any screen (7.8+), timelines/waveforms (7.9/7.10), and
the §9 states (loading/empty/error, run lock, draft-vs-committed, director
diff, keyboard map) — those are design work for the units that need them, and
consume these primitives.

---

# D. Screens

## 7.8 Control room, read-only — L — **DONE**

**Depends on:** 7.6, 7.7; 7.5 for waveforms.

Readiness header (percent, checklist rows, budget chip carrying `estimate:
true` and a visible warning when `ledger_writable` is false), scene rail,
timeline rendered but not yet editable, QA evidence, history (event log,
timeline versions, prior mixes), dossier.

QA evidence renders continuity warnings as the two thumbnails compared, side by
side, with confidence — a comparison, never a fault.

**Acceptance:** a real seeded demo project renders completely with no
placeholder or invented values.

**Landed:** `ui/src/lib/control/` — `ControlRoom` (route `/projects/{id}` via
a minimal path router in `App.svelte`; parallel fetches; fatal only when the
project record fails, ancillary 404s become empty states), chrome (title +
meta, Meter, `BudgetChip` with estimate tag and ledger-writable warning),
viewer column = **explicit unfinished video slot** (ViewerPlaceholder says
"video player — unit 7.9"; the scene rail beneath carries real scenes),
rail tabs Readiness (checklist rows + latest run's stages) / Evidence
(loudness with display-scale bars + target markers, warn notes as measured
comparisons; continuity as side-by-side thumbnails with luma figures from
scene stats, delta vs threshold, confidence, "nothing has been corrected") /
History (versions, runs, event log, latest mix's real peaks via 7.5) /
Dossier (provenance table). `Timeline`: ruler (1s ticks, 3s majors), lanes
grouped by kind (Beds/Rhythmic/One-shots), event blocks with **structural**
kind envelopes (discrete decay / rhythmic bursts / sustained bed — seeded by
event index, deterministic; not claimed as measured — per-event peaks need
the 7.10 edit model), read-only inspector (real fields only: event, index,
start, span, generated, kind, intensity, seed), and a resizable frame via a
real `role=separator` grip implemented as a Svelte action
(`timeline-grip.ts`).

**Verified against the live API:** the deployed `standoff_kling` project
serves exactly the shapes consumed — readiness items (ok/warn), qa
(measured −21.4 LUFS vs −14.0 target, visual issue scene_04→scene_05
confidence 0.79, threshold 20), timeline (v1, 15.041667s, 28 events),
scenes with mean_luma — and the computed continuity delta (31.8) matches the
API's own detail string.

Tests: `helpers.test.ts` (11) — lane grouping, honest placement (min block
width), loudness phrasing, continuity figures, event-type tails. `npm test`
54 passing; svelte-check 0/0; prod bundle 84 kB with the dev gallery still
tree-shaken.

**Not in this unit:** the player/preview (7.9), direct manipulation (7.10),
spend paths and run locking (7.11), director mode (7.12), project
creation/upload (7.13), polish incl. full keyboard map (7.14). The §9 states
that 7.8 needed (loading, error, empty) are the minimal honest versions;
loading/empty/error polish is 7.14's.

**Review fixes folded in:**

- **Runs arrive oldest-first** (`ProjectStore.list_runs`); ReadinessPanel now
  takes the run list and shows the most recent stage-bearing run (falling
  back to the latest run overall), and HistoryPanel's "latest mix" is the
  last mix element — both were off-by-one against the ordering. The mix
  waveform is labelled with `project.mix_run_id`, the same run the peaks were
  fetched for.
- **Canvases repaint on theme change** (control-room.md §6): `theme.ts`
  dispatches `ff:theme-change`; WaveformStrip and EnvelopeCanvas listen, so
  the waveform ink updates when the toggle flips.
- **A non-404 ancillary failure is not absence**: `quiet()` sets an
  `ancillaryFailed` flag and the rail shows a warning row ("the empty rows
  below are a failure, not absence") instead of silently rendering empty
  states that look like the data does not exist.
- The grip's `body.resizing` cursor class now has a rule (was set, never
  styled); the timeline's resize action owns it.

**Continuity slot regression fixed (third review round):** the comparison
content was passed as Evidence's default `children`, which renders inside the
3px meter bar — thumbnails and note collided with the source row. It now
renders through the `body` snippet, and the recorded headline uses a new
`mono={false}` statement treatment (UI font, wrapping, row no longer squeezes
the label) instead of the tabular-mono −21.4 LUFS slot. The regression test
asserts structural position (compare inside the body slot, nothing in the
bar), which the substring-based tests could not catch.

## 7.9 A/B player and browser preview — L — **DONE**

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

**Landed:** `Player.svelte` replaces the unfinished video slot when the project
serves `source_url`/`mix_url` (7.4) — one transport, two stacked `<video>`
sources (Original / Foley mix), toggle preserves the playhead (seek on
loadedmetadata when the target isn't ready), scrubber, timecode,
K/Space play-pause + ←/→ seek (window-level, typing contexts skipped), and a
Preview mode: the real stems scheduled over the original video via Web Audio
at their **transient** offsets — discrete = `start + 0.05` (the bias; the
per-clip onset trim is not exposed by the API and the marker says so),
rhythmic/continuous at `start_time`. Labelled "Browser preview — not the
measured mix"; a stem that fails to load is counted, never substituted; QA
numbers untouched (server's measured mix only). The timeline ruler draws teal
hit-point ticks at the transient positions, not `start_time`.

**Two honest enablers the API did not have** (same pattern as the 7.8 review
finding: the mechanism assumed something unreachable):

- **`Event.stem_key`** — the event→audio link, stamped at mix time from the
  run's own inventory (7.1/7.2 provenance pattern; full A.4 round-trip
  through `timeline_io` + analyze; exposed on `TimelineEventOut`). The stamp
  refreshes `stem_key` on every run: a repair moves the cache address (new
  variant), so the repair's mix allocates a new version recording the truth
  (repair lineage + run record now use the stamped version; the repair test
  moved from v3 to v4).
- **`GET /projects/{id}/preview/stems/{key}`** (§3.13, docs/api.md) —
  same-origin streaming of a project-inventory-validated stem. B2's signed
  URLs carry no CORS, so the browser cannot decode them directly; this route
  re-serves the bytes (immutable cache header — the address is
  content-addressed).

**Review fixes folded in:** the timeline GET response was silently dropping
`stem_key` (the A.4 trap at the response-shaping door — would have killed the
preview; now pinned by the repair test's API assertion); Preview is disabled
without a source video (it overlays the original); the stem fetch uses the
raw key (Cloud Run proxies can reject `%2F`, and the key is server-validated);
preview listeners attach once (toggling off/on must not stack duplicate
play handlers); the deployed `run.app` build predates 7.4 (no `source_url`/
`mix_url` on the live project — the frontend is typed against the current
schema and verified by construction + tests, not against the stale deploy).

Tests: `preview.test.ts` (transient offsets, schedule builder). Suite 695
passed / 1 skipped (handoff.md / SPEC.md updated); npm test 62; svelte-check
0/0.
milliseconds, exactly the resolution a director works at.

## 7.10 Direct manipulation — L — **DONE**

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

**Landed:** drag-to-move and edge-handle resize on the timeline blocks
(10 ms snap per the mockup, ⌥-drag overrides; arrow keys nudge 10/100/1 ms), an
inline rename and delete in the inspector — all edits land in a draft
(`ui/src/lib/control/draft.ts` + `seed.ts`/`sha256.ts`), persisted to
localStorage per project and restored on reload. Commit submits the whole
`TimelineCommitRequest` and the server allocates exactly one new version;
version chips in the timeline head switch committed versions read-only (the
fetch lands before the view flips, so content is never shown under the wrong
version's label), and "Edit from here" re-forks the draft from any version —
undo is version switching (decision A.2.1). The draft is diffed against its
base (no-op edits never fork one); a commit that would be refused because the
timeline moved underneath (freezes the latest records that the draft lacks)
offers a merge of the recorded freezes.

**Seed freezing.** The editor writes the frozen mix seed onto any rhythmic
event it touches, at the base position — the value assemble would always have
rendered — so a nudge translates instead of re-rolling the tile texture.
Non-rhythmic events are left untouched (design doc §7: a seed they never use
would be noise). Two parity traps closed: `sha256.ts` ships a self-contained
SHA-256 pinned against FIPS vectors, and `pyToFixed3` reproduces Python's
round-half-even float formatting on the exact dyadic ties (q/16, odd q) where
JS `toFixed` diverges — the whole legacy-seed function is pinned against ~30
hashlib vectors including the tie values. **render.py stamps the seed the mix
actually used** for rhythmic events at mix_freeze (same provenance pattern as
`source_duration_s`/`stem_key`; non-rhythmic events get none — fabricated
provenance is §A.3's rule), so a never-mixed event's first render records its
seed and every later nudge preserves it. One commit, one new version: verified
in the DOM tests (jsdom drag → commit → exactly one `PUT` body) and in
`test_render.py` (remix of a stamped timeline allocates no version).

**Renaming is the one billable-looking edit, and it is honest about it:** free
to commit (it writes only the document), it re-routes the next generation by
the new name — so the UI says so and leaves the actual bill to Regenerate's
existing cost confirmation. On frozen content the commit itself refuses a
rename (`400`, a different generation); the rename control explains that and
points at Regenerate.

**Not in this unit:** director mode (7.12), the full keyboard map and polish
(7.14), and "reroll variation" UI. Intensity/kind edits are not surfaced
(kind changes are free per 7.3 but the mockup's inspector does not show them).

**Second review round (a11y + stability, fixed):**

- **Resize is keyboard-reachable.** The per-event edge handles get the grip's
  machinery via a `resizeHandle` action — role=separator, tabindex,
  aria-valuemin/max/now and a keydown listener (←/→ move the focused edge
  10 ms, Shift 100 ms, Alt 1 ms), with stopPropagation so the block's own
  arrow-nudge does not also translate the event (WCAG 2.1.1 on a primary
  editing operation).
- **Envelopes are stable under renumbering.** `EnvelopeCanvas` used the event
  index as its deterministic seed, so deleting one event redrew every later
  one — read as "those sounds changed". It now seeds from the frozen mix seed
  when present, else a stable name hash: an event's envelope depends on the
  event, never its position.
- **The preview debounces its reschedule.** Dragging while the preview plays
  fired stop+schedule per pointermove (a full BufferSource/GainNode rebuild
  ~60×/sec). The schedule swap is still immediate (cheap, buffer cache
  intact); the audible restart is trailing-debounced to once per gesture.
- **A failed pointer capture cannot strand a gesture** — a window-level
  pointerup fallback closes the drag wherever the release lands.
- **Coverage for the gaps the first review found:** last-page navigation
  states (next_cursor null), the stale-draft merge affordance surviving an
  edit end-to-end (fake-timer poll), explicit discard, keyboard resize, and
  `/ledger` under an unwritable ledger (rows still served, flag false so the
  UI fails closed).

## 7.11 Spend paths and run locking — M — **DONE**

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


**Landed (7.11):** `ui/src/lib/spend/` — regenerate cost probe + confirm_cost_usd echo, Mix/Export ConfirmDialogs, export override, 2s poll + run lock banner, stuck-run on lease expiry. Wired into ControlRoom chrome + Timeline inspector. Contained: no timeline edit.

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

## 7.13 Project creation and upload — M — **DONE**

**Depends on:** 7.6, 7.7.

Projects index (status, readiness, budget), then create: title, budget, file
picker → `POST /projects` → presigned `PUT` with real upload progress →
confirm → kick off analysis. No UI exists for any of this today.


**Landed (7.13):** `ui/src/lib/projects/` — index, create form, presigned PUT with real XHR progress, confirm upload, poll ingest, kick analyze. Routes: `/`, `/projects/new`, `/projects/{id}`.

## 7.14 Polish — M

**Depends on:** everything.

Accessibility pass, keyboard map, empty and error states, the pass that decides
whether it reads as a tool.

## 7.15 Spend tab — S — **DONE**

**Depends on:** 7.7, 7.8.

The money the API already reported and nothing displayed: `spent_estimated` vs
`spent_actual`, the alert thresholds with their `crossed` flags, and the
`by_model` breakdown from `/budget`, plus per-model daily usage and
`ok`/`warn`/`blocked` from `/quota`. No backend work — every figure was already
on the wire and only the chrome chip's headline was being used.

**Fails closed on an unwritable ledger.** `docs/api.md` §3.10: the figures are
then *fiction* rather than imprecise — nothing is recorded, so usage reads zero
however much was spent — and a UI seeing it "must stop presenting the numbers
at all". A warning above readable figures does not satisfy that, so the panel
withholds every number and says why. Either endpoint reporting it poisons both.

Daily quota, not per-minute: `requests_per_day` is the binding constraint
(`gemini-2.5-flash` allows 20/day), which is what matters before a demo.

## 7.16 Ledger rows endpoint — S — **DONE**

**Depends on:** nothing. **Files:** `docs/api.md` first, then `api/app.py`,
`api/schemas.py`, `ledger.py`, tests. Then a section in the Spend tab (7.15).

`/budget` summarises; individual transactions are not exposed. `iter_transactions`
exists in `ledger.py` with no route. Add one returning rows — timestamp,
endpoint, `cost_estimate`, `cost_source`, `success`, `error`, `project_id` —
so spend is **auditable** rather than only totalled. That is the immutable
history principle applied to money: one immutable object per billed call under
`ledger/{ulid}.json` already exists; nothing reads it back.

Scope decisions this unit makes: per-project (`/projects/{id}/ledger`) or
global (`/ledger`) or both; pagination (the ledger grows one object per call,
so a full scan is already what makes `/budget` slow — see 7.17); and whether
failed rows are included by default (they should be — a failed billable call
can still have cost money).

**Acceptance:** every row the summary counted is retrievable and the totals
reconcile with `/budget`.

**Landed:** `GET /projects/{id}/ledger?limit=50&cursor=N` (docs/api.md §3.14) —
per-project, paginated, newest first, failed rows included, with the same
ownership model as `project_spend` (direct tag or rescue override, first-come
by `before`), so the rows reconcile with the summary by construction.
Pagination is index-based on the sorted list (`next_cursor`); the row cache's
10 s TTL bounds the scan per request. `LedgerRowOut`/`LedgerRowsResponse`
carry the `estimate`/`ledger_writable` flags like `/budget` and `/quota`, so a
UI fails closed the same way. 404 before the scan (`_load_state`), 503 on a
storage outage. `iter_attributions` now caches for 10 s (invalidated by
`record_attribution`, the only writer) so the endpoint does not pay a backend
LIST per page — the 7.18 lesson applied to a new read path. Spend tab gains a
"Ledger — individual transactions" table (when/endpoint/cost+source/result,
failed rows as `Failed` chips with their error, est/actual tags per row,
newer/older pagination, "2 of N" count) behind the same fail-closed
`ledger_writable` rule, fetched lazily on the tab's first open. Tests: 6 API
contract tests (rows, pagination, isolation, rescue adoption, reconciliation
with /budget, 404) + 2 SpendPanel render tests.

**Not in this unit:** the archive explorer (7.17), and a global `/ledger` route
(the per-project view is what the Spend tab needs; a bucket-wide one belongs
to the explorer's LIST-metadata pagination).

**Review fixes (8c02541 → ):**

- **Pagination no longer traps the user.** `next_cursor` names the *older*
  page, so the original "at start" derivation inverted both boundaries (page 1
  got a dead "newer" button; the short last page disabled both). "Newer" now
  walks back exactly one page via a cursor stack owned by ControlRoom, and the
  button states come from that stack, not from a page payload.
- **The Spend tab refetches after a run completes** — the reset in
  `refreshAfterRun` re-opens the ledger when the tab is open, so the moment
  new rows exist is no longer the moment the table blanks.
- **Editing re-checks staleness instead of clearing it** — `applyEdit` calls
  `checkDraftStale()`; a stale draft stays stale (and keeps its merge
  affordance) when the director nudges an unrelated event.
- **`_attr_cache` gets the row cache's epoch contract** — `record_attribution`
  bumps an epoch and a reader whose LIST raced the write discards and
  re-reads rather than installing a pre-rescue snapshot (tested by racing the
  writer inside the reader's LIST). The global `conftest` storage fixture now
  invalidates the attribution cache too, closing the same poisoning class the
  unit fixtures already fixed.
- **`LedgerRowsResponse.estimate` dropped** — it was never set by the endpoint
  and never read by the UI; unlike `/quota` and `/budget`, every row names its
  own `cost_source`, so an aggregate "estimates" claim would be false. The
  docs say so instead.
- **Cursor instability documented** — the index cursor shifts when rows land
  at index 0 mid-paging; the bound (10 s row-cache TTL) and the tradeoff are
  stated in §3.14.

## 7.17 Archive explorer — M

**Depends on:** 7.16 for the ledger section; otherwise nothing.
**Files:** `docs/api.md` first, `storage/base.py` + `storage/b2.py` +
`storage/local.py`, `api/app.py`, a new UI route.

A view of the **whole bucket**, not one project: every project, every object,
and the versions of each artifact. The per-project view already exists — that
is the Dossier tab — and this is the archive above it.

**Do not build it on the dossier's mechanism.** The dossier does one backend
`HEAD` per object to get size, which measures **56 s for 131 keys** against real
B2 and gets monotonically worse as the bucket grows. It does not need to:
`list_objects_v2` already returns `Size`, `LastModified` and `ETag` for every
object in the listing, 1000 at a time, and `B2Backend.list_keys` currently
throws all of that away and keeps only the key. So this unit adds a
`list_objects(prefix)` backend method returning that metadata, and the explorer
is one paginated LIST per page rather than N HEADs — cheap where the dossier is
not. `LocalBackend` gets the same method from `os.stat`.

**Versions.** Artifacts are immutable and content-addressed, so S3 object
versions are near-meaningless here — an object is never overwritten. The
versions that matter are FoleyFlow's own, and they are legible from the key
space: `timelines/timeline.v{n}.json`, `runs/{run_id}/…` per run, one manifest
per export, `exports/latest.json` as the pointer. Group by those rather than
asking the store for object versions.

**The provenance caveat is a display requirement, not a footnote.** The invariant
is "cache keys are addresses, not provenance": the ~170 shared stems under
`cache/` belong to no project, and a fallback-served clip is cached at the
*requested* tier's address while `served_endpoint` records who actually produced
it. A raw listing invites reading a `cache/` key as evidence a project used a
given provider. The shared-cache section must say so where it is read, and
per-object provenance must come from the stem inventory, never inferred from
the key.

Signed URLs are handed out per row as the dossier does; the same TTL applies,
so the explorer is browsable but never a durable public handle.

**Acceptance:** the whole bucket is listable without a single HEAD, and the
shared-cache section cannot be read as project provenance.

## 7.18 Backend read latency — M — **DONE**

**Depends on:** nothing. Measured 2026-08-03 against the live bucket; phase-2
offline harness 2026-08-03.

**Landed (wave 1):** Concurrent `get_bytes_many` (bound pool) for `list_events`,
`list_runs`, `list_projects`, ledger rows, and attributions; storage-mode
`ledger_writable` probe cached 15s (fail-closed pin still immediate);
ledger row cache single-flight + 10s TTL with write invalidation **and an
epoch so a mid-flight write cannot reinstall a pre-write snapshot**; waiters
only accept a still-fresh cache (leader failure does not serve stale money);
GET /qa uses `get_project` existence instead of a full event fold; GET /budget
folds state and scans the ledger in parallel (no double fold);
`get_project` parallelises scenes/QA/source-mix HEADs (StorageError → 503);
dossier concurrent HEADs (7.17 LIST-metadata still the full size win).

**Landed (phase-2, no Redis):**
- **A. Write-through exact aggregates** — process-local per-project spend
  (`total` / `estimated` / `actual` / `by_endpoint`) and per-endpoint
  timestamps for quota windows. Seeded once from a full scan; O(1) updates on
  successful `log_transaction`; invalidated on attribution rescue / test rewire.
  Same semantics as a full scan (failed calls counted). Mid-write reseed uses
  entry identity so a concurrent budget poll cannot double-count. Multi-instance:
  no TTL (sharper than the 10s row cache); single-instance deploy is the
  topology that keeps this exact.
- **B. Event-log checkpoint** — `list_events` caches bodies keyed by
  `(process epoch, key count, last key)`; `append_event` bumps epoch and
  clears so same-process folds never miss a just-written event.
- **C. Two shared pools** — `get_io_pool()` (24) for multi-get only;
  `get_route_pool()` (8) for route fan-out that may nest multi-gets (same-pool
  nesting deadlocks under load).
- **Harness:** `uv run python scripts/latency_bench.py` (fixed per-op delay on
  LocalBackend; baseline/after JSON under `scripts/`).

Suite 718. Offline harness @ 50 ms/op, ~60 events, 200 ledger rows (warm after
cold seed on the same process):

| path | baseline warm | phase-2 warm | notes |
|---|---|---|---|
| `GET /projects/{id}` fold | ~304 ms | ~151 ms | list_keys + project + parallel HEADs; no event multi-get |
| `GET /quota` | ~0.2 ms | ~0.1 ms | row cache / aggregates |
| `GET /budget` | ~254 ms | ~101 ms | state checkpoint + O(1) spend |
| `GET /qa` | ~100 ms | ~100 ms | already cheap |

**Live HTTP (deployed `a4f1dc4`, standoff `proj_01KYJD1RKETS3KTX5HA3H5JPDQ`,
2026-08-03)** — `uv run python scripts/latency_live.py --base-url $URL
--project-id proj_01KYJD… --rounds 3`:

| endpoint | pre-fix (2026-08-03) | live first | live median (3 rounds) | speedup (med) |
|---|---:|---:|---:|---:|
| `GET /projects/{id}` | **8.4 s** | 1.99 s | **0.36 s** | **23×** |
| `GET /quota` | **11.1 s** | 0.27 s | **0.27 s** | **41×** |
| `GET /projects/{id}/budget` | **25.1 s** | 1.44 s | **0.45 s** | **55×** |
| `GET /projects/{id}/qa` | 7.2 s | 0.34 s | **0.30 s** | **24×** |
| `GET /projects/{id}/dossier` | 56.0 s | 1.74 s | **1.02 s** | **55×** |
| `GET /projects/{id}/timeline` | 0.7 s | 0.55 s | 0.31 s | 2× |
| `GET /projects/{id}/scenes` | 0.7 s | 0.29 s | 0.29 s | 2× |

First round still pays cold seed (ledger multi-get, event list); rounds 2–3
are warm in-process. Dossier improved via concurrent HEADs; size-from-LIST
remains 7.17. Raw JSON: `scripts/latency_live_results.json`.

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

- The archive explorer (7.17) — scoped 2026-08-03. **7.18 (read latency) is
  DONE; 7.16 (ledger rows) is DONE.** 7.17 must not be built on the dossier's
  HEAD-per-object mechanism; see the unit.
- The states the mockup does not cover — loading, empty, error, the in-flight
  run lock, draft-versus-committed, and the director-mode diff drawn on the
  timeline itself. Listed in `docs/design/control-room.md` §9. These are design
  work, not improvisation from the mockup, and they belong to the units that
  need them (7.8, 7.10, 7.11, 7.12).

**Review follow-up (2026-08-03): aggregates now re-seed on a TTL.** The
process-local quota and project-spend aggregates were seeded once per process
and never re-scanned, so rows written by *another* process were invisible for
the process's lifetime. Probed: with 8 rows in storage the aggregate reported
3 while the scan path reported 8. That matters because the CLI (`foleyflow
analyze`, `foleyflow render`, `scripts/seed_*.sh`) appends to the same bucket
in its own process — quota, the guard that stops a demo blowing the 20/day
Gemini free tier, would read 20 remaining while the provider refused the next
call, and `foleyflow doctor` inherits the same stale view through the API.
Spend had the identical shape, making it a money figure that under-reports.

Both now re-seed from a full scan once they age past `_AGG_TTL_S` (30 s),
bounding staleness to TTL + the row cache's own TTL — the same contract the
row cache already had, instead of "until restart". Write-through keeps them
exact for this process's own rows in between, so warm reads stay
sub-millisecond. A write landing mid-scan bumps the epoch and the scan retries;
under continuous writes an already-seeded aggregate keeps its exact
write-through value rather than spinning. Regression tests cover both, and
were verified to fail without the fix.
