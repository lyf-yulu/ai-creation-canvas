"""Frozen server-owned model profiles used only by guarded paid acceptance."""

from __future__ import annotations

from copy import deepcopy


_PROMPT = {"port_id": "prompt", "media_type": "text", "min_items": 1, "max_items": 1}
_REFERENCE = {"port_id": "reference_images", "media_type": "image", "min_items": 1, "max_items": 10}
_CHIYUN_PROPERTIES = {
    "size": {"type": "string", "enum": ["auto", "1024x1024", "1024x1536", "1536x1024"], "default": "auto"},
    "output_count": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1},
}


def _chiyun(profile: str, family: str, provider_model_name: str) -> dict[str, object]:
    return {
        "adapter_type": "chiyun_openai_images",
        "family": family,
        "provider_model_name": provider_model_name,
        "contract": {
            "operation": "image.edit",
            "input_ports": [_PROMPT, _REFERENCE],
            "output_media_type": "image",
            "parameter_schema": {"type": "object", "x-aicc-profile": profile, "properties": _CHIYUN_PROPERTIES, "required": ["size", "output_count"], "additionalProperties": False},
            "parameter_mappings": {"size": "size", "output_count": "n"},
        },
    }


_PROFILES: dict[str, object] = {
    "banana": _chiyun("banana", "nano-banana", "gemini-2.5-flash-image"),
    "gpt-image2": _chiyun("gpt_image2", "gpt-image", "gpt-image-2"),
    "seedream": {
        "adapter_type": "ark", "family": "seedream", "provider_model_name": "doubao-seedream-5-0-pro-260628",
        "contract": {
            "operation": "image.edit",
            "input_ports": [_PROMPT, _REFERENCE],
            "output_media_type": "image",
            "parameter_schema": {
                "type": "object", "x-aicc-profile": "seedream",
                "properties": {
                    "size": {"type": "string", "default": "2K", "x-ark-size": {"presets": ["1K", "1.5K", "2K"], "min_pixels": 921600, "max_pixels": 4624220, "min_ratio": 0.0625, "max_ratio": 16}},
                    "watermark": {"type": "boolean", "default": False},
                    "output_format": {"type": "string", "enum": ["png", "jpeg"], "default": "png"},
                    "prompt_optimization": {"type": "string", "enum": ["standard", "fast"], "default": "standard"},
                },
                "additionalProperties": False,
            },
            "parameter_mappings": {"size": "size", "watermark": "watermark", "output_format": "output_format", "prompt_optimization": "optimize_prompt_options.mode"},
        },
    },
    "seedance": {
        "adapter_type": "ark", "family": "seedance", "provider_model_name": "doubao-seedance-2-5-260628",
        "contract": {
            "operation": "video.generate",
            "input_ports": [
                _PROMPT,
                {"port_id": "first_frame", "media_type": "image", "min_items": 0, "max_items": 1},
                {"port_id": "last_frame", "media_type": "image", "min_items": 0, "max_items": 1},
                {"port_id": "reference_images", "media_type": "image", "min_items": 0, "max_items": 9},
                {"port_id": "reference_audio", "media_type": "audio", "min_items": 0, "max_items": 3},
            ],
            "output_media_type": "video",
            "parameter_schema": {
                "type": "object", "x-aicc-profile": "seedance",
                "properties": {
                    "ratio": {"type": "string", "enum": ["adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"], "default": "16:9"},
                    "resolution": {"type": "string", "enum": ["480p", "720p", "1080p", "4k"], "default": "720p"},
                    "duration": {"type": "integer", "minimum": 4, "maximum": 30, "default": 5},
                    "generate_audio": {"type": "boolean", "default": True},
                    "camera_fixed": {"type": "boolean", "default": False},
                    "return_last_frame": {"type": "boolean", "default": False},
                    "output_format": {"type": "string", "enum": ["mp4", "mov"], "default": "mp4"},
                    "watermark": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
            "parameter_mappings": {"ratio": "ratio", "resolution": "resolution", "duration": "duration", "generate_audio": "generate_audio", "camera_fixed": "camera_fixed", "return_last_frame": "return_last_frame", "output_format": "output_format", "watermark": "watermark"},
        },
    },
}


def acceptance_model_profiles() -> dict[str, object]:
    """Return an isolated JSON-compatible copy of the four reviewed profiles."""
    return {"profiles": deepcopy(_PROFILES)}
