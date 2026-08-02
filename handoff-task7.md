# Handoff — task 7, the control room

The one piece of FoleyFlow still unbuilt. Written 2026-08-02, after the design
discussion that settled the decisions in §2. `handoff.md` carries the summary;
this file is the whole thing.

Sizes follow `handoff.md`: **XS** one edit · **S** one file or one route ·
**M** spans modules, needs tests · **L** a build in its own right.

---

## 1. What this is

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
  cost. §5 is mostly about not lying.

The old dashboard (`api/static/index.html` and its router in
`api/dashboard.py`) is **deleted outright**, not kept behind a second route.
Its own docstring always described it as disposable.

## 2. Decisions taken

Settled in discussion; not open unless something below proves impossible.

1. **A commit creates one timeline version.** Not one per action. Edits live in
   client-side draft state until the director commits, and each commit writes a
   new immutable timeline object. Undo is version switching, not a mutation
   stack — the versioned-artifacts-in-B2 design pays for itself here.
2. **Resizing an event is free and never regenerates.** It windows the clip
   that already exists. Regeneration is its own explicit button with its own
   cost confirmation. See §3.2 — this is the change with the most reach.
3. **An event's mix seed is frozen into the timeline as data.** See §3.1.
4. **A run in flight locks editing.** The UI shows the project as running and
   refuses edits; a run whose lease expires surfaces as a stuck-run error
   rather than an indefinite spinner.
5. **Stack: Svelte 5 + Vite + TypeScript**, in a `ui/` directory, building to
   `src/foleyflow/api/static/`. Types are generated from the OpenAPI schema
   FastAPI already emits, so contract drift is a build failure rather than a
   demo-time surprise. Mithril was considered — its advantage is running with
   no build step, which stops mattering once the app is large enough to need
   one.
6. **Scope is the full app**, including project creation and upload.

## 3. Timeline data model changes — M

The backbone. The editor is not buildable without these, and they are what make
direct manipulation honest rather than decorative.

### 3.1 `seed` on the event

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

The fix is not a new formula. Add an optional `seed` field to the event:

- **Absent** → computed exactly as today. Every existing timeline and every
  stored manifest still reproduces byte-for-byte, which is why this is the
  chosen route over dropping `start_time` from the hash.
- **Present** → used verbatim. The editor writes the event's current seed down
  the moment it touches it, and from then on the seed travels with the event.

Moving becomes pure translation, the seed becomes a versioned auditable part of
the artifact like everything else, and "reroll the variation" becomes a
deliberate button instead of an accident of dragging.

### 3.2 Split requested duration from placed span

`routing.py` builds the SFX request duration differently per kind
(`routing.py:138-140`):

| kind | requested duration | resizing today |
|---|---|---|
| rhythmic | fixed `RHYTHMIC_LOOP_S` (1.75 s) | already free — changes tiling span only |
| discrete | `event.duration + HEADROOM_S` | **billable** — duration is in the cache key |
| continuous | `event.duration + HEADROOM_S` | **billable** — same reason |

Since resize must be free (decision 2), the field has to split:

- **`source_duration_s`** — what was actually generated. Part of the SFX cache
  key. Changes **only** through the Regenerate button.
- **placement span** (`start_time` + `duration`) — freely editable, windows the
  existing clip, never re-bills.

For discrete events this is a genuinely new renderer capability: today a
discrete clip plays out in full and `duration` does not affect placement at all
(`assemble.py:284`), so "trim a one-shot" does not exist yet.

### 3.3 Writing timelines at all — M

There is **no timeline write path in the API**. `GET /timeline` and
`GET /timeline/versions` are the only routes (`api/app.py:614`, `:639`).
Timelines are produced by analysis and forked by repair; nothing lets a client
say "this event moves 200 ms left".

Needs a new endpoint that takes a client-submitted edit set and allocates
version N+1 beside the original. Immutability is not an obstacle here — it is
the design. Document it in `docs/api.md` first, as every other endpoint was.

## 4. API additions — M

Beyond §3.3:

- **`source_url` and `mix_url` on `GET /projects/{id}`** — the A/B player needs
  a signed URL for the original video and the foley mix. Today the source URL
  exists only inside `/dossier` (`api/app.py:1028`), which HEADs every object in
  the project to compose its listing. Too heavy for first paint. `export_url`
  only exists post-export, so pre-export A/B needs the mix run's `foley.mp4`.
- **A waveform peaks endpoint** — precomputed peak data per stem and per mix. A
  timeline of flat coloured rectangles is the visual signature of generated
  software; waveforms are what make it read as an instrument. Computing peaks
  server-side once beats every client decoding full audio. Shape is open.

## 5. Editor behaviour — L

### 5.1 The playhead must show the transient, not `start_time`

Discrete events are placed at `start_time + DISCRETE_BIAS_S - onset_s`
(`assemble.py:284`) — a deliberate +50 ms late bias, minus the clip's own
trimmed onset so the transient lands where intended. A ruler that labels
`start_time` as the hit point is wrong by tens of milliseconds, which is
exactly the resolution a director works at. Show where the sound actually
lands; keep `start_time` as the underlying value.

### 5.2 Cost geometry must be visible

The SFX cache is keyed on `(endpoint, prompt, duration, variant)`, so:

- Moving an event — **free**.
- Resizing — **free**, after §3.2.
- Renaming — **billable, and possibly re-routed**: the prompt derives from the
  name, and `route_event` keyword-matches the name to choose the endpoint.
- Regenerate — **billable**, with `confirm_cost_usd` echoed back per SPEC §9.4.

Dragging is free and instant; anything that spends money says so before it
does. A UI where some drags silently cost thirty cents is the real slop.

