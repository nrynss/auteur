import os
import json
import re
import tempfile
import subprocess
import time
import base64
from typing import List, Optional, Tuple
from dotenv import load_dotenv

# Load env variables (for FAL_KEY)
load_dotenv()

from foley.models import Event, Timeline
from foley.ledger import execute_with_ledger
from foleyflow.providers.config import get_analysis_config

# Default VLM model — read from providers.toml [analysis] section.
# Any video-capable model behind fal's OpenRouter gateway works here, e.g.
# "google/gemini-2.5-flash", "google/gemini-3-flash-preview", "qwen/qwen3.6-flash".
DEFAULT_MODEL = get_analysis_config().get("default_vlm", "google/gemini-2.5-flash")

# Cache upload URIs so the same clip is uploaded once when several models
# analyze it (direct runs + refinement passes).
#
# TTL'd because a Gemini Files API URI expires after ~48h server-side. An
# unbounded process-lifetime cache in a long-running API server would hand back
# a dead URI and fail with an opaque error *after* spending one of 20 daily
# requests. Re-uploading is free, so a short TTL costs nothing.
_UPLOAD_URI_TTL_S = 3600.0
_upload_cache: dict = {}


def _cached_upload(key) -> Optional[str]:
    entry = _upload_cache.get(key)
    if not entry:
        return None
    uri, stored_at = entry
    if (time.monotonic() - stored_at) > _UPLOAD_URI_TTL_S:
        _upload_cache.pop(key, None)
        return None
    return uri

def upload_video(path: str, model_id: Optional[str] = None) -> str:
    """Make `path` reachable by the analysis provider, returning its URI.

    The adapter is resolved from the model that will consume the upload, not
    hardcoded: an uploaded file is provider-scoped, so a Gemini Files API URI
    is meaningless to fal and vice versa. Cached per (path, adapter) for the
    process — the same clip is uploaded once even when several models analyze
    it, but switching provider re-uploads rather than handing over a URI the
    new provider cannot read.
    """
    from foleyflow.providers import get_adapter
    from foleyflow.providers.config import get_analysis_config, get_model_spec

    model_id = model_id or get_analysis_config()["video_model"]
    adapter_name = get_model_spec(model_id).adapter

    key = (os.path.abspath(path), adapter_name)
    cached = _cached_upload(key)
    if cached:
        return cached
    print(f"Uploading {os.path.basename(path)} via the {adapter_name} adapter...")
    uri = get_adapter(adapter_name).upload(path)
    _upload_cache[key] = (uri, time.monotonic())
    return uri

TIMELINE_PROMPT = """You are a professional Foley artist analyzing a silent video.
Output ONLY valid JSON, no markdown fences, matching this schema:

{
  "duration": <float>,
  "ambience": "<one continuous background-sound description>",
  "events": [
    {
      "name": "<specific audible event — ALWAYS name the material/surface,
               e.g. 'footsteps on gravel' not 'footsteps',
               'wooden door closing' not 'door closing'>",
      "start_time": <float seconds, precise>,
      "duration": <float seconds>,
      "intensity": "low" | "med" | "high",
      "kind": "discrete" | "rhythmic" | "continuous"
    }
  ]
}

Rules:
- Only include events that would actually be audible.
- For repeated actions (walking, typing), emit ONE event with
  kind="rhythmic" spanning the interval — do not emit each repetition.
- Be conservative: if unsure what an object/material is, choose the
  most generic plausible sound source.
- Timestamps must correspond to the visible moment of impact/contact.
"""

def get_video_duration(path: str) -> float:
    """Use ffprobe to query the duration of a video file."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    return float(result.stdout.strip())

def extract_frames(video_path: str, temp_dir: str, fps: float = 2.0) -> List[Tuple[str, float]]:
    """Sample frames from the video via ffmpeg at specified fps."""
    out_pattern = os.path.join(temp_dir, "frame_%04d.jpg")
    cmd = [
        "ffmpeg",
        "-y",
        "-i", video_path,
        "-vf", f"fps={fps}",
        "-vsync", "vfr",
        out_pattern
    ]
    # Run ffmpeg, suppress output unless error
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg frame extraction failed: {result.stderr}")
    
    # List and sort extracted frames
    files = sorted([f for f in os.listdir(temp_dir) if f.startswith("frame_") and f.endswith(".jpg")])
    frames = []
    for i, filename in enumerate(files):
        frame_path = os.path.join(temp_dir, filename)
        ts = i / fps
        frames.append((frame_path, ts))
    return frames

def image_to_base64_data_uri(path: str) -> str:
    """Convert a local image to a base64 Data URI."""
    with open(path, "rb") as image_file:
        encoded = base64.b64encode(image_file.read()).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"

def clean_json_text(text: str) -> str:
    """Clean markdown code blocks and excess whitespace from LLM response."""
    # Remove markdown code block markers
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    return text.strip()

def repair_json_with_vlm(broken_json: str, error_msg: str) -> str:
    """Use Gemini via OpenRouter to repair broken JSON."""
    analysis = get_analysis_config()
    vision_model = analysis["vision_model"]

    repair_prompt = f"""Here is a string that was supposed to be valid JSON matching the schema, but failed to parse with the error: {error_msg}.
