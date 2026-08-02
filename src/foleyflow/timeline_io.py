"""Timeline JSON (de)serialization — shared by the CLI (--timeline /
--from-timeline), run_spike.py, and the samples/ export."""

import json
import math

from foleyflow.models import Event, Timeline


def timeline_to_dict(t: Timeline) -> dict:
    events = []
    for ev in t.events:
        ed = {
            "name": ev.name,
            "start_time": ev.start_time,
            "duration": ev.duration,
            "intensity": ev.intensity,
            "kind": ev.kind,
        }
        # Omit null optionals so existing timelines stay clean / unchanged.
        if ev.seed is not None:
            ed["seed"] = ev.seed
        if ev.source_duration_s is not None:
            ed["source_duration_s"] = ev.source_duration_s
        if ev.stem_key is not None:
            ed["stem_key"] = ev.stem_key
        events.append(ed)
    d = {
        "duration": t.duration,
        "ambience": t.ambience,
        "events": events,
    }
    # Preserve history and other extra fields in the output dictionary
    if hasattr(t, "extra_fields") and t.extra_fields:
        for k, v in t.extra_fields.items():
            if k not in d:
                d[k] = v
    if hasattr(t, "history") and t.history:
        d["history"] = t.history
    return d


def _coerce_seed(raw) -> int | None:
    """Optional mix seed: int as-is, reject non-integers rather than crash later
    inside numpy's RNG. JSON null / missing → None.

    Shared with `analyze._validate_event` so the two round-trip doors (§A.4)
    cannot drift on what a seed *is*. They deliberately differ only in what
    they do with a rejection: this door raises (the document came from our own
    storage, so a bad seed is corruption), analyze warns and drops the seed
    (it is parsing untrusted model output, and a hallucinated `"12"` must not
    fail the whole analysis). Truncating `3.7` to `3` — which analyze used to
    do via `int()` — is the failure mode this sharing removes: a silently
    different seed is a silently different mix.
    """
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TypeError(
            f"event seed must be an int or null, got {type(raw).__name__}")
    return raw


def _coerce_source_duration_s(raw) -> float | None:
    """Optional frozen SFX request duration. JSON null / missing → None.
    Accept int or float (JSON numbers); reject bool/str, negatives, non-finite.
    Shared with analyze so freeze validation is one rule.

    `0.0` is accepted and is *present*, not absent — nulling it is an unfreeze.
    It does **not** mean a zero-length request: `route_event` clamps every
    requested duration to the endpoint's declared minimum (0.3–1.0 s depending
    on the model), so a frozen `0.0` addresses the endpoint-minimum clip. That
    is still a stable, already-paid-for cache address, which is all a freeze
    has to be, so it stays legal rather than being rejected.
    """
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise TypeError(
            f"event source_duration_s must be a number or null, "
            f"got {type(raw).__name__}")
    val = float(raw)
    if not math.isfinite(val) or val < 0.0:
        raise ValueError(
            f"event source_duration_s must be a non-negative finite number, "
            f"got {raw!r}")
    return val


def _coerce_stem_key(raw) -> str | None:
    """Optional stem storage key. JSON null / missing → None. Reject
    non-strings — a stem key is an address, and a mangled one would make the
    preview fetch the wrong audio or none."""
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise TypeError(
            f"event stem_key must be a non-empty string or null, "
            f"got {type(raw).__name__}")
    return raw.strip()


def dict_to_timeline(d: dict) -> Timeline:
    events = [
        Event(
            name=ev["name"],
            start_time=ev["start_time"],
            duration=ev["duration"],
            intensity=ev["intensity"],
            kind=ev["kind"],
            seed=_coerce_seed(ev.get("seed")),
            source_duration_s=_coerce_source_duration_s(ev.get("source_duration_s")),
            stem_key=_coerce_stem_key(ev.get("stem_key")),
        ) for ev in d["events"]
    ]
    # Identify history and extra fields in input dictionary
    history = d.get("history", [])
    extra_fields = {k: v for k, v in d.items() if k not in ("duration", "ambience", "events", "history")}
    return Timeline(
        duration=d["duration"], 
        ambience=d["ambience"], 
        events=events,
        history=history,
        extra_fields=extra_fields
    )


def save_timeline(t: Timeline, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(timeline_to_dict(t), f, indent=2)
        f.write("\n")


def load_timeline(path: str) -> Timeline:
    with open(path, "r", encoding="utf-8") as f:
        return dict_to_timeline(json.load(f))
