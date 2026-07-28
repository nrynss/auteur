"""Audio and visual QA — loudness, true peak, and exposure continuity.

SPEC.md §11.5 / §13: true-peak overage blocks export; loudness drift and
exposure jumps are warnings that open evidence, never automatic corrections.

Measurements are deterministic ffmpeg / arithmetic. Scene colour statistics
already exist (§12); this module only compares adjacent shots and measures a
mix with `ebur128=peak=true`.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence

# Broadcast-ish delivery targets from SPEC.md §11.5.
TARGET_LUFS = -14.0
TARGET_TRUE_PEAK_DBTP = -1.0

# How far integrated loudness may sit from the target before a warning.
# ±2 LU is a common delivery window around a nominated target.
LOUDNESS_TOLERANCE_LU = 2.0

# Adjacent-shot mean-luma delta (0..255) that fires an exposure warning.
# The demo clip's scenes 4→5 jump by 31.7; 20 keeps that firing while ignoring
# ordinary within-shot variation.
LUMA_DELTA_THRESHOLD = 20.0

# ffmpeg on a truncated mix must not pin a worker indefinitely.
FFMPEG_TIMEOUT_S = 120

SEVERITY_BLOCKING = "blocking"
SEVERITY_WARNING = "warning"

CODE_TRUE_PEAK_EXCEEDED = "true_peak_exceeded"
CODE_LOUDNESS_OUT_OF_RANGE = "loudness_out_of_range"
CODE_EXPOSURE_DISCONTINUITY = "exposure_discontinuity"

STATUS_OK = "ok"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_BLOCKED = "blocked"


class QaError(RuntimeError):
    """ffmpeg failed while measuring loudness."""


@dataclass(frozen=True)
class QaIssue:
    severity: str
    code: str
    scene_id: Optional[str] = None
    compared_with: Optional[str] = None
    confidence: Optional[float] = None
    detail: Optional[str] = None

    def to_dict(self) -> dict:
        payload = {"severity": self.severity, "code": self.code}
        if self.scene_id is not None:
            payload["scene_id"] = self.scene_id
        if self.compared_with is not None:
            payload["compared_with"] = self.compared_with
        if self.confidence is not None:
            payload["confidence"] = self.confidence
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


@dataclass(frozen=True)
class AudioMetrics:
    integrated_lufs: float
    true_peak_dbtp: float
    loudness_range_lu: Optional[float] = None


@dataclass(frozen=True)
class AudioQa:
    integrated_lufs: Optional[float]
    true_peak_dbtp: Optional[float]
    target_lufs: float = TARGET_LUFS
    target_true_peak_dbtp: float = TARGET_TRUE_PEAK_DBTP
    issues: List[QaIssue] = field(default_factory=list)
    measured: bool = False
    loudness_range_lu: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "integrated_lufs": self.integrated_lufs,
            "true_peak_dbtp": self.true_peak_dbtp,
            "target_lufs": self.target_lufs,
            "target_true_peak_dbtp": self.target_true_peak_dbtp,
            "loudness_range_lu": self.loudness_range_lu,
            "measured": self.measured,
            "issues": [i.to_dict() for i in self.issues],
        }


@dataclass(frozen=True)
class VisualQa:
    issues: List[QaIssue] = field(default_factory=list)
    luma_delta_threshold: float = LUMA_DELTA_THRESHOLD

    def to_dict(self) -> dict:
        return {
            "luma_delta_threshold": self.luma_delta_threshold,
            "issues": [i.to_dict() for i in self.issues],
        }


@dataclass(frozen=True)
class QaReport:
    run_id: Optional[str]
    overall_status: str
    audio: AudioQa
    visual: VisualQa

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "overall_status": self.overall_status,
            "audio": self.audio.to_dict(),
            "visual": self.visual.to_dict(),
        }

    @property
    def has_blocking(self) -> bool:
        return self.overall_status == STATUS_BLOCKED

    @property
    def has_warnings(self) -> bool:
        return any(
            i.severity == SEVERITY_WARNING
            for i in (*self.audio.issues, *self.visual.issues)
        )


def _run_ffmpeg(cmd: List[str]) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        raise QaError(f"ffmpeg timed out after {FFMPEG_TIMEOUT_S}s") from e
    return proc


_SUMMARY_I = re.compile(r"^\s*I:\s*(-?[0-9.]+)\s*LUFS", re.M)
_SUMMARY_LRA = re.compile(r"^\s*LRA:\s*(-?[0-9.]+)\s*LU", re.M)
_SUMMARY_PEAK = re.compile(r"True peak:\s*\n\s*Peak:\s*(-?[0-9.]+)\s*dBFS", re.M)
# Fallback when the Summary block is missing (very short clips).
_STREAM_I = re.compile(r"\bI:\s*(-?[0-9.]+)\s*LUFS")
_STREAM_FTPK = re.compile(r"FTPK:\s*(-?[0-9.]+)\s+(-?[0-9.]+)\s*dBFS")


def measure_loudness(audio_path: str) -> AudioMetrics:
    """Integrated loudness and true peak via ffmpeg `ebur128=peak=true`.

    True peak is reported in dBFS by ffmpeg; that value *is* dBTP for delivery
    checks (SPEC.md §11.5).
    """
    proc = _run_ffmpeg([
        "ffmpeg", "-hide_banner", "-nostdin",
        "-i", audio_path,
        "-filter:a", "ebur128=peak=true",
        "-f", "null", "-",
    ])
    stderr = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        tail = stderr.strip()[-300:]
        raise QaError(f"ffmpeg ebur128 failed (exit {proc.returncode}): {tail}")

    summary = stderr
    if "Summary:" in stderr:
        summary = stderr[stderr.rfind("Summary:"):]

    i_match = _SUMMARY_I.search(summary) or _STREAM_I.search(stderr)
    peak_match = _SUMMARY_PEAK.search(stderr)
    if not peak_match:
        ftpk = list(_STREAM_FTPK.finditer(stderr))
        if ftpk:
            last = ftpk[-1]
            peak = max(float(last.group(1)), float(last.group(2)))
        else:
            peak = None
    else:
        peak = float(peak_match.group(1))

    if i_match is None or peak is None:
        raise QaError(
            f"could not parse ebur128 summary from ffmpeg output for {audio_path!r}"
        )

    lra_match = _SUMMARY_LRA.search(summary)
    return AudioMetrics(
        integrated_lufs=round(float(i_match.group(1)), 1),
        true_peak_dbtp=round(peak, 1),
        loudness_range_lu=(round(float(lra_match.group(1)), 1)
                           if lra_match else None),
    )


def check_audio(
    metrics: AudioMetrics,
    *,
    target_lufs: float = TARGET_LUFS,
    target_true_peak_dbtp: float = TARGET_TRUE_PEAK_DBTP,
    loudness_tolerance_lu: float = LOUDNESS_TOLERANCE_LU,
    scene_id: Optional[str] = None,
) -> List[QaIssue]:
    """Compare measured loudness against the §11.5 / §13 targets."""
    issues: List[QaIssue] = []
    if metrics.true_peak_dbtp > target_true_peak_dbtp:
        over = metrics.true_peak_dbtp - target_true_peak_dbtp
        issues.append(QaIssue(
            severity=SEVERITY_BLOCKING,
            code=CODE_TRUE_PEAK_EXCEEDED,
            scene_id=scene_id,
            detail=(f"true peak {metrics.true_peak_dbtp:.1f} dBTP exceeds "
                    f"target {target_true_peak_dbtp:.1f} by {over:.1f} dB"),
        ))
    drift = abs(metrics.integrated_lufs - target_lufs)
    if drift > loudness_tolerance_lu:
        issues.append(QaIssue(
            severity=SEVERITY_WARNING,
            code=CODE_LOUDNESS_OUT_OF_RANGE,
            scene_id=scene_id,
            detail=(f"integrated loudness {metrics.integrated_lufs:.1f} LUFS "
                    f"is {drift:.1f} LU from target {target_lufs:.1f}"),
        ))
    return issues


def _scene_luma(scene: Any) -> Optional[float]:
    """Accept a Scene dataclass, a raw scenes.json dict, or None."""
    if scene is None:
        return None
    stats = getattr(scene, "stats", None)
    if stats is None and isinstance(scene, dict):
        stats = scene.get("stats")
    if stats is None:
        return None
    if isinstance(stats, dict):
        value = stats.get("mean_luma")
    else:
        value = getattr(stats, "mean_luma", None)
    return float(value) if value is not None else None


def _scene_id(scene: Any) -> str:
    if isinstance(scene, dict):
        return scene["id"]
    return scene.id


def exposure_confidence(delta: float, threshold: float = LUMA_DELTA_THRESHOLD) -> float:
    """Map how far a jump exceeds the threshold onto 0.5..0.99.

    Tuned so the demo clip's 31.7-luma jump at threshold 20 lands near the
    SPEC.md §11.5 example confidence of 0.82.
    """
    if delta <= threshold:
        return 0.0
    excess = (delta - threshold) / threshold
    return round(min(0.99, 0.5 + 0.5 * min(1.0, excess)), 2)


def check_continuity(
    scenes: Sequence[Any],
    *,
    luma_threshold: float = LUMA_DELTA_THRESHOLD,
) -> List[QaIssue]:
    """Flag adjacent shots whose mean luma differs by at least `luma_threshold`.

    Warning only — SPEC.md §12 / §13 forbid treating this as an automatic grade.
    Scenes without stats are skipped rather than inventing a discontinuity.
    """
    issues: List[QaIssue] = []
    for prev, curr in zip(scenes, scenes[1:]):
        a = _scene_luma(prev)
        b = _scene_luma(curr)
        if a is None or b is None:
            continue
        delta = abs(b - a)
        if delta < luma_threshold:
            continue
        issues.append(QaIssue(
            severity=SEVERITY_WARNING,
            code=CODE_EXPOSURE_DISCONTINUITY,
            scene_id=_scene_id(curr),
            compared_with=_scene_id(prev),
            confidence=exposure_confidence(delta, luma_threshold),
            detail=(f"mean luma jumps {delta:.1f} between "
                    f"{_scene_id(prev)} ({a:.1f}) and "
                    f"{_scene_id(curr)} ({b:.1f})"),
        ))
    return issues


def _overall_status(issues: Sequence[QaIssue]) -> str:
    if any(i.severity == SEVERITY_BLOCKING for i in issues):
        return STATUS_BLOCKED
    if issues:
        return STATUS_NEEDS_REVIEW
    return STATUS_OK


def build_report(
    scenes: Sequence[Any] = (),
    *,
    audio_path: Optional[str] = None,
    run_id: Optional[str] = None,
    target_lufs: float = TARGET_LUFS,
    target_true_peak_dbtp: float = TARGET_TRUE_PEAK_DBTP,
    loudness_tolerance_lu: float = LOUDNESS_TOLERANCE_LU,
    luma_threshold: float = LUMA_DELTA_THRESHOLD,
) -> QaReport:
    """Assemble a full QA report from scenes and an optional mix/WAV path.

    Continuity needs only stored scene stats. Audio needs a real mix — silent
    sources and pre-mix projects leave `audio.measured` false rather than
    inventing numbers.
    """
    visual_issues = check_continuity(scenes, luma_threshold=luma_threshold)
    visual = VisualQa(issues=visual_issues, luma_delta_threshold=luma_threshold)

    if audio_path:
        metrics = measure_loudness(audio_path)
        audio_issues = check_audio(
            metrics,
            target_lufs=target_lufs,
            target_true_peak_dbtp=target_true_peak_dbtp,
            loudness_tolerance_lu=loudness_tolerance_lu,
        )
        audio = AudioQa(
            integrated_lufs=metrics.integrated_lufs,
            true_peak_dbtp=metrics.true_peak_dbtp,
            target_lufs=target_lufs,
            target_true_peak_dbtp=target_true_peak_dbtp,
            loudness_range_lu=metrics.loudness_range_lu,
            issues=audio_issues,
            measured=True,
        )
    else:
        audio = AudioQa(
            integrated_lufs=None,
            true_peak_dbtp=None,
            target_lufs=target_lufs,
            target_true_peak_dbtp=target_true_peak_dbtp,
            issues=[],
            measured=False,
        )

    all_issues = [*audio.issues, *visual.issues]
    return QaReport(
        run_id=run_id,
        overall_status=_overall_status(all_issues),
        audio=audio,
        visual=visual,
    )


def report_from_dict(d: dict) -> QaReport:
    """Rehydrate a stored report. Tolerates older payloads missing `measured`."""
    def _issues(raw: Sequence[dict]) -> List[QaIssue]:
        return [
            QaIssue(
                severity=i["severity"],
                code=i["code"],
                scene_id=i.get("scene_id"),
                compared_with=i.get("compared_with"),
                confidence=i.get("confidence"),
                detail=i.get("detail"),
            )
            for i in raw
        ]

    audio_raw = d.get("audio") or {}
    visual_raw = d.get("visual") or {}
    measured = bool(audio_raw.get("measured",
                                   audio_raw.get("integrated_lufs") is not None))
    audio = AudioQa(
        integrated_lufs=audio_raw.get("integrated_lufs"),
        true_peak_dbtp=audio_raw.get("true_peak_dbtp"),
        target_lufs=float(audio_raw.get("target_lufs", TARGET_LUFS)),
        target_true_peak_dbtp=float(
            audio_raw.get("target_true_peak_dbtp", TARGET_TRUE_PEAK_DBTP)),
        loudness_range_lu=audio_raw.get("loudness_range_lu"),
        issues=_issues(audio_raw.get("issues") or []),
        measured=measured,
    )
    visual = VisualQa(
        issues=_issues(visual_raw.get("issues") or []),
        luma_delta_threshold=float(
            visual_raw.get("luma_delta_threshold", LUMA_DELTA_THRESHOLD)),
    )
    return QaReport(
        run_id=d.get("run_id"),
        overall_status=d.get("overall_status") or _overall_status(
            [*audio.issues, *visual.issues]),
        audio=audio,
        visual=visual,
    )


def with_preserved_audio(
    report: QaReport, previous: Optional[dict],
) -> QaReport:
    """Keep prior mix measurements when this write is continuity-only.

    Re-analysis rebuilds visual QA from fresh scene stats but does not re-measure
    the mix. Without this merge, a continuity-only save would erase a blocking
    true-peak finding and unblock export incorrectly.
    """
    if report.audio.measured or not previous:
        return report
    prev_audio = (previous.get("audio") or {})
    if not prev_audio.get("measured"):
        return report
    audio = report_from_dict({"audio": prev_audio, "visual": {}}).audio
    all_issues = [*audio.issues, *report.visual.issues]
    return QaReport(
        run_id=report.run_id,
        overall_status=_overall_status(all_issues),
        audio=audio,
        visual=report.visual,
    )
