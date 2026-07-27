"""Provider registry — public API for the providers package.

Downstream code should import from here:
    from foleyflow.providers import get_model_spec, get_adapter, ...
"""

from foleyflow.providers.base import Adapter, GenerationResult, ModelSpec
from foleyflow.providers.config import (
    ConfigError,
    get_adapter_config,
    get_all_model_specs,
    get_analysis_config,
    get_defaults,
    get_model_spec,
    get_routing_config,
    load_config,
)

__all__ = [
    "Adapter",
    "ConfigError",
    "GenerationResult",
    "ModelSpec",
    "get_adapter",
    "get_adapter_config",
    "get_all_model_specs",
    "get_analysis_config",
    "get_defaults",
    "get_model_spec",
    "get_routing_config",
    "load_config",
]


def get_adapter(name: str) -> Adapter:
    """Instantiate and return the adapter for the given name.

    Currently supports 'fal' and 'local'. The adapter module is imported
    lazily so fal_client is never loaded unless a fal adapter is actually
    requested.
    """
    if name == "fal":
        from foleyflow.providers.fal import FalAdapter
        return FalAdapter()
    if name == "local":
        from foleyflow.providers.local import LocalAdapter
        return LocalAdapter()
    raise KeyError(f"Unknown adapter: {name!r}")
