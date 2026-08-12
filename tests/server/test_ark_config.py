from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_creation_canvas.adapters.ark import load_ark_model_declarations
from ai_creation_canvas.api.jobs import _validate_parameters
from ai_creation_canvas.__main__ import create_local_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.models import ModelSpec


def test_ark_model_declarations_are_data_only_and_reject_secret_or_url_fields(tmp_path) -> None:
    config = tmp_path / "ark-models.json"
    config.write_text(json.dumps({"models": [{
        "model_id": "image-endpoint", "service_id": "ark-image", "display_name": "图片模型",
        "operations": ["image.generate"],
        "input_ports": [{"port_id": "prompt", "media_type": "text", "min_items": 1, "max_items": 1}],
        "parameter_mappings": {"size": "size"},
        "parameter_schema": {"type": "object", "properties": {"size": {"type": "string", "enum": ["1024x1024"], "default": "1024x1024"}}, "additionalProperties": False},
    }]}), encoding="utf-8")
    declarations = load_ark_model_declarations(config, tmp_path)
    assert declarations[0].model_id == "image-endpoint"

    config.write_text(json.dumps({"models": [{
        "model_id": "image-endpoint", "service_id": "ark-image", "display_name": "图片模型", "operations": ["image.generate"], "parameter_schema": {}, "api_key": "not-allowed",
    }]}), encoding="utf-8")
    with pytest.raises(ValueError, match="Ark model configuration is invalid"):
        load_ark_model_declarations(config, tmp_path)


