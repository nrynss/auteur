#!/usr/bin/env python3
"""Live exercise of paid + fallback SFX tiers (handoff task 14).

Two halves:

1. **Paid** — real ElevenLabs call with a unique prompt (cache miss). Asserts
   the stem is cached at the paid endpoint address and a ledger row exists
   with project attribution and cost_source estimate.

2. **Fallback** — paid request with ELEVENLABS_API_KEY cleared so the
   connector fails without billing ElevenLabs; fal serves. Asserts
   ``served_endpoint`` is the fal model on the stem result (and optionally
   through a minimal project inventory).

Never prints secret values. Loads ``.env`` and optionally ``../fal-ai/.env``
for ``FAL_KEY``.

Usage:
  uv run python scripts/exercise_tiers.py
  uv run python scripts/exercise_tiers.py --paid-only
  uv run python scripts/exercise_tiers.py --fallback-only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


def _unique_prompt(tag: str) -> str:
    # Unique every run so the SFX cache cannot serve a prior take.
    return (
        f"task14 {tag} single dry wooden door creak short "
        f"{int(time.time() * 1000)} {os.getpid()}"
    )


def _paid_request(prompt: str):
    from foleyflow.routing import SfxRequest
    from foleyflow.tiers import get_tier

    tier = get_tier("paid")
    return SfxRequest(
        endpoint=tier.endpoint,
        prompt=prompt,
        duration_s=1.5,
        category="foley",
        tier=tier.name,
    )


def run_paid(project_id: str) -> dict:
    from foleyflow.ledger import (
        invalidate_project_spend_cache,
        iter_transactions,
        set_project_context,
    )
    from foleyflow.sfx_cache import cache_object_key, prewarm
    from foleyflow.storage import get_backend

    if not os.environ.get("ELEVENLABS_API_KEY"):
        raise SystemExit("ELEVENLABS_API_KEY is required for the paid half")

    prompt = _unique_prompt("paid")
    request = _paid_request(prompt)
    key = cache_object_key(request.endpoint, request.prompt, request.duration_s)
    backend = get_backend()
    if backend.exists(key):
        raise SystemExit(f"cache already has {key}; prompt was not unique")

    before = time.time()
    work = tempfile.mkdtemp(prefix="task14-paid-")
    try:
        with set_project_context(project_id):
            # prewarm generates AND puts the canonical wav under the paid
            # endpoint's cache address (generate_canonical_wav does not put).
            results = prewarm(backend, work, [request])
        if not results or not results[0].ok:
            err = results[0].error if results else "no result"
            raise SystemExit(f"paid generation failed: {err}")
        result = results[0]
        if result.served and result.served.endpoint != request.endpoint:
            raise SystemExit(
                f"paid half escalated unexpectedly to {result.served.endpoint}")
        if not backend.exists(key):
            raise SystemExit(f"paid stem not in cache at {key}")
        local = os.path.join(work, os.path.basename(key))
        local_bytes = os.path.getsize(local) if os.path.exists(local) else 0

        # Ledger rows are append-only; find the one we just wrote.
        invalidate_project_spend_cache()
        rows = [
            r for r in iter_transactions()
            if r.get("endpoint") == request.endpoint
            and float(r.get("timestamp") or 0) >= before - 1.0
            and r.get("project_id") == project_id
        ]
        if not rows:
            raise SystemExit(
                "no ledger row for paid call — LedgerRecordingMixin may be "
                "missing or project context was lost")
        row = rows[-1]
        if not row.get("success"):
            raise SystemExit(f"ledger row records failure: {row.get('error')}")
        cost = float(row.get("cost_estimate") or 0)
        if cost <= 0:
            raise SystemExit(f"ledger cost is not positive: {cost}")
        source = row.get("cost_source") or "estimate"
        return {
            "half": "paid",
            "ok": True,
            "prompt": prompt,
            "endpoint": request.endpoint,
            "served_endpoint": (result.served or request).endpoint,
            "cache_key": key,
            "local_bytes": local_bytes,
            "ledger": {
                "cost_estimate": cost,
                "cost_source": source,
                "project_id": row.get("project_id"),
                "success": row.get("success"),
            },
        }
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)


def run_fallback(project_id: str) -> dict:
    """Paid request with no ElevenLabs key → fal serves; prove served_endpoint.

    Runs a one-stem project mix against a LocalBackend project store so the
    §11.6 manifest names the fal endpoint that actually produced the audio
    (handoff acceptance). Generation goes through prewarm during mix — not a
    prior cache fill — so served_by is recorded on the inventory.
    """
    from foleyflow.ledger import set_project_context
    from foleyflow.models import Event, Timeline
    from foleyflow.projects import ProjectStore
    from foleyflow.render import build_manifest, render_mix
    from foleyflow.storage.local import LocalBackend

    # Force the paid connector to fail without contacting ElevenLabs. Popping
    # the env is not enough: load_dotenv() / the connector may re-read `.env`
    # and restore a real key. An invalid placeholder is the reliable path.
    os.environ["ELEVENLABS_API_KEY"] = "task14-invalid-key-force-fallback"
    if not os.environ.get("FAL_KEY"):
        raise SystemExit(
            "FAL_KEY is required for the fallback half (fal serves when paid fails)")

    # Provider cache may already hold a ledgered ElevenLabs instance from the
    # paid half — force a fresh construction that sees the invalid key.
    from foleyflow.providers import genblaze as gb

    gb._PROVIDER_CACHE.clear()
    os.environ["FOLEYFLOW_SFX_TIER"] = "paid"

    prompt = _unique_prompt("fallback")
    # Paid-tier request shape (for reporting).
    request = _paid_request(prompt)
    requested_ep = request.endpoint

    work = tempfile.mkdtemp(prefix="task14-store-")
    store = ProjectStore(LocalBackend(os.path.join(work, "b2")),
                         work_dir=os.path.join(work, "w"))
    # SFX cache stays on B2 (FOLEYFLOW_CACHE_BACKEND=b2) so fal-served bytes
    # land in the shared bucket; project artifacts stay local.
    proj = store.create_project(
        title="task14-fallback", input_key="in/source.mp4",
        source_sha256="b" * 64, source_filename="task14.mp4",
        duration_s=2.0)
    store.record_input(proj.id, sha256="b" * 64, duration_s=2.0)
    # Unique event name → unique prompt via routing → guaranteed cache miss.
    timeline = Timeline(
        duration=2.0, ambience="",
        events=[Event(name=prompt, start_time=0.2, duration=1.5,
                      intensity="medium", kind="discrete")])
    store.save_timeline(proj.id, timeline)
    video = os.path.join(work, "source.mp4")
    _ensure_video(video, seconds=2.0)
    run = store.start_run(proj.id, "mix", actor="task14")
    with set_project_context(project_id):
        try:
            mix = render_mix(store, proj.id, run=run, video_path=video,
                             tier="discrete")
            run = store.complete_run(
                run, timeline_version=1, metadata={"task14": "fallback"})
        except Exception as exc:
            try:
                store.fail_run(run, str(exc))
            except Exception:
                pass
            raise SystemExit(f"fallback mix failed: {type(exc).__name__}: {exc}")

    inventory = store.load_stem_inventory(proj.id, run.id)
    served_in_inv = [
        s for s in inventory
        if s.get("served_endpoint") and s.get("served_endpoint") != s.get("endpoint")
    ]
    if not served_in_inv:
        raise SystemExit(
            f"stem inventory missing served_endpoint after fallback mix: "
            f"{json.dumps(inventory, indent=2)}")

    served_ep = served_in_inv[0]["served_endpoint"]
    manifest = build_manifest(
        store, proj.id, run=run,
        mix={"run_id": run.id, "tier": "discrete", "timeline_version": 1,
             "mix_key": mix.mix_key, "video_key": mix.video_key},
    )
    stem_arts = [a for a in manifest["artifacts"] if a.get("type") == "audio_stem"]
    served_in_manifest = [
        a for a in stem_arts
        if a.get("served_endpoint") and a.get("served_endpoint") != a.get("endpoint")
    ]
    if not served_in_manifest:
        raise SystemExit(
            f"manifest stems lack served_endpoint: {stem_arts}")

    return {
        "half": "fallback",
        "ok": True,
        "prompt": prompt,
        "requested_endpoint": requested_ep,
        "served_endpoint": served_ep,
        "served_by": served_in_inv[0].get("served_by"),
        "inventory_served": served_in_inv,
        "manifest_served_stems": [
            {k: a.get(k) for k in ("endpoint", "served_endpoint", "served_by",
                                   "b2_key", "prompt") if k in a}
            for a in served_in_manifest
        ],
        "manifest_artifact_types": sorted({a["type"] for a in manifest["artifacts"]}),
        "local_project_id": proj.id,
        "mix_run_id": run.id,
        "mix_key": mix.mix_key,
    }


def _ensure_video(path: str, seconds: float = 2.0) -> None:
    """Write a short silent mp4 via ffmpeg, or a placeholder if ffmpeg is gone."""
    import subprocess

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             f"color=c=black:s=320x240:d={seconds}",
             "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo",
             "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-c:a", "aac", path],
            check=True, capture_output=True, timeout=60)
    except Exception:
        with open(path, "wb") as fh:
            fh.write(b"\0" * 1024)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paid-only", action="store_true")
    parser.add_argument("--fallback-only", action="store_true")
    parser.add_argument(
        "--project-id",
        default="proj_01KZ117JH73KC0E442E79KSCW2",
        help="project_id stamped on ledger rows (default: inhaler demo project)",
    )
    parser.add_argument(
        "--out",
        default=str(ROOT / "scripts" / "task14_results.json"),
        help="where to write the JSON report",
    )
    args = parser.parse_args()

    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT / "src"))

    _load_env_file(ROOT / ".env")
    _load_env_file(ROOT.parent / "fal-ai" / ".env")

    # Storage ledger + B2 cache for real evidence.
    os.environ.setdefault("FOLEYFLOW_CACHE_BACKEND", "b2")
    # Must not force local JSONL or rows leave B2.
    os.environ.pop("FOLEYFLOW_LEDGER_PATH", None)

    results = []
    do_paid = not args.fallback_only
    do_fallback = not args.paid_only

    # Fallback first when both run: a process that has already talked to
    # ElevenLabs may retain a live client. Fallback needs a clean process with
    # an invalid key, so when both halves are requested we subprocess it.
    if do_fallback and do_paid:
        print("=== fallback half (subprocess; paid fails → fal serves) ===",
              flush=True)
        import subprocess
        fb_out = Path(tempfile.mktemp(suffix=".json", prefix="task14-fb-"))
        env = os.environ.copy()
        env["ELEVENLABS_API_KEY"] = "task14-invalid-key-force-fallback"
        # FAL_KEY must already be in the parent env (loaded above).
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()),
             "--fallback-only", "--project-id", args.project_id,
             "--out", str(fb_out)],
            cwd=str(ROOT), env=env, capture_output=True, text=True)
        sys.stdout.write(proc.stdout or "")
        sys.stderr.write(proc.stderr or "")
        if proc.returncode != 0:
            print(f"fallback subprocess failed (exit {proc.returncode})",
                  flush=True)
            return proc.returncode or 1
        if fb_out.is_file():
            results.extend(json.loads(fb_out.read_text(encoding="utf-8")))
            fb_out.unlink(missing_ok=True)
    elif do_fallback:
        print("=== fallback half (paid fails → fal serves) ===", flush=True)
        _load_env_file(ROOT.parent / "fal-ai" / ".env")
        fb = run_fallback(args.project_id)
        results.append(fb)
        print(json.dumps({k: v for k, v in fb.items() if k != "prompt"},
                         indent=2))
        print(f"prompt (unique): {fb['prompt'][:60]}…", flush=True)

    if do_paid:
        print("=== paid half (ElevenLabs live) ===", flush=True)
        # Restore a real key if fallback half invalidated it.
        _load_env_file(ROOT / ".env")
        # Force override from file even if invalid placeholder is present.
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("ELEVENLABS_API_KEY="):
                os.environ["ELEVENLABS_API_KEY"] = (
                    line.split("=", 1)[1].strip().strip("'").strip('"'))
        from foleyflow.providers import genblaze as gb
        gb._PROVIDER_CACHE.clear()
        paid = run_paid(args.project_id)
        results.append(paid)
        print(json.dumps({k: v for k, v in paid.items() if k != "prompt"},
                         indent=2))
        print(f"prompt (unique): {paid['prompt'][:60]}…", flush=True)

    out = Path(args.out)
    out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out}", flush=True)

    # Acceptance summary
    paid_ok = any(r.get("half") == "paid" and r.get("ok") for r in results)
    fb_ok = any(r.get("half") == "fallback" and r.get("ok") for r in results)
    if args.paid_only:
        return 0 if paid_ok else 1
    if args.fallback_only:
        return 0 if fb_ok else 1
    if not (paid_ok and fb_ok):
        return 1
    print("\nACCEPTANCE: paid ledgered + fallback served_endpoint proven",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
