# FoleyFlow

> An AI post-production copilot that turns a rough video into an auditable, production-ready deliverable.

Built for the **Backblaze Generative AI Media Hackathon**. See [SPEC.md](SPEC.md) for the full build specification.

FoleyFlow analyzes a video, generates and mixes synchronized Foley, runs technical
post-production checks, and preserves the full production record in Backblaze B2.

## Status

Milestone 1 in progress. The media core (`src/foley`) is ported from a prior working
Foley pipeline; the FoleyFlow layer (`src/foleyflow`) adds B2-backed storage, projects,
QA, and provenance.

| Layer | Package | State |
|---|---|---|
| Media core — VLM analysis, routing, SFX, mix, mux | `src/foley` | Ported, tests green |
| Storage — content-addressed cache, B2 artifacts | `src/foleyflow/storage` | In progress |
| Projects, runs, versions | `src/foleyflow` | Not started |
| QA — loudness, true peak, visual continuity | `src/foleyflow` | Not started |
| Web API + control room UI | `src/foleyflow` | Not started |

## Prerequisites

- Python 3.14+ and [uv](https://docs.astral.sh/uv/)
- `ffmpeg` and `ffprobe` on `PATH`
- A [fal.ai](https://fal.ai) API key
- A Backblaze B2 bucket and an application key scoped to it

## Setup

```bash
uv sync
```

```bash
cp .env.example .env
```

Fill in `FAL_KEY` and the `B2_*` values, then run the test suite:

```bash
uv run pytest -q
```

The suite is fully offline — no API calls, no spend.

## Media core CLI

The ported pipeline is usable standalone while the web layer is built.

Analyze and render a clip (makes paid fal calls):

```bash
uv run foley render path/to/clip.mp4
```

Re-render deterministically from a saved timeline (no analysis call, $0):

```bash
uv run foley render path/to/clip.mp4 --from-timeline samples/timelines/clip_1.json
```

Same timeline in, byte-identical mix out — mix randomness is seeded from a
sha256 of each event's name and start time, never Python's per-process `hash()`.

## Providers

| Role | Provider / model |
|---|---|
| Video understanding → event timeline | `google/gemini-2.5-flash` via fal's OpenRouter gateway |
| Ambience and default SFX | `cassetteai/sound-effects-generator` |
| Hero discrete Foley | `fal-ai/elevenlabs/sound-effects/v2` |
| Impacts | `fal-ai/mmaudio-v2/text-to-audio` |

Routing is data-driven in [routing.yaml](routing.yaml) — adding a category or
keyword never requires touching code.

## Attribution

The `src/foley` media core originates from a prior fal.ai hackathon project by the
same author, ported here with its test suite. Everything under `src/foleyflow` —
B2 storage, projects, QA, provenance, and the control-room UI — is new work for
this submission.
