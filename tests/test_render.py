"""Project-scoped rendering: mix, QA, manifest, export.

Two layers, deliberately:

* Offline tests stub ffmpeg and provider generation and assert the *wiring* —
  which keys artifacts land under, what the event log says, what the manifest
  claims, and what the export gate refuses.
* One `needs_ffmpeg` test runs the real thing end to end on a generated clip:
  video in, muxed Foley video out of B2. It is slower and it is the only test
  that proves the milestone's exit condition, so it earns its seconds.

Nothing here touches the network. SFX generation is replaced by a synthesized
WAV at `sfx_cache.generate_canonical_wav`, which is the single billed call in
the whole path — everything above it, including the shared cache, runs for real.
"""

import os
import shutil
import subprocess

import numpy as np
import pytest
import soundfile as sf

from foleyflow import qa, render, sfx_cache
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
    """Replace the one billed call with a synthesized clip.

    Patched at `sfx_cache`, not at the sfx_fn: everything between — the shared
    content-addressed cache, the local scratch reuse, the observer that reports
    hits — is code this module is here to exercise.
    """
    calls = []

    def _generate(request, dest_path):
        calls.append(request)
        return _write_wav(dest_path, seconds=max(request.duration_s, 0.5))

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
    assert result.timeline_version == 1


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
