"""Google Gemini adapter — video and image understanding for scene analysis.

Replaces the fal route for analysis. FoleyFlow was already calling
`google/gemini-2.5-flash`; fal's OpenRouter gateway was only a proxy in front
of it. Talking to Google directly removes a dependency, a middleman's pricing,
and a credit balance we no longer have — with no change to the model, so the
tuned TIMELINE_PROMPT and its validated event timings carry over untouched.

## Why the upload step survives

`upload()` exists because Gemini cannot fetch arbitrary URLs. Its `file_data`
part accepts a Files API URI or a YouTube link and nothing else, so a presigned
B2 URL will not work here even though the source video is already in B2 after
ingest. The upload therefore moves from fal's CDN to Gemini's Files API rather
than disappearing.

Files API specifics that matter:
  * An uploaded file is PROCESSING before it is ACTIVE. Passing it to
    generate_content too early fails, so upload() polls to ACTIVE.
  * Files expire after ~48h server-side. That is well beyond a single run, and
    re-uploading is free, so nothing caches the URI across runs.

## Response shape

`generate()` returns `{"output": <text>}` in `raw_response` because that is
what `foley.analyze.parse_timeline_json` already reads (it checks `output`
then `text`). Keeping the envelope identical to fal's means analyze.py needed
no changes to swap providers — the point of the adapter layer.
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

from foleyflow.providers.base import GenerationResult, ModelSpec

# Files API: how long to wait for an uploaded video to become ACTIVE.
UPLOAD_POLL_INTERVAL_S = 1.0
UPLOAD_TIMEOUT_S = 300.0

DEFAULT_API_KEY_ENV = "GOOGLE_API_KEY"


class GoogleAdapter:
    """Adapter for Google's Gemini models via the google-genai SDK."""

    name: str = "google"

    def __init__(self, api_key: Optional[str] = None,
                 api_key_env: str = DEFAULT_API_KEY_ENV):
        self._api_key = api_key
        self._api_key_env = api_key_env
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        key = self._api_key or os.environ.get(self._api_key_env)
        if not key:
            raise RuntimeError(
                f"{self._api_key_env} is unset — create a key at "
                "https://aistudio.google.com/apikey and add it to .env"
            )
        from google import genai
        self._client = genai.Client(api_key=key)
        return self._client

    def upload(self, local_path: str) -> str:
        """Upload a local media file to the Files API and return its URI.

        Blocks until the file reaches ACTIVE — Gemini rejects a file that is
        still PROCESSING, and the failure surfaces as a confusing 400 about the
        request rather than about the upload.
        """
        client = self._get_client()
        uploaded = client.files.upload(file=local_path)

        deadline = time.monotonic() + UPLOAD_TIMEOUT_S
        while getattr(uploaded.state, "name", str(uploaded.state)) == "PROCESSING":
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"Gemini Files API did not finish processing "
                    f"{os.path.basename(local_path)} within {UPLOAD_TIMEOUT_S:.0f}s"
                )
            time.sleep(UPLOAD_POLL_INTERVAL_S)
            uploaded = client.files.get(name=uploaded.name)

        state = getattr(uploaded.state, "name", str(uploaded.state))
        if state != "ACTIVE":
            raise RuntimeError(
                f"Gemini Files API returned state {state} for "
                f"{os.path.basename(local_path)} — expected ACTIVE"
            )
        return uploaded.uri

    def generate(self, spec: ModelSpec, arguments: dict,
                 method: str = "subscribe", **kwargs) -> GenerationResult:
        """Run a multimodal completion.

        `arguments` keeps the shape analyze.py already builds:
          video_urls / image_urls : list of Files API URIs (may be empty)
          prompt                  : the text prompt
          model                   : optional model id override
        """
        client = self._get_client()
        model = arguments.get("model") or spec.model_id
        prompt = arguments.get("prompt", "")

        parts: list[Any] = []
        for uri in (arguments.get("video_urls") or []):
            parts.append(_file_part(uri, "video/mp4"))
        for uri in (arguments.get("image_urls") or []):
            parts.append(_file_part(uri, "image/jpeg"))
        parts.append(prompt)

        response = client.models.generate_content(model=model, contents=parts)

        return GenerationResult(
            raw_response={"output": response.text or "", "model": model},
            actual_cost_usd=None,   # Gemini does not return per-call cost
        )


def _file_part(uri: str, default_mime: str):
    """Build a Files API part. A bare string would be sent as literal text."""
    from google.genai import types
    return types.Part.from_uri(file_uri=uri, mime_type=default_mime)
