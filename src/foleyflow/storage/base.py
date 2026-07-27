"""The storage interface every FoleyFlow backend implements.

Keys are POSIX-style, slash-separated, and never absolute — e.g.
`cache/03f8c512074fd16b.wav` or
`projects/proj_01J.../runs/run_01J.../stems/evt_armor_steps_01.wav`.
The backend owns any bucket/prefix concerns; callers never see them.
"""

from typing import List, Optional, Protocol, runtime_checkable


class StorageError(RuntimeError):
    """A backend operation failed. Raised instead of leaking boto3/OS errors so
    callers can treat every backend the same way."""


@runtime_checkable
class Backend(Protocol):
    """Object storage, reduced to what FoleyFlow actually needs.

    Deliberately has no delete: SPEC.md §10 requires that a completed
    generation or manifest is never overwritten or removed. `exports/latest.json`
    is the one mutable pointer, and it is written with put(), not mutated.
    """

    def exists(self, key: str) -> bool:
        """True if an object is stored at `key`."""
        ...

    def get(self, key: str, dest_path: str) -> str:
        """Download `key` to `dest_path`, creating parent directories.
        Returns dest_path. Raises StorageError if the object is absent."""
        ...

    def put(self, key: str, src_path: str, content_type: Optional[str] = None) -> str:
        """Upload the file at `src_path` to `key`. Returns the key."""
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
