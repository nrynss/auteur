"""Deterministic local stages, run and recorded by Genblaze.

SPEC §8.2 asks Genblaze to "define the ordered stages and their inputs/outputs"
and to "record execution state, timing, errors, and retries" — for the local
work as much as the provider calls. Mix, mux and QA are ffmpeg and numpy, not
API calls, but they are exactly the stages a user watches and exactly the ones
that fail slowly. Running them as `SyncProvider` steps is what makes them
timed, retried and recorded like anything else, rather than subprocess calls
that happen invisibly beside the orchestrator.

The provider is `providers.genblaze.FFmpegStageProvider`, a sibling of
genblaze-core's own `FFmpegTransform`: same contract, FoleyFlow's operations.

## What a StageRecord is for

`StageRecord` is the shape SPEC §8.2's last line needs — "the workflow run ID,
current stage, and stage-level outcome in its job history". It is written to the
event log as each stage ends (so a polling client sees progress live) and
collected onto the finished `Run` (so history survives the process).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# A local ffmpeg stage that has not finished in ten minutes is wedged, not slow.
# Long enough for a full-length mux on modest hardware; short enough that a
# hung subprocess does not hold a run's slot indefinitely.
STAGE_TIMEOUT_S = 600.0

Stage = Tuple[str, Callable[[dict], dict]]


@dataclass(frozen=True)
class StageRecord:
    """One stage's outcome, as the job history renders it."""

    name: str
    status: str                       # succeeded | failed | running
    elapsed_s: float = 0.0
    provider: Optional[str] = None
    retries: int = 0
    error: Optional[str] = None
    genblaze_run_id: Optional[str] = None
    started_at: Optional[str] = None
    # One human-readable line for the job history — "24/25 stems (19 cached)".
    # Never parsed; the machine-readable counts live on the stem inventory.
    detail: Optional[str] = None

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status,
                "elapsed_s": round(self.elapsed_s, 3),
                "provider": self.provider, "retries": self.retries,
                "error": self.error, "genblaze_run_id": self.genblaze_run_id,
                "started_at": self.started_at, "detail": self.detail}


@dataclass
class StageLog:
    """Accumulates stage records and, optionally, publishes each one as it lands.

    `sink` is called with one `StageRecord` per completed stage. `render.py`
    wires it to the project event log, so `GET /runs/{id}` can answer "which
    stage is it on" for a run that has not finished — the finished record is
    only written at the end, and a progress bar that only updates at the end is
    not a progress bar.
    """

    sink: Optional[Callable[[StageRecord], None]] = None
    records: List[StageRecord] = field(default_factory=list)
    genblaze_run_ids: List[str] = field(default_factory=list)

    def add(self, record: StageRecord) -> StageRecord:
        self.records.append(record)
        if record.genblaze_run_id and record.genblaze_run_id not in self.genblaze_run_ids:
            self.genblaze_run_ids.append(record.genblaze_run_id)
        if self.sink is not None:
            try:
                self.sink(record)
            except Exception:
                # A stage that succeeded must not be reported as failed because
                # writing its progress event did. The record is still in
                # `records` and reaches the run's metadata at completion.
                pass
        return record

    @property
    def genblaze_run_id(self) -> Optional[str]:
        """The first Genblaze run id this log saw.

        One id per job is what the UI shows and what `Run.genblaze_run_id`
        holds; a job that spans several pipelines (stems, then the local
        stages) keeps the rest in `genblaze_run_ids`.
        """
        return self.genblaze_run_ids[0] if self.genblaze_run_ids else None

    @property
    def current(self) -> Optional[str]:
        return self.records[-1].name if self.records else None

    def to_list(self) -> List[dict]:
        return [r.to_dict() for r in self.records]


class StageFailed(RuntimeError):
    """A local stage failed. Carries the partial log for the job to record."""

    def __init__(self, record: StageRecord, log: StageLog):
        super().__init__(f"stage {record.name!r} failed: {record.error}")
        self.record = record
        self.log = log


