"""Provider abstraction layer — Adapter protocol, ModelSpec, GenerationResult.

This module defines the interface that every provider adapter must implement.
No vendor-specific code lives here; each adapter (fal.py, local.py, …) imports
this protocol and implements it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, runtime_checkable


@runtime_checkable
class Adapter(Protocol):
    """Interface that every provider adapter must implement.

    Deliberately shaped like Genblaze's ``SyncProvider.generate(step) -> Step``
    so the fal adapter can be lifted into a ``genblaze_fal`` connector with thin
    translation rather than a rewrite.
    """

    name: str

    def generate(self, spec: ModelSpec, arguments: dict) -> GenerationResult:
        """Call the provider and return a normalized result.

        GenerationResult carries: asset_url (or local_path), raw_response,
        and actual_cost_usd when the vendor reports one.
        """
        ...

    def upload(self, local_path: str) -> str:
        """Make a local file reachable by the provider. HTTP providers upload
        to a CDN; local adapters return a file:// URL."""
        ...


@dataclass
class ModelSpec:
    """Everything the system needs to know about one model, loaded from
    providers.toml. The ``model_id`` is the TOML table key and doubles as the
    SFX cache key — see cleanup.md §4.1."""

    model_id: str
    adapter: str
    role: str
    cost_per_call_usd: Optional[float] = None
    cost_per_second_usd: Optional[float] = None
    # Assumed duration when a per-second-priced call carries no duration
    # argument. Without it, such a call would estimate $0 — see
    # foleyflow.ledger.estimate_cost.
    default_duration_s: Optional[float] = None
    duration_min_s: Optional[float] = None
    duration_max_s: Optional[float] = None
    prompt_field: Optional[str] = None
    duration_field: Optional[str] = None
    duration_type: Optional[str] = None     # "int" | "float"
    # Where this model's quota `used` counts come from. Absent = the cost
    # ledger; "elevenlabs" = the workspace analytics endpoint (the ledger
    # cannot see other processes on the same key, and the paid tier's
    # responses carry no cost).
    usage_source: Optional[str] = None
    # Daily hard cap in USD for the workspace, driven by the ElevenLabs
    # usage analytics (foleyflow.elevenlabs.cap_status). None or <= 0 = off;
    # when set, billable work against the paid tier is refused past the cap.
    hard_cap_usd: Optional[float] = None
    extra_arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationResult:
    """Normalized result from any adapter's ``generate()`` call."""

    asset_url: Optional[str] = None
    local_path: Optional[str] = None
    raw_response: Any = None
    actual_cost_usd: Optional[float] = None
