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
import threading
import time

# Crockford base32: no I, L, O, or U — avoids transcription ambiguity.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ENCODED_LEN = 26
_TIMESTAMP_LEN = 10
_RANDOM_BITS = 80
_MAX_RANDOM = (1 << _RANDOM_BITS) - 1

# Guards the monotonic counter below. Events are appended from concurrent
# worker threads, so the last-issued state has to be shared safely.
_lock = threading.Lock()
_last_ms = -1
_last_random = 0


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        value, remainder = divmod(value, 32)
        chars.append(_ALPHABET[remainder])
    return "".join(reversed(chars))


def new_ulid(timestamp_ms: int | None = None) -> str:
    """A fresh, monotonically increasing ULID.

    Monotonic within a millisecond, per the ULID spec's monotonic variant: when
    the clock has not advanced, the random component is incremented rather than
    redrawn.

    This matters because sort order *is* the event log's order. Several events
    are routinely appended inside one millisecond — a run that starts, proposes
    a plan, and completes — and with independently random suffixes they would
    come back shuffled, so the audit trail would show a run completing before it
    started.

    Pass timestamp_ms only in tests that assert ordering.
    """
    global _last_ms, _last_random

    if timestamp_ms is not None:
        randomness = int.from_bytes(os.urandom(10), "big")
        return (_encode(timestamp_ms, _TIMESTAMP_LEN)
                + _encode(randomness, _ENCODED_LEN - _TIMESTAMP_LEN))

    with _lock:
        now_ms = int(time.time() * 1000)
        if now_ms == _last_ms and _last_random < _MAX_RANDOM:
            _last_random += 1
        elif now_ms > _last_ms:
            _last_ms = now_ms
            _last_random = int.from_bytes(os.urandom(10), "big") >> 1
        else:
            # Clock went backwards, or the counter saturated. Stay on the last
            # timestamp and keep counting: a duplicate id would be far worse
            # than a few milliseconds of drift.
            _last_ms += 1
            _last_random = int.from_bytes(os.urandom(10), "big") >> 1
        ts, randomness = _last_ms, _last_random

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

    # Case-sensitive on purpose. Crockford base32 decoding is case-insensitive
    # for *parsing*, but this id is about to become an S3 key, and S3 keys are
    # case-sensitive — accepting a lowercase variant lets a caller create a
    # second project that shadows an existing one, with its own event log and
    # a list ordering that no longer matches creation time. Windows'
    # case-insensitive filesystem hides this locally; B2 does not.
    return (
        len(identifier) == _ENCODED_LEN
        and all(c in _ALPHABET for c in identifier)
    )