def run_stages(name: str, stages: Sequence[Stage], *,
               log: Optional[StageLog] = None,
               project_id: Optional[str] = None,
               timeout: float = STAGE_TIMEOUT_S) -> Dict[str, dict]:
    """Run an ordered list of local stages as one Genblaze pipeline.

    Sequential on purpose: `Pipeline.run()` rather than `arun()`. Mix feeds mux
    feeds QA, so there is nothing to fan out here, and the fan-out that does
    exist — one step per independent stem — lives in `foleyflow.stems`.

    Returns each stage's result dict, keyed by stage name. Raises `StageFailed`
    on the first failure, with the log so far attached, because a caller that
    loses the mix still needs to record that the mix is what it lost.
    """
    from genblaze_core import Modality, StepType
    from genblaze_core.pipeline.pipeline import Pipeline

    from foleyflow.projects import utcnow
    from foleyflow.providers.genblaze import FFmpegStageProvider

    log = log if log is not None else StageLog()
    provider = FFmpegStageProvider({stage: fn for stage, fn in stages})

    results: Dict[str, dict] = {}
    # One pipeline per stage rather than one pipeline of N steps: a stage's
    # params are built from the previous stage's output (the mix path the mux
    # consumes, the WAV QA measures), and Genblaze resolves a step's params at
    # construction time. Building all four up front would freeze paths that do
    # not exist yet. Each stage still gets its own Genblaze run id, timing,
    # status and retry accounting — which is what the record is for.
    for stage_name, _fn in stages:
        started = time.monotonic()
        started_at = utcnow()
        pipeline = Pipeline(
            f"{name}-{stage_name}",
            project_id=project_id,
            chain=False,
            # Nothing to validate: the "model" is a locally registered callable,
            # and preflight would ask a provider with no catalog about it.
            preflight=False,
        )
        pipeline.step(provider, model=stage_name, modality=Modality.AUDIO,
                      step_type=StepType.CUSTOM)
        outcome = pipeline.run(fail_fast=True, raise_on_failure=False,
                               progress=False, timeout=timeout)
        step = outcome.run.steps[0] if outcome.run.steps else None
        elapsed = time.monotonic() - started

        if step is None or step.status != "succeeded":
            record = log.add(StageRecord(
                name=stage_name, status="failed", elapsed_s=elapsed,
                provider=provider.name,
                retries=step.retries if step else 0,
                error=(step.error if step and step.error else "stage did not run"),
                genblaze_run_id=outcome.run.run_id, started_at=started_at))
            raise StageFailed(record, log)

        log.add(StageRecord(
            name=stage_name, status="succeeded", elapsed_s=elapsed,
            provider=provider.name, retries=step.retries,
            genblaze_run_id=outcome.run.run_id, started_at=started_at))
        results[stage_name] = provider.result_for(stage_name) or {}

    return results


def record_stem_stage(log: StageLog, stem_results: Sequence[Any], *,
                      genblaze_run_id: Optional[str] = None,
                      elapsed_s: float = 0.0,
                      started_at: Optional[str] = None) -> StageRecord:
    """Fold a fan-out of stems into one stage record.

    Thirty stems are one *stage* even though they are thirty Genblaze steps —
    a job history that lists them individually is a log, not a status. The
    per-stem attempt history stays on the stem inventory, where a repair needs
    it; here the stage says how many were generated, how many were served from
    cache, and how many could not be produced at all.
    """
    total = len(stem_results)
    failed = [r for r in stem_results if not r.ok]
    cached = sum(1 for r in stem_results if r.cached)
    escalated = sum(1 for r in stem_results if len(r.attempts) > 1 and r.ok)

    error = None
    if failed:
        error = (f"{len(failed)} stem(s) could not be generated: "
                 + "; ".join(sorted({r.error or "unknown" for r in failed})))

    return log.add(StageRecord(
        name="stems",
        status="failed" if failed else "succeeded",
        elapsed_s=elapsed_s,
        provider="genblaze",
        error=error,
        genblaze_run_id=genblaze_run_id,
        started_at=started_at,
        detail=(f"{total - len(failed)}/{total} stems "
                f"({cached} cached, {escalated} escalated)"),
    ))
