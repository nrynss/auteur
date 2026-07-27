"""Timeline -> full-length stereo mix — T3.

Pure numpy + soundfile mixing; ZERO API calls happen in this module's math.
SFX acquisition is injected (`sfx_fn`), defaulting to sfx.get_sfx — tests
inject a fake and the whole mixer runs offline.

Determinism is a product claim (picture-lock: same timeline -> byte-identical
render), not a nicety. Every random choice (per-tile gain jitter) is drawn
from a numpy Generator seeded by a sha256 of the event name + start time —
never Python's process-randomized `hash()`.

Mix length is round(clip_duration_s * 48000) samples where clip_duration_s
is the ffprobe value passed by the caller — NEVER timeline.duration (that
field is display-only, see models.Timeline).
"""

import hashlib
import os
from typing import Optional

import numpy as np
import soundfile as sf

from foley import sfx
from foley.models import Timeline
from foley.routing import route_ambience, route_event

SR = sfx.CANONICAL_SR            # 48000
CHANNELS = sfx.CANONICAL_CHANNELS  # 2

# Degradation ladder (build_spec.md): each tier renders standalone.
TIERS = ("ambience", "discrete", "full")

# Ear-tunable at T5 — every mix level lives here, nowhere else. Baseline:
# every cached clip arrives RMS-normalized to -18 dBFS (sfx.py), so these
# are relative balances between layers, not absolute loudness fixes.
GAINS = {
    "ambience": 0.35,       # the bed sits low under everything
    "discrete_low": 0.5,
    "discrete_med": 0.8,
    "discrete_high": 1.0,
    "rhythmic": 0.6,        # fallback when no per-intensity entry matches
    "rhythmic_low": 0.45,   # per-intensity rhythmic gains: lets a directed
    "rhythmic_med": 0.6,    # timeline split one walking interval into
    "rhythmic_high": 0.75,  # low->med->high segments to build as someone approaches

    "continuous": 0.45,     # BEFORE the locked -6 dB duck below
}

# Numeric defaults (locked) — build_spec.md table. NOT ear-tunable.
DISCRETE_BIAS_S = 0.05        # +50 ms late bias; audio never precedes visual
CONTINUOUS_DUCK_DB = -6.0     # continuous layers duck under the main bed
MAX_CONTINUOUS_LAYERS = 2     # concurrent continuous layers; extras dropped
ONSET_TRIM_THRESHOLD_DBFS = -40.0
MASTER_PEAK_DBFS = -1.0       # scale down (never hard-clip) past this

# Shape constants.
PLACE_FADE_S = 0.010      # click-guard linear edge fades on placed audio
LOOP_XFADE_S = 0.050      # equal-power crossfade at loop tile seams
LOOP_JITTER_DB = 1.5      # per-tile gain jitter (rhythmic tiling only)
BED_EDGE_FADE_S = 0.250   # ambience/continuous layer edge fade in/out


def _db_to_linear(db: float) -> float:
    return 10.0 ** (db / 20.0)


def _event_seed(name: str, start_time: float) -> int:
    """Deterministic per-event RNG seed. sha256, not hash() — Python string
    hashing is randomized per process and would break byte-identical renders."""
    raw = f"{name}|{start_time:.3f}"
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8], 16)


def load_clip(path: str) -> np.ndarray:
    """Read a canonical WAV as float32 (N, 2). Fails loud on a non-48 kHz
    file — conversion happens once at sfx cache time, never in mixing math."""
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    if sr != SR:
        raise ValueError(f"assemble: {path} is {sr} Hz, expected canonical {SR} Hz")
    return data


