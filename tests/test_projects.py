"""Offline tests for the project/run/version record set.

Runs against LocalBackend — the key space is identical to B2's, so anything
passing here behaves the same in the bucket.
"""

import pytest

from foleyflow.models import Event as FoleyEvent, Timeline
from foleyflow import ids
from foleyflow.projects import (
    EVENT_INPUT_RECORDED,
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
    assert events[0].type == EVENT_PROJECT_CREATED
    # The hash arrives as a separate input.recorded event: the project record
    # is written before the upload exists, so sha256 is not knowable at
    # creation and Project is frozen. get_state reads it from the log.
    recorded = [e for e in events if e.type == EVENT_INPUT_RECORDED]
    assert recorded and recorded[0].payload["sha256"] == project.source_sha256


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
    before = len(store.list_events(project.id))
    for i in range(5):
        store.append_event(project.id, f"postproduction.test.{i}")
    types = [e.type for e in store.list_events(project.id)]
    assert types[before:] == [f"postproduction.test.{i}" for i in range(5)]


def test_each_event_is_its_own_object(store, project):
    """One object per event: appending to a shared JSONL would need
    read-modify-write, and two concurrent writers would lose an event."""
    before = len(store.backend.list_keys(store.events_prefix(project.id)))
    store.append_event(project.id, EVENT_QA_FLAGGED)
    keys = store.backend.list_keys(store.events_prefix(project.id))
    assert len(keys) == before + 1
    assert len(set(keys)) == len(keys), "each event is a distinct object"


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


# -- scenes -----------------------------------------------------------------

def test_scenes_round_trip_and_log_completion(store, project):
    from foleyflow.projects import EVENT_ANALYSIS_COMPLETED
    from foleyflow.scenes import Scene, SceneStats

    scenes = [
        Scene(id="scene_01", index=1, start_s=0.0, end_s=3.67,
              stats=SceneStats(mean_luma=133.1, mean_rgb=[136.6, 133.9, 120.1],
                               luma_stddev=1.9, sample_count=7)),
        Scene(id="scene_02", index=2, start_s=3.67, end_s=6.67,
              stats=SceneStats(mean_luma=122.5, mean_rgb=[125.3, 123.7, 108.8],
                               luma_stddev=36.8, sample_count=6)),
    ]
    store.save_scenes(project.id, scenes)

    loaded = store.load_scenes(project.id)
    assert [s["id"] for s in loaded] == ["scene_01", "scene_02"]
    assert loaded[0]["stats"]["mean_luma"] == 133.1
    assert loaded[1]["end_s"] == 6.67
    assert EVENT_ANALYSIS_COMPLETED in [e.type for e in store.list_events(project.id)]


def test_load_scenes_before_analysis_is_empty_not_an_error(store, project):
    assert store.load_scenes(project.id) == []


# -- QA ---------------------------------------------------------------------

def test_qa_report_round_trips_and_flags_the_log(store, project):
    from foleyflow.qa import build_report
    from foleyflow.scenes import Scene, SceneStats

    scenes = [
        Scene(id="scene_01", index=1, start_s=0.0, end_s=1.0,
              stats=SceneStats(mean_luma=40.0, mean_rgb=[40, 40, 40],
                               luma_stddev=1.0, sample_count=4)),
        Scene(id="scene_02", index=2, start_s=1.0, end_s=2.0,
              stats=SceneStats(mean_luma=90.0, mean_rgb=[90, 90, 90],
                               luma_stddev=1.0, sample_count=4)),
    ]
    report = build_report(scenes, run_id="run_01TESTQA000000000000000000")
    store.save_qa_report(project.id, report)

    loaded = store.load_qa_report(project.id)
    assert loaded["overall_status"] == "needs_review"
    assert loaded["visual"]["issues"][0]["code"] == "exposure_discontinuity"
    assert EVENT_QA_FLAGGED in [e.type for e in store.list_events(project.id)]
    assert store.get_state(project.id).status == STATUS_NEEDS_REVIEW


def test_clean_qa_report_clears_a_prior_flag(store, project):
    from foleyflow.qa import AudioQa, QaIssue, QaReport, VisualQa, STATUS_BLOCKED
    from foleyflow.projects import EVENT_QA_CLEARED

    store.save_qa_report(project.id, QaReport(
        run_id=None, overall_status=STATUS_BLOCKED,
        audio=AudioQa(
            integrated_lufs=-10.0, true_peak_dbtp=1.4, measured=True,
            issues=[QaIssue(severity="blocking", code="true_peak_exceeded")]),
        visual=VisualQa(),
    ))
    assert EVENT_QA_FLAGGED in [e.type for e in store.list_events(project.id)]

    from foleyflow.qa import build_report
    from foleyflow.scenes import Scene, SceneStats

    scenes = [
        Scene(id="scene_01", index=1, start_s=0.0, end_s=1.0,
              stats=SceneStats(mean_luma=100.0, mean_rgb=[100, 100, 100],
                               luma_stddev=1.0, sample_count=4)),
        Scene(id="scene_02", index=2, start_s=1.0, end_s=2.0,
              stats=SceneStats(mean_luma=105.0, mean_rgb=[105, 105, 105],
                               luma_stddev=1.0, sample_count=4)),
    ]
    # Continuity-only clean write must not wipe the measured audio — so overall
    # stays blocked from the preserved true-peak. Clear audio explicitly to
    # exercise the cleared event.
    store.save_qa_report(project.id, QaReport(
        run_id=None, overall_status="ok",
        audio=AudioQa(integrated_lufs=-14.0, true_peak_dbtp=-2.0, measured=True),
        visual=build_report(scenes).visual,
    ))
    types = [e.type for e in store.list_events(project.id)]
    assert EVENT_QA_CLEARED in types
    assert types.index(EVENT_QA_CLEARED) > types.index(EVENT_QA_FLAGGED)
    # No timeline: cleared flag must not leave the project in needs_review.
    assert store.get_state(project.id).status == "new"


def test_continuity_only_save_preserves_measured_audio(store, project):
    """Re-analysis must not erase a true-peak block by overwriting with visuals."""
    from foleyflow.qa import (
        AudioQa, QaIssue, QaReport, VisualQa, STATUS_BLOCKED, build_report,
    )
    from foleyflow.scenes import Scene, SceneStats

    store.save_qa_report(project.id, QaReport(
        run_id="run_01MIX00000000000000000000000",
        overall_status=STATUS_BLOCKED,
        audio=AudioQa(
            integrated_lufs=-12.0, true_peak_dbtp=1.4, measured=True,
            measured_run_id="run_01MIX00000000000000000000000",
            issues=[QaIssue(severity="blocking", code="true_peak_exceeded")]),
        visual=VisualQa(),
    ))

    scenes = [
        Scene(id="scene_01", index=1, start_s=0.0, end_s=1.0,
              stats=SceneStats(mean_luma=40.0, mean_rgb=[40, 40, 40],
                               luma_stddev=1.0, sample_count=4)),
        Scene(id="scene_02", index=2, start_s=1.0, end_s=2.0,
              stats=SceneStats(mean_luma=90.0, mean_rgb=[90, 90, 90],
                               luma_stddev=1.0, sample_count=4)),
    ]
    store.save_qa_report(project.id, build_report(scenes, run_id="run_01ANALYZE0000000000000000"))
    loaded = store.load_qa_report(project.id)
    assert loaded["audio"]["measured"] is True
    assert loaded["audio"]["true_peak_dbtp"] == 1.4
    assert loaded["audio"]["measured_run_id"] == "run_01MIX00000000000000000000000"
    assert loaded["run_id"] == "run_01ANALYZE0000000000000000"
    assert any(i["code"] == "true_peak_exceeded" for i in loaded["audio"]["issues"])
    assert any(i["code"] == "exposure_discontinuity" for i in loaded["visual"]["issues"])
    assert loaded["overall_status"] == "blocked"


def test_identical_qa_save_does_not_duplicate_events(store, project):
    from foleyflow.qa import build_report
    from foleyflow.scenes import Scene, SceneStats

    scenes = [
        Scene(id="scene_01", index=1, start_s=0.0, end_s=1.0,
              stats=SceneStats(mean_luma=40.0, mean_rgb=[40, 40, 40],
                               luma_stddev=1.0, sample_count=4)),
        Scene(id="scene_02", index=2, start_s=1.0, end_s=2.0,
              stats=SceneStats(mean_luma=90.0, mean_rgb=[90, 90, 90],
                               luma_stddev=1.0, sample_count=4)),
    ]
    store.save_qa_report(project.id, build_report(scenes, run_id="run_a"))
    before = len(store.list_events(project.id))
    store.save_qa_report(project.id, build_report(scenes, run_id="run_b"))
    assert len(store.list_events(project.id)) == before
    assert store.load_qa_report(project.id)["run_id"] == "run_b"


def test_failed_analysis_is_not_masked_by_a_qa_flag(store, project):
    """A mid-flight QA flag without a timeline must not become needs_review."""
    run = store.start_run(project.id, "analyze")
    store.append_event(project.id, EVENT_QA_FLAGGED, run_id=run.id,
                       payload={"overall_status": "needs_review"})
    store.fail_run(run, "boom")
    assert store.get_state(project.id).status == STATUS_FAILED


def test_load_qa_before_any_report_is_none(store, project):
    assert store.load_qa_report(project.id) is None


def test_list_events_concurrent_fetch_is_chronological_and_complete(store, project):
    """Concurrent multi-get must not reorder or drop events (unit 7.18)."""
    for i in range(15):
        store.append_event(project.id, f"postproduction.test.bulk.{i}",
                           payload={"i": i})
    events = store.list_events(project.id)
    bulk = [e for e in events if e.type.startswith("postproduction.test.bulk.")]
    assert len(bulk) == 15
    assert [e.payload["i"] for e in bulk] == list(range(15))
    # ULID ids are creation-ordered; concurrent fetch must preserve that.
    assert [e.id for e in events] == sorted(e.id for e in events)


def test_event_log_cache_reused_until_append(store, project, monkeypatch):
    """Second list_events must not re-GET bodies when the key set is stable."""
    store.append_event(project.id, "postproduction.test.cached", payload={"n": 1})
    # Prime the cache.
    first = store.list_events(project.id)
    assert any(e.type == "postproduction.test.cached" for e in first)

    gets = {"n": 0}
    real_get = store.backend.get_bytes

    def counting_get(key):
        gets["n"] += 1
        return real_get(key)

    monkeypatch.setattr(store.backend, "get_bytes", counting_get)
    second = store.list_events(project.id)
    assert [e.id for e in second] == [e.id for e in first]
    assert gets["n"] == 0, "stable key set should hit the event-log checkpoint"


def test_event_log_cache_invalidates_on_append(store, project, monkeypatch):
    """append_event must clear the checkpoint so the new event is visible."""
    store.list_events(project.id)  # prime
    store.append_event(project.id, "postproduction.test.after_cache",
                       payload={"n": 2})
    events = store.list_events(project.id)
    assert any(e.type == "postproduction.test.after_cache" for e in events)
    # State fold must also see it (never serve pre-append snapshot).
    state = store.get_state(project.id)
    assert state.event_count == len(events)


def test_get_state_sees_just_appended_event(store, project):
    """Same-process append then get_state must include the new event."""
    before = store.get_state(project.id).event_count
    store.append_event(project.id, "postproduction.test.immediate",
                       payload={})
    after = store.get_state(project.id)
    assert after.event_count == before + 1
