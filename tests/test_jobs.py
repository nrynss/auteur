"""Tests for the background job runner.

Concurrency is the whole point of this module, so several of these drive real
threads and synchronise with events rather than sleeping — a sleep-based test
either takes seconds or flakes on a loaded machine.
"""

import threading
import time

import pytest

from foleyflow.api.jobs import JobRunner, SlotTaken
from foleyflow.projects import ProjectStore
from foleyflow.storage import LocalBackend


@pytest.fixture
def store(tmp_path):
    return ProjectStore(LocalBackend(str(tmp_path / "b2")), work_dir=str(tmp_path / "w"))


@pytest.fixture
def runner(store):
    r = JobRunner(store, max_workers=4)
    yield r
    r.shutdown(wait=True)


@pytest.fixture
def project(store):
    return store.create_project(title="T", input_key="k", source_sha256="s",
                                source_filename="f.mp4")


def _wait(runner, run_id, timeout=10.0):
    """Block until `run_id` reaches a terminal state.

    Falls back to polling storage: submit() sweeps finished entries, so a job
    that already completed may no longer be in the runner's table. Storage is
    the record; the table is only a cache.
    """
    job = runner.get(run_id)
    if job is not None and job.future is not None:
        job.future.result(timeout=timeout)
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        active = runner.get(run_id)
        if active is None or (active.future is not None and active.future.done()):
            return
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


# -- the 202 contract -------------------------------------------------------

def test_run_is_visible_in_storage_before_submit_returns(runner, store, project):
    """A client that receives the 202 polls immediately. If the run were opened
    inside the worker there would be a window where the id it holds does not
    exist yet, and the poll would 404."""
    gate = threading.Event()
    run = runner.submit(project.id, "analyze", lambda r: gate.wait(5))

    event_run_ids = [e.run_id for e in store.list_events(project.id)]
    assert run.id in event_run_ids, "started event must be written before submit returns"

    gate.set()
    _wait(runner, run.id)


def test_work_runs_and_completion_is_recorded(runner, store, project):
    def work(run):
        store.complete_run(run, timeline_version=1)

    run = runner.submit(project.id, "analyze", work)
    _wait(runner, run.id)

    stored = store.get_run(project.id, run.id)
    assert stored.status == "completed"
    assert stored.timeline_version == 1


# -- failure paths ----------------------------------------------------------

def test_raising_work_records_a_terminal_failure(runner, store, project):
    """Anything escaping a worker thread is otherwise lost, leaving the run
    'running' in the log forever."""
    def work(run):
        raise ValueError("provider exploded")

    run = runner.submit(project.id, "analyze", work)
    _wait(runner, run.id)

    stored = store.get_run(project.id, run.id)
    assert stored.status == "failed"
    assert stored.error_code == "invalid_data"
    assert "ValueError" in stored.error
    # The message must NOT survive: provider and storage exceptions embed
    # presigned URLs, bucket names, and absolute server paths, and this record
    # is immutable in B2 and served verbatim by GET /runs/{id}.
    assert "provider exploded" not in stored.error


def test_failure_to_record_a_failure_does_not_kill_the_worker(runner, store,
                                                              project, caplog):
    """Storage can be down at exactly the moment we try to record that
    something went wrong. The thread must survive to serve the next job."""
    def exploding_fail_run(*_a, **_k):
        raise RuntimeError("B2 unreachable")

    store.fail_run = exploding_fail_run

    run = runner.submit(project.id, "analyze", lambda r: 1 / 0)
    _wait(runner, run.id)

    # The pool is still usable afterwards.
    done = threading.Event()
    ok = runner.submit(project.id, "generate", lambda r: done.set())
    _wait(runner, ok.id)
    assert done.is_set()


def test_a_failed_job_does_not_block_later_ones(runner, store, project):
    for _ in range(3):
        r = runner.submit(project.id, "analyze", lambda run: 1 / 0)
        _wait(runner, r.id)

    results = []
    r = runner.submit(project.id, "generate", lambda run: results.append("ran"))
    _wait(runner, r.id)
    assert results == ["ran"]


# -- in-flight tracking -----------------------------------------------------

