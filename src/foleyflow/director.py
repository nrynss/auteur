"""Director mode (unit 7.12, docs/api.md §3.16).

**Chat proposes, never applies.** The client sends the current timeline
document and a plain-language instruction; this module asks the director model
for a proposed document, schema-validates it, and diffs it against what was
sent. Nothing is written here: the endpoint never allocates a version and never
claims a job slot. Approval is the client committing the proposed document
through the normal `PUT /timeline` path, which owns every freeze and run-lock
rule.

## The model output is untrusted

The director model (`gemini-3.5-flash-lite`, decision 9) returns a JSON list of
placements. Every value is validated and clamped server-side, and the response
carries the outcome as a `status`, not an HTTP error: `proposed`, `no_changes`,
or `invalid_output`. A turn that produces garbage is still billed and still
counts against `/quota` — the call happened — so the UI must be able to say
exactly that rather than pretend the turn never cost anything.

## Placement-only scope

The model may change `start_time` and `duration` — the free edits that window
the clips that already exist — and nothing else. It cannot add, remove, rename
or re-key events, and it cannot touch `seed`/`source_duration_s`. The proposal
always carries exactly the request document's events, in the same order, so the
diff is per-index old → new and the commit that eventually writes it conserves
every freeze by construction (the freezes were copied through untouched).
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional

from foleyflow.analyze import clean_json_text
from foleyflow.ledger import estimate_cost, execute_with_ledger, get_actual_cost
from foleyflow.models import Event, Timeline
from foleyflow.providers.config import get_analysis_config
from foleyflow.timeline_io import timeline_to_dict

# The model that reads the instruction and proposes placements. Declared in
# providers.toml [analysis] so deploy can re-aim it; the [models] entry owns
# quota accounting (500/day — the reason flash-lite, decision 9) and pricing.
DEFAULT_DIRECTOR_MODEL = "gemini-3.5-flash-lite"

# The smallest span a proposal may produce — mirrors the draft's working
# resolution (MIN_DURATION_S in the UI) so an approved proposal applies
# cleanly. Server-side the number is ours, not the model's.
MIN_PROPOSED_DURATION_S = 0.01

# Comparison epsilon for "did the proposal change this event".
_PLACEMENT_EPS = 1e-9


def director_model() -> str:
    """The model id for director turns (providers.toml [analysis])."""
    return get_analysis_config().get("director_model", DEFAULT_DIRECTOR_MODEL)


def build_prompt(instruction: str, timeline_json: str,
                 clip_duration: Optional[float]) -> str:
    """The text-only completion prompt. The model is told to return a *complete*
    placement list, not a patch: a version is a document (decision A.2.11), so
    proposing a document diff needs the whole document to diff against. The
    output schema is deliberately narrow — index, start_time, duration — so a
    hallucinated rename or a dropped event is a validation failure, not a
    silently applied edit."""
    duration_line = (
        f"The clip is {clip_duration:.3f}s long." if clip_duration is not None
        else "The clip duration is the timeline's top-level duration."
    )
    return f"""You are Auteur's director assistant, proposing timeline edits for a film
sound designer. You NEVER apply anything — you return a proposed document for
the designer to review.

Each event has:
- index: its array position — the ONLY stable identifier (names repeat)
- name: what the sound is
- kind: one of: discrete, rhythmic, continuous
- intensity: one of: low, med, high
- start_time: seconds
- duration: seconds
- seed / source_duration_s: frozen values you must copy through UNCHANGED

{duration_line}

The designer's instruction:
{instruction}

Return ONLY a JSON object of the form:
{{"events": [{{"index": <int>, "start_time": <float>, "duration": <float>}}, ...]}}

Rules:
- Include EVERY index exactly once, in the SAME ORDER as the input.
- Change start_time / duration only where the instruction requires.
- start_time must be >= 0.0; duration must be > 0.0. Times are seconds.
- Do not invent, drop, duplicate, rename or reorder events.
- Do not add fields. Do not add commentary outside the JSON.

