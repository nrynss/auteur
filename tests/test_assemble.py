"""Offline unit tests for src/foleyflow/assemble.py. NO network / fal API calls —
assemble_timeline always gets an injected fake sfx_fn that writes synthetic
48 kHz stereo WAVs; the pure helpers get numpy arrays directly.
"""

import numpy as np
import pytest
import soundfile as sf

from foleyflow import assemble
from foleyflow.models import Event, Timeline

SR = assemble.SR  # 48000


# -- Synthetic clip helpers --

def _stereo(mono: np.ndarray) -> np.ndarray:
    return np.stack([mono, mono], axis=1).astype(np.float32)


def _dc(seconds: float, amp: float = 0.5) -> np.ndarray:
    return _stereo(np.full(int(round(seconds * SR)), amp, dtype=np.float32))


def _sine(seconds: float, freq: float = 440.0, amp: float = 0.25) -> np.ndarray:
    t = np.arange(int(round(seconds * SR))) / SR
    return _stereo((amp * np.sin(2 * np.pi * freq * t)).astype(np.float32))


def _impulse(n: int, at: int, amp: float = 0.8) -> np.ndarray:
    mono = np.zeros(n, dtype=np.float32)
    mono[at] = amp
    return _stereo(mono)


def _zero_mix(seconds: float) -> np.ndarray:
    return np.zeros((int(round(seconds * SR)), 2), dtype=np.float32)


# -- place --

def test_place_transient_lands_at_biased_sample_index():
    mix = _zero_mix(3.0)
    # Peak at sample 1000 — past the 480-sample (10 ms) fade-in, so the
    # envelope does not attenuate it.
    clip = _impulse(4800, at=1000)
    start_s = 1.0 + assemble.DISCRETE_BIAS_S  # caller applies the +50 ms bias
    assemble.place(mix, clip, start_s, gain=1.0)
    expected = round((1.0 + 0.05) * SR) + 1000
    assert int(np.argmax(np.abs(mix[:, 0]))) == expected
    assert mix[expected, 0] == pytest.approx(0.8)


def test_place_applies_edge_fade_envelope():
    mix = _zero_mix(2.0)
    clip = _dc(0.5, amp=0.5)
    start = int(round(0.5 * SR))
    assemble.place(mix, clip, 0.5, gain=1.0)
    fade = int(round(assemble.PLACE_FADE_S * SR))
    assert mix[start, 0] == pytest.approx(0.0)              # fade-in starts at 0
    assert mix[start + fade, 0] == pytest.approx(0.5)       # past the fade: full
    assert mix[start + len(clip) - 1, 0] == pytest.approx(0.0)  # fade-out ends at 0


def test_place_truncates_overrun_and_fades_the_truncated_tail():
    mix = _zero_mix(1.0)
    clip = _dc(0.5, amp=0.5)   # placed at 0.8 s -> only 0.2 s fits
    assemble.place(mix, clip, 0.8, gain=1.0)
    assert len(mix) == SR      # mix length unchanged
    assert mix[int(0.9 * SR), 0] == pytest.approx(0.5)  # middle of what fits
    assert mix[-1, 0] == pytest.approx(0.0)             # tail faded, no click


def test_place_beyond_mix_end_is_a_noop():
    mix = _zero_mix(1.0)
    assemble.place(mix, _dc(0.5), 2.0, gain=1.0)
    assert not np.any(mix)


def test_place_applies_gain():
    mix = _zero_mix(1.0)
    assemble.place(mix, _dc(0.2, amp=0.5), 0.1, gain=0.4)
    mid = int(round(0.2 * SR))  # middle of the placed clip, past the fades
    assert mix[mid, 0] == pytest.approx(0.5 * 0.4)


# -- trim_onset --

