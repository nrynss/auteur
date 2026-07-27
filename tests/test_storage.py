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
