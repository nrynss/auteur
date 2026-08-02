"""OpenAI analysis adapter — the vendor logic the Genblaze lift wraps.

Seam tests in test_analysis_genblaze.py stub execute_with_ledger / _vlm_call,
so they never exercise frame sampling, data-URI building, or the message
shape. These tests cover that surface offline: no OPENAI_API_KEY, a stub
client for generate(), and real ffmpeg for the video path when available.
"""

from __future__ import annotations

import base64
import shutil
from types import SimpleNamespace

import pytest

from foleyflow.providers.base import ModelSpec
from foleyflow.providers import openai as oa

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not on PATH")

SPEC = ModelSpec(model_id="gpt-5.6-luna", adapter="openai", role="analysis")


def test_local_path_resolves_file_urls_and_bare_paths(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"x")
    assert oa._local_path(f"file://{path}") == str(path)
    assert oa._local_path(str(path)) == str(path)
    assert oa._local_path("https://example.com/v.mp4") is None
    assert oa._local_path("data:image/jpeg;base64,aa") is None


def test_image_part_passes_through_data_and_https_urls():
    data = "data:image/jpeg;base64,abc"
    assert oa._image_part(data) == {
        "type": "image_url", "image_url": {"url": data}}
    https = "https://cdn.example/frame.jpg"
    assert oa._image_part(https)["image_url"]["url"] == https


def test_file_as_data_uri_encodes_local_bytes(tmp_path):
    path = tmp_path / "f.jpg"
    path.write_bytes(b"jpeg-bytes")
    uri = oa._file_as_data_uri(str(path))
    assert uri.startswith("data:image/jpeg;base64,")
    encoded = uri.split(",", 1)[1]
    assert base64.b64decode(encoded) == b"jpeg-bytes"


def test_generate_builds_vision_message_shape_without_a_network_call(tmp_path):
    """Assign adapter._client so generate never looks up OPENAI_API_KEY."""
    adapter = oa.OpenAIAdapter(api_key="unused")
    captured = {}

    class StubCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content='{"ambience":"wind"}'))],
                usage=None,
            )

    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(completions=StubCompletions()))

    img = tmp_path / "frame.jpg"
    img.write_bytes(b"img")
    result = adapter.generate(SPEC, {
        "prompt": "analyze",
        "model": "gpt-5.6-luna",
        "image_urls": [f"file://{img}"],
    })

    assert result.raw_response["output"] == '{"ambience":"wind"}'
    assert result.raw_response["model"] == "gpt-5.6-luna"
    assert captured["model"] == "gpt-5.6-luna"
    content = captured["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "analyze"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_generate_accepts_singular_video_url(tmp_path, monkeypatch):
    """Retired fal/localizer helpers pass video_url; must not drop the media."""
    adapter = oa.OpenAIAdapter(api_key="unused")
    captured = {}

    class StubCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content="ok"))],
                usage=None,
            )

    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(completions=StubCompletions()))
    monkeypatch.setattr(
        oa, "_video_as_image_parts",
        lambda uri: [{"type": "text", "text": f"sampled:{uri}"}])

    adapter.generate(SPEC, {
        "prompt": "p",
        "video_url": "file:///tmp/clip.mp4",
    })
    content = captured["messages"][0]["content"]
    assert any(
        p.get("text") == "sampled:file:///tmp/clip.mp4" for p in content)


@needs_ffmpeg
def test_video_as_image_parts_samples_scaled_frames(tmp_path):
    """The whole reason the OpenAI option exists: video → vision frames."""
    import subprocess

    clip = tmp_path / "clip.mp4"
    # 2s solid color; at 2fps that is 4 frames before the max-frames cap.
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=2",
         "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )

    parts = oa._video_as_image_parts(f"file://{clip}")
    # Alternating [frame at …] text markers and image_url parts.
    texts = [p["text"] for p in parts if p.get("type") == "text"]
    images = [p for p in parts if p.get("type") == "image_url"]
    assert len(texts) == len(images) >= 2
    assert texts[0].startswith("[frame at ")
    assert images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    # Cap is a hard ceiling; a 2s clip is well under it.
    assert len(images) <= oa.VIDEO_MAX_FRAMES


def test_upload_returns_file_uri(tmp_path):
    path = tmp_path / "a.mp4"
    path.write_bytes(b"v")
    assert oa.OpenAIAdapter(api_key="x").upload(str(path)) == f"file://{path}"


def test_missing_api_key_raises_before_sdk_import(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    adapter = oa.OpenAIAdapter()
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        adapter._get_client()