def test_trim_onset_strips_leading_silence_keeping_fade_preroll():
    lead = int(0.1 * SR)  # 100 ms of digital silence
    clip = np.concatenate([_stereo(np.zeros(lead, dtype=np.float32)),
                           _dc(0.1, amp=0.5)])
    trimmed, onset_s = assemble.trim_onset(clip)
    preroll = int(round(assemble.PLACE_FADE_S * SR))
    assert len(trimmed) == len(clip) - lead + preroll
    assert onset_s == pytest.approx(preroll / SR)
    # First loud sample sits exactly `preroll` samples in.
    assert trimmed[preroll, 0] == pytest.approx(0.5)
    assert not np.any(np.abs(trimmed[:preroll]) > 0)


def test_trim_onset_leaves_hot_clip_untouched():
    clip = _dc(0.2, amp=0.5)
    trimmed, onset_s = assemble.trim_onset(clip)
    assert onset_s == 0.0
    assert np.array_equal(trimmed, clip)


def test_trim_onset_treats_sub_threshold_noise_as_silence():
    # -50 dBFS leading noise floor is below the -40 dBFS threshold.
    lead = int(0.1 * SR)
    quiet = _stereo(np.full(lead, 10 ** (-50 / 20), dtype=np.float32))
    clip = np.concatenate([quiet, _dc(0.1, amp=0.5)])
    trimmed, onset_s = assemble.trim_onset(clip)
    preroll = int(round(assemble.PLACE_FADE_S * SR))
    assert len(trimmed) == len(clip) - lead + preroll


def test_trim_onset_all_quiet_clip_returned_unchanged_with_warning(capsys):
    clip = _stereo(np.full(1000, 1e-4, dtype=np.float32))  # ~-80 dBFS
    trimmed, onset_s = assemble.trim_onset(clip)
    assert np.array_equal(trimmed, clip)
    assert onset_s == 0.0
    assert "warning" in capsys.readouterr().out.lower()


# -- tile_loop --

def test_tile_loop_covers_interval_and_nothing_outside():
    mix = _zero_mix(6.0)
    rng = np.random.default_rng(0)
    loop = _stereo(rng.uniform(-0.3, 0.3, int(1.75 * SR)).astype(np.float32))
    assemble.tile_loop(mix, loop, 1.0, 4.0, gain=1.0, seed=42)
    start, end = int(1.0 * SR), int(4.0 * SR)
    assert not np.any(mix[:start])          # silent before
    assert not np.any(mix[end:])            # silent after
    # Energy throughout the interval interior (checked in 250 ms windows).
    win = SR // 4
    for pos in range(start, end - win, win):
        assert float(np.sqrt(np.mean(mix[pos:pos + win] ** 2))) > 0.01


def test_tile_loop_seams_are_crossfaded_no_discontinuity_spikes():
    # A 440.3 Hz sine does NOT loop cleanly over 0.5 s — hard concatenation
    # would produce a phase-jump spike at each seam. With equal-power 50 ms
    # crossfades the sample-to-sample difference stays near the sine's own
    # slope (~0.014 per sample at this amplitude).
    mix = _zero_mix(3.0)
    loop = _sine(0.5, freq=440.3, amp=0.25)
    assemble.tile_loop(mix, loop, 0.0, 3.0, gain=1.0, seed=7)
    max_jump = float(np.max(np.abs(np.diff(mix[:, 0]))))
    assert max_jump < 0.06  # a hard seam at these phases would jump > 0.2


def test_tile_loop_jitter_within_bounds_and_actually_applied():
    mix = _zero_mix(5.0)
    loop = _dc(0.5, amp=0.5)  # DC: mid-tile sample value == amp * tile gain
    assemble.tile_loop(mix, loop, 0.0, 5.0, gain=0.7, seed=123)
    loop_len = len(loop)
    xfade = int(round(assemble.LOOP_XFADE_S * SR))
    hop = loop_len - xfade
    lo, hi = 10 ** (-assemble.LOOP_JITTER_DB / 20), 10 ** (assemble.LOOP_JITTER_DB / 20)
    ratios = []
    k = 0
    while k * hop + loop_len - xfade < len(mix):
        mid = k * hop + loop_len // 2  # away from both seams
        ratios.append(mix[mid, 0] / (0.5 * 0.7))
        k += 1
    assert all(lo - 1e-4 <= r <= hi + 1e-4 for r in ratios)
    assert len(set(np.round(ratios, 6))) > 1  # jitter really varies per tile


