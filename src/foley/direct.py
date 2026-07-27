"""foley direct -- T5b. Director's-notes -> timeline edit via LLM.

Automates what the T5 tuning rounds did by hand (DECISIONS.md): an editor's
plain-language notes ("move the match strike 200ms later and make it high
intensity; drop the paper tearing event") get applied to an existing
Timeline by an LLM edit, then run through the SAME `parse_timeline_json`
validation/clamping analyze.py uses for a fresh analysis -- a hallucinated
edit is neutralized the same way a hallucinated analysis is.

The call is TEXT-ONLY: it reuses the configured vision model gateway
call with an empty `image_urls` list, the same trick `repair_json_with_vlm`
uses to get a plain text completion out of the vision gateway. No video or
image is uploaded, so this is a cheap (~$0.001) call regardless of clip
length.
"""

import json
import os
import re

from foley.analyze import DEFAULT_MODEL, parse_timeline_json
from foley.ledger import execute_with_ledger
from foley.models import Timeline
from foley.timeline_io import timeline_to_dict

DIRECT_RULES = """You are applying a director's notes to a Foley timeline. \
Apply ONLY what the notes require; preserve every other event and field \
exactly. Times are seconds.

Schema constraints (must be preserved exactly in the output):
- Each event has "name" (string), "start_time" (float seconds), "duration"
  (float seconds), "intensity", and "kind".
- "intensity" must be exactly one of: "low", "med", "high".
- "kind" must be exactly one of: "discrete", "rhythmic", "continuous".
- The top level has "duration" (float seconds), "ambience" (string), and
  "events" (a list of the event objects above).

Output ONLY the complete corrected JSON, same schema, no fences, no
commentary."""


def apply_notes(timeline: Timeline, notes: str, model: str = DEFAULT_MODEL,
                 clip_duration: float | None = None) -> Timeline:
    """Apply plain-language director's notes to `timeline`, returning the
    edited Timeline.

    Serializes the current timeline to JSON, appends the notes and the
    schema rules, and sends the whole thing as a text-only completion via
    the existing OpenRouter gateway (the empty-image-list trick). The
    response is parsed through the EXISTING `parse_timeline_json`, so
    dropping/clamping of malformed events applies exactly as it does for a
    fresh video analysis.

    clip_duration: pass the ffprobe duration of the source clip (via
    `--clip` on the CLI) to clamp any hallucinated out-of-range times; None
    skips clamping (the default -- notes edits don't always have the source
    clip on hand). If provided, it also overwrites the top-level timeline
    duration to guarantee it stays in sync with ffprobe.
    """
    import datetime

    timeline_json = json.dumps(timeline_to_dict(timeline), indent=2)
    prompt = (
        f"{DIRECT_RULES}\n\n"
        f"Current timeline JSON:\n{timeline_json}\n\n"
        f"Director's notes:\n{notes}\n"
    )

    from foleyflow.providers.config import get_analysis_config
    analysis = get_analysis_config()
    vision_model = analysis["vision_model"]

    response = execute_with_ledger(
        vision_model,
        arguments={"image_urls": [], "prompt": prompt, "model": model},
    )
    response_text = response.get("output") or response.get("text") or ""
    new_timeline = parse_timeline_json(response_text, clip_duration=clip_duration)
    if clip_duration is not None:
        new_timeline.duration = clip_duration

    # Calculate human diff and append history log (T5b provenance)
    diff_str = diff_timelines(timeline, new_timeline)
    
    new_timeline.history = list(timeline.history) if timeline.history else []
    new_timeline.extra_fields = dict(timeline.extra_fields) if timeline.extra_fields else {}
    
    now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
    new_timeline.history.append({
        "when": now_str,
        "model": model,
        "notes": notes,
        "changes": diff_str
    })

    return new_timeline


_DIRECTED_SUFFIX = re.compile(r"^(?P<base>.*)\.directed(?P<n>\d*)$")


def default_output_path(in_path: str) -> str:
    """`<in stem>.directed.json`, next to the input -- but NEVER the input
    itself. If the computed default output path already exists on disk, or matches
    the input path (case-insensitive on Windows), bump N until we find a filename
    that does not exist. This turns outputs into an implicit take history and
    prevents clobbering curated directed files:
    clip_1.json -> clip_1.directed.json -> clip_1.directed2.json -> ...
    """
    directory, filename = os.path.split(in_path)
    stem = filename[:-5] if filename.endswith(".json") else filename

    m = _DIRECTED_SUFFIX.match(stem)
    if m:
        base = m.group("base")
        n = int(m.group("n")) if m.group("n") else 1
        out_name = f"{base}.directed{n + 1}.json"
    else:
        base = stem
        out_name = f"{stem}.directed.json"

    out_path = os.path.join(directory, out_name) if directory else out_name

    # Bump until we find a filename that does not exist and doesn't equal input path
    while os.path.exists(out_path) or os.path.normcase(os.path.abspath(out_path)) == os.path.normcase(os.path.abspath(in_path)):
        stem_out = out_name[:-5] if out_name.endswith(".json") else out_name
        m_out = _DIRECTED_SUFFIX.match(stem_out)
        if m_out:
            curr_base = m_out.group("base")
            curr_n = int(m_out.group("n")) if m_out.group("n") else 1
            next_n = curr_n + 1
        else:
            curr_base = stem_out
            next_n = 2
        out_name = f"{curr_base}.directed{next_n}.json"
        out_path = os.path.join(directory, out_name) if directory else out_name

    return out_path


_FIELDS = ("start_time", "duration", "intensity", "kind")


def _float_neq(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) > 1e-9
    return a != b


def diff_timelines(old: Timeline, new: Timeline) -> str:
    """Human-readable diff between two timelines: top-level field changes,
    per-event field changes (old -> new), and added/removed events.

    Events are matched by name first (repeated names, e.g. a rhythmic
    'footsteps on gravel' split into several segments, pair off by closest
    start_time among same-named candidates). Unmatched old events are
    "removed"; unmatched new events are "added".
    """
    lines = []

    if _float_neq(old.duration, new.duration):
        lines.append(f"  EDITED: duration: {old.duration} -> {new.duration}")
    if old.ambience != new.ambience:
        lines.append(f"  EDITED: ambience: {old.ambience!r} -> {new.ambience!r}")

    old_events = list(old.events)
    new_events = list(new.events)
    unmatched_new = list(range(len(new_events)))

    for old_ev in old_events:
        candidates = [i for i in unmatched_new if new_events[i].name == old_ev.name]
        if not candidates:
            lines.append(f"  REMOVED: {old_ev.name!r} @ {old_ev.start_time}s")
            continue
        best = min(candidates, key=lambda i: abs(new_events[i].start_time - old_ev.start_time))
        unmatched_new.remove(best)
        new_ev = new_events[best]

        changes = [
            f"{field} {getattr(old_ev, field)} -> {getattr(new_ev, field)}"
            for field in _FIELDS
            if _float_neq(getattr(old_ev, field), getattr(new_ev, field))
        ]
        if changes:
            lines.append(f"  EDITED: {old_ev.name!r}: " + "; ".join(changes))

    for i in unmatched_new:
        new_ev = new_events[i]
        lines.append(
            f"  ADDED: {new_ev.name!r} @ {new_ev.start_time}s "
            f"({new_ev.kind}, {new_ev.intensity})"
        )

    if not lines:
        return "(no changes)"
    return "\n".join(lines)
