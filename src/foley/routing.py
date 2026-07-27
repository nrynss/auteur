"""Event/ambience -> SfxRequest routing.

Data-driven via providers.toml [routing] section so routes are editable
without code. Zero API calls here — this module never imports fal_client;
it only decides *where* and *what* to ask for. T2 (sfx.py) does the asking.
"""

import os
from dataclasses import dataclass
from functools import lru_cache

from foley.keywords import has_word
from foley.models import Event

# Numeric defaults (locked) — build_spec.md "Intensity -> prompt qualifier".
INTENSITY_QUALIFIERS = {
    "low": "soft, distant",
    "med": "",
    "high": "close, loud, sharp",
}

# build_spec.md: discrete/continuous events get event.duration + this headroom.
HEADROOM_S = 0.5

# build_spec.md: rhythmic events request ONE loopable segment in [1.5, 2.0]s,
# not the full interval — the assembler (T3) tiles it. Picked mid-range.
RHYTHMIC_LOOP_S = 1.75


@dataclass
class SfxRequest:
    endpoint: str
    prompt: str
    duration_s: float
    category: str  # for logging/demo output


def _load_routes() -> dict:
    """Load routes from providers.toml [routing] section."""
    from foleyflow.providers.config import get_routing_config
    return get_routing_config()


def _get_duration_limits(endpoint: str) -> tuple:
    """Read duration bounds from providers.toml for the given endpoint.
    Falls back to [defaults] if the endpoint is unknown or has no bounds."""
    from foleyflow.providers.config import get_defaults, get_model_spec
    try:
        spec = get_model_spec(endpoint)
        lo = spec.duration_min_s
        hi = spec.duration_max_s
        if lo is not None and hi is not None:
            return (lo, hi)
    except KeyError:
        pass
    defaults = get_defaults()
    return (defaults.get("duration_min_s", 1.0), defaults.get("duration_max_s", 22.0))


def _categorize(name: str) -> tuple:
    """First keyword-matching category wins (ordered list in providers.toml);
    falls back to the 'default' entry."""
    routes = _load_routes()
    name_l = name.lower()
    for cat in routes["categories"]:
        for kw in cat["keywords"]:
            if has_word(name_l, kw):
                return cat["name"], cat["endpoint"]
    default = routes["default"]
    return default["name"], default["endpoint"]


def _apply_intensity(prompt: str, intensity: str) -> str:
    qualifier = INTENSITY_QUALIFIERS.get(intensity, "")
    return f"{prompt}, {qualifier}" if qualifier else prompt


def _clamp_duration(endpoint: str, duration_s: float) -> float:
    lo, hi = _get_duration_limits(endpoint)
    if duration_s < lo:
        print(f"[routing] WARNING: {endpoint} duration {duration_s:.2f}s below min "
              f"{lo}s — clamping up.")
        return lo
    if duration_s > hi:
        print(f"[routing] WARNING: {endpoint} duration {duration_s:.2f}s above max "
              f"{hi}s — clamping down.")
        return hi
    return duration_s


def route_event(event: Event) -> SfxRequest:
    category, endpoint = _categorize(event.name)
    prompt = _apply_intensity(event.name, event.intensity)

    if event.kind == "rhythmic":
        # One loopable segment, not the full interval — T3 tiles it.
        duration_s = RHYTHMIC_LOOP_S
    else:
        duration_s = event.duration + HEADROOM_S

    duration_s = _clamp_duration(endpoint, duration_s)
    return SfxRequest(endpoint=endpoint, prompt=prompt, duration_s=duration_s, category=category)


def route_ambience(ambience: str, clip_duration: float) -> SfxRequest:
    routes = _load_routes()
    ambience_cat = next(
        (c for c in routes["categories"] if c["name"] == "ambience"),
        None,
    )
    if ambience_cat is None:
        raise ValueError(
            "providers.toml: routing categories must include an 'ambience' category"
        )
    endpoint = ambience_cat["endpoint"]
    duration_s = _clamp_duration(endpoint, clip_duration + HEADROOM_S)
    return SfxRequest(endpoint=endpoint, prompt=ambience, duration_s=duration_s, category="ambience")