def test_in_flight_job_is_discoverable(runner, project):
    """Backs the 409 already_running response."""
    gate = threading.Event()
    run = runner.submit(project.id, "analyze", lambda r: gate.wait(5))

    active = runner.active_for_project(project.id)
    assert active is not None and active.run_id == run.id
    assert runner.active_for_project(project.id, kind="analyze") is not None
    assert runner.active_for_project(project.id, kind="export") is None

    gate.set()
    _wait(runner, run.id)


def test_finished_jobs_are_swept(runner, store, project):
    """The table would otherwise grow for the life of the process."""
    run = runner.submit(project.id, "analyze", lambda r: store.complete_run(r))
    _wait(runner, run.id)

    assert runner.active_for_project(project.id) is None
    assert runner.get(run.id) is None, "swept once observed as finished"


def test_other_projects_do_not_appear_as_in_flight(runner, store, project):
    other = store.create_project(title="O", input_key="k", source_sha256="s2",
                                 source_filename="o.mp4")
    gate = threading.Event()
    run = runner.submit(project.id, "analyze", lambda r: gate.wait(5))

    assert runner.active_for_project(other.id) is None

    gate.set()
    _wait(runner, run.id)


# -- concurrency ------------------------------------------------------------

def test_concurrent_jobs_do_not_lose_events(runner, store):
    """Every run writes events for the same project concurrently. One object
    per event means no read-modify-write, so none may be lost."""
    project = store.create_project(title="C", input_key="k", source_sha256="c",
                                   source_filename="c.mp4")
    runs = [runner.submit(project.id, "generate", lambda r: store.complete_run(r),
                          exclusive=False)
            for _ in range(8)]
    for r in runs:
        _wait(runner, r.id)

    completed = {r.id for r in store.list_runs(project.id)}
    assert completed == {r.id for r in runs}, "every run must have a record"

    logged = {e.run_id for e in store.list_events(project.id) if e.run_id}
    assert logged == {r.id for r in runs}, "every run must appear in the log"


def test_run_ids_are_unique_under_concurrency(runner, store):
    project = store.create_project(title="U", input_key="k", source_sha256="u",
                                   source_filename="u.mp4")
    runs = [runner.submit(project.id, "generate", lambda r: None, exclusive=False)
            for _ in range(20)]
    for r in runs:
        _wait(runner, r.id)
    assert len({r.id for r in runs}) == 20


# -- the already_running race ----------------------------------------------

def test_second_submit_is_refused_while_one_is_in_flight(runner, project):
    gate = threading.Event()
    first = runner.submit(project.id, "analyze", lambda r: gate.wait(5))

    with pytest.raises(SlotTaken) as excinfo:
        runner.submit(project.id, "analyze", lambda r: None)
    assert excinfo.value.existing.run_id == first.id

    gate.set()
    _wait(runner, first.id)


def test_simultaneous_submits_admit_exactly_one(runner, store, project):
    """Regression: the guard used to check under the lock, release it, and then
    submit. A double-click or a client retry landed two requests that both saw
    no active job and both ran — two Gemini calls against a 20/day budget.
    """
    gate = threading.Event()
    ready = threading.Barrier(8)
    accepted, refused = [], []
    lock = threading.Lock()

    def attempt():
        ready.wait(5)
        try:
            run = runner.submit(project.id, "analyze", lambda r: gate.wait(5))
            with lock:
                accepted.append(run.id)
        except SlotTaken:
            with lock:
                refused.append(1)

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert len(accepted) == 1, f"exactly one may win, got {len(accepted)}"
    assert len(refused) == 7

    gate.set()
    _wait(runner, accepted[0])

    started = [e for e in store.list_events(project.id)
               if e.type == "postproduction.run.started"]
    assert len(started) == 1, "a refused submit must not write a start event"


def test_slot_is_released_after_the_job_finishes(runner, store, project):
    first = runner.submit(project.id, "analyze", lambda r: store.complete_run(r))
    _wait(runner, first.id)
    second = runner.submit(project.id, "analyze", lambda r: store.complete_run(r))
    _wait(runner, second.id)
    assert second.id != first.id


def test_a_failed_job_releases_the_slot(runner, store, project):
    """Otherwise one crash would wedge the project until restart."""
    first = runner.submit(project.id, "analyze", lambda r: 1 / 0)
    _wait(runner, first.id)
    runner.submit(project.id, "analyze", lambda r: store.complete_run(r))
