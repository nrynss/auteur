"""Project-scoped rendering: mix, QA, manifest, export.

Two layers, deliberately:

* Offline tests stub ffmpeg and provider generation and assert the *wiring* —
  which keys artifacts land under, what the event log says, what the manifest
  claims, and what the export gate refuses.
* One `needs_ffmpeg` test runs the real thing end to end on a generated clip:
  video in, muxed Foley video out of B2. It is slower and it is the only test
  that proves the milestone's exit condition, so it earns its seconds.

Nothing here touches the network. SFX generation is replaced by synthesized
WAVs at the two provider seams — `stems.generate_many` (the Genblaze fan-out
the render warms its cache with) and `sfx_cache.generate_canonical_wav` (the
per-stem fallback) — which are the only billed calls in the whole path.
Everything above them, including the shared cache and the stage recording,
runs for real.
"""

import dataclasses
import os
import pathlib
import shutil
import subprocess
import tempfile

import numpy as np
import pytest
import soundfile as sf

from foleyflow import qa, render, sfx_cache, stems
from foleyflow.models import Event, Timeline
from foleyflow.projects import EVENT_MIX_COMPLETED, EVENT_STEM_GENERATED, ProjectStore
from foleyflow.storage import LocalBackend

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not on PATH")

SR = 48000


@pytest.fixture
def store(tmp_path):
    return ProjectStore(LocalBackend(str(tmp_path / "b2")),
                        work_dir=str(tmp_path / "w"))


@pytest.fixture
def project(store):
    project = store.create_project(title="Standoff", input_key="in/source.mp4",
                                   source_sha256="a" * 64,
                                   source_filename="standoff.mp4")
    store.record_input(project.id, sha256="a" * 64, duration_s=4.0,
                       size_bytes=1024)
    store.save_timeline(project.id, _timeline())
    return project


def _timeline(duration=4.0):
    return Timeline(duration=duration, ambience="desert wind", events=[
        Event(name="holster creak", start_time=0.5, duration=0.5,
              intensity="low", kind="discrete"),
        # The same name twice, on purpose: names repeat, so anything keyed on
        # them would silently collapse two events into one.
        Event(name="holster creak", start_time=2.0, duration=0.5,
              intensity="low", kind="discrete"),
        Event(name="gunshot", start_time=3.0, duration=0.8,
              intensity="high", kind="discrete"),
    ])


def _write_wav(path, seconds=1.0, amplitude=0.1):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    n = int(SR * seconds)
    tone = amplitude * np.sin(2 * np.pi * 220 * np.arange(n) / SR)
    sf.write(path, np.column_stack([tone, tone]).astype(np.float32), SR,
             format="WAV", subtype="FLOAT")
    return path


def _placeholder_video(path):
    """Stands in for the source when ffmpeg is stubbed out.

    The stubs never read it — only its presence matters — so this stays bytes
    on disk rather than a WAV wearing an .mp4 name, which would be a lie the
    real-ffmpeg test could not tell.
    """
    with open(path, "wb") as fh:
        fh.write(b"\0" * 64)
    return path


@pytest.fixture
def fake_generation(monkeypatch):
    """Replace the billed calls with synthesized clips.

    Two seams, because a render reaches the provider two ways and both must be
    stubbed or the suite would try to talk to a GPU worker:

    * `stems.generate_many` — the Genblaze fan-out the render warms its cache
      with. Stubbed at the *pipeline* boundary, so `sfx_cache.prewarm`'s cache
      probing, canonicalization and B2 write all still run for real.
    * `sfx_cache.generate_canonical_wav` — the per-stem path `backed_sfx_fn`
      falls back to when assembly asks for something the warm-up did not
      produce.

    Both append to the same list, so "how many stems did this run generate"
    stays one number regardless of which path produced them. Everything between
    — the shared content-addressed cache, the local scratch reuse, the observer
    that reports hits — is code this module is here to exercise.
    """
    calls = []

    def _generate(request, dest_path):
        calls.append(request)
        return _write_wav(dest_path, seconds=max(request.duration_s, 0.5))

    def _generate_many(requests, **kwargs):
        results = []
        for request in requests:
            calls.append(request)
            raw = str(tmp_raw_dir / f"{len(calls)}.wav")
            _write_wav(raw, seconds=max(request.duration_s, 0.5))
            results.append(stems.StemResult(
                request=request, served=request, local_path=raw,
                attempts=[stems.StemAttempt(
                    tier=request.tier, endpoint=request.endpoint,
                    status="succeeded", genblaze_run_id="gb-test-run")]))
        return results

    tmp_raw_dir = pathlib.Path(tempfile.mkdtemp(prefix="foleyflow-raw-"))
    monkeypatch.setattr(stems, "generate_many", _generate_many)
    monkeypatch.setattr(sfx_cache, "generate_canonical_wav", _generate)
    return calls


