"""Offline unit tests for src/foleyflow/sfx.py. NO network / fal API calls here —
ledger.execute_with_ledger and net.download are monkeypatched wherever
exercised — those are the two seams the Genblaze provider reaches the wire
through, so stubbing them leaves the orchestration itself running for real.
ffmpeg/ffprobe are local binaries (no network) and are used directly, same
as tests/test_analyze.py's use of ffprobe via get_video_duration.
"""

import shutil

import numpy as np
import pytest
import soundfile as sf

from foleyflow import ledger, net, sfx
from foleyflow.routing import SfxRequest

CASSETTEAI = "cassetteai/sound-effects-generator"
ELEVENLABS = "fal-ai/elevenlabs/sound-effects/v2"
MMAUDIO = "fal-ai/mmaudio-v2/text-to-audio"


def _req(endpoint=CASSETTEAI, prompt="footsteps on gravel", duration_s=2.0):
    return SfxRequest(endpoint=endpoint, prompt=prompt, duration_s=duration_s, category="test")


# -- Cache key stability --

def test_cache_key_stable_for_identical_request():
    k1 = sfx.cache_key(CASSETTEAI, "footsteps on gravel", 2.0)
    k2 = sfx.cache_key(CASSETTEAI, "footsteps on gravel", 2.0)
    assert k1 == k2
    assert len(k1) == 16


def test_cache_key_changes_with_prompt():
    k1 = sfx.cache_key(CASSETTEAI, "footsteps on gravel", 2.0)
    k2 = sfx.cache_key(CASSETTEAI, "footsteps on snow", 2.0)
    assert k1 != k2


def test_cache_key_changes_with_duration():
    k1 = sfx.cache_key(CASSETTEAI, "footsteps on gravel", 2.0)
    k2 = sfx.cache_key(CASSETTEAI, "footsteps on gravel", 3.0)
    assert k1 != k2


def test_cache_key_changes_with_endpoint():
    k1 = sfx.cache_key(CASSETTEAI, "wooden door creaking", 2.0)
    k2 = sfx.cache_key(ELEVENLABS, "wooden door creaking", 2.0)
    assert k1 != k2


def test_cache_key_rounds_duration_to_two_decimals():
    # 2.001 and 2.004 both format to "2.00" -> same key (documented rounding).
    k1 = sfx.cache_key(CASSETTEAI, "wind", 2.001)
    k2 = sfx.cache_key(CASSETTEAI, "wind", 2.004)
    assert k1 == k2
    # 2.006 rounds to "2.01" -> different key.
    k3 = sfx.cache_key(CASSETTEAI, "wind", 2.006)
    assert k1 != k3


# -- Cache key and extra_arguments --

MOSS = "openmoss/moss-soundeffect-v2"


def test_model_without_extras_keeps_the_original_key():
    """The fal models declare no extra_arguments, so their keys must stay
    byte-identical to the pre-extras rule — otherwise every cached clip is
    orphaned."""
    import hashlib
    legacy = hashlib.sha256(
        f"{CASSETTEAI}|footsteps on gravel|2.00".encode("utf-8")
    ).hexdigest()[:16]
    assert sfx.cache_key(CASSETTEAI, "footsteps on gravel", 2.0) == legacy


def test_declared_extras_join_the_key():
    """MOSS declares num_inference_steps/cfg_scale, which change the audio."""
    import hashlib
    without = hashlib.sha256(
        f"{MOSS}|door creak|2.50".encode("utf-8")
    ).hexdigest()[:16]
    assert sfx.cache_key(MOSS, "door creak", 2.5) != without


def test_changing_an_extra_changes_the_key(monkeypatch):
    """Regenerating at a different step count must not resolve to the old clip."""
    before = sfx.cache_key(MOSS, "door creak", 2.5)

    from foleyflow.providers import config as provider_config
    real = provider_config.get_model_spec

    def patched(model_id, **kwargs):
        spec = real(model_id, **kwargs)
        if model_id == MOSS:
            spec.extra_arguments = dict(spec.extra_arguments,
                                        num_inference_steps=100)
        return spec

    monkeypatch.setattr(provider_config, "get_model_spec", patched)
    assert sfx.cache_key(MOSS, "door creak", 2.5) != before


def test_extras_segment_is_order_independent(monkeypatch):
    """TOML key order must not change the key — the segment sorts its keys."""
    from foleyflow.providers import config as provider_config
    real = provider_config.get_model_spec

    def reordered(model_id, **kwargs):
        spec = real(model_id, **kwargs)
        if model_id == MOSS:
            spec.extra_arguments = {"cfg_scale": 4.0, "num_inference_steps": 50}
        return spec

    expected = sfx.cache_key(MOSS, "door creak", 2.5)
    monkeypatch.setattr(provider_config, "get_model_spec", reordered)
    assert sfx.cache_key(MOSS, "door creak", 2.5) == expected


