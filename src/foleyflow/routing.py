"""Event/ambience -> SfxRequest routing.

Data-driven via providers.toml [routing] section so routes are editable
without code. Zero API calls here — this module never imports fal_client;
it only decides *where* and *what* to ask for. T2 (sfx.py) does the asking.

## Categories and tiers are different questions

A routing *category* answers "what kind of sound is this" — it names the
request in provenance and the UI, and it is the seam a per-event hero override
selects on. A *tier* answers "which endpoint serves it" (§9.1). Keeping them
separate is why every category can share one endpoint today without losing the
distinction, and why flipping to the paid tier is one config key rather than an
edit to every category.

`route_event` therefore resolves the category first and then hands the
category's endpoint to `tiers.resolve_endpoint` as a default. With no
`[sfx_tiers]` table the default wins and routing behaves exactly as it did
before tiers existed.
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import List

from foleyflow.keywords import has_word
from foleyflow.models import Event

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
    # Which §9.1 tier chose `endpoint`, when a tier table is configured.
    # Reporting only — it is deliberately NOT part of the cache key, because
    # `endpoint` already is and two tiers naming the same endpoint would
    # otherwise generate the same audio twice under different addresses.
    tier: str = ""


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


def clamp_duration(endpoint: str, duration_s: float) -> float:
    """Clamp a requested duration to the endpoint's declared bounds.

    Public because the repair route re-aims a request at an alternative tier
    and must clamp against *that* tier's endpoint (bounds differ between
    providers), the same rule `route_event` uses internally.
    """
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


def _tiered(endpoint: str) -> tuple:
    """(endpoint, tier_name) after applying the active §9.1 tier.

    Clamping happens *after* this, against whichever endpoint won: the tiers
    have different duration bounds (MOSS 0.3–30s, fal-ElevenLabs 0.5–22s) and
    clamping to the category's bounds would produce a request the selected
    endpoint rejects.
    """
    from foleyflow import tiers

    resolved = tiers.resolve_endpoint(endpoint)
    return resolved, (tiers.tier_for_endpoint(resolved) or "")


def route_event(event: Event) -> SfxRequest:
    category, endpoint = _categorize(event.name)
    endpoint, tier = _tiered(endpoint)
    prompt = _apply_intensity(event.name, event.intensity)

    if event.kind == "rhythmic":
        # One loopable segment, not the full interval — T3 tiles it.
        duration_s = RHYTHMIC_LOOP_S
    else:
        duration_s = event.duration + HEADROOM_S

    duration_s = clamp_duration(endpoint, duration_s)
    return SfxRequest(endpoint=endpoint, prompt=prompt, duration_s=duration_s,
                      category=category, tier=tier)


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
    endpoint, tier = _tiered(ambience_cat["endpoint"])
    duration_s = clamp_duration(endpoint, clip_duration + HEADROOM_S)
    return SfxRequest(endpoint=endpoint, prompt=ambience, duration_s=duration_s,
                      category="ambience", tier=tier)


def escalate(request: SfxRequest) -> List[SfxRequest]:
    """The same request re-aimed at each fallback tier, in escalation order.

    Returns `[]` when the request's tier has no fallback (or no tier table is
    configured), which is the offline default — see providers.toml's note on
    why the free local tier deliberately does not escalate to a paid API.

    Each variant is re-clamped against its own endpoint's bounds, so a 25s
    ambience bed generated locally becomes a 22s request when it escalates to
    fal-ElevenLabs rather than being rejected. That changes the cache key, which
    is correct: it is a different clip from a different model.
    """
    from foleyflow import tiers

    if not request.tier:
        return []
    chain = tiers.tier_chain(request.tier)[1:]
    out: List[SfxRequest] = []
    for tier in chain:
        out.append(SfxRequest(
            endpoint=tier.endpoint,
            prompt=request.prompt,
            duration_s=clamp_duration(tier.endpoint, request.duration_s),
            category=request.category,
            tier=tier.name,
        ))
    return out