@pytest.fixture
def stub_ffmpeg(monkeypatch):
    """No ffmpeg: no source audio bed, a stub mux, and a fixed measurement."""
    monkeypatch.setattr(render, "extract_audio", lambda *a, **k: False)

    def _mux(video_path, wav_path, out_path):
        shutil.copyfile(wav_path, out_path)
        return out_path

    monkeypatch.setattr(render, "mux_video", _mux)
    monkeypatch.setattr(qa, "measure_loudness", lambda path: qa.AudioMetrics(
        integrated_lufs=-14.2, true_peak_dbtp=-1.6, loudness_range_lu=5.0))


def _run(store, project_id, kind="mix"):
    """A started run, without going through the HTTP layer."""
    return store.start_run(project_id, kind)


# -- the mix -----------------------------------------------------------------

def test_mix_stores_artifacts_under_the_run(store, project, fake_generation,
                                            stub_ffmpeg, tmp_path):
    video = str(tmp_path / "source.mp4")
    _placeholder_video(video)
    run = _run(store, project.id)

    result = render.render_mix(store, project.id, run=run, video_path=video)

    assert result.mix_key == f"projects/{project.id}/runs/{run.id}/mix.wav"
    assert result.video_key == f"projects/{project.id}/runs/{run.id}/foley.mp4"
    assert store.backend.exists(result.mix_key)
    assert store.backend.exists(result.video_key)
    # v2, not v1: a completed mix stamps the durations it actually generated
    # onto a new version and records itself against that one. v2 is the
    # document that names this mix's cache addresses, and it renders
    # byte-identically (see test_mix_freeze_stamp_renders_identically).
    assert result.timeline_version == 2
    assert store.list_timeline_versions(project.id) == [1, 2]


def test_mix_uses_the_ffprobe_duration_not_the_timelines(store, project,
                                                         fake_generation,
                                                         stub_ffmpeg, tmp_path,
                                                         monkeypatch):
    """The recorded duration wins. A model's self-reported one drifts against
    picture, and the mix length is what the video is muxed to."""
    seen = {}

    real = render.assemble_timeline

    def _spy(timeline, clip_duration_s, **kw):
        seen["duration"] = clip_duration_s
        return real(timeline, clip_duration_s, **kw)

    monkeypatch.setattr(render, "assemble_timeline", _spy)
    store.save_timeline(project.id, _timeline(duration=99.0))   # v2, lying

    video = str(tmp_path / "source.mp4")
    _placeholder_video(video)
    render.render_mix(store, project.id, run=_run(store, project.id),
                      video_path=video)

    assert seen["duration"] == 4.0          # from record_input, not the timeline


def test_mix_records_the_stem_inventory_and_only_bills_once(store, project,
                                                            fake_generation,
                                                            stub_ffmpeg, tmp_path):
    """Two events share one request; the shared cache must bill it once."""
    video = str(tmp_path / "source.mp4")
    _placeholder_video(video)
    run = _run(store, project.id)

    result = render.render_mix(store, project.id, run=run, video_path=video)

    inventory = store.load_stem_inventory(project.id, run.id)
    assert inventory and len(inventory) == len(result.stems)
    assert all(s["b2_key"].startswith("cache/") for s in inventory)
    assert all(s["sha256"] for s in inventory)
    # Every distinct request generated exactly once, and every stem landed in
    # the shared cache rather than only in this run's scratch.
    assert len(fake_generation) == len(inventory)
    for stem in inventory:
        assert store.backend.exists(stem["b2_key"])


