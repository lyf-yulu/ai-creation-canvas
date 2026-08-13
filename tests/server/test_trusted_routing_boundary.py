from __future__ import annotations

from dataclasses import replace

import pytest

from ai_creation_canvas.model_registry import ProviderDefinition
from ai_creation_canvas.model_routing import LogicalModelDefinition, ModelRouteDefinition
from ai_creation_canvas.credential_pools import CredentialKey, CredentialPool
from ai_creation_canvas.routing import RouteSelector
from ai_creation_canvas.trusted_routing import (
    provider_protocol_for_definition,
    trusted_route_presets,
    validate_trusted_route,
)


def _route(profile_id: str, channel_id: str) -> ModelRouteDefinition:
    preset = trusted_route_presets()[(profile_id, channel_id)]
    return ModelRouteDefinition(
        route_id=f"{profile_id}-{channel_id}",
        model_id=profile_id.replace("_", "-"),
        provider_id=preset.provider_id,
        provider_model_name=preset.provider_model_name,
        adapter_type=preset.adapter_type,
        credential_pool_ref=f"{profile_id}-{channel_id}-pool",
        family=preset.family,
        operation_contracts=preset.operation_contracts,
        priority=10,
        max_concurrency=2,
        enabled=False,
    )


def _model(route: ModelRouteDefinition) -> LogicalModelDefinition:
    return LogicalModelDefinition(
        model_id=route.model_id,
        display_name=route.model_id,
        introduction="Trusted preset model.",
        modality=route.operation_contracts[0].output_media_type,
        operation_contracts=route.operation_contracts,
    )


def test_four_profiles_have_exact_code_owned_positive_presets() -> None:
    presets = trusted_route_presets()
    assert {profile for profile, _channel in presets} == {"banana", "gpt_image2", "seedream", "seedance"}
    assert set(presets) == {
        ("banana", "chiyun"),
        ("gpt_image2", "chiyun"), ("seedream", "ark"), ("seedance", "ark"),
    }
    for profile_id, channel_id in (("banana", "chiyun"), ("gpt_image2", "chiyun"), ("seedream", "ark"), ("seedance", "ark")):
        route = _route(profile_id, channel_id)
        assert validate_trusted_route(route, _model(route)).profile_id == profile_id


def test_chiyun_banana_and_gpt_image2_use_separate_protocols_and_provider_ids() -> None:
    presets = trusted_route_presets()
    banana = presets[("banana", "chiyun")]
    gpt = presets[("gpt_image2", "chiyun")]

    assert banana.provider_id == "chiyun-banana"
    assert banana.adapter_type == "chiyun_gemini_images"
    assert banana.family == "nano-banana"
    assert gpt.provider_id == "chiyun-gpt-image2"
    assert gpt.adapter_type == "chiyun_openai_images"
    assert gpt.family == "gpt-image"
    assert banana.operation_contracts != gpt.operation_contracts


def test_each_chiyun_protocol_accepts_only_its_fixed_origin_and_adapter() -> None:
    banana = ProviderDefinition(
        "chiyun-banana", "Chiyun Banana", "chiyun_gemini_images",
        "https://chiyun.work", "banana-key",
    )
    gpt = ProviderDefinition(
        "chiyun-gpt-image2", "Chiyun GPT", "chiyun_openai_images",
        "https://chiyun.work", "gpt-key",
    )
    assert provider_protocol_for_definition(banana).adapter_type == "chiyun_gemini_images"
    assert provider_protocol_for_definition(gpt).adapter_type == "chiyun_openai_images"
    assert provider_protocol_for_definition(replace(banana, adapter_type="chiyun_openai_images")) is None
    assert provider_protocol_for_definition(replace(gpt, adapter_type="chiyun_gemini_images")) is None
    assert provider_protocol_for_definition(replace(banana, base_url="https://other.example")) is None


def test_every_internal_route_field_must_match_one_exact_preset() -> None:
    route = _route("banana", "chiyun")
    model = _model(route)
    contract = route.operation_contracts[0]
    body = contract.to_dict()
    body["parameter_mappings"] = {"aspect_ratio": "ratio", "image_size": "imageSize"}
    from ai_creation_canvas.model_registry import OperationContract
    tampered_contract = OperationContract.from_dict(body)
    tampered = (
        replace(route, provider_id="unknown"),
        replace(route, provider_model_name="gemini-2.5-flash-image-preview"),
        replace(route, adapter_type="ark"),
        replace(route, family="gpt-image"),
        replace(route, operation_contracts=(tampered_contract,)),
    )
    for candidate in tampered:
        with pytest.raises(ValueError, match="trusted preset"):
            validate_trusted_route(candidate, model)


def test_historical_nonpreset_route_is_excluded_by_trusted_selector() -> None:
    route = _route("banana", "chiyun")
    model = _model(route)
    historical = replace(route, enabled=True, provider_model_name="historical-model")
    selector = RouteSelector(trusted_routes_only=True)
    pool = CredentialPool(
        historical.credential_pool_ref, historical.provider_id, "test", (historical.family,),
        (CredentialKey("test-key", "test-only-secret", 1),), "a" * 64,
    )
    assert selector.candidates(model, "image.edit", {"size": "auto", "output_count": 1}, {"prompt": ("text",), "reference_images": ("image",)}, (historical,), {pool.pool_id: pool}) == ()


def test_only_exact_code_owned_provider_origin_can_become_a_protocol() -> None:
    ark = ProviderDefinition("ark", "Ark", "ark", "https://ark.cn-beijing.volces.com", "ark-key")
    assert provider_protocol_for_definition(ark).provider_id == "ark"
    for provider in (
        ProviderDefinition("ark", "Ark", "ark", "https://evil.example", "ark-key"),
        ProviderDefinition("chiyun", "Chiyun", "chiyun_openai_images", "https://evil.example", "chiyun-key"),
        ProviderDefinition("t8star", "T8", "chiyun_openai_images", "https://evil.example", "t8-key"),
    ):
        assert provider_protocol_for_definition(provider) is None
