"""Serve the built control-room SPA (unit 7.6).

`ui/` (Svelte 5 + Vite) builds into this package's `static/` directory. This
router serves the result with an SPA catch-all so client-side routes
deep-link, and explains itself when the bundle is absent — a developer running
the API without running the UI build gets a page that says so, not a 404, and
not a stale bundle pretending to be current (handoff-task7.md, decision 8).

Built assets are never committed; they exist in the deployed image because the
Dockerfile's Node stage runs the build and the Python stage copies only its
output. `FOLEYFLOW_STATIC_DIR` overrides the static directory so tests can
point the router at a fixture bundle without writing into the source tree.

The catch-all must never swallow API traffic: unknown `/api/v1/*` paths stay
the API's JSON 404, and files are served only when they actually exist in the
static directory (with a path-traversal guard on the file case).

Cache headers are deliberate on both sides of the bundle (RFC 9111):
`index.html` is `no-cache` so a deploy is visible immediately — heuristic
freshness could outlive a deploy and reference hashed asset filenames that no
longer exist — while content-hashed `/assets/*` are immutable by construction
and safe to cache for a year. Immutability is scoped to that one subtree, not
to every real file: anything Vite copies verbatim from `ui/public/` keeps its
name across builds, so it revalidates like the index. Everything carries
`X-Content-Type-Options: nosniff`.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

STATIC_DIR = Path(__file__).parent / "static"

router = APIRouter()

# Cache headers. no-cache means "revalidate before reuse", not "don't store" —
# the browser still caches index.html, it just checks with the server on every
# navigation, which is exactly what a deploy needs.
_REVALIDATE_CACHE_CONTROL = "no-cache"
_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
_SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff"}

# Only this subtree is content-hashed. Vite fingerprints everything it emits
# into `assets/`, which is what makes a year-long immutable cache safe there.
# Files copied verbatim from `ui/public/` (robots.txt, favicon.ico, a web
# manifest) land beside index.html under their own stable names — marking those
# immutable would pin a stale copy in every cache for a year with no way to
# invalidate it. `ui/public/` does not exist today; this keeps adding one from
# being a silent trap.
ASSETS_SUBDIR = "assets"

_UNBUILT_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FoleyFlow — UI not built</title>
<style>
  body { margin:0; font:14px/1.5 ui-sans-serif,system-ui,sans-serif;
         background:#0E1113; color:#E8EDEE; }
  header { padding:.75rem 1rem; border-bottom:1px solid #2A3033; }
  main { max-width:640px; margin:0 auto; padding:2rem 1rem; }
  h1 { font-size:1rem; margin:0; }
  h2 { font-size:1.1rem; margin:1.5rem 0 .5rem; }
  p { color:#8C989C; }
  code, pre { font-family:ui-monospace,Menlo,Consolas,monospace; }
  pre { background:#1F2427; border:1px solid #2A3033; border-radius:6px;
        padding:.75rem; overflow-x:auto; color:#E8EDEE; }
  a { color:#4FB3A6; }
</style>
</head>
<body>
<header><h1>FoleyFlow</h1></header>
<main>
<h2>The control-room UI is not built</h2>
<p>This API is running without its frontend bundle. That is expected when
serving from a source checkout that has not run the UI build, and it is
deliberate: built assets are never committed, so what you see is never a
stale bundle pretending to be current.</p>
<p>Build it locally (the TS types come from the API's own OpenAPI schema):</p>
<pre>uv run python scripts/emit_openapi.py --out ui/openapi.json
cd ui &amp;&amp; npm install &amp;&amp; npm run build</pre>
<p>or build the Docker image, whose Node stage runs the build for you:</p>
<pre>docker build --build-arg FOLEYFLOW_GIT_SHA="$(git rev-parse HEAD)" -t foleyflow .</pre>
<p>The API itself is unaffected — <a href="/docs">/docs</a> and
<a href="/api/v1/openapi.json">/api/v1/openapi.json</a> still work.</p>
</main>
</body>
</html>
"""


def static_dir() -> Path:
    return Path(os.environ.get("FOLEYFLOW_STATIC_DIR", STATIC_DIR))


def _index_path() -> Path:
    return static_dir() / "index.html"


def _revalidate_headers() -> dict:
    return {"Cache-Control": _REVALIDATE_CACHE_CONTROL, **_SECURITY_HEADERS}


def _asset_headers() -> dict:
    return {"Cache-Control": _ASSET_CACHE_CONTROL, **_SECURITY_HEADERS}


def _file_headers(relative: Path) -> dict:
    """Immutable only for the content-hashed subtree; everything else
    revalidates. `relative` is already proven to sit inside the static dir."""
    if relative.parts and relative.parts[0] == ASSETS_SUBDIR:
        return _asset_headers()
    return _revalidate_headers()


def _serve_index() -> Response:
    return FileResponse(_index_path(), media_type="text/html",
                        headers=_revalidate_headers())


def _unbuilt_page() -> Response:
    return HTMLResponse(_UNBUILT_PAGE, status_code=200,
                        headers=_revalidate_headers())


# Both routes answer HEAD as well as GET: HEAD-based uptime monitors, CDNs and
# load balancers probe the root with HEAD, and Starlette does not auto-register
# HEAD for GET routes.
@router.api_route("/", methods=["GET", "HEAD"], include_in_schema=False,
                  response_class=HTMLResponse)
def dashboard() -> Response:
    if _index_path().is_file():
        return _serve_index()
    return _unbuilt_page()


@router.api_route("/{path:path}", methods=["GET", "HEAD"],
                   include_in_schema=False, response_class=HTMLResponse)
def spa_fallback(path: str) -> Response:
    # Unknown API paths keep the API's own 404 (JSON error envelope), never
    # the SPA. The API router is registered before this one in main.py, so
    # known routes never reach here; only unmatched ones do. Bare /api is
    # caught too — it is the one hole in a plain prefix check.
    if path == "api" or path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    if path in ("docs", "redoc", "openapi.json"):
        raise HTTPException(status_code=404, detail="Not found")

    root = static_dir()

    # Real files (hashed /assets/*) are served as-is; anything else is a
    # client-side route and gets the SPA. The traversal guard keeps a crafted
    # path from reaching a file outside the static directory.
    if path:
        candidate = (root / path).resolve()
        try:
            relative = candidate.relative_to(root.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="Not found")
        if candidate.is_file():
            return FileResponse(candidate, headers=_file_headers(relative))

    if _index_path().is_file():
        return _serve_index()
    return _unbuilt_page()
