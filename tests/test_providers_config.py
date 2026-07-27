"""Tests for the providers config layer.

Validates loading, validation errors, and accessor functions against
providers.toml and synthetic bad configs. NO network calls.
"""

import os
import tempfile
import textwrap

import pytest

from foleyflow.providers.config import (
    ConfigError,
    get_analysis_config,
    get_defaults,
    get_model_spec,
    get_routing_config,
    load_config,
)


TOML_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "providers.toml")
)


# -- valid load --

def test_load_config_succeeds():
    config = load_config(TOML_PATH)
    assert config["meta"]["version"] == 1
    assert "models" in config
    assert "routing" in config
    assert "analysis" in config


def test_get_model_spec_cassetteai():
    config = load_config(TOML_PATH)
    spec = get_model_spec("cassetteai/sound-effects-generator", _config=config)
    assert spec.model_id == "cassetteai/sound-effects-generator"
    assert spec.adapter == "fal"
    assert spec.role == "sfx"
    assert spec.cost_per_call_usd == 0.02
    assert spec.duration_min_s == 1.0
    assert spec.duration_max_s == 30.0
    assert spec.prompt_field == "prompt"
    assert spec.duration_field == "duration"
    assert spec.duration_type == "int"


def test_get_model_spec_elevenlabs():
    config = load_config(TOML_PATH)
    spec = get_model_spec("fal-ai/elevenlabs/sound-effects/v2", _config=config)
    assert spec.prompt_field == "text"
    assert spec.duration_field == "duration_seconds"
    assert spec.duration_type == "float"
    assert spec.duration_min_s == 0.5
    assert spec.duration_max_s == 22.0


def test_get_model_spec_mmaudio():
    config = load_config(TOML_PATH)
    spec = get_model_spec("fal-ai/mmaudio-v2/text-to-audio", _config=config)
    assert spec.prompt_field == "prompt"
    assert spec.duration_field == "duration"
    assert spec.duration_type == "float"


# -- unknown model --

def test_unknown_model_raises_keyerror():
    config = load_config(TOML_PATH)
    with pytest.raises(KeyError, match="not-a-real-model"):
        get_model_spec("not-a-real-model", _config=config)


# -- analysis config --

def test_analysis_config():
    """Analysis routes to Google directly; fal's gateway was only a proxy in
    front of the same Gemini model."""
    config = load_config(TOML_PATH)
    analysis = get_analysis_config(_config=config)
    assert analysis["video_model"] == "gemini-2.5-flash"
    assert analysis["vision_model"] == "gemini-2.5-flash"
    assert analysis["default_vlm"] == "gemini-2.5-flash"


def test_every_analysis_model_is_declared_and_routable():
    """A typo in [analysis] would otherwise surface as a KeyError mid-run,
    after the clip has already been uploaded."""
    config = load_config(TOML_PATH)
    analysis = get_analysis_config(_config=config)
    for role in ("video_model", "vision_model", "localization_model", "default_vlm"):
        spec = get_model_spec(analysis[role], _config=config)
        assert spec.adapter, f"{role} -> {analysis[role]} declares no adapter"


# -- routing config --

def test_routing_config_categories_ordered():
    config = load_config(TOML_PATH)
    routing = get_routing_config(_config=config)
    names = [c["name"] for c in routing["categories"]]
    assert names == ["ambience", "footsteps", "door_creak", "impact_strike"]


def test_routing_config_default():
    config = load_config(TOML_PATH)
    routing = get_routing_config(_config=config)
    assert routing["default"]["name"] == "default"
    assert routing["default"]["endpoint"] == "cassetteai/sound-effects-generator"


# -- defaults --

def test_defaults():
    config = load_config(TOML_PATH)
    defaults = get_defaults(_config=config)
    assert defaults["cost_per_call_usd"] == 0.01
    assert defaults["duration_min_s"] == 1.0
    assert defaults["duration_max_s"] == 22.0


# -- validation errors --

def _write_toml(tmp_dir: str, content: str) -> str:
    path = os.path.join(tmp_dir, "bad.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(content))
    return path


def test_missing_required_field_adapter():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_toml(tmp, """\
            [meta]
            version = 1
            [models."bad/model"]
            role = "sfx"
            [[routing.categories]]
            name = "ambience"
            endpoint = "bad/model"
            keywords = ["wind"]
            [routing.default]
            name = "default"
            endpoint = "bad/model"
        """)
        with pytest.raises(ConfigError, match="missing required field 'adapter'"):
            load_config(path)


def test_missing_required_field_role():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_toml(tmp, """\
            [meta]
            version = 1
            [models."bad/model"]
            adapter = "fal"
            [[routing.categories]]
            name = "ambience"
            endpoint = "bad/model"
            keywords = ["wind"]
            [routing.default]
            name = "default"
            endpoint = "bad/model"
        """)
        with pytest.raises(ConfigError, match="missing required field 'role'"):
            load_config(path)


def test_inverted_duration_bounds():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_toml(tmp, """\
            [meta]
            version = 1
            [models."bad/model"]
            adapter = "fal"
            role = "sfx"
            duration_min_s = 30.0
            duration_max_s = 1.0
            [[routing.categories]]
            name = "ambience"
            endpoint = "bad/model"
            keywords = ["wind"]
            [routing.default]
            name = "default"
            endpoint = "bad/model"
        """)
        with pytest.raises(ConfigError, match="inverted duration bounds"):
            load_config(path)


def test_route_referencing_undeclared_model():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_toml(tmp, """\
            [meta]
            version = 1
            [models."good/model"]
            adapter = "fal"
            role = "sfx"
            [[routing.categories]]
            name = "ambience"
            endpoint = "nonexistent/model"
            keywords = ["wind"]
            [routing.default]
            name = "default"
            endpoint = "good/model"
        """)
        with pytest.raises(ConfigError, match="undeclared model"):
            load_config(path)


def test_missing_ambience_category():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_toml(tmp, """\
            [meta]
            version = 1
            [models."good/model"]
            adapter = "fal"
            role = "sfx"
            [[routing.categories]]
            name = "footsteps"
            endpoint = "good/model"
            keywords = ["footstep"]
            [routing.default]
            name = "default"
            endpoint = "good/model"
        """)
        with pytest.raises(ConfigError, match="ambience"):
            load_config(path)
