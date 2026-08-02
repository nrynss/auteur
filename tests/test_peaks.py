"""Unit tests for waveform peak envelopes (unit 7.5).

Offline: synthetic WAVs + LocalBackend. No network, no providers.
"""

import json

import numpy as np
import pytest
import soundfile as sf

from foleyflow.peaks import (
    PEAKS_PER_SECOND,
    InvalidAudioError,
    PeaksResult,
    compute_peaks,
    get_or_compute_peaks,
    is_project_stem_key,
    peaks_cache_key,
)
from foleyflow.storage import LocalBackend, ObjectNotFound


SR = 48000


def _write_wav(path, data, sr=SR):
    """Write a float stereo (or mono) array as canonical WAV."""
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim == 1:
        arr = np.column_stack([arr, arr])
    sf.write(path, arr, sr, format="WAV", subtype="FLOAT")
    return path


def _sine(duration_s, freq=440.0, amp=0.5, sr=SR):
    n = int(round(duration_s * sr))
    t = np.arange(n, dtype=np.float64) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _dc(duration_s, value=0.25, sr=SR):
    n = int(round(duration_s * sr))
    return np.full(n, value, dtype=np.float32)


# -- compute_peaks ----------------------------------------------------------

def test_peaks_per_second_default_is_100():
    assert PEAKS_PER_SECOND == 100


def test_dc_clip_envelope_matches_amplitude(tmp_path):
    """Constant signal → every bin's min and max equal the DC level."""
    path = _write_wav(tmp_path / "dc.wav", _dc(0.05, value=0.3))
    result = compute_peaks(path)
    assert result.peaks_per_second == 100
    assert result.sample_rate == SR
    assert result.channels == 2
    assert abs(result.duration_s - 0.05) < 1e-6
    # 0.05 s * 100 pps = 5 bins
    assert len(result.min) == 5
    assert len(result.max) == 5
    for lo, hi in zip(result.min, result.max):
        assert lo == pytest.approx(0.3, abs=1e-5)
        assert hi == pytest.approx(0.3, abs=1e-5)


def test_sine_envelope_tracks_peak_amplitude(tmp_path):
    """A full-scale sine should hit ~±amp in bins that contain peaks."""
    amp = 0.7
    # 0.1 s at 100 Hz → exactly 10 cycles; peaks every 5 ms, bins 10 ms.
    path = _write_wav(tmp_path / "sine.wav", _sine(0.1, freq=100.0, amp=amp))
    result = compute_peaks(path)
    assert len(result.max) == 10  # 0.1 * 100
    # Every 10 ms bin of a 100 Hz sine contains a crest and a trough.
    assert max(result.max) == pytest.approx(amp, abs=0.02)
    assert min(result.min) == pytest.approx(-amp, abs=0.02)
    for lo, hi in zip(result.min, result.max):
        assert hi >= lo
        assert hi == pytest.approx(amp, abs=0.05)
        assert lo == pytest.approx(-amp, abs=0.05)


def test_array_input_with_sample_rate():
    data = _dc(0.02, value=-0.4)
    result = compute_peaks(data, sample_rate=SR)
    assert result.channels == 1
    assert all(v == pytest.approx(-0.4, abs=1e-5) for v in result.min)
    assert all(v == pytest.approx(-0.4, abs=1e-5) for v in result.max)


def test_stereo_min_max_across_channels():
    """Min/max span both channels, not just channel 0."""
    n = SR // 50  # 20 ms → 2 bins at 100 pps
    left = np.full(n, 0.1, dtype=np.float32)
    right = np.full(n, -0.5, dtype=np.float32)
    stereo = np.column_stack([left, right])
    result = compute_peaks(stereo, sample_rate=SR)
    assert result.channels == 2
    assert all(v == pytest.approx(-0.5, abs=1e-5) for v in result.min)
    assert all(v == pytest.approx(0.1, abs=1e-5) for v in result.max)


def test_empty_audio_yields_empty_arrays():
    result = compute_peaks(np.zeros((0, 2), dtype=np.float32), sample_rate=SR)
    assert result.min == []
    assert result.max == []
    assert result.duration_s == 0.0


def test_peaks_cache_key_is_sibling_suffix():
    assert peaks_cache_key("cache/abc.wav") == "cache/abc.wav.peaks.json"
    assert peaks_cache_key("projects/p/runs/r/mix.wav") == (
        "projects/p/runs/r/mix.wav.peaks.json"
    )


# -- get_or_compute_peaks + cache -------------------------------------------

