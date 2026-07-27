"""fal.ai adapter — the only module in src/ that imports fal_client.

Implements the Adapter protocol from base.py. All fal-specific response
parsing, auth, retry logic, and upload lives here.
"""

from __future__ import annotations

from foleyflow.providers.base import Adapter, GenerationResult, ModelSpec


class FalAdapter:
    """Adapter for fal.ai endpoints."""

    name: str = "fal"

    def generate(self, spec: ModelSpec, arguments: dict,
                 method: str = "subscribe", **kwargs) -> GenerationResult:
        """Call the fal endpoint and return a normalized result."""
        import fal_client

        endpoint = spec.model_id

        if method == "subscribe":
            raw = fal_client.subscribe(endpoint, arguments, **kwargs)
        elif method == "run":
            raw = fal_client.run(endpoint, arguments, **kwargs)
        elif method == "submit":
            raw = fal_client.submit(endpoint, arguments, **kwargs)
            # submit returns a handle, not a completed result
            return GenerationResult(raw_response=raw)
        else:
            raise ValueError(f"Unknown fal_client method: {method}")

        # Extract asset URL from the fal response envelope
        asset_url = extract_audio_url(raw)
        actual_cost = _get_actual_cost(raw)

        return GenerationResult(
            asset_url=asset_url,
            raw_response=raw,
            actual_cost_usd=actual_cost,
        )

    def upload(self, local_path: str) -> str:
        """Upload a local file to fal's CDN. Returns the CDN URL."""
        import fal_client
        return fal_client.upload_file(local_path)


def extract_audio_url(result) -> str | None:
    """Tolerant extraction: result['audio'] or result['audio_file'] (dict
    with 'url'), else result['url']. Returns None if no URL can be found
    (callers that need it should raise).

    Moved here from foley/sfx.py — response envelope parsing is adapter
    logic (cleanup.md §4.5).
    """
    if isinstance(result, dict):
        audio_info = result.get("audio") or result.get("audio_file")
        if isinstance(audio_info, dict) and audio_info.get("url"):
            return audio_info["url"]
        if result.get("url"):
            return result["url"]
    return None


def _get_actual_cost(result) -> float | None:
    """Inspect response payload for cost or billing metadata."""
    if isinstance(result, dict):
        if "cost" in result:
            try:
                return float(result["cost"])
            except (ValueError, TypeError):
                pass
        if "billing" in result and isinstance(result["billing"], dict):
            try:
                return float(result["billing"].get("cost", 0.0))
            except (ValueError, TypeError):
                pass
    return None
