"""Stem generation, orchestrated by Genblaze.

This is SPEC §8.2's fourth and fifth bullets — "fan out independent
sound-effect generation where safe" and "retry only the failed or low-confidence
stem" — implemented with Genblaze's own machinery rather than a bespoke thread
pool and a bespoke retry loop.

## Shape of a run

One `Pipeline` per escalation round; one step per stem. `chain=False` means the
steps are independent, so `arun()` fans them out with `asyncio.gather` under a
semaphore. Genblaze owns the timing, the per-step retry policy, the status, and
the error classification; this module owns only the two decisions Genblaze
cannot make for us:

1. **Which endpoint.** Cross-provider escalation (ElevenLabs -> fal -> local)
   cannot use `Pipeline.step(fallback_models=…)`, because that switches *models
   within one provider*. So a round is run per tier, and the next round is built
   from the steps that failed — which is precisely "retry only the failed stem",
   and it means a partial failure costs one retry, not a whole re-render.
2. **Whether to generate at all.** The shared content-addressed cache is
   consulted before each round via the caller's `probe`, so an escalated request
   whose clip already exists is a cache hit rather than a second bill.

## Why the whole thing still works with no credits

Nothing here imports a vendor SDK or reads an API key. Providers are resolved
from providers.toml through `providers.genblaze.get_provider`, and the tier
table decides which one is asked. With the default `test` tier the only network
call is to the self-hosted worker.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from foleyflow.routing import SfxRequest

# Stems are independent but the providers behind them are not infinitely
# parallel: the self-hosted worker serialises on one GPU, and the paid tiers
# rate-limit. Four is the same figure api.jobs uses for the same reason.
DEFAULT_MAX_CONCURRENCY = 4


class StemGenerationError(RuntimeError):
    """Every tier in the chain failed for this request."""

    def __init__(self, request: SfxRequest, attempts: List["StemAttempt"]):
        detail = "; ".join(f"{a.tier or a.endpoint}: {a.error}"
                           for a in attempts if a.error) or "no attempts"
        super().__init__(
            f"could not generate {request.prompt!r} ({request.duration_s:.2f}s): {detail}")
        self.request = request
        self.attempts = attempts


@dataclass(frozen=True)
class StemAttempt:
    """One tier's turn at one stem. Recorded whether it worked or not."""

    tier: str
    endpoint: str
    status: str                      # succeeded | failed | cached
    elapsed_s: float = 0.0
    error: Optional[str] = None
    retries: int = 0
    genblaze_run_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {"tier": self.tier, "endpoint": self.endpoint,
                "status": self.status, "elapsed_s": round(self.elapsed_s, 3),
                "error": self.error, "retries": self.retries,
                "genblaze_run_id": self.genblaze_run_id}


@dataclass
class StemResult:
    """What became of one requested stem."""

    request: SfxRequest              # as routed, before any escalation
    served: Optional[SfxRequest] = None   # the request that produced the bytes
    local_path: Optional[str] = None
    cached: bool = False
    attempts: List[StemAttempt] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.local_path is not None

    @property
    def tier(self) -> str:
        return (self.served or self.request).tier

    @property
    def endpoint(self) -> str:
        return (self.served or self.request).endpoint

    def to_dict(self) -> dict:
        return {"endpoint": self.endpoint, "tier": self.tier,
                "prompt": self.request.prompt,
                "duration_s": self.request.duration_s,
                "cached": self.cached, "error": self.error,
                "attempts": [a.to_dict() for a in self.attempts]}


# -- internals ----------------------------------------------------------------


def _provider_for(endpoint: str):
    from foleyflow.providers.config import get_model_spec
    from foleyflow.providers.genblaze import get_provider

    return get_provider(get_model_spec(endpoint).adapter)


def _build_pipeline(requests: List[SfxRequest], *, name: str,
                    max_concurrency: int):
    from genblaze_core import Modality, Pipeline, StepType

    from foleyflow.sfx import build_arguments

    pipeline = Pipeline(
        name,
        chain=False,
        max_concurrency=max_concurrency,
        # Preflight calls validate_model() on every step, which for a
        # discovery-capable connector is a network round trip per stem before
        # any work starts. The models are declared in providers.toml and
        # validated at config load; a second, billable opinion adds nothing.
        preflight=False,
    )
    for request in requests:
        pipeline.step(
            _provider_for(request.endpoint),
            model=request.endpoint,
            prompt=request.prompt,
            modality=Modality.AUDIO,
            step_type=StepType.GENERATE,
            params=build_arguments(request),
            metadata={"foleyflow_tier": request.tier,
                      "foleyflow_category": request.category},
        )
    return pipeline


def _execute(pipeline, *, timeout: Optional[float] = None):
    """Run a pipeline, fanning out when we can and falling back when we cannot.

    `arun()` is the concurrent path, but it needs a loop of its own. Auteur's
    API handlers are sync (they run in Starlette's threadpool) and the CLI is
    sync, so `asyncio.run` is normally available; a caller that already has a
    running loop gets the sequential `run()` instead of a
    "cannot be called from a running event loop" crash mid-render.
    """
    kwargs = {"fail_fast": False, "raise_on_failure": False, "progress": False}
    if timeout is not None:
        kwargs["timeout"] = timeout
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(pipeline.arun(**kwargs))
    return pipeline.run(**kwargs)


def _step_error(step) -> str:
    return step.error or f"step ended {step.status}"


# -- public API ---------------------------------------------------------------


