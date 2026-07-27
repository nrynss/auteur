"""Local adapter stub — for MOSS-SoundEffect and Stable Audio Open.

Proves the Adapter interface admits a non-HTTP provider without committing to
the inference work. ``[adapters.local] enabled = true`` in providers.toml
produces a clear error, not a crash.
"""

from __future__ import annotations

from foleyflow.providers.base import Adapter, GenerationResult, ModelSpec


class LocalAdapter:
    """Stub adapter for local inference (not yet implemented)."""

    name: str = "local"

    def generate(self, spec: ModelSpec, arguments: dict, **kwargs) -> GenerationResult:
        raise NotImplementedError(
            "Local inference not yet implemented. "
            "See cleanup.md §8 — model shortlist: MOSS-SoundEffect, Stable Audio Open."
        )

    def upload(self, local_path: str) -> str:
        """Local files are already local — return a file:// URL."""
        return f"file://{local_path}"