def test_unknown_endpoint_still_produces_a_key():
    """Endpoints absent from providers.toml must not raise — build_arguments
    falls back for them, and so must the cache."""
    assert len(sfx.cache_key("not/declared", "hiss", 1.0)) == 16


# -- Argument-builder shapes --

def test_elevenlabs_argument_shape():
    args = sfx.build_arguments(_req(endpoint=ELEVENLABS, prompt="door creak", duration_s=3.5))
    assert args == {"text": "door creak", "duration_seconds": 3.5}


def test_cassetteai_argument_shape():
    args = sfx.build_arguments(_req(endpoint=CASSETTEAI, prompt="wind rustling", duration_s=6.62))
    assert args == {"prompt": "wind rustling", "duration": 7}  # int(round(6.62))


def test_mmaudio_argument_shape():
    args = sfx.build_arguments(_req(endpoint=MMAUDIO, prompt="match strike", duration_s=0.7))
    assert args == {"prompt": "match strike", "duration": 0.7}


def test_unknown_endpoint_falls_back_to_prompt_duration_shape():
    args = sfx.build_arguments(_req(endpoint="fal-ai/some-new-sfx-model", prompt="clink", duration_s=1.5))
    assert args == {"prompt": "clink", "duration": 1.5}


# -- Response URL extraction --

def test_extract_audio_url_from_audio_key():
    assert sfx.extract_audio_url({"audio": {"url": "http://x/a.wav"}}) == "http://x/a.wav"


def test_extract_audio_url_from_audio_file_key():
    assert sfx.extract_audio_url({"audio_file": {"url": "http://x/b.wav"}}) == "http://x/b.wav"


def test_extract_audio_url_from_bare_url_key():
    assert sfx.extract_audio_url({"url": "http://x/c.wav"}) == "http://x/c.wav"


def test_extract_audio_url_raises_with_raw_result_on_miss():
    with pytest.raises(ValueError, match="no audio URL"):
        sfx.extract_audio_url({"unexpected": "shape"})


# -- RMS normalization math --

def _write_sine(path, seconds=1.0, sr=44100, freq=440.0, amplitude=0.2, channels=1):
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    tone = amplitude * np.sin(2 * np.pi * freq * t).astype(np.float32)
    if channels == 2:
        data = np.stack([tone, tone], axis=1)
    else:
        data = tone
    sf.write(path, data, sr, subtype="FLOAT")


def _measured_rms_dbfs(path):
    data, _ = sf.read(path, dtype="float32", always_2d=True)
    rms = float(np.sqrt(np.mean(np.square(data))))
    return 20.0 * np.log10(rms) if rms > 0 else float("-inf")


def test_rms_normalize_hits_target_within_half_db(tmp_path):
    wav_path = str(tmp_path / "sine.wav")
    _write_sine(wav_path, amplitude=0.2, channels=2)
    sfx.rms_normalize(wav_path)
    measured = _measured_rms_dbfs(wav_path)
    assert abs(measured - sfx.RMS_TARGET_DBFS) < 0.5


def test_rms_normalize_on_quiet_mono_source_within_half_db(tmp_path):
    wav_path = str(tmp_path / "quiet_sine.wav")
    _write_sine(wav_path, amplitude=0.01, channels=1)
    sfx.rms_normalize(wav_path)
    measured = _measured_rms_dbfs(wav_path)
    assert abs(measured - sfx.RMS_TARGET_DBFS) < 0.5


def test_rms_normalize_peak_guard_kicks_in_for_near_full_scale_pulses(tmp_path, capsys):
    # Sparse near-full-scale square pulses: low overall RMS (mostly silence)
    # but a peak close to full scale. Normalizing RMS up to -18 dBFS would
    # need to push that near-full-scale peak past the -1 dBFS guard, so the
    # peak-guard path must clamp instead of honoring the RMS target exactly.
    sr = 44100
    n = sr  # 1 second
    data = np.zeros(n, dtype=np.float32)
    pulse_width = int(0.01 * n)  # 1% duty cycle
    data[:pulse_width] = 0.95
    wav_path = str(tmp_path / "pulses.wav")
    sf.write(wav_path, data, sr, subtype="FLOAT")

    sfx.rms_normalize(wav_path)

    out, _ = sf.read(wav_path, dtype="float32", always_2d=True)
    peak = float(np.max(np.abs(out)))
    peak_guard_linear = 10.0 ** (sfx.PEAK_GUARD_DBFS / 20.0)
    assert peak <= peak_guard_linear + 1e-6
    # Guard should have kicked in below the RMS target (can't hit -18 dBFS
    # RMS without exceeding the peak guard on this signal).
    measured = _measured_rms_dbfs(wav_path)
    assert measured < sfx.RMS_TARGET_DBFS
    warning = capsys.readouterr().out
    assert "peak guard" in warning.lower() or "warning" in warning.lower()


