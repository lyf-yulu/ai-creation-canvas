"""Standalone administrator endpoints with safe projections."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ai_creation_canvas.api._common import context_for, problem
from ai_creation_canvas.api.usage import all_usage_projection
from ai_creation_canvas.auth.local import LocalAuthService
from ai_creation_canvas.domain.models import PortalRole


router = APIRouter(prefix="/api/v1/admin")


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


@router.get("/usage")
async def all_usage(request: Request) -> dict[str, object]:
    _require_admin(request)
    return all_usage_projection(request.app.state.canvas_store)


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


@router.get("/users")
async def list_users(request: Request) -> dict[str, object]:
    _require_admin(request)
    store = request.app.state.canvas_store
    return {"users": [_safe_user(row, store.assigned_models(str(row["user_id"]))) for row in store.list_users()]}


@router.patch("/users/{user_id}")
async def update_user(user_id: str, body: UserPatch, request: Request) -> dict[str, object]:
    _require_admin(request)
    store = request.app.state.canvas_store
    try:
        row = store.set_user_enabled(user_id, body.enabled)
    except KeyError:
        raise problem(request, "USER_NOT_FOUND", "The requested user was not found.", status=404) from None
    return _safe_user(row, store.assigned_models(user_id))


@router.get("/models")
async def list_models(request: Request) -> dict[str, object]:
    user = _require_admin(request)
    context = context_for(request)
    result = await request.app.state.model_catalog.list_models(context, cookie_header=request.headers.get("cookie"))
    return {"models": jsonable_encoder(result.models), "diagnostics": result.diagnostics, "requested_by": user.user_id}


@router.put("/users/{user_id}/models")
async def replace_models(user_id: str, body: ModelAssignments, request: Request) -> dict[str, object]:
    _require_admin(request)
    store = request.app.state.canvas_store
    if store.user_by_id(user_id) is None:
        raise problem(request, "USER_NOT_FOUND", "The requested user was not found.", status=404)
    result = await request.app.state.model_catalog.list_models(context_for(request), cookie_header=request.headers.get("cookie"))
    available = {model.model_id for model in result.models}
    if not set(body.model_ids).issubset(available):
        raise problem(request, "MODEL_UNAVAILABLE", "The selected model is unavailable.", status=400)
    model_ids = store.replace_model_assignments(user_id, tuple(body.model_ids))
    return {"user_id": user_id, "model_ids": list(model_ids)}