def test_tile_loop_deterministic_for_same_seed():
    loop = _sine(0.5, freq=311.0, amp=0.2)
    mix_a = _zero_mix(4.0)
    mix_b = _zero_mix(4.0)
    assemble.tile_loop(mix_a, loop, 0.5, 3.5, gain=0.6, seed=99)
    assemble.tile_loop(mix_b, loop, 0.5, 3.5, gain=0.6, seed=99)
    assert mix_a.tobytes() == mix_b.tobytes()


def test_tile_loop_different_seed_differs():
    loop = _dc(0.5, amp=0.5)
    mix_a = _zero_mix(4.0)
    mix_b = _zero_mix(4.0)
    assemble.tile_loop(mix_a, loop, 0.0, 4.0, gain=0.6, seed=1)
    assemble.tile_loop(mix_b, loop, 0.0, 4.0, gain=0.6, seed=2)
    assert mix_a.tobytes() != mix_b.tobytes()


# -- Master normalize --

def test_master_normalize_scales_clipping_mix_to_minus_one_dbfs(capsys):
    mix = _sine(1.0, amp=1.0) + _sine(1.0, amp=0.6)  # peaks at 1.6
    out = assemble.master_normalize(mix)
    limit = 10 ** (assemble.MASTER_PEAK_DBFS / 20)
    peak = float(np.max(np.abs(out)))
    assert peak <= limit + 1e-6
    assert peak == pytest.approx(limit, rel=1e-4)  # scaled, not clipped
    assert not np.any(np.abs(out) > 1.0)
    assert "warning" in capsys.readouterr().out.lower()


def test_master_normalize_leaves_quiet_mix_untouched():
    mix = _sine(0.5, amp=0.3)
    out = assemble.master_normalize(mix)
    assert np.array_equal(out, mix)


# -- assemble_timeline: tiers, bias end-to-end, determinism --

def _fake_sfx_fn(tmp_path):
    """Offline stand-in for sfx.get_sfx: canonical 48 kHz stereo WAVs keyed
    by routing category. Ambience = quiet DC bed; discrete (impact) = leading
    silence + impulse (exercises onset trim); footsteps (rhythmic here) =
    noise loop; default (continuous here) = quiet DC."""
    rng = np.random.default_rng(0)
    clips = {
        "ambience": _dc(7.0, amp=0.1),
        # amp 0.6 keeps the full-tier peak below -1 dBFS so master normalize
        # does not rescale and break exact tier-comparison assertions.
        "impact_strike": np.concatenate([
            _stereo(np.zeros(int(0.3 * SR), dtype=np.float32)),  # leading silence
            _impulse(int(0.7 * SR), at=0, amp=0.6),
        ]),
        "footsteps": _stereo(rng.uniform(-0.2, 0.2, int(1.75 * SR)).astype(np.float32)),
        "default": _dc(2.0, amp=0.3),
    }
    paths = {}
    for category, data in clips.items():
        p = str(tmp_path / f"{category}.wav")
        sf.write(p, data, SR, subtype="FLOAT")
        paths[category] = p

    def fn(request):
        return paths[request.category]

    return fn


def _timeline():
    return Timeline(
        duration=999.0,  # display-only garbage on purpose — must be ignored
        ambience="light wind in trees",
        events=[
            Event(name="glass impact", start_time=2.0, duration=0.3,
                  intensity="high", kind="discrete"),
            Event(name="footsteps on gravel", start_time=0.0, duration=1.5,
                  intensity="med", kind="rhythmic"),
            Event(name="fabric rustling", start_time=3.5, duration=1.0,
                  intensity="low", kind="continuous"),
        ],
    )


