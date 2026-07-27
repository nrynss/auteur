"""Genblaze integration — the workflow spine.

Genblaze owns stage sequencing, provider invocation, retry, and the hash-verified
provenance manifest; B2 is where every artifact and manifest lands. This module
is the seam between FoleyFlow's own config and Genblaze's `Pipeline` / sink.

## Why ingest comes first

Genblaze has no fal connector (checked at 0.3.7: assemblyai, decart, elevenlabs,
gmicloud, google, hume, langsmith, lmnt, luma, nvidia, openai, replicate, runway,
s3, stability-audio), so generation cannot run through it until we write one.
`Pipeline.ingest()` needs no provider at all — it records the act of bringing
bytes in, with the same canonical-hash discipline as a generative run. That makes
it the honest first stage: SPEC.md §7 lists Ingest as deterministic anyway.

## Credentials

`S3StorageBackend.for_backblaze()` reads `B2_BUCKET` / `B2_REGION` / `B2_KEY_ID` /
`B2_APP_KEY` from the environment. We pass ours explicitly instead: our `.env`
names the secret `B2_APPLICATION_KEY`, and storing the same credential under two
names is how they drift apart.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Optional

from dotenv import load_dotenv

from genblaze_core import Asset, KeyStrategy, ObjectStorageSink, Pipeline
from genblaze_s3 import S3StorageBackend

load_dotenv()

# SPEC.md §10 puts every FoleyFlow object under one root prefix.
DEFAULT_PREFIX = os.environ.get("B2_PREFIX", "foleyflow")


def work_dir() -> str:
    """FoleyFlow's local scratch directory.

    Genblaze only reads `file://` inputs from below the system temp directory —
    a deliberate guard against arbitrary file reads, and `ObjectStorageSink`
    offers no way to widen it. Staging here is the sanctioned path rather than a
    workaround: in the real flow an uploaded video lands in a working directory
    anyway, and this is also the natural `work_dir` for
    `foleyflow.sfx_cache.backed_sfx_fn`.
    """
    path = os.path.join(tempfile.gettempdir(), "foleyflow", "work")
    os.makedirs(path, exist_ok=True)
    return path


def stage_for_ingest(src_path: str, *, subdir: str = "") -> str:
    """Copy `src_path` into the working directory so Genblaze may read it.

    Returns the staged path. Copies rather than moves — the caller's file is an
    input we do not own.
    """
    dest_dir = os.path.join(work_dir(), subdir) if subdir else work_dir()
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(src_path))
    if os.path.abspath(src_path) != os.path.abspath(dest):
        shutil.copyfile(src_path, dest)
    return dest


def get_storage_backend(*, preflight: bool = True) -> S3StorageBackend:
    """Build the B2 backend from FoleyFlow's own environment names.

    `region` is passed rather than left to auto-detection on purpose. Genblaze
    auto-corrects the region when B2 answers a cross-region request with a 301,
    but some regions — us-east-005, the one this project uses — answer 403
    instead, and there is no redirect header to read. A wrong region there fails
    with an opaque auth error rather than self-correcting.
    """
    bucket = os.environ.get("B2_BUCKET")
    if not bucket:
        raise RuntimeError("B2_BUCKET is unset — see .env.example")

    return S3StorageBackend.for_backblaze(
        bucket,
        region=os.environ.get("B2_REGION"),
        key_id=os.environ.get("B2_KEY_ID"),
        app_key=os.environ.get("B2_APPLICATION_KEY"),
        auto_lifecycle=False,   # lifecycle is managed out-of-band, not per-construction
        preflight=preflight,
    )


def get_sink(prefix: str = DEFAULT_PREFIX, *, preflight: bool = True) -> ObjectStorageSink:
    """Artifact sink for every FoleyFlow run.

    CONTENT_ADDRESSABLE keys assets by SHA-256, so re-ingesting the same source
    video costs one object rather than one per project — the same property that
    makes the SFX cache shareable across projects.
    """
    return ObjectStorageSink(
        get_storage_backend(preflight=preflight),
        prefix=prefix,
        key_strategy=KeyStrategy.CONTENT_ADDRESSABLE,
    )


def ingest_source_video(
    video_path: str,
    *,
    project_id: str,
    title: Optional[str] = None,
    sink: Optional[ObjectStorageSink] = None,
):
    """Record a source video into B2 with a provenance manifest.

    This is SPEC.md §7's Ingest stage: the input becomes an immutable, hashed,
    durably-stored record before any analysis runs. Returns Genblaze's
    PipelineResult — `.manifest.canonical_hash` is the value a judge can verify
    with `genblaze verify`.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(video_path)

    staged = stage_for_ingest(video_path, subdir=project_id)
    # Three slashes: file:///E:/... — url2pathname needs the leading slash before
    # a Windows drive letter, and a two-slash form would read 'E:' as a host.
    asset = Asset(
        url="file:///" + staged.replace(os.sep, "/").lstrip("/"),
        media_type="video/mp4",
    )

    return Pipeline.ingest(
        assets=[asset],
        source="foleyflow-upload",
        source_metadata={
            "project_id": project_id,
            "title": title or os.path.basename(video_path),
            "original_filename": os.path.basename(video_path),
        },
        sink=sink or get_sink(),
        name=f"ingest-{project_id}",
    )
