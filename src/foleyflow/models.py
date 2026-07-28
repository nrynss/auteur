from dataclasses import dataclass, field
from typing import List, Dict, Any

@dataclass
class Event:
    name: str          # e.g., "footstep on gravel" (surface/material mandatory)
    start_time: float  # seconds
    duration: float    # seconds
    intensity: str     # low | med | high
    kind: str          # discrete | rhythmic | continuous

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
