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
npm run build   # typegen + vite build → src/foleyflow/api/static/
```

The Docker build runs `npm ci && npm run build` in a Node stage and copies
only the output into the Python image, so no Node ships to Cloud Run.

## Versions

Svelte 5, Vite 8, TypeScript 5 (svelte-check and openapi-typescript pin TS
`^5`; TS 7 is not supported by the toolchain yet).