def test_assemble_mix_length_from_clip_duration_not_timeline(tmp_path):
    mix = assemble.assemble_timeline(_timeline(), 5.0, tier="ambience",
                                     sfx_fn=_fake_sfx_fn(tmp_path))
    assert mix.shape == (round(5.0 * SR), 2)
    assert mix.dtype == np.float32


def test_assemble_unknown_tier_raises(tmp_path):
    with pytest.raises(ValueError, match="tier"):
        assemble.assemble_timeline(_timeline(), 5.0, tier="loudest",
                                   sfx_fn=_fake_sfx_fn(tmp_path))


def test_ambience_tier_has_no_discrete_energy_full_tier_does(tmp_path):
    fn = _fake_sfx_fn(tmp_path)
    amb = assemble.assemble_timeline(_timeline(), 5.0, tier="ambience", sfx_fn=fn)
    full = assemble.assemble_timeline(_timeline(), 5.0, tier="full", sfx_fn=fn)
    # The discrete impulse lands at start_time + 50 ms bias exactly.
    idx = round((2.0 + assemble.DISCRETE_BIAS_S) * SR)
    bed_level = 0.1 * assemble.GAINS["ambience"]
    assert abs(amb[idx, 0]) <= bed_level + 1e-5           # bed only
    assert abs(full[idx, 0]) > bed_level + 0.4            # bed + hot transient


def test_discrete_tier_has_discrete_but_no_rhythmic_energy(tmp_path):
    fn = _fake_sfx_fn(tmp_path)
    amb = assemble.assemble_timeline(_timeline(), 5.0, tier="ambience", sfx_fn=fn)
    disc = assemble.assemble_timeline(_timeline(), 5.0, tier="discrete", sfx_fn=fn)
    idx = round((2.0 + assemble.DISCRETE_BIAS_S) * SR)
    assert abs(disc[idx, 0]) > 0.4                        # discrete present
    # Rhythmic window [0, 1.5] s: discrete tier == ambience tier there.
    r = slice(0, int(1.5 * SR))
    assert np.array_equal(disc[r], amb[r])


def test_full_tier_adds_rhythmic_and_continuous_layers(tmp_path):
    fn = _fake_sfx_fn(tmp_path)
    amb = assemble.assemble_timeline(_timeline(), 5.0, tier="ambience", sfx_fn=fn)
    full = assemble.assemble_timeline(_timeline(), 5.0, tier="full", sfx_fn=fn)
    r = slice(int(0.2 * SR), int(1.3 * SR))               # rhythmic interior
    c = slice(int(3.8 * SR), int(4.2 * SR))               # continuous interior
    assert not np.array_equal(full[r], amb[r])
    assert not np.array_equal(full[c], amb[c])


def test_continuous_layer_is_ducked_6db_under_its_gain(tmp_path):
    fn = _fake_sfx_fn(tmp_path)
    amb = assemble.assemble_timeline(_timeline(), 5.0, tier="ambience", sfx_fn=fn)
    full = assemble.assemble_timeline(_timeline(), 5.0, tier="full", sfx_fn=fn)
    mid = int(4.0 * SR)  # continuous interior, past the layer edge fades
    layer = full[mid, 0] - amb[mid, 0]
    expected = 0.3 * assemble.GAINS["continuous"] * 10 ** (assemble.CONTINUOUS_DUCK_DB / 20)
    assert layer == pytest.approx(expected, rel=1e-3)