def _apply_edge_fades(seg: np.ndarray, fade_s: float) -> np.ndarray:
    """Linear fade-in/out over the first/last fade_s of `seg`, in place.
    Fade length is clamped to half the segment so short segments still work."""
    n = len(seg)
    f = min(int(round(fade_s * SR)), n // 2)
    if f <= 0:
        return seg
    ramp = np.linspace(0.0, 1.0, f, endpoint=False, dtype=np.float32)[:, None]
    seg[:f] *= ramp
    seg[n - f:] *= ramp[::-1]
    return seg


def place(mix: np.ndarray, clip: np.ndarray, start_s: float, gain: float) -> np.ndarray:
    """Slice-add `clip` * gain into `mix` at start_s, with PLACE_FADE_S edge
    fades. Overruns past the mix end are truncated FIRST, then faded — the
    placed audio always fades to zero, so a truncated tail never clicks.
    Starts at/after the mix end are skipped. Mutates and returns `mix`."""
    n = len(mix)
    start = int(round(start_s * SR))
    if start < 0:
        # Never place early: drop the pre-start samples instead of shifting.
        clip = clip[-start:]
        start = 0
    if start >= n or len(clip) == 0:
        return mix
    seg = clip[: n - start] * np.float32(gain)  # copy — safe to fade in place
    _apply_edge_fades(seg, PLACE_FADE_S)
    mix[start : start + len(seg)] += seg
    return mix


def trim_onset(clip: np.ndarray, threshold_dbfs: float = ONSET_TRIM_THRESHOLD_DBFS):
    """Strip leading samples below threshold_dbfs, keeping up to PLACE_FADE_S
    of quiet pre-roll so place()'s fade-in ramps over silence instead of
    eating the transient's attack.

    Returns (trimmed, onset_s): onset_s = seconds of kept pre-roll before the
    first loud sample; the caller subtracts it from the placement time so the
    transient itself lands exactly on target. (Deviation from the suggested
    array-only return: without onset_s the caller can't compensate for the
    pre-roll and the transient would land up to 10 ms late.)

    A clip starting hot is returned untouched with onset_s = 0. An all-quiet
    clip is returned untouched with a warning."""
    thresh = np.float32(_db_to_linear(threshold_dbfs))
    loud = np.flatnonzero(np.max(np.abs(clip), axis=1) >= thresh)
    if len(loud) == 0:
        print("[assemble] WARNING: clip never crosses "
              f"{threshold_dbfs} dBFS — onset trim skipped.")
        return clip, 0.0
    preroll = int(round(PLACE_FADE_S * SR))
    first = int(loud[0])
    start = max(first - preroll, 0)
    return clip[start:], (first - start) / SR


def _tile_segment(loop: np.ndarray, n_samples: int, gain: float, rng,
                  xfade_s: float = LOOP_XFADE_S,
                  jitter_db: float = 0.0) -> np.ndarray:
    """Tile `loop` to cover n_samples with equal-power (sin/cos) crossfades
    at the seams. jitter_db > 0 draws one uniform gain offset per tile from
    `rng` (pass jitter_db=0.0 and rng=None for a steady bed). Returns a new
    (n_samples, 2) float32 array."""
    if n_samples <= 0 or len(loop) == 0:
        return np.zeros((max(n_samples, 0), CHANNELS), dtype=np.float32)
    loop_len = len(loop)
    xfade = min(int(round(xfade_s * SR)), loop_len // 2)
    hop = max(loop_len - xfade, 1)
    if xfade > 0:
        t = np.linspace(0.0, 1.0, xfade, endpoint=False, dtype=np.float32)[:, None]
        fade_in = np.sin(t * np.pi / 2).astype(np.float32)
        fade_out = np.cos(t * np.pi / 2).astype(np.float32)

    out = np.zeros((n_samples + loop_len, CHANNELS), dtype=np.float32)
    pos = 0
    tile_idx = 0
    while pos < n_samples:
        g = gain
        if jitter_db > 0.0:
            g = gain * _db_to_linear(rng.uniform(-jitter_db, jitter_db))
        tile = loop * np.float32(g)  # copy per tile — safe to window in place
        if xfade > 0:
            if tile_idx > 0:
                tile[:xfade] *= fade_in
            tile[-xfade:] *= fade_out
        out[pos : pos + loop_len] += tile
        pos += hop
        tile_idx += 1
    return out[:n_samples]


def tile_loop(mix: np.ndarray, loop: np.ndarray, start_s: float, end_s: float,
              gain: float, seed: int) -> np.ndarray:
    """Rhythmic beds: tile `loop` across [start_s, end_s] with ~50 ms
    equal-power seam crossfades and per-tile gain jitter of ±LOOP_JITTER_DB,
    drawn from a Generator seeded with `seed` (deterministic: same seed ->
    byte-identical tiles). Edge-faded (PLACE_FADE_S), end clamped to the mix.
    Mutates and returns `mix`."""
    n = len(mix)
    start = max(int(round(start_s * SR)), 0)
    end = min(int(round(end_s * SR)), n)
    if end <= start:
        return mix
    seg = _tile_segment(loop, end - start, gain, np.random.default_rng(seed),
                        jitter_db=LOOP_JITTER_DB)
    _apply_edge_fades(seg, PLACE_FADE_S)
    mix[start:end] += seg
    return mix


def _add_bed(mix: np.ndarray, clip: np.ndarray, start_s: float, end_s: float,
             gain: float, edge_fade_s: float = BED_EDGE_FADE_S) -> np.ndarray:
    """Ambience-style layer: loop/trim `clip` over [start_s, end_s] (seam
    crossfades via _tile_segment, no jitter — the bed level stays put), then
    edge fade in/out (~250 ms). Used for the main ambience bed and for
    continuous events. Mutates and returns `mix`."""
    n = len(mix)
    start = max(int(round(start_s * SR)), 0)
    end = min(int(round(end_s * SR)), n)
    if end <= start:
        return mix
    seg = _tile_segment(clip, end - start, gain, rng=None, jitter_db=0.0)
    _apply_edge_fades(seg, edge_fade_s)
    mix[start:end] += seg
    return mix


def master_normalize(mix: np.ndarray, peak_dbfs: float = MASTER_PEAK_DBFS) -> np.ndarray:
    """If the summed mix peaks above peak_dbfs, scale the WHOLE mix down to
    that peak and print a warning. Never hard-clips."""
    peak = float(np.max(np.abs(mix))) if len(mix) else 0.0
    limit = _db_to_linear(peak_dbfs)
    if peak > limit:
        peak_db = 20.0 * np.log10(peak)
        print(f"[assemble] WARNING: mix peak {peak_db:.1f} dBFS exceeds "
              f"{peak_dbfs} dBFS — scaling entire mix down (no hard clip).")
        mix = (mix * np.float32(limit / peak)).astype(np.float32)
    return mix


def assemble_timeline(timeline: Timeline, clip_duration_s: float,
                      tier: str = "full", sfx_fn=None,
                      orig_audio_path: Optional[str] = None) -> np.ndarray:
    """Render `timeline` to a finished stereo float32 mix of exactly
    round(clip_duration_s * 48000) samples.

    clip_duration_s MUST be the ffprobe duration (never timeline.duration).
    sfx_fn(SfxRequest) -> WAV path; defaults to sfx.get_sfx (cached, billed
    on miss) — tests inject a fake for fully-offline renders.
    orig_audio_path: Optional path to a WAV file of the video's original audio.
    If provided, it is loaded and mixed in as a background bed.

    Tiers (degradation ladder, each standalone):
      ambience  — bed only
      discrete  — bed + discrete events
      full      — + rhythmic tiling + continuous layers
    """
    if tier not in TIERS:
        raise ValueError(f"assemble: unknown tier {tier!r}, expected one of {TIERS}")
    if sfx_fn is None:
        sfx_fn = sfx.get_sfx

    n = int(round(clip_duration_s * SR))
    mix = np.zeros((n, CHANNELS), dtype=np.float32)

    # Mix original audio bed (e.g. music underscore) if provided
    if orig_audio_path and os.path.exists(orig_audio_path):
        try:
            orig_clip = load_clip(orig_audio_path)
            n_orig = len(orig_clip)
            if n_orig > n:
                orig_clip = orig_clip[:n]
            elif n_orig < n:
                pad = np.zeros((n - n_orig, CHANNELS), dtype=np.float32)
                orig_clip = np.vstack([orig_clip, pad])
            
            # Mix music bed with a default gain (0.5) so foley sits as foreground
            mix += orig_clip * np.float32(0.5)
            print(f"[assemble] mixed original audio bed from {orig_audio_path}", flush=True)
        except Exception as e:
            print(f"[assemble] WARNING: failed to load/mix original audio: {e}", flush=True)

    # Ambience bed — every tier.
    if timeline.ambience:
        clip = load_clip(sfx_fn(route_ambience(timeline.ambience, clip_duration_s)))
        _add_bed(mix, clip, 0.0, clip_duration_s, GAINS["ambience"])
    else:
        print("[assemble] WARNING: timeline has no ambience string — bed skipped.")

    # Discrete events — "discrete" and "full" tiers.
    if tier in ("discrete", "full"):
        for ev in timeline.events:
            if ev.kind != "discrete":
                continue
            clip = load_clip(sfx_fn(route_event(ev)))
            clip, onset_s = trim_onset(clip)
            gain = GAINS.get(f"discrete_{ev.intensity}", GAINS["discrete_med"])
            # +50 ms late bias; onset_s compensates for the kept pre-roll so
            # the transient itself lands at start_time + bias exactly.
            place(mix, clip, ev.start_time + DISCRETE_BIAS_S - onset_s, gain)

    if tier == "full":
        # Rhythmic events: tile the 1.75 s loop across the event interval.
        for ev in timeline.events:
            if ev.kind != "rhythmic":
                continue
            loop = load_clip(sfx_fn(route_event(ev)))
            gain = GAINS.get(f"rhythmic_{ev.intensity}", GAINS["rhythmic"])
            tile_loop(mix, loop, ev.start_time,
                      min(ev.start_time + ev.duration, clip_duration_s),
                      gain, _event_seed(ev.name, ev.start_time))

        # Continuous events: ambience treatment over their interval, ducked
        # -6 dB under the main bed. Concurrency rule: timeline order wins —
        # a continuous event overlapping >= 2 already-accepted continuous
        # layers is dropped with a warning (first-come priority; simple and
        # deterministic, revisit by ear at T5 if it ever fires on a demo clip).
        duck = _db_to_linear(CONTINUOUS_DUCK_DB)
        accepted = []  # list of (start_s, end_s)
        for ev in timeline.events:
            if ev.kind != "continuous":
                continue
            s = ev.start_time
            e = min(ev.start_time + ev.duration, clip_duration_s)
            overlapping = [a for a in accepted if a[0] < e and s < a[1]]
            if len(overlapping) >= MAX_CONTINUOUS_LAYERS:
                print(f"[assemble] WARNING: dropping continuous layer "
                      f"'{ev.name}' at {s:.2f}s — already "
                      f"{MAX_CONTINUOUS_LAYERS} concurrent continuous layers "
                      f"(first-come priority).")
                continue
            accepted.append((s, e))
            clip = load_clip(sfx_fn(route_event(ev)))
            _add_bed(mix, clip, s, e, GAINS["continuous"] * duck)

    return master_normalize(mix)


def write_mix(mix: np.ndarray, path: str) -> None:
    """Write the mix as a canonical 48 kHz stereo float32 WAV."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    sf.write(path, mix.astype(np.float32), SR, subtype="FLOAT")
