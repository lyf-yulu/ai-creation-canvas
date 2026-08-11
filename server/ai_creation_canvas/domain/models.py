"""Provider-neutral domain values shared by adapters and application services."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import math
from typing import Mapping

from ai_creation_canvas.errors import ApiError


class FrozenDict(Mapping[str, object]):
    """A recursively immutable JSON object with a safe deepcopy representation."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, object]) -> None:
        self._values = {key: _freeze_json_value(value) for key, value in values.items()}

    def __getitem__(self, key: str) -> object:
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"FrozenDict({self._values!r})"

    def __deepcopy__(self, memo: dict[int, object]) -> dict[str, object]:
        result = _thaw_json_value(self)
        assert isinstance(result, dict)
        memo[id(self)] = result
        return result


def _freeze_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        return FrozenDict(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("parameter float values must be finite")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("parameter values must be JSON-compatible")


def _thaw_json_value(value: object) -> object:
    if isinstance(value, FrozenDict):
        return {key: _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value


def _stable_id(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty stable identifier")
    return value


def _non_empty_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value


def _utc_timestamp(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


class PortalRole(StrEnum):
    ADMIN = "admin"
    USER = "user"
    VIEWER = "viewer"


class ModelOperation(StrEnum):
    IMAGE_GENERATE = "image.generate"
    IMAGE_EDIT = "image.edit"
    VIDEO_GENERATE = "video.generate"
    VIDEO_IMAGE_TO_VIDEO = "video.image_to_video"


class AssetKind(StrEnum):
    REFERENCE = "reference"
    PORTRAIT = "portrait"


class AssetMediaType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class AssetStatus(StrEnum):
    PROCESSING = "processing"
    ACTIVE = "active"
    FAILED = "failed"


class JobStatus(StrEnum):
    UPLOADING = "uploading"
    SUBMITTING = "submitting"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ModelInputPort:
    port_id: str
    media_type: str
    min_items: int = 0
    max_items: int = 1

    def __post_init__(self) -> None:
        _stable_id(self.port_id, "port_id")
        if self.media_type not in {"text", "image", "video", "audio"}:
            raise ValueError("media_type must be supported")
        if isinstance(self.min_items, bool) or isinstance(self.max_items, bool) or not isinstance(self.min_items, int) or not isinstance(self.max_items, int):
            raise ValueError("port item limits must be integers")
        if self.min_items < 0 or self.max_items < 1 or self.min_items > self.max_items or self.max_items > 64:
            raise ValueError("port item limits are invalid")


@dataclass(frozen=True, slots=True)
class PortalUser:
    user_id: str
    username: str
    role: PortalRole | str

    def __post_init__(self) -> None:
        _stable_id(self.user_id, "user_id")
        _non_empty_text(self.username, "username")
        try:
            object.__setattr__(self, "role", PortalRole(self.role))
        except ValueError as error:
            raise ValueError("role must be a supported PortalRole") from error


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Verified request metadata; it deliberately never carries service credentials."""

    user: PortalUser
    request_id: str
    trace_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.user, PortalUser):
            raise ValueError("user must be a PortalUser")
        _stable_id(self.request_id, "request_id")
        _stable_id(self.trace_id, "trace_id")


@dataclass(frozen=True, slots=True)
class ModelSpec:
    model_id: str
    service_id: str
    display_name: str
    operations: tuple[ModelOperation | str, ...]
    input_media: tuple[str, ...] = ()
    parameter_schema: Mapping[str, object] = field(default_factory=dict)
    requires_asset_kind: AssetKind | str | None = None
    input_ports: tuple[ModelInputPort, ...] = ()
    parameter_mappings: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _stable_id(self.model_id, "model_id")
        _stable_id(self.service_id, "service_id")
        _non_empty_text(self.display_name, "display_name")
        try:
            object.__setattr__(self, "operations", tuple(ModelOperation(item) for item in self.operations))
        except ValueError as error:
            raise ValueError("operations must contain supported ModelOperation values") from error
        if not self.operations:
            raise ValueError("operations must contain supported ModelOperation values")
        object.__setattr__(self, "input_media", tuple(self.input_media))
        if any(not isinstance(item, str) or not item.strip() for item in self.input_media):
            raise ValueError("input_media must contain non-empty media types")
        if self.requires_asset_kind is not None:
            try:
                object.__setattr__(self, "requires_asset_kind", AssetKind(self.requires_asset_kind))
            except ValueError as error:
                raise ValueError("requires_asset_kind must be a supported AssetKind") from error
        object.__setattr__(self, "parameter_schema", FrozenDict(self.parameter_schema))
        ports = tuple(self.input_ports)
        if any(not isinstance(port, ModelInputPort) for port in ports) or len({port.port_id for port in ports}) != len(ports):
            raise ValueError("input_ports must contain unique ModelInputPort values")
        object.__setattr__(self, "input_ports", ports)
        mappings = dict(self.parameter_mappings)
        if any(not isinstance(key, str) or not key or not isinstance(value, str) or not value for key, value in mappings.items()):
            raise ValueError("parameter_mappings must contain stable names")
        object.__setattr__(self, "parameter_mappings", FrozenDict(mappings))


@dataclass(frozen=True, slots=True)
class AssetRef:
    asset_id: str
    kind: AssetKind | str
    status: AssetStatus | str
    mime_type: str
    media_type: AssetMediaType | str | None = None

    def __post_init__(self) -> None:
        _stable_id(self.asset_id, "asset_id")
        try:
            object.__setattr__(self, "kind", AssetKind(self.kind))
        except ValueError as error:
            raise ValueError("kind must be a supported AssetKind") from error
        try:
            object.__setattr__(self, "status", AssetStatus(self.status))
        except ValueError as error:
            raise ValueError("status must be a supported AssetStatus") from error
        _non_empty_text(self.mime_type, "mime_type")
        if self.media_type is None:
            return
        try:
            object.__setattr__(self, "media_type", AssetMediaType(self.media_type))
        except ValueError as error:
            raise ValueError("media_type must be a supported AssetMediaType") from error


@dataclass(frozen=True, slots=True)
class JobRequest:
    operation: ModelOperation | str
    model_id: str
    prompt: str
    idempotency_key: str
    params: Mapping[str, object] = field(default_factory=dict)
    asset_ids: tuple[str, ...] = ()
    inputs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "operation", ModelOperation(self.operation))
        except ValueError as error:
            raise ValueError("operation must be a supported ModelOperation") from error
        _stable_id(self.model_id, "model_id")
        _non_empty_text(self.prompt, "prompt")
        _stable_id(self.idempotency_key, "idempotency_key")
        object.__setattr__(self, "asset_ids", tuple(self.asset_ids))
        if any(not isinstance(asset_id, str) or not asset_id.strip() for asset_id in self.asset_ids):
            raise ValueError("asset_ids must contain non-empty stable identifiers")
        object.__setattr__(self, "params", FrozenDict(self.params))
        normalized_inputs: dict[str, tuple[str, ...]] = {}
        for port_id, asset_ids in self.inputs.items():
            _stable_id(port_id, "input port ID")
            values = tuple(asset_ids)
            if len(values) > 64 or any(not isinstance(asset_id, str) or not asset_id.strip() for asset_id in values):
                raise ValueError("inputs must contain stable asset identifiers")
            normalized_inputs[port_id] = values
        object.__setattr__(self, "inputs", FrozenDict(normalized_inputs))


@dataclass(frozen=True, slots=True)
class JobState:
    job_id: str
    status: JobStatus | str
    result: AssetRef | None = None
    error: ApiError | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        _stable_id(self.job_id, "job_id")
        try:
            object.__setattr__(self, "status", JobStatus(self.status))
        except ValueError as error:
            raise ValueError("status must be a supported JobStatus") from error
        if self.result is not None and not isinstance(self.result, AssetRef):
            raise ValueError("result must be an AssetRef")
        if self.error is not None and not isinstance(self.error, ApiError):
            raise ValueError("error must be an ApiError")
        if self.status is JobStatus.SUCCEEDED:
            if self.result is None:
                raise ValueError("succeeded jobs require a result")
            if self.error is not None:
                raise ValueError("succeeded jobs cannot include an error")
        elif self.status is JobStatus.FAILED:
            if self.result is not None:
                raise ValueError("failed jobs cannot include a result")
            if self.error is None:
                raise ValueError("failed jobs require an error")
        elif self.result is not None or self.error is not None:
            raise ValueError("in-progress jobs cannot include a result or error")
        object.__setattr__(self, "updated_at", _utc_timestamp(self.updated_at, "updated_at"))


@dataclass(frozen=True, slots=True)
class UpstreamJob:
    service_id: str
    upstream_job_id: str
    state: JobState
    submitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        _stable_id(self.service_id, "service_id")
        _stable_id(self.upstream_job_id, "upstream_job_id")
        if not isinstance(self.state, JobState):
            raise ValueError("state must be a JobState")
        object.__setattr__(self, "submitted_at", _utc_timestamp(self.submitted_at, "submitted_at"))
