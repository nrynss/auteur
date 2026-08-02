"""Local adapter — the self-hosted SFX tier from SPEC.md §9.1.

Talks HTTP to the MOSS-SoundEffect worker rather than importing torch, so the
deployed app stays free of CUDA and the worker can move behind a tunnel without
touching this code. From here it is "one more provider with a base URL".

The worker returns fal's response envelope ({"audio": {"url": ...}}) so
``sfx.get_sfx`` — which calls ``extract_audio_url`` then ``net.download`` — works
against either provider unchanged.

Base URL comes from ``$FOLEYFLOW_SFX_WORKER_URL`` or ``[adapters.local] base_url``
in providers.toml, defaulting to localhost.

## Authentication

The worker is reachable through a public Cloudflare tunnel in the deployed app,
so ``POST /generate`` and ``GET /files/{name}`` sit behind a bearer token read
from ``$FOLEYFLOW_SFX_WORKER_TOKEN``. Three properties matter:

* **Optional.** No token set means no ``Authorization`` header at all, which is
  what a loopback worker in dev mode expects — the worker only enforces the
  token when it has one itself. Tests therefore need no credential.
* **Scoped.** ``download_headers`` attaches the token only to URLs under this
  adapter's own base URL. The generated-audio URL is a second request, and
  ``net.download`` is shared with fal and ElevenLabs; a blanket header would
  hand this project's worker token to a vendor CDN.
* **Not part of the cache key.** A transport credential does not change the
  audio. Folding it into the key would orphan every stem already in the shared
  B2 cache the moment the token was set or rotated.
"""

from __future__ import annotations

import os
from typing import Dict, Optional
from urllib.parse import urljoin

from foleyflow.providers.base import Adapter, GenerationResult, ModelSpec

DEFAULT_BASE_URL = "http://127.0.0.1:8600"
TOKEN_ENV = "FOLEYFLOW_SFX_WORKER_TOKEN"

# Generation is ~20s at 50 steps and the worker serialises on one GPU, so a
# queued request can wait behind several others. net.download's 60s default
# would spuriously fail the second concurrent stem.
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

    @property
    def token(self) -> Optional[str]:
        """The worker's bearer token, or None when it is running open.

        An empty string is treated as unset — an exported-but-blank env var is
        how a deployment ends up sending ``Authorization: Bearer `` and getting
        a 401 that looks like a wrong token rather than a missing one.
        """
        value = os.environ.get(TOKEN_ENV, "").strip()
        return value or None

    def auth_headers(self) -> Dict[str, str]:
        token = self.token
        return {"Authorization": f"Bearer {token}"} if token else {}

    def download_headers(self, url: str) -> Dict[str, str]:
        """Credentials for fetching ``url``, if it is ours to authenticate.

        Deliberately a prefix test against this adapter's own base URL: the
        generated-audio URL is resolved from a worker-relative path, so a URL
        that does not start with the base URL did not come from the worker and
        must not receive its token.
        """
        base = self.base_url
        return self.auth_headers() if url.startswith(base + "/") or url == base else {}

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
            resp = client.post(url, json=arguments, headers=self.auth_headers() or None)
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
        """Worker reachability, for `foleyflow health`.

        Sent unauthenticated: `/health` is the one open endpoint, and it reports
        `auth_required` so a health check can say "the worker is up but wants a
        token you have not configured" instead of failing as unreachable.
        """
        import httpx

        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{self.base_url}/health")
            resp.raise_for_status()
            payload = resp.json()

        if isinstance(payload, dict) and payload.get("auth_required") and not self.token:
            payload = {**payload, "auth_configured": False}
        elif isinstance(payload, dict) and payload.get("auth_required"):
            payload = {**payload, "auth_configured": True}
        return payload
