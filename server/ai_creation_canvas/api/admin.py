"""Standalone administrator endpoints with safe projections."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from ai_creation_canvas.api._common import context_for, problem
from ai_creation_canvas.auth.local import LocalAuthService
from ai_creation_canvas.domain.models import PortalRole
from ai_creation_canvas.domain.models import ModelInputPort, ModelOperation
from ai_creation_canvas.model_registry import GovernedModelDefinition, ModelModality, OperationContract, ProviderDefinition


router = APIRouter(prefix="/api/v1/admin")


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool


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


def _safe_user(row: dict[str, object], model_ids: tuple[str, ...]) -> dict[str, object]:
    return {
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


def _assigned_model_ids(store, user_id: str) -> tuple[str, ...]:
    return tuple(sorted({*store.assigned_models(user_id), *store.governed_assigned_models(user_id)}))


@router.get("/users")
async def list_users(request: Request) -> dict[str, object]:
    _require_admin(request)
    store = request.app.state.canvas_store
    return {"users": [_safe_user(row, _assigned_model_ids(store, str(row["user_id"]))) for row in store.list_users()]}


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
    governed = {model.model_id for model in store.list_model_definitions()}
    static_ids = tuple(model_id for model_id in body.model_ids if model_id not in governed)
    governed_ids = tuple(model_id for model_id in body.model_ids if model_id in governed)
    store.replace_model_assignments(user_id, static_ids)
    store.replace_governed_model_access(user_id, governed_ids, actor_user_id=admin.user_id)
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
    providers = [provider.admin_projection(credential_available=bool(adapter_factory and adapter_factory.credential_available(provider))) for provider in store.list_provider_definitions()]
    templates = [{"template_id": "chiyun_gpt_image_edit_v1", "title": "Chiyun GPT Image 图生图", "modality": "image", "operation": "image.edit"}]
    return {"providers": providers, "models": [model.public_projection() for model in store.list_model_definitions()], "templates": templates}


@router.post("/model-registry/providers", status_code=201)
async def create_provider(body: ProviderCreate, request: Request) -> dict[str, object]:
    admin = _require_admin(request)
    try:
        definition = ProviderDefinition(**body.model_dump(), revision=1)
        saved = request.app.state.canvas_store.create_provider_definition(definition, actor_user_id=admin.user_id)
    except ValueError:
        raise problem(request, "REQUEST_REJECTED", "The request was rejected.", status=400) from None
    factory = request.app.state.adapter_factory
    return saved.admin_projection(credential_available=bool(factory and factory.credential_available(saved)))


@router.put("/model-registry/providers/{provider_id}")
async def update_provider(provider_id: str, body: ProviderUpdate, request: Request) -> dict[str, object]:
    admin = _require_admin(request)
    if provider_id != body.provider_id:
        raise problem(request, "REQUEST_REJECTED", "The request was rejected.", status=400)
    try:
        saved = request.app.state.canvas_store.update_provider_definition(ProviderDefinition(**body.model_dump(exclude={"revision"}), revision=body.revision), expected_revision=body.revision, actor_user_id=admin.user_id)
    except ValueError:
        raise problem(request, "REVISION_CONFLICT", "The resource changed. Reload and try again.", status=409) from None
    factory = request.app.state.adapter_factory
    return saved.admin_projection(credential_available=bool(factory and factory.credential_available(saved)))


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
