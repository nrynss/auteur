"""SPA serving tests (unit 7.6).

The dashboard route previously had zero coverage — deleting the throwaway
`index.html` broke nothing, which also meant there was no regression net. These
tests are that net, on both sides of decision 8 (handoff-task7.md):

- bundle absent → an explanatory "UI not built" page, not a 404;
- bundle present → the SPA at `/`, deep links return the SPA, hashed assets
  are served as files, and unknown `/api/v1/*` paths keep the API's JSON 404
  instead of being swallowed by the catch-all.
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from foleyflow.api import dashboard as dash
from foleyflow.api.app import API_PREFIX, create_app
from foleyflow.api.jobs import JobRunner
from foleyflow.projects import ProjectStore
from foleyflow.storage import LocalBackend

VALID_ID = "proj_01KYJD1RKETS3KTX5HA3H5JPDQ"


@pytest.fixture
def app(tmp_path):
    """The same wiring as `foleyflow.api.main`: API router first, dashboard
    router (SPA catch-all) second, so registration order matches production."""
    store = ProjectStore(LocalBackend(str(tmp_path / "b2")),
                         work_dir=str(tmp_path / "w"))
    runner = JobRunner(store, max_workers=1)
    app = create_app(store, runner)
    app.include_router(dash.router)
    return app


@pytest.fixture
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _make_bundle(root) -> None:
    """A minimal built-SPA fixture: index.html plus hashed assets."""
    static = root / "static"
    static.mkdir(parents=True)
    (static / "index.html").write_text(
        "<!doctype html><html><body>FoleyFlow control room</body></html>"
    )
    assets = static / "assets"
    assets.mkdir()
    (assets / "index-BEEdWDKB.js").write_text("console.log('spa');")
    (assets / "index-DtdrJn58.css").write_text("body{}")


def test_unbuilt_ui_explains_itself(client, tmp_path, monkeypatch):
    """Decision 8: no bundle → a page that says so, not a 404."""
    monkeypatch.setenv("FOLEYFLOW_STATIC_DIR", str(tmp_path / "static"))
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "not built" in r.text


def test_default_brand_on_the_server_surfaces(client, tmp_path, monkeypatch):
    """Defaults are today's values: the fallback page's <title> and <h1> and
    the OpenAPI title all carry FoleyFlow (task 19's acceptance — the brand
    is configurable, and a default deployment looks exactly as before)."""
    monkeypatch.setenv("FOLEYFLOW_STATIC_DIR", str(tmp_path / "static"))
    r = client.get("/")
    assert "<title>FoleyFlow — UI not built</title>" in r.text
    assert "<header><h1>FoleyFlow</h1></header>" in r.text
    assert client.get(f"{API_PREFIX}/openapi.json").json()["info"]["title"] \
        == "FoleyFlow"


def test_configured_brand_reaches_the_unbuilt_page(client, tmp_path, monkeypatch):
    """The fallback page a partner sees when their build failed is the moment
    they least want someone else's name — it reads FOLEYFLOW_BRAND_NAME like
    every other server surface."""
    monkeypatch.setenv("FOLEYFLOW_STATIC_DIR", str(tmp_path / "static"))
    monkeypatch.setattr(dash, "BRAND_NAME", "KinoPost")
    r = client.get("/")
    assert r.status_code == 200
    assert "<title>KinoPost — UI not built</title>" in r.text
    assert "<header><h1>KinoPost</h1></header>" in r.text
    assert "FoleyFlow" not in r.text


def test_configured_brand_reaches_the_openapi_title(tmp_path, monkeypatch):
    """/docs and /api/v1/openapi.json render the FastAPI title — a
    white-label partner's API documentation must not carry the default
    brand. Patches the module attribute, the same contract as GIT_SHA."""
    import foleyflow.api.app as app_mod

    assert create_app().openapi()["info"]["title"] == "FoleyFlow"
    monkeypatch.setattr(app_mod, "BRAND_NAME", "KinoPost")
    assert create_app().openapi()["info"]["title"] == "KinoPost"


def test_unbuilt_ui_explains_itself_on_deep_link(client, tmp_path, monkeypatch):
    monkeypatch.setenv("FOLEYFLOW_STATIC_DIR", str(tmp_path / "static"))
    r = client.get(f"/projects/{VALID_ID}")
    assert r.status_code == 200
    assert "not built" in r.text


def test_unbuilt_ui_leaves_api_intact(client, tmp_path, monkeypatch):
    """The API's own surfaces keep working when the UI is absent."""
    monkeypatch.setenv("FOLEYFLOW_STATIC_DIR", str(tmp_path / "static"))
    assert client.get("/docs").status_code == 200
    r = client.get(f"{API_PREFIX}/openapi.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")


