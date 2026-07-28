"""Timeline JSON (de)serialization — shared by the CLI (--timeline /
--from-timeline), run_spike.py, and the samples/ export."""

import json

from foleyflow.models import Event, Timeline


def timeline_to_dict(t: Timeline) -> dict:
    d = {
        "duration": t.duration,
        "ambience": t.ambience,
        "events": [
            {
                "name": ev.name,
                "start_time": ev.start_time,
                "duration": ev.duration,
                "intensity": ev.intensity,
                "kind": ev.kind
            } for ev in t.events
        ]
    }
    # Preserve history and other extra fields in the output dictionary
    if hasattr(t, "extra_fields") and t.extra_fields:
        for k, v in t.extra_fields.items():
            if k not in d:
                d[k] = v
    if hasattr(t, "history") and t.history:
        d["history"] = t.history
    return d


def dict_to_timeline(d: dict) -> Timeline:
    events = [
        Event(
            name=ev["name"],
            start_time=ev["start_time"],
            duration=ev["duration"],
            intensity=ev["intensity"],
            kind=ev["kind"]
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
