# Demo capture harness

Playwright drives the **real** deployed app and records itself into
`demo/product/{clip_id}.webm` for the product demo film.

This is **not** the task-22 browser gate (`ui/playwright/`). That suite stubs
`api/v1` with fixtures. This harness never does — filming a mock is the
"fake state" the demo plan forbids.

## Money

The live service is on the **paid SFX tier**. Every stem generation and every
director-mode send bills real money.

**This harness must not trigger a billable action.** It never clicks Mix,
Export, Regenerate, or director Send, and never confirms a cost dialog. A
fail-closed safety layer aborts accidental billable POSTs
(`/director`, `/mixes`, `/exports`, `/regenerate`, `/analyze`), blocks
director form submit (including the `⏎` button and Enter), and refuses
clicks whose labels match spend language. Allowed playback labels: Foley
mix, Original, Preview, Play, Pause.

Free by design: navigation, tabs, hover, select, drag, arrow-nudge, A/B
preview, scrubbing, opening Evidence / Spend / Artifacts / Director.

## Setup (once)

```bash
cd scripts/demo
npm install
npm run install-browsers   # chromium only
```

## How to run

From `scripts/demo/`:

```bash
# Dry-run: clip list, no browser
npm run list
# or: node run.mjs --list

# Capture every product clip → demo/product/*.webm
npm run capture
# or: node run.mjs

# Re-shoot one clip (one manifest row = one re-take unit)
node run.mjs --clip 01_room
node run.mjs --clip 05_listen,07_nudge
```

Environment overrides:

| Variable | Default | Meaning |
|---|---|---|
| `DEMO_BASE_URL` | `https://auteur.nryn.dev` | Live origin (no trailing slash) |
| `DEMO_PROJECT_ID` | Desert duel seed | Seeded project for all product clips |
| `DEMO_CLIP` | all | Comma-separated clip ids (same as `--clip`) |
| `DEMO_COLOR_SCHEME` | `dark` | Pinned theme (not OS) |
| `DEMO_HEADED` | unset | Set `1` to watch the browser |
| `--skip-ledger` | off | Skip before/after ledger count check |

Project ids and the clip list live in **one place**: `inventory.json`
(loaded by both `run.mjs` and the Playwright specs).

## Clip inventory (product only)

| id | Project | Free? | On screen |
|---|---|---|---|
| `01_room` | primary | yes | Control room + real timeline |
| `04_director` | primary | yes* | Director tab cost geometry / empty composer |
| `05_listen` | primary | yes | Foley mix play + brief Original A/B |
| `07_nudge` | primary | yes | Drag + Shift+arrow draft nudge (no Commit) |
| `08_spend` | primary | yes | Budget, thresholds, transactions (warm seed; spent may be $0) |
| `09_evidence` | primary | yes | Loudness / true peak / shot-to-shot |
| `10_dossier` | primary | yes | Artifacts inventory with sizes |

Cards (`02_name`, `03_chapter_director`, `06_chapter_cost`, `11_end`) and VO
are produced elsewhere.

### `04_director` — the one that costs if you get it wrong

Director proposals live in **client state**. There is no seeded server-side
"pending proposal" to open. Sending a message bills a model call.

This harness films the **free surface** (Director tab, empty state, costline
copy: model proposes, human decides, moves are free). It does **not** send a
turn and therefore does **not** show ghosted old-vs-new / Apply.

To film ghost → approve later: pre-bake on a warm project in a **separate,
explicit** session (you approve the spend), leave the proposal on screen, and
add a clip that only performs Apply/Discard — or ask before enabling a live
send in the harness. Do not invent a ghost diff.

## recordVideo pins (re-shoot if missed)

- `recordVideo.size` **1920×1080** explicitly (unset → soft 800×800-fitted video)
- Viewport 1920×1080, `colorScheme` pinned, `reducedMotion: "reduce"`
- One **context per clip**; close context before reading `page.video().path()`
- Output is VFR WebM **without audio** — expected; ffmpeg rebuilds CFR 30 and
  product sound comes from the exported mix bed

## Output

```text
demo/product/01_room.webm
demo/product/04_director.webm
…
```

`demo/` is gitignored. Harness source under `scripts/demo/` is committed.

## Acceptance checks after a session

```bash
# Dimensions + duration
for f in demo/product/*.webm; do
  echo "== $f"
  ffprobe -v error -select_streams v:0 \
    -show_entries stream=width,height \
    -show_entries format=duration \
    -of default=nw=1 "$f"
done

# Ledger: run.mjs prints before/after total_count; both must match.
# Unreadable ledger (HTTP/network) fails the session — fail closed, not null===null.
```

Failed takes land under `demo/product/.tmp/{id}.failed.webm` and **do not**
overwrite a previous good `demo/product/{id}.webm`. Size is checked on the
temp file **before** any rename onto the final path.


## What it will not do

- Touch `ui/playwright/` or `ui/playwright.config.ts`
- Run in CI / add workflows
- Route API responses to fixtures
- Click Mix, Export, Regenerate, Confirm cost, or director Send
