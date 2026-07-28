"""The throwaway dashboard.

One static HTML file, no build step, no framework, no npm. It exists to satisfy
SPEC.md §17's "minimal dashboard shell" and to exercise the API by hand; the
real control room in §6.1 replaces it wholesale, so nothing here is worth
investing in.

Kept in its own router rather than in `app.py` so the API and the disposable UI
stay separable — deleting this file and its one `include_router` line removes
the dashboard entirely.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse

STATIC_DIR = Path(__file__).parent / "static"

router = APIRouter()


@router.get("/", include_in_schema=False, response_class=HTMLResponse)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")
