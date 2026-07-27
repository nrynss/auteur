import os
import json
import time
import hashlib
from typing import Any, Dict, Optional
import fal_client

LEDGER_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ledger.jsonl"))

# Default cost estimation per second or per run if API doesn't report it
COST_ESTIMATES = {
    "fal-ai/ltx-2.3/text-to-video": 0.36,         # ~$0.36 per 6s clip ($0.06/s)
    "fal-ai/ltx-2.3/image-to-video": 0.36,        # ~$0.36 per 6s clip
    "openrouter/router/video": 0.01,              # Gemini 2.5 Flash video analysis
    "openrouter/router/vision": 0.005,            # Gemini 2.5 Flash image analysis
    "fal-ai/elevenlabs/sound-effects/v2": 0.05,  # Estimate per SFX gen
    "cassetteai/sound-effects-generator": 0.02,
    "beatoven/sound-effect-generation": 0.02,
    "fal-ai/mmaudio-v2/text-to-audio": 0.03,
    "fal-ai/marlin/find": 0.01,                   # $0.015/1K tokens; short clips ≈ well under 1K
    "nvidia/nemotron-3-nano-omni/video": 0.02,    # pricing unpublished; rough guess
    "fal-ai/sa2va/8b/video": 0.02,                # pricing unpublished; rough guess
    "fal-ai/kling-video/v3/pro/text-to-video": 0.168,  # per-second: $0.112/s audio-off, $0.168/s audio-on
}

def get_params_hash(arguments: Dict[str, Any]) -> str:
    """Generate a stable hash for input arguments."""
    serialized = json.dumps(arguments, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

def estimate_cost(endpoint: str, arguments: Dict[str, Any]) -> float:
    """Estimate cost for an API call based on endpoint and params."""
    if endpoint in COST_ESTIMATES:
        # Handle duration-based costs
        if "ltx-2.3" in endpoint:
            duration = arguments.get("duration", 6)
            return float(duration) * 0.06
        if "kling" in endpoint:
            duration = float(arguments.get("duration", 5))
            rate = 0.168 if arguments.get("generate_audio", True) else 0.112
            return duration * rate
        return COST_ESTIMATES[endpoint]
    return 0.01  # Default fallback cost

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

def execute_with_ledger(endpoint: str, arguments: Dict[str, Any], method: str = "subscribe", **kwargs) -> Any:
    """
    Wrap fal_client calls to log their cost and inputs to ledger.jsonl.
    method can be 'subscribe', 'run', or 'submit'.
    """
    params_hash = get_params_hash(arguments)
    estimated = estimate_cost(endpoint, arguments)
    
    try:
        if method == "subscribe":
            result = fal_client.subscribe(endpoint, arguments, **kwargs)
        elif method == "run":
            result = fal_client.run(endpoint, arguments, **kwargs)
        elif method == "submit":
            # For submit, it returns a request handle.
            # NOTE: submit-path accounting is known-imprecise (logs success +
            # estimate at handle creation, before the job completes). The path
            # is unused today — fix before any async batching of SFX.
            handler = fal_client.submit(endpoint, arguments, **kwargs)
            log_transaction(endpoint, params_hash, estimated, success=True)
            return handler
        else:
            raise ValueError(f"Unknown fal_client method: {method}")
        
        # Check if actual cost is available in response
        actual_cost = get_actual_cost(result)
        final_cost = actual_cost if actual_cost is not None else estimated
        source = "actual" if actual_cost is not None else "estimate"

        log_transaction(endpoint, params_hash, final_cost, success=True, cost_source=source)
        return result
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