def test_rms_normalize_skips_near_silent_clip(tmp_path, capsys):
    wav_path = str(tmp_path / "silence.wav")
    sf.write(wav_path, np.zeros(1000, dtype=np.float32), 44100, subtype="FLOAT")
    sfx.rms_normalize(wav_path)  # must not raise / divide-by-zero
    warning = capsys.readouterr().out
    assert "silent" in warning.lower()


# -- Mono -> stereo, 48 kHz conversion (ffmpeg) --

def test_convert_to_canonical_upmixes_mono_and_resamples(tmp_path):
    src_path = str(tmp_path / "mono_src.wav")
    _write_sine(src_path, seconds=0.5, sr=22050, amplitude=0.3, channels=1)
    dest_path = str(tmp_path / "canonical.wav")

    sfx.convert_to_canonical(src_path, dest_path)

    info = sf.info(dest_path)
    assert info.channels == sfx.CANONICAL_CHANNELS == 2
    assert info.samplerate == sfx.CANONICAL_SR == 48000

    data, _ = sf.read(dest_path, dtype="float32", always_2d=True)
    # Mono duplicated to L/R, not fake stereo width: channels identical.
    assert np.allclose(data[:, 0], data[:, 1], atol=1e-4)


# -- get_sfx: cache hit avoids network + API entirely --

def test_get_sfx_cache_hit_skips_execute_and_download(tmp_path, monkeypatch):
    # Build a tiny real WAV as the "downloaded" fixture — fake_download just
    # copies it in place of an httpx GET.
    fixture_path = str(tmp_path / "fixture_source.wav")
    _write_sine(fixture_path, seconds=0.3, sr=44100, amplitude=0.3, channels=1)

    calls = {"execute": 0, "download": 0}

    def fake_execute(endpoint, arguments, **kwargs):
        calls["execute"] += 1
        return {"audio": {"url": "https://example.invalid/fake.wav"}}

    def fake_download(url, dest_path, timeout=60.0):
        calls["download"] += 1
        shutil.copy(fixture_path, dest_path)
        return dest_path

    # Patched on `ledger`, not on `sfx`: generation goes through
    # providers.genblaze.FoleyAdapterProvider, which calls
    # ledger.execute_with_ledger and then net.download to materialize the
    # result. The Genblaze pipeline around them is exercised for real.
    monkeypatch.setattr(ledger, "execute_with_ledger", fake_execute)
    monkeypatch.setattr(net, "download", fake_download)

    cache_dir = str(tmp_path / "sfx_cache_test")
    request = _req(endpoint=CASSETTEAI, prompt="test cache hit", duration_s=1.0)

    path1 = sfx.get_sfx(request, cache_dir=cache_dir)
    assert calls == {"execute": 1, "download": 1}
    assert sf.info(path1).samplerate == 48000
    assert sf.info(path1).channels == 2

    path2 = sfx.get_sfx(request, cache_dir=cache_dir)
    assert path1 == path2
    # No new network/API calls on the cache hit.
    assert calls == {"execute": 1, "download": 1}


def test_get_sfx_different_requests_produce_different_cache_files(tmp_path, monkeypatch):
    fixture_path = str(tmp_path / "fixture_source2.wav")
    _write_sine(fixture_path, seconds=0.3, sr=44100, amplitude=0.3, channels=1)

    def fake_execute(endpoint, arguments, **kwargs):
        return {"audio": {"url": "https://example.invalid/fake2.wav"}}

    def fake_download(url, dest_path, timeout=60.0):
        shutil.copy(fixture_path, dest_path)
        return dest_path

    # Patched on `ledger`, not on `sfx`: generation goes through
    # providers.genblaze.FoleyAdapterProvider, which calls
    # ledger.execute_with_ledger and then net.download to materialize the
    # result. The Genblaze pipeline around them is exercised for real.
    monkeypatch.setattr(ledger, "execute_with_ledger", fake_execute)
    monkeypatch.setattr(net, "download", fake_download)

    cache_dir = str(tmp_path / "sfx_cache_test2")
    path_a = sfx.get_sfx(_req(prompt="footsteps on gravel"), cache_dir=cache_dir)
    path_b = sfx.get_sfx(_req(prompt="footsteps on snow"), cache_dir=cache_dir)
    assert path_a != path_b
