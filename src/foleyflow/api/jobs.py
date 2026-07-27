"""Background job runner.

Every mutation in the API returns 202 and a run id (SPEC.md §14) rather than
blocking. Analysis takes ~25 seconds, generation longer; holding a request open
for that stalls a worker thread and any proxy in front of it will time out
first anyway.

## Where run state lives

In the B2 event log, not in this process. `JobRunner` owns *execution*; the
`ProjectStore` owns *truth*. A client polling `GET /runs/{id}` is answered from
storage, so the answer does not depend on which worker thread serves it, and a
run that was in flight when the process died is still visible in the log
afterwards rather than vanishing.

The in-memory table here exists only to answer "is something already running
for this project" cheaply and to hold the future for cancellation. It is a
cache, never the record.

## Single-instance constraint

A `ThreadPoolExecutor` makes concurrent runs safe within one process. It does
NOT coordinate across instances: two servers would each happily start an
analysis for the same project. The deployment target is a single Cloud Run
instance, which matches the MVP scope, and this is stated in docs/api.md so the
limit is visible in the contract rather than discovered in production.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from foleyflow.projects import ProjectStore, Run

logger = logging.getLogger("foleyflow.jobs")

# Small on purpose. The work is ffmpeg and provider calls — CPU and network
# bound, not something that benefits from many threads, and the Gemini free
# tier is 5 requests/minute regardless.
DEFAULT_MAX_WORKERS = 4


@dataclass
class ActiveJob:
    run_id: str
    project_id: str
    kind: str
    future: Future

    @property
    def running(self) -> bool:
        return not self.future.done()


class JobRunner:
    def __init__(self, store: ProjectStore, max_workers: int = DEFAULT_MAX_WORKERS):
        self.store = store
        self._pool = ThreadPoolExecutor(max_workers=max_workers,
                                        thread_name_prefix="foleyflow-job")
        self._active: Dict[str, ActiveJob] = {}
        # Guards _active. Held only for dict access, never across the job
        # itself — holding it during submit would serialise every request.
        self._lock = threading.Lock()

    # -- inspection -------------------------------------------------------

    def active_for_project(self, project_id: str, kind: Optional[str] = None) -> Optional[ActiveJob]:
        """The in-flight job for a project, if any.

        Sweeps finished entries on the way past rather than in a background
        timer: the table is only read here, so this is the natural point to
        keep it from growing for the life of the process.
        """
        with self._lock:
            for run_id in [rid for rid, job in self._active.items() if not job.running]:
                self._active.pop(run_id, None)
            for job in self._active.values():
                if job.project_id == project_id and (kind is None or job.kind == kind):
                    return job
        return None

    def get(self, run_id: str) -> Optional[ActiveJob]:
        with self._lock:
            return self._active.get(run_id)

    # -- submission -------------------------------------------------------

    def submit(self, project_id: str, kind: str, work: Callable[[Run], None], *,
               parent_run_id: Optional[str] = None,
               metadata: Optional[dict] = None, actor: str = "system") -> Run:
        """Start a run and execute `work(run)` on a worker thread.

        The run is opened — and its started event written — before the future is
        submitted, so a client that receives the 202 can immediately poll the
        run and find it. Doing it inside the worker would leave a window where
        the run id the client holds does not yet exist in storage.
        """
        run = self.store.start_run(project_id, kind, parent_run_id=parent_run_id,
                                   metadata=metadata, actor=actor)
        future = self._pool.submit(self._execute, run, work)
        with self._lock:
            self._active[run.id] = ActiveJob(run_id=run.id, project_id=project_id,
                                             kind=kind, future=future)
        return run

    def _execute(self, run: Run, work: Callable[[Run], None]) -> None:
        """Run the work and record a terminal state, whatever happens.

        A bare `except Exception` is correct here: this is the top of a worker
        thread, so anything that escapes is lost silently and the run stays
        'running' in the log forever. Every exit path must write a terminal
        state.
        """
        try:
            work(run)
        except Exception as exc:
            logger.exception("run %s (%s) failed", run.id, run.kind)
            try:
                self.store.fail_run(run, f"{type(exc).__name__}: {exc}")
            except Exception:
                # Storage failed while recording a failure. Nothing further can
                # be done from here, but it must not be swallowed silently —
                # the run will look stuck and this log is the only explanation.
                logger.exception("could not record failure for run %s", run.id)

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)


def completion_of(runner: JobRunner, run: Run) -> Callable[[], None]:
    """Helper for work functions that must mark their own completion.

    `_execute` records failures but not successes, because only the work knows
    what to record — a timeline version, a Genblaze run id, a mix version. This
    returns a closure so a work function does not need to capture the store.
    """
    def _complete(**kwargs) -> None:
        runner.store.complete_run(run, **kwargs)
    return _complete
