"""Regression tests for the defects found in the adversarial review.

One file so the set stays visible: each test names the failure it prevents, and
several encode behaviour that looked correct until someone raced it or pointed a
broken credential at it.
"""

import json
import os
import threading
from datetime import datetime, timedelta, timezone

import pytest

from foleyflow.models import Event as FoleyEvent, Timeline
from foleyflow import ids
from foleyflow.projects import (
    EVENT_RUN_FAILED_AFTER_COMPLETION,
    RUN_LEASE_S,
    STATUS_FAILED,
    STATUS_READY,
    ProjectStore,
)
from foleyflow.storage import LocalBackend, ObjectNotFound, PreconditionFailed, StorageError
from foleyflow.storage.base import validate_key


@pytest.fixture
def store(tmp_path):
    return ProjectStore(LocalBackend(str(tmp_path / "b2")), work_dir=str(tmp_path / "w"))


@pytest.fixture
def project(store):
    return store.create_project(title="T", input_key="k", source_sha256="s" * 64,
                                source_filename="f.mp4", duration_s=15.0)


def _timeline(tag):
    return Timeline(duration=15.0, ambience=tag, events=[
        FoleyEvent(name=tag, start_time=1.0, duration=1.0,
                   intensity="med", kind="discrete")])


# -- #1 concurrent version allocation ---------------------------------------

def test_concurrent_save_timeline_never_loses_a_plan(store, project):
    """Both writers used to compute v2, both saw it absent, and both wrote it —
    one plan destroyed with no error, and two plan.proposed events claiming the
    same version."""
    results, errors = [], []
    barrier = threading.Barrier(8)

    def save(tag):
        try:
            barrier.wait(5)
            results.append(store.save_timeline(project.id, _timeline(tag)))
        except Exception as e:                       # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=save, args=(f"plan-{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)

    assert not errors, errors
    assert sorted(results) == list(range(1, 9)), "each writer gets a distinct version"
    assert store.list_timeline_versions(project.id) == list(range(1, 9))

    stored = {store.load_timeline(project.id, v).ambience for v in results}
    assert len(stored) == 8, "every plan survives; none is overwritten"


def test_put_if_absent_admits_exactly_one_writer(tmp_path):
    backend = LocalBackend(str(tmp_path / "b2"))
    wins, losses = [], []
    barrier = threading.Barrier(10)

    def attempt(i):
        barrier.wait(5)
        try:
            backend.put_if_absent("a/contested.json", str(i).encode())
            wins.append(i)
        except PreconditionFailed:
            losses.append(i)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert len(wins) == 1 and len(losses) == 9


# -- #3 exists() must not fail open -----------------------------------------

def test_exists_raises_rather_than_reporting_absent_on_an_unknown_error():
    """Returning False for a 403 made every guard fail open: the immutability
    check stopped firing, the SFX cache re-billed all 161 clips, and the
    timeline cache re-spent the daily Gemini budget."""
    from foleyflow.storage.b2 import B2Backend

    backend = B2Backend.__new__(B2Backend)
    backend.bucket, backend.prefix = "b", ""

    class Denied(Exception):
        response = {"ResponseMetadata": {"HTTPStatusCode": 403},
                    "Error": {"Code": "AccessDenied"}}

    class Client:
        def head_object(self, **_):
            raise Denied()

    backend._client = Client()
    import foleyflow.storage.b2 as b2mod
    original = b2mod._boto_errors
    b2mod._boto_errors = lambda: (Denied,)
    try:
        with pytest.raises(StorageError):
            backend.exists("cache/real-object.wav")
    finally:
        b2mod._boto_errors = original


# -- #8 absent vs unreachable ------------------------------------------------

def test_missing_object_and_broken_backend_are_different_errors(tmp_path):
    backend = LocalBackend(str(tmp_path / "b2"))
    with pytest.raises(ObjectNotFound):
        backend.get_bytes("nope.json")
    assert issubclass(ObjectNotFound, StorageError), "existing handlers keep working"


# -- #11 fail_run after complete_run ----------------------------------------