def test_a_second_mix_is_free(store, project, fake_generation, stub_ffmpeg,
                              tmp_path):
    """The whole cost argument in §9 rests on this."""
    video = str(tmp_path / "source.mp4")
    _placeholder_video(video)
    render.render_mix(store, project.id, run=_run(store, project.id),
                      video_path=video)
    first_pass = len(fake_generation)

    second = render.render_mix(store, project.id, run=_run(store, project.id),
                               video_path=video)

    assert len(fake_generation) == first_pass       # nothing regenerated
    assert second.generated_count == 0
    assert all(s["cached"] for s in second.stems)


def test_mix_logs_spend_but_not_cache_hits(store, project, fake_generation,
                                           stub_ffmpeg, tmp_path):
    video = str(tmp_path / "source.mp4")
    _placeholder_video(video)
    render.render_mix(store, project.id, run=_run(store, project.id),
                      video_path=video)
    render.render_mix(store, project.id, run=_run(store, project.id),
                      video_path=video)

    generated = [e for e in store.list_events(project.id)
                 if e.type == EVENT_STEM_GENERATED]
    mixes = [e for e in store.list_events(project.id)
             if e.type == EVENT_MIX_COMPLETED]
    assert len(generated) == len(fake_generation)   # first run only
    assert len(mixes) == 2


def test_mix_measures_the_written_wav(store, project, fake_generation,
                                      stub_ffmpeg, tmp_path):
    video = str(tmp_path / "source.mp4")
    _placeholder_video(video)
    result = render.render_mix(store, project.id, run=_run(store, project.id),
                               video_path=video)

    assert result.qa["audio"]["measured"] is True
    assert result.qa["audio"]["integrated_lufs"] == -14.2
    assert store.load_qa_report(project.id)["audio"]["measured_run_id"] == result.run_id


def test_mix_without_a_timeline_is_refused(store, fake_generation, stub_ffmpeg,
                                           tmp_path):
    from foleyflow.storage.base import ObjectNotFound

    bare = store.create_project(title="No plan", input_key="in/source.mp4",
                                source_sha256="b" * 64, source_filename="x.mp4")
    video = str(tmp_path / "source.mp4")
    _placeholder_video(video)
    with pytest.raises(ObjectNotFound):
        render.render_mix(store, bare.id, run=_run(store, bare.id),
                          video_path=video)


def test_variant_forces_one_stem_to_regenerate(store, project, fake_generation,
                                               stub_ffmpeg, tmp_path):
    """The repair loop's seam: one event regenerates, the rest stay cached."""
    video = str(tmp_path / "source.mp4")
    _placeholder_video(video)
    render.render_mix(store, project.id, run=_run(store, project.id),
                      video_path=video)
    baseline = len(fake_generation)

    repaired = render.render_mix(
        store, project.id, run=_run(store, project.id), video_path=video,
        variant_fn=lambda req: "r2" if "gunshot" in req.prompt.lower() else "")

    assert len(fake_generation) == baseline + 1
    assert repaired.generated_count == 1


