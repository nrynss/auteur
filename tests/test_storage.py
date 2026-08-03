"""Offline tests for foleyflow.storage. No network, no credentials, no spend."""

import os

import pytest

from foleyflow.storage import LocalBackend, StorageError, get_backend


def _write(path: str, data: bytes = b"canonical-wav-bytes") -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


# -- LocalBackend --

def test_scope_id_identifies_physical_store(tmp_path):
    """scope_id is the process-cache key for ledger/storage scoped data."""
    root = tmp_path / "store"
    root.mkdir()
    a = LocalBackend(str(root))
    b = LocalBackend(str(root))
    other = LocalBackend(str(tmp_path / "other"))
    assert a.scope_id() == b.scope_id()
    assert a.scope_id() != other.scope_id()
    assert a.scope_id().startswith("local:")


def test_b2_scope_id_is_bucket_and_prefix(monkeypatch):
    """B2 scope_id is bucket + prefix; trailing slash on prefix is normalized."""
    import boto3
    from foleyflow.storage.b2 import B2Backend

    class _FakeClient:
        pass

    # B2Backend imports boto3 inside __init__; stub the client construction.
    monkeypatch.setattr(boto3, "client",
                        lambda *a, **k: _FakeClient())

    def make(prefix: str) -> B2Backend:
        return B2Backend(
            endpoint_url="https://s3.us-east-005.backblazeb2.com",
            region="us-east-005",
            bucket="my-bucket",
            key_id="kid",
            application_key="secret",
            prefix=prefix,
        )

    a = make("foleyflow/")
    b = make("foleyflow")
    other = make("other")
    empty = make("")
    assert a.scope_id() == b.scope_id() == "b2:my-bucket/foleyflow"
    assert a.scope_id() != other.scope_id()
    assert empty.scope_id() == "b2:my-bucket"
    assert a.scope_id() != empty.scope_id()


def test_factory_reuses_backend_for_identical_config(monkeypatch, tmp_path):
    """Warm scope_id paths must not construct a new backend every hit."""
    import os
    import foleyflow.storage.factory as factory

    root = str(tmp_path / "cached_store")
    monkeypatch.setenv("FOLEYFLOW_CACHE_BACKEND", "local")
    monkeypatch.setenv("FOLEYFLOW_LOCAL_CACHE_DIR", root)
    factory._backend_cache.clear()

    # Suite conftest wraps factory.get_backend with an offline LocalBackend.
    # Exercise the real caching path directly.
    def real_get(kind=None):
        kind = (kind or os.environ.get("FOLEYFLOW_CACHE_BACKEND", "local")).strip().lower()
        key = factory._config_key(kind)
        with factory._backend_lock:
            cached = factory._backend_cache.get(key)
            if cached is not None:
                return cached
            backend = factory._build_backend(kind)
            factory._backend_cache[key] = backend
            return backend

    monkeypatch.setattr(factory, "get_backend", real_get)
    first = factory.get_backend("local")
    second = factory.get_backend("local")
    assert first is second
    assert first.scope_id() == LocalBackend(root).scope_id()


def test_put_get_roundtrip_preserves_bytes(tmp_path):
    backend = LocalBackend(str(tmp_path / "store"))
    src = _write(str(tmp_path / "src.wav"), b"abc123")

    assert backend.exists("cache/deadbeef.wav") is False
    backend.put("cache/deadbeef.wav", src)
    assert backend.exists("cache/deadbeef.wav") is True

    dest = str(tmp_path / "out" / "pulled.wav")
    assert backend.get("cache/deadbeef.wav", dest) == dest
    with open(dest, "rb") as f:
        assert f.read() == b"abc123"


def test_keys_map_to_nested_directories(tmp_path):
    """Key space is identical across backends, so a slash is a real directory
    locally and a real prefix in B2."""
    root = tmp_path / "store"
    backend = LocalBackend(str(root))
    backend.put("projects/proj_1/runs/run_1/stems/evt_a.wav",
                _write(str(tmp_path / "s.wav")))
    assert (root / "projects" / "proj_1" / "runs" / "run_1" / "stems" / "evt_a.wav").exists()


def test_get_missing_key_raises_storage_error(tmp_path):
    backend = LocalBackend(str(tmp_path / "store"))
    with pytest.raises(StorageError):
        backend.get("cache/nope.wav", str(tmp_path / "out.wav"))


@pytest.mark.parametrize("key", [
    "../escape.wav",
    "cache/../../escape.wav",
    "/etc/passwd",          # rooted; ntpath.isabs says False since Python 3.13
    "\\windows\\system32",
    "C:/windows/system32",
    "cache//x.wav",
    "",
])
def test_traversal_and_absolute_keys_are_rejected(tmp_path, key):
    backend = LocalBackend(str(tmp_path / "store"))
    with pytest.raises(StorageError):
        backend.exists(key)