### 5.3 Preview in the browser, render on the server

If every nudge needs a server mix, the edit loop costs money and minutes and
the tool is dead. Stems are already individually addressable with signed URLs,
so preview schedules them at edited offsets via Web Audio against the video:
instant scrubbing, free, no server round trip. Committing and rendering is the
only thing that spends.

The rule is honesty. A browser mix skips `rms_normalize`, ducking, and
true-peak measurement, so it is labelled a preview, and **QA numbers only ever
come from the server's measured mix**. Never present the approximation as the
measurement.

### 5.4 Locking and stuck runs

The API already reports a run whose process died as stale after `RUN_LEASE_S`
and names it in the project's `stale_run_ids` (`docs/api.md` §3.6). The UI locks
editing while a run is in flight, renders live stages as they arrive (`stages`
carries `name`, `status`, `elapsed_s`, `retries`, `provider` and is readable
mid-flight), and turns an expired lease into a real error. There is no SSE —
this polls.

## 6. Director mode — L

Text in, timeline edits out. The discipline that keeps it from being a gimmick:

**Chat proposes, never applies.** The model emits a schema-validated edit set;
the timeline renders it as a diff — ghosted old positions against new — and the
director approves or rejects. That is SPEC §8.4's stated principles (schema
validated AI outputs, explicit human approval for consequential actions) and it
is also better UX: "move all the footsteps 40 ms earlier" is worth reviewing
before it costs anything.

**Model choice is a real constraint.** `gemini-2.5-flash` is capped at **20
requests/day** on the free tier (`providers.toml`) — a chat interface burns
that in one session. `gemini-3.5-flash-lite` allows 500/day and is the
realistic director-mode model, or the paid path. Settle it before it is
discovered mid-demo. Every turn is a provider call: it goes through the ledger
and counts against quota like everything else.

## 7. Screens

1. **Projects index** — status, readiness, budget per project; entry to create.
2. **New project** — title, budget, file picker → `POST /projects` → presigned
   `PUT` with real upload progress → confirm → kick off analysis. No UI today.
3. **Control room** — readiness header (percent, checklist rows keyed
   `ok`/`warn`/`blocked`/`pending`, budget chip carrying `estimate: true` and a
   visible warning when `ledger_writable` is false); A/B player; scene rail;
   the editable timeline; repair with cost confirmation; live stage progress;
   QA evidence; history (event log, timeline versions, prior mixes); dossier;
   export with blockers listed and `override_blocking` behind an explicit
   confirm that records the actor.

**Evidence-first throughout** (SPEC §8.4): show the measured number and its
source, never a synthesised verdict. QA warnings are review prompts, not
automatic corrections — the visual continuity check is an adjacent-shot mean
luma comparison and must never be presented as a detected fault. It renders as
the two thumbnails compared, side by side, with its confidence.

The cleanest way to enforce this is structural rather than remembered: if the
only component that renders a QA number requires its source alongside it, no
screen can accidentally show a verdict.

## 8. Component layer first

The screen's vocabulary repeats hard — state chips in four states, "measured
number + its source" evidence rows, scene thumbnails, stage progress with
elapsed and retries, cost-confirmation dialogs. Define tokens and roughly ten
primitives in `ui/src/lib/` before building screens. This is what separates a
considered UI from an ad-hoc one.

Accessibility is not decoration here: a control room that is all colour-coded
state must not encode meaning in colour alone.

Claude Design (`DesignSync`) was considered and set aside. It syncs an existing
component library to claude.ai as standalone preview HTML; it does not design
anything, and with Svelte components it would mean either hand-authored
duplicate previews that drift or a render step. Revisit only if the library
needs to be shared outside the repo.

## 9. Build and deploy

Multi-stage Dockerfile: a Node stage builds `ui/`, the Python image copies only
the built assets, so no Node ships to Cloud Run. CI gains a UI build job.
`api/dashboard.py` goes from serving one file to serving the built SPA with a
catch-all so client-side routes deep-link.

**Open:** whether to also commit the built assets. Committing means a checkout
without Node still serves a working UI (demo resilience), at the cost of build
output in git and a CI check that it is not stale. Not committing is cleaner
but leaves the UI unavailable to anyone who has not run the build.

## 10. Sequencing

Read-only before write, so the risky money-spending paths land on a UI already
proven against real data.

1. Timeline data model (§3.1, §3.2) with renderer support and tests.
2. Timeline write endpoint (§3.3), documented in `docs/api.md` first.
3. `source_url` / `mix_url`, peaks endpoint (§4).
4. UI skeleton: Vite, generated types, routing, component layer (§8).
5. Control room read-only — readiness, scenes, timeline, QA evidence.
6. A/B player and Web Audio preview (§5.3).
7. Direct manipulation — move, resize, commit, version switching.
8. Interactive spend paths — regenerate, mix, export, with confirmations.
9. Director mode (§6).
10. Upload flow (§7.2).
11. Polish, accessibility pass, keyboard map.

## 11. Tests

Python-side for every new field, the write endpoint, the two project URLs, the
peaks endpoint, and SPA serving. The suite count in `handoff.md` and `SPEC.md`
moves and CI asserts it (`scripts/check_test_count.py`).

Vitest for the UI logic actually worth testing — A/B playhead sync, the
cost-confirmation round trip, draft-to-commit version allocation — rather than
blanket component tests.

## 12. Still open

- Commit the built UI assets, or not (§9).
- Director-mode model choice (§6).
- Peaks endpoint shape (§4).
- Trim semantics per event kind: discrete needs a genuine new renderer path,
  rhythmic and continuous are span changes (§3.2).