def test_repair_route_changes_only_the_target_stem(store, project, stub_ffmpeg,
                                                   tmp_path, monkeypatch):
    """SPEC §18 criterion 5 end to end through the HTTP route: one stem's
    address (and bytes) change, every other stem's b2_key is identical across
    the two runs, and the lineage names the superseded take."""
    import time

    from fastapi.testclient import TestClient

    from foleyflow import sfx_cache, stems
    from foleyflow import ledger as ledger_mod
    from foleyflow.api.app import API_PREFIX, create_app
    from foleyflow.api.jobs import JobRunner
    from foleyflow.projects import EVENT_REPAIR_APPROVED

    # Keep the ledger on a local JSONL so budget/attribution scans do not hit
    # a live B2 bucket when the operator's shell has FOLEYFLOW_CACHE_BACKEND=b2
    # (that scan is multi-second and the old 4s poll window timed out).
    monkeypatch.setenv("FOLEYFLOW_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setattr(ledger_mod, "LEDGER_PATH", str(tmp_path / "ledger.jsonl"))

    # Generation stub whose output varies per call, so the regenerated take is
    # genuinely different audio and the two mixes differ byte-for-byte.
    calls = []

    def _generate(request, dest_path):
        calls.append(request)
        return _write_wav(dest_path, seconds=max(request.duration_s, 0.5),
                          amplitude=0.1 + 0.01 * len(calls))

    def _generate_many(requests, **kwargs):
        results = []
        for request in requests:
            calls.append(request)
            raw = str(tmp_path / f"raw{len(calls)}.wav")
            _write_wav(raw, seconds=max(request.duration_s, 0.5),
                       amplitude=0.1 + 0.01 * len(calls))
            results.append(stems.StemResult(
                request=request, served=request, local_path=raw,
                attempts=[stems.StemAttempt(
                    tier=request.tier, endpoint=request.endpoint,
                    status="succeeded", genblaze_run_id="gb-test-run")]))
        return results

    monkeypatch.setattr(stems, "generate_many", _generate_many)
    monkeypatch.setattr(sfx_cache, "generate_canonical_wav", _generate)

    video = str(tmp_path / "source.mp4")
    _placeholder_video(video)
    store.backend.put(project.input_key, video, content_type="video/mp4")

    # Baseline mix.
    first = render.render_mix(store, project.id, run=_run(store, project.id),
                              video_path=video)
    first_inventory = store.load_stem_inventory(project.id, first.run_id)
    first_keys = {s["b2_key"] for s in first_inventory}
    gunshot = next(s for s in first_inventory
                   if s["prompt"].startswith("gunshot"))
    baseline_calls = len(calls)

    runner = JobRunner(store, max_workers=1)
    app = create_app(store, runner)
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            f"{API_PREFIX}/projects/{project.id}/events/2/regenerate",
            json={"note": "thin take"})
        assert resp.status_code == 202, resp.text
        run_id = resp.json()["run_id"]
        run = None
        for _ in range(1000):
            run = client.get(
                f"{API_PREFIX}/projects/{project.id}/runs/{run_id}").json()
            if run["status"] != "running":
                break
            time.sleep(0.02)
        assert run["status"] == "completed", run
    runner.shutdown(wait=True)

    # Exactly one new generation: the target stem under its variant address.
    assert len(calls) == baseline_calls + 1

    repaired_inventory = store.load_stem_inventory(project.id, run_id)
    repaired_keys = {s["b2_key"] for s in repaired_inventory}
    repaired_gunshot = next(s for s in repaired_inventory
                            if s["prompt"].startswith("gunshot"))

    # The target stem moved to a new address (variant salt); the whole rest of
    # the inventory is byte-for-byte the same set of objects.
    assert repaired_gunshot["b2_key"] != gunshot["b2_key"]
    assert repaired_gunshot["variant"]
    assert repaired_keys - {repaired_gunshot["b2_key"]} == \
        first_keys - {gunshot["b2_key"]}

    # The new take is different audio: the two mixes differ.
    first_mix = store.backend.get_bytes(store.mix_key(project.id, first.run_id))
    repaired_mix = store.backend.get_bytes(store.mix_key(project.id, run_id))
    assert first_mix != repaired_mix

    # The lineage names the superseded take and the new version.
    lineage = [e for e in store.list_events(project.id)
               if e.type == EVENT_REPAIR_APPROVED]
    assert len(lineage) == 1
    payload = lineage[0].payload
    assert payload["superseded"] == {"cache_key": gunshot["cache_key"],
                                     "b2_key": gunshot["b2_key"],
                                     "variant": ""}
    assert payload["variant"] == repaired_gunshot["variant"]
    # v4, not v3: the baseline mix stamped its generated durations onto v2, so
    # the repair branches from there (v3 = the repair document). The repair's
    # mix then re-stamps the document — the repaired event's stem_key moves to
    # the new variant's address, so a new version records the truth rather
    # than letting v3 name an address that no longer exists.
    assert payload["timeline_version"] == 4
    assert payload["note"] == "thin take"

    # The new version is a real generation of the project, not an overwrite.
    assert store.list_timeline_versions(project.id) == [1, 2, 3, 4]

    # The API exposes the stamped address (the A.4-style passthrough that a
    # hand-enumerated response could silently drop): the repaired event's
    # stem_key in the GET response is the repaired stem's address.
    tl = client.get(f"{API_PREFIX}/projects/{project.id}/timeline").json()
    assert tl["version"] == 4
    repaired_event = next(e for e in tl["events"] if e["index"] == 2)
    assert repaired_event["stem_key"] == repaired_gunshot["b2_key"]

    # The stamped stem_key follows the address: the repaired event now names
    # the repaired stem, every other event keeps its key.
    repaired_timeline = store.load_timeline(project.id, 4)
    assert repaired_timeline.events[2].stem_key == repaired_gunshot["b2_key"]
    baseline_timeline = store.load_timeline(project.id, 2)
    for i in range(len(baseline_timeline.events)):
        if i != 2:
            assert (repaired_timeline.events[i].stem_key
                    == baseline_timeline.events[i].stem_key)


