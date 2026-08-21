"""Frozen server-owned model profiles used only by guarded paid acceptance."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from ai_creation_canvas.adapters.ark import ArkModelDeclaration, load_ark_model_declarations


_PROMPT = {"port_id": "prompt", "media_type": "text", "min_items": 1, "max_items": 1}
_REFERENCE = {"port_id": "reference_images", "media_type": "image", "min_items": 1, "max_items": 10}
_CHIYUN_OPENAI_PROPERTIES = {
    "ratio": {"type": "string", "enum": ["auto", "1:1", "3:2", "2:3", "16:9", "9:16"], "default": "auto"},
    "output_count": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1},
}


def _chiyun_openai(profile: str, family: str, provider_model_name: str) -> dict[str, object]:
    return {
        "adapter_type": "chiyun_openai_images",
        "family": family,
        "provider_model_name": provider_model_name,
        "contract": {
            "operation": "image.edit",
            "input_ports": [_PROMPT, _REFERENCE],
            "output_media_type": "image",
            "parameter_schema": {"type": "object", "x-aicc-profile": profile, "properties": _CHIYUN_OPENAI_PROPERTIES, "required": ["ratio", "output_count"], "additionalProperties": False},
            "parameter_mappings": {"ratio": "ratio", "output_count": "n"},
        },
    }


def _chiyun_gemini() -> dict[str, object]:
    return {
        "adapter_type": "chiyun_gemini_images",
        "family": "nano-banana",
        "provider_model_name": "banana2-ssvip",
        "contract": {
            "operation": "image.edit",
            "input_ports": [_PROMPT, _REFERENCE],
            "output_media_type": "image",
            "parameter_schema": {
                "type": "object",
                "x-aicc-profile": "banana",
                "properties": {
                    "aspect_ratio": {"type": "string", "enum": ["1:1", "16:9", "9:16", "4:3", "3:4"], "default": "1:1"},
                    "image_size": {"type": "string", "enum": ["1K", "2K", "4K"], "default": "2K"},
                },
                "required": ["aspect_ratio", "image_size"],
                "additionalProperties": False,
            },
            "parameter_mappings": {
                "aspect_ratio": "aspectRatio",
                "image_size": "imageSize",
            },
        },
    }


_CHIYUN_PROFILES: dict[str, object] = {
    "banana": _chiyun_gemini(),
    "gpt-image2": _chiyun_openai("gpt_image2", "gpt-image", "gpt-image-2"),
}


def _ark_profile(declaration: ArkModelDeclaration, *, operation: str, family: str) -> dict[str, object]:
    if operation not in declaration.operations:
        raise ValueError("acceptance Ark operation is not formally declared")
    ports = [
        {
            "port_id": port.port_id,
            "media_type": str(port.media_type),
            "min_items": port.min_items,
            "max_items": port.max_items,
        }
        for port in declaration.input_ports
    ]
    if operation == "image.edit":
        references = next((port for port in ports if port["port_id"] == "reference_images"), None)
        if references is None:
            raise ValueError("acceptance image edit reference input is unavailable")
        references["min_items"] = max(1, int(references["min_items"]))
    return {
        "adapter_type": "ark",
        "family": family,
        "provider_model_name": declaration.model_id,
        "contract": {
            "operation": operation,
            "input_ports": ports,
            "output_media_type": "image" if operation == "image.edit" else "video",
            "parameter_schema": deepcopy(declaration.parameter_schema),
            "parameter_mappings": deepcopy(declaration.parameter_mappings),
        },
    }


def acceptance_model_profiles() -> dict[str, object]:
    """Build acceptance profiles from trusted templates and the formal Ark config."""
    config = Path(__file__).resolve().parents[1] / "config" / "ark-models.example.json"
    ark = {item.model_id: item for item in load_ark_model_declarations(config, config.parent)}
    profiles = deepcopy(_CHIYUN_PROFILES)
    profiles["seedream"] = _ark_profile(
        ark["doubao-seedream-5-0-pro-260628"], operation="image.edit", family="seedream",
    )
    profiles["seedance"] = _ark_profile(
        ark["doubao-seedance-2-5-260628"], operation="video.generate", family="seedance",
    )
    return {"profiles": profiles}
