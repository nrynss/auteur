# FoleyFlow control room — `ui/`

Svelte 5 + Vite + TypeScript SPA. Builds into the Python package's static
directory (`src/foleyflow/api/static/`), which `api/dashboard.py` serves with
an SPA catch-all. **Built assets are never committed** (handoff-task7.md,
decision 8) — they exist in the Docker image, or on disk after a local build.

## Development

The API and the UI build to the same tree, so the flow is:

```sh
# 1. Run the API (terminal 1)
uv run uvicorn foleyflow.api.main:app --reload

# 2. Generate the OpenAPI schema + TS types, then run Vite (terminal 2)
uv run python scripts/emit_openapi.py --out ui/openapi.json
npm install
npm run dev        # http://localhost:5173 — /api and /docs are proxied to
                   # the API on :8000, so the SPA hits the real backend.
```

`npm run typegen` regenerates `src/lib/api/schema.d.ts` from `ui/openapi.json`
via `openapi-typescript`. `ui/openapi.json` and `schema.d.ts` are gitignored:
the schema is a build input generated from the FastAPI app itself
(`scripts/emit_openapi.py`), so contract drift fails the UI build instead of
surfacing at demo time.

## Checks

```sh
npm run check   # svelte-check (type-check)
npm test        # vitest — pure logic, SSR contracts, DOM interaction (jsdom)
npm run build   # typegen + svelte-check + vite build → src/foleyflow/api/static/
npm run build:fast  # typegen + vite build, no type-check (iteration only)
```

The Docker build runs `npm ci && npm run build` in a Node stage and copies
only the output into the Python image, so no Node ships to Cloud Run.

## Branding (task 19)

The whole rendered brand surface is build-time configuration — a deployment
can rebrand without a patch:

| Variable | Default | What it sets |
|---|---|---|
| `VITE_BRAND_NAME` | `FoleyFlow` | product name: chrome wordmarks, page titles, the mount-error message |
| `VITE_BRAND_TITLE_TEMPLATE` | `{name} — {page}` | page-title shape; `{name}` and `{page}` are the placeholders |
| `VITE_BRAND_FAVICON` | inline SVG waveform mark | any href — a data URI or a path under `/public` (copied into the build verbatim) |

```sh
VITE_BRAND_NAME="KinoPost" \
VITE_BRAND_TITLE_TEMPLATE="{name} · {page}" \
VITE_BRAND_FAVICON="data:image/svg+xml,…" \
npm run build
```

Only what a user sees is externalised. The CLI, the package, `FOLEYFLOW_*` env
vars, the storage key space and the `foleyflow-theme` localStorage key stay
brand-neutral. Colours need nothing: `src/lib/theme.css` tokens are consumed
by components, never raw values (unit 7.7). The static `<title>` in
`index.html` is injected at build time from the same template the SPA uses, so
the pre-hydration title and the runtime titles cannot drift apart. CI builds
once with defaults and once rebranded, and greps the bundles (task 19's
acceptance).

## Versions

Svelte 5, Vite 8, TypeScript 5 (svelte-check and openapi-typescript pin TS
`^5`; TS 7 is not supported by the toolchain yet).
