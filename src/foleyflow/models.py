from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class Event:
    name: str          # e.g., "footstep on gravel" (surface/material mandatory)
    start_time: float  # seconds
    duration: float    # seconds
    intensity: str     # low | med | high
    kind: str          # discrete | rhythmic | continuous
    # Frozen mix seed for rhythmic per-tile gain jitter. None = compute from
    # name+start_time (legacy, byte-identical to pre-seed timelines). When set,
    # nudging start_time keeps the same jitter texture — only placement shifts.
    seed: Optional[int] = None
    # What was actually requested/generated from SFX — part of the cache key
    # (endpoint, prompt, duration, variant). None = legacy: discrete/continuous
    # request event.duration + HEADROOM_S; rhythmic requests RHYTHMIC_LOOP_S.
    # When set, placement duration may change freely without re-billing.
    source_duration_s: Optional[float] = None
    # The stem's storage address (cache object key) this event's audio lives
    # at, stamped at mix time from the run's own inventory (unit 7.9). The
    # browser preview uses it to fetch the right clip; names repeat and stems
    # dedupe, so this is the only honest event→audio link. None = never mixed
    # (or the mix predates stamping).
    stem_key: Optional[str] = None

@dataclass
class Timeline:
    """Structured Foley timeline for one clip.

    `duration` is the model-reported clip length and is DISPLAY-ONLY.
    The sole source of truth for mix length and mux is the ffprobe duration
    (analyze.get_video_duration); the analyze paths overwrite this field with
    the ffprobe value after parse. Downstream code (assembly, mux) must never
    size buffers from a duration that did not come from ffprobe.
    """
    duration: float
    ambience: str      # background-sound description
    events: List[Event]
    history: List[Dict[str, Any]] = field(default_factory=list)
    extra_fields: Dict[str, Any] = field(default_factory=dict)