def test_max_two_concurrent_continuous_layers_extras_dropped(tmp_path, capsys):
    fn = _fake_sfx_fn(tmp_path)
    t = Timeline(duration=0.0, ambience="wind", events=[
        Event("fabric rustling", 1.0, 3.0, "low", "continuous"),
        Event("cloth shifting sound", 1.5, 3.0, "low", "continuous"),
        Event("tarp flapping sound", 2.0, 3.0, "low", "continuous"),
    ])
    mix = assemble.assemble_timeline(t, 6.0, tier="full", sfx_fn=fn)
    out = capsys.readouterr().out
    assert "dropping continuous layer" in out
    assert "tarp flapping sound" in out
    # Only 2 layers + bed contribute. Each ducked DC layer contributes at most
    # sqrt(2) * its gain (equal-power crossfade seam bump on a fully-correlated
    # signal); a third accepted layer would exceed this bound.
    duck = 10 ** (assemble.CONTINUOUS_DUCK_DB / 20)
    layer = 0.3 * assemble.GAINS["continuous"] * duck
    two_layers_worst = 0.1 * assemble.GAINS["ambience"] + 2 * np.sqrt(2) * layer
    assert float(np.max(mix[:, 0])) <= two_layers_worst + 1e-4


def test_assemble_timeline_is_byte_identical_across_runs(tmp_path):
    fn = _fake_sfx_fn(tmp_path)
    mix_a = assemble.assemble_timeline(_timeline(), 5.0, tier="full", sfx_fn=fn)
    mix_b = assemble.assemble_timeline(_timeline(), 5.0, tier="full", sfx_fn=fn)
    assert mix_a.tobytes() == mix_b.tobytes()


# -- event seed freeze (unit 7.1) --

def _rhythmic_only_timeline(start_time: float, seed=None, duration: float = 2.0):
    """Minimal timeline: no ambience/discrete so the mix is pure rhythmic tiles."""
    return Timeline(
        duration=0.0,
        ambience="",
        events=[
            Event(name="footsteps on gravel", start_time=start_time,
                  duration=duration, intensity="med", kind="rhythmic",
                  seed=seed),
        ],
    )


def test_absent_seed_matches_explicit_legacy_event_seed(tmp_path):
    """No seed field → same mix as freezing _event_seed(name, start_time)."""
    fn = _fake_sfx_fn(tmp_path)
    t1, d = 0.4, 2.0
    legacy = assemble._event_seed("footsteps on gravel", t1)
    mix_absent = assemble.assemble_timeline(
        _rhythmic_only_timeline(t1, seed=None, duration=d),
        5.0, tier="full", sfx_fn=fn)
    mix_frozen_legacy = assemble.assemble_timeline(
        _rhythmic_only_timeline(t1, seed=legacy, duration=d),
        5.0, tier="full", sfx_fn=fn)
    assert mix_absent.tobytes() == mix_frozen_legacy.tobytes()


def test_absent_seed_changes_when_start_time_changes(tmp_path):
    """Legacy path: nudging start_time redraws jitter (different seed)."""
    fn = _fake_sfx_fn(tmp_path)
    d = 2.0
    mix_a = assemble.assemble_timeline(
        _rhythmic_only_timeline(0.5, seed=None, duration=d),
        5.0, tier="full", sfx_fn=fn)
    mix_b = assemble.assemble_timeline(
        _rhythmic_only_timeline(0.6, seed=None, duration=d),
        5.0, tier="full", sfx_fn=fn)
    # After aligning by the 0.1 s nudge, interiors must differ (new seed).
    fade = int(round(assemble.PLACE_FADE_S * SR))
    a = mix_a[int(0.5 * SR) + fade : int(0.5 * SR) + int(d * SR) - fade]
    b = mix_b[int(0.6 * SR) + fade : int(0.6 * SR) + int(d * SR) - fade]
    n = min(len(a), len(b))
    assert not np.allclose(a[:n], b[:n], atol=1e-6)


def test_frozen_seed_jitter_texture_independent_of_start_time(tmp_path):
    """Same frozen seed at two start times → byte-identical jitter, shifted."""
    fn = _fake_sfx_fn(tmp_path)
    frozen = 424242
    d = 2.0
    t1, t2 = 0.3, 0.8
    mix_a = assemble.assemble_timeline(
        _rhythmic_only_timeline(t1, seed=frozen, duration=d),
        5.0, tier="full", sfx_fn=fn)
    mix_b = assemble.assemble_timeline(
        _rhythmic_only_timeline(t2, seed=frozen, duration=d),
        5.0, tier="full", sfx_fn=fn)
    # Interior of the rhythmic span, past edge fades which sit at absolute ends.
    fade = int(round(assemble.PLACE_FADE_S * SR))
    a = mix_a[int(round(t1 * SR)) + fade : int(round((t1 + d) * SR)) - fade]
    b = mix_b[int(round(t2 * SR)) + fade : int(round((t2 + d) * SR)) - fade]
    assert len(a) == len(b)
    assert len(a) > SR  # at least ~1 s of interior to compare
    # Byte identity, not float tolerance — AC2 is a pure translation.
    assert a.tobytes() == b.tobytes()