# -- the export gate ---------------------------------------------------------

def test_export_publishes_the_pointer_and_a_manifest(store, project,
                                                     fake_generation,
                                                     stub_ffmpeg, tmp_path):
    video = str(tmp_path / "source.mp4")
    _placeholder_video(video)
    mix = render.render_mix(store, project.id, run=_run(store, project.id),
                            video_path=video)

    export_run = _run(store, project.id, kind="export")
    pointer = render.publish_export(store, project.id, run=export_run)

    assert pointer["export_key"] == mix.video_key
    assert pointer["run_id"] == mix.run_id        # names the mix, not the export
    assert store.get_latest_export(project.id)["export_key"] == mix.video_key
    assert store.get_state(project.id).has_export is True

    manifest = store.load_manifest(project.id, export_run.id)
    assert manifest["manifest_version"] == render.MANIFEST_VERSION
    assert manifest["final_export_key"] == mix.video_key
    assert manifest["source"]["sha256"] == "a" * 64
    kinds = {a["type"] for a in manifest["artifacts"]}
    assert kinds == {"timeline", "audio_stem", "mix", "final_export"}


def test_export_is_refused_while_qa_blocks(store, project, fake_generation,
                                           stub_ffmpeg, tmp_path, monkeypatch):
    monkeypatch.setattr(qa, "measure_loudness", lambda path: qa.AudioMetrics(
        integrated_lufs=-9.0, true_peak_dbtp=2.5, loudness_range_lu=5.0))
    video = str(tmp_path / "source.mp4")
    _placeholder_video(video)
    render.render_mix(store, project.id, run=_run(store, project.id),
                      video_path=video)

    with pytest.raises(render.ExportBlocked) as caught:
        render.publish_export(store, project.id,
                              run=_run(store, project.id, kind="export"))

    assert "true_peak_exceeded" in caught.value.blockers
    assert store.get_latest_export(project.id) is None


def test_override_publishes_and_records_what_was_overridden(store, project,
                                                            fake_generation,
                                                            stub_ffmpeg,
                                                            tmp_path, monkeypatch):
    """§13 allows the human override; the manifest has to admit to it."""
    monkeypatch.setattr(qa, "measure_loudness", lambda path: qa.AudioMetrics(
        integrated_lufs=-9.0, true_peak_dbtp=2.5, loudness_range_lu=5.0))
    video = str(tmp_path / "source.mp4")
    _placeholder_video(video)
    render.render_mix(store, project.id, run=_run(store, project.id),
                      video_path=video)

    export_run = _run(store, project.id, kind="export")
    pointer = render.publish_export(store, project.id, run=export_run,
                                    override_blocking=True)

    assert "true_peak_exceeded" in pointer["overridden_blockers"]
    assert store.load_manifest(project.id, export_run.id)["qa_override"] is True