def test_failure_after_completion_is_recorded_without_rewriting_the_run(store, project):
    """Work that raises *after* complete_run succeeded used to make fail_run
    raise from inside the error handler, leaving the run recorded as completed
    with no failure anywhere — §12's "never a silently incomplete complete
    project", inverted."""
    run = store.start_run(project.id, "analyze")
    store.complete_run(run)

    store.fail_run(run, "exploded after completion")      # must not raise

    assert store.get_run(project.id, run.id).status == "completed"
    types = [e.type for e in store.list_events(project.id)]
    assert EVENT_RUN_FAILED_AFTER_COMPLETION in types


# -- #10 stale runs ----------------------------------------------------------

def test_a_run_past_its_lease_stops_reading_as_live(store, project):
    """Cloud Run SIGTERMs on deploy, so a crashed run is expected. Without a
    lease the project reads `analyzing` forever with no record of failure."""
    store.start_run(project.id, "analyze")
    assert store.get_state(project.id).status == "analyzing"

    later = datetime.now(timezone.utc) + timedelta(seconds=RUN_LEASE_S + 60)
    state = store.get_state(project.id, now=later)
    assert state.status == STATUS_FAILED
    assert state.stale_run_ids


# -- #12 the fold respects run kind ------------------------------------------

def test_a_repair_run_does_not_regress_a_published_export(store, project):
    """A shipped project used to drop to `analyzing` the moment a user clicked
    Regenerate, with no path back to `ready`."""
    export_run = store.complete_run(store.start_run(project.id, "export"))
    store.set_latest_export(project.id, run_id=export_run.id, export_key="e/f.mp4")
    assert store.get_state(project.id).status == STATUS_READY

    repair = store.start_run(project.id, "repair")
    assert store.get_state(project.id).status != "analyzing"

    store.complete_run(repair)
    state = store.get_state(project.id)
    assert state.status == STATUS_READY, "the export is still published"
    assert state.has_export is True


def test_one_failed_run_does_not_mask_a_successful_one(store, project):
    """Status used to be whichever event was written last."""
    good = store.start_run(project.id, "analyze")
    bad = store.start_run(project.id, "generate")
    store.save_timeline(project.id, _timeline("plan"), run_id=good.id)
    store.complete_run(good, timeline_version=1)
    store.fail_run(bad, "boom")

    assert store.get_state(project.id).timeline_version == 1


# -- #9 source hash arrives after creation -----------------------------------

def test_source_hash_and_duration_come_from_the_event_log(store):
    """Project is frozen and is written before the upload exists, so these can
    only live in the log — otherwise readiness never reaches 100%."""
    p = store.create_project(title="T", input_key="k", source_sha256="",
                             source_filename="f.mp4")
    assert store.get_state(p.id).source_sha256 is None
    assert store.get_state(p.id).status == "awaiting_upload"

    store.record_input(p.id, sha256="a" * 64, duration_s=15.04, size_bytes=100)
    state = store.get_state(p.id)
    assert state.source_sha256 == "a" * 64
    assert state.duration_s == 15.04


# -- #18 / #19 id and key validation -----------------------------------------

@pytest.mark.parametrize("bad", [
    "run_../../../etc/evil", "run_short", "notarun", "run_" + "a" * 26,
])
def test_run_ids_are_validated_before_becoming_keys(store, project, bad):
    with pytest.raises(ValueError):
        store.run_key(project.id, bad)


@pytest.mark.parametrize("bad", ["scene_../../evil", "scene_x", "../evil"])
def test_scene_ids_are_validated_before_becoming_keys(store, project, bad):
    with pytest.raises(ValueError):
        store.scene_thumbnail_key(project.id, bad)


def test_negative_timeline_versions_are_rejected(store, project):
    for bad in (-1, 0, "1"):
        with pytest.raises(ValueError):
            store.timeline_key(project.id, bad)


def test_key_validation_is_shared_by_every_backend():
    """LocalBackend rejecting traversal while B2Backend forwarded it meant
    safety depended on which backend was configured."""
    for bad in ("../escape", "a/../../escape", "/abs", "a//b", "", "C:/x", "a\0b"):
        with pytest.raises(StorageError):
            validate_key(bad)


