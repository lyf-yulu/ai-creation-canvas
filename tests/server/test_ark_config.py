from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_creation_canvas.adapters.ark import load_ark_model_declarations
from ai_creation_canvas.__main__ import create_local_app
from ai_creation_canvas.config import Settings


def test_ark_model_declarations_are_data_only_and_reject_secret_or_url_fields(tmp_path) -> None:
    config = tmp_path / "ark-models.json"
    config.write_text(json.dumps({"models": [{
        "model_id": "image-endpoint", "service_id": "ark-image", "display_name": "图片模型",
        "operations": ["image.generate"],
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
        "operations": ["image.generate"], "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False},
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


def test_ark_declarations_are_not_limited_to_the_local_identity_mode(tmp_path) -> None:
    config = tmp_path / "ark-models.json"
    config.write_text(json.dumps({"models": [{
        "model_id": "image-endpoint", "service_id": "ark-image", "display_name": "图片模型",
        "operations": ["image.generate"], "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    }]}), encoding="utf-8")
    settings = Settings(
        environment="production", port=8991, data_dir=tmp_path / "data", portal_internal_token="deployment-token",
        enable_ark_adapter=True, ark_models_config_path=config, ark_models_config_root=tmp_path,
    )
    assert settings.ark_models_config_path == config