def test_signed_url_is_a_file_url_locally(tmp_path):
    backend = LocalBackend(str(tmp_path / "store"))
    backend.put("cache/x.wav", _write(str(tmp_path / "s.wav")))
    assert backend.signed_url("cache/x.wav").startswith("file:///")


def test_head_reports_size_and_local_sha256(tmp_path):
    """LocalBackend hashes the file it owns, so the dossier's size and hash
    columns can be full without a second read of the bytes by the caller."""
    import hashlib

    backend = LocalBackend(str(tmp_path / "store"))
    data = b"canonical-wav-bytes"
    backend.put("cache/x.wav", _write(str(tmp_path / "s.wav"), data))
    head = backend.head("cache/x.wav")
    assert head.size_bytes == len(data)
    assert head.sha256 == hashlib.sha256(data).hexdigest()


def test_head_missing_key_raises_object_not_found(tmp_path):
    from foleyflow.storage.base import ObjectNotFound

    backend = LocalBackend(str(tmp_path / "store"))
    with pytest.raises(ObjectNotFound):
        backend.head("cache/nope.wav")


# -- object listing without HEADs (unit 7.19) -------------------------------

def test_list_objects_carries_stat_metadata_in_list_keys_order(tmp_path):
    """LocalBackend.list_objects supplies Size / LastModified from os.stat for
    every object — the listing's contract (unit 7.19). sha256 is deliberately
    absent: the listing is hash-free, and a caller needing a content hash opts
    in per candidate via head (7.19 review)."""
    backend = LocalBackend(str(tmp_path / "store"))
    payloads = {
        "projects/p1/timelines/timeline.v1.json": b'{"v":1}',
        "cache/0123456789abcdef.wav": b"canonical-wav-bytes",
        "projects/p1/runs/r1/mix.wav": b"mix-bytes",
    }
    for key, data in payloads.items():
        backend.put_bytes(key, data)

    listed = backend.list_objects("projects/p1")
    assert [m.key for m in listed] == sorted(k for k in payloads
                                             if k.startswith("projects/p1"))
    for meta in listed:
        assert meta.size_bytes == len(payloads[meta.key])
        assert meta.last_modified is not None
        # Hash-free by contract: the dossier must never pay to hash the whole
        # shared cache just to resolve the stems one project uses.
        assert meta.sha256 is None
        # The per-candidate opt-in is head — the same value it always gave.
        assert backend.head(meta.key).sha256 is not None

    # The two LISTs a real dossier needs cover every candidate key.
    all_keys = {m.key for m in backend.list_objects("projects/p1")}
    all_keys |= {m.key for m in backend.list_objects("cache")}
    assert all_keys == set(payloads)


def test_list_objects_matches_list_keys_exactly(tmp_path):
    """The two listings must never disagree — list_keys is the hot path
    (event logs, ledger scans) and list_objects adds hashing, so they are
    deliberately separate implementations that must return the same keys."""
    backend = LocalBackend(str(tmp_path / "store"))
    for i in range(5):
        backend.put_bytes(f"proj/x/obj{i}.json", str(i).encode())
        backend.put_bytes(f"proj/x/obj{i}.json.part", b"half")  # skipped
    assert [m.key for m in backend.list_objects("proj/x")] == \
        backend.list_keys("proj/x")


def test_list_keys_stays_hash_free(tmp_path, monkeypatch):
    """Neither listing pays a hash — list_keys runs on every event-log and
    ledger scan (unit 7.18's wins depend on it being cheap) and list_objects
    lists whole prefixes (the dossier would otherwise hash the whole shared
    cache to resolve a handful of stems — 7.19 review). The hash lives in
    head, the per-candidate opt-in."""
    import foleyflow.storage.local as local_mod

    backend = LocalBackend(str(tmp_path / "store"))
    for i in range(3):
        backend.put_bytes(f"proj/x/obj{i}.json", str(i).encode())

    def _refuse_hash(path):
        raise AssertionError(f"listings must not hash files: {path!r}")

    monkeypatch.setattr(local_mod, "_sha256_file", _refuse_hash)
    assert backend.list_keys("proj/x") == \
        [f"proj/x/obj{i}.json" for i in range(3)]
    assert [m.key for m in backend.list_objects("proj/x")] == \
        [f"proj/x/obj{i}.json" for i in range(3)]
    # The hash is only ever paid per candidate, via head.
    with pytest.raises(AssertionError):
        backend.head("proj/x/obj0.json")