def test_seed_zero_is_frozen_not_absent(tmp_path):
    """seed=0 is falsy but must not fall through to the legacy name+start hash."""
    fn = _fake_sfx_fn(tmp_path)
    d = 2.0
    mix_zero = assemble.assemble_timeline(
        _rhythmic_only_timeline(0.5, seed=0, duration=d),
        5.0, tier="full", sfx_fn=fn)
    mix_legacy = assemble.assemble_timeline(
        _rhythmic_only_timeline(0.5, seed=None, duration=d),
        5.0, tier="full", sfx_fn=fn)
    # Only true if the legacy hash happens to be 0 — vanishingly unlikely, and
    # if it ever is, the frozen-at-two-starts test still covers seed=0 usage.
    if assemble._event_seed("footsteps on gravel", 0.5) != 0:
        assert mix_zero.tobytes() != mix_legacy.tobytes()
    # seed=0 still freezes texture across a start_time nudge.
    mix_shifted = assemble.assemble_timeline(
        _rhythmic_only_timeline(0.9, seed=0, duration=d),
        5.0, tier="full", sfx_fn=fn)
    fade = int(round(assemble.PLACE_FADE_S * SR))
    a = mix_zero[int(0.5 * SR) + fade : int((0.5 + d) * SR) - fade]
    b = mix_shifted[int(0.9 * SR) + fade : int((0.9 + d) * SR) - fade]
    assert len(a) == len(b)
    assert a.tobytes() == b.tobytes()


# -- source_duration_s discrete windowing (unit 7.2) --

def _discrete_only_timeline(duration: float, source_duration_s=None,
                            start_time: float = 1.0):
    """Minimal timeline: no ambience/rhythmic so the mix is pure discrete."""
    return Timeline(
        duration=0.0,
        ambience="",
        events=[
            Event(name="glass impact", start_time=start_time, duration=duration,
                  intensity="high", kind="discrete",
                  source_duration_s=source_duration_s),
        ],
    )


def _long_discrete_sfx_fn(tmp_path, clip_seconds: float = 2.0):
    """SFX stand-in: hot DC clip of fixed length (no leading silence)."""
    data = _dc(clip_seconds, amp=0.5)
    p = str(tmp_path / "long_discrete.wav")
    sf.write(p, data, SR, subtype="FLOAT")

    def fn(request):
        return p

    return fn


def test_absent_source_duration_s_discrete_plays_full_clip(tmp_path):
    """Legacy: placement duration is ignored for discrete; full clip plays."""
    fn = _long_discrete_sfx_fn(tmp_path, clip_seconds=2.0)
    # Short placement duration — without freeze, still places the full 2 s.
    mix = assemble.assemble_timeline(
        _discrete_only_timeline(duration=0.5, source_duration_s=None),
        5.0, tier="discrete", sfx_fn=fn)
    start = int(round((1.0 + assemble.DISCRETE_BIAS_S) * SR))
    # Past the 10 ms fade-in, DC should still be present near 1.5 s into clip.
    far = start + int(1.5 * SR)
    assert mix[far, 0] == pytest.approx(
        0.5 * assemble.GAINS["discrete_high"], rel=1e-3)


