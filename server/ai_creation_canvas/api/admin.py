"""Standalone administrator endpoints with safe projections."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError, field_validator
from dataclasses import replace
from typing import Literal
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from python_multipart.exceptions import MultipartParseError

from ai_creation_canvas.api._common import context_for, problem
from ai_creation_canvas.api.usage import all_usage_projection
from ai_creation_canvas.auth.local import LocalAuthService
from ai_creation_canvas.credential_pool_import import import_credential_pool_json
from ai_creation_canvas.credential_pools import CredentialPoolLoader
from ai_creation_canvas.domain.models import PortalRole
from ai_creation_canvas.domain.models import ModelInputPort, ModelOperation
from ai_creation_canvas.model_registry import GovernedModelDefinition, ModelModality, OperationContract, ProviderDefinition
from ai_creation_canvas.model_routing import (
    HistoricalAuditStub,
    LogicalModelDefinition,
    ModelRouteDefinition,
    ObjectReferenced,
    RevisionConflict,
    validate_route_model,
    validate_route_pool,
)
from ai_creation_canvas.trusted_routing import provider_has_trusted_origin, validate_trusted_route


router = APIRouter(prefix="/api/v1/admin")
_CREDENTIAL_JSON_MAX_BYTES = 1024 * 1024
_CREDENTIAL_MULTIPART_OVERHEAD = 64 * 1024
_CREDENTIAL_HEADER_MAX_BYTES = 32 * 1024


class _BoundedCredentialStream:
    def __init__(self, request: Request) -> None:
        self._source = request.stream()
        self._consumed = 0

    async def __aiter__(self):
        async for chunk in self._source:
            self._consumed += len(chunk)
            if self._consumed > _CREDENTIAL_JSON_MAX_BYTES + _CREDENTIAL_MULTIPART_OVERHEAD:
                raise MultiPartException("credential upload is too large")
            yield chunk


class _CredentialJsonParser(MultiPartParser):
    def __init__(self, request: Request) -> None:
        super().__init__(request.headers, _BoundedCredentialStream(request), max_files=1, max_fields=0, max_part_size=1024)
        self.file_bytes = 0
        self.header_bytes = 0

    def on_part_begin(self) -> None:
        self.header_bytes = 0
        super().on_part_begin()

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self.header_bytes += end - start
        if self.header_bytes > _CREDENTIAL_HEADER_MAX_BYTES:
            raise MultiPartException("credential upload headers are too large")
        super().on_header_field(data, start, end)

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self.header_bytes += end - start
        if self.header_bytes > _CREDENTIAL_HEADER_MAX_BYTES:
            raise MultiPartException("credential upload headers are too large")
        super().on_header_value(data, start, end)

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._current_part.file is not None:
            self.file_bytes += end - start
            if self.file_bytes > _CREDENTIAL_JSON_MAX_BYTES:
                raise MultiPartException("credential upload is too large")
        super().on_part_data(data, start, end)

    async def parse(self):
        try:
            return await super().parse()
        except BaseException:
            for temporary in self._files_to_close_on_error:
                temporary.close()
            raise


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool


class UsageRates(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    video_price_fen: int = Field(ge=0, le=1_000_000_000)
    image_price_fen: int = Field(ge=0, le=1_000_000_000)


class ModelAssignments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model_ids: list[str] = Field(max_length=128)

    @field_validator("model_ids")
    @classmethod
    def stable_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not item or len(item) > 128 for item in value):
            raise ValueError("model IDs are invalid")
        return value


def _require_admin(request: Request):
    user = context_for(request).user
    if user.role is not PortalRole.ADMIN or not isinstance(getattr(request.app.state, "local_auth", None), LocalAuthService):
        raise problem(request, "API_NOT_FOUND", "The requested API resource was not found.", status=404)
    return user


def _safe_user(
    row: dict[str, object], model_ids: tuple[str, ...], *, comfy_workflow_ids: tuple[str, ...] | None = None
) -> dict[str, object]:
    value = {
        "user_id": row["user_id"],
        "username": row["username_normalized"],
        "display_name": row["display_name"],
        "role": row["role"],
        "enabled": bool(row["enabled"]),
        "must_change_password": bool(row["must_change_password"]),
        "model_ids": list(model_ids),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if comfy_workflow_ids is not None:
        value["comfy_workflow_ids"] = list(comfy_workflow_ids)
    return value


def _assigned_model_ids(store, user_id: str) -> tuple[str, ...]:
    return tuple(sorted({*store.assigned_models(user_id), *store.governed_assigned_models(user_id)}))


@router.get("/users")
async def list_users(request: Request) -> dict[str, object]:
    _require_admin(request)
    store = request.app.state.canvas_store
    return {
        "users": [
            _safe_user(
                row,
                _assigned_model_ids(store, str(row["user_id"])),
                comfy_workflow_ids=store.assigned_comfy_workflow_ids(str(row["user_id"])),
            )
            for row in store.list_users()
        ]
    }


@router.get("/usage")
async def usage_summary(request: Request) -> dict[str, object]:
    _require_admin(request)
    store = request.app.state.canvas_store
    rows = store.admin_usage_by_user()
    charged = all_usage_projection(store)
    charged_by_user = {str(item["user_id"]): item["summary"] for item in charged["users"]}
    empty_charged = {"successful_jobs": 0, "image_count": 0, "video_seconds": 0, "total_cost_fen": "0"}
    users = [
        {
            "user_id": row["user_id"],
            "username": row["username_normalized"],
            "display_name": row["display_name"],
            **{name: int(row[name]) for name in ("jobs", "succeeded", "failed", "active", "image", "video")},
            "summary": charged_by_user.get(str(row["user_id"]), empty_charged),
        }
        for row in rows
    ]
    return {
        "summary": charged["summary"],
        "jobs": charged["jobs"],
        "totals": {name: sum(int(row[name]) for row in rows) for name in ("jobs", "succeeded", "failed", "active", "image", "video")},
        "users": users,
    }


@router.get("/usage/rates")
async def get_usage_rates(request: Request) -> dict[str, int]:
    _require_admin(request)
    return request.app.state.canvas_store.usage_rates()


@router.put("/usage/rates")
async def update_usage_rates(request: Request) -> dict[str, int]:
    _require_admin(request)
    try:
        body = UsageRates.model_validate(await request.json())
    except (ValidationError, ValueError):
        raise problem(request, "REQUEST_REJECTED", "The request was rejected.") from None
    return request.app.state.canvas_store.set_usage_rates(
        video_price_fen=body.video_price_fen,
        image_price_fen=body.image_price_fen,
    )


@router.patch("/users/{user_id}")
async def update_user(user_id: str, body: UserPatch, request: Request) -> dict[str, object]:
    _require_admin(request)
    store = request.app.state.canvas_store
    try:
        row = store.set_user_enabled(user_id, body.enabled)
    except KeyError:
        raise problem(request, "USER_NOT_FOUND", "The requested user was not found.", status=404) from None
    return _safe_user(row, _assigned_model_ids(store, user_id))


@router.get("/models")
async def list_models(request: Request) -> dict[str, object]:
    user = _require_admin(request)
    context = context_for(request)
    result = await request.app.state.model_catalog.list_models(context, cookie_header=request.headers.get("cookie"))
    return {"models": jsonable_encoder(result.models), "diagnostics": result.diagnostics, "requested_by": user.user_id}


@router.put("/users/{user_id}/models")
async def replace_models(user_id: str, body: ModelAssignments, request: Request) -> dict[str, object]:
    admin = _require_admin(request)
    store = request.app.state.canvas_store
    if store.user_by_id(user_id) is None:
        raise problem(request, "USER_NOT_FOUND", "The requested user was not found.", status=404)
    result = await request.app.state.model_catalog.list_models(context_for(request), cookie_header=request.headers.get("cookie"))
    available = {model.model_id for model in result.models}
    if not set(body.model_ids).issubset(available):
        raise problem(request, "MODEL_UNAVAILABLE", "The selected model is unavailable.", status=400)
    governed = {
        *(model.model_id for model in store.list_model_definitions()),
        *(model.model_id for model in store.list_logical_models() if isinstance(model, LogicalModelDefinition)),
    }
    static_ids = tuple(model_id for model_id in body.model_ids if model_id not in governed)
    governed_ids = tuple(model_id for model_id in body.model_ids if model_id in governed)
    store.replace_user_model_access(user_id, static_ids, governed_ids, actor_user_id=admin.user_id)
    model_ids = tuple(body.model_ids)
    return {"user_id": user_id, "model_ids": list(model_ids)}


class ProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provider_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    adapter_type: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=512)
    credential_ref: str = Field(min_length=1, max_length=128)
    enabled: bool = True


class ProviderUpdate(ProviderCreate):
    revision: StrictInt = Field(ge=1)


class GovernedModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model_id: str = Field(min_length=1, max_length=128)
    provider_id: str = Field(min_length=1, max_length=128)
    provider_model_name: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    introduction: str = Field(min_length=1, max_length=1000)
    template_id: str = Field(min_length=1, max_length=128)
    enabled: bool = True


class GovernedModelUpdate(GovernedModelCreate):
    revision: StrictInt = Field(ge=1)


def _chiyun_template() -> OperationContract:
    return OperationContract(
        ModelOperation.IMAGE_EDIT,
        (ModelInputPort("prompt", "text", 1, 1), ModelInputPort("reference_images", "image", 1, 10)),
        "image",
        {"type": "object", "properties": {"size": {"type": "string", "enum": ["auto", "1024x1024", "1024x1536", "1536x1024"], "default": "auto", "title": "尺寸"}, "output_count": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1, "title": "生成数量"}}, "required": ["size", "output_count"], "additionalProperties": False},
        {"size": "size", "output_count": "n"},
    )


_TEMPLATES = {"chiyun_gpt_image_edit_v1": _chiyun_template}


def _model_from_body(body: GovernedModelCreate, *, revision: int = 1) -> GovernedModelDefinition:
    factory = _TEMPLATES.get(body.template_id)
    if factory is None:
        raise ValueError("model template is unavailable")
    return GovernedModelDefinition(body.model_id, body.provider_id, body.provider_model_name, body.display_name, body.introduction, ModelModality.IMAGE, (factory(),), body.enabled, revision)


@router.get("/model-registry")
async def get_model_registry(request: Request) -> dict[str, object]:
    _require_admin(request)
    store = request.app.state.canvas_store
    adapter_factory = request.app.state.adapter_factory
    providers = [
        {
            **provider.public_projection(),
            "adapter_type": provider.adapter_type,
            "trusted_origin": provider_has_trusted_origin(provider),
            "credential_available": bool(adapter_factory and adapter_factory.credential_available(provider)),
        }
        for provider in store.list_provider_definitions()
    ]
    templates = [{"template_id": "chiyun_gpt_image_edit_v1", "title": "Chiyun GPT Image 图生图", "modality": "image", "operation": "image.edit"}]
    return {"providers": providers, "models": [model.public_projection() for model in store.list_model_definitions()], "templates": templates}


@router.post("/model-registry/providers", status_code=201)
async def create_provider(body: ProviderCreate, request: Request) -> dict[str, object]:
    del body
    _require_admin(request)
    raise problem(request, "PROVIDER_REGISTRY_READ_ONLY", "Provider definitions are deployment-owned.", status=405)


@router.put("/model-registry/providers/{provider_id}")
async def update_provider(provider_id: str, body: ProviderUpdate, request: Request) -> dict[str, object]:
    del provider_id, body
    _require_admin(request)
    raise problem(request, "PROVIDER_REGISTRY_READ_ONLY", "Provider definitions are deployment-owned.", status=405)


@router.delete("/model-registry/providers/{provider_id}", status_code=204)
async def delete_provider(provider_id: str, request: Request, revision: int = Query(ge=1)) -> Response:
    admin = _require_admin(request)
    store = request.app.state.canvas_store
    references = _reference_counts(store.provider_references(provider_id))
    if references:
        return _reference_conflict(request, references)
    try:
        store.delete_provider_definition(provider_id, expected_revision=revision, actor_user_id=admin.user_id)
    except KeyError:
        raise problem(request, "RESOURCE_NOT_FOUND", "The requested resource was not found.", status=404) from None
    except RevisionConflict:
        raise problem(request, "REVISION_CONFLICT", "The resource changed. Reload and try again.", status=409) from None
    except ObjectReferenced:
        references = _reference_counts(store.provider_references(provider_id))
        return _reference_conflict(request, references)
    return Response(status_code=204)


@router.post("/model-registry/models", status_code=201)
async def create_governed_model(body: GovernedModelCreate, request: Request) -> dict[str, object]:
    admin = _require_admin(request)
    try:
        saved = request.app.state.canvas_store.create_model_definition(_model_from_body(body), actor_user_id=admin.user_id)
    except (KeyError, ValueError):
        raise problem(request, "REQUEST_REJECTED", "The request was rejected.", status=400) from None
    return saved.public_projection()


@router.put("/model-registry/models/{model_id}")
async def update_governed_model(model_id: str, body: GovernedModelUpdate, request: Request) -> dict[str, object]:
    admin = _require_admin(request)
    if model_id != body.model_id:
        raise problem(request, "REQUEST_REJECTED", "The request was rejected.", status=400)
    try:
        saved = request.app.state.canvas_store.update_model_definition(_model_from_body(body, revision=body.revision), expected_revision=body.revision, actor_user_id=admin.user_id)
    except (KeyError, ValueError):
        raise problem(request, "REVISION_CONFLICT", "The resource changed. Reload and try again.", status=409) from None
    return saved.public_projection()


class InputPortBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    port_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    media_type: Literal["text", "image", "video", "audio"]
    min_items: StrictInt = Field(ge=0, le=64)
    max_items: StrictInt = Field(ge=1, le=64)


class OperationContractBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["image.generate", "image.edit", "video.generate"]
    input_ports: list[InputPortBody] = Field(min_length=1, max_length=16)
    output_media_type: Literal["image", "video", "audio", "text"]
    parameter_schema: dict[str, object]
    parameter_mappings: dict[str, str] = Field(max_length=64)

    @field_validator("parameter_schema")
    @classmethod
    def bounded_schema(cls, value: dict[str, object]) -> dict[str, object]:
        import json

        try:
            encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("parameter schema is invalid") from None
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise ValueError("parameter schema is too large")
        return value

    def contract(self) -> OperationContract:
        return OperationContract.from_dict(self.model_dump(mode="json"))


class LogicalModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    display_name: str = Field(min_length=1, max_length=128)
    introduction: str = Field(min_length=1, max_length=1000)
    modality: Literal["image", "video"]
    operation_contracts: list[OperationContractBody] = Field(min_length=1, max_length=8)
    enabled: StrictBool = True


class LogicalModelUpdate(LogicalModelCreate):
    revision: StrictInt = Field(ge=1)


class ModelRouteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    route_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    model_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    provider_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    provider_model_name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    adapter_type: Literal["ark", "chiyun_gemini_images", "chiyun_openai_images"]
    credential_pool_ref: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    family: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    operation_contracts: list[OperationContractBody] = Field(min_length=1, max_length=8)
    priority: StrictInt = Field(ge=0, le=1_000_000)
    max_concurrency: StrictInt = Field(ge=1, le=4096)
    enabled: StrictBool = True


class ModelRouteUpdate(ModelRouteCreate):
    revision: StrictInt = Field(ge=1)


class LifecycleRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: StrictInt = Field(ge=1)


def _logical_from_body(body: LogicalModelCreate, *, revision: int = 1, archived_at: str | None = None) -> LogicalModelDefinition:
    return LogicalModelDefinition(
        body.model_id,
        body.display_name,
        body.introduction,
        body.modality,
        tuple(item.contract() for item in body.operation_contracts),
        body.enabled,
        archived_at,
        revision,
    )


def _route_from_body(body: ModelRouteCreate, *, revision: int = 1, archived_at: str | None = None) -> ModelRouteDefinition:
    return ModelRouteDefinition(
        body.route_id,
        body.model_id,
        body.provider_id,
        body.provider_model_name,
        body.adapter_type,
        body.credential_pool_ref,
        body.family,
        tuple(item.contract() for item in body.operation_contracts),
        body.priority,
        body.max_concurrency,
        body.enabled,
        archived_at,
        revision,
    )


def _logical_projection(item) -> dict[str, object]:
    if isinstance(item, HistoricalAuditStub):
        return item.audit_projection()
    assert isinstance(item, LogicalModelDefinition)
    return {
        "model_id": item.model_id,
        "display_name": item.display_name,
        "introduction": item.introduction,
        "modality": item.modality.value,
        "operation_contracts": [contract.to_dict() for contract in item.operation_contracts],
        "enabled": item.enabled,
        "archived_at": item.archived_at,
        "revision": item.revision,
    }


def _route_projection(item) -> dict[str, object]:
    if isinstance(item, HistoricalAuditStub):
        return item.audit_projection()
    assert isinstance(item, ModelRouteDefinition)
    return {
        "route_id": item.route_id,
        "model_id": item.model_id,
        "provider_id": item.provider_id,
        "provider_model_name": item.provider_model_name,
        "adapter_type": item.adapter_type,
        "credential_pool_ref": item.credential_pool_ref,
        "family": item.family,
        "operation_contracts": [contract.to_dict() for contract in item.operation_contracts],
        "priority": item.priority,
        "max_concurrency": item.max_concurrency,
        "enabled": item.enabled,
        "archived_at": item.archived_at,
        "revision": item.revision,
    }


def _reference_counts(references: tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for reference in references:
        category = reference.split(":", 1)[0]
        if category in {"job", "access", "assignment", "route", "model"}:
            counts[category] = counts.get(category, 0) + 1
    return counts


def _reference_conflict(request: Request, references: dict[str, int]) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "code": "RESOURCE_REFERENCED",
            "message": "The resource is referenced.",
            "retryable": False,
            "request_id": getattr(request.state, "request_id", "request"),
            "phase": "request",
            "references": references,
        },
    )


def _resource_error(request: Request, error: Exception):
    if isinstance(error, KeyError):
        return problem(request, "RESOURCE_NOT_FOUND", "The requested resource was not found.", status=404)
    if isinstance(error, RevisionConflict):
        return problem(request, "REVISION_CONFLICT", "The resource changed. Reload and try again.", status=409)
    return problem(request, "REQUEST_REJECTED", "The request was rejected.", status=400)


def _route_runtime(request: Request):
    runtime = getattr(request.app.state, "managed_routing_runtime", None)
    if runtime is None:
        raise ValueError("managed routing is unavailable")
    return runtime


def _validate_admin_route(request: Request, route: ModelRouteDefinition) -> None:
    runtime = _route_runtime(request)
    model = request.app.state.canvas_store.logical_model(route.model_id)
    provider = request.app.state.canvas_store.provider_definition(route.provider_id)
    pools = runtime.pools()
    pool = pools.get(route.credential_pool_ref)
    if (
        not isinstance(model, LogicalModelDefinition)
        or provider is None
        or not provider.enabled
        or provider.adapter_type != route.adapter_type
        or pool is None
    ):
        raise ValueError("route dependencies are unavailable")
    validate_trusted_route(route, model)
    validate_route_model(route, model)
    validate_route_pool(route, pool)
    if not route.enabled:
        return
    protocol_available = getattr(runtime.adapter_factory, "protocol_available", None)
    if not callable(protocol_available) or not protocol_available(provider):
        raise ValueError("route provider protocol is unavailable")
    validator = getattr(runtime.adapter_factory, "validate_route", None)
    if not callable(validator):
        raise ValueError("trusted route validation is unavailable")
    validator(route)


@router.get("/logical-models")
async def list_logical_models(request: Request, include_archived: bool = False) -> dict[str, object]:
    _require_admin(request)
    items = request.app.state.canvas_store.list_logical_models(include_archived=include_archived)
    return {"models": [_logical_projection(item) for item in items]}


@router.post("/logical-models", status_code=201)
async def create_logical_model(body: LogicalModelCreate, request: Request) -> dict[str, object]:
    admin = _require_admin(request)
    try:
        saved = request.app.state.canvas_store.create_logical_model(_logical_from_body(body), actor_user_id=admin.user_id)
    except (KeyError, ValueError) as error:
        raise _resource_error(request, error) from None
    return _logical_projection(saved)


@router.get("/logical-models/{model_id}")
async def get_logical_model(model_id: str, request: Request) -> dict[str, object]:
    _require_admin(request)
    item = request.app.state.canvas_store.logical_model(model_id)
    if item is None:
        raise problem(request, "RESOURCE_NOT_FOUND", "The requested resource was not found.", status=404)
    return _logical_projection(item)


@router.put("/logical-models/{model_id}")
async def update_logical_model(model_id: str, body: LogicalModelUpdate, request: Request) -> dict[str, object]:
    admin = _require_admin(request)
    current = request.app.state.canvas_store.logical_model(model_id)
    if current is None:
        raise problem(request, "RESOURCE_NOT_FOUND", "The requested resource was not found.", status=404)
    if current.revision != body.revision:
        raise problem(request, "REVISION_CONFLICT", "The resource changed. Reload and try again.", status=409)
    if not isinstance(current, LogicalModelDefinition) or model_id != body.model_id or body.enabled != current.enabled:
        raise problem(request, "REQUEST_REJECTED", "The request was rejected.", status=400)
    try:
        saved = request.app.state.canvas_store.update_logical_model(
            _logical_from_body(body, revision=body.revision, archived_at=current.archived_at),
            expected_revision=body.revision,
            actor_user_id=admin.user_id,
        )
    except (KeyError, ValueError) as error:
        raise _resource_error(request, error) from None
    return _logical_projection(saved)


async def _logical_lifecycle(model_id: str, body: LifecycleRevision, request: Request, action: str) -> dict[str, object]:
    admin = _require_admin(request)
    store = request.app.state.canvas_store
    try:
        if action == "disable":
            saved = store.set_logical_model_enabled(model_id, enabled=False, expected_revision=body.revision, actor_user_id=admin.user_id)
        elif action == "enable":
            saved = store.set_logical_model_enabled(model_id, enabled=True, expected_revision=body.revision, actor_user_id=admin.user_id)
        elif action == "archive":
            saved = store.archive_logical_model(model_id, expected_revision=body.revision, actor_user_id=admin.user_id)
        elif action == "restore":
            saved = store.restore_logical_model(model_id, expected_revision=body.revision, actor_user_id=admin.user_id)
        else:
            saved = store.purge_logical_model_runtime(model_id, expected_revision=body.revision, actor_user_id=admin.user_id)
    except ObjectReferenced:
        return _reference_conflict(request, _reference_counts(store.logical_model_references(model_id)))
    except (KeyError, ValueError) as error:
        raise _resource_error(request, error) from None
    return _logical_projection(saved)


@router.post("/logical-models/{model_id}/disable")
async def disable_logical_model(model_id: str, body: LifecycleRevision, request: Request):
    return await _logical_lifecycle(model_id, body, request, "disable")


@router.post("/logical-models/{model_id}/enable")
async def enable_logical_model(model_id: str, body: LifecycleRevision, request: Request):
    return await _logical_lifecycle(model_id, body, request, "enable")


@router.post("/logical-models/{model_id}/archive")
async def archive_logical_model(model_id: str, body: LifecycleRevision, request: Request):
    return await _logical_lifecycle(model_id, body, request, "archive")


@router.post("/logical-models/{model_id}/restore")
async def restore_logical_model(model_id: str, body: LifecycleRevision, request: Request):
    return await _logical_lifecycle(model_id, body, request, "restore")


@router.post("/logical-models/{model_id}/purge-runtime")
async def purge_logical_model(model_id: str, body: LifecycleRevision, request: Request):
    return await _logical_lifecycle(model_id, body, request, "purge")


@router.delete("/logical-models/{model_id}", status_code=204)
async def delete_logical_model(model_id: str, request: Request, revision: int = Query(ge=1)) -> Response:
    admin = _require_admin(request)
    store = request.app.state.canvas_store
    try:
        store.delete_logical_model(model_id, expected_revision=revision, actor_user_id=admin.user_id)
    except ObjectReferenced:
        return _reference_conflict(request, _reference_counts(store.logical_model_references(model_id)))
    except (KeyError, ValueError) as error:
        raise _resource_error(request, error) from None
    return Response(status_code=204)


def _route_for_parent(request: Request, model_id: str, route_id: str):
    route = request.app.state.canvas_store.model_route(route_id)
    if route is None or route.model_id != model_id:
        raise problem(request, "RESOURCE_NOT_FOUND", "The requested resource was not found.", status=404)
    return route


@router.get("/logical-models/{model_id}/routes")
async def list_model_routes(model_id: str, request: Request, include_archived: bool = False) -> dict[str, object]:
    _require_admin(request)
    if request.app.state.canvas_store.logical_model(model_id) is None:
        raise problem(request, "RESOURCE_NOT_FOUND", "The requested resource was not found.", status=404)
    return {"routes": [_route_projection(item) for item in request.app.state.canvas_store.list_model_routes(model_id=model_id, include_archived=include_archived)]}


@router.post("/logical-models/{model_id}/routes", status_code=201)
async def create_model_route(model_id: str, body: ModelRouteCreate, request: Request) -> dict[str, object]:
    admin = _require_admin(request)
    if model_id != body.model_id:
        raise problem(request, "REQUEST_REJECTED", "The request was rejected.", status=400)
    try:
        definition = _route_from_body(body)
        _validate_admin_route(request, definition)
        saved = request.app.state.canvas_store.create_model_route(definition, actor_user_id=admin.user_id)
    except (KeyError, ValueError) as error:
        raise _resource_error(request, error) from None
    return _route_projection(saved)


@router.get("/logical-models/{model_id}/routes/{route_id}")
async def get_model_route(model_id: str, route_id: str, request: Request) -> dict[str, object]:
    _require_admin(request)
    return _route_projection(_route_for_parent(request, model_id, route_id))


@router.put("/logical-models/{model_id}/routes/{route_id}")
async def update_model_route(model_id: str, route_id: str, body: ModelRouteUpdate, request: Request) -> dict[str, object]:
    admin = _require_admin(request)
    current = _route_for_parent(request, model_id, route_id)
    if current.revision != body.revision:
        raise problem(request, "REVISION_CONFLICT", "The resource changed. Reload and try again.", status=409)
    if (
        not isinstance(current, ModelRouteDefinition)
        or model_id != body.model_id
        or route_id != body.route_id
        or body.enabled != current.enabled
    ):
        raise problem(request, "REQUEST_REJECTED", "The request was rejected.", status=400)
    try:
        definition = _route_from_body(body, revision=body.revision, archived_at=current.archived_at)
        _validate_admin_route(request, definition)
        saved = request.app.state.canvas_store.update_model_route(definition, expected_revision=body.revision, actor_user_id=admin.user_id)
    except (KeyError, ValueError) as error:
        raise _resource_error(request, error) from None
    return _route_projection(saved)


async def _route_lifecycle(model_id: str, route_id: str, body: LifecycleRevision, request: Request, action: str) -> dict[str, object]:
    admin = _require_admin(request)
    current = _route_for_parent(request, model_id, route_id)
    store = request.app.state.canvas_store
    try:
        if action == "disable":
            saved = store.set_model_route_enabled(route_id, enabled=False, expected_revision=body.revision, actor_user_id=admin.user_id)
        elif action == "enable":
            if not isinstance(current, ModelRouteDefinition) or current.revision != body.revision:
                raise RevisionConflict
            _validate_admin_route(request, replace(current, enabled=True))
            saved = store.set_model_route_enabled(route_id, enabled=True, expected_revision=body.revision, actor_user_id=admin.user_id)
        elif action == "archive":
            saved = store.archive_model_route(route_id, expected_revision=body.revision, actor_user_id=admin.user_id)
        elif action == "restore":
            saved = store.restore_model_route(route_id, expected_revision=body.revision, actor_user_id=admin.user_id)
        else:
            saved = store.purge_model_route_runtime(route_id, expected_revision=body.revision, actor_user_id=admin.user_id)
    except ObjectReferenced:
        return _reference_conflict(request, _reference_counts(store.route_references(route_id)))
    except (KeyError, ValueError) as error:
        raise _resource_error(request, error) from None
    return _route_projection(saved)


@router.post("/logical-models/{model_id}/routes/{route_id}/disable")
async def disable_model_route(model_id: str, route_id: str, body: LifecycleRevision, request: Request):
    return await _route_lifecycle(model_id, route_id, body, request, "disable")


@router.post("/logical-models/{model_id}/routes/{route_id}/enable")
async def enable_model_route(model_id: str, route_id: str, body: LifecycleRevision, request: Request):
    return await _route_lifecycle(model_id, route_id, body, request, "enable")


@router.post("/logical-models/{model_id}/routes/{route_id}/archive")
async def archive_model_route(model_id: str, route_id: str, body: LifecycleRevision, request: Request):
    return await _route_lifecycle(model_id, route_id, body, request, "archive")


@router.post("/logical-models/{model_id}/routes/{route_id}/restore")
async def restore_model_route(model_id: str, route_id: str, body: LifecycleRevision, request: Request):
    return await _route_lifecycle(model_id, route_id, body, request, "restore")


@router.post("/logical-models/{model_id}/routes/{route_id}/purge-runtime")
async def purge_model_route(model_id: str, route_id: str, body: LifecycleRevision, request: Request):
    return await _route_lifecycle(model_id, route_id, body, request, "purge")


@router.delete("/logical-models/{model_id}/routes/{route_id}", status_code=204)
async def delete_model_route(model_id: str, route_id: str, request: Request, revision: int = Query(ge=1)) -> Response:
    admin = _require_admin(request)
    _route_for_parent(request, model_id, route_id)
    store = request.app.state.canvas_store
    try:
        store.delete_model_route(route_id, expected_revision=revision, actor_user_id=admin.user_id)
    except ObjectReferenced:
        return _reference_conflict(request, _reference_counts(store.route_references(route_id)))
    except (KeyError, ValueError) as error:
        raise _resource_error(request, error) from None
    return Response(status_code=204)


@router.get("/credential-pools")
async def list_credential_pools(request: Request) -> dict[str, object]:
    _require_admin(request)
    runtime = _route_runtime(request)
    pools = runtime.pools()
    summaries: list[dict[str, object]] = []
    for pool_id in sorted(pools):
        pool = pools[pool_id]
        provider = request.app.state.canvas_store.provider_definition(pool.provider_id)
        total = sum(key.max_concurrency for key in pool.keys)
        metrics: dict[str, object] = {"capacity_status": "unavailable", "available_count": None, "busy_count": None}
        summarize = getattr(runtime.coordinator, "credential_pool_metrics", None)
        if callable(summarize):
            try:
                candidate = await summarize(pool)
                if (
                    isinstance(candidate, dict)
                    and candidate.get("capacity_status") == "available"
                    and type(candidate.get("available_count")) is int
                    and type(candidate.get("busy_count")) is int
                ):
                    metrics = candidate
            except Exception:
                pass
        protocol_available = getattr(runtime.adapter_factory, "protocol_available", None)
        protocol_trusted = bool(isinstance(provider, ProviderDefinition) and callable(protocol_available) and protocol_available(provider))
        summaries.append({
            "pool_id": pool.pool_id,
            "provider_id": pool.provider_id,
            "adapter_type": provider.adapter_type if isinstance(provider, ProviderDefinition) and protocol_trusted else None,
            "group": pool.group,
            "allowed_families": list(pool.allowed_families),
            "revision_digest": pool.revision_digest,
            "key_count": len(pool.keys),
            "total_capacity": total,
            **metrics,
            "circuit_status": "unsupported",
            "circuit_open_count": None,
        })
    return {"pools": summaries}


@router.post("/credential-pools/import")
async def import_credential_pools(request: Request) -> dict[str, object]:
    _require_admin(request)
    loader = getattr(request.app.state, "credential_pool_loader", None)
    target = getattr(request.app.state.settings, "credential_pools_path", None)
    root = getattr(request.app.state.settings, "credential_pools_root", None)
    if not isinstance(loader, CredentialPoolLoader) or target is None or root is None:
        raise problem(
            request,
            "CREDENTIAL_POOLS_IMPORT_UNAVAILABLE",
            "Credential import is not configured.",
            status=409,
        )
    stated = request.headers.get("content-length", "")
    if stated and (not stated.isdecimal() or len(stated) > 20 or int(stated) > _CREDENTIAL_JSON_MAX_BYTES + _CREDENTIAL_MULTIPART_OVERHEAD):
        raise problem(request, "CREDENTIAL_POOLS_INVALID", "The credential pool file is invalid.", status=400)
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data;"):
        raise problem(request, "CREDENTIAL_POOLS_INVALID", "The credential pool file is invalid.", status=400)
    form = None
    try:
        form = await _CredentialJsonParser(request).parse()
        upload = form.get("file")
        if (
            not isinstance(upload, UploadFile)
            or not isinstance(upload.filename, str)
            or not upload.filename.lower().endswith(".json")
            or upload.content_type != "application/json"
        ):
            raise ValueError("invalid credential upload")
        raw = await upload.read(_CREDENTIAL_JSON_MAX_BYTES + 1)
        if len(raw) > _CREDENTIAL_JSON_MAX_BYTES:
            raise ValueError("invalid credential upload")
        import_credential_pool_json(loader, target, root, raw)
    except (MultiPartException, MultipartParseError, OSError, ValueError):
        raise problem(request, "CREDENTIAL_POOLS_INVALID", "The credential pool file is invalid.", status=400) from None
    finally:
        if form is not None:
            await form.close()
    return await list_credential_pools(request)
