"""Code-owned provider origins and exact managed-route calling presets."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Mapping

from ai_creation_canvas.acceptance_models import acceptance_model_profiles
from ai_creation_canvas.model_registry import OperationContract, ProviderDefinition
from ai_creation_canvas.model_routing import LogicalModelDefinition, ModelRouteDefinition, validate_route_model


_TRUSTED_PROVIDER_ORIGINS: Mapping[tuple[str, str], str] = MappingProxyType({
    ("chiyun-banana", "chiyun_gemini_images"): "https://chiyun.work",
    ("chiyun-gpt-image2", "chiyun_openai_images"): "https://chiyun.work",
    ("ark", "ark"): "https://ark.cn-beijing.volces.com",
})


@dataclass(frozen=True, slots=True)
class TrustedRoutePreset:
    profile_id: str
    channel_id: str
    provider_id: str
    provider_model_name: str
    adapter_type: str
    family: str
    operation_contracts: tuple[OperationContract, ...]


@lru_cache(maxsize=1)
def trusted_route_presets() -> Mapping[tuple[str, str], TrustedRoutePreset]:
    raw_profiles = acceptance_model_profiles()["profiles"]
    assert isinstance(raw_profiles, dict)
    channels = {
        "banana": (("chiyun", "chiyun-banana"),),
        "gpt_image2": (("chiyun", "chiyun-gpt-image2"),),
        "seedream": (("ark", "ark"),),
        "seedance": (("ark", "ark"),),
    }
    result: dict[tuple[str, str], TrustedRoutePreset] = {}
    for profile_id, configured_channels in channels.items():
        source_id = "gpt-image2" if profile_id == "gpt_image2" else profile_id
        raw = raw_profiles[source_id]
        assert isinstance(raw, dict) and isinstance(raw["contract"], dict)
        contract = OperationContract.from_dict(raw["contract"])
        for channel_id, provider_id in configured_channels:
            result[(profile_id, channel_id)] = TrustedRoutePreset(
                profile_id=profile_id,
                channel_id=channel_id,
                provider_id=provider_id,
                provider_model_name=str(raw["provider_model_name"]),
                adapter_type=str(raw["adapter_type"]),
                family=str(raw["family"]),
                operation_contracts=(contract,),
            )
    return MappingProxyType(result)


def _contracts_equal(left: tuple[OperationContract, ...], right: tuple[OperationContract, ...]) -> bool:
    return [item.to_dict() for item in left] == [item.to_dict() for item in right]


def validate_trusted_route(route: ModelRouteDefinition, model: LogicalModelDefinition | None = None) -> TrustedRoutePreset:
    if not isinstance(route, ModelRouteDefinition):
        raise ValueError("route does not match a trusted preset")
    preset = next((
        item for item in trusted_route_presets().values()
        if route.provider_id == item.provider_id
        and route.provider_model_name == item.provider_model_name
        and route.adapter_type == item.adapter_type
        and route.family == item.family
        and _contracts_equal(route.operation_contracts, item.operation_contracts)
    ), None)
    if preset is None:
        raise ValueError("route does not match a trusted preset")
    if model is not None:
        if not isinstance(model, LogicalModelDefinition) or not _contracts_equal(model.operation_contracts, preset.operation_contracts):
            raise ValueError("logical model does not match a trusted preset")
        validate_route_model(route, model)
    return preset


def provider_protocol_for_definition(provider: ProviderDefinition):
    """Return a protocol only when persisted state exactly matches code-owned trust."""
    if not isinstance(provider, ProviderDefinition) or not provider.enabled:
        return None
    approved = _TRUSTED_PROVIDER_ORIGINS.get((provider.provider_id, provider.adapter_type))
    if approved is None or provider.base_url != approved:
        return None
    from ai_creation_canvas.adapters.factory import ProviderProtocol

    return ProviderProtocol(provider.provider_id, provider.adapter_type, approved)


def provider_has_trusted_origin(provider: ProviderDefinition | None) -> bool:
    return provider_protocol_for_definition(provider) is not None if isinstance(provider, ProviderDefinition) else False