Please correct the JSON and output ONLY the valid JSON, with no markdown fences or other text.

JSON:
{broken_json}"""

    try:
        response = execute_with_ledger(
            vision_model,
            arguments={
                "image_urls": [],
                "prompt": repair_prompt,
                # No `model` override: the adapter prefers arguments["model"] over the
            # spec's id, so a fal-era "google/gemini-2.5-flash" string would be
            # sent to google-genai and 404 — killing the one recovery path for a
            # malformed response *after* the request was already billed.
            }
        )
        return clean_json_text(response.get("output") or response.get("text") or "")
    except Exception as e:
        # If repair fails, just raise original parsing error
        raise ValueError(f"JSON repair failed: {e}")

VALID_KINDS = {"discrete", "rhythmic", "continuous"}
VALID_INTENSITIES = {"low", "med", "high"}

def _validate_event(ev: dict, clip_duration: float | None) -> Event | None:
    """Validate/clamp one raw event dict. Returns None if the event must be
    dropped (missing name or start_time — garbage from a hallucinated or
    truncated response must never reach routing or the mixer)."""
    name = ev.get("name")
    if not isinstance(name, str) or not name.strip():
        print(f"Warning: dropping event with missing/empty name: {ev}")
        return None
    if ev.get("start_time") is None:
        print(f"Warning: dropping event '{name}' with missing start_time.")
        return None

    start = float(ev["start_time"])
    duration = max(0.0, float(ev.get("duration", 0.0)))

    # Clamp against the ffprobe clip duration (NOT the model-reported one).
    if start < 0.0:
        print(f"Warning: clamping negative start_time {start} -> 0.0 for '{name}'.")
        start = 0.0
    if clip_duration is not None:
        if start > clip_duration:
            print(f"Warning: clamping start_time {start} -> {clip_duration} for '{name}'.")
            start = clip_duration
        if start + duration > clip_duration:
            duration = clip_duration - start

    kind = str(ev.get("kind", "discrete")).strip().lower()
    if kind not in VALID_KINDS:
        print(f"Warning: unknown kind '{ev.get('kind')}' for '{name}'; defaulting to 'discrete'.")
        kind = "discrete"
    intensity = str(ev.get("intensity", "med")).strip().lower()
    if intensity not in VALID_INTENSITIES:
        print(f"Warning: unknown intensity '{ev.get('intensity')}' for '{name}'; defaulting to 'med'.")
        intensity = "med"

    return Event(name=name.strip(), start_time=start, duration=duration,
                 intensity=intensity, kind=kind)

def parse_timeline_json(text: str, clip_duration: float | None = None) -> Timeline:
    """Parse JSON text into Timeline and Event dataclasses, with one repair retry.

    Events are validated/clamped at parse time: nameless events and events
    without a start_time are dropped; start_time is clamped to
    [0, clip_duration] and event end to clip end; kind/intensity are
    normalized to known enums with a warning on fallback.
    clip_duration must be the ffprobe duration (get_video_duration) — the
    model's reported duration is never trusted for clamping.
    """
    cleaned = clean_json_text(text)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as err:
        # Attempt repair once
        print(f"Warning: JSON decoding failed ({err}). Attempting VLM repair...")
        try:
            repaired_text = repair_json_with_vlm(cleaned, str(err))
            data = json.loads(repaired_text)
        except Exception as repair_err:
            raise ValueError(f"Failed to parse and repair JSON timeline: {repair_err}") from err

    # Construct the dataclasses (dropping/clamping invalid events)
    events = []
    for ev in data.get("events", []):
        validated = _validate_event(ev, clip_duration)
        if validated is not None:
            events.append(validated)

    return Timeline(
        duration=float(data.get("duration", 0.0)),
        ambience=data.get("ambience", ""),
        events=events
    )

def analyze_video_direct(path: str, model: str = DEFAULT_MODEL, extra_args: dict = None) -> Timeline:
    """Primary path: Send the video file directly to a video-capable VLM via
    the configured video analysis gateway. extra_args lets callers pass gateway
    options (e.g. {"reasoning": True} — mandatory for the Gemini Pro models)."""
    analysis = get_analysis_config()
    # The model the caller asked for IS the endpoint to bill and count against.
    # Under fal's gateway these were genuinely different — the endpoint was the
    # router, `model` selected what sat behind it — but calling Google directly
    # collapses them. Passing the configured default here while actually
    # invoking `model` would file every request under the wrong model, and the
    # quota panel would then report the wrong budget as spent. On 20/day that
    # is not a cosmetic error.
    effective_model = model or analysis["video_model"]

    duration = get_video_duration(path)
    video_url = upload_video(path, model_id=effective_model)

    prompt = TIMELINE_PROMPT + f"\nNote: The video duration is exactly {duration:.3f} seconds."

    # Docs specify `video_urls` (list); the singular form is accepted in practice
    # (verified via ledger success entries) but stick to the documented schema.
    arguments = {
        "video_urls": [video_url],
        "prompt": prompt,
        "model": effective_model
    }
    if extra_args:
        arguments.update(extra_args)
    response = execute_with_ledger(effective_model, arguments=arguments)

    response_text = response.get("output") or response.get("text") or ""
    timeline = parse_timeline_json(response_text, clip_duration=duration)
    timeline.duration = duration  # ffprobe is the sole source of truth
    return timeline

def analyze_video_fal(path: str, endpoint: str, extra_args: dict = None) -> Timeline:
    """Run the timeline prompt against a native fal video endpoint with the
    common {video_url, prompt} -> {output} shape."""
    duration = get_video_duration(path)
    video_url = upload_video(path)
    prompt = TIMELINE_PROMPT + f"\nNote: The video duration is exactly {duration:.3f} seconds."
    args = {"video_url": video_url, "prompt": prompt}
    if extra_args:
        args.update(extra_args)
    response = execute_with_ledger(endpoint, arguments=args)
    timeline = parse_timeline_json(response.get("output") or response.get("text") or "",
                                   clip_duration=duration)
    timeline.duration = duration  # ffprobe is the sole source of truth
    return timeline

def find_event_span(video_path_or_url: str, query: str):
    """Temporal localization via the localization model — a 2B video VLM tuned
    for 'what happened and when'. Returns (start, end) in seconds, or None if
    the event was not found. Intended as a refinement pass: the timeline VLM
    names the events, the localizer pins each discrete event's moment.
    """
    analysis = get_analysis_config()
    localization_model = analysis["localization_model"]

    video_url = (video_path_or_url if video_path_or_url.startswith("http")
                 else upload_video(video_path_or_url))
    response = execute_with_ledger(
        localization_model,
        arguments={"video_url": video_url, "prompt": query}
    )
    span = response.get("span")
    # Schema says [EventSegment]; the documented example shows a single object.
    if isinstance(span, list):
        span = span[0] if span else None
    if isinstance(span, dict) and "start" in span and "end" in span:
        return float(span["start"]), float(span["end"])
    return None

def analyze_video_sampled(path: str, model: str = DEFAULT_MODEL) -> Timeline:
    """Fallback path: Extract frames at 2 fps and send to the vision gateway."""
    analysis = get_analysis_config()
    vision_model = analysis["vision_model"]

    # 1. Get video duration
    duration = get_video_duration(path)
    
    # 2. Extract frames
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"Extracting frames from {os.path.basename(path)}...")
        frames = extract_frames(path, temp_dir, fps=2.0)
        
        # Prepare base64 image URIs and construct prompt with timestamps
        image_uris = []
        timestamps_desc = []
        for idx, (frame_path, ts) in enumerate(frames):
            image_uris.append(image_to_base64_data_uri(frame_path))
            timestamps_desc.append(f"- Image {idx+1}: timestamp = {ts:.2f} seconds")
            
        print(f"Extracted {len(frames)} frames. Sending to {vision_model}...")
        
        # Construct detailed frame prompt
        time_desc_str = "\n".join(timestamps_desc)
        prompt = f"""You are analyzing a sequence of images representing frames sampled from a silent video at a rate of 2 frames per second.
