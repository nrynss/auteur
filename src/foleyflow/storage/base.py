"""The storage interface every FoleyFlow backend implements.

Keys are POSIX-style, slash-separated, and never absolute — e.g.
`cache/03f8c512074fd16b.wav` or
`projects/proj_01J.../runs/run_01J.../stems/evt_armor_steps_01.wav`.
The backend owns any bucket/prefix concerns; callers never see them.

## Absent is not the same as broken

`ObjectNotFound` exists so callers can tell "there is no such object" from
"storage did not answer". Collapsing them is how a B2 outage becomes a
convincing `404 project_not_found`, and how a permission error becomes a cache
miss that silently re-bills every generation.

## Error messages must not carry paths or credentials

A `StorageError` reaches the client through an API error handler and is written
into permanent run records. Messages here name the *key* — which the caller
already knows — never the resolved local path, bucket, or provider URL.
"""

from dataclasses import dataclass
from typing import List, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class ObjectHead:
    """What `Backend.head` knows about one stored object.

    `size_bytes` is always known — that is the point of a head request.
    `sha256` is a full-content hash *of the stored object*, supplied when the
    backend can produce one without a download: LocalBackend hashes the local
    file, B2's S3 API does not expose one for objects uploaded without S3
    checksums. Callers must not treat `None` as a gap in their own records —
    it means the backend itself does not know.
    """

    size_bytes: int
    sha256: Optional[str] = None


class StorageError(RuntimeError):
    """A backend operation failed. Raised instead of leaking boto3/OS errors so
    callers can treat every backend the same way."""


class ObjectNotFound(StorageError):
    """The object genuinely is not there.

    A subclass of StorageError so existing `except StorageError` sites keep
    working, but distinguishable by anything that needs to tell a missing
    project from an unreachable bucket.
    """


class PreconditionFailed(StorageError):
    """A conditional write lost the race — the key already exists.

    Raised by `put_if_absent`. Callers retry with a new key rather than
    treating it as a failure.
    """


def validate_key(key: str) -> List[str]:
    """Check `key` against the key contract and return its segments.

    Enforced by every backend, not just the one whose native path handling
    happens to reject traversal. LocalBackend rejecting `../` while B2Backend
    forwards it verbatim would mean the safety of an id depended on which
    backend you were running — an accident, not a defence.
    """
    if not key or key.startswith(("/", "\\")):
        raise StorageError(f"unsafe storage key: {key!r}")
    parts = key.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise StorageError(f"unsafe storage key: {key!r}")
    if any("\\" in p or ":" in p or "\0" in p for p in parts):
        raise StorageError(f"unsafe storage key: {key!r}")
    return parts


@runtime_checkable
class Backend(Protocol):
    """Object storage, reduced to what FoleyFlow actually needs.

    Deliberately has no delete: SPEC.md §10 requires that a completed
    generation or manifest is never overwritten or removed. `exports/latest.json`
    is the one mutable pointer, and it is written with put(), not mutated.
    """

    def exists(self, key: str) -> bool:
        """True if an object is stored at `key`.

        Must raise rather than return False when the answer is unknown — a
        permission or network failure is not evidence of absence, and callers
        use this to guard immutable writes and cache lookups.
        """

    def head(self, key: str) -> ObjectHead:
        """Size — and a content hash where the backend can supply one cheaply
        — for the object at `key`.

        Absent and unknown stay distinct, as everywhere else in this
        interface: a missing object raises `ObjectNotFound`; a backend that
        cannot answer raises `StorageError`. A caller building an artifact
        listing must not fill it with zeros because storage is having a bad
        moment.
        """
        ...

    def get(self, key: str, dest_path: str) -> str:
        """Download `key` to `dest_path`, creating parent directories.
        Returns dest_path. Raises ObjectNotFound if the object is absent."""
        ...

    def get_bytes(self, key: str) -> bytes:
        """Read `key` into memory. Preferred for small JSON records — writing
        them through a shared scratch file is a data race."""
        ...

    def put(self, key: str, src_path: str, content_type: Optional[str] = None) -> str:
        """Upload the file at `src_path` to `key`. Returns the key."""
        ...

    def put_bytes(self, key: str, data: bytes,
                  content_type: Optional[str] = None) -> str:
        """Upload `data` directly, with no intermediate file."""
        ...

    def put_if_absent(self, key: str, data: bytes,
                      content_type: Optional[str] = None) -> str:
        """Atomically write `data` only if `key` does not exist.

        Raises PreconditionFailed if it does. This is the primitive that makes
        version allocation safe: an `exists()` check followed by a `put()` is
        two operations, and two writers racing between them both believe they
        won and one silently destroys the other's artifact.
        """
        ...

    def signed_url(self, key: str, expires_s: int = 3600) -> str:
        """A short-lived URL the browser can fetch directly. Local backends may
        return a `file://` URL — only the B2 backend produces a real signed URL."""
        ...

    def list_keys(self, prefix: str) -> List[str]:
        """Every key under `prefix`, lexicographically sorted.

        Sorted because ULID-named objects then come back in creation order,
        which is how the project store finds the latest run and replays an
        event log without opening anything. Returns [] for an empty prefix
        rather than raising — an absent prefix is an empty collection, not an
        error.
        """
        ...
