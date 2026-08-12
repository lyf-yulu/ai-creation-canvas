"""Governed, data-only provider and model definitions.

Definitions select trusted adapter code; they never contain executable code or
browser-visible credentials.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import re
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlsplit

from ai_creation_canvas.domain.models import ModelInputPort, ModelOperation, ModelSpec
from ai_creation_canvas.parameter_schema import validate_parameter_schema


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_CREDENTIAL_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_ADAPTER_TYPES = frozenset({"ark", "chiyun_openai_images", "portal_jobs", "portal_portrait", "demo"})
_PARAMETER_TARGET = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)?\Z")


class ModelModality(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    TEXT = "text"


def _stable_id(value: object, field: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise ValueError(f"{field} is invalid")
    return value.strip()


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    provider_id: str
    display_name: str
    adapter_type: str
    base_url: str
    credential_ref: str
    enabled: bool = True
    revision: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _stable_id(self.provider_id, "provider_id"))
        object.__setattr__(self, "display_name", _text(self.display_name, "display_name", 128))
        if self.adapter_type not in _ADAPTER_TYPES:
            raise ValueError("adapter_type is not registered")
        parsed = urlsplit(self.base_url)
        try:
            port = parsed.port
        except ValueError:
            raise ValueError("base_url is invalid") from None
        if parsed.scheme != "https" or not parsed.hostname or port not in {None, 443} or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("base_url must be an HTTPS origin")
        object.__setattr__(self, "base_url", f"https://{parsed.netloc}")
        if not isinstance(self.credential_ref, str) or _CREDENTIAL_REF.fullmatch(self.credential_ref) is None:
            raise ValueError("credential_ref is invalid")
        if type(self.enabled) is not bool or type(self.revision) is not int or self.revision < 1:
            raise ValueError("provider state is invalid")

    def public_projection(self) -> dict[str, object]:
        return {"provider_id": self.provider_id, "display_name": self.display_name, "enabled": self.enabled, "revision": self.revision}


@dataclass(frozen=True, slots=True)
class OperationContract:
    operation: ModelOperation | str
    input_ports: tuple[ModelInputPort, ...]
    output_media_type: str
    parameter_schema: Mapping[str, object]
    parameter_mappings: Mapping[str, str]

    def __post_init__(self) -> None:
        try:
            operation = ModelOperation(self.operation)
        except ValueError as error:
            raise ValueError("operation is invalid") from error
        object.__setattr__(self, "operation", operation)
        ports = tuple(self.input_ports)
        if not ports or len(ports) > 16 or any(not isinstance(port, ModelInputPort) for port in ports) or len({port.port_id for port in ports}) != len(ports):
            raise ValueError("input ports are invalid")
        if self.output_media_type not in {"image", "video", "audio", "text"}:
            raise ValueError("output_media_type is invalid")
        schema = json.loads(json.dumps(self.parameter_schema, allow_nan=False))
        validate_parameter_schema(schema)
        properties = schema.get("properties")
        mappings = dict(self.parameter_mappings)
        if not isinstance(properties, dict) or set(mappings) != set(properties):
            raise ValueError("each parameter requires one mapping")
        if len(set(mappings.values())) != len(mappings) or any(_PARAMETER_TARGET.fullmatch(target) is None or "__" in target for target in mappings.values()):
            raise ValueError("parameter mappings are unsafe")
        object.__setattr__(self, "input_ports", ports)
        object.__setattr__(self, "parameter_schema", MappingProxyType(schema))
        object.__setattr__(self, "parameter_mappings", MappingProxyType(mappings))

    def to_dict(self, *, public: bool = False) -> dict[str, object]:
        body: dict[str, object] = {
            "operation": self.operation.value,
            "input_ports": [
                {"port_id": port.port_id, "media_type": port.media_type, "min_items": port.min_items, "max_items": port.max_items}
                for port in self.input_ports
            ],
            "output_media_type": self.output_media_type,
            "parameter_schema": json.loads(json.dumps(dict(self.parameter_schema))),
        }
        if not public:
            body["parameter_mappings"] = dict(self.parameter_mappings)
        return body

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "OperationContract":
        raw_ports = value.get("input_ports")
        if not isinstance(raw_ports, list):
            raise ValueError("operation ports are invalid")
        ports: list[ModelInputPort] = []
        for raw in raw_ports:
            if not isinstance(raw, dict) or set(raw) != {"port_id", "media_type", "min_items", "max_items"}:
                raise ValueError("operation port is invalid")
            ports.append(ModelInputPort(str(raw["port_id"]), str(raw["media_type"]), raw["min_items"], raw["max_items"]))
        schema = value.get("parameter_schema")
        mappings = value.get("parameter_mappings")
        if not isinstance(schema, dict) or not isinstance(mappings, dict) or any(not isinstance(key, str) or not isinstance(item, str) for key, item in mappings.items()):
            raise ValueError("operation parameters are invalid")
        return cls(str(value.get("operation")), tuple(ports), str(value.get("output_media_type")), schema, mappings)


@dataclass(frozen=True, slots=True)
class GovernedModelDefinition:
    model_id: str
    provider_id: str
    provider_model_name: str
    display_name: str
    introduction: str
    modality: ModelModality | str
    operation_contracts: tuple[OperationContract, ...]
    enabled: bool = True
    revision: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_id", _stable_id(self.model_id, "model_id"))
        object.__setattr__(self, "provider_id", _stable_id(self.provider_id, "provider_id"))
        object.__setattr__(self, "provider_model_name", _stable_id(self.provider_model_name, "provider_model_name"))
        object.__setattr__(self, "display_name", _text(self.display_name, "display_name", 128))
        object.__setattr__(self, "introduction", _text(self.introduction, "introduction", 1000))
        try:
            modality = ModelModality(self.modality)
        except ValueError as error:
            raise ValueError("modality is invalid") from error
        contracts = tuple(self.operation_contracts)
        if not contracts or len(contracts) > 8 or any(not isinstance(item, OperationContract) for item in contracts) or len({item.operation for item in contracts}) != len(contracts):
            raise ValueError("operation contracts are invalid")
        prefix = {ModelModality.IMAGE: "image.", ModelModality.VIDEO: "video.", ModelModality.AUDIO: "audio.", ModelModality.TEXT: "text."}[modality]
        if any(not item.operation.value.startswith(prefix) or item.output_media_type != modality.value for item in contracts):
            raise ValueError("operation contracts cross model modality")
        if type(self.enabled) is not bool or type(self.revision) is not int or self.revision < 1:
            raise ValueError("model state is invalid")
        object.__setattr__(self, "modality", modality)
        object.__setattr__(self, "operation_contracts", contracts)

    def model_spec(self, service_id: str, operation: ModelOperation | str | None = None) -> ModelSpec:
        contract = self.operation_contracts[0] if operation is None and len(self.operation_contracts) == 1 else next((item for item in self.operation_contracts if item.operation == operation), None)
        if contract is None:
            raise ValueError("an explicit operation is required")
        return ModelSpec(self.model_id, service_id, self.display_name, (contract.operation,), tuple(port.media_type for port in contract.input_ports), contract.parameter_schema, None, contract.input_ports, contract.parameter_mappings)

    def public_projection(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "introduction": self.introduction,
            "modality": self.modality.value,
            "operations": [item.operation.value for item in self.operation_contracts],
            "operation_contracts": [item.to_dict(public=True) for item in self.operation_contracts],
            "enabled": self.enabled,
            "revision": self.revision,
        }

    def contracts_json(self) -> str:
        return json.dumps([item.to_dict() for item in self.operation_contracts], sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> "GovernedModelDefinition":
        raw = json.loads(str(record["operation_contracts_json"]))
        if not isinstance(raw, list):
            raise ValueError("stored operation contracts are invalid")
        return cls(
            model_id=str(record["model_id"]), provider_id=str(record["provider_id"]),
            provider_model_name=str(record["provider_model_name"]), display_name=str(record["display_name"]),
            introduction=str(record["introduction"]), modality=str(record["modality"]),
            operation_contracts=tuple(OperationContract.from_dict(item) for item in raw if isinstance(item, dict)),
            enabled=bool(record["enabled"]), revision=int(record["revision"]),
        )


def provider_from_record(record: Mapping[str, object]) -> ProviderDefinition:
    return ProviderDefinition(
        provider_id=str(record["provider_id"]), display_name=str(record["display_name"]),
        adapter_type=str(record["adapter_type"]), base_url=str(record["base_url"]),
        credential_ref=str(record["credential_ref"]), enabled=bool(record["enabled"]), revision=int(record["revision"]),
    )
