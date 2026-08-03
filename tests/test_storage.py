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
