"""Offline tests for the project/run/version record set.

Runs against LocalBackend — the key space is identical to B2's, so anything
passing here behaves the same in the bucket.
"""

import pytest

from foley.models import Event as FoleyEvent, Timeline
from foleyflow import ids
from foleyflow.projects import (
    EVENT_PROJECT_CREATED,
    EVENT_QA_FLAGGED,
    STATUS_FAILED,
    STATUS_NEEDS_REVIEW,
    STATUS_READY,
    ProjectStore,
)
from foleyflow.storage import LocalBackend, StorageError


@pytest.fixture
def store(tmp_path):
    return ProjectStore(LocalBackend(str(tmp_path / "b2")), work_dir=str(tmp_path / "w"))


@pytest.fixture
def project(store):
    return store.create_project(
        title="Western standoff", input_key="assets/ab/cd/abcd.mp4",
        source_sha256="abcd" * 16, source_filename="standoff_kling.mp4",
        duration_s=15.04,
    )


def _timeline(*names):
    return Timeline(
        duration=15.04, ambience="desert wind",
        events=[FoleyEvent(name=n, start_time=i + 0.5, duration=1.0,
                           intensity="med", kind="discrete")
                for i, n in enumerate(names or ["boot step on gravel"])],
    )


# -- ids --------------------------------------------------------------------

def test_ulids_sort_in_creation_order():
    """Object storage gives us a sorted listing and nothing else — ordering
    has to come from the id itself."""
    earlier = ids.new_ulid(timestamp_ms=1_700_000_000_000)
    later = ids.new_ulid(timestamp_ms=1_700_000_001_000)
    assert earlier < later
    assert ids.timestamp_ms(earlier) == 1_700_000_000_000


def test_id_validation_rejects_malformed_and_wrong_kind():
    assert ids.is_valid(ids.new_project_id(), "proj")
    assert not ids.is_valid(ids.new_run_id(), "proj")
    assert not ids.is_valid("proj_../../etc/passwd", "proj")
    assert not ids.is_valid("proj_short", "proj")


def test_store_rejects_a_traversal_shaped_project_id(store):
    """The id becomes part of an object key."""
    with pytest.raises(ValueError):
        store.get_project("../../etc/passwd")


# -- projects ---------------------------------------------------------------

def test_create_and_read_back(store, project):
    loaded = store.get_project(project.id)
    assert loaded == project
    assert loaded.title == "Western standoff"


def test_creation_is_recorded_in_the_event_log(store, project):
    events = store.list_events(project.id)
    assert [e.type for e in events] == [EVENT_PROJECT_CREATED]
    assert events[0].payload["source_sha256"] == project.source_sha256


def test_projects_list_newest_first(store):
    a = store.create_project(title="A", input_key="k", source_sha256="1",
                             source_filename="a.mp4")
    b = store.create_project(title="B", input_key="k", source_sha256="2",
                             source_filename="b.mp4")
    assert [p.id for p in store.list_projects()] == [b.id, a.id]


def test_project_record_is_immutable(store, project):
    """A rerun of creation must not quietly rewrite identity."""
    with pytest.raises(StorageError, match="immutable"):
        store.create_project(title="Renamed", input_key="k", source_sha256="x",
                             source_filename="x.mp4", project_id=project.id)


# -- event log --------------------------------------------------------------

def test_events_are_returned_in_order(store, project):
    for i in range(5):
        store.append_event(project.id, f"postproduction.test.{i}")
    types = [e.type for e in store.list_events(project.id)]
    assert types[1:] == [f"postproduction.test.{i}" for i in range(5)]


def test_each_event_is_its_own_object(store, project):
    """One object per event: appending to a shared JSONL would need
    read-modify-write, and two concurrent writers would lose an event."""
    store.append_event(project.id, EVENT_QA_FLAGGED)
    keys = store.backend.list_keys(store.events_prefix(project.id))
    assert len(keys) == 2
    assert len(set(keys)) == 2


# -- timelines --------------------------------------------------------------

