"""Content-addressed artifact storage for FoleyFlow.

Two backends behind one small interface:

  LocalBackend — filesystem, the dev/offline default. Byte-for-byte compatible
                 with the media core's `sfx_cache/` layout.
  B2Backend    — Backblaze B2 via its S3-compatible API.

The SFX cache lives in B2 in a deployed FoleyFlow because it is genuinely
content-addressed: the same (endpoint, prompt, duration) always names the same
audio, so the cache is shared across every project in the bucket rather than
re-billed per project. It also keeps 178 MB of WAVs out of the git repo.

This storage package does not import `foley` at all, so it can be reused or
tested on its own. Note that the reverse is no longer true: since the provider
decoupling, `foley` reads its model config from `foleyflow.providers`, so the
media core is no longer independently pullable from upstream.
"""

from foleyflow.storage.base import (
    Backend,
    ObjectNotFound,
    PreconditionFailed,
    StorageError,
)
from foleyflow.storage.local import LocalBackend
from foleyflow.storage.factory import get_backend

__all__ = ["Backend", "StorageError", "ObjectNotFound", "PreconditionFailed",
           "LocalBackend", "get_backend"]
