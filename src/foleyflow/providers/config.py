"""TOML configuration loader and validator for providers.toml.

One ``@lru_cache``'d read; all downstream code calls ``get_model_spec()``,
``get_analysis_config()``, ``get_routing_config()`` — never reads the file
directly.

Uses ``tomllib`` from the standard library (Python 3.11+; this project
requires 3.14). **No TOML dependency added.**
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from typing import Any, Dict, List, Optional

from foleyflow.providers.base import ModelSpec

_TOML_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "providers.toml")
)


class ConfigError(Exception):
    """Raised when providers.toml is invalid."""


@lru_cache(maxsize=1)
def load_config(path: Optional[str] = None) -> dict:
    """Load and validate providers.toml. Returns the raw dict.

    Cached after the first successful load. Pass ``path`` for testing only;
    production always reads the repo-root ``providers.toml``.
    """
    path = path or _TOML_PATH
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    _validate(raw, path)
    return raw


def _validate(raw: dict, path: str) -> None:
    """Fail loud on invalid config — name the offending key."""

    # --- models ---
    models = raw.get("models", {})
    for model_id, spec in models.items():
        if "adapter" not in spec:
            raise ConfigError(
                f"providers.toml: model {model_id!r} is missing required field 'adapter'"
            )
        if "role" not in spec:
            raise ConfigError(
                f"providers.toml: model {model_id!r} is missing required field 'role'"
            )
        lo = spec.get("duration_min_s")
        hi = spec.get("duration_max_s")
        if lo is not None and hi is not None and lo > hi:
            raise ConfigError(
                f"providers.toml: model {model_id!r} has inverted duration bounds "
                f"(min={lo} > max={hi})"
            )

    # --- routing ---
    routing = raw.get("routing", {})
    categories = routing.get("categories", [])
    default = routing.get("default", {})

    # Validate that every route references a declared model.
    for cat in categories:
        ep = cat.get("endpoint")
        if ep and ep not in models:
            raise ConfigError(
                f"providers.toml: routing category {cat.get('name')!r} references "
                f"undeclared model {ep!r}"
            )
    if default:
        ep = default.get("endpoint")
        if ep and ep not in models:
            raise ConfigError(
                f"providers.toml: routing default references undeclared model {ep!r}"
            )

    # Must have an 'ambience' category (routing.py:115 does
    # next(c for c in ... if c["name"] == "ambience"), which raises an
    # uncaught StopIteration if missing).
    cat_names = {c.get("name") for c in categories}
    if "ambience" not in cat_names:
        raise ConfigError(
            "providers.toml: routing categories must include an 'ambience' category "
            "(route_ambience depends on it)"
        )


def get_model_spec(model_id: str, *, _config: Optional[dict] = None) -> ModelSpec:
    """Look up a model by its canonical ID. Raises ``KeyError`` if unknown."""
    config = _config or load_config()
    models = config.get("models", {})
    if model_id not in models:
        raise KeyError(f"Unknown model: {model_id!r} (not declared in providers.toml)")
    raw = models[model_id]
    return ModelSpec(
        model_id=model_id,
        adapter=raw["adapter"],
        role=raw["role"],
        cost_per_call_usd=raw.get("cost_per_call_usd"),
        cost_per_second_usd=raw.get("cost_per_second_usd"),
        duration_min_s=raw.get("duration_min_s"),
        duration_max_s=raw.get("duration_max_s"),
        prompt_field=raw.get("prompt_field"),
        duration_field=raw.get("duration_field"),
        duration_type=raw.get("duration_type"),
        extra_arguments=raw.get("extra_arguments", {}),
    )


def get_adapter_config(name: str, *, _config: Optional[dict] = None) -> dict:
    """Return raw adapter config dict for the given adapter name."""
    config = _config or load_config()
    adapters = config.get("adapters", {})
    if name not in adapters:
        raise KeyError(f"Unknown adapter: {name!r} (not declared in providers.toml)")
    return adapters[name]


def get_analysis_config(*, _config: Optional[dict] = None) -> dict:
    """Return the ``[analysis]`` section."""
    config = _config or load_config()
    return config.get("analysis", {})


def get_routing_config(*, _config: Optional[dict] = None) -> dict:
    """Return ``{"categories": [...], "default": {...}}`` from the
    ``[routing]`` section."""
    config = _config or load_config()
    routing = config.get("routing", {})
    return {
        "categories": routing.get("categories", []),
        "default": routing.get("default", {}),
    }


def get_defaults(*, _config: Optional[dict] = None) -> dict:
    """Return the ``[defaults]`` section."""
    config = _config or load_config()
    return config.get("defaults", {})


def get_all_model_specs(*, _config: Optional[dict] = None) -> Dict[str, ModelSpec]:
    """Return a dict of model_id -> ModelSpec for every declared model."""
    config = _config or load_config()
    models = config.get("models", {})
    return {mid: get_model_spec(mid, _config=config) for mid in models}
