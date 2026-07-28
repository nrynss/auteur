"""Offline tests for the analysis cache.

`analyze_fn` is a counting fake throughout — a real call would spend one of
20 daily requests, which is exactly what this cache exists to protect.
"""

import json
import os

import pytest

from foleyflow.models import Event, Timeline
from foleyflow import timeline_cache as tc
from foleyflow.storage import LocalBackend

PROMPT = "You are a professional Foley artist analyzing a silent video."
MODEL = "gemini-2.5-flash"


def _timeline(name="footsteps on gravel"):
    return Timeline(
        duration=15.0,
        ambience="rain on stone",
        events=[Event(name=name, start_time=3.2, duration=2.5,
                      intensity="med", kind="rhythmic")],
    )


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"fake-video-bytes")
    return str(p)


@pytest.fixture
def backend(tmp_path):
    return LocalBackend(str(tmp_path / "store"))


def _counting(timeline=None):
    calls = []

    def fn():
        calls.append(1)
        return timeline or _timeline()

    return fn, calls


def test_miss_analyzes_then_hit_does_not(video, backend, tmp_path):
    fn, calls = _counting()
    t1, cached1 = tc.get_or_analyze(video, fn, backend=backend, model_id=MODEL,
                                    prompt=PROMPT, work_dir=str(tmp_path / "w1"))
    assert cached1 is False and len(calls) == 1

    t2, cached2 = tc.get_or_analyze(video, fn, backend=backend, model_id=MODEL,
                                    prompt=PROMPT, work_dir=str(tmp_path / "w2"))
    assert cached2 is True, "second call must not spend a request"
    assert len(calls) == 1
    assert t2.ambience == t1.ambience
    assert [e.name for e in t2.events] == [e.name for e in t1.events]
    assert t2.events[0].start_time == t1.events[0].start_time


def test_identical_content_under_a_different_name_hits(video, backend, tmp_path):
    """The key is the video's bytes, so re-uploading the same footage into a
    new project reuses the analysis."""
    fn, calls = _counting()
    tc.get_or_analyze(video, fn, backend=backend, model_id=MODEL, prompt=PROMPT)

    copy = tmp_path / "renamed.mp4"
    copy.write_bytes(open(video, "rb").read())
    _, cached = tc.get_or_analyze(str(copy), fn, backend=backend,
                                  model_id=MODEL, prompt=PROMPT)
    assert cached is True
    assert len(calls) == 1


def test_different_video_misses(video, backend, tmp_path):
    fn, calls = _counting()
    tc.get_or_analyze(video, fn, backend=backend, model_id=MODEL, prompt=PROMPT)

    other = tmp_path / "other.mp4"
    other.write_bytes(b"completely-different")
    _, cached = tc.get_or_analyze(str(other), fn, backend=backend,
                                  model_id=MODEL, prompt=PROMPT)
    assert cached is False and len(calls) == 2


def test_changed_prompt_invalidates(video, backend):
    """Otherwise prompt tuning would look like a no-op: you would edit the
    prompt, re-run, and get the old timeline back."""
    fn, calls = _counting()
    tc.get_or_analyze(video, fn, backend=backend, model_id=MODEL, prompt=PROMPT)
    _, cached = tc.get_or_analyze(video, fn, backend=backend, model_id=MODEL,
                                  prompt=PROMPT + " Be precise about timing.")
    assert cached is False and len(calls) == 2


def test_reflowed_prompt_whitespace_does_not_invalidate(video, backend):
    fn, calls = _counting()
    tc.get_or_analyze(video, fn, backend=backend, model_id=MODEL, prompt=PROMPT)
    reflowed = PROMPT.replace(" ", "\n   ", 1) + "  "
    _, cached = tc.get_or_analyze(video, fn, backend=backend, model_id=MODEL,
                                  prompt=reflowed)
    assert cached is True, "reformatting a docstring must not cost 20 requests"
    assert len(calls) == 1


def test_models_do_not_overwrite_each_other(video, backend):
    """Flash and Flash Lite timelines coexist — required for benchmarking one
    against the other on timing precision."""
    fn_a, calls_a = _counting(_timeline("flash result"))
    fn_b, calls_b = _counting(_timeline("lite result"))
    tc.get_or_analyze(video, fn_a, backend=backend, model_id=MODEL, prompt=PROMPT)
    t_lite, cached = tc.get_or_analyze(video, fn_b, backend=backend,
                                       model_id="gemini-3.5-flash-lite", prompt=PROMPT)
    assert cached is False
    assert t_lite.events[0].name == "lite result"

    t_flash, cached_flash = tc.get_or_analyze(video, fn_a, backend=backend,
                                              model_id=MODEL, prompt=PROMPT)
    assert cached_flash is True
    assert t_flash.events[0].name == "flash result", "Lite must not clobber Flash"


def test_force_reanalyzes_and_refreshes_the_entry(video, backend):
    fn_a, _ = _counting(_timeline("first"))
    fn_b, _ = _counting(_timeline("second"))
    tc.get_or_analyze(video, fn_a, backend=backend, model_id=MODEL, prompt=PROMPT)

    t, cached = tc.get_or_analyze(video, fn_b, backend=backend, model_id=MODEL,
                                  prompt=PROMPT, force=True)
    assert cached is False and t.events[0].name == "second"

    t2, cached2 = tc.get_or_analyze(video, fn_a, backend=backend,
                                    model_id=MODEL, prompt=PROMPT)
    assert cached2 is True
    assert t2.events[0].name == "second", "force must write, not just bypass"


def test_stored_payload_records_what_produced_it(video, backend, tmp_path):
    fn, _ = _counting()
    tc.get_or_analyze(video, fn, backend=backend, model_id=MODEL, prompt=PROMPT,
                      work_dir=str(tmp_path / "w"))
    key = tc.cache_object_key(tc.file_sha256(video), MODEL, tc.prompt_fingerprint(PROMPT))
    dest = str(tmp_path / "pulled.json")
    backend.get(key, dest)
    payload = json.load(open(dest, encoding="utf-8"))
    assert payload["model_id"] == MODEL
    assert payload["video_sha256"] == tc.file_sha256(video)
    assert payload["cache_version"] == 1
