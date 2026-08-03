"""SFX tier selection — which endpoint a stem request actually goes to.

SPEC §9.1 describes three tiers. They exist so the *same code path* serves all
three and the only thing that changes is configuration:

| tier | endpoint | reached through |
|---|---|---|
| ``paid`` | ElevenLabs sound-effects v2 | the ``genblaze-elevenlabs`` connector |
| ``fallback`` | ``fal-ai/elevenlabs/sound-effects/v2`` | the lifted fal adapter |
| ``test`` | ``openmoss/moss-soundeffect-v2`` | the lifted self-hosted worker |

Everything here is read from ``[sfx_tiers]`` in providers.toml. Nothing is
hardcoded — a tier is a table, and the fallback edge is a key on it, so the
demo's escalation policy is editable without a code change.

## Why the default is ``test``

The SFX cache is content-addressed on ``(endpoint, prompt, duration)``. Making
``paid`` the default would change the endpoint segment of every key and orphan
the ~170 stems already in the shared B2 cache — the demo's reliability
guarantee. ``test`` resolves to exactly the endpoint every routing category
already declares, so the keys are byte-identical to the ones on disk today.
Switching to the paid tier is a deliberate act: ``FOLEYFLOW_SFX_TIER=paid`` (or
``active`` in the TOML), and it generates fresh clips under fresh keys rather
than silently reusing MOSS output as if ElevenLabs had produced it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional

ENV_TIER = "FOLEYFLOW_SFX_TIER"


@dataclass(frozen=True)
class Tier:
    """One row of the tier table."""

    name: str
    endpoint: str
    fallback: Optional[str] = None
    label: str = ""

    @property
    def adapter(self) -> str:
        """Which Auteur adapter serves this tier's endpoint.

        Derived from the endpoint's own ``[models.…] adapter`` rather than
        restated on the tier, so the two cannot disagree.
        """
        from foleyflow.providers.config import get_model_spec

        return get_model_spec(self.endpoint).adapter


class TierError(RuntimeError):
    """The tier configuration cannot be satisfied."""


def _raw(*, _config: Optional[dict] = None) -> dict:
    from foleyflow.providers.config import get_tier_config

    return get_tier_config(_config=_config)


def load_tiers(*, _config: Optional[dict] = None) -> Dict[str, Tier]:
    """Every declared tier, keyed by name. ``{}`` when none are configured."""
    raw = _raw(_config=_config)
    out: Dict[str, Tier] = {}
    for name, body in raw.items():
        if name == "active" or not isinstance(body, dict):
            continue
        out[name] = Tier(name=name, endpoint=body["endpoint"],
                         fallback=body.get("fallback"),
                         label=body.get("label", ""))
    return out


def active_tier_name(*, _config: Optional[dict] = None) -> Optional[str]:
    """The selected tier. ``$FOLEYFLOW_SFX_TIER`` wins over the TOML.

    Env first so a demo can be flipped to the paid tier without editing a file
    that is under version control — the same reason the SFX worker's base URL
    is env-overridable.
    """
    tiers = load_tiers(_config=_config)
    if not tiers:
        return None
    env = os.environ.get(ENV_TIER)
    if env:
        if env not in tiers:
            raise TierError(
                f"{ENV_TIER}={env!r} is not a declared tier "
                f"(known: {sorted(tiers)})")
        return env
    active = _raw(_config=_config).get("active")
    if active and active not in tiers:
        raise TierError(
            f"providers.toml: [sfx_tiers] active={active!r} is not a declared tier")
    return active


def get_tier(name: str, *, _config: Optional[dict] = None) -> Tier:
    tiers = load_tiers(_config=_config)
    if name not in tiers:
        raise TierError(f"Unknown SFX tier {name!r} (known: {sorted(tiers)})")
    return tiers[name]


def tier_chain(name: Optional[str] = None, *,
               _config: Optional[dict] = None) -> List[Tier]:
    """The escalation order starting at ``name``: the tier, then its fallbacks.

    Cycle-safe — a tier already in the chain terminates it rather than looping
    forever, because a config typo must not hang a render.
    """
    tiers = load_tiers(_config=_config)
    if not tiers:
        return []
    start = name or active_tier_name(_config=_config)
    if not start:
        return []
    chain: List[Tier] = []
    seen = set()
    cursor: Optional[str] = start
    while cursor and cursor not in seen:
        if cursor not in tiers:
            raise TierError(
                f"SFX tier {cursor!r} is referenced as a fallback but not declared")
        seen.add(cursor)
        tier = tiers[cursor]
        chain.append(tier)
        cursor = tier.fallback
    return chain


def resolve_endpoint(default: str, *, _config: Optional[dict] = None) -> str:
    """The endpoint the active tier selects, or ``default`` if no tier is set.

    ``default`` is the routing category's own endpoint, so a deployment with no
    ``[sfx_tiers]`` table behaves exactly as it did before tiers existed.
    """
    active = active_tier_name(_config=_config)
    if not active:
        return default
    return get_tier(active, _config=_config).endpoint


def tier_for_endpoint(endpoint: str, *,
                      _config: Optional[dict] = None) -> Optional[str]:
    """Name of the tier serving ``endpoint``, if any. For reporting only."""
    for name, tier in load_tiers(_config=_config).items():
        if tier.endpoint == endpoint:
            return name
    return None