def test_lowercase_ulids_are_rejected(store):
    """S3 keys are case-sensitive, so a lowercase variant creates a second,
    shadowing project. Windows' case-insensitive filesystem hides this
    locally — it would only appear against B2."""
    real = ids.new_project_id()
    assert ids.is_valid(real, "proj")
    assert not ids.is_valid(real.lower(), "proj")
    with pytest.raises(ValueError):
        store.get_project(real.lower())


# -- #13 ffmpeg failures are not single-shot clips ---------------------------

def test_scene_detection_raises_on_a_broken_file(tmp_path):
    """A failed probe produced no cut markers, which was indistinguishable from
    a genuine single-shot clip — so an unreadable video silently became one
    fabricated scene and ANALYSIS_COMPLETED was recorded for it."""
    from foleyflow.scenes import SceneDetectionError, detect_scenes

    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"this is not a video")
    with pytest.raises(SceneDetectionError):
        detect_scenes(str(broken), 5.0)


# -- #4 truncated cache entries ---------------------------------------------

def test_zero_length_local_file_is_not_treated_as_a_cache_hit(tmp_path):
    from foleyflow.routing import SfxRequest
    from foleyflow import sfx_cache

    backend = LocalBackend(str(tmp_path / "b2"))
    work = str(tmp_path / "work")
    os.makedirs(work, exist_ok=True)

    req = SfxRequest(endpoint="cassetteai/sound-effects-generator",
                     prompt="rain", duration_s=8.0, category="ambience")
    key = sfx_cache.cache_object_key(req.endpoint, req.prompt, req.duration_s)
    # A crash part-way through a write leaves exactly this.
    open(os.path.join(work, os.path.basename(key)), "wb").close()

    calls = []

    def fake_generate(request, dest_path):
        calls.append(1)
        with open(dest_path, "wb") as f:
            f.write(b"real-audio")
        return dest_path

    import foleyflow.sfx_cache as mod
    original, mod.generate_canonical_wav = mod.generate_canonical_wav, fake_generate
    try:
        path = sfx_cache.backed_sfx_fn(backend, work)(req)
    finally:
        mod.generate_canonical_wav = original

    assert calls == [1], "an empty file must not count as a cached stem"
    assert open(path, "rb").read() == b"real-audio"


# -- ULID monotonicity (found by a flaky test, not by the review) -----------

def test_ulids_generated_in_a_tight_loop_stay_ordered():
    """Sort order IS the event log's order. Several events are routinely
    appended inside one millisecond — a run that starts, proposes a plan, and
    completes — and with independently random suffixes they came back shuffled,
    so the audit trail could show a run completing before it started.
    """
    generated = [ids.new_ulid() for _ in range(2000)]
    assert generated == sorted(generated)
    assert len(set(generated)) == len(generated), "ids must stay unique"