def test_get_or_compute_writes_peaks_beside_audio(tmp_path):
    backend = LocalBackend(str(tmp_path / "store"))
    work = str(tmp_path / "work")
    audio_key = "cache/stem01.wav"
    local = tmp_path / "stem.wav"
    _write_wav(local, _sine(0.05, amp=0.4))
    backend.put(audio_key, str(local), content_type="audio/wav")

    result = get_or_compute_peaks(backend, audio_key, work_dir=work)
    assert result.peaks_per_second == 100
    assert len(result.max) == 5
    peaks_key = peaks_cache_key(audio_key)
    assert backend.exists(peaks_key)
    stored = PeaksResult.from_dict(
        json.loads(backend.get_bytes(peaks_key).decode("utf-8")))
    assert stored.min == result.min
    assert stored.max == result.max


def test_cache_hit_does_not_re_decode_audio(tmp_path, monkeypatch):
    """Second call must not download or re-decode the WAV."""
    backend = LocalBackend(str(tmp_path / "store"))
    work = str(tmp_path / "work")
    audio_key = "cache/hit.wav"
    local = tmp_path / "hit.wav"
    _write_wav(local, _dc(0.03, value=0.2))
    backend.put(audio_key, str(local), content_type="audio/wav")

    first = get_or_compute_peaks(backend, audio_key, work_dir=work)
    assert backend.exists(peaks_cache_key(audio_key))

    get_calls = []
    compute_calls = []
    real_get = backend.get

    def spy_get(key, dest):
        get_calls.append(key)
        return real_get(key, dest)

    def spy_compute(*args, **kwargs):
        compute_calls.append(1)
        raise AssertionError("compute_peaks must not run on cache hit")

    monkeypatch.setattr(backend, "get", spy_get)
    monkeypatch.setattr("foleyflow.peaks.compute_peaks", spy_compute)

    second = get_or_compute_peaks(backend, audio_key, work_dir=work)
    assert second.min == first.min
    assert second.max == first.max
    assert get_calls == []
    assert compute_calls == []


def test_missing_audio_raises_object_not_found(tmp_path):
    backend = LocalBackend(str(tmp_path / "store"))
    with pytest.raises(ObjectNotFound):
        get_or_compute_peaks(backend, "cache/missing.wav",
                             work_dir=str(tmp_path / "work"))


def test_put_if_absent_race_returns_winner_peaks(tmp_path, monkeypatch):
    """When another writer stores peaks first, we re-read their object."""
    backend = LocalBackend(str(tmp_path / "store"))
    work = str(tmp_path / "work")
    audio_key = "cache/race.wav"
    local = tmp_path / "race.wav"
    _write_wav(local, _dc(0.02, value=0.5))
    backend.put(audio_key, str(local), content_type="audio/wav")

    # Pre-seed peaks as if a concurrent writer finished first, but only
    # after we miss the initial read — simulate via put_if_absent always
    # failing and a second get_bytes succeeding.
    from foleyflow.storage.base import PreconditionFailed

    winner = PeaksResult(
        peaks_per_second=100, channels=2, sample_rate=SR, duration_s=0.02,
        min=[-1.0], max=[1.0],
    )
    peaks_key = peaks_cache_key(audio_key)
    backend.put_if_absent(
        peaks_key,
        json.dumps(winner.to_dict()).encode("utf-8"),
        content_type="application/json",
    )

    # Delete peaks after first load attempt by wrapping get_bytes to miss
    # once then hit — simpler: just call get_or_compute when peaks already
    # exist (the happy cache path). Race path is when peaks appear between
    # miss and put_if_absent.
    real_get_bytes = backend.get_bytes
    calls = {"n": 0}

    def miss_then_hit(key):
        calls["n"] += 1
        if key == peaks_key and calls["n"] == 1:
            raise ObjectNotFound(f"no object at {key!r}")
        return real_get_bytes(key)

    def always_race(key, data, content_type=None):
        raise PreconditionFailed(f"object already exists at {key!r}")

    monkeypatch.setattr(backend, "get_bytes", miss_then_hit)
    monkeypatch.setattr(backend, "put_if_absent", always_race)

    result = get_or_compute_peaks(backend, audio_key, work_dir=work)
    assert result.min == winner.min
    assert result.max == winner.max


def test_corrupt_peaks_json_is_recomputed(tmp_path):
    """Garbage peaks cache is a miss — recompute and overwrite."""
    backend = LocalBackend(str(tmp_path / "store"))
    work = str(tmp_path / "work")
    audio_key = "cache/corrupt.wav"
    local = tmp_path / "corrupt.wav"
    _write_wav(local, _dc(0.04, value=0.35))
    backend.put(audio_key, str(local), content_type="audio/wav")

    peaks_key = peaks_cache_key(audio_key)
    backend.put_bytes(peaks_key, b"not-json{{{", content_type="application/json")

    result = get_or_compute_peaks(backend, audio_key, work_dir=work)
    assert result.peaks_per_second == 100
    assert len(result.max) == 4
    assert all(v == pytest.approx(0.35, abs=1e-4) for v in result.max)

    stored = PeaksResult.from_dict(
        json.loads(backend.get_bytes(peaks_key).decode("utf-8")))
    assert stored.min == result.min
    assert stored.max == result.max


