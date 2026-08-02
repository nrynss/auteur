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
    get_tier_config,
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
    "get_tier_config",
    "load_config",
]


def get_adapter(name: str) -> Adapter:
    """Instantiate and return the adapter for the given name.

    Currently supports 'fal' and 'local'. The adapter module is imported
    lazily so fal_client is never loaded unless a fal adapter is actually
    requested.
    """
    import importlib

    from foleyflow.providers.config import get_adapter_config

    cfg = get_adapter_config(name)          # raises KeyError if undeclared
    target = cfg.get("module")
    if not target or ":" not in target:
        raise KeyError(
            f"Adapter {name!r} declares no valid 'module' in providers.toml "
            "(expected 'package.module:ClassName')"
        )

    module_path, class_name = target.split(":", 1)
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise KeyError(f"Adapter {name!r} module {module_path!r} is not importable: {e}") from e

    try:
        cls = getattr(module, class_name)
    except AttributeError as e:
        raise KeyError(f"Adapter {name!r}: {module_path!r} has no {class_name!r}") from e

    # api_key_env is an adapter-level concern, so pass it when the class wants
    # one. Everything else an adapter needs comes from its ModelSpec.
    kwargs = {}
    if "api_key_env" in cfg:
        import inspect
        if "api_key_env" in inspect.signature(cls).parameters:
            kwargs["api_key_env"] = cfg["api_key_env"]

    return cls(**kwargs)