def test_spa_served_at_root(client, tmp_path, monkeypatch):
    _make_bundle(tmp_path)
    monkeypatch.setenv("FOLEYFLOW_STATIC_DIR", str(tmp_path / "static"))
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "FoleyFlow control room" in r.text


def test_deep_link_returns_spa(client, tmp_path, monkeypatch):
    """Client-side routes deep-link: the router returns the SPA, not a 404."""
    _make_bundle(tmp_path)
    monkeypatch.setenv("FOLEYFLOW_STATIC_DIR", str(tmp_path / "static"))
    r = client.get(f"/projects/{VALID_ID}/timeline")
    assert r.status_code == 200
    assert "FoleyFlow control room" in r.text


def test_hashed_assets_served_as_files(client, tmp_path, monkeypatch):
    """/assets/* must be served as real files, never rewritten to the SPA."""
    _make_bundle(tmp_path)
    monkeypatch.setenv("FOLEYFLOW_STATIC_DIR", str(tmp_path / "static"))
    r = client.get("/assets/index-BEEdWDKB.js")
    assert r.status_code == 200
    assert "console.log" in r.text
    assert r.headers["content-type"].startswith("text/javascript")


def test_unknown_api_path_stays_json_not_spa(client, tmp_path, monkeypatch):
    """The catch-all must not swallow unknown API traffic."""
    _make_bundle(tmp_path)
    monkeypatch.setenv("FOLEYFLOW_STATIC_DIR", str(tmp_path / "static"))
    r = client.get("/api/v1/no-such-route")
    assert r.status_code == 404
    assert not r.headers["content-type"].startswith("text/html")


