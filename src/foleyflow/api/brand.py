"""Server-side brand surface (handoff.md task 19).

The UI rebrands at build time via `VITE_BRAND_*` (ui/src/lib/brand.ts). Two
server surfaces also render the product name, and neither is behind config
unless this module says so:

- the OpenAPI title — `create_app()` names the FastAPI app, served at
  `/api/v1/openapi.json` (`info.title`) and rendered by `/docs` and `/redoc`;
- the "UI not built" fallback page (`api/dashboard.py`) — the page a
  self-hosting partner sees when their build step failed, which is exactly
  when they least want to see someone else's name.

Both read `FOLEYFLOW_BRAND_NAME` (default "FoleyFlow", today's value — a
default deployment renders byte-identical to before). A rebranding deployment
sets the runtime env var here AND the build-time `VITE_BRAND_NAME` for the UI.

Read at import time as a module constant — the same contract as `GIT_SHA` /
`BUILD_TIME` in app.py: the FastAPI title is fixed when `create_app()` runs,
so a lazy reader would be a seam nothing else in the codebase has. Tests that
need an override monkeypatch the module attribute (see test_dashboard.py).
"""

import os

BRAND_NAME = os.environ.get("FOLEYFLOW_BRAND_NAME") or "FoleyFlow"