def generate_many(
    requests: List[SfxRequest],
    *,
    probe: Optional[Callable[[SfxRequest], Optional[str]]] = None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    on_event: Optional[Callable[[dict], None]] = None,
    timeout: Optional[float] = None,
    run_name: str = "foleyflow-stems",
) -> List[StemResult]:
    """Generate every request, fanned out through Genblaze. Never raises.

    Returns one :class:`StemResult` per input request, in order. A request that
    exhausted its tier chain comes back with `ok == False` and the full attempt
    history, because a render that loses one stem out of thirty should say which
    one and why rather than aborting.

    probe(request) -> local path or None. Consulted once per request per tier;
    a hit skips generation for that request in that round.
    """
    def _notify(payload: dict) -> None:
        if on_event is None:
            return
        try:
            on_event(payload)
        except Exception:
            # An observer must never break a render that has already spent money.
            pass

    from foleyflow.routing import escalate

    results = [StemResult(request=r) for r in requests]
    # Position in `results` -> the request to try in the current round.
    pending: Dict[int, SfxRequest] = dict(enumerate(requests))
    # Position -> remaining escalation targets.
    remaining: Dict[int, List[SfxRequest]] = {
        i: escalate(r) for i, r in enumerate(requests)}

    round_index = 0
    while pending:
        round_index += 1
        # Cache first: an escalated request may already have been generated by
        # an earlier project, and the point of a shared cache is that it is
        # checked at every address the request could land at.
        if probe is not None:
            for i in list(pending):
                hit = probe(pending[i])
                if hit:
                    results[i].served = pending[i]
                    results[i].local_path = hit
                    results[i].cached = True
                    results[i].attempts.append(StemAttempt(
                        tier=pending[i].tier, endpoint=pending[i].endpoint,
                        status="cached"))
                    _notify({"phase": "stem_cached", "index": i,
                             "endpoint": pending[i].endpoint,
                             "tier": pending[i].tier})
                    pending.pop(i)
        if not pending:
            break

        order = sorted(pending)
        batch = [pending[i] for i in order]
        _notify({"phase": "stem_round", "round": round_index,
                 "count": len(batch),
                 "tier": batch[0].tier if batch else ""})

        started = time.monotonic()
        try:
            pipeline = _build_pipeline(
                batch, name=f"{run_name}-{round_index}",
                max_concurrency=max_concurrency)
            outcome = _execute(pipeline, timeout=timeout)
        except Exception as exc:
            # The pipeline could not even be built or launched (an unreachable
            # provider class, a missing connector). Charge it to every request
            # in the round rather than losing it.
            elapsed = time.monotonic() - started
            for i in order:
                results[i].attempts.append(StemAttempt(
                    tier=pending[i].tier, endpoint=pending[i].endpoint,
                    status="failed", elapsed_s=elapsed,
                    error=f"{type(exc).__name__}: {exc}"))
            failed_positions = list(order)
        else:
            gb_run_id = outcome.run.run_id
            failed_positions = []
            for position, step in zip(order, outcome.run.steps):
                request = pending[position]
                elapsed = _step_elapsed(step, fallback=time.monotonic() - started)
                path = _asset_path(step)
                if path is not None:
                    results[position].served = request
                    results[position].local_path = path
                    results[position].attempts.append(StemAttempt(
                        tier=request.tier, endpoint=request.endpoint,
                        status="succeeded", elapsed_s=elapsed,
                        retries=step.retries, genblaze_run_id=gb_run_id))
                    _notify({"phase": "stem_done", "index": position,
                             "endpoint": request.endpoint, "tier": request.tier,
                             "genblaze_run_id": gb_run_id})
                else:
                    results[position].attempts.append(StemAttempt(
                        tier=request.tier, endpoint=request.endpoint,
                        status="failed", elapsed_s=elapsed,
                        error=_step_error(step), retries=step.retries,
                        genblaze_run_id=gb_run_id))
                    failed_positions.append(position)
                    _notify({"phase": "stem_failed", "index": position,
                             "endpoint": request.endpoint, "tier": request.tier,
                             "error": _step_error(step),
                             "genblaze_run_id": gb_run_id})

        # Only the failures go round again, and only to a tier they have not
        # tried. Everything else is already recorded as done.
        next_pending: Dict[int, SfxRequest] = {}
        for position in failed_positions:
            queue = remaining.get(position) or []
            if queue:
                next_pending[position] = queue.pop(0)
            else:
                results[position].error = _last_error(results[position])
        pending = next_pending

    return results


def _last_error(result: StemResult) -> str:
    for attempt in reversed(result.attempts):
        if attempt.error:
            return attempt.error
    return "generation failed"


def _step_elapsed(step, *, fallback: float) -> float:
    if step.started_at and step.completed_at:
        return (step.completed_at - step.started_at).total_seconds()
    return fallback


def _asset_path(step) -> Optional[str]:
    """The local file a successful step produced, or None.

    Checked by looking for the asset rather than by trusting the status: a
    provider that reports success without attaching audio has failed as far as
    a mix is concerned, and finding that out here is better than finding it out
    in `soundfile.read`.
    """
    import os

    from foleyflow.providers.genblaze import asset_local_path

    for asset in step.assets:
        try:
            path = asset_local_path(asset)
        except ValueError:
            continue
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
    return None


def generate_one(request: SfxRequest,
                 probe: Optional[Callable[[SfxRequest], Optional[str]]] = None,
                 **kwargs) -> StemResult:
    """One stem through the same orchestration. Raises on total failure.

    The single-stem path deliberately reuses `generate_many` rather than
    shortcutting past Genblaze: if the fan-out path were the only orchestrated
    one, the CLI and the repair loop would exercise different code from the
    render that matters.
    """
    result = generate_many([request], probe=probe, max_concurrency=1, **kwargs)[0]
    if not result.ok:
        raise StemGenerationError(request, result.attempts)
    return result
