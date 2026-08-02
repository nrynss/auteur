# Control room — visual direction

**Locked 2026-08-02.** Unit 7.7 implements this; units 7.8 onward consume it.
The reference implementation is `control-room-mockup.html` beside this file —
open it in a browser before writing any component. It is a static mockup built
on the real `standoff_kling` project (its own scene frames, timeline, stem
inventory and QA report), so what it shows is what the API actually serves.

Where this document and the mockup disagree, this document wins: the mockup is
one screen in one state, and it makes compromises this document does not.

---

## 1. The brief in one line

A director's tool, not a status page. It must not look generated, and it must
not lie about precision.

**Not generated** means: no colour-coded cards in a grid, no flat rectangles
standing in for audio, no emoji as icons, no verdict where a measurement
belongs. Density, real typography, monospace numerics, keyboard access and
honest waveforms are the difference.

**Not lying** means every number on screen is attached to something real — a
transient position, a cache address, a dollar cost — and is absent when there
is nothing real behind it. See §7; that section is the one with teeth.

## 2. Colour

Two reasons converge on the same palette, and the domain one is the stronger:

- **Colour-grading suites use neutral, low-chroma surrounds** so the picture's
  own colour is not misjudged by what sits around it. A Foley tool sits next to
  video and inherits that constraint.
- The colour-emotion literature finds **brightness and saturation drive arousal
  far more than hue**, with blue-green hues rating most pleasant (Valdez &
  Mehrabian, 1994 is the usual citation).

So: near-neutral grounds with a faint blue-green bias — never a pure grey, which
reads as unconsidered — and a single desaturated teal accent.

| token | light | dark | use |
|---|---|---|---|
| `--ground` | `#EFF1F1` | `#0E1113` | app background |
| `--surface` | `#FFFFFF` | `#171B1E` | panels, chrome, rails |
| `--raised` | `#F7F8F8` | `#1F2427` | controls, inset cards |
| `--sunken` | `#E4E7E7` | `#0A0D0F` | wells, the video letterbox |
| `--line` | `#D6DBDB` | `#2A3033` | borders |
| `--line-soft` | `#E3E7E7` | `#222729` | internal dividers |
| `--text` | `#121618` | `#E8EDEE` | primary |
| `--dim` | `#5E6A6E` | `#8C989C` | secondary |
| `--faint` | `#8A9599` | `#6B7679` | metadata, tick labels |
| `--accent` | `#2E7D74` | `#4FB3A6` | selection, active, primary action |
| `--accent-q` | `#D7E8E5` | `#1B3330` | accent fills |
| `--ok` | `#3F7D53` | `#5FB37A` | semantic only |
| `--warn` | `#9A6B14` | `#D9A441` | semantic only |
| `--stop` | `#B4453C` | `#E0655C` | semantic only |
| `--wave` | `#7FA9A3` | `#3E6B67` | waveform ink |

**Rules.**

- Semantic colours are **not** the accent and never decorate. A thing is teal
  because it is selected or active, never because it is good.
- **Colour never carries meaning alone.** Every state pairs a colour with a
  glyph and a word. A control room that is all colour-coded state is unusable
  otherwise, and this is an accessibility requirement, not a preference.
- Both themes get equal care. Tokens are defined on `:root`, redefined under
  `@media (prefers-color-scheme: dark)`, and redefined again under
  `:root[data-theme="dark"]` / `[data-theme="light"]` so an explicit toggle wins
  in both directions. Style through tokens; never write colours inside the media
  query.

## 3. Typography

- **UI:** `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI",
  system-ui, sans-serif`. Deliberate — it renders as SF on Apple hardware, and
  a webfont would risk a silent fallback for no gain.
- **Numerics:** `ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas,
  monospace` with `font-variant-numeric: tabular-nums`. **Every** timecode,
  measurement, duration, cost and cache key. Digits must not jitter as they
  change.
