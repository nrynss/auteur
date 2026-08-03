"""Backend selection from the environment.

`FOLEYFLOW_CACHE_BACKEND=local|b2` picks the implementation. Missing B2
configuration fails loud rather than silently degrading to local — a deployed
FoleyFlow that quietly stopped writing to B2 would produce projects whose
provenance manifests point at objects that were never uploaded.
"""

import os
import re
import threading
from typing import Optional, Tuple

from dotenv import load_dotenv

from foleyflow.storage.base import Backend, StorageError
from foleyflow.storage.local import LocalBackend

load_dotenv()

# B2_REGION is derivable and therefore not required — see _derive_region.
_B2_REQUIRED = ("B2_ENDPOINT_URL", "B2_BUCKET", "B2_KEY_ID", "B2_APPLICATION_KEY")

_B2_ENDPOINT_RE = re.compile(r"s3\.([a-z0-9-]+)\.backblazeb2\.com")

# One Backend instance per config fingerprint. Ledger warm paths resolve
# scope_id via get_backend() on every cache hit; constructing a fresh B2
# client (boto3) each time undoes unit 7.18's sub-ms aggregate reads.
# Keyed by env that determines the store so rotating credentials or
# switching buckets builds a new client; identical config reuses one.
_backend_lock = threading.Lock()
_backend_cache: dict = {}  # config key -> Backend


def _normalize_endpoint(endpoint: str) -> str:
    """B2's console shows the endpoint without a scheme ('s3.us-east-005.
    backblazeb2.com'). boto3 needs one, and fails with an unhelpful
    'Invalid endpoint' if it is missing — so add it rather than make every
    caller remember."""
    endpoint = endpoint.strip()
    if not endpoint.startswith(("http://", "https://")):
        endpoint = "https://" + endpoint
    return endpoint


def _derive_region(endpoint: str, configured: Optional[str]) -> str:
    """B2 encodes the region in the endpoint host, and a region that disagrees
    with the host fails signing with an opaque 403. Prefer the host so the two
    cannot drift apart."""
    if configured:
        return configured.strip()
    match = _B2_ENDPOINT_RE.search(endpoint)
    if not match:
        raise StorageError(
            f"B2_REGION is unset and cannot be derived from B2_ENDPOINT_URL "
            f"({endpoint!r}) — set B2_REGION explicitly, e.g. 'us-east-005'."
        )
    return match.group(1)


def _config_key(kind: str) -> Tuple:
    """Fingerprint of env that selects one physical store + credentials."""
    if kind == "local":
        root = os.environ.get("FOLEYFLOW_LOCAL_CACHE_DIR", "sfx_cache")
        return ("local", os.path.realpath(os.path.abspath(root)))
    if kind == "b2":
        return (
            "b2",
            os.environ.get("B2_ENDPOINT_URL", "").strip(),
            os.environ.get("B2_BUCKET", "").strip(),
            os.environ.get("B2_KEY_ID", "").strip(),
            os.environ.get("B2_APPLICATION_KEY", "").strip(),
            os.environ.get("B2_PREFIX", "foleyflow").strip(),
            os.environ.get("B2_REGION", "").strip(),
        )
    return ("unknown", kind)


def _build_backend(kind: str) -> Backend:
    if kind == "local":
        return LocalBackend(os.environ.get("FOLEYFLOW_LOCAL_CACHE_DIR", "sfx_cache"))

    if kind == "b2":
        missing = [v for v in _B2_REQUIRED if not os.environ.get(v)]
        if missing:
            raise StorageError(
                "FOLEYFLOW_CACHE_BACKEND=b2 but these are unset: "
                + ", ".join(missing)
                + " — see .env.example"
            )
        endpoint = _normalize_endpoint(os.environ["B2_ENDPOINT_URL"])
        bucket = os.environ["B2_BUCKET"].strip()
        # B2's UI shows both a bucket name and a 24-hex-char bucket ID; only
        # the name works over the S3 API, and using the ID yields a bare 404
        # on every call with no hint about why.
        if re.fullmatch(r"[0-9a-f]{24}", bucket):
            raise StorageError(
                f"B2_BUCKET looks like a bucket ID ({bucket}), not a bucket name. "
                "The S3-compatible API needs the name shown in the B2 console."
            )
        from foleyflow.storage.b2 import B2Backend
        return B2Backend(
            endpoint_url=endpoint,
            region=_derive_region(endpoint, os.environ.get("B2_REGION")),
            bucket=bucket,
            key_id=os.environ["B2_KEY_ID"],
            application_key=os.environ["B2_APPLICATION_KEY"],
            prefix=os.environ.get("B2_PREFIX", "foleyflow"),
        )

    raise StorageError(f"unknown FOLEYFLOW_CACHE_BACKEND {kind!r}, expected 'local' or 'b2'")


def get_backend(kind: Optional[str] = None) -> Backend:
    """Return the configured backend. `kind` overrides FOLEYFLOW_CACHE_BACKEND.

    Instances are cached per config fingerprint so warm ledger paths that only
    need ``scope_id()`` do not pay a boto3 client construction on every hit.
    Tests that monkeypatch this function are unaffected.
    """
    kind = (kind or os.environ.get("FOLEYFLOW_CACHE_BACKEND", "local")).strip().lower()
    key = _config_key(kind)
    with _backend_lock:
        cached = _backend_cache.get(key)
        if cached is not None:
            return cached
        backend = _build_backend(kind)
        _backend_cache[key] = backend
        return backend