Current timeline JSON:
{timeline_json}"""


def _clamp_placement(start_time: float, duration: float,
                     clip_duration: Optional[float]) -> tuple:
    """Clamp a proposed placement to what a commit accepts, mirroring the
    draft's own clamps (move clamps to [0, clip]; resize keeps a minimum span
    inside the clip) so an approved proposal applies without further
    adjustment. Clamping is honest: the proposal is still rendered as a diff
    and reviewed before anything is applied."""
    if clip_duration is None or not math.isfinite(clip_duration):
        clip = float("inf")
    else:
        clip = max(0.0, clip_duration)
    start = min(max(start_time, 0.0), clip)
    end = min(start + max(duration, 0.0), clip)
    span = max(MIN_PROPOSED_DURATION_S, end - start)
    # Round to the working resolution (the draft's finest keyboard nudge is
    # 1 ms) so the diff is deterministic and the document stays clean —
    # float subtraction like 3.96 + 0.4 − 3.96 must not leak a trailing
    # 0.00000000000000036 into an immutable proposal.
    return round(start, 3), round(span, 3)


def _edits_for(events: List[Event], proposed: List[Event]) -> List[dict]:
    """Per-index old → new placement pairs for changed events. Only events
    whose start_time or duration actually moved appear; everything else is
    untouched and needs no review."""
    edits: List[dict] = []
    for i, (old_ev, new_ev) in enumerate(zip(events, proposed)):
        moved = abs(old_ev.start_time - new_ev.start_time) > _PLACEMENT_EPS
        resized = abs(old_ev.duration - new_ev.duration) > _PLACEMENT_EPS
        if moved or resized:
            edits.append({
                "index": i,
                "old": {"start_time": old_ev.start_time,
                        "duration": old_ev.duration},
                "new": {"start_time": new_ev.start_time,
                        "duration": new_ev.duration},
            })
    return edits


def _message(edits: List[dict]) -> str:
    """A terse human summary: "moved 2 events" / "resized 1 event" /
    "moved 1 event and resized 1"."""
    moved = sum(1 for e in edits
                if abs(e["old"]["start_time"] - e["new"]["start_time"]) > _PLACEMENT_EPS)
    resized = sum(1 for e in edits
                  if abs(e["old"]["duration"] - e["new"]["duration"]) > _PLACEMENT_EPS)
    parts = []
    if moved:
        parts.append(f"moved {moved} {_plural(moved, 'event')}")
    if resized:
        parts.append(f"resized {resized} {_plural(resized, 'event')}")
    if not parts:
        return "The model proposed no changes."
    return " and ".join(parts) + " — free to apply; approve to fork your draft."


def _plural(n: int, word: str) -> str:
    return word if n == 1 else f"{word}s"


def parse_proposal_output(text: str) -> Optional[List[dict]]:
    """Parse the model's text into a list of raw placement dicts.

    Returns None when the text is not a JSON object with an `events` list —
    the turn is `invalid_output`, billed and counted like any other. Markdown
    fences and surrounding prose are tolerated (clean_json_text); a
    hallucinated shape is not.
    """
    cleaned = clean_json_text(text)
    if not cleaned:
        return None
    try:
        payload = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    events = payload.get("events")
    if not isinstance(events, list):
        return None
    return events


def validate_proposal(timeline: Timeline, raw_events: List[dict],
                      clip_duration: Optional[float]) -> tuple:
    """Validate the model's placements against the base document.

    Returns ``(status, proposed_events, edits)`` where status is
    ``proposed`` / ``no_changes`` / ``invalid_output``. Validation is strict
    about the *shape* (the model must echo every index in order — a dropped or
    invented event is a hallucination, not a deletion the director asked for)
    and forgiving about the *numbers* (a −0.001s slip is clamped, then shown
    in the diff). Freezes are copied through untouched, so the proposal can
    never move a cache address or invent a clip nobody has paid for.
    """
    if len(raw_events) != len(timeline.events):
        return "invalid_output", list(timeline.events), []

    proposed: List[Event] = []
    for i, base in enumerate(timeline.events):
        raw = raw_events[i]
        if not isinstance(raw, dict):
            return "invalid_output", list(timeline.events), []
        try:
            index = int(raw.get("index"))
            start = float(raw.get("start_time"))
            duration = float(raw.get("duration"))
        except (TypeError, ValueError):
            return "invalid_output", list(timeline.events), []
        if index != i:
            return "invalid_output", list(timeline.events), []
        if not math.isfinite(start) or not math.isfinite(duration):
            return "invalid_output", list(timeline.events), []
        start, span = _clamp_placement(start, duration, clip_duration)
        proposed.append(Event(
            name=base.name,
            start_time=start,
            duration=span,
            intensity=base.intensity,
            kind=base.kind,
            seed=base.seed,
            source_duration_s=base.source_duration_s,
        ))

    edits = _edits_for(timeline.events, proposed)
    status = "proposed" if edits else "no_changes"
    return status, proposed, edits


def propose(instruction: str, timeline: Timeline,
            clip_duration: Optional[float] = None) -> dict:
    """One director turn: call the model through the ledger, validate, diff.

    Returns the response document (docs/api.md §3.16) with ``status``,
    ``timeline`` (proposed), ``edits``, cost figures. Never writes storage —
    the caller owns the project context (``set_project_context``) so the ledger
    row lands attributed to the project that asked.
    """
    model = director_model()
    timeline_json = json.dumps(timeline_to_dict(timeline), indent=1)
    prompt = build_prompt(instruction, timeline_json, clip_duration)
    arguments: Dict[str, Any] = {"image_urls": [], "prompt": prompt, "model": model}
    estimated = estimate_cost(model, arguments)

    raw = execute_with_ledger(model, arguments)
    text = raw.get("output") or raw.get("text") or ""

    raw_events = parse_proposal_output(text)
    if raw_events is None:
        return {
            "status": "invalid_output",
            "message": "The model returned an unparseable proposal — nothing "
                       "was applied and the turn was still billed.",
            "timeline": timeline,
            "edits": [],
            # The call happened; the row (if it landed) carries this figure.
            "cost_usd": estimated,
            "cost_source": "estimate",
        }

    status, proposed, edits = validate_proposal(timeline, raw_events,
                                                clip_duration)
    actual = get_actual_cost(raw)
    return {
        "status": status,
        "message": _message(edits),
        "timeline": Timeline(duration=timeline.duration,
                             ambience=timeline.ambience,
                             events=proposed,
                             history=list(timeline.history),
                             extra_fields=dict(timeline.extra_fields)),
        "edits": edits,
        "cost_usd": actual if actual is not None else estimated,
        "cost_source": "actual" if actual is not None else "estimate",
    }