def test_schema_invalid_peaks_json_is_recomputed(tmp_path):
    """Valid JSON missing required fields / mismatched min-max is a miss."""
    backend = LocalBackend(str(tmp_path / "store"))
    work = str(tmp_path / "work")
    audio_key = "cache/badschema.wav"
    local = tmp_path / "badschema.wav"
    _write_wav(local, _dc(0.02, value=0.1))
    backend.put(audio_key, str(local), content_type="audio/wav")

    peaks_key = peaks_cache_key(audio_key)
    # min/max length mismatch + missing duration_s
    bad = json.dumps({
        "peaks_per_second": 100,
        "channels": 2,
        "sample_rate": SR,
        "min": [0.0, 0.1],
        "max": [0.0],
    }).encode("utf-8")
    backend.put_bytes(peaks_key, bad, content_type="application/json")

    result = get_or_compute_peaks(backend, audio_key, work_dir=work)
    assert len(result.min) == len(result.max)
    stored = json.loads(backend.get_bytes(peaks_key).decode("utf-8"))
    assert "duration_s" in stored
    assert len(stored["min"]) == len(stored["max"])


def test_wrong_peaks_per_second_is_recomputed(tmp_path):
    """Cached envelope at a different resolution is treated as a miss."""
    backend = LocalBackend(str(tmp_path / "store"))
    work = str(tmp_path / "work")
    audio_key = "cache/pps.wav"
    local = tmp_path / "pps.wav"
    _write_wav(local, _dc(0.05, value=0.2))
    backend.put(audio_key, str(local), content_type="audio/wav")

    peaks_key = peaks_cache_key(audio_key)
    wrong = PeaksResult(
        peaks_per_second=10, channels=2, sample_rate=SR, duration_s=0.05,
        min=[0.0] * 1, max=[0.0] * 1,
    )
    backend.put_bytes(
        peaks_key,
        json.dumps(wrong.to_dict()).encode("utf-8"),
        content_type="application/json",
    )

    result = get_or_compute_peaks(
        backend, audio_key, work_dir=work, peaks_per_second=100)
    assert result.peaks_per_second == 100
    assert len(result.max) == 5
    stored = PeaksResult.from_dict(
        json.loads(backend.get_bytes(peaks_key).decode("utf-8")))
    assert stored.peaks_per_second == 100


def test_invalid_wav_raises_invalid_audio_error(tmp_path):
    backend = LocalBackend(str(tmp_path / "store"))
    work = str(tmp_path / "work")
    audio_key = "cache/notwav.wav"
    backend.put_bytes(audio_key, b"this is not a wav", content_type="audio/wav")

    with pytest.raises(InvalidAudioError) as ei:
        get_or_compute_peaks(backend, audio_key, work_dir=work)
    assert ei.value.key == audio_key


def test_is_project_stem_key_prefers_latest_mix_and_short_circuits():
    """Latest-mix inventory is checked first; hit skips scanning other runs."""
    class FakeStore:
        def __init__(self):
            self.list_runs_calls = 0
            self.load_calls = []

        def get_latest_mix(self, project_id):
            return {"run_id": "run_latest"}

        def list_runs(self, project_id):
            self.list_runs_calls += 1
            # Would be expensive if always scanned — must not be called on hit.
            return [
                type("R", (), {"id": "run_old"})(),
                type("R", (), {"id": "run_latest"})(),
            ]

        def load_stem_inventory(self, project_id, run_id):
            self.load_calls.append(run_id)
            if run_id == "run_latest":
                return [{"b2_key": "cache/hot.wav"}]
            return [{"b2_key": "cache/old.wav"}]

    store = FakeStore()
    assert is_project_stem_key(store, "proj", "cache/hot.wav") is True
    assert store.load_calls == ["run_latest"]
    assert store.list_runs_calls == 0

    store2 = FakeStore()
    assert is_project_stem_key(store2, "proj", "cache/old.wav") is True
    # Latest miss, then newest-first scan finds old run.
    assert store2.load_calls[0] == "run_latest"
    assert "run_old" in store2.load_calls
    assert store2.list_runs_calls == 1

    store3 = FakeStore()
    assert is_project_stem_key(store3, "proj", "cache/missing.wav") is False
