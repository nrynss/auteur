"""Backend selection from the environment.

`FOLEYFLOW_CACHE_BACKEND=local|b2` picks the implementation. Missing B2
configuration fails loud rather than silently degrading to local — a deployed
FoleyFlow that quietly stopped writing to B2 would produce projects whose
provenance manifests point at objects that were never uploaded.
"""

import os
from typing import Optional

from dotenv import load_dotenv

from foleyflow.storage.base import Backend, StorageError
from foleyflow.storage.local import LocalBackend

load_dotenv()

_B2_VARS = ("B2_ENDPOINT_URL", "B2_REGION", "B2_BUCKET", "B2_KEY_ID", "B2_APPLICATION_KEY")


def get_backend(kind: Optional[str] = None) -> Backend:
    """Build the configured backend. `kind` overrides FOLEYFLOW_CACHE_BACKEND."""
    kind = (kind or os.environ.get("FOLEYFLOW_CACHE_BACKEND", "local")).strip().lower()

    if kind == "local":
        return LocalBackend(os.environ.get("FOLEYFLOW_LOCAL_CACHE_DIR", "sfx_cache"))

    if kind == "b2":
        missing = [v for v in _B2_VARS if not os.environ.get(v)]
        if missing:
            raise StorageError(
                "FOLEYFLOW_CACHE_BACKEND=b2 but these are unset: "
                + ", ".join(missing)
                + " — see .env.example"
            )
        from foleyflow.storage.b2 import B2Backend
        return B2Backend(
            endpoint_url=os.environ["B2_ENDPOINT_URL"],
            region=os.environ["B2_REGION"],
            bucket=os.environ["B2_BUCKET"],
            key_id=os.environ["B2_KEY_ID"],
            application_key=os.environ["B2_APPLICATION_KEY"],
            prefix=os.environ.get("B2_PREFIX", "foleyflow"),
        )

    raise StorageError(f"unknown FOLEYFLOW_CACHE_BACKEND {kind!r}, expected 'local' or 'b2'")