def test_source_duration_s_windows_discrete_to_placement_duration(tmp_path):
    """With freeze, a longer generated clip is truncated to placement duration."""
    fn = _long_discrete_sfx_fn(tmp_path, clip_seconds=2.0)
    frozen = 2.0  # matches generated length; not used for windowing
    mix_full = assemble.assemble_timeline(
        _discrete_only_timeline(duration=2.0, source_duration_s=frozen),
        5.0, tier="discrete", sfx_fn=fn)
    mix_short = assemble.assemble_timeline(
        _discrete_only_timeline(duration=0.4, source_duration_s=frozen),
        5.0, tier="discrete", sfx_fn=fn)
    assert mix_full.tobytes() != mix_short.tobytes()
    start = int(round((1.0 + assemble.DISCRETE_BIAS_S) * SR))
    # Full placement: energy ~1.5 s into the clip.
    far = start + int(1.5 * SR)
    assert abs(mix_full[far, 0]) > 0.1
    assert abs(mix_short[far, 0]) < 1e-6  # short placement ends before here
    # Short placement still has energy near the start (past fade).
    near = start + int(0.1 * SR)
    assert abs(mix_short[near, 0]) > 0.1


def test_placement_resize_changes_mix_not_route_duration(tmp_path):
    """Resize placement with frozen source_duration_s: mix changes, request does not."""
    from foleyflow.routing import route_event

    frozen = 2.0
    fn = _long_discrete_sfx_fn(tmp_path, clip_seconds=2.0)
    t_short = _discrete_only_timeline(duration=0.5, source_duration_s=frozen)
    t_long = _discrete_only_timeline(duration=1.5, source_duration_s=frozen)
    req_short = route_event(t_short.events[0])
    req_long = route_event(t_long.events[0])
    assert req_short.duration_s == req_long.duration_s == frozen
    # plan_requests stem inventory must also match.
    plans_s = assemble.plan_requests(t_short, 5.0, tier="discrete")
    plans_l = assemble.plan_requests(t_long, 5.0, tier="discrete")
    assert [(r.endpoint, r.prompt, r.duration_s) for r in plans_s] == \
           [(r.endpoint, r.prompt, r.duration_s) for r in plans_l]
    mix_s = assemble.assemble_timeline(t_short, 5.0, tier="discrete", sfx_fn=fn)
    mix_l = assemble.assemble_timeline(t_long, 5.0, tier="discrete", sfx_fn=fn)
    assert mix_s.tobytes() != mix_l.tobytes()


def test_frozen_source_duration_s_duration_zero_places_silence(tmp_path):
    """Freeze on + placement duration=0 → empty window; no energy from that event."""
    fn = _long_discrete_sfx_fn(tmp_path, clip_seconds=2.0)
    mix = assemble.assemble_timeline(
        _discrete_only_timeline(duration=0.0, source_duration_s=2.0),
        5.0, tier="discrete", sfx_fn=fn)
    # Pure discrete timeline, no ambience: mix must stay silent.
    assert not np.any(mix)


def test_absent_source_duration_s_placement_duration_does_not_window(tmp_path):
    """Legacy absent freeze: two placement durations still play the full clip."""
    fn = _long_discrete_sfx_fn(tmp_path, clip_seconds=2.0)
    mix_a = assemble.assemble_timeline(
        _discrete_only_timeline(duration=0.3, source_duration_s=None),
        5.0, tier="discrete", sfx_fn=fn)
    mix_b = assemble.assemble_timeline(
        _discrete_only_timeline(duration=1.7, source_duration_s=None),
        5.0, tier="discrete", sfx_fn=fn)
    assert mix_a.tobytes() == mix_b.tobytes()


# -- write_mix --

def test_write_mix_produces_canonical_float32_wav(tmp_path):
    mix = _sine(1.0, amp=0.3)
    out_path = str(tmp_path / "nested" / "mix.wav")  # parent dir auto-created
    assemble.write_mix(mix, out_path)
    info = sf.info(out_path)
    assert info.samplerate == SR
    assert info.channels == 2
    assert info.subtype == "FLOAT"
    data, _ = sf.read(out_path, dtype="float32", always_2d=True)
    assert np.array_equal(data, mix)