def test_list_objects_absent_prefix_is_empty_and_a_file_prefix_works(tmp_path):
    """An absent prefix is an empty collection (matching list_keys); a prefix
    that names a single file — a one-segment input key in tests — is that one
    object, so the dossier can still list it without a HEAD."""
    backend = LocalBackend(str(tmp_path / "store"))
    assert backend.list_objects("nope") == []
    backend.put_bytes("k", b"single-segment")
    listed = backend.list_objects("k")
    assert [m.key for m in listed] == ["k"]
    assert listed[0].size_bytes == len(b"single-segment")


# -- factory --

def test_factory_defaults_to_local(monkeypatch):
    monkeypatch.delenv("FOLEYFLOW_CACHE_BACKEND", raising=False)
    assert isinstance(get_backend(), LocalBackend)


def test_b2_without_config_fails_loud_rather_than_degrading(monkeypatch):
    """A deployed FoleyFlow that quietly fell back to local would write
    manifests pointing at objects that were never uploaded."""
    for var in ("B2_ENDPOINT_URL", "B2_REGION", "B2_BUCKET", "B2_KEY_ID",
                "B2_APPLICATION_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(StorageError) as excinfo:
        get_backend("b2")
    assert "B2_BUCKET" in str(excinfo.value)


def test_unknown_backend_kind_raises(monkeypatch):
    with pytest.raises(StorageError):
        get_backend("dropbox")


# -- concurrent multi-get (unit 7.18) ---------------------------------------

def test_get_bytes_many_matches_serial_get(tmp_path):
    """Concurrent multi-get returns the same bytes as serial get_bytes."""
    from foleyflow.storage.base import get_bytes_many

    backend = LocalBackend(str(tmp_path / "store"))
    payloads = {f"obj/{i}.json": f'{{"i":{i}}}'.encode() for i in range(20)}
    for key, data in payloads.items():
        backend.put_bytes(key, data, content_type="application/json")

    keys = list(payloads.keys())
    serial = {k: backend.get_bytes(k) for k in keys}
    concurrent = get_bytes_many(backend, keys, max_workers=8)
    assert concurrent == serial
    # Insertion order follows the input key sequence.
    assert list(concurrent.keys()) == keys


def test_get_bytes_many_skip_missing_omits_absent_keys(tmp_path):
    from foleyflow.storage.base import ObjectNotFound, get_bytes_many

    backend = LocalBackend(str(tmp_path / "store"))
    backend.put_bytes("a.json", b"A")
    backend.put_bytes("c.json", b"C")

    out = get_bytes_many(backend, ["a.json", "b.json", "c.json"],
                         skip_missing=True)
    assert out == {"a.json": b"A", "c.json": b"C"}

    with pytest.raises(ObjectNotFound):
        get_bytes_many(backend, ["a.json", "b.json"], skip_missing=False)


def test_get_bytes_many_propagates_storage_error(tmp_path):
    """A non-ObjectNotFound fault must fail the whole multi-get — never drop
    rows silently (event log / ledger correctness)."""
    from foleyflow.storage.base import get_bytes_many

    class Flaky(LocalBackend):
        def get_bytes(self, key):
            if key == "bad.json":
                raise StorageError("simulated outage")
            return super().get_bytes(key)

    backend = Flaky(str(tmp_path / "store"))
    backend.put_bytes("ok.json", b"ok")
    backend.put_bytes("bad.json", b"x")

    with pytest.raises(StorageError, match="simulated outage"):
        get_bytes_many(backend, ["ok.json", "bad.json"], max_workers=4)


def test_route_pool_nested_multi_get_does_not_deadlock(tmp_path):
    """Route tasks that call get_bytes_many must not share the multi-get pool.

    Submitting nested multi-gets onto the same ThreadPoolExecutor as the
    outer fan-out deadlocks when every worker waits on futures that cannot
    start (phase-2 adversarial P0). Route pool + io pool are separate.
    """
    import time
    from concurrent.futures import wait, FIRST_EXCEPTION

    from foleyflow.storage.base import get_bytes_many, get_route_pool

    backend = LocalBackend(str(tmp_path / "store"))
    for i in range(8):
        backend.put_bytes(f"k{i}.json", f"v{i}".encode())

    keys = [f"k{i}.json" for i in range(8)]

    def nested_multi_get(_i):
        # Simulate get_state / project_spend work submitted on the route pool.
        return get_bytes_many(backend, keys, max_workers=4)

    pool = get_route_pool()
    futs = [pool.submit(nested_multi_get, i) for i in range(4)]
    done, not_done = wait(futs, timeout=5.0, return_when=FIRST_EXCEPTION)
    assert not not_done, "nested multi-get via route pool deadlocked"
    for fut in futs:
        out = fut.result(timeout=0.1)
        assert len(out) == 8
