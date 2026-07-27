"""Event/ambience -> SfxRequest routing.

Data-driven via routing.yaml (repo root) so routes are editable without code.
Zero API calls here — this module never imports fal_client; it only decides
*where* and *what* to ask for. T2 (sfx.py) does the asking.
"""

import os
from dataclasses import dataclass
from functools import lru_cache

import yaml

from foley.keywords import has_word
from foley.models import Event

ROUTING_YAML_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "routing.yaml")
)

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

# Per-endpoint min/max, verified against the fal API docs at T2
# (https://fal.ai/models/<endpoint>/api). Clamping prints a warning and
# returns the clamped value; never raises.
DURATION_LIMITS = {
    # docs: integer seconds, required; page prose "generates high-quality SFX
    # up to 30 seconds long" (example shows duration: 30). Schema states no
    # formal minimum. # unverified: lower bound (guessed 1s, integer >= 1).
    "cassetteai/sound-effects-generator": (1.0, 30.0),
    # docs: "Duration in seconds (0.5-22). If None, optimal duration will be
    # determined from prompt." — explicit range in the Input Schema.
    "fal-ai/elevenlabs/sound-effects/v2": (0.5, 22.0),
    # docs: duration is an optional float, default 8; no min/max documented
    # anywhere on the page. # unverified: conservative guess matching the
    # other two endpoints' order of magnitude.
    "fal-ai/mmaudio-v2/text-to-audio": (1.0, 30.0),
}
DEFAULT_DURATION_LIMITS = (1.0, 22.0)


@dataclass
class SfxRequest:
    endpoint: str
    prompt: str
    duration_s: float
    category: str  # for logging/demo output


@lru_cache(maxsize=1)
def _load_routes() -> dict:
    with open(ROUTING_YAML_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _categorize(name: str) -> tuple:
    """First keyword-matching category wins (ordered list in routing.yaml);
    falls back to the yaml `default` entry."""
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
    lo, hi = DURATION_LIMITS.get(endpoint, DEFAULT_DURATION_LIMITS)
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
    ambience_cat = next(c for c in routes["categories"] if c["name"] == "ambience")
    endpoint = ambience_cat["endpoint"]
    duration_s = _clamp_duration(endpoint, clip_duration + HEADROOM_S)
    return SfxRequest(endpoint=endpoint, prompt=ambience, duration_s=duration_s, category="ambience")