def test_ulids_stay_ordered_across_threads():
    out, lock = [], threading.Lock()

    def make():
        local = [ids.new_ulid() for _ in range(200)]
        with lock:
            out.extend(local)

    threads = [threading.Thread(target=make) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert len(set(out)) == len(out), "concurrent generation must not collide"


def test_event_log_order_survives_rapid_appends(store, project):
    """The end-to-end version of the above, through storage."""
    for i in range(40):
        store.append_event(project.id, f"postproduction.seq.{i:02d}")
    seq = [e.type for e in store.list_events(project.id)
           if e.type.startswith("postproduction.seq.")]
    assert seq == [f"postproduction.seq.{i:02d}" for i in range(40)]


# -- found by the first live run against real B2 -----------------------------
#
# None of these could fail offline: LocalBackend implements conditional writes
# correctly, so the suite exercised a code path that no shipped backend takes.

def _b2_stub(*, conditional_status):
    """A B2Backend wired to a fake S3 client.

    `conditional_status` is what the endpoint answers to a PutObject carrying
    `If-None-Match: *` — 501 is what Backblaze B2 really returns.
    """
    import foleyflow.storage.b2 as b2mod

    code = "NotImplemented" if conditional_status == 501 else "AccessDenied"

    class Refused(Exception):
        response = {"ResponseMetadata": {"HTTPStatusCode": conditional_status},
                    "Error": {"Code": code}}

    class Client:
        def __init__(self):
            self.objects = {}
            self.conditional_attempts = 0

        def put_object(self, **kw):
            if "IfNoneMatch" in kw:
                self.conditional_attempts += 1
                raise Refused()
            self.objects[kw["Key"]] = kw["Body"]

        def head_object(self, **kw):
            if kw["Key"] not in self.objects:
                raise _NotFound()
            return {}

    class _NotFound(Exception):
        response = {"ResponseMetadata": {"HTTPStatusCode": 404},
                    "Error": {"Code": "404"}}

    backend = b2mod.B2Backend.__new__(b2mod.B2Backend)
    backend.bucket, backend.prefix = "b", ""
    backend._conditional_put = True
    backend._client = Client()
    return backend, b2mod, (Refused, _NotFound)


def test_b2_falls_back_when_the_endpoint_rejects_conditional_writes():
    """Backblaze answers 501 to `If-None-Match: *`. Propagating that as a
    StorageError meant every immutable write failed against the only backend
    that ships: no run ever recorded a terminal state, no timeline version
    could be allocated, and a mix could not complete."""
    backend, b2mod, errors = _b2_stub(conditional_status=501)
    original = b2mod._boto_errors
    b2mod._boto_errors = lambda: errors
    try:
        backend.put_if_absent("projects/p/runs/r/run.json", b"{}")
        assert backend._client.objects, "the object must still be written"

        # Already present: the guard has to survive the downgrade, or the
        # immutability rule quietly becomes "last writer wins".
        with pytest.raises(PreconditionFailed):
            backend.put_if_absent("projects/p/runs/r/run.json", b"{}")

        # And the doomed conditional round trip is not repeated per write.
        assert backend._client.conditional_attempts == 1
    finally:
        b2mod._boto_errors = original


def test_b2_still_fails_closed_on_an_unexpected_conditional_error():
    """Only 501 downgrades. A 403 must not be read as "no conditional support"
    and turned into an unconditional overwrite."""
    backend, b2mod, errors = _b2_stub(conditional_status=403)
    original = b2mod._boto_errors
    b2mod._boto_errors = lambda: errors
    try:
        with pytest.raises(StorageError):
            backend.put_if_absent("projects/p/runs/r/run.json", b"{}")
        assert not backend._client.objects
    finally:
        b2mod._boto_errors = original


def test_a_write_failure_is_not_filed_as_already_completed(store, project):
    """`fail_run` caught every StorageError as "the run already finished", so a
    genuine write failure left no run.json and no `run.failed` event."""
    class Broken(LocalBackend):
        def put_if_absent(self, key, data, content_type=None):
            raise StorageError("backend unreachable")

    run = store.start_run(project.id, "mix")
    store.backend = Broken(store.backend.root)

    with pytest.raises(StorageError):
        store.fail_run(run, "boom")

    types = [e.type for e in store.list_events(project.id)]
    assert EVENT_RUN_FAILED_AFTER_COMPLETION not in types, \
        "a broken backend is not evidence that the run completed"


def test_a_failed_run_with_no_record_does_not_poll_as_running(store, project):
    """`GET /runs/{id}` fell back to "running" whenever run.json was missing, so
    a `--wait` client polled a dead run until it was killed."""
    from fastapi.testclient import TestClient

    from foleyflow.api.app import API_PREFIX, create_app
    from foleyflow.api.jobs import JobRunner

    run = store.start_run(project.id, "mix")
    # The state fail_run leaves behind when the record write itself fails.
    store.append_event(project.id, EVENT_RUN_FAILED_AFTER_COMPLETION,
                       run_id=run.id,
                       payload={"kind": "mix", "error": "MissingCredentialsError",
                                "error_code": "internal_error"})

    runner = JobRunner(store, max_workers=1)
    with TestClient(create_app(store, runner),
                    raise_server_exceptions=False) as c:
        body = c.get(f"{API_PREFIX}/projects/{project.id}/runs/{run.id}").json()
    runner.shutdown(wait=True)

    assert body["status"] == "failed"
    assert body["error_code"] == "internal_error"
