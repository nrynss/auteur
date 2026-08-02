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
    inside numpy's RNG. JSON null / missing → None."""
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TypeError(
            f"event seed must be an int or null, got {type(raw).__name__}")
    return raw


def _coerce_source_duration_s(raw) -> float | None:
    """Optional frozen SFX request duration. JSON null / missing → None.
    Accept int or float (JSON numbers); reject bool/str, negatives, non-finite.
    0.0 is valid and means a frozen zero-length request (not the same as
    absent/legacy). Shared with analyze so freeze validation is one rule."""
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
