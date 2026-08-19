"""Allowlisted construction of adapters from governed definitions."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import os
import re
from types import MappingProxyType
from typing import Callable, Mapping, Protocol
from urllib.parse import urlsplit

import httpx

from ai_creation_canvas.adapters.ark import ArkGenerationAdapter, ArkModelDeclaration
from ai_creation_canvas.adapters.chiyun import ChiyunGenerationAdapter
from ai_creation_canvas.adapters.chiyun_gemini import ChiyunGeminiGenerationAdapter
from ai_creation_canvas.coordination import CredentialLease
from ai_creation_canvas.domain.models import ModelOperation
from ai_creation_canvas.model_registry import GovernedModelDefinition, ModelModality, ProviderDefinition
from ai_creation_canvas.model_routing import ModelRouteDefinition


_ARK_URL = "https://ark.cn-beijing.volces.com"
_CODE_OWNED_PROTOCOLS = frozenset({
    ("ark", "ark", _ARK_URL),
    ("chiyun-banana", "chiyun_gemini_images", "https://chiyun.work"),
    ("chiyun-gpt-image2", "chiyun_openai_images", "https://chiyun.work"),
})
_PROTOCOL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_ARK_IMAGE_TARGETS = frozenset({
    "size", "ratio", "quality", "n", "strength", "watermark", "output_format",
    "optimize_prompt_options.mode", "sequential_image_generation",
    "sequential_image_generation_options.max_images",
})
_ARK_VIDEO_TARGETS = frozenset({
    "ratio", "duration", "resolution", "generate_audio", "camera_fixed",
    "return_last_frame", "output_format", "watermark",
})
_RATIO_VALUES = ("adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16")
_ARK_IMAGE_RATIO_VALUES = ("1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9")
_ARK_SIZE_RULE = {
    "type": "string",
    "default": "2K",
    "x-ark-size": {
        "presets": ["1K", "1.5K", "2K", "3K", "4K"],
        "min_pixels": 921_600,
        "max_pixels": 16_777_216,
        "min_ratio": 0.0625,
        "max_ratio": 16,
    },
}
_ARK_IMAGE_RATIO_RULE = {
    "type": "string",
    "enum": list(_ARK_IMAGE_RATIO_VALUES),
    "default": "1:1",
}
_ARK_IMAGE_PARAMETERS: Mapping[str, tuple[str, Mapping[str, object]]] = MappingProxyType({
    "size": ("size", _ARK_SIZE_RULE),
    "ratio": ("ratio", _ARK_IMAGE_RATIO_RULE),
    "output_count": ("n", {"type": "integer", "minimum": 1, "maximum": 15, "default": 1}),
    "watermark": ("watermark", {"type": "boolean", "default": False}),
    "output_format": ("output_format", {"type": "string", "enum": ["png", "jpeg"], "default": "png"}),
    "prompt_optimization": ("optimize_prompt_options.mode", {"type": "string", "enum": ["standard", "fast"], "default": "standard"}),
    "sequence_mode": ("sequential_image_generation", {"type": "string", "enum": ["disabled", "auto"], "default": "disabled"}),
    "max_images": ("sequential_image_generation_options.max_images", {"type": "integer", "minimum": 1, "maximum": 15, "default": 4}),
})
_ARK_VIDEO_PARAMETERS: Mapping[str, tuple[str, Mapping[str, object]]] = MappingProxyType({
    "ratio": ("ratio", {"type": "string", "enum": list(_RATIO_VALUES), "default": "16:9"}),
    "resolution": ("resolution", {"type": "string", "enum": ["480p", "720p", "1080p", "4k"], "default": "720p"}),
    "duration": ("duration", {"type": "integer", "minimum": 4, "maximum": 30, "default": 5}),
    "generate_audio": ("generate_audio", {"type": "boolean", "default": True}),
    "camera_fixed": ("camera_fixed", {"type": "boolean", "default": False}),
    "return_last_frame": ("return_last_frame", {"type": "boolean", "default": False}),
    "output_format": ("output_format", {"type": "string", "enum": ["mp4", "mov"], "default": "mp4"}),
    "watermark": ("watermark", {"type": "boolean", "default": False}),
})
_CHIYUN_PARAMETERS: Mapping[str, tuple[str, Mapping[str, object]]] = MappingProxyType({
    "size": ("size", {"type": "string", "enum": ["auto", "1024x1024", "1024x1536", "1536x1024"], "default": "auto"}),
    "output_count": ("n", {"type": "integer", "minimum": 1, "maximum": 4, "default": 1}),
})
_CHIYUN_GEMINI_PARAMETERS: Mapping[str, tuple[str, Mapping[str, object]]] = MappingProxyType({
    "aspect_ratio": ("aspectRatio", {"type": "string", "enum": ["1:1", "16:9", "9:16", "4:3", "3:4"], "default": "1:1"}),
    "image_size": ("imageSize", {"type": "string", "enum": ["1K", "2K", "4K"], "default": "2K"}),
})
_RULE_KEYS = frozenset({"type", "enum", "minimum", "maximum", "default", "x-ark-size", "title", "description", "x-ui-visible-when"})


@dataclass(frozen=True, slots=True)
class ProviderProtocol:
    """Server-code-owned provider origin and protocol selection."""

    provider_id: str
    adapter_type: str
    base_url: str
    _readonly_deployment_approved: bool = False

    @classmethod
    def from_readonly_deployment(
        cls, provider_id: str, adapter_type: str, base_url: str, *, approved_origin: str,
    ) -> "ProviderProtocol":
        """Construct from a deployment-owned declaration, never from registry state."""
        if base_url != approved_origin:
            raise ValueError("provider protocol does not match its approved origin")
        return cls(provider_id, adapter_type, base_url, True)

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or _PROTOCOL_ID.fullmatch(self.provider_id) is None:
            raise ValueError("provider protocol ID is invalid")
        if self.adapter_type not in {"ark", "chiyun_gemini_images", "chiyun_openai_images"}:
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
        if not self._readonly_deployment_approved and (self.provider_id, self.adapter_type, origin) not in _CODE_OWNED_PROTOCOLS:
            raise ValueError("provider protocol origin is not code-owned")
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
    def __init__(self, *, data_dir: Path | str, credential_resolver: CredentialResolver, asset_loader: Callable[[str], tuple[bytes, str]], transport: httpx.AsyncBaseTransport | None = None, trusted_provider_origins: Mapping[tuple[str, str], str] | None = None) -> None:
        self._data_dir, self._credential_resolver, self._asset_loader, self._transport = Path(data_dir), credential_resolver, asset_loader, transport
        self._cache: dict[tuple[str, int, tuple[tuple[str, int], ...]], object] = {}
        self._trusted_provider_origins = MappingProxyType(dict(trusted_provider_origins or {}))

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
        from ai_creation_canvas.trusted_routing import provider_has_trusted_origin
        deployment_origin = self._trusted_provider_origins.get((provider.provider_id, provider.adapter_type))
        if not provider_has_trusted_origin(provider) and provider.base_url != deployment_origin:
            raise ValueError("provider origin is not code-owned")
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
        trusted_route_validator: Callable[[ModelRouteDefinition], None] | None = None,
    ) -> None:
        if not callable(asset_loader) or (transport is not None and transport_factory is not None):
            raise ValueError("route adapter dependencies are invalid")
        protocols = dict(provider_protocols)
        if any(not isinstance(key, str) or not isinstance(value, ProviderProtocol) or key != value.provider_id for key, value in protocols.items()):
            raise ValueError("provider protocol registry is invalid")
        self._data_dir = Path(data_dir)
        self._asset_loader = asset_loader
        self._protocols = MappingProxyType(protocols)
        self._transport = transport
        self._transport_factory = transport_factory
        if trusted_route_validator is None:
            from ai_creation_canvas.trusted_routing import validate_trusted_route
            trusted_route_validator = validate_trusted_route
        if not callable(trusted_route_validator):
            raise ValueError("trusted route validator is unavailable")
        self._trusted_route_validator = trusted_route_validator

    def __repr__(self) -> str:
        return f"RouteAdapterFactory(provider_count={len(self._protocols)})"

    def protocol_available(self, provider: ProviderDefinition) -> bool:
        protocol = self._protocols.get(provider.provider_id) if isinstance(provider, ProviderDefinition) else None
        return bool(
            protocol is not None
            and provider.enabled
            and provider.adapter_type == protocol.adapter_type
            and provider.base_url == protocol.base_url
        )

    def validate_route(self, route: ModelRouteDefinition) -> None:
        """Validate against code-owned templates without reading a deployed credential."""
        if (
            not isinstance(route, ModelRouteDefinition)
            or route.archived_at is not None
            or not route.provider_model_name
            or not route.credential_pool_ref
        ):
            raise ValueError("route runtime is unavailable")
        self._trusted_route_validator(route)
        protocol = self._protocols.get(route.provider_id)
        if protocol is None or protocol.adapter_type != route.adapter_type:
            raise ValueError("route provider protocol is unavailable")
        if len(route.operation_contracts) != 1:
            raise ValueError("route must bind exactly one operation template")
        contract = route.operation_contracts[0]
        _validate_parameter_contract(route.adapter_type, contract.operation, contract.parameter_schema, contract.parameter_mappings, route.family)
        ports = {port.port_id: port for port in contract.input_ports}
        if route.adapter_type == "ark":
            targets = set(contract.parameter_mappings.values())
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
                return
            if contract.operation is ModelOperation.VIDEO_GENERATE:
                if contract.output_media_type != "video" or not targets <= _ARK_VIDEO_TARGETS:
                    raise ValueError("Ark video route contract is unsupported")
                limits = {
                    "first_frame": ("image", 1),
                    "last_frame": ("image", 1),
                    "reference_images": ("image", 30),
                    "reference_audio": ("audio", 10),
                }
                if set(ports) - ({"prompt"} | set(limits)):
                    raise ValueError("Ark video inputs are unsupported")
                for name, port in ports.items():
                    if name == "prompt":
                        continue
                    media_type, maximum = limits[name]
                    if port.media_type != media_type or port.min_items != 0 or port.max_items > maximum:
                        raise ValueError("Ark video inputs are unsupported")
                return
            raise ValueError("Ark route operation is unsupported")
        references = ports.get("reference_images")
        if (
            route.adapter_type == "chiyun_gemini_images"
            and contract.operation is ModelOperation.IMAGE_EDIT
            and contract.output_media_type == "image"
            and set(ports) == {"prompt", "reference_images"}
            and _is_prompt_port(ports.get("prompt"))
            and references is not None
            and references.media_type == "image"
            and references.min_items >= 1
            and references.max_items <= 10
            and dict(contract.parameter_mappings) == {"aspect_ratio": "aspectRatio", "image_size": "imageSize"}
        ):
            return
        if (
            route.adapter_type != "chiyun_openai_images"
            or contract.operation is not ModelOperation.IMAGE_EDIT
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
        self.validate_route(route)
        protocol = self._protocols[route.provider_id]
        contract = route.operation_contracts[0]
        transport = self._transport_factory(protocol) if self._transport_factory is not None else self._transport
        if route.adapter_type == "ark":
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
        if route.adapter_type == "chiyun_gemini_images":
            service_id = route.route_id
            provider = ProviderDefinition(service_id, "Managed Chiyun Gemini route", route.adapter_type, protocol.base_url, "lease")
            model = GovernedModelDefinition(
                route.model_id, service_id, route.provider_model_name, route.model_id, "Managed route",
                ModelModality.IMAGE, route.operation_contracts,
            )
            return ChiyunGeminiGenerationAdapter(
                provider=provider, models=(model,), api_key=lease.secret.strip(), data_dir=self._data_dir,
                asset_loader=self._asset_loader, transport=transport,
            )
        raise ValueError("route adapter is not allowlisted")

    def build_result_reader(self, route: ModelRouteDefinition):
        """Build a secret-free reader for files already materialized locally."""
        if not isinstance(route, ModelRouteDefinition) or route.adapter_type not in {"ark", "chiyun_gemini_images", "chiyun_openai_images"}:
            raise ValueError("managed result reader is unavailable")
        return _LocalRouteResultReader(self._data_dir, route.adapter_type)


class _LocalRouteResultReader:
    requires_portal_cookie = False

    def __init__(self, data_dir: Path, adapter_type: str) -> None:
        self._data_dir, self._adapter_type = data_dir, adapter_type

    def __repr__(self) -> str:
        return f"LocalRouteResultReader(adapter_type={self._adapter_type!r})"

    async def open_result(self, context, result_id: str, *, cookie_header: str, range_header: str | None = None, head: bool = False):
        del context, cookie_header
        if self._adapter_type == "chiyun_openai_images":
            from ai_creation_canvas.adapters.chiyun import _FileStream, _RESULT_ID, _MAX_RESULT, _detect_mime, _empty_stream, _range
            path = self._data_dir / "chiyun-results" / result_id
            if _RESULT_ID.fullmatch(result_id) is None or path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= _MAX_RESULT:
                return _empty_stream(404)
            mime = _detect_mime(path.read_bytes()[:16])
            if mime is None:
                return _empty_stream(404)
            if range_header is None:
                return _FileStream(path, mime, head=head)
            interval = _range(range_header, path.stat().st_size)
            if interval is None:
                return _empty_stream(416, size=path.stat().st_size)
            start, end = interval
            return _FileStream(path, mime, offset=start, length=end - start + 1, head=head)
        if self._adapter_type == "chiyun_gemini_images":
            from ai_creation_canvas.adapters.chiyun_gemini import _RESULT_ID, _detect_mime
            from ai_creation_canvas.adapters.chiyun import _FileStream, _empty_stream, _range
            path = self._data_dir / "chiyun-gemini-results" / result_id
            if _RESULT_ID.fullmatch(result_id) is None or path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 32 * 1024 * 1024:
                return _empty_stream(404)
            mime = _detect_mime(path.read_bytes()[:16])
            if mime is None:
                return _empty_stream(404)
            if range_header is None:
                return _FileStream(path, mime, head=head)
            interval = _range(range_header, path.stat().st_size)
            if interval is None:
                return _empty_stream(416, size=path.stat().st_size)
            start, end = interval
            return _FileStream(path, mime, offset=start, length=end - start + 1, head=head)
        from ai_creation_canvas.adapters.ark import _FileStream, _RESULT_ID, _missing_stream, _range, _range_missing_stream
        result_root = self._data_dir / "ark-results"
        media, metadata = result_root / result_id, result_root / f"{result_id}.json"
        if _RESULT_ID.fullmatch(result_id) is None or media.is_symlink() or metadata.is_symlink() or not media.is_file() or not metadata.is_file():
            return _missing_stream()
        try:
            mime = json.loads(metadata.read_text(encoding="utf-8"))["mime"]
        except (OSError, ValueError, KeyError, TypeError):
            return _missing_stream()
        if range_header is None:
            return _FileStream(media, mime, head=head)
        interval = _range(range_header, media.stat().st_size)
        if interval is None:
            return _range_missing_stream(media.stat().st_size)
        start, end = interval
        return _FileStream(media, mime, offset=start, length=end - start + 1, head=head)


def _is_prompt_port(port: object) -> bool:
    return (
        getattr(port, "port_id", None) == "prompt"
        and getattr(port, "media_type", None) == "text"
        and getattr(port, "min_items", None) == 1
        and getattr(port, "max_items", None) == 1
    )


def _validate_parameter_contract(
    adapter_type: str,
    operation: ModelOperation,
    schema: Mapping[str, object],
    mappings: Mapping[str, str],
    family: str,
) -> None:
    if adapter_type == "ark" and operation in {ModelOperation.IMAGE_GENERATE, ModelOperation.IMAGE_EDIT}:
        template = _ARK_IMAGE_PARAMETERS
        required_template = frozenset()
    elif adapter_type == "ark" and operation is ModelOperation.VIDEO_GENERATE:
        template = _ARK_VIDEO_PARAMETERS
        required_template = frozenset()
    elif adapter_type == "chiyun_openai_images" and operation is ModelOperation.IMAGE_EDIT:
        template = _CHIYUN_PARAMETERS
        required_template = frozenset({"size", "output_count"})
    elif adapter_type == "chiyun_gemini_images" and operation is ModelOperation.IMAGE_EDIT:
        template = _CHIYUN_GEMINI_PARAMETERS
        required_template = frozenset({"aspect_ratio", "image_size"})
    else:
        raise ValueError("route parameter template is unsupported")
    if set(schema) - {"type", "properties", "required", "additionalProperties", "x-aicc-profile"}:
        raise ValueError("route parameter schema is unsupported")
    profile = schema.get("x-aicc-profile")
    expected_profile = (
        "seedream" if adapter_type == "ark" and operation in {ModelOperation.IMAGE_GENERATE, ModelOperation.IMAGE_EDIT}
        else "seedance" if adapter_type == "ark" and operation is ModelOperation.VIDEO_GENERATE
        else "banana" if family == "nano-banana"
        else "gpt_image2" if family == "gpt-image"
        else None
    )
    if profile is not None and profile != expected_profile:
        raise ValueError("route parameter profile is unsupported")
    properties = schema.get("properties")
    raw_required = schema.get("required", [])
    if (
        schema.get("type") != "object"
        or schema.get("additionalProperties", False) is not False
        or not isinstance(properties, Mapping)
        or not isinstance(raw_required, list)
        or any(not isinstance(item, str) for item in raw_required)
    ):
        raise ValueError("route parameter schema is unsupported")
    required = frozenset(raw_required)
    if len(required) != len(raw_required) or not required <= set(properties) or required != required_template:
        raise ValueError("route parameter required fields are unsupported")
    if adapter_type in {"chiyun_openai_images", "chiyun_gemini_images"} and set(properties) != set(template):
        raise ValueError("Chiyun parameter schema must include its fixed fields")
    if set(properties) - set(template) or set(mappings) != set(properties):
        raise ValueError("route parameter names are unsupported")
    for name, raw_rule in properties.items():
        if not isinstance(raw_rule, Mapping) or set(raw_rule) - _RULE_KEYS:
            raise ValueError("route parameter rule is unsupported")
        provider_target, trusted_rule = template[name]
        if mappings[name] != provider_target or not _rule_is_subset(raw_rule, trusted_rule):
            raise ValueError("route parameter contract widens its trusted template")


def _rule_is_subset(rule: Mapping[str, object], trusted: Mapping[str, object]) -> bool:
    kind = rule.get("type")
    if kind != trusted.get("type"):
        return False
    if ("default" in rule) != ("default" in trusted) or rule.get("default") != trusted.get("default"):
        return False
    trusted_enum = trusted.get("enum")
    route_enum = rule.get("enum")
    if trusted_enum is not None:
        if (
            not isinstance(trusted_enum, list)
            or not isinstance(route_enum, list)
            or not route_enum
            or any(item not in trusted_enum for item in route_enum)
        ):
            return False
    elif route_enum is not None and (not isinstance(route_enum, list) or not route_enum):
        return False
    if isinstance(route_enum, list) and any(not _matches_kind(item, kind) for item in route_enum):
        return False
    if kind in {"string", "boolean"} and ("minimum" in rule or "maximum" in rule):
        return False
    if kind != "string" and "x-ark-size" in rule:
        return False
    if any(bound in rule and not _number(rule[bound]) for bound in ("minimum", "maximum")):
        return False
    for lower in ("minimum",):
        if lower in trusted and (lower not in rule or not _number(rule[lower]) or rule[lower] < trusted[lower]):
            return False
    for upper in ("maximum",):
        if upper in trusted and (upper not in rule or not _number(rule[upper]) or rule[upper] > trusted[upper]):
            return False
    trusted_size = trusted.get("x-ark-size")
    route_size = rule.get("x-ark-size")
    if trusted_size is not None:
        if not isinstance(trusted_size, Mapping) or not isinstance(route_size, Mapping):
            return False
        if set(route_size) != {"presets", "min_pixels", "max_pixels", "min_ratio", "max_ratio"}:
            return False
        if (
            not isinstance(route_size["presets"], list)
            or not route_size["presets"]
            or not set(route_size["presets"]) <= set(trusted_size["presets"])
            or route_size["min_pixels"] < trusted_size["min_pixels"]
            or route_size["max_pixels"] > trusted_size["max_pixels"]
            or route_size["min_ratio"] < trusted_size["min_ratio"]
            or route_size["max_ratio"] > trusted_size["max_ratio"]
        ):
            return False
    elif route_size is not None:
        return False
    default = rule.get("default")
    if "default" in rule and isinstance(route_enum, list) and default not in route_enum:
        return False
    if "default" in rule and _number(default):
        if "minimum" in rule and default < rule["minimum"] or "maximum" in rule and default > rule["maximum"]:
            return False
    return True


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _matches_kind(value: object, kind: object) -> bool:
    return (
        kind == "string" and isinstance(value, str)
        or kind == "boolean" and isinstance(value, bool)
        or kind == "integer" and isinstance(value, int) and not isinstance(value, bool)
        or kind == "number" and _number(value)
    )
