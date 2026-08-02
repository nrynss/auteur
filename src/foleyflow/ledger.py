import os
import json
import time
import hashlib
from typing import Any, Dict, Optional

from foleyflow.providers.config import get_defaults, get_model_spec

# Repo root by default, which is right for local runs and wrong on Cloud Run:
# the container filesystem is read-only outside /tmp, so the append silently
# fails and `quota.ledger_writable()` goes False — at which point every quota
# figure reads zero no matter how much budget was actually spent, and nothing
# stops the app walking past the 20-analyses/day free tier into a 429.
# `FOLEYFLOW_LEDGER_PATH` points it somewhere writable there.
LEDGER_PATH = os.environ.get("FOLEYFLOW_LEDGER_PATH") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "ledger.jsonl"))


def get_params_hash(arguments: Dict[str, Any]) -> str:
    """Generate a stable hash for input arguments."""
    serialized = json.dumps(arguments, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

def _resolve_duration(spec, arguments: Dict[str, Any]) -> Optional[float]:
    """Find the duration in `arguments` for a per-second-priced model.

    The argument's field name is per-model config (`duration_field`), so this
    cannot assume "duration": ElevenLabs calls it `duration_seconds`. Looking
    only for the literal "duration" would miss it and price the call at $0.
    Falls back to the literal name for models that declare no duration_field.
    """
    for key in (spec.duration_field, "duration"):
        if key and key in arguments:
            try:
                value = float(arguments[key])
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    return spec.default_duration_s


def estimate_cost(endpoint: str, arguments: Dict[str, Any]) -> float:
    """Estimate cost for an API call based on endpoint and params.

    Reads pricing from providers.toml. If the model declares
    ``cost_per_second_usd``, cost is ``duration * rate``; otherwise
    ``cost_per_call_usd``. Falls back to ``defaults.cost_per_call_usd``
    for unknown endpoints.

    A per-second model whose duration cannot be resolved warns and falls back
    rather than returning 0 — this figure guards spend and is shown to users
    before a billable regeneration (SPEC.md §9.4), so silently reporting $0
    is the one outcome worse than reporting the wrong number.
    """
    default_cost = get_defaults().get("cost_per_call_usd", 0.01)

    try:
        spec = get_model_spec(endpoint)
    except KeyError:
        return default_cost

    if spec.cost_per_second_usd is not None:
        duration = _resolve_duration(spec, arguments)
        if duration is not None:
            return duration * spec.cost_per_second_usd
        fallback = spec.cost_per_call_usd or default_cost
        print(f"[ledger] WARNING: {endpoint} is priced per second but the call "
              f"carries no usable duration (looked for "
              f"{spec.duration_field or 'duration'!r}) and declares no "
              f"default_duration_s — estimating ${fallback:.4f} instead of $0.")
        return fallback

    if spec.cost_per_call_usd is not None:
        return spec.cost_per_call_usd

    return default_cost

def log_transaction(endpoint: str, params_hash: str, cost: float, success: bool,
                    error: Optional[str] = None, cost_source: str = "estimate"):
    """Write a transaction record to ledger.jsonl.

    `cost_estimate` keeps its historical name for backward compatibility with
    existing rows; `cost_source` says whether the value is "actual" (the
    response carried a cost field) or an "estimate" from COST_ESTIMATES.
    """
    record = {
        "endpoint": endpoint,
        "params_hash": params_hash,
        "cost_estimate": cost,
        "cost_source": cost_source,
        "success": success,
        "error": error,
        "timestamp": time.time(),
    }
    # Append to ledger.jsonl safely
    try:
        with open(LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        print(f"Ledger Warning: Failed to write transaction to log: {e}")

def get_actual_cost(result: Any) -> Optional[float]:
    """Inspect response payload for cost or billing metadata."""
    if isinstance(result, dict):
        # Look for explicit cost/billing fields returned by openrouter or other models
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

def execute_with_ledger(endpoint: str, arguments: Dict[str, Any],
                        method: str = "subscribe", adapter=None, **kwargs) -> Any:
    """Wrap provider calls to log their cost and inputs to ledger.jsonl.

    ``adapter`` is an optional Adapter instance; when omitted, the adapter
    configured for this endpoint in providers.toml is used. method can be
    'subscribe', 'run', or 'submit'.
    """
    params_hash = get_params_hash(arguments)
    estimated = estimate_cost(endpoint, arguments)

    # Resolve adapter
    if adapter is None:
        from foleyflow.providers import get_adapter
        try:
            spec = get_model_spec(endpoint)
            adapter = get_adapter(spec.adapter)
        except KeyError:
            # Fallback for unknown endpoints: use fal
            from foleyflow.providers import get_adapter
            adapter = get_adapter("fal")
            from foleyflow.providers.base import ModelSpec
            spec = ModelSpec(model_id=endpoint, adapter="fal", role="unknown")
    else:
        try:
            spec = get_model_spec(endpoint)
        except KeyError:
            from foleyflow.providers.base import ModelSpec
            spec = ModelSpec(model_id=endpoint, adapter="fal", role="unknown")

    try:
        result = adapter.generate(spec, arguments, method=method, **kwargs)

        if method == "submit":
            # submit returns a handle — log estimate at creation time
            log_transaction(endpoint, params_hash, estimated, success=True)
            return result.raw_response

        # Check if actual cost is available in response
        actual_cost = result.actual_cost_usd
        if actual_cost is None:
            actual_cost = get_actual_cost(result.raw_response)
        final_cost = actual_cost if actual_cost is not None else estimated
        source = "actual" if actual_cost is not None else "estimate"

        log_transaction(endpoint, params_hash, final_cost, success=True, cost_source=source)
        return result.raw_response
    except Exception as e:
        log_transaction(endpoint, params_hash, estimated, success=False, error=str(e))
        raise e

def summarize_ledger(path: str = LEDGER_PATH) -> Dict[str, Any]:
    """Sum costs of successful calls: grand total + per-endpoint totals.
    Powers the CLI exit footer (T4). Tolerates old rows without cost_source."""
    total = 0.0
    by_endpoint: Dict[str, float] = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not row.get("success"):
                    continue
                cost = float(row.get("cost_estimate") or 0.0)
                endpoint = row.get("endpoint", "unknown")
                total += cost
                by_endpoint[endpoint] = by_endpoint.get(endpoint, 0.0) + cost
    return {"total": total, "by_endpoint": by_endpoint}
