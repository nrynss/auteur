"""Content-addressed artifact storage for Auteur.

Two backends behind one small interface:

  LocalBackend — filesystem, the dev/offline default. Byte-for-byte compatible
                 with the media core's `sfx_cache/` layout.
  B2Backend    — Backblaze B2 via its S3-compatible API.

The SFX cache lives in B2 in a deployed Auteur because it is genuinely
content-addressed: the same (endpoint, prompt, duration) always names the same
audio, so the cache is shared across every project in the bucket rather than
re-billed per project. It also keeps 178 MB of WAVs out of the git repo.

Storage stays free of the media pipeline modules so it can be reused or tested
on its own. Pipeline modules (analyze, sfx, routing) read model config from
`foleyflow.providers`.
"""

from foleyflow.storage.base import (
    Backend,
    ObjectHead,
    ObjectNotFound,
    PreconditionFailed,
    StorageError,
    get_bytes_many,
    get_io_pool,
    get_route_pool,
)
from foleyflow.storage.local import LocalBackend
from foleyflow.storage.factory import get_backend

__all__ = [
    "Backend",
    "ObjectHead",
    "StorageError",
    "ObjectNotFound",
    "PreconditionFailed",
    "LocalBackend",
    "get_backend",
    "get_bytes_many",
    "get_io_pool",
    "get_route_pool",
]