The frames correspond to the following timestamps:
{time_desc_str}

Please generate the Foley timeline matching the requested schema.
Video duration is exactly {duration:.3f} seconds.

{TIMELINE_PROMPT}"""

        response = execute_with_ledger(
            vision_model,
            arguments={
                "image_urls": image_uris,
                "prompt": prompt,
                "model": model
            }
        )
        
        response_text = response.get("output") or response.get("text") or ""
        timeline = parse_timeline_json(response_text, clip_duration=duration)
        timeline.duration = duration  # ffprobe is the sole source of truth
        return timeline

def analyze_video(path: str, strategy: str = "direct", model: str = DEFAULT_MODEL) -> Timeline:
    """
    Analyze a silent video and return a structured Timeline.

    The PRODUCT PATH is 'direct' (the default): direct video upload to the
    VLM, failing loud on any error — it NEVER falls back silently to frame
    sampling (the spike proved sampled cannot see short discrete events).
    'sampled' and 'auto' are explicit opt-ins for research only.

    strategy:
      'direct'  — direct video upload to the VLM; raises on failure. (default)
      'sampled' — frame sampling; raises on failure. Research only.
      'auto'    — direct first, loud fallback to sampled. Research only.
    model: any video-capable (direct) or vision-capable (sampled) model id
    behind fal's OpenRouter gateway.
    Explicit strategies never fall back silently: measurements labeled 'direct'
    must actually come from the direct path.
    """
    if strategy == "direct":
        return analyze_video_direct(path, model=model)
    if strategy == "sampled":
        return analyze_video_sampled(path, model=model)
    try:
        return analyze_video_direct(path, model=model)
    except Exception as e:
        print(f"WARNING: direct video strategy failed ({e}); falling back to frame sampling.")
        return analyze_video_sampled(path, model=model)