def test_export_without_a_mix_is_refused(store, project):
    from foleyflow.storage.base import ObjectNotFound

    with pytest.raises(ObjectNotFound):
        render.publish_export(store, project.id,
                              run=_run(store, project.id, kind="export"))


# -- the real thing ----------------------------------------------------------

@needs_ffmpeg
def test_silent_video_becomes_a_b2_backed_foley_export(store, project,
                                                       fake_generation, tmp_path):
    """Milestone 2's exit condition, with real ffmpeg and real mix math.

    Only provider generation is stubbed. The clip is muxed, uploaded, pulled
    back out of storage, and probed — so a mix that is silent, the wrong
    length, or not a playable file fails here rather than in front of a judge.
    """
    source = str(tmp_path / "clip.mp4")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=4",
         "-pix_fmt", "yuv420p", source],
        check=True)

    run = _run(store, project.id)
    result = render.render_mix(store, project.id, run=run, video_path=source)

    export_run = _run(store, project.id, kind="export")
    pointer = render.publish_export(store, project.id, run=export_run)

    # Pull the published bytes back out of storage — not the local scratch.
    fetched = str(tmp_path / "fetched.mp4")
    store.backend.get(pointer["export_key"], fetched)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,duration", "-of", "csv=p=0", fetched],
        capture_output=True, text=True, check=True).stdout
    assert "audio" in probe and "video" in probe

    mix, sr = sf.read(result.mix_path)
    assert sr == SR
    assert abs(len(mix) / sr - 4.0) < 0.05          # mixed to the probed length
    assert float(np.abs(mix).max()) > 0.0           # not a silent render
    assert result.qa["audio"]["measured"] is True


# -- the §8.2 job history ----------------------------------------------------

def test_a_render_records_every_stage_in_order(store, project, fake_generation,
                                               stub_ffmpeg, tmp_path):
    """SPEC §8.2: stage-level outcome, visible in job history."""
    video = str(tmp_path / "source.mp4")
    _placeholder_video(video)

    result = render.render_mix(store, project.id, run=_run(store, project.id),
                               video_path=video)

    assert [s["name"] for s in result.stages] == ["stems", "mix", "mux", "qa"]
    assert all(s["status"] == "succeeded" for s in result.stages)
    # Timing is Genblaze's, not a stopwatch we wrapped around it.
    assert all(s["elapsed_s"] >= 0 for s in result.stages)
    assert result.genblaze_run_id


def test_stage_records_reach_the_event_log_as_they_happen(store, project,
                                                          fake_generation,
                                                          stub_ffmpeg, tmp_path):
    """A run's own record is only written at the end; a client watching a
    render needs the log."""
    from foleyflow.projects import EVENT_STAGE_RECORDED

    video = str(tmp_path / "source.mp4")
    _placeholder_video(video)
    run = _run(store, project.id)
    render.render_mix(store, project.id, run=run, video_path=video)

    recorded = [e.payload["name"] for e in store.list_events(project.id)
                if e.type == EVENT_STAGE_RECORDED and e.run_id == run.id]
    assert recorded == ["stems", "mix", "mux", "qa"]


def test_the_stems_stage_counts_cache_hits_separately(store, project,
                                                      fake_generation,
                                                      stub_ffmpeg, tmp_path):
    """A second render generates nothing, and the history should say so."""
    video = str(tmp_path / "source.mp4")
    _placeholder_video(video)
    render.render_mix(store, project.id, run=_run(store, project.id),
                      video_path=video)

    second = render.render_mix(store, project.id, run=_run(store, project.id),
                               video_path=video)
    stems_stage = next(s for s in second.stages if s["name"] == "stems")
    assert stems_stage["status"] == "succeeded"
    assert second.generated_count == 0


