"""Allowlisted construction of adapters from governed definitions."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
from types import MappingProxyType
from typing import Callable, Mapping, Protocol
from urllib.parse import urlsplit

import httpx

from ai_creation_canvas.adapters.ark import ArkGenerationAdapter, ArkModelDeclaration
from ai_creation_canvas.adapters.chiyun import ChiyunGenerationAdapter
from ai_creation_canvas.coordination import CredentialLease
from ai_creation_canvas.domain.models import ModelOperation
from ai_creation_canvas.model_registry import GovernedModelDefinition, ModelModality, ProviderDefinition
from ai_creation_canvas.model_routing import ModelRouteDefinition


_ARK_URL = "https://ark.cn-beijing.volces.com"
_PROTOCOL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_ARK_IMAGE_TARGETS = frozenset({
    "size", "quality", "n", "strength", "watermark", "output_format",
    "optimize_prompt_options.mode", "sequential_image_generation",
    "sequential_image_generation_options.max_images",
})
_ARK_VIDEO_TARGETS = frozenset({
    "ratio", "duration", "resolution", "generate_audio", "camera_fixed",
    "return_last_frame", "watermark",
})


@dataclass(frozen=True, slots=True)
class ProviderProtocol:
    """Server-code-owned provider origin and protocol selection."""

    provider_id: str
    adapter_type: str
    base_url: str

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or _PROTOCOL_ID.fullmatch(self.provider_id) is None:
            raise ValueError("provider protocol ID is invalid")
        if self.adapter_type not in {"ark", "chiyun_openai_images"}:
            raise ValueError("provider protocol adapter is unsupported")
        parsed = urlsplit(self.base_url)
        try:
            port = parsed.port
        except ValueError:
            raise ValueError("provider protocol origin is invalid") from None
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or port not in {None, 443}
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("provider protocol origin is invalid")
        origin = f"https://{parsed.netloc}"
        if self.adapter_type == "ark" and origin != _ARK_URL:
            raise ValueError("Ark protocol origin is fixed")
        object.__setattr__(self, "base_url", origin)


class CredentialResolver(Protocol):
    def resolve(self, reference: str) -> str: ...


class MappingCredentialResolver:
    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    def resolve(self, reference: str) -> str:
        value = self._values.get(reference)
        if not isinstance(value, str) or len(value.strip()) < 8:
            raise ValueError("credential reference is unavailable")
        return value.strip()


class EnvironmentCredentialResolver:
    """Resolve deployment-owned references without ever persisting the secret."""
    def resolve(self, reference: str) -> str:
        if not isinstance(reference, str) or not reference:
            raise ValueError("credential reference is unavailable")
        name = "AICC_CREDENTIAL_" + re.sub(r"[^A-Za-z0-9]", "_", reference).upper()
        value = os.environ.get(name)
        if not isinstance(value, str) or len(value.strip()) < 8:
            raise ValueError("credential reference is unavailable")
        return value.strip()


class AdapterFactory:
    def __init__(self, *, data_dir: Path | str, credential_resolver: CredentialResolver, asset_loader: Callable[[str], tuple[bytes, str]], transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._data_dir, self._credential_resolver, self._asset_loader, self._transport = Path(data_dir), credential_resolver, asset_loader, transport
        self._cache: dict[tuple[str, int, tuple[tuple[str, int], ...]], object] = {}

    def credential_available(self, provider: ProviderDefinition) -> bool:
        try:
            self._credential_resolver.resolve(provider.credential_ref)
        except ValueError:
            return False
        return True

    def build(self, provider: ProviderDefinition, models: tuple[GovernedModelDefinition, ...]):
        if not models or any(model.provider_id != provider.provider_id for model in models):
            raise ValueError("provider model binding is invalid")
        key = (provider.provider_id, provider.revision, tuple(sorted((model.model_id, model.revision) for model in models)))
        if key in self._cache:
            return self._cache[key]
        if provider.adapter_type != "chiyun_openai_images":
            raise ValueError("adapter type is not supported by the governed factory")
        adapter = ChiyunGenerationAdapter(
            provider=provider, models=models, api_key=self._credential_resolver.resolve(provider.credential_ref),
            data_dir=self._data_dir, asset_loader=self._asset_loader, transport=self._transport,
        )
        self._cache[key] = adapter
        return adapter


class RouteAdapterFactory:
    """Construct one authenticated adapter for one immutable route lease."""

    def __init__(
        self,
        *,
        data_dir: Path | str,
        asset_loader: Callable[[str], tuple[bytes, str]],
        provider_protocols: Mapping[str, ProviderProtocol],
        transport: httpx.AsyncBaseTransport | None = None,
        transport_factory: Callable[[ProviderProtocol], httpx.AsyncBaseTransport | None] | None = None,
    ) -> None:
        if not callable(asset_loader) or (transport is not None and transport_factory is not None):
            raise ValueError("route adapter dependencies are invalid")
        protocols = dict(provider_protocols)
        if (
            not protocols
            or any(not isinstance(key, str) or not isinstance(value, ProviderProtocol) or key != value.provider_id for key, value in protocols.items())
        ):
            raise ValueError("provider protocol registry is invalid")
        self._data_dir = Path(data_dir)
        self._asset_loader = asset_loader
        self._protocols = MappingProxyType(protocols)
        self._transport = transport
        self._transport_factory = transport_factory

    def __repr__(self) -> str:
        return f"RouteAdapterFactory(provider_count={len(self._protocols)})"

    def build(self, route: ModelRouteDefinition, lease: CredentialLease):
        if not isinstance(route, ModelRouteDefinition) or not isinstance(lease, CredentialLease):
            raise ValueError("route and credential lease are required")
        if (
            not route.enabled
            or route.archived_at is not None
            or not route.provider_model_name
            or not route.credential_pool_ref
            or route.route_id != lease.route_id
            or route.credential_pool_ref != lease.pool_id
            or not isinstance(lease.secret, str)
            or len(lease.secret.strip()) < 8
        ):
            raise ValueError("route runtime is unavailable")
        protocol = self._protocols.get(route.provider_id)
        if protocol is None or protocol.adapter_type != route.adapter_type:
            raise ValueError("route provider protocol is unavailable")
        if len(route.operation_contracts) != 1:
            raise ValueError("route must bind exactly one operation template")
        contract = route.operation_contracts[0]
        transport = self._transport_factory(protocol) if self._transport_factory is not None else self._transport
        if route.adapter_type == "ark":
            targets = set(contract.parameter_mappings.values())
            ports = {port.port_id: port for port in contract.input_ports}
            if not _is_prompt_port(ports.get("prompt")):
                raise ValueError("Ark prompt contract is unsupported")
            if contract.operation in {ModelOperation.IMAGE_GENERATE, ModelOperation.IMAGE_EDIT}:
                if contract.output_media_type != "image" or not targets <= _ARK_IMAGE_TARGETS:
                    raise ValueError("Ark image route contract is unsupported")
                references = ports.get("reference_images")
                if contract.operation is ModelOperation.IMAGE_GENERATE and set(ports) != {"prompt"}:
                    raise ValueError("Ark image generation inputs are unsupported")
                if contract.operation is ModelOperation.IMAGE_EDIT and (
                    set(ports) != {"prompt", "reference_images"}
                    or references is None
                    or references.media_type != "image"
                    or references.min_items < 1
                    or references.max_items > 14
                ):
                    raise ValueError("Ark image edit inputs are unsupported")
            elif contract.operation is ModelOperation.VIDEO_GENERATE:
                if contract.output_media_type != "video" or not targets <= _ARK_VIDEO_TARGETS:
                    raise ValueError("Ark video route contract is unsupported")
                video_limits = {
                    "first_frame": ("image", 1),
                    "last_frame": ("image", 1),
                    "reference_images": ("image", 9),
                    "reference_audio": ("audio", 3),
                }
                if set(ports) - ({"prompt"} | set(video_limits)):
                    raise ValueError("Ark video inputs are unsupported")
                for name, port in ports.items():
                    if name == "prompt":
                        continue
                    media_type, maximum = video_limits[name]
                    if port.media_type != media_type or port.min_items != 0 or port.max_items > maximum:
                        raise ValueError("Ark video inputs are unsupported")
            else:
                raise ValueError("Ark route operation is unsupported")
            declaration = ArkModelDeclaration(
                route.model_id,
                route.route_id,
                route.model_id,
                (contract.operation,),
                contract.parameter_schema,
                contract.input_ports,
                contract.parameter_mappings,
                provider_model_name=route.provider_model_name,
            )
            return ArkGenerationAdapter(
                api_key=lease.secret.strip(),
                data_dir=self._data_dir,
                models=(declaration,),
                transport=transport,
                asset_loader=self._asset_loader,
            )
        if route.adapter_type == "chiyun_openai_images":
            ports = {port.port_id: port for port in contract.input_ports}
            references = ports.get("reference_images")
            if (
                contract.operation is not ModelOperation.IMAGE_EDIT
                or contract.output_media_type != "image"
                or set(ports) != {"prompt", "reference_images"}
                or not _is_prompt_port(ports.get("prompt"))
                or references is None
                or references.media_type != "image"
                or references.min_items < 1
                or references.max_items > 10
                or dict(contract.parameter_mappings) != {"size": "size", "output_count": "n"}
            ):
                raise ValueError("Chiyun route contract is unsupported")
            service_id = route.route_id
            provider = ProviderDefinition(
                service_id,
                "Managed Chiyun route",
                route.adapter_type,
                protocol.base_url,
                "lease",
            )
            model = GovernedModelDefinition(
                route.model_id,
                service_id,
                route.provider_model_name,
                route.model_id,
                "Managed route",
                ModelModality.IMAGE,
                route.operation_contracts,
            )
            return ChiyunGenerationAdapter(
                provider=provider,
                models=(model,),
                api_key=lease.secret.strip(),
                data_dir=self._data_dir,
                asset_loader=self._asset_loader,
                transport=transport,
            )
        raise ValueError("route adapter is not allowlisted")


def _is_prompt_port(port: object) -> bool:
    return (
        getattr(port, "port_id", None) == "prompt"
        and getattr(port, "media_type", None) == "text"
        and getattr(port, "min_items", None) == 1
        and getattr(port, "max_items", None) == 1
    )
