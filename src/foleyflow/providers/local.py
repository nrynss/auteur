"""Local adapter — the self-hosted SFX tier from SPEC.md §9.1.

Talks HTTP to the MOSS-SoundEffect worker rather than importing torch, so the
deployed app stays free of CUDA and the worker can move behind a tunnel without
touching this code. From here it is "one more provider with a base URL".

The worker returns fal's response envelope ({"audio": {"url": ...}}) so
``sfx.get_sfx`` — which calls ``extract_audio_url`` then ``net.download`` — works
against either provider unchanged.

Base URL comes from ``$FOLEYFLOW_SFX_WORKER_URL`` or ``[adapters.local] base_url``
in providers.toml, defaulting to localhost.
"""

from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urljoin

from foleyflow.providers.base import Adapter, GenerationResult, ModelSpec

DEFAULT_BASE_URL = "http://127.0.0.1:8600"

# Generation is ~16s at 50 steps and the worker serialises on one GPU, so a
# queued request can wait behind another. net.download's 60s default would
# spuriously fail the second concurrent stem.
GENERATE_TIMEOUT_S = 300.0


class LocalAdapter:
    """Adapter for the self-hosted SFX worker."""

    name: str = "local"

    def __init__(self, base_url: Optional[str] = None):
        self._base_url = base_url

    @property
    def base_url(self) -> str:
        """Env wins over config so a tunnel URL can be swapped without an edit."""
        if self._base_url:
            return self._base_url.rstrip("/")
        env = os.environ.get("FOLEYFLOW_SFX_WORKER_URL")
        if env:
            return env.rstrip("/")
        try:
            from foleyflow.providers.config import get_adapter_config
            configured = (get_adapter_config("local") or {}).get("base_url")
            if configured:
                return str(configured).rstrip("/")
        except Exception:
            pass
        return DEFAULT_BASE_URL

    def generate(self, spec: ModelSpec, arguments: dict,
                 method: str = "subscribe", **kwargs) -> GenerationResult:
        """POST the already-built arguments to the worker.

        ``arguments`` arrives from ``sfx.build_arguments``, which has already
        applied providers.toml's prompt_field/duration_field mapping and merged
        extra_arguments (num_inference_steps, cfg_scale) — so it is passed
        through rather than reassembled here.
        """
        import httpx

        url = f"{self.base_url}/generate"
        with httpx.Client(timeout=GENERATE_TIMEOUT_S) as client:
            resp = client.post(url, json=arguments)
            resp.raise_for_status()
            raw = resp.json()

        # The worker returns a root-relative path so it stays correct behind a
        # tunnel; resolve it here, where the base URL is known.
        audio = raw.get("audio")
        if isinstance(audio, dict) and audio.get("url"):
            absolute = urljoin(self.base_url + "/", audio["url"].lstrip("/"))
            audio["url"] = absolute
        else:
            absolute = None

        return GenerationResult(
            asset_url=absolute,
            raw_response=raw,
            # Self-hosted inference is free per generation (§9.1). Reported as
            # an actual cost, not an estimate, because 0.00 is exactly right.
            actual_cost_usd=0.0,
        )

    def upload(self, local_path: str) -> str:
        """Local files are already local — return a file:// URL."""
        return f"file://{local_path}"

    def health(self) -> dict:
        """Worker reachability, for `foleyflow health`."""
        import httpx

        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{self.base_url}/health")
            resp.raise_for_status()
            return resp.json()
