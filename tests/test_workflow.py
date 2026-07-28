"""Offline tests for the Genblaze seam.

No B2 calls and no credentials: everything here exercises path handling and
configuration, which is where this module's real bugs live. The live path is
covered by actually running an ingest, not by a mock that would just assert
Genblaze's own behavior back at us.
"""

import os
import tempfile

import pytest

from foleyflow import workflow


def test_work_dir_is_readable_by_genblaze():
    """Genblaze only reads file:// inputs from below the temp dir and exposes no
    way to widen that allowlist, so a work_dir outside it makes every ingest
    fail with an opaque access-denied error.

    Asserted against genblaze's own allowlist rather than against
    tempfile.gettempdir() — work_dir is *built from* gettempdir, so comparing
    the two could never fail.
    """
    from pathlib import Path

    from genblaze_core._utils import ALLOWED_FILE_ROOTS

    resolved = Path(workflow.work_dir()).resolve()
    assert resolved.is_dir()
    assert any(resolved.is_relative_to(root) for root in ALLOWED_FILE_ROOTS), (
        f"{resolved} is outside genblaze's allowlist {ALLOWED_FILE_ROOTS}")


def test_stage_copies_rather_than_moves(tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"video-bytes")

    staged = workflow.stage_for_ingest(str(src), subdir="proj_test_copy")
    assert os.path.exists(staged)
    assert src.exists(), "the caller's input file must survive staging"
    with open(staged, "rb") as f:
        assert f.read() == b"video-bytes"


def test_stage_is_idempotent(tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"abc")
    a = workflow.stage_for_ingest(str(src), subdir="proj_test_idem")
    b = workflow.stage_for_ingest(str(src), subdir="proj_test_idem")
    assert a == b


def test_stage_separates_projects(tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"abc")
    a = workflow.stage_for_ingest(str(src), subdir="proj_a")
    b = workflow.stage_for_ingest(str(src), subdir="proj_b")
    assert a != b


def test_staged_path_yields_a_readable_file_url(tmp_path):
    """Regression: on Windows a two-slash file:// URL parses the drive letter
    as a hostname, so url2pathname returns a path that does not exist."""
    from urllib.request import url2pathname
    from urllib.parse import urlparse

    src = tmp_path / "clip.mp4"
    src.write_bytes(b"abc")
    staged = workflow.stage_for_ingest(str(src), subdir="proj_url")

    url = "file:///" + staged.replace(os.sep, "/").lstrip("/")
    parsed = urlparse(url)
    assert parsed.netloc == "", "drive letter must not be parsed as a host"
    assert os.path.exists(url2pathname(parsed.path))


def test_missing_video_raises_before_touching_the_network():
    with pytest.raises(FileNotFoundError):
        workflow.ingest_source_video("does/not/exist.mp4", project_id="proj_x")


def test_backend_requires_a_bucket(monkeypatch):
    monkeypatch.delenv("B2_BUCKET", raising=False)
    with pytest.raises(RuntimeError, match="B2_BUCKET"):
        workflow.get_storage_backend(preflight=False)
