"""Content-addressed artifact storage for FoleyFlow.

Two backends behind one small interface:

  LocalBackend — filesystem, the dev/offline default. Byte-for-byte compatible
                 with the media core's `sfx_cache/` layout.
  B2Backend    — Backblaze B2 via its S3-compatible API.

The SFX cache lives in B2 in a deployed FoleyFlow because it is genuinely
content-addressed: the same (endpoint, prompt, duration) always names the same
audio, so the cache is shared across every project in the bucket rather than
re-billed per project. It also keeps 178 MB of WAVs out of the git repo.

Nothing in this package imports `foley` at module scope beyond the pure
helpers, and nothing in `foley` imports this package — the media core stays
independently testable and pullable from upstream.
"""

from foleyflow.storage.base import Backend, StorageError
from foleyflow.storage.local import LocalBackend
from foleyflow.storage.factory import get_backend

__all__ = ["Backend", "StorageError", "LocalBackend", "get_backend"]