- Base 13px — tool density, not document density. Scale: 10 (uppercase labels,
  `letter-spacing: .05–.07em`), 11.5 (metadata), 12.5–13 (UI text), 14 (project
  title). Weights 500 / 560 / 590; avoid 700 outside tiny glyphs.

## 4. Form

Radii 5 / 7 / 10px, applied consistently by role (controls / panels /
containers); pills at 999. One shadow, reserved for the video frame. Separation
by hairline, not by nested boxes. No gradients.

## 5. Layout

The composition editors actually use — **viewer top-left, inspector right,
timeline full-width along the bottom.** Not a dashboard.

```
┌──────────────────────────────────────────────────────────┐
│ chrome  46px — title · readiness · budget · theme · act  │
├───────────────────────────────┬──────────────────────────┤
│ 16:9 preview (flex)           │ rail 344px               │
│ transport                     │  Readiness │ Evidence │  │
│ scene rail (thin strip)       │  Director                │
├───────────────────────────────┴──────────────────────────┤
│ ⇕ timeline — resizable, min 148px                        │
│   header · ruler · lanes (scroll) · inspector            │
└──────────────────────────────────────────────────────────┘
```

- **Scene rail is a thin strip inside the viewer column**, under the transport —
  attached to the picture it describes, and it leaves the rail full height.
- **The timeline is resizable** by dragging its top edge. The grip is a real
  `role="separator"`, focusable, arrow-key operable (Shift for coarse). Lanes
  scroll internally; header, ruler and inspector stay pinned.
- Below 1000px the rail moves under the viewer and the app stops being
  viewport-locked.

## 6. Waveforms

Canvas, not SVG paths, and repainted on theme change and on every resize.
Discrete one-shots get a **transient-led decay** envelope; beds and rhythmic
events get a **sustained** one. This is the single biggest signal that the
timeline is an instrument rather than a chart — flat coloured rectangles are
the visual signature of generated software.

Real peak data comes from unit 7.5; the mockup synthesises it deterministically.

## 7. Honesty, enforced structurally

Do not implement these as conventions to remember. Build them so the wrong
thing is hard to express.

- **A QA number cannot render without its source.** If the only component that
  displays a measurement requires the artifact it was measured from, no screen
  can accidentally show a verdict. `−21.4 LUFS` always arrives with
  `measured from runs/run_01KZ0JYN…/mix.wav`.
- **The transport shows the transient, not `start_time`.** Discrete events are
  placed at `start_time + DISCRETE_BIAS_S − onset_s`; labelling `start_time` as
  the hit point is wrong by tens of milliseconds, which is the resolution a
  director works at.
- **Fields appear only when they mean something.** Only rhythmic events use a
  mix seed, so the seed row and "reroll variation" exist only for rhythmic
  events. The mockup's timeline has none, so neither appears.
- **Cost geometry is on screen.** Move and resize are free and say so;
  Regenerate carries its price; a director-mode proposal is stamped with its
  cost before it is approved.
- **Continuity is a comparison, never a fault.** Two thumbnails side by side,
  the luma figures, the threshold, the confidence, and a sentence saying nothing
  has been corrected.
- **Events are addressed by index.** Names repeat — `standoff_kling` has two
  events called "leather holster creaking". Anything that shows or acts on an
  event shows its index.

## 8. Copy

Active voice, no apologies. A control says what happens; a state says what is
true. Prefer the measured phrase to the judgement: "7.4 LU under target", not
"loudness problem". Errors state what went wrong and what to do.

## 9. What the mockup does not cover

It is one screen in one state. Still to design, and **not** to be improvised
from it:

- Loading, empty, and error states for every panel.
- The in-flight run lock — stages arriving live, and the stale-lease error.
- The projects index and the create/upload flow (unit 7.13).
- Draft-versus-committed treatment: how a modified draft reads against the
  committed version, and how version switching presents.
- The director-mode diff on the timeline itself — ghosted old positions against
  new. The mockup shows the proposal only as a list.
- Focus order and the full keyboard map.
