"""Presigned upload targets.

SPEC.md §8.3 makes B2 the "direct video upload destination through presigned
URLs" — the client PUTs straight to the bucket and a 26 MB video never passes
through the application server.

The signing itself now lives on the backend as `B2Backend.signed_upload_url`,
next to `signed_url` — the presigned-GET half of the same idea. What remains
here is the key derivation, which is an API-layer concern because it depends on
the untrusted filename a client supplied.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Tuple

from foleyflow.storage.b2 import B2Backend
from foleyflow.storage.base import Backend, StorageError

# Long enough for a large file on a poor connection, short enough that a leaked
# URL is not an open write channel into the bucket.
DEFAULT_UPLOAD_EXPIRY_S = 900


def source_key(project_id: str, filename: str) -> str:
    """Where a project's source video lives (SPEC.md §10).

    The stored name is fixed as `source` plus the original extension rather than
    the user's filename: the filename is untrusted input and this key is
    concatenated into an object path. The original name is preserved in the
    project record, where it is data rather than a path component.
    """
    _, ext = os.path.splitext(filename)
    ext = ext.lower()
    if not ext or not ext.startswith(".") or not ext[1:].isalnum() or len(ext) > 6:
        ext = ".mp4"
    return f"projects/{project_id}/input/source{ext}"


def create_upload_target(backend: Backend, key: str, content_type: str,
                         expires_s: int = DEFAULT_UPLOAD_EXPIRY_S) -> Tuple[str, str, dict]:
    """Return `(url, expires_at_iso, headers)` for a direct client upload.

    `Content-Type` is pinned into the signature, so a client cannot sign a URL
    for a video and then use it to store something else.
    """
    if not isinstance(backend, B2Backend):
        # LocalBackend has nothing to presign. Callers in dev upload through the
        # API instead; failing loudly beats handing back a file:// URL that a
        # browser cannot PUT to.
        raise StorageError(
            "direct upload requires the B2 backend "
            "(set FOLEYFLOW_CACHE_BACKEND=b2)"
        )

    url = backend.signed_upload_url(key, content_type, expires_s)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_s)).isoformat()
    return url, expires_at, {"Content-Type": content_type}