def test_a_fallback_served_stem_is_attributed_to_who_actually_produced_it(
        store, project, stub_ffmpeg, tmp_path, monkeypatch):
    """A fallback must not be laundered into the requested tier's name.

    The cache address is the tier that was *asked for* — that is deliberate, so
    an outage does not re-hammer a dead endpoint. But `endpoint` on the stem
    inventory is that same address, and the §11.6 provenance manifest is built
    from the inventory. Without `served_endpoint`, an export would claim the
    paid tier generated audio that the fallback produced, which is precisely
    the claim the manifest exists to make honestly.
    """
    raw_dir = pathlib.Path(tempfile.mkdtemp(prefix="foleyflow-fallback-"))
    calls = []

    def _escalating_generate_many(requests, **kwargs):
        """Every request escalates: asked of `paid`, answered by `fallback`."""
        results = []
        for request in requests:
            calls.append(request)
            raw = str(raw_dir / f"{len(calls)}.wav")
            _write_wav(raw, seconds=max(request.duration_s, 0.5))
            served = dataclasses.replace(
                request, endpoint="fal-ai/elevenlabs/sound-effects/v2",
                tier="fallback")
            results.append(stems.StemResult(
                request=request, served=served, local_path=raw,
                attempts=[
                    stems.StemAttempt(tier=request.tier,
                                      endpoint=request.endpoint,
                                      status="failed", error="503"),
                    stems.StemAttempt(tier="fallback",
                                      endpoint=served.endpoint,
                                      status="succeeded",
                                      genblaze_run_id="gb-test-run"),
                ]))
        return results

    monkeypatch.setattr(stems, "generate_many", _escalating_generate_many)
    monkeypatch.setattr(sfx_cache, "generate_canonical_wav",
                        lambda request, dest: _write_wav(dest, seconds=0.5))

    video = str(tmp_path / "source.mp4")
    _placeholder_video(video)
    run = _run(store, project.id)
    render.render_mix(store, project.id, run=run, video_path=video)

    inventory = store.load_stem_inventory(project.id, run.id)
    assert inventory, "expected stems"
    for stem in inventory:
        # Addressed under what was asked for ...
        assert stem["endpoint"] != "fal-ai/elevenlabs/sound-effects/v2"
        # ... and attributed to what answered.
        assert stem["served_endpoint"] == "fal-ai/elevenlabs/sound-effects/v2"
        assert stem["served_by"] == "fallback"

    # The spend log names the endpoint that actually charged.
    generated = [e for e in store.list_events(project.id)
                 if e.type == EVENT_STEM_GENERATED]
    assert generated
    assert all(e.payload["served_endpoint"] ==
               "fal-ai/elevenlabs/sound-effects/v2" for e in generated)


def test_a_stem_served_by_its_own_tier_records_no_fallback(
        store, project, fake_generation, stub_ffmpeg, tmp_path):
    """The common case stays quiet — no misleading `served_endpoint` key."""
    video = str(tmp_path / "source.mp4")
    _placeholder_video(video)
    run = _run(store, project.id)
    render.render_mix(store, project.id, run=run, video_path=video)

    for stem in store.load_stem_inventory(project.id, run.id):
        assert "served_endpoint" not in stem
    for event in store.list_events(project.id):
        if event.type == EVENT_STEM_GENERATED:
            assert "served_endpoint" not in event.payload


# -- generated-duration freezes (unit 7.2's mechanism, made reachable) --------

