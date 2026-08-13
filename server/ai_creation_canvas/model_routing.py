"""Data-only logical model and provider route contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import re
from typing import Mapping

from ai_creation_canvas.credential_pools import CredentialPool
from ai_creation_canvas.domain.models import ModelOperation
from ai_creation_canvas.model_registry import ModelModality, OperationContract


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_ADAPTER_TYPES = frozenset({"ark", "chiyun_gemini_images", "chiyun_openai_images", "portal_jobs", "portal_portrait", "demo"})
_MAX_CONTRACT_BYTES = 64 * 1024
_MAX_PARAMETERS = 64


class ObjectReferenced(ValueError):
    """An object cannot be physically deleted while history refers to it."""


class RevisionConflict(ValueError):
    """The caller did not operate on the current stored revision."""


@dataclass(frozen=True, slots=True)
class DeleteResult:
    deleted: bool


def _stable_id(value: object, field: str, *, reference: bool = False) -> str:
    pattern = _REFERENCE if reference else _ID
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _text(value: object, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(char) < 32 and char not in "\n\t" for char in value)
    ):
        raise ValueError(f"{field} is invalid")
    return value.strip()


def _archived_at(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError("archived_at is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("archived_at is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("archived_at is invalid")
    return value


def _contracts(value: object, modality: ModelModality) -> tuple[OperationContract, ...]:
    try:
        contracts = tuple(value)  # type: ignore[arg-type]
    except TypeError:
        raise ValueError("operation contracts are invalid") from None
    if (
        not contracts
        or len(contracts) > 8
        or any(not isinstance(item, OperationContract) for item in contracts)
        or len({item.operation for item in contracts}) != len(contracts)
    ):
        raise ValueError("operation contracts are invalid")
    prefix = f"{modality.value}."
    for contract in contracts:
        if not contract.operation.value.startswith(prefix) or contract.output_media_type != modality.value:
            raise ValueError("operation contracts cross model modality")
        properties = contract.parameter_schema.get("properties")
        if not isinstance(properties, Mapping) or len(properties) > _MAX_PARAMETERS:
            raise ValueError("operation contract schema must be bounded")
        if set(contract.parameter_mappings) != set(properties):
            raise ValueError("each parameter requires one mapping")
    if len(_contracts_json(contracts).encode("utf-8")) > _MAX_CONTRACT_BYTES:
        raise ValueError("operation contract schema must be bounded")
    return contracts


def _contracts_json(contracts: tuple[OperationContract, ...]) -> str:
    return json.dumps(
        [item.to_dict() for item in contracts],
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def contracts_from_json(value: object) -> tuple[OperationContract, ...]:
    try:
        raw = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("stored operation contracts are invalid") from None
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise ValueError("stored operation contracts are invalid")
    return tuple(OperationContract.from_dict(item) for item in raw)


@dataclass(frozen=True, slots=True)
class LogicalModelDefinition:
    model_id: str
    display_name: str
    introduction: str
    modality: ModelModality | str
    operation_contracts: tuple[OperationContract, ...]
    enabled: bool = True
    archived_at: str | None = None
    revision: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_id", _stable_id(self.model_id, "model_id"))
        object.__setattr__(self, "display_name", _text(self.display_name, "display_name", 128))
        object.__setattr__(self, "introduction", _text(self.introduction, "introduction", 1000))
        try:
            modality = ModelModality(self.modality)
        except ValueError as error:
            raise ValueError("modality is invalid") from error
        object.__setattr__(self, "modality", modality)
        object.__setattr__(self, "operation_contracts", _contracts(self.operation_contracts, modality))
        object.__setattr__(self, "archived_at", _archived_at(self.archived_at))
        if type(self.enabled) is not bool or type(self.revision) is not int or self.revision < 1:
            raise ValueError("logical model state is invalid")
        if self.archived_at is not None and self.enabled:
            raise ValueError("archived logical model must be disabled")

    def contracts_json(self) -> str:
        return _contracts_json(self.operation_contracts)

    def audit_projection(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "display_name": self.display_name,
            "modality": self.modality.value,
            "enabled": self.enabled,
            "archived_at": self.archived_at,
            "revision": self.revision,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> LogicalModelDefinition:
        return cls(
            model_id=str(record["model_id"]),
            display_name=str(record["display_name"]),
            introduction=str(record["introduction"]),
            modality=str(record["modality"]),
            operation_contracts=contracts_from_json(record["operation_contracts_json"]),
            enabled=bool(record["enabled"]),
            archived_at=str(record["archived_at"]) if record.get("archived_at") is not None else None,
            revision=int(record["revision"]),
        )


@dataclass(frozen=True, slots=True)
class ModelRouteDefinition:
    route_id: str
    model_id: str
    provider_id: str
    provider_model_name: str
    adapter_type: str
    credential_pool_ref: str
    family: str
    operation_contracts: tuple[OperationContract, ...]
    priority: int
    max_concurrency: int
    enabled: bool = True
    archived_at: str | None = None
    revision: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "route_id", _stable_id(self.route_id, "route_id"))
        object.__setattr__(self, "model_id", _stable_id(self.model_id, "model_id"))
        object.__setattr__(self, "provider_id", _stable_id(self.provider_id, "provider_id"))
        object.__setattr__(self, "provider_model_name", _stable_id(self.provider_model_name, "provider_model_name", reference=True))
        if self.adapter_type not in _ADAPTER_TYPES:
            raise ValueError("adapter_type is not registered")
        object.__setattr__(self, "credential_pool_ref", _stable_id(self.credential_pool_ref, "credential_pool_ref", reference=True))
        object.__setattr__(self, "family", _stable_id(self.family, "family", reference=True))
        modalities = {
            contract.output_media_type for contract in tuple(self.operation_contracts)
            if isinstance(contract, OperationContract)
        }
        if len(modalities) != 1:
            raise ValueError("route operation contracts are invalid")
        modality = ModelModality(next(iter(modalities)))
        object.__setattr__(self, "operation_contracts", _contracts(self.operation_contracts, modality))
        if type(self.priority) is not int or not 0 <= self.priority <= 1_000_000:
            raise ValueError("priority is invalid")
        if type(self.max_concurrency) is not int or not 1 <= self.max_concurrency <= 4096:
            raise ValueError("max_concurrency is invalid")
        object.__setattr__(self, "archived_at", _archived_at(self.archived_at))
        if type(self.enabled) is not bool or type(self.revision) is not int or self.revision < 1:
            raise ValueError("route state is invalid")
        if self.archived_at is not None and self.enabled:
            raise ValueError("archived route must be disabled")

    def contracts_json(self) -> str:
        return _contracts_json(self.operation_contracts)

    def audit_projection(self) -> dict[str, object]:
        return {
            "route_id": self.route_id,
            "model_id": self.model_id,
            "enabled": self.enabled,
            "archived_at": self.archived_at,
            "revision": self.revision,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> ModelRouteDefinition:
        return cls(
            route_id=str(record["route_id"]),
            model_id=str(record["model_id"]),
            provider_id=str(record["provider_id"]),
            provider_model_name=str(record["provider_model_name"]),
            adapter_type=str(record["adapter_type"]),
            credential_pool_ref=str(record["credential_pool_ref"]),
            family=str(record["family"]),
            operation_contracts=contracts_from_json(record["operation_contracts_json"]),
            priority=int(record["priority"]),
            max_concurrency=int(record["max_concurrency"]),
            enabled=bool(record["enabled"]),
            archived_at=str(record["archived_at"]) if record.get("archived_at") is not None else None,
            revision=int(record["revision"]),
        )


@dataclass(frozen=True, slots=True)
class RouteCompatibility:
    route_id: str
    operation: ModelOperation | str
    provider_id: str
    pool_id: str
    priority: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "route_id", _stable_id(self.route_id, "route_id"))
        try:
            object.__setattr__(self, "operation", ModelOperation(self.operation))
        except ValueError as error:
            raise ValueError("operation is invalid") from error
        object.__setattr__(self, "provider_id", _stable_id(self.provider_id, "provider_id"))
        object.__setattr__(self, "pool_id", _stable_id(self.pool_id, "pool_id", reference=True))
        if type(self.priority) is not int or not 0 <= self.priority <= 1_000_000:
            raise ValueError("priority is invalid")


@dataclass(frozen=True, slots=True)
class HistoricalAuditStub:
    object_id: str
    object_type: str
    display_name: str | None
    modality: str | None
    model_id: str | None
    enabled: bool
    archived_at: str
    revision: int
    created_at: str
    updated_at: str

    def audit_projection(self) -> dict[str, object]:
        body: dict[str, object] = {
            f"{self.object_type}_id": self.object_id,
            "enabled": self.enabled,
            "archived_at": self.archived_at,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.display_name is not None:
            body["display_name"] = self.display_name
        if self.modality is not None:
            body["modality"] = self.modality
        if self.model_id is not None:
            body["model_id"] = self.model_id
        return body


def validate_route_model(route: ModelRouteDefinition, model: LogicalModelDefinition) -> None:
    if not isinstance(route, ModelRouteDefinition) or not isinstance(model, LogicalModelDefinition):
        raise ValueError("route and model definitions are required")
    if route.model_id != model.model_id:
        raise ValueError("route model ID is incompatible")
    model_contracts = {item.operation: item for item in model.operation_contracts}
    for route_contract in route.operation_contracts:
        model_contract = model_contracts.get(route_contract.operation)
        if model_contract is None or route_contract.output_media_type != model_contract.output_media_type:
            raise ValueError("route contract is incompatible with logical model")
        model_ports = {port.port_id: port for port in model_contract.input_ports}
        route_ports = {port.port_id: port for port in route_contract.input_ports}
        required_ports = {port.port_id for port in model_contract.input_ports if port.min_items > 0}
        if not required_ports <= set(route_ports):
            raise ValueError("route contract omits required input ports")
        for port in route_contract.input_ports:
            public_port = model_ports.get(port.port_id)
            if (
                public_port is None
                or port.media_type != public_port.media_type
                or port.min_items < public_port.min_items
                or port.max_items > public_port.max_items
            ):
                raise ValueError("route contract input ports are incompatible")
        route_properties = route_contract.parameter_schema.get("properties")
        model_properties = model_contract.parameter_schema.get("properties")
        assert isinstance(route_properties, Mapping) and isinstance(model_properties, Mapping)
        if not set(route_properties) <= set(model_properties):
            raise ValueError("route contract parameters are incompatible")
        for name, rule in route_properties.items():
            if rule != model_properties[name]:
                raise ValueError("route contract parameter schema is incompatible")
        if set(route_contract.parameter_mappings) != set(route_properties):
            raise ValueError("route contract silently omits a parameter mapping")


def validate_route_pool(route: ModelRouteDefinition, pool: CredentialPool) -> None:
    if not isinstance(route, ModelRouteDefinition) or not isinstance(pool, CredentialPool):
        raise ValueError("route and credential pool are required")
    if route.credential_pool_ref != pool.pool_id:
        raise ValueError("credential pool reference does not match route pool")
    if route.provider_id != pool.provider_id:
        raise ValueError("credential pool provider does not match route provider")
    if route.family not in pool.allowed_families:
        raise ValueError("credential pool family does not match route family")
