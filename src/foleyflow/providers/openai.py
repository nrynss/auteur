"""OpenAI adapter — vision analysis for Foley timeline generation.

OpenAI's Chat Completions models (``gpt-4o``, ``gpt-4o-mini``, …) take images,
not raw video. When analysis hands us a video path we sample frames with ffmpeg
and send those as a vision request — the same TIMELINE_PROMPT the Gemini path
uses, so the two providers are interchangeable at the product layer.

## Why this is not genblaze-openai

``genblaze-openai`` ships Sora / DALL·E / TTS providers (media generation). Chat
is a standalone helper outside the Pipeline machinery. Auteur's analysis
path needs the same Adapter protocol as Google/fal so Genblaze can lift it the
same way — cost ledger, config, cache key — rather than a second code path.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import tempfile
from typing import Any, List, Optional
from urllib.parse import urlparse
from urllib.request import url2pathname

from dotenv import load_dotenv

from foleyflow.providers.base import GenerationResult, ModelSpec

load_dotenv()

DEFAULT_API_KEY_ENV = ("OPENAI_API_KEY",)
# Frame rate for video → vision. Matches analyze.extract_frames default so the
# OpenAI path and the sampled Gemini path see the same temporal density.
VIDEO_SAMPLE_FPS = 2.0
# Cap request size: a 30s clip at 2fps would be ~60 full-res images and a
# multi-MB body. Scale and a hard frame ceiling keep cost and latency bounded
# for the primary OpenAI analysis path (unlike Gemini's research-only
# sampled strategy).
VIDEO_FRAME_SCALE = "640:-2"
VIDEO_MAX_FRAMES = 24


class OpenAIAdapter:
    """Adapter for OpenAI Chat Completions (vision-capable models)."""

    name: str = "openai"

    def __init__(self, api_key: Optional[str] = None,
                 api_key_env: "str | tuple[str, ...] | list[str]" = DEFAULT_API_KEY_ENV):
        self._api_key = api_key
        self._api_key_env = (
            (api_key_env,) if isinstance(api_key_env, str) else tuple(api_key_env)
        )
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        key = self._api_key
        if not key:
            for name in self._api_key_env:
                key = os.environ.get(name)
                if key:
                    break
        if not key:
            names = " or ".join(self._api_key_env)
            raise RuntimeError(
                f"{names} is unset — create a key at "
                "https://platform.openai.com/api-keys and add it to .env"
            )
        from openai import OpenAI
        self._client = OpenAI(api_key=key)
        return self._client

    def upload(self, local_path: str) -> str:
        """Return a file:// URL for a local media file.

        OpenAI vision does not use a Files API URI the way Gemini does: images
        travel as data URLs or https URLs on the message. Returning file://
        keeps the analyze upload cache working (per-adapter keying) and lets
        generate() resolve the local path when a video needs frame sampling.
        """
        return f"file://{os.path.abspath(local_path)}"

    def generate(self, spec: ModelSpec, arguments: dict,
                 method: str = "subscribe", **kwargs) -> GenerationResult:
        """Run a multimodal chat completion.

        ``arguments`` matches the shape analyze.py already builds for Google:
          video_urls / image_urls : list of URIs (file://, data:, or https)
          prompt                  : the text prompt
          model                   : optional model id override
        """
        client = self._get_client()
        model = arguments.get("model") or spec.model_id
        prompt = arguments.get("prompt") or ""

        content: List[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for uri in arguments.get("image_urls") or []:
            content.append(_image_part(uri))
        # Accept both the plural list analyze_video_direct builds and the
        # singular form used by the retired fal/localizer helpers, so reviving
        # those call sites cannot silently drop the video.
        video_uris = list(arguments.get("video_urls") or [])
        if arguments.get("video_url"):
            video_uris.append(arguments["video_url"])
        for uri in video_uris:
            # OpenAI has no native video part; sample frames and attach them.
            content.extend(_video_as_image_parts(uri))

        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
        )
        text = ""
        if response.choices:
            text = response.choices[0].message.content or ""

        actual_cost = None
        usage = getattr(response, "usage", None)
        # OpenAI does not put dollars on the response; leave cost as estimate.

        return GenerationResult(
            raw_response={
                "output": text,
                "model": model,
                "usage": usage.model_dump() if usage and hasattr(usage, "model_dump")
                else None,
            },
            actual_cost_usd=actual_cost,
        )


def _local_path(uri: str) -> Optional[str]:
    """Resolve a file:// URI (or bare path) to a local filesystem path."""
    if uri.startswith("file://"):
        return url2pathname(urlparse(uri).path)
    if uri.startswith(("http://", "https://", "data:")):
        return None
    if os.path.exists(uri):
        return uri
    return None


def _image_part(uri: str) -> dict:
    """One OpenAI vision image content block."""
    if uri.startswith("data:") or uri.startswith(("http://", "https://")):
        return {"type": "image_url", "image_url": {"url": uri}}
    path = _local_path(uri)
    if path is None:
        raise ValueError(f"cannot resolve image URI for OpenAI: {uri!r}")
    return {"type": "image_url", "image_url": {"url": _file_as_data_uri(path)}}


def _file_as_data_uri(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    mime = mime or "image/jpeg"
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _extract_scaled_frames(video_path: str, temp_dir: str,
                           fps: float = VIDEO_SAMPLE_FPS) -> List[tuple]:
    """Sample frames at ``fps``, scaled to VIDEO_FRAME_SCALE, oldest first.

    Sibling of ``analyze.extract_frames`` with an explicit scale filter so the
    OpenAI request body stays bounded. Returns ``(path, timestamp_s)`` pairs.
    """
    import subprocess

    out_pattern = os.path.join(temp_dir, "frame_%04d.jpg")
    vf = f"fps={fps},scale={VIDEO_FRAME_SCALE}"
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", vf, "-vsync", "vfr", out_pattern,
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg frame extraction failed: {result.stderr}")

    files = sorted(
        f for f in os.listdir(temp_dir)
        if f.startswith("frame_") and f.endswith(".jpg")
    )
    return [
        (os.path.join(temp_dir, name), i / fps)
        for i, name in enumerate(files)
    ]


def _video_as_image_parts(uri: str) -> List[dict]:
    """Sample a video at VIDEO_SAMPLE_FPS and return vision image parts.

    OpenAI chat models do not accept video parts. Frame sampling is the only
    way to run the same TIMELINE_PROMPT against an OpenAI model.
    """
    path = _local_path(uri)
    if path is None:
        raise ValueError(
            f"OpenAI analysis needs a local video path to sample frames; "
            f"got {uri!r}"
        )
    parts: List[dict] = []
    with tempfile.TemporaryDirectory(prefix="foleyflow-openai-") as tmp:
        frames = _extract_scaled_frames(path, tmp, fps=VIDEO_SAMPLE_FPS)
        if len(frames) > VIDEO_MAX_FRAMES:
            # Evenly spaced subset so a long clip still covers start and end.
            step = len(frames) / VIDEO_MAX_FRAMES
            frames = [frames[int(i * step)] for i in range(VIDEO_MAX_FRAMES)]
        for frame_path, ts in frames:
            parts.append({
                "type": "text",
                "text": f"[frame at {ts:.2f}s]",
            })
            parts.append({
                "type": "image_url",
                "image_url": {"url": _file_as_data_uri(frame_path)},
            })
    if not parts:
        raise RuntimeError(
            f"ffmpeg extracted no frames from {os.path.basename(path)}"
        )
    return parts