def test_explicit_local_ark_configuration_registers_models_without_exposing_key(tmp_path, monkeypatch) -> None:
    config = tmp_path / "ark-models.json"
    config.write_text(json.dumps({"models": [{
        "model_id": "image-endpoint", "service_id": "ark-image", "display_name": "图片模型",
        "operations": ["image.generate"], "input_ports": [{"port_id": "prompt", "media_type": "text", "min_items": 1, "max_items": 1}], "parameter_mappings": {}, "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    }]}), encoding="utf-8")
    monkeypatch.setenv("ARK_API_KEY", "test-only-secret")
    app, accounts = create_local_app(port=8993, data_dir=tmp_path / "data", static_dir=tmp_path / "dist", bootstrap_if_empty=True, ark_models_config=config)
    assert accounts is not None and accounts.created
    assert app.state.adapter_registry.generation("ark-image").model_ids == ("image-endpoint",)
    assert "test-only-secret" not in repr(app.state.settings)


def test_example_ark_video_declaration_uses_a_supported_five_second_default() -> None:
    config = Path(__file__).parents[2] / "server" / "config" / "ark-models.example.json"
    declarations = {item.service_id: item for item in load_ark_model_declarations(config, config.parent)}
    video = declarations["ark-video"]
    assert video.model_id == "doubao-seedance-2-0-260128"
    assert video.parameter_schema["properties"]["duration"]["default"] == 5
    assert "output_format" not in video.parameter_schema["properties"]
    assert "output_format" not in video.parameter_mappings
    assert {port.port_id: port.max_items for port in video.input_ports}["reference_audio"] == 3


def test_example_seedream_4_declares_only_confirmed_single_image_request_parameters() -> None:
    config = Path(__file__).parents[2] / "server" / "config" / "ark-models.example.json"
    image = {item.service_id: item for item in load_ark_model_declarations(config, config.parent)}["ark-image"]
    assert "count" not in image.parameter_schema["properties"]
    assert "count" not in image.parameter_mappings
    assert "n" not in image.parameter_mappings.values()


def test_frozen_seedream_size_constraint_accepts_presets_and_safe_dimensions() -> None:
    config = Path(__file__).parents[2] / "server" / "config" / "ark-models.example.json"
    declaration = {item.model_id: item for item in load_ark_model_declarations(config, config.parent)}["doubao-seedream-4-0-250828"]
    model = ModelSpec(
        declaration.model_id,
        declaration.service_id,
        declaration.display_name,
        declaration.operations,
        ("text",),
        declaration.parameter_schema,
        None,
        declaration.input_ports,
        declaration.parameter_mappings,
    )

    assert _validate_parameters(model, {"size": "1K"}) is None
    assert _validate_parameters(model, {"size": "1024x1024"}) is None


def test_example_catalog_declares_current_official_ark_model_matrix() -> None:
    config = Path(__file__).parents[2] / "server" / "config" / "ark-models.example.json"
    declarations = {item.model_id: item for item in load_ark_model_declarations(config, config.parent)}
    assert set(declarations) == {
        "doubao-seedream-5-0-pro-260628",
        "doubao-seedream-5-0-260128",
        "doubao-seedream-4-5-251128",
        "doubao-seedream-4-0-250828",
        "doubao-seedance-2-5-260628",
        "doubao-seedance-2-0-260128",
        "doubao-seedance-2-0-fast-260128",
        "doubao-seedance-2-0-mini-260615",
    }

    pro = declarations["doubao-seedream-5-0-pro-260628"]
    lite = declarations["doubao-seedream-5-0-260128"]
    old = declarations["doubao-seedream-4-0-250828"]
    assert {port.port_id: port.max_items for port in pro.input_ports}["reference_images"] == 10
    assert {port.port_id: port.max_items for port in lite.input_ports}["reference_images"] == 14
    assert "sequence_mode" not in pro.parameter_schema["properties"]
    assert lite.parameter_mappings["max_images"] == "sequential_image_generation_options.max_images"
    assert old.parameter_schema["properties"]["prompt_optimization"]["enum"] == ["standard", "fast"]

    seedance_25 = declarations["doubao-seedance-2-5-260628"]
    seedance_20 = declarations["doubao-seedance-2-0-260128"]
    seedance_fast = declarations["doubao-seedance-2-0-fast-260128"]
    assert seedance_25.parameter_schema["properties"]["duration"]["maximum"] == 30
    assert seedance_25.parameter_schema["properties"]["resolution"]["enum"] == ["480p", "720p"]
    assert seedance_20.parameter_schema["properties"]["resolution"]["enum"] == ["480p", "720p", "1080p", "4k"]
    assert seedance_fast.parameter_schema["properties"]["resolution"]["enum"] == ["480p", "720p"]


def test_loader_rejects_malformed_ark_size_constraints(tmp_path) -> None:
    base = {
        "model_id": "image-endpoint", "service_id": "ark-image", "display_name": "图片模型",
        "operations": ["image.generate"],
        "input_ports": [{"port_id": "prompt", "media_type": "text", "min_items": 1, "max_items": 1}],
        "parameter_mappings": {"size": "size"},
        "parameter_schema": {
            "type": "object",
            "properties": {"size": {"type": "string", "x-ark-size": {"presets": ["2K"], "min_pixels": 10, "max_pixels": 1, "min_ratio": 1, "max_ratio": 16}}},
            "additionalProperties": False,
        },
    }
    config = tmp_path / "ark-models.json"
    config.write_text(json.dumps({"models": [base]}), encoding="utf-8")
    with pytest.raises(ValueError, match="Ark model configuration is invalid"):
        load_ark_model_declarations(config, tmp_path)


def test_ark_declarations_are_not_limited_to_the_local_identity_mode(tmp_path) -> None:
    config = tmp_path / "ark-models.json"
    config.write_text(json.dumps({"models": [{
        "model_id": "image-endpoint", "service_id": "ark-image", "display_name": "图片模型",
        "operations": ["image.generate"], "input_ports": [{"port_id": "prompt", "media_type": "text", "min_items": 1, "max_items": 1}], "parameter_mappings": {}, "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    }]}), encoding="utf-8")
    settings = Settings(
        environment="production", port=8991, data_dir=tmp_path / "data", portal_internal_token="deployment-token",
        enable_ark_adapter=True, ark_models_config_path=config, ark_models_config_root=tmp_path,
    )
    assert settings.ark_models_config_path == config