def test_known_api_route_unaffected_by_catch_all(client, tmp_path, monkeypatch):
    _make_bundle(tmp_path)
    monkeypatch.setenv("FOLEYFLOW_STATIC_DIR", str(tmp_path / "static"))
    r = client.get(f"{API_PREFIX}/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_spa_fallback_rejects_traversal(tmp_path, monkeypatch):
    """A crafted path must never resolve to a file outside the static dir."""
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<html>spa</html>")
    secret = tmp_path / "secret.txt"
    secret.write_text("outside the static dir")
    monkeypatch.setenv("FOLEYFLOW_STATIC_DIR", str(root))

    with pytest.raises(HTTPException) as exc:
        dash.spa_fallback("../../secret.txt")
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        dash.spa_fallback("assets/../../secret.txt")
    assert exc.value.status_code == 404

    # api/ paths are refused outright, before any file lookup.
    with pytest.raises(HTTPException) as exc:
        dash.spa_fallback("api/v1/whatever")
    assert exc.value.status_code == 404


def test_health_probe_when_unbuilt(client, tmp_path, monkeypatch):
    """Platform liveness (/health) predates the UI and is unaffected by it."""
    monkeypatch.setenv("FOLEYFLOW_STATIC_DIR", str(tmp_path / "static"))
    r = client.get(f"{API_PREFIX}/health")
    assert r.status_code == 200


# -- cache and security headers ---------------------------------------------


def test_index_is_not_cached_and_nosniff(client, tmp_path, monkeypatch):
    """RFC 9111: index.html must be revalidated (no-cache), or heuristic
    freshness can outlive a deploy and reference hashed asset filenames that
    no longer exist. Applies to the root and every SPA deep link."""
    _make_bundle(tmp_path)
    monkeypatch.setenv("FOLEYFLOW_STATIC_DIR", str(tmp_path / "static"))
    for path in ("/", f"/projects/{VALID_ID}/timeline"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"
        assert r.headers["x-content-type-options"] == "nosniff"


def test_assets_cached_immutably_and_nosniff(client, tmp_path, monkeypatch):
    """Content-hashed /assets/* are immutable by construction — the one thing
    safe to cache forever, and previously the one thing that was not cached."""
    _make_bundle(tmp_path)
    monkeypatch.setenv("FOLEYFLOW_STATIC_DIR", str(tmp_path / "static"))
    for path in ("/assets/index-BEEdWDKB.js", "/assets/index-DtdrJn58.css"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert r.headers["x-content-type-options"] == "nosniff"


def test_unhashed_public_files_are_not_cached_immutably(
        client, tmp_path, monkeypatch):
    """Only `assets/` is content-hashed. Vite copies `ui/public/` verbatim, so
    those files keep their names across builds — an immutable year-long cache
    would pin a stale robots.txt or favicon with no way to invalidate it.
    `ui/public/` does not exist yet; this is the net for when it does."""
    _make_bundle(tmp_path)
    static = tmp_path / "static"
    (static / "robots.txt").write_text("User-agent: *\nDisallow:\n")
    (static / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    monkeypatch.setenv("FOLEYFLOW_STATIC_DIR", str(static))

    for path in ("/robots.txt", "/favicon.ico"):
        r = client.get(path)
        assert r.status_code == 200, path
        # Served as a real file (not swallowed by the SPA catch-all)...
        assert "immutable" not in r.headers["cache-control"], path
        assert r.headers["cache-control"] == "no-cache", path
        assert r.headers["x-content-type-options"] == "nosniff", path

    # ...while the hashed subtree keeps its year.
    hashed = client.get("/assets/index-BEEdWDKB.js")
    assert hashed.headers["cache-control"] == (
        "public, max-age=31536000, immutable")


def test_unbuilt_page_is_not_cached(client, tmp_path, monkeypatch):
    """The unbuilt page is ephemeral — once the bundle exists it must not
    linger in a cache."""
    monkeypatch.setenv("FOLEYFLOW_STATIC_DIR", str(tmp_path / "static"))
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["x-content-type-options"] == "nosniff"


# -- guard edges -------------------------------------------------------------


def test_bare_api_path_stays_json_not_spa(client, tmp_path, monkeypatch):
    """The one hole in the api/ guard: bare /api fell through to the SPA."""
    _make_bundle(tmp_path)
    monkeypatch.setenv("FOLEYFLOW_STATIC_DIR", str(tmp_path / "static"))
    r = client.get("/api")
    assert r.status_code == 404
    assert not r.headers["content-type"].startswith("text/html")


def test_head_root_supported(client, tmp_path, monkeypatch):
    """HEAD-based uptime monitors probe / with HEAD; it must not 405."""
    _make_bundle(tmp_path)
    monkeypatch.setenv("FOLEYFLOW_STATIC_DIR", str(tmp_path / "static"))
    r = client.head("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-cache"
    assert r.content == b""  # HEAD never carries a body


def test_head_deep_link_supported(client, tmp_path, monkeypatch):
    _make_bundle(tmp_path)
    monkeypatch.setenv("FOLEYFLOW_STATIC_DIR", str(tmp_path / "static"))
    r = client.head(f"/projects/{VALID_ID}/timeline")
    assert r.status_code == 200


def test_head_root_when_unbuilt(client, tmp_path, monkeypatch):
    monkeypatch.setenv("FOLEYFLOW_STATIC_DIR", str(tmp_path / "static"))
    r = client.head("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-cache"
    assert r.content == b""