def test_mix_stamps_generated_durations_and_renders_identically(
        store, project, fake_generation, stub_ffmpeg, tmp_path):
    """A completed mix writes down the durations it actually generated.

    This is the only honest source for `source_duration_s`: analysis is told
    not to invent one, and the commit endpoint may only record an address that
    already exists. Without it the resize-is-free mechanism is unreachable in
    production.

    The stamp must be free of consequences: the stamped version has to route to
    exactly the same SfxRequest and render to exactly the same bytes, or the
    bookkeeping would have changed the film.
    """
    from foleyflow.routing import route_event

    video = str(tmp_path / "source.mp4")
    _placeholder_video(video)

    first = render.render_mix(store, project.id, run=_run(store, project.id),
                              video_path=video)
    assert first.timeline_version == 2

    v1 = store.load_timeline(project.id, 1)
    v2 = store.load_timeline(project.id, 2)
    assert all(e.source_duration_s is None for e in v1.events)
    assert all(e.source_duration_s is not None for e in v2.events)

    # Every frozen value is an address this run really recorded, and routing
    # the stamped event reproduces the original request field for field —
    # clamping is idempotent, so the post-clamp value the inventory stores
    # round-trips through `source_duration_s` unchanged.
    inventory = store.load_stem_inventory(project.id, first.run_id)
    frozen_durations = {round(s["duration_s"], 6) for s in inventory}
    inventory_keys = {s["b2_key"] for s in inventory}
    for before, after in zip(v1.events, v2.events):
        assert route_event(after) == route_event(before)
        assert round(after.source_duration_s, 6) in frozen_durations
        # Every stamped stem_key is an address this run really recorded — the
        # honest event→audio link the preview trusts.
        assert after.stem_key in inventory_keys
        # Discrete one-shots also get their placement span widened to the
        # source length: with a freeze present the renderer windows them to
        # `duration`, so leaving the old span would silently cut the headroom.
        assert after.duration == pytest.approx(after.source_duration_s)

    # Same document, same audio — byte for byte.
    second = render.render_mix(store, project.id, run=_run(store, project.id),
                               video_path=video)
    assert second.timeline_version == 2      # nothing left to stamp
    assert store.list_timeline_versions(project.id) == [1, 2]
    assert (store.backend.get_bytes(store.mix_key(project.id, first.run_id))
            == store.backend.get_bytes(store.mix_key(project.id, second.run_id)))


def test_mix_freeze_is_recorded_as_a_system_write(store, project,
                                                  fake_generation, stub_ffmpeg,
                                                  tmp_path):
    """History stays real (decision A.2.1): the stamp is not a director's edit."""
    from foleyflow.projects import EVENT_PLAN_PROPOSED

    video = str(tmp_path / "source.mp4")
    _placeholder_video(video)
    render.render_mix(store, project.id, run=_run(store, project.id),
                      video_path=video)

    proposals = [e for e in store.list_events(project.id)
                 if e.type == EVENT_PLAN_PROPOSED]
    stamp = proposals[-1]
    assert stamp.payload["timeline_version"] == 2
    assert stamp.payload["source"] == "mix_freeze"
    assert stamp.actor == "system"


def test_stamp_leaves_events_without_a_recorded_stem_unfrozen():
    """A freeze with no provenance would be a fabricated number (§A.3)."""
    timeline = Timeline(duration=4.0, ambience="wind", events=[
        Event(name="gunshot", start_time=1.0, duration=0.8,
              intensity="high", kind="discrete"),
        Event(name="boot scuff", start_time=2.0, duration=0.3,
              intensity="med", kind="discrete"),
    ])
    from foleyflow.routing import route_event
    from foleyflow.sfx_cache import cache_object_key

    request = route_event(timeline.events[0])
    inventory = [{"b2_key": cache_object_key(request.endpoint, request.prompt,
                                             request.duration_s, ""),
                  "duration_s": request.duration_s}]

    stamped, changed = render.stamp_source_durations(timeline, inventory)
    assert changed is True
    assert stamped.events[0].source_duration_s == pytest.approx(request.duration_s)
    assert stamped.events[1].source_duration_s is None   # no stem, no freeze

    # Nothing to stamp → no version churn.
    assert render.stamp_source_durations(timeline, []) == (timeline, False)


def test_stamped_timeline_survives_the_round_trip(store, project,
                                                  fake_generation, stub_ffmpeg,
                                                  tmp_path):
    """§A.4: an Event field that is not enumerated by hand vanishes on save."""
    from foleyflow.timeline_io import dict_to_timeline, timeline_to_dict

    video = str(tmp_path / "source.mp4")
    _placeholder_video(video)
    render.render_mix(store, project.id, run=_run(store, project.id),
                      video_path=video)

    stamped = store.load_timeline(project.id, 2)     # already one round trip
    again = dict_to_timeline(timeline_to_dict(stamped))
    assert [e.source_duration_s for e in again.events] == \
        [e.source_duration_s for e in stamped.events]
    assert all(e.source_duration_s is not None for e in again.events)
