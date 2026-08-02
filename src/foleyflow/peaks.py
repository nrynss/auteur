"""Waveform peak envelopes for control-room display.

## Decisions (unit 7.5)

**Resolution.** ``PEAKS_PER_SECOND = 100`` (10 ms bins). Dense enough for
timeline zoom; a 30 s stem is ~3000 floats per ``min``/``max`` array.

**Storage.** Peaks live next to the audio object as
``{audio_key}.peaks.json`` — e.g. ``cache/<sha>.wav.peaks.json`` or
``projects/{id}/runs/{run_id}/mix.wav.peaks.json``. Written once with
``put_if_absent`` so concurrent first paints do not race. A cache hit
returns the stored JSON without re-decoding audio. Corrupt or mismatched
cache objects are treated as a miss and recomputed (overwritten with
``put_bytes`` when ``put_if_absent`` cannot replace).

**Format.** Separate ``min`` and ``max`` float lists (raw sample values,
typically in ``[-1, 1]``), plus ``peaks_per_second``, ``channels``,
``sample_rate``, and ``duration_s``. Two-sided envelopes draw cleanly;
mono max-abs is recoverable as ``max(|min[i]|, |max[i]|)``.

**Peak algorithm.** For each bin of samples, take the minimum and maximum
across every sample and channel in that bin. Canonical audio is 48 kHz
stereo float WAV (see ``foleyflow.sfx`` / ``assemble``).
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from typing import List, Optional, Union

import numpy as np
import soundfile as sf

from foleyflow.storage.base import Backend, ObjectNotFound, PreconditionFailed

PEAKS_PER_SECOND = 100
PEAKS_SUFFIX = ".peaks.json"


class InvalidAudioError(Exception):
    """Audio object exists but cannot be decoded as a WAV."""

    def __init__(self, key: str, reason: str = ""):
        self.key = key
        self.reason = reason
        msg = f"invalid audio at {key!r}"
        if reason:
            msg = f"{msg}: {reason}"
        super().__init__(msg)


@dataclass(frozen=True)
class PeaksResult:
    """Two-sided peak envelope for one audio object."""

    peaks_per_second: int
    channels: int
    sample_rate: int
    duration_s: float
    min: List[float]
    max: List[float]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PeaksResult":
        if not isinstance(data, dict):
            raise TypeError("peaks data must be a dict")
        for field in (
            "peaks_per_second",
            "channels",
            "sample_rate",
            "duration_s",
            "min",
            "max",
        ):
            if field not in data:
                raise KeyError(field)
        mins = [float(x) for x in data["min"]]
        maxs = [float(x) for x in data["max"]]
        if len(mins) != len(maxs):
            raise ValueError("min and max must have the same length")
        return cls(
            peaks_per_second=int(data["peaks_per_second"]),
            channels=int(data["channels"]),
            sample_rate=int(data["sample_rate"]),
            duration_s=float(data["duration_s"]),
            min=mins,
            max=maxs,
        )


def peaks_cache_key(audio_key: str) -> str:
    """Sibling peaks object for an audio storage key.

    ``cache/abc.wav`` → ``cache/abc.wav.peaks.json``.
    ``…/mix.wav`` → ``…/mix.wav.peaks.json``.
    """
    if not audio_key or audio_key.endswith("/"):
        raise ValueError(f"not a valid audio key: {audio_key!r}")
    return f"{audio_key}{PEAKS_SUFFIX}"


def compute_peaks(
    wav_path_or_array: Union[str, np.ndarray],
    *,
    peaks_per_second: int = PEAKS_PER_SECOND,
    sample_rate: Optional[int] = None,
) -> PeaksResult:
    """Compute a min/max peak envelope for a WAV path or float array.

    * Path: read with soundfile (float32, always 2-D).
    * Array: shape ``(n,)`` or ``(n, channels)``; ``sample_rate`` required.

    Bins are ``sample_rate / peaks_per_second`` samples wide. Partial last
    bins still contribute one peak. Empty audio yields empty ``min``/``max``.
    """
    if peaks_per_second <= 0:
        raise ValueError("peaks_per_second must be > 0")

    if isinstance(wav_path_or_array, (str, os.PathLike)):
        try:
            data, sr = sf.read(
                os.fspath(wav_path_or_array), dtype="float32", always_2d=True)
        except sf.SoundFileError as e:
            raise InvalidAudioError(os.fspath(wav_path_or_array), str(e)) from e
    else:
        if sample_rate is None:
            raise ValueError("sample_rate is required when passing an array")
        arr = np.asarray(wav_path_or_array, dtype=np.float32)
        if arr.ndim == 1:
            data = arr[:, None]
        elif arr.ndim == 2:
            data = arr
        else:
            raise ValueError(f"expected 1-D or 2-D array, got shape {arr.shape}")
        sr = int(sample_rate)

    n_samples, n_channels = data.shape
    duration_s = float(n_samples) / float(sr) if sr > 0 else 0.0

    if n_samples == 0:
        return PeaksResult(
            peaks_per_second=peaks_per_second,
            channels=n_channels,
            sample_rate=sr,
            duration_s=0.0,
            min=[],
            max=[],
        )

    samples_per_bin = sr / float(peaks_per_second)
    n_bins = max(1, int(math.ceil(n_samples / samples_per_bin)))

    mins: List[float] = []
    maxs: List[float] = []
    for i in range(n_bins):
        start = int(round(i * samples_per_bin))
        end = int(round((i + 1) * samples_per_bin))
        if start >= n_samples:
            break
        end = min(end, n_samples)
        if end <= start:
            continue
        chunk = data[start:end]
        mins.append(float(np.min(chunk)))
        maxs.append(float(np.max(chunk)))

    return PeaksResult(
        peaks_per_second=peaks_per_second,
        channels=n_channels,
        sample_rate=sr,
        duration_s=duration_s,
        min=mins,
        max=maxs,
    )


def _load_peaks_json(
    backend: Backend,
    peaks_key: str,
    *,
    peaks_per_second: Optional[int] = None,
) -> Optional[PeaksResult]:
    """Load cached peaks, or ``None`` on miss / corrupt / resolution mismatch.

    Corrupt JSON or schema errors are treated as a cache miss so the caller
    can recompute. When ``peaks_per_second`` is given and the stored value
    differs, also treat as a miss.
    """
    try:
        raw = backend.get_bytes(peaks_key)
    except ObjectNotFound:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
        result = PeaksResult.from_dict(data)
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError,
            ValueError):
        return None
    if (peaks_per_second is not None
            and result.peaks_per_second != peaks_per_second):
        return None
    return result


def get_or_compute_peaks(
    backend: Backend,
    audio_key: str,
    *,
    work_dir: str,
    peaks_per_second: int = PEAKS_PER_SECOND,
) -> PeaksResult:
    """Return peaks for ``audio_key``, computing and caching on miss.

    1. Try the sibling peaks object (valid schema + matching resolution).
    2. On miss: download/read audio, compute, ``put_if_absent`` peaks JSON.
    3. If ``put_if_absent`` loses the race, re-load; if still unusable
       (corrupt or wrong resolution), ``put_bytes`` overwrite.

    Raises ``ObjectNotFound`` when the audio object is absent.
    Raises ``InvalidAudioError`` when the object exists but is not decodable.
    """
    peaks_key = peaks_cache_key(audio_key)
    cached = _load_peaks_json(
        backend, peaks_key, peaks_per_second=peaks_per_second)
    if cached is not None:
        return cached

    os.makedirs(work_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="peaks-", suffix=".wav", dir=work_dir)
    os.close(fd)
    try:
        backend.get(audio_key, tmp_path)
        try:
            result = compute_peaks(tmp_path, peaks_per_second=peaks_per_second)
        except InvalidAudioError as e:
            # Re-bind to the storage key, not the local temp path.
            raise InvalidAudioError(audio_key, e.reason) from e
        payload = json.dumps(result.to_dict(), separators=(",", ":")).encode("utf-8")
        try:
            backend.put_if_absent(peaks_key, payload, content_type="application/json")
        except PreconditionFailed:
            # Another writer won the race — prefer their stored peaks when valid.
            raced = _load_peaks_json(
                backend, peaks_key, peaks_per_second=peaks_per_second)
            if raced is not None:
                return raced
            # Object exists but is corrupt or wrong resolution — overwrite.
            backend.put_bytes(peaks_key, payload, content_type="application/json")
        return result
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def collect_project_stem_keys(store, project_id: str) -> set:
    """Every ``b2_key`` recorded in any stem inventory for the project.

    Used for bulk inventory inspection. Prefer ``is_project_stem_key`` for
    single-key auth — that path short-circuits on the first hit.
    """
    keys: set = set()
    for run in store.list_runs(project_id):
        for stem in store.load_stem_inventory(project_id, run.id):
            b2_key = stem.get("b2_key")
            if isinstance(b2_key, str) and b2_key:
                keys.add(b2_key)
    return keys


def is_project_stem_key(store, project_id: str, key: str) -> bool:
    """True when ``key`` appears in some stem inventory for the project.

    1. Latest mix run inventory first (common case for control-room paint).
    2. Remaining runs newest-first; return True on the first hit.
    3. False if no inventory lists the key.

    Does not build the full stem-key set — returns as soon as membership is
    known, so projects with many historical runs stay cheap for hot keys.
    """
    checked: set = set()

    latest = store.get_latest_mix(project_id)
    if latest is not None:
        run_id = latest.get("run_id")
        if isinstance(run_id, str) and run_id:
            checked.add(run_id)
            for stem in store.load_stem_inventory(project_id, run_id):
                b2_key = stem.get("b2_key")
                if isinstance(b2_key, str) and b2_key == key:
                    return True

    # list_runs is oldest-first; reverse for newest-first scan.
    for run in reversed(store.list_runs(project_id)):
        if run.id in checked:
            continue
        for stem in store.load_stem_inventory(project_id, run.id):
            b2_key = stem.get("b2_key")
            if isinstance(b2_key, str) and b2_key == key:
                return True
    return False