def test_timeline_versions_increment_and_coexist(store, project):
    v1 = store.save_timeline(project.id, _timeline("boot step"))
    v2 = store.save_timeline(project.id, _timeline("boot step", "gunshot"))
    assert (v1, v2) == (1, 2)
    assert store.list_timeline_versions(project.id) == [1, 2]

    # A repair must not destroy the plan it replaced.
    assert len(store.load_timeline(project.id, 1).events) == 1
    assert len(store.load_timeline(project.id, 2).events) == 2
    assert len(store.load_timeline(project.id).events) == 2, "default is latest"


def test_timeline_survives_a_round_trip(store, project):
    original = _timeline("boot step on wet gravel")
    version = store.save_timeline(project.id, original)
    loaded = store.load_timeline(project.id, version)
    assert loaded.ambience == original.ambience
    assert loaded.events[0].name == original.events[0].name
    assert loaded.events[0].start_time == original.events[0].start_time


def test_loading_without_a_timeline_fails_clearly(store, project):
    with pytest.raises(StorageError, match="no timeline"):
        store.load_timeline(project.id)


# -- runs -------------------------------------------------------------------

def test_run_object_appears_only_at_a_terminal_state(store, project):
    """Storage must never hold a run claiming to be 'running' after the
    process that owned it died."""
    run = store.start_run(project.id, "analyze")
    assert store.list_runs(project.id) == []
    assert any(e.run_id == run.id for e in store.list_events(project.id))

    store.complete_run(run, timeline_version=1)
    assert [r.id for r in store.list_runs(project.id)] == [run.id]
    assert store.get_run(project.id, run.id).status == "completed"


def test_failed_run_records_its_error(store, project):
    run = store.start_run(project.id, "analyze")
    store.fail_run(run, "provider 429")
    assert store.get_run(project.id, run.id).error == "provider 429"
    assert store.get_state(project.id).status == STATUS_FAILED


def test_repair_run_links_to_its_parent(store, project):
    first = store.complete_run(store.start_run(project.id, "generate"))
    repair = store.start_run(project.id, "repair", parent_run_id=first.id)
    assert store.complete_run(repair).parent_run_id == first.id


def test_completed_run_cannot_be_rewritten(store, project):
    run = store.start_run(project.id, "generate")
    store.complete_run(run)
    with pytest.raises(StorageError, match="immutable"):
        store.complete_run(run)


# -- exports ----------------------------------------------------------------

def test_latest_export_is_repointable(store, project):
    """The one sanctioned mutable pointer — it names an immutable run, so
    republishing does not destroy the previous export."""
    assert store.get_latest_export(project.id) is None
    store.set_latest_export(project.id, run_id="run_1", export_key="a/final.v1.mp4")
    store.set_latest_export(project.id, run_id="run_2", export_key="b/final.v2.mp4")
    latest = store.get_latest_export(project.id)
    assert latest["run_id"] == "run_2"
    assert latest["export_key"] == "b/final.v2.mp4"


# -- derived state ----------------------------------------------------------

def test_state_is_derived_not_stored(store, project):
    """No status field is ever written, so state cannot drift from history."""
    state = store.get_state(project.id)
    assert state.status == "new"
    assert state.timeline_version is None

    run = store.start_run(project.id, "analyze")
    store.save_timeline(project.id, _timeline(), run_id=run.id)
    store.complete_run(run, timeline_version=1)

    state = store.get_state(project.id)
    assert state.status == STATUS_NEEDS_REVIEW
    assert state.timeline_version == 1
    assert state.latest_run_id == run.id

    stored = store._get_json(store.project_key(project.id))
    assert "status" not in stored, "status must not be persisted"


def test_export_moves_the_project_to_ready(store, project):
    run = store.complete_run(store.start_run(project.id, "export"))
    store.set_latest_export(project.id, run_id=run.id, export_key="e/final.mp4")
    state = store.get_state(project.id)
    assert state.status == STATUS_READY
    assert state.has_export is True


def test_state_is_json_serializable_for_the_ui(store, project):
    import json
    json.dumps(store.get_state(project.id).to_dict())
