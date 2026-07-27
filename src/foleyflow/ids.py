"""ULID identifiers.

SPEC.md §11 shows ids of the form `proj_01J...` / `run_01J...` — ULIDs: a
48-bit millisecond timestamp followed by 80 random bits, Crockford base32.

The property that earns them their place here is that they sort
lexicographically in creation order. Object storage gives us a sorted key
listing and nothing else — no indexes, no ORDER BY — so "the latest run" and
"replay events in order" are both just `sorted(list_keys(...))[-1]` and
`sorted(...)`. A uuid4 would force us to open every object to find out which
came last.
"""

from __future__ import annotations

import os
import time

# Crockford base32: no I, L, O, or U — avoids transcription ambiguity.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ENCODED_LEN = 26
_TIMESTAMP_LEN = 10


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        value, remainder = divmod(value, 32)
        chars.append(_ALPHABET[remainder])
    return "".join(reversed(chars))


def new_ulid(timestamp_ms: int | None = None) -> str:
    """A fresh ULID. Pass timestamp_ms only in tests that assert ordering."""
    ts = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    randomness = int.from_bytes(os.urandom(10), "big")
    return _encode(ts, _TIMESTAMP_LEN) + _encode(randomness, _ENCODED_LEN - _TIMESTAMP_LEN)


def timestamp_ms(ulid: str) -> int:
    """Milliseconds since epoch encoded in `ulid`'s prefix."""
    value = 0
    for char in ulid[:_TIMESTAMP_LEN]:
        value = value * 32 + _ALPHABET.index(char.upper())
    return value


def new_project_id() -> str:
    return f"proj_{new_ulid()}"


def new_run_id() -> str:
    return f"run_{new_ulid()}"


def new_event_id() -> str:
    return f"evt_{new_ulid()}"


def is_valid(identifier: str, prefix: str | None = None) -> bool:
    """Whether `identifier` is a well-formed id, optionally of a given kind.

    Used to reject a caller-supplied id before it becomes a storage key —
    a project id lands in an object path, so a malformed one is a traversal
    risk as much as a data-quality problem.
    """
    if prefix is not None:
        if not identifier.startswith(f"{prefix}_"):
            return False
        identifier = identifier[len(prefix) + 1:]
    elif "_" in identifier:
        identifier = identifier.split("_", 1)[1]

    return (
        len(identifier) == _ENCODED_LEN
        and all(c in _ALPHABET for c in identifier.upper())
    )
