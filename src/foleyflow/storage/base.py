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

import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Sequence, runtime_checkable


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


@dataclass(frozen=True)
class ObjectMeta:
    """One stored object as a listing reports it (unit 7.19).

    `list_objects_v2` returns Size, LastModified and ETag for every object in
    the listing, 1000 at a time, and LocalBackend supplies the same from
    `os.stat` — so an artifact listing can be composed with zero HEAD requests
    (the dossier measured 56 s of HEADs against a real 131-key project).

    `sha256` follows the same rule as `ObjectHead.sha256`: a full-content hash
    of the stored object when the backend can supply one without a download
    (LocalBackend hashes the file), else None. It is NEVER derived from an
    ETag — a multipart upload's ETag is not an MD5 of the content, let alone a
    sha256 (docs/api.md §3.9).
    """

    key: str
    size_bytes: int
    last_modified: Optional[str] = None   # ISO-8601; None when unknown
    etag: Optional[str] = None            # opaque, backend-specific
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

    def head_supplies_sha256(self) -> bool:
        """Whether `head`'s result carries a full-content sha256 of the stored
        object (unit 7.19 review). LocalBackend hashes the local file; B2's
        S3 API does not expose one. Callers use this instead of concrete
        backend types when deciding whether a per-object `head` can buy them
        a hash — a listing must never pay a round trip for a hash that does
        not exist.
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

    def list_objects(self, prefix: str) -> List[ObjectMeta]:
        """Every object under `prefix` with its listing metadata (unit 7.19).

        The same key space and ordering as `list_keys`, plus the Size,
        LastModified and ETag the listing already carries — so callers compose
        artifact listings without one `head` per object (the dossier measured
        56 s of HEADs against a real 131-key project; `list_objects_v2`
        returns the same facts 1000 at a time, and LocalBackend reads them
        from `os.stat`). `sha256` is only ever the backend's own full-content
        hash (LocalBackend), never an ETag-derived guess.

        Returns [] for an empty prefix, matching `list_keys`. Absent and
        unknown stay distinct, as everywhere else in this interface: a backend
        that cannot answer raises `StorageError`.
        """
        ...

    def scope_id(self) -> str:
        """Stable id for the physical store this backend addresses.

        Process-local ledger caches (row snapshots, attribution overrides,
        spend/quota aggregates) key on this string: two backends that point at
        the same store share a cache slot; different stores must not. Local
        backends use a canonical absolute root path; B2 uses bucket + prefix.
        Callers must never parse the string — equality is the only contract.
        """
        ...


# Bound concurrent GETs against object storage. Dozens of serial B2 round-trips
# dominate GET /projects/{id} and ledger scans; unbounded fan-out would open
# hundreds of sockets on a large ledger.
_DEFAULT_MULTI_GET_WORKERS = 24

# Process-level pool for storage multi-get only (unit 7.18 phase-2).
# Creating a ThreadPoolExecutor per multi-get under poll storms exploded
# threads; one capped pool queues instead.
#
# Route-level fan-out (get_project parallel scenes/QA/HEAD, budget
# state+spend, dossier HEADs) uses a *separate* pool — see
# :func:`get_route_pool`. Submitting work onto the multi-get pool that then
# calls ``get_bytes_many`` (which blocks on the same pool) deadlocks when
# every worker is waiting on nested futures (phase-2 adversarial P0).
_IO_POOL_WORKERS = 24
_io_pool: Optional[ThreadPoolExecutor] = None
_io_pool_lock = threading.Lock()

_ROUTE_POOL_WORKERS = 8
_route_pool: Optional[ThreadPoolExecutor] = None
_route_pool_lock = threading.Lock()


def get_io_pool(max_workers: int = _IO_POOL_WORKERS) -> ThreadPoolExecutor:
    """Shared bound thread pool for storage multi-get only.

    ``max_workers`` is accepted for API symmetry with callers that know their
    fan-out; the process pool is sized to ``_IO_POOL_WORKERS`` and never
    grows past that cap. Do **not** run tasks here that themselves call
    :func:`get_bytes_many` — use :func:`get_route_pool` for route fan-out.
    """
    global _io_pool
    del max_workers  # pool size is process-global; fan-out is queue depth.
    with _io_pool_lock:
        if _io_pool is None:
            _io_pool = ThreadPoolExecutor(
                max_workers=_IO_POOL_WORKERS,
                thread_name_prefix="ff-storage-io",
            )
        return _io_pool


def get_route_pool(max_workers: int = _ROUTE_POOL_WORKERS) -> ThreadPoolExecutor:
    """Shared pool for independent route I/O that may nest multi-gets.

    Separate from :func:`get_io_pool` so a budget handler that fans out
    ``get_state`` + ``project_spend`` (each may multi-get) cannot deadlock
    by blocking io-pool workers on more io-pool work.
    """
    global _route_pool
    del max_workers
    with _route_pool_lock:
        if _route_pool is None:
            _route_pool = ThreadPoolExecutor(
                max_workers=_ROUTE_POOL_WORKERS,
                thread_name_prefix="ff-route-io",
            )
        return _route_pool


def get_bytes_many(
    backend: Backend,
    keys: Sequence[str],
    *,
    max_workers: int = _DEFAULT_MULTI_GET_WORKERS,
    skip_missing: bool = False,
) -> Dict[str, bytes]:
    """Fetch many small objects concurrently.

    Returns a dict keyed by storage key. Insertion order follows ``keys``
    (first occurrence of each unique key), so callers can rebuild ordered
    lists by iterating the original key sequence.

    ``ObjectNotFound`` handling matches the two call-site patterns this exists
    for:

    * ``skip_missing=False`` (event logs, run records): a listed key must
      exist; any miss fails the whole call rather than silently dropping a
      row from the log.
    * ``skip_missing=True`` (ledger rows): a race between list and get is
      tolerated by omitting that key — same as the historical serial loop.

    Any other ``StorageError`` (or unexpected exception wrapped as one) fails
    the whole operation. Never silently drop events/rows on a transient
    backend fault.

    Uses the process-level :func:`get_io_pool` so concurrent polls do not each
    spawn a fresh executor.
    """
    if not keys:
        return {}

    # Preserve first-seen order while deduplicating work.
    unique: List[str] = list(dict.fromkeys(keys))
    workers = max(1, min(max_workers, len(unique)))

    def _fetch(key: str) -> bytes:
        return backend.get_bytes(key)

    out: Dict[str, bytes] = {}
    if len(unique) == 1 or workers == 1:
        for key in unique:
            try:
                out[key] = _fetch(key)
            except ObjectNotFound:
                if not skip_missing:
                    raise
            # Other StorageError / unexpected: propagate.
        return out

    hard_error: Optional[BaseException] = None
    pool = get_io_pool()
    # Submit up to ``workers`` tasks; the shared pool queues the rest so a
    # single multi-get cannot spawn unbounded threads.
    future_map: Dict[Future, str] = {
        pool.submit(_fetch, key): key for key in unique
    }
    for fut in as_completed(future_map):
        key = future_map[fut]
        try:
            out[key] = fut.result()
        except ObjectNotFound as exc:
            if not skip_missing and hard_error is None:
                hard_error = exc
        except StorageError as exc:
            if hard_error is None:
                hard_error = exc
        except Exception as exc:
            if hard_error is None:
                hard_error = StorageError(
                    f"failed to read {key!r}: {type(exc).__name__}: {exc}"
                )
    if hard_error is not None:
        raise hard_error

    # Re-order by input key sequence so dict iteration matches list order.
    return {k: out[k] for k in unique if k in out}
